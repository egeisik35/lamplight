#!/usr/bin/env python3
"""End-to-end test of lamplight against virtual LampArray keyboards.

Creates a fake HID device through /dev/uhid for each recorded descriptor
(Microsoft's reference device, Lenovo LOQ), answers its feature reports
like real firmware would, and checks that lamplight finds it, writes udev
rules for it, and lights its lamps correctly. Only the virtual device is
touched: real keyboards on the machine keep their colors.

Needs root for /dev/uhid:  sudo python3 tests/uhid_lamparray.py
"""
import glob
import os
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import lamplight  # noqa: E402

# linux/uhid.h
UHID_DESTROY, UHID_START, UHID_GET_REPORT, UHID_GET_REPORT_REPLY = 1, 2, 9, 10
UHID_CREATE2, UHID_SET_REPORT, UHID_SET_REPORT_REPLY = 11, 13, 14
EVENT_SIZE = 4380
BUS_USB = 3
VENDOR, PRODUCT = 0x1209, 0x4C41  # pid.codes test range: never a real device


class FakeLampArray:
    """Firmware side of a LampArray: answers lamplight's feature reports."""

    def __init__(self, descriptor, lamps=24, width_um=300_000, height_um=100_000):
        self.descriptor = descriptor
        (self.reports,) = lamplight.parse_descriptor(descriptor)
        self.by_id = {rid: (name, size) for name, (rid, size) in self.reports.items()}
        self.lamps, self.width, self.height = lamps, width_um, height_um
        self.colors = [(0, 0, 0)] * lamps
        self.autonomous = True
        self.selected = 0
        self.fd = None

    def position(self, lamp):  # a grid of 8 lamps per row
        cols = 8
        rows = (self.lamps + cols - 1) // cols
        return ((lamp % cols) * self.width // (cols - 1), (lamp // cols) * self.height // max(1, rows - 1))

    def feature(self, rid):
        name, size = self.by_id[rid]
        if name == 'attrs':  # count, bbox w/h/d (um), kind (1 keyboard), min interval (us)
            body = struct.pack('<HIIIII', self.lamps, self.width, self.height, 20_000, 1, 10_000)
        elif name == 'attr_resp':
            x, y = self.position(self.selected)
            body = struct.pack('<HIII', self.selected, x, y, 0)
        else:
            body = b''
        return (bytes([rid]) + body).ljust(size, b'\0')[:size]

    def set_feature(self, data):
        name, _ = self.by_id[data[0]]
        p = data[1:]
        if name == 'control':
            self.autonomous = bool(p[0])
        elif name == 'attr_req':
            self.selected = struct.unpack_from('<H', p)[0]
        elif name == 'range':
            _, first, last, r, g, b, _i = struct.unpack_from('<BHH4B', p)
            for lamp in range(first, last + 1):
                self.colors[lamp] = (r, g, b)
        elif name == 'multi':
            count = p[0]
            n = (self.reports['multi'][1] - 3) // 6
            ids = struct.unpack_from(f'<{n}H', p, 2)
            rgbi = struct.unpack_from(f'<{4 * n}B', p, 2 + 2 * n)
            for k in range(count):
                self.colors[ids[k]] = tuple(rgbi[4 * k:4 * k + 3])

    # --- uhid plumbing -----------------------------------------------------

    def create(self):
        self.fd = os.open('/dev/uhid', os.O_RDWR)
        req = struct.pack('<128s64s64sHHIIII', b'lamplight virtual LampArray', b'', b'',
                          len(self.descriptor), BUS_USB, VENDOR, PRODUCT, 1, 0)
        ev = struct.pack('<I', UHID_CREATE2) + req + self.descriptor
        os.write(self.fd, ev.ljust(EVENT_SIZE, b'\0'))
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                ev = os.read(self.fd, EVENT_SIZE)
            except OSError:
                return
            kind = struct.unpack_from('<I', ev)[0]
            if kind == UHID_GET_REPORT:
                req_id, rnum, _rtype = struct.unpack_from('<IBB', ev, 4)
                data = self.feature(rnum)
                reply = struct.pack('<IIHH', UHID_GET_REPORT_REPLY, req_id, 0, len(data)) + data
                os.write(self.fd, reply.ljust(EVENT_SIZE, b'\0'))
            elif kind == UHID_SET_REPORT:
                req_id, _rnum, _rtype, size = struct.unpack_from('<IBBH', ev, 4)
                self.set_feature(ev[12:12 + size])
                os.write(self.fd, struct.pack('<IIH', UHID_SET_REPORT_REPLY, req_id, 0).ljust(EVENT_SIZE, b'\0'))

    def destroy(self):
        os.write(self.fd, struct.pack('<I', UHID_DESTROY).ljust(EVENT_SIZE, b'\0'))
        os.close(self.fd)


def find_virtual_hidraw(timeout=5):
    tag = f'{VENDOR:04X}:{PRODUCT:04X}'
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for sys_path in glob.glob('/sys/class/hidraw/hidraw*'):
            if tag in os.path.realpath(os.path.join(sys_path, 'device')).upper():
                return sys_path
        time.sleep(0.05)
    raise TimeoutError('virtual hidraw device did not appear')


def check(name, descriptor_file):
    with open(os.path.join(HERE, descriptor_file), 'rb') as f:
        fake = FakeLampArray(f.read())
    fake.create()
    try:
        sys_path = find_virtual_hidraw()
        node = '/dev/' + os.path.basename(sys_path)
        assert lamplight.is_lamparray(sys_path), 'probe does not recognise the device'
        assert any(f'{VENDOR:04X}:{PRODUCT:04X}' in r.upper() for r in lamplight.udev_rules()), \
            'no udev rule generated'

        dev = lamplight.LampArray(node, fake.reports)
        assert dev.count == fake.lamps, f'lamp count {dev.count} != {fake.lamps}'
        assert dev.kind == 'Keyboard'
        assert all(0 <= x <= 1 and 0 <= y <= 1 for x, y in dev.positions)
        assert dev.positions[0] == (0.0, 0.0) and dev.positions[7][0] == 1.0, 'lamp positions'

        dev.autonomous(False)
        assert not fake.autonomous, 'control report not applied'

        dev.show([(255, 0, 0)] * fake.lamps)  # one colour: range report if available
        assert fake.colors == [(255, 0, 0)] * fake.lamps, 'solid colour'

        gradient = [(i * 10, 255 - i * 10, i) for i in range(fake.lamps)]
        dev.show(gradient)  # per-lamp: multi-update reports, several chunks
        assert fake.colors == gradient, 'per-lamp colours'

        dev.autonomous(True)
        dev.close()
        print(f'PASS  {name}: {fake.lamps} lamps, {node}')
    finally:
        fake.destroy()


if __name__ == '__main__':
    if os.geteuid() != 0:
        sys.exit('needs root for /dev/uhid: sudo python3 tests/uhid_lamparray.py')
    check('Microsoft reference LampArray', 'microsoft-sample.desc')
    check('Lenovo LOQ (ITE 048d:c693)', 'lenovo-loq-ite-c693.desc')
    check('ASUS Vivobook (ITE 5570, report IDs 0x41-0x46)', 'ite5570.desc')
