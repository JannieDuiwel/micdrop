import numpy as np

from micdrop.audio import RingBuffer, mix_block


def _frames(n, channels=2, value=0.5):
    return np.full((n, channels), value, dtype="float32")


class TestRingBuffer:
    def test_read_returns_written_frames(self):
        rb = RingBuffer(100, 2)
        rb.write(_frames(30))
        out = rb.read(30)
        assert out.shape == (30, 2)
        assert np.allclose(out, 0.5)

    def test_underrun_returns_only_available(self):
        rb = RingBuffer(100, 2)
        rb.write(_frames(10))
        assert rb.read(64).shape == (10, 2)
        assert rb.read(64).shape == (0, 2)

    def test_wraparound_preserves_order(self):
        rb = RingBuffer(8, 1)
        rb.write(np.arange(6, dtype="float32").reshape(-1, 1))
        rb.read(6)
        rb.write(np.arange(6, 12, dtype="float32").reshape(-1, 1))  # wraps
        out = rb.read(6)
        assert np.allclose(out[:, 0], np.arange(6, 12))

    def test_overflow_drops_oldest(self):
        rb = RingBuffer(8, 1)
        rb.write(np.arange(12, dtype="float32").reshape(-1, 1))
        out = rb.read(8)
        assert np.allclose(out[:, 0], np.arange(4, 12))


class TestMixBlock:
    def test_clip_only_behaves_like_before(self):
        out = np.zeros((4, 2), dtype="float32")
        buf = _frames(10, value=0.25)
        buf2, pos = mix_block(out, buf, 0, None)
        assert np.allclose(out, 0.25)
        assert buf2 is buf and pos == 4

    def test_clip_end_zero_fills_and_resets(self):
        out = np.ones((4, 2), dtype="float32")
        buf2, pos = mix_block(out, _frames(2, value=0.25), 0, None)
        assert np.allclose(out[:2], 0.25) and np.allclose(out[2:], 0.0)
        assert buf2 is None and pos == 0

    def test_live_only(self):
        rb = RingBuffer(100, 2)
        rb.write(_frames(4, value=0.3))
        out = np.empty((4, 2), dtype="float32")
        mix_block(out, None, 0, rb)
        assert np.allclose(out, 0.3)

    def test_clip_plus_live_sums_and_clips(self):
        rb = RingBuffer(100, 2)
        rb.write(_frames(4, value=0.9))
        out = np.zeros((4, 2), dtype="float32")
        mix_block(out, _frames(10, value=0.8), 0, rb)
        assert np.allclose(out, 1.0)  # 0.9 + 0.8 clipped

    def test_live_underrun_mixes_partial(self):
        rb = RingBuffer(100, 2)
        rb.write(_frames(2, value=0.25))
        out = np.zeros((4, 2), dtype="float32")
        mix_block(out, _frames(10, value=0.25), 0, rb)
        assert np.allclose(out[:2], 0.5) and np.allclose(out[2:], 0.25)
