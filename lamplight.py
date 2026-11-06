#!/usr/bin/env python3
"""lamplight: drive HID LampArray (Windows "Dynamic Lighting") devices on Linux.

Talks to any hidraw device whose report descriptor contains a LampArray
collection (HID usage page 0x59), so it is not tied to one vendor or model.
Effects are rendered per lamp from the positions the device reports.
"""
import argparse
import array
import colorsys
import fcntl
import glob
import math
import os
import struct
import sys
import time

__version__ = '0.4.0'

LIGHTING_PAGE = 0x59
REPORTS = {  # LampArray collection usages
    0x02: 'attrs',      # LampArrayAttributesReport
    0x20: 'attr_req',   # LampAttributesRequestReport
    0x22: 'attr_resp',  # LampAttributesResponseReport
    0x50: 'multi',      # LampMultiUpdateReport
    0x60: 'range',      # LampRangeUpdateReport
    0x70: 'control',    # LampArrayControlReport
}
KINDS = ['Undefined', 'Keyboard', 'Mouse', 'GameController', 'Peripheral', 'Scene',
         'Notification', 'Chassis', 'Wearable', 'Furniture', 'Art']

MODES = ['solid', 'breathe', 'cycle', 'rainbow', 'wave', 'wheel', 'gradient', 'firmware', 'off']
ANIMATED = {'breathe', 'cycle', 'rainbow', 'wave', 'wheel'}
DIRECTIONS = ['right', 'left', 'down', 'up', 'out', 'in', 'cw', 'ccw']
FRAME_S = 1 / 30


