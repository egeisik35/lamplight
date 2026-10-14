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
import json
import math
import os
import select
import struct
import sys
import time

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

    def show(self, colors):
        """colors: one (r, g, b) tuple of 0..255 ints per lamp."""
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
    devices = []
    for sys_path in sorted(glob.glob('/sys/class/hidraw/hidraw*')):
        try:
            with open(os.path.join(sys_path, 'device', 'report_descriptor'), 'rb') as f:
                arrays = parse_descriptor(f.read())
        except OSError:
            continue
        for reports in arrays:
            node = '/dev/' + os.path.basename(sys_path)
            try:
                devices.append(LampArray(node, reports))
            except OSError as e:
                hint = ' (see README: udev rule)' if isinstance(e, PermissionError) else ''
                print(f'Error: {node}: {e.strerror}{hint}', file=sys.stderr)
    return devices


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


def render(state, t, positions):
    mode = state['mode']
    c1, c2 = state['colors']
    phase = t / state['speed'] * (-1 if state['reverse'] else 1)
    if mode == 'solid':
        return [c1] * len(positions)
    if mode == 'gradient':
        return [mix(c1, c2, x) for x, _ in positions]
    if mode == 'breathe':
        return [mix(c2, c1, (1 - math.cos(2 * math.pi * phase)) / 2)] * len(positions)
    if mode == 'cycle':
        return [hue(phase)] * len(positions)
    if mode == 'rainbow':
        return [hue(phase - x) for x, _ in positions]
    if mode == 'wave':  # band of color 1 sweeping across color 2
        def band(x):
            d = abs(((x - phase) % 1.0) - 0.5) * 2  # 0 at band center, 1 far away
            return mix(c1, c2, min(1.0, d * 2.5))
        return [band(x) for x, _ in positions]
    if mode == 'wheel':
        return [hue(phase + math.atan2(y - 0.5, x - 0.5) / (2 * math.pi)) for x, y in positions]
    return [(0, 0, 0)] * len(positions)  # off


def normalize(raw):
    """Fill defaults and validate a state dict (from CLI or the JSON stdin protocol)."""
    mode = raw.get('mode', 'solid')
    if mode not in MODES:
        raise ValueError(f'unknown mode {mode!r}, use one of: {", ".join(MODES)}')
    colors = list(raw.get('colors') or ['ffffff'])
    colors = [hex_rgb(c) for c in (colors + ['000000'])[:2]]
    brightness = min(1.0, max(0.0, float(raw.get('brightness', 1.0))))
    speed = float(raw.get('speed', 4.0))
    if speed <= 0:
        raise ValueError('speed must be > 0 seconds')
    return {'mode': mode, 'colors': colors, 'brightness': brightness,
            'speed': speed, 'reverse': bool(raw.get('reverse', False))}


def apply(devices, state, t, autonomous_changed):
    for dev in devices:
        if autonomous_changed:
            dev.autonomous(state['mode'] == 'firmware')
        if state['mode'] == 'firmware':
            continue
        b = state['brightness']
        dev.show([tuple(round(v * b * 255) for v in c) for c in render(state, t, dev.positions)])


def run(devices, states):
    """Apply states pulled from `states(timeout)`; animate between updates.

    `states` returns a normalized state, None (nothing new before timeout) or
    StopIteration. Every new state re-sends the autonomous-mode flag, since
    firmware can fall back to its own effect after suspend.
    """
    state, start = None, time.monotonic()
    dt = max([FRAME_S] + [d.min_interval_us / 1e6 for d in devices])
    while True:
        animated = state is not None and state['mode'] in ANIMATED
        new = states(dt if animated else None)
        if new is StopIteration:
            return
        if new is not None:
            state = new
        elif not animated:
            continue
        apply(devices, state, time.monotonic() - start, new is not None)


class StdinStates:
    """JSON-lines protocol: one state object per line on stdin; EOF stops."""

    def __init__(self, fd=0):
        self.fd, self.buf = fd, b''

    def __call__(self, timeout):
        while b'\n' not in self.buf:
            if not select.select([self.fd], [], [], timeout)[0]:
                return None
            chunk = os.read(self.fd, 4096)
            if not chunk:
                return StopIteration
            self.buf += chunk
        line, self.buf = self.buf.split(b'\n', 1)
        try:
            return normalize(json.loads(line))
        except (ValueError, TypeError, AttributeError) as e:
            print(f'lamplight: ignoring bad state: {e}', file=sys.stderr, flush=True)
            return None


def main(argv=None):
    p = argparse.ArgumentParser(prog='lamplight', description=__doc__.splitlines()[0])
    p.add_argument('command', choices=['info', 'daemon', 'probe'] + MODES,
                   help='info, daemon (JSON lines on stdin), probe SYSPATH (udev), or a lighting mode')
    p.add_argument('syspath', nargs='?', help='probe only: /sys path of a hidraw device')
    p.add_argument('-c', '--color', action='append', metavar='RRGGBB',
                   help='primary color; give twice for the secondary color')
    p.add_argument('-b', '--brightness', type=float, default=100, help='0-100 (default 100)')
    p.add_argument('-s', '--speed', type=float, default=4.0, help='effect period in seconds (default 4)')
    p.add_argument('-r', '--reverse', action='store_true', help='reverse effect direction')
    args = p.parse_args(argv)

    if args.command == 'probe':
        return 0 if args.syspath and is_lamparray(args.syspath) else 1

    devices = find_devices()
    if not devices:
        print('Error: no usable HID LampArray device found', file=sys.stderr)
        return 1

    if args.command == 'info':
        for d in devices:
            print(f'{d.devnode}: {d.kind}, {d.count} lamp(s), '
                  f'{d.bbox[0] / 1000:.0f}x{d.bbox[1] / 1000:.0f} mm, '
                  f'min update {d.min_interval_us} us')
        return 0

    try:
        if args.command == 'daemon':
            run(devices, StdinStates())
            return 0
        state = normalize({'mode': args.command, 'colors': args.color,
                           'brightness': args.brightness / 100, 'speed': args.speed,
                           'reverse': args.reverse})
        if state['mode'] not in ANIMATED:
            apply(devices, state, 0, True)
            return 0
        print(f'Running {state["mode"]}, Ctrl+C to stop')
        pending = [state]
        run(devices, lambda timeout: pending.pop() if pending else time.sleep(timeout))
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
