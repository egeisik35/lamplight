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

__version__ = '0.1.0'

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
    return {'mode': mode, 'colors': colors, 'brightness': brightness,
            'speed': speed, 'direction': direction}


def apply(devices, state, t, autonomous_changed):
    for dev in devices:
        if autonomous_changed:
            dev.autonomous(state['mode'] == 'firmware')
        if state['mode'] == 'firmware':
            continue
        b = state['brightness']
        dev.show([tuple(round(v * b * 255) for v in c) for c in render(state, t, dev.positions)])


def frame_interval(devices):
    return max([FRAME_S] + [d.min_interval_us / 1e6 for d in devices])


# ---------------------------------------------------------------------------
# Service: owns the devices and renders the shared GSettings state. The app
# and the GNOME tile only change settings, so effects keep running without
# them and on any desktop.
# ---------------------------------------------------------------------------

SCHEMA = 'io.github.egeisik35.Lamplight'

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
    if not settings.get_boolean('enabled') or (screen_off and settings.get_boolean('off-with-screen')):
        return normalize({'mode': 'off'})
    mode = settings.get_string('mode')
    colors = [settings.get_string('color'), settings.get_string('color2')]
    if mode == 'accent':
        mode, colors = 'solid', [vivid(ACCENTS.get(accent, ACCENTS['blue']))]
    return normalize({'mode': mode, 'colors': colors,
                      'brightness': settings.get_double('brightness'),
                      'speed': settings.get_double('speed'),
                      'direction': settings.get_string('direction')})


class Service:
    def __init__(self):
        from gi.repository import Gio, GLib
        self.GLib = GLib
        self.settings = Gio.Settings.new(SCHEMA)
        schema = Gio.SettingsSchemaSource.get_default().lookup('org.gnome.desktop.interface', True)
        self.interface = (Gio.Settings.new('org.gnome.desktop.interface')
                          if schema and schema.has_key('accent-color') else None)
        self.devices, self.screen_off, self.timer = [], False, 0

        self.settings.connect('changed', lambda _s, key: key != 'status' and self.update())
        if self.interface:
            self.interface.connect('changed::accent-color', lambda *_: self.update())
        session = Gio.bus_get_sync(Gio.BusType.SESSION)
        for name in ('org.gnome.ScreenSaver', 'org.freedesktop.ScreenSaver'):
            session.signal_subscribe(None, name, 'ActiveChanged', None, None,
                                     Gio.DBusSignalFlags.NONE, self.on_screensaver)
        Gio.bus_get_sync(Gio.BusType.SYSTEM).signal_subscribe(
            'org.freedesktop.login1', 'org.freedesktop.login1.Manager', 'PrepareForSleep',
            '/org/freedesktop/login1', None, Gio.DBusSignalFlags.NONE, self.on_sleep)
        # ponytail: polling every 10 s covers hotplug; use udev monitoring if
        # people plug lighting devices in and out often.
        GLib.timeout_add_seconds(10, self.rescan_if_missing)
        self.rescan()

    def set_status(self, status):
        if self.settings.get_string('status') != status:
            self.settings.set_string('status', status)

    def rescan(self):
        for d in self.devices:
            d.close()
        self.devices, seen = find_devices()
        self.set_status('' if self.devices else 'no-access' if seen else 'no-device')
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
    Service()
    loop = GLib.MainLoop()
    for sig in (2, 15):  # SIGINT, SIGTERM: stop and keep the last colors
        signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: loop.quit() or GLib.SOURCE_REMOVE)
    loop.run()
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog='lamplight', description=__doc__.splitlines()[0])
    p.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    p.add_argument('command', choices=['info', 'service', 'probe'] + MODES,
                   help='info, service (background, follows settings), probe SYSPATH (udev), '
                        'or a lighting mode')
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