def parse_descriptor(desc):
    """Find LampArrays in a HID report descriptor.

    Returns a list with one dict per LampArray: {report name: (report_id, feature_size)}.
    feature_size is the report length in bytes including the report ID.
    """
    arrays, feature_bits = [], {}
    page = rid = rsize = rcount = depth = 0
    usages, current = [], None
    i = 0
    while i < len(desc):
        b = desc[i]
        if b == 0xFE:  # long item
            i += 3 + desc[i + 1]
            continue
        n = (0, 1, 2, 4)[b & 3]
        tag, val = b & 0xFC, int.from_bytes(desc[i + 1:i + 1 + n], 'little')
        i += 1 + n
        if tag == 0x04:
            page = val
        elif tag == 0x84:
            rid = val
        elif tag == 0x74:
            rsize = val
        elif tag == 0x94:
            rcount = val
        elif tag == 0x08:
            usages.append(val)
        elif tag == 0xA0:  # Collection
            depth += 1
            if page == LIGHTING_PAGE and usages:
                if usages[0] == 0x01:
                    current = {}
                    arrays.append(current)
                elif current is not None and usages[0] in REPORTS:
                    current[REPORTS[usages[0]]] = rid
            usages = []
        elif tag == 0xC0:
            depth -= 1
            if depth == 0:
                current = None
        elif tag in (0x80, 0x90, 0xB0):  # Input / Output / Feature
            if tag == 0xB0:
                feature_bits[rid] = feature_bits.get(rid, 0) + rsize * rcount
            usages = []
    return [{name: (r, 1 + feature_bits.get(r, 0) // 8) for name, r in a.items()}
            for a in arrays if {'attrs', 'control', 'multi'} <= a.keys()]


def is_lamparray(sys_path):
    try:
        with open(os.path.join(sys_path, 'device', 'report_descriptor'), 'rb') as f:
            return bool(parse_descriptor(f.read()))
    except OSError:
        return False


def access_ids():
    """HID IDs (BUS:VENDOR:PRODUCT) of the LampArray devices present now."""
    ids = set()
    for sys_path in glob.glob('/sys/class/hidraw/hidraw*'):
        if is_lamparray(sys_path):
            hid = os.path.basename(os.path.realpath(os.path.join(sys_path, 'device')))
            ids.add(hid.rsplit('.', 1)[0])
    return sorted(ids)


def udev_rules():
    """Static udev rules for the LampArray devices present now.

    The GNOME extension installs the same rules, since a rule must never
    run a user-writable script (/usr/bin/lamplight probe) as root.
    """
    return [f'SUBSYSTEM=="hidraw", KERNELS=="{i}.*", TAG+="uaccess"' for i in access_ids()]


def _ioc(nr, size):
    return 0xC0000000 | (size << 16) | (ord('H') << 8) | nr


class LampArray:
    def __init__(self, devnode, reports):
        self.devnode, self.reports = devnode, reports
        self.fd = os.open(devnode, os.O_RDWR)
        self.last = None
        a = self._get('attrs')
        self.count, w, h, d, kind, self.min_interval_us = struct.unpack_from('<HIIIII', a, 1)
        self.bbox = (w, h, d)
        self.kind = KINDS[kind] if kind < len(KINDS) else f'Unknown ({kind})'
        multi_size = reports['multi'][1]
        self.ids_per_update = max(1, (multi_size - 3) // 6)
        self.positions = self._normalized_positions()

    def _get(self, name):
        rid, size = self.reports[name]
        buf = array.array('B', [rid] + [0] * (size - 1))
        fcntl.ioctl(self.fd, _ioc(0x07, size), buf)
        return buf

    def _set(self, name, payload):
        rid, size = self.reports[name]
        data = bytes([rid]) + payload
        buf = array.array('B', data.ljust(size, b'\0'))
        fcntl.ioctl(self.fd, _ioc(0x06, len(buf)), buf)

    def _normalized_positions(self):
        """Lamp positions scaled to 0..1 inside the bounding box."""
        if 'attr_req' not in self.reports or 'attr_resp' not in self.reports:
            return [(0.5, 0.5)] * self.count
        pos = []
        for lamp in range(self.count):
            self._set('attr_req', struct.pack('<H', lamp))
            _, x, y = struct.unpack_from('<HII', self._get('attr_resp'), 1)
            w, h = self.bbox[0] or 1, self.bbox[1] or 1
            pos.append((min(x / w, 1.0), min(y / h, 1.0)))
        return pos

    def autonomous(self, on):
        self._set('control', bytes([1 if on else 0]))
        self.last = None  # firmware may have changed the lamps: resend next frame

    def show(self, colors):
        """colors: one (r, g, b) tuple of 0..255 ints per lamp."""
        if colors == self.last:  # slow effects repeat frames; skip the I/O
            return
        self.last = colors
        if 'range' in self.reports and len(set(colors)) == 1:
            self._set('range', struct.pack('<BHH4B', 1, 0, self.count - 1, *colors[0], 255))
            return
        n = self.ids_per_update
        for start in range(0, self.count, n):
            chunk = colors[start:start + n]
            ids = list(range(start, start + len(chunk))) + [0] * (n - len(chunk))
            rgbi = [v for c in chunk for v in (*c, 255)] + [0] * 4 * (n - len(chunk))
            last = start + n >= self.count
            self._set('multi', struct.pack(f'<BB{n}H{4 * n}B', len(chunk), int(last), *ids, *rgbi))

    def close(self):
        os.close(self.fd)


def find_devices():
    """Return (usable devices, whether any LampArray was seen at all)."""
    devices, seen = [], False
    for sys_path in sorted(glob.glob('/sys/class/hidraw/hidraw*')):
        try:
            with open(os.path.join(sys_path, 'device', 'report_descriptor'), 'rb') as f:
                arrays = parse_descriptor(f.read())
        except OSError:
            continue
        for reports in arrays:
            seen = True
            node = '/dev/' + os.path.basename(sys_path)
            try:
                devices.append(LampArray(node, reports))
            except OSError as e:
                hint = ' (see README: udev rule)' if isinstance(e, PermissionError) else ''
                print(f'Error: {node}: {e.strerror}{hint}', file=sys.stderr)
    return devices, seen


# ---------------------------------------------------------------------------
# Effects: pure functions of (state, time, lamp position) -> rgb 0..1 floats
# ---------------------------------------------------------------------------

def hex_rgb(h):
    h = h.lstrip('#')
    if len(h) != 6:
        raise ValueError(f'color must be RRGGBB, got {h!r}')
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def mix(a, b, k):
    return tuple(x + (y - x) * k for x, y in zip(a, b))


def hue(h):
    return colorsys.hsv_to_rgb(h % 1.0, 1.0, 1.0)


def axis(direction, x, y):
    """Position along the effect direction, 0..1."""
    if direction in ('out', 'in'):
        d = min(1.0, math.hypot(x - 0.5, y - 0.5) / math.hypot(0.5, 0.5))
        return d if direction == 'out' else 1 - d
    return {'right': x, 'left': 1 - x, 'down': y, 'up': 1 - y}.get(direction, x)


def render(state, t, positions):
    mode, direction = state['mode'], state['direction']
    c1, c2 = state['colors']
    phase = t / state['speed']
    backwards = direction in ('left', 'up', 'in', 'ccw')
    if mode == 'solid':
        return [c1] * len(positions)
    if mode == 'gradient':
        return [mix(c1, c2, axis(direction, x, y)) for x, y in positions]
    if mode == 'breathe':
        return [mix(c2, c1, (1 - math.cos(2 * math.pi * phase)) / 2)] * len(positions)
    if mode == 'cycle':
        return [hue(-phase if backwards else phase)] * len(positions)
    if mode == 'rainbow':
        return [hue(phase - axis(direction, x, y)) for x, y in positions]
    if mode == 'wave':  # band of color 1 sweeping across color 2
        def band(s):
            d = abs(((s - phase) % 1.0) - 0.5) * 2  # 0 at band center, 1 far away
            return mix(c1, c2, min(1.0, d * 2.5))
        return [band(axis(direction, x, y)) for x, y in positions]
    if mode == 'wheel':
        spin = -phase if backwards else phase
        return [hue(spin + math.atan2(y - 0.5, x - 0.5) / (2 * math.pi)) for x, y in positions]
    return [(0, 0, 0)] * len(positions)  # off


def normalize(raw):
    """Fill defaults and validate a state dict."""
    mode = raw.get('mode', 'solid')
    if mode not in MODES:
        raise ValueError(f'unknown mode {mode!r}, use one of: {", ".join(MODES)}')
    colors = list(raw.get('colors') or ['ffffff'])
    colors = [hex_rgb(c) for c in (colors + ['000000'])[:2]]
    brightness = min(1.0, max(0.0, float(raw.get('brightness', 1.0))))
    speed = float(raw.get('speed', 4.0))
    if speed <= 0:
        raise ValueError('speed must be > 0 seconds')
    direction = raw.get('direction', 'right')
    if direction not in DIRECTIONS:
        raise ValueError(f'unknown direction {direction!r}, use one of: {", ".join(DIRECTIONS)}')
    balance = tuple(min(1.0, max(0.0, float(g))) for g in raw.get('balance', (1, 1, 1)))
    if len(balance) != 3:
        raise ValueError('balance needs three channel gains')
    gamma = min(3.0, max(1.0, float(raw.get('gamma', 1.0))))
    return {'mode': mode, 'colors': colors, 'brightness': brightness,
            'speed': speed, 'direction': direction, 'balance': balance, 'gamma': gamma}


def apply(devices, state, t, autonomous_changed):
    for dev in devices:
        if autonomous_changed:
            dev.autonomous(state['mode'] == 'firmware')
        if state['mode'] == 'firmware':
            continue
        # LEDs are linear while screen colors are gamma-encoded, so decode
        # first; then white balance (LED channels differ, often blue is
        # strongest).
        gains = [state['brightness'] * g * 255 for g in state['balance']]
        gamma = state['gamma']
        dev.show([tuple(round(v ** gamma * g) for v, g in zip(c, gains))
                  for c in render(state, t, dev.positions)])


def frame_interval(devices):
    return max([FRAME_S] + [d.min_interval_us / 1e6 for d in devices])


# ---------------------------------------------------------------------------
# Service: owns the devices and renders the shared GSettings state. The app
# and the GNOME tile only change settings, so effects keep running without
# them and on any desktop.
# ---------------------------------------------------------------------------

SCHEMA = 'org.gnome.shell.extensions.lamplight'

# Session bus name of the service. The GNOME extension talks to it here and
# D-Bus starts the service on the first call.
BUS_NAME = 'io.github.egeisik35.Lamplight'
BUS_PATH = '/io/github/egeisik35/Lamplight'
BUS_XML = f'''<node><interface name="{BUS_NAME}">
  <method name="AccessIds"><arg type="as" direction="out"/></method>
  <method name="Rescan"/>
  <method name="Quit"/>
</interface></node>'''

# libadwaita accent colors (GNOME 47+)
ACCENTS = {
    'blue': '3584e4', 'teal': '2190a4', 'green': '3a944a', 'yellow': 'c88800',
    'orange': 'ed5b00', 'red': 'e62d42', 'pink': 'd56199', 'purple': '9141ac',
    'slate': '6f8396',
}


def vivid(hex_color):
    """LEDs wash out darker UI colors: scale so the brightest channel is full."""
    rgb = hex_rgb(hex_color)
    top = max(rgb) or 1
    return ''.join(f'{round(v / top * 255):02x}' for v in rgb)


def settings_state(settings, accent=None, screen_off=False):
    """Build a normalized state from the Lamplight GSettings."""
    balance = settings.get_value('white-balance').unpack()
    if not settings.get_boolean('enabled') or (screen_off and settings.get_boolean('off-with-screen')):
        return normalize({'mode': 'off'})
    mode = settings.get_string('mode')
    colors = [settings.get_string('color'), settings.get_string('color2')]
    if mode == 'accent':
        mode, colors = 'solid', [vivid(ACCENTS.get(accent, ACCENTS['blue']))]
    return normalize({'mode': mode, 'colors': colors,
                      'brightness': settings.get_double('brightness'),
                      'speed': settings.get_double('speed'),
                      'direction': settings.get_string('direction'),
                      'balance': balance,
                      'gamma': settings.get_double('gamma')})


def hardware_level(brightness, max_level):
    """Backlight level (Fn keys / UPower) needed to reach a 0..1 brightness."""
    return math.ceil(round(brightness * max_level, 6))


def color_scale(brightness, level, max_level):
    """Color multiplier so that level/max * scale == brightness."""
    return min(1.0, brightness * max_level / level) if level else brightness


class Service:
    def __init__(self):
        from gi.repository import Gio, GLib
        self.GLib = GLib
        self.settings = Gio.Settings.new(SCHEMA)
        schema = Gio.SettingsSchemaSource.get_default().lookup('org.gnome.desktop.interface', True)
        self.interface = (Gio.Settings.new('org.gnome.desktop.interface')
                          if schema and schema.has_key('accent-color') else None)
        self.devices, self.screen_off, self.timer = [], False, 0
        self.session = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        self.settings.connect('changed', self.on_setting)
        self.backlight, self.max_level, self.level = None, 0, 0
        try:
            # Laptop keyboards also have a firmware backlight level (Fn keys,
            # GNOME's Keyboard tile). Keep it and our slider in sync.
            self.backlight = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None, 'org.freedesktop.UPower',
                '/org/freedesktop/UPower/KbdBacklight', 'org.freedesktop.UPower.KbdBacklight', None)
            self.max_level = self.backlight.call_sync('GetMaxBrightness', None, 0, -1, None).unpack()[0]
            self.backlight.connect('g-signal', self.on_backlight)
            self.on_level(self.backlight.call_sync('GetBrightness', None, 0, -1, None).unpack()[0])
        except Exception:  # no UPower or no firmware backlight: our slider alone
            self.backlight, self.max_level = None, 0
        if self.interface:
            self.interface.connect('changed::accent-color', lambda *_: self.update())
        session = Gio.bus_get_sync(Gio.BusType.SESSION)
        for name in ('org.gnome.ScreenSaver', 'org.freedesktop.ScreenSaver'):
            session.signal_subscribe(None, name, 'ActiveChanged', None, None,
                                     Gio.DBusSignalFlags.NONE, self.on_screensaver)
        Gio.bus_get_sync(Gio.BusType.SYSTEM).signal_subscribe(
            'org.freedesktop.login1', 'org.freedesktop.login1.Manager', 'PrepareForSleep',
            '/org/freedesktop/login1', None, Gio.DBusSignalFlags.NONE, self.on_sleep)
        # Polling every 10 s covers hotplug and newly granted access.
        GLib.timeout_add_seconds(10, self.rescan_if_missing)
        self.rescan()

    def on_setting(self, _settings, key):
        if key in ('status', 'lamp-count'):
            return
        if key == 'brightness' and self.max_level:
            level = hardware_level(self.settings.get_double('brightness'), self.max_level)
            if level != self.level:
                self.level = level
                self.backlight.call('SetBrightness', self.GLib.Variant('(i)', (level,)), 0, -1, None)
        self.update()

    def on_backlight(self, _proxy, _sender, signal, params):
        if signal == 'BrightnessChanged':
            self.on_level(params.unpack()[0])

    def on_level(self, level):
        """Firmware level changed (Fn keys, GNOME tile, or our own request)."""
        self.level = level
        # GNOME's idle dimming also lands here, so a fine slider
        # value like 0.8 can round up to the full level after idle.
        if hardware_level(self.settings.get_double('brightness'), self.max_level) != level:
            self.settings.set_double('brightness', level / self.max_level)
        else:
            self.update()

    def shutdown(self):
        """Stopping while the screen is locked: honor turn-off-with-screen.

        The GNOME extension stops this service when the screen locks, which
        can race the ScreenSaver signal, so ask once more.
        """
        try:
            active = self.session.call_sync(
                'org.gnome.ScreenSaver', '/org/gnome/ScreenSaver', 'org.gnome.ScreenSaver',
                'GetActive', None, None, 0, 500, None).unpack()[0]
        except Exception:
            active = self.screen_off
        if active and self.devices and self.settings.get_boolean('off-with-screen'):
            try:
                apply(self.devices, normalize({'mode': 'off'}), 0, True)
            except OSError:
                pass

    def set_status(self, status):
        if self.settings.get_string('status') != status:
            self.settings.set_string('status', status)

    def rescan(self):
        for d in self.devices:
            d.close()
        self.devices, seen = find_devices()
        self.set_status('' if self.devices else 'no-access' if seen else 'no-device')
        lamps = max((d.count for d in self.devices), default=0)
        if self.settings.get_int('lamp-count') != lamps:  # lets the preview match the hardware
            self.settings.set_int('lamp-count', lamps)
        self.update()
        return self.GLib.SOURCE_REMOVE

    def rescan_if_missing(self):
        if not self.devices:
            self.rescan()
        return self.GLib.SOURCE_CONTINUE

    def on_screensaver(self, _conn, _sender, _path, _iface, _signal, params):
        self.screen_off = params.unpack()[0]
        self.update()

    def on_sleep(self, _conn, _sender, _path, _iface, _signal, params):
        if not params.unpack()[0]:  # resumed: devices may have re-enumerated
            self.GLib.timeout_add(1500, self.rescan)

    def draw(self, new_state):
        try:
            # Wall-clock time keeps the app preview in step with the keyboard.
            apply(self.devices, self.state, time.time(), new_state)
            return True
        except OSError as e:
            print(f'lamplight: device write failed: {e.strerror}', file=sys.stderr, flush=True)
            self.devices = []
            self.set_status('no-access')
            self.GLib.timeout_add(2000, self.rescan)
            return False

    def update(self):
        accent = self.interface.get_string('accent-color') if self.interface else None
        try:
            self.state = settings_state(self.settings, accent, self.screen_off)
            if self.max_level:  # the firmware level already dims; scale the rest
                self.state['brightness'] = color_scale(
                    self.state['brightness'], self.level, self.max_level)
        except ValueError as e:
            print(f'lamplight: bad settings: {e}', file=sys.stderr, flush=True)
            return
        if not self.devices or not self.draw(True):
            return
        animated = self.state['mode'] in ANIMATED
        if animated and not self.timer:
            self.timer = self.GLib.timeout_add(round(frame_interval(self.devices) * 1000), self.tick)
        elif not animated and self.timer:
            self.GLib.source_remove(self.timer)
            self.timer = 0

    def tick(self):
        if self.devices and self.state['mode'] in ANIMATED and self.draw(False):
            return self.GLib.SOURCE_CONTINUE
        self.timer = 0
        return self.GLib.SOURCE_REMOVE


def serve():
    try:
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import GLib
    except (ImportError, ValueError):
        print('Error: the service needs PyGObject (python-gobject / python3-gi)', file=sys.stderr)
        return 1
    try:
        gi.require_version('GLibUnix', '2.0')
        from gi.repository import GLibUnix
        signal_add = GLibUnix.signal_add
    except (ImportError, ValueError):  # GLib < 2.80
        signal_add = GLib.unix_signal_add
    from gi.repository import Gio
    service = Service()
    loop = GLib.MainLoop()

    def stop():
        service.shutdown()
        loop.quit()
        return GLib.SOURCE_REMOVE

    def on_call(_conn, _sender, _path, _iface, method, _params, invocation):
        if method == 'AccessIds':
            invocation.return_value(GLib.Variant('(as)', (access_ids(),)))
            return
        invocation.return_value(None)
        if method == 'Rescan':
            service.rescan()
        else:  # Quit: the extension was disabled (or the screen locked)
            stop()

    def on_bus(conn, _name):
        conn.register_object(BUS_PATH, Gio.DBusNodeInfo.new_for_xml(BUS_XML).interfaces[0],
                             on_call, None, None)

    def on_lost(_conn, _name):  # another instance runs already
        print('lamplight: service already running', file=sys.stderr, flush=True)
        loop.quit()

    Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                     on_bus, None, on_lost)
    for sig in (2, 15):  # SIGINT, SIGTERM: stop and keep the last colors
        signal_add(GLib.PRIORITY_DEFAULT, sig, stop)
    loop.run()
    return 0


def setup():
    """After pip install: let D-Bus start the service, install the schema.

    Package installs (make, AUR) ship both system-wide and skip this.
    """
    import shutil
    import subprocess
    exe = os.path.abspath(sys.argv[0])
    data = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    xml = f'{SCHEMA}.gschema.xml'
    here = os.path.dirname(os.path.abspath(__file__))
    # pip puts data files in <prefix>/share, the module in <prefix>/lib/pythonX.Y/site-packages
    found = [p for p in (os.path.join(here, '../../../share/glib-2.0/schemas', xml),
                         os.path.join(here, 'data', xml)) if os.path.exists(p)]
    if not found:
        print(f'Error: {xml} not found next to {here}', file=sys.stderr)
        return 1
    schemas = os.path.join(data, 'glib-2.0/schemas')
    os.makedirs(schemas, exist_ok=True)
    if os.path.realpath(found[0]) != os.path.realpath(os.path.join(schemas, xml)):
        shutil.copy(found[0], schemas)
    subprocess.run(['glib-compile-schemas', schemas], check=True)
    services = os.path.join(data, 'dbus-1/services')
    os.makedirs(services, exist_ok=True)
    with open(os.path.join(services, f'{BUS_NAME}.service'), 'w') as f:
        f.write(f'[D-BUS Service]\nName={BUS_NAME}\nExec={exe} service\n')
    try:
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        # Make the bus see the new file now (dbus-broker does not watch for it).
        bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                      'ReloadConfig', None, None, 0, -1, None)
        # Start it now, so an enabled extension does not wait for the next login.
        bus.call_sync(BUS_NAME, BUS_PATH, BUS_NAME, 'Rescan', None, None, 0, -1, None)
    except Exception as e:
        print(f'Error: could not start the service: {e}', file=sys.stderr)
        return 1
    print(f'Done. D-Bus now starts "{exe} service" when the GNOME extension asks for it.')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog='lamplight', description=__doc__.splitlines()[0])
    p.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    p.add_argument('command', choices=['info', 'service', 'setup', 'probe', 'udev-rules'] + MODES,
                   help='info, service (background, follows settings), setup (after pip '
                        'install, for the GNOME extension), probe SYSPATH (udev), '
                        'udev-rules (static rules for present devices), or a lighting mode')
    p.add_argument('syspath', nargs='?', help='probe only: /sys path of a hidraw device')
    p.add_argument('-c', '--color', action='append', metavar='RRGGBB',
                   help='primary color; give twice for the secondary color')
    p.add_argument('-b', '--brightness', type=float, default=100, help='0-100 (default 100)')
    p.add_argument('-s', '--speed', type=float, default=4.0, help='effect period in seconds (default 4)')
    p.add_argument('-d', '--direction', choices=DIRECTIONS, default='right',
                   help='effect direction (cw/ccw for wheel; left/up/in/ccw reverse cycle)')
    args = p.parse_args(argv)

    if args.command == 'probe':
        return 0 if args.syspath and is_lamparray(args.syspath) else 1
    if args.command == 'service':
        return serve()
    if args.command == 'setup':
        return setup()
    if args.command == 'udev-rules':
        rules = udev_rules()
        print('\n'.join(rules))
        return 0 if rules else 3

    devices, seen = find_devices()
    if not devices:
        if not seen:
            print('Error: no HID LampArray device found', file=sys.stderr)
            return 3  # nothing to control on this machine
        print('Error: no usable HID LampArray device found', file=sys.stderr)
        return 1

    if args.command == 'info':
        for d in devices:
            print(f'{d.devnode}: {d.kind}, {d.count} lamp(s), '
                  f'{d.bbox[0] / 1000:.0f}x{d.bbox[1] / 1000:.0f} mm, '
                  f'min update {d.min_interval_us} us')
        return 0

    try:
        state = normalize({'mode': args.command, 'colors': args.color,
                           'brightness': args.brightness / 100, 'speed': args.speed,
                           'direction': args.direction})
        if state['mode'] not in ANIMATED:
            apply(devices, state, 0, True)
            return 0
        print(f'Running {state["mode"]}, Ctrl+C to stop')
        apply(devices, state, 0, True)
        start, dt = time.monotonic(), frame_interval(devices)
        while True:
            time.sleep(dt)
            apply(devices, state, time.monotonic() - start, False)
    except KeyboardInterrupt:
        return 0
    except ValueError as e:
        print(f'Error: {e}', file=sys.stderr)
        return 2
    except OSError as e:
        print(f'Error: device write failed: {e.strerror}', file=sys.stderr)
        return 1
    finally:
        for d in devices:
            d.close()


if __name__ == '__main__':
    sys.exit(main())
