import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import lamplight  # noqa: E402

HERE = os.path.dirname(__file__)


def test_parse_ite5570():
    with open(os.path.join(HERE, 'ite5570.desc'), 'rb') as f:
        arrays = lamplight.parse_descriptor(f.read())
    # Only the standard 0x59 page counts; the vendor 0xFF59 mirror is ignored.
    assert arrays == [{'attrs': (0x41, 23), 'attr_req': (0x42, 3), 'attr_resp': (0x43, 29),
                       'multi': (0x44, 51), 'range': (0x45, 10), 'control': (0x46, 2)}]


def test_parse_microsoft_reference():
    # Microsoft's reference LampArray descriptor (ArduinoHidForWindows, MIT):
    # USB device with report IDs 1-6 instead of the ITE5570's 0x41-0x46.
    with open(os.path.join(HERE, 'microsoft-sample.desc'), 'rb') as f:
        arrays = lamplight.parse_descriptor(f.read())
    assert arrays == [{'attrs': (1, 23), 'attr_req': (2, 3), 'attr_resp': (3, 29),
                       'multi': (4, 51), 'range': (5, 10), 'control': (6, 2)}]


def test_parse_lenovo_loq():
    # Lenovo LOQ IRX10, ITE 048d:c693 USB per-zone keyboard (from lampctl, MIT).
    with open(os.path.join(HERE, 'lenovo-loq-ite-c693.desc'), 'rb') as f:
        arrays = lamplight.parse_descriptor(f.read())
    assert arrays == [{'attrs': (1, 23), 'attr_req': (2, 3), 'attr_resp': (3, 29),
                       'multi': (4, 51), 'range': (5, 10), 'control': (6, 2)}]


def test_non_rgb_devices_are_ignored():
    for name in ('elan-touchpad.desc', 'logitech-m650.desc'):
        with open(os.path.join(HERE, name), 'rb') as f:
            assert lamplight.parse_descriptor(f.read()) == [], name


def test_directions():
    s = lamplight.normalize({'mode': 'gradient', 'colors': ['ff0000', '0000ff'], 'direction': 'up'})
    assert lamplight.render(s, 0, [(0.5, 1), (0.5, 0)]) == [(1, 0, 0), (0, 0, 1)]
    s = lamplight.normalize({'mode': 'gradient', 'colors': ['ff0000', '0000ff'], 'direction': 'out'})
    center, corner = lamplight.render(s, 0, [(0.5, 0.5), (0, 0)])
    assert center == (1, 0, 0) and corner == (0, 0, 1)
    fwd = lamplight.normalize({'mode': 'cycle', 'speed': 4})
    back = lamplight.normalize({'mode': 'cycle', 'speed': 4, 'direction': 'ccw'})
    assert lamplight.render(fwd, 1, [(0, 0)]) != lamplight.render(back, 1, [(0, 0)])
    try:
        lamplight.normalize({'direction': 'sideways'})
        raise AssertionError('bad direction accepted')
    except ValueError:
        pass


def test_effects_shapes_and_ranges():
    positions = [(x / 9, y / 3) for y in range(4) for x in range(10)]
    for mode in lamplight.MODES:
        for direction in lamplight.DIRECTIONS:
            state = lamplight.normalize({'mode': mode, 'colors': ['ff0000', '0000ff'],
                                         'speed': 2, 'direction': direction})
            for t in (0, 0.3, 1.7):
                frame = lamplight.render(state, t, positions)
                assert len(frame) == len(positions), (mode, direction)
                assert all(0 <= v <= 1 for c in frame for v in c), (mode, direction)


def test_gradient_and_breathe_endpoints():
    s = lamplight.normalize({'mode': 'gradient', 'colors': ['ff0000', '0000ff']})
    assert lamplight.render(s, 0, [(0, 0), (1, 0)]) == [(1, 0, 0), (0, 0, 1)]
    s = lamplight.normalize({'mode': 'breathe', 'colors': ['ff0000'], 'speed': 2})
    assert lamplight.render(s, 1, [(0, 0)])[0] == (1, 0, 0)  # peak at half period
    assert lamplight.render(s, 0, [(0, 0)])[0] == (0, 0, 0)  # fades to black by default


