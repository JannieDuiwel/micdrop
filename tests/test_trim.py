import numpy as np

from micdrop.audio import trim_segment


def _ramp(n, channels=2):
    """Frames 0..n-1, every channel identical, values 1.0 (easy to spot fades)."""
    return np.ones((n, channels), dtype="float32")


class TestTrimSegment:
    def test_untrimmed_returns_identical_samples(self):
        data = _ramp(1000)
        out = trim_segment(data, 1000, 0, 0)
        assert out.shape == data.shape
        assert np.array_equal(out, data)  # no fade on edges that were not cut

    def test_untrimmed_still_returns_a_copy(self):
        data = _ramp(100)
        out = trim_segment(data, 1000, 0, 0)
        assert out is not data
        out[0, 0] = 99.0
        assert data[0, 0] == 1.0

    def test_start_offset_converts_ms_to_frames(self):
        data = _ramp(1000)
        out = trim_segment(data, 1000, 100, 0)  # 1000 Hz -> 100 ms == 100 frames
        assert out.shape[0] == 900

    def test_end_ms_zero_means_full_length(self):
        data = _ramp(1000)
        assert trim_segment(data, 1000, 0, 0).shape[0] == 1000

    def test_frame_conversion_at_44100(self):
        data = _ramp(88200)  # 2 s, so the 1500 ms end is not clamped by the tail
        out = trim_segment(data, 44100, 500, 1500)
        assert out.shape[0] == 44100  # 1.0 s kept

    def test_frame_conversion_at_48000(self):
        data = _ramp(96000)  # 2 s
        out = trim_segment(data, 48000, 500, 1500)
        assert out.shape[0] == 48000  # 1.0 s kept

    def test_end_past_the_tail_is_clamped(self):
        data = _ramp(1000)
        out = trim_segment(data, 1000, 0, 999_999)
        assert out.shape[0] == 1000

    def test_negative_start_treated_as_zero(self):
        data = _ramp(1000)
        out = trim_segment(data, 1000, -5000, 0)
        assert out.shape[0] == 1000

    def test_end_before_start_never_yields_an_empty_slice(self):
        data = _ramp(1000)
        out = trim_segment(data, 1000, 800, 200)
        assert out.shape[0] >= 1

    def test_start_past_the_end_never_yields_an_empty_slice(self):
        data = _ramp(1000)
        out = trim_segment(data, 1000, 999_999, 0)
        assert out.shape[0] >= 1

    def test_does_not_mutate_the_input(self):
        data = _ramp(1000)
        before = data.copy()
        trim_segment(data, 1000, 100, 900)
        assert np.array_equal(data, before)

    def test_fade_applied_at_both_cut_edges(self):
        data = _ramp(48000)
        out = trim_segment(data, 48000, 100, 900)  # cut at both ends
        assert out[0, 0] < 0.05  # faded in from ~0
        assert out[-1, 0] < 0.05  # faded out to ~0
        mid = out.shape[0] // 2
        assert out[mid, 0] == 1.0  # untouched in the middle

    def test_no_fade_in_when_start_edge_was_not_cut(self):
        data = _ramp(48000)
        out = trim_segment(data, 48000, 0, 900)
        assert out[0, 0] == 1.0  # start was not a cut, so no ramp
        assert out[-1, 0] < 0.05  # end was

    def test_no_fade_out_when_end_edge_was_not_cut(self):
        data = _ramp(48000)
        out = trim_segment(data, 48000, 100, 0)
        assert out[0, 0] < 0.05
        assert out[-1, 0] == 1.0

    def test_very_short_segment_does_not_crash(self):
        data = _ramp(48000)
        out = trim_segment(data, 48000, 100, 101)  # 1 ms, shorter than the fade
        assert out.shape[0] >= 1
