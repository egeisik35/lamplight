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


def test_effects_shapes_and_ranges():
    positions = [(x / 9, y / 3) for y in range(4) for x in range(10)]
    for mode in lamplight.MODES:
        state = lamplight.normalize({'mode': mode, 'colors': ['ff0000', '0000ff'], 'speed': 2})
        for t in (0, 0.3, 1.7):
            frame = lamplight.render(state, t, positions)
            assert len(frame) == len(positions), mode
            assert all(0 <= v <= 1 for c in frame for v in c), mode


def test_gradient_and_breathe_endpoints():
    s = lamplight.normalize({'mode': 'gradient', 'colors': ['ff0000', '0000ff']})
    assert lamplight.render(s, 0, [(0, 0), (1, 0)]) == [(1, 0, 0), (0, 0, 1)]
    s = lamplight.normalize({'mode': 'breathe', 'colors': ['ff0000'], 'speed': 2})
    assert lamplight.render(s, 1, [(0, 0)])[0] == (1, 0, 0)  # peak at half period
    assert lamplight.render(s, 0, [(0, 0)])[0] == (0, 0, 0)  # fades to black by default


class FakeArray(lamplight.LampArray):
    """LampArray with ioctls recorded instead of sent."""

    def __init__(self, count, ids_per_update=8):
        self.count, self.ids_per_update, self.sent = count, ids_per_update, []
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


def test_stdin_protocol(tmp_path=None):
    r, w = os.pipe()
    os.write(w, b'{"mode": "solid", "colors": ["00ff00"]}\n{"mode": "nope"}\n')
    os.close(w)
    states = lamplight.StdinStates(r)
    assert states(0)['colors'][0] == (0, 1, 0)
    assert states(0) is None  # bad line ignored
    assert states(0) is StopIteration


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
    print('ok')