class FakeArray(lamplight.LampArray):
    """LampArray with ioctls recorded instead of sent."""

    def __init__(self, count, ids_per_update=8):
        self.count, self.ids_per_update, self.sent, self.last = count, ids_per_update, [], None
        self.positions = [(0.5, 0.5)] * count
        self.reports = {'multi': (0x44, 3 + 6 * ids_per_update), 'range': (0x45, 10)}

    def _set(self, name, payload):
        self.sent.append((name, payload))


def test_multi_update_chunks_and_complete_flag():
    dev = FakeArray(count=20)
    dev.show([(i, 0, 0) for i in range(20)])
    assert [n for n, _ in dev.sent] == ['multi'] * 3
    counts_flags = [struct.unpack_from('<BB', p) for _, p in dev.sent]
    assert counts_flags == [(8, 0), (8, 0), (4, 1)]
    ids = struct.unpack_from('<8H', dev.sent[2][1], 2)
    assert ids[:4] == (16, 17, 18, 19)


def test_uniform_frame_uses_range_update():
    dev = FakeArray(count=1)
    dev.show([(255, 101, 0)])
    assert dev.sent == [('range', struct.pack('<BHH4B', 1, 0, 0, 255, 101, 0, 255))]


def test_repeated_frame_is_skipped_until_autonomous_change():
    dev = FakeArray(count=1)
    dev.reports['control'] = (0x46, 2)
    dev.show([(1, 2, 3)])
    dev.show([(1, 2, 3)])
    assert len(dev.sent) == 1
    dev.autonomous(False)
    dev.show([(1, 2, 3)])
    assert [n for n, _ in dev.sent] == ['range', 'control', 'range']


class FakeSettings:
    def __init__(self, **values):
        self.v = {'enabled': True, 'off-with-screen': True, 'mode': 'solid', 'color': '102030',
                  'color2': '000000', 'brightness': 1.0, 'speed': 4.0, 'direction': 'right',
                  'gamma': 2.2}
        self.v.update(values)

    def get_boolean(self, k):
        return self.v[k]

    def get_value(self, k):
        class V:
            def unpack(_self):
                return (1.0, 1.0, 0.6)
        return V()

    get_string = get_double = get_boolean


def test_settings_state():
    st = lamplight.settings_state(FakeSettings(), 'blue')
    assert st['mode'] == 'solid' and st['colors'][0] == lamplight.hex_rgb('102030')
    accent = lamplight.settings_state(FakeSettings(mode='accent'), 'red')
    assert max(accent['colors'][0]) == 1.0  # accent boosted to full brightness
    assert lamplight.settings_state(FakeSettings(), screen_off=True)['mode'] == 'off'
    assert lamplight.settings_state(FakeSettings(**{'off-with-screen': False}),
                                    screen_off=True)['mode'] == 'solid'
    assert lamplight.settings_state(FakeSettings(enabled=False))['mode'] == 'off'
    assert lamplight.settings_state(FakeSettings())['balance'] == (1.0, 1.0, 0.6)


def test_brightness_split_between_firmware_level_and_color():
    for b in (0.0, 0.2, 1 / 3, 0.5, 0.8, 1.0):
        level = lamplight.hardware_level(b, 3)
        scale = lamplight.color_scale(b, level, 3)
        assert 0 <= level <= 3 and 0 <= scale <= 1
        assert abs(level / 3 * scale - b) < 1e-9, b
    assert lamplight.hardware_level(1 / 3, 3) == 1  # exact steps need no extra level
    assert lamplight.color_scale(0.5, 3, 3) == 0.5  # Fn keys set full, slider at half


def test_white_balance_scales_channels():
    dev = FakeArray(count=1)
    dev.reports['control'] = (0x46, 2)
    state = lamplight.normalize({'mode': 'solid', 'colors': ['ffffff'], 'balance': (1, 1, 0.5)})
    lamplight.apply([dev], state, 0, False)
    assert dev.sent[-1] == ('range', struct.pack('<BHH4B', 1, 0, 0, 255, 255, 128, 255))


def test_gamma_darkens_mid_values_only():
    dev = FakeArray(count=1)
    state = lamplight.normalize({'mode': 'solid', 'colors': ['ff8000'], 'gamma': 2.2})
    lamplight.apply([dev], state, 0, False)
    r, g, b = struct.unpack_from('<4B', dev.sent[-1][1], 5)[:3]
    assert (r, b) == (255, 0) and 50 < g < 60  # 0x80 -> about 22 % light


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
    print('ok')
