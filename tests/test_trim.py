import numpy as np

from micdrop.audio import trim_segment, compute_peaks, rebucket_peaks


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


class TestComputePeaks:
    def test_returns_exactly_the_requested_column_count(self):
        data = np.ones((10_000, 2), dtype="float32")
        assert compute_peaks(data, 200).shape == (200,)

    def test_short_clip_still_fills_every_column(self):
        data = np.ones((7, 2), dtype="float32")  # fewer frames than columns
        assert compute_peaks(data, 200).shape == (200,)

    def test_values_stay_within_zero_and_one(self):
        rng = np.random.default_rng(0)
        data = rng.uniform(-1.0, 1.0, size=(5000, 2)).astype("float32")
        peaks = compute_peaks(data, 100)
        assert peaks.min() >= 0.0
        assert peaks.max() <= 1.0

    def test_silence_gives_all_zeros_and_does_not_divide_by_zero(self):
        data = np.zeros((1000, 2), dtype="float32")
        peaks = compute_peaks(data, 50)
        assert np.all(peaks == 0.0)

    def test_constant_amplitude_normalises_to_one(self):
        data = np.full((1000, 2), 0.25, dtype="float32")
        peaks = compute_peaks(data, 50)
        assert np.allclose(peaks, 1.0)

    def test_loud_burst_reads_louder_than_quiet_tail(self):
        data = np.zeros((1000, 2), dtype="float32")
        data[:500] = 1.0
        data[500:] = 0.1
        peaks = compute_peaks(data, 10)
        assert peaks[0] > peaks[-1]

    def test_empty_clip_returns_zeros(self):
        assert np.all(compute_peaks(np.zeros((0, 2), dtype="float32"), 20) == 0.0)

    def test_zero_columns_returns_empty(self):
        assert compute_peaks(np.ones((100, 2), dtype="float32"), 0).shape == (0,)


class TestRebucketPeaks:
    def test_returns_requested_width(self):
        peaks = np.linspace(0.0, 1.0, 2000, dtype="float32")
        assert len(rebucket_peaks(peaks, 640)) == 640

    def test_returns_plain_floats_not_numpy(self):
        peaks = np.linspace(0.0, 1.0, 2000, dtype="float32")
        out = rebucket_peaks(peaks, 10)
        assert all(type(v) is float for v in out)

    def test_widening_beyond_source_length_does_not_crash(self):
        peaks = np.array([0.0, 1.0], dtype="float32")
        assert len(rebucket_peaks(peaks, 50)) == 50

    def test_keeps_the_loudest_sample_in_each_bucket(self):
        peaks = np.zeros(100, dtype="float32")
        peaks[3] = 1.0  # a spike inside the first bucket of 10
        out = rebucket_peaks(peaks, 10)
        assert out[0] == 1.0

    def test_zero_width_returns_empty(self):
        assert rebucket_peaks(np.ones(10, dtype="float32"), 0) == []


from micdrop.audio import Player


class _FakeVoice:
    """Stands in for _Voice: same three attributes Player.play touches."""

    def __init__(self, samplerate=48000, channels=2):
        self.samplerate = samplerate
        self.channels = channels
        self.played = None

    def play(self, data):
        self.played = data


def _player(monkeypatch, cached, primary=True, monitor=False):
    p = Player()
    if primary:
        p._primary = _FakeVoice()
    if monitor:
        p._monitor = _FakeVoice()
    monkeypatch.setattr(p, "_prepare", lambda path, sr: cached)
    return p


class TestPlayerTrim:
    def test_play_does_not_mutate_the_cached_decode(self, monkeypatch):
        cached = np.ones((48000, 2), dtype="float32")
        before = cached.copy()
        p = _player(monkeypatch, cached)
        p.play("clip.wav", start_ms=100, end_ms=900)
        assert np.array_equal(cached, before), "the fade leaked into the cache"

    def test_play_sends_only_the_trimmed_range(self, monkeypatch):
        cached = np.ones((48000, 2), dtype="float32")  # 1.0 s at 48 kHz
        p = _player(monkeypatch, cached)
        p.play("clip.wav", start_ms=250, end_ms=750)
        assert p._primary.played.shape[0] == 24000  # 0.5 s

    def test_untrimmed_play_sends_the_whole_clip(self, monkeypatch):
        cached = np.ones((48000, 2), dtype="float32")
        p = _player(monkeypatch, cached)
        p.play("clip.wav")
        assert p._primary.played.shape[0] == 48000

    def test_monitor_only_leaves_the_mic_voice_silent(self, monkeypatch):
        cached = np.ones((4800, 2), dtype="float32")
        p = _player(monkeypatch, cached, primary=True, monitor=True)
        p.play("clip.wav", monitor_only=True)
        assert p._primary.played is None
        assert p._monitor.played is not None

    def test_monitor_only_without_a_monitor_returns_zero(self, monkeypatch):
        cached = np.ones((4800, 2), dtype="float32")
        p = _player(monkeypatch, cached, primary=True, monitor=False)
        assert p.play("clip.wav", monitor_only=True) == 0.0
        assert p._primary.played is None

    def test_normal_play_without_any_device_still_raises(self, monkeypatch):
        cached = np.ones((4800, 2), dtype="float32")
        p = _player(monkeypatch, cached, primary=False)
        try:
            p.play("clip.wav")
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected RuntimeError when no device is selected")

    def test_peaks_returns_envelope_and_duration(self, monkeypatch):
        cached = np.ones((48000, 2), dtype="float32")
        p = _player(monkeypatch, cached)
        peaks, duration = p.peaks("clip.wav", 100)
        assert peaks.shape == (100,)
        assert abs(duration - 1.0) < 1e-6

from micdrop.clipedit import format_ms, trim_badge


class TestFormatMs:
    def test_sub_second(self):
        assert format_ms(400) == "0:00.4"

    def test_seconds_and_tenths(self):
        assert format_ms(2900) == "0:02.9"

    def test_pads_seconds_to_two_digits(self):
        assert format_ms(5000) == "0:05.0"

    def test_crosses_a_minute(self):
        assert format_ms(65_000) == "1:05.0"

    def test_zero(self):
        assert format_ms(0) == "0:00.0"

    def test_negative_clamps_to_zero(self):
        assert format_ms(-500) == "0:00.0"

    def test_truncates_rather_than_rounds_up_a_tenth(self):
        assert format_ms(2999) == "0:02.9"


class TestTrimBadge:
    def test_untrimmed_gives_no_badge(self):
        assert trim_badge(0, 0) == ""

    def test_both_ends_trimmed(self):
        assert trim_badge(400, 2900) == "✂ 0:00.4–0:02.9"

    def test_only_the_start_trimmed(self):
        assert trim_badge(400, 0) == "✂ from 0:00.4"

    def test_only_the_end_trimmed(self):
        assert trim_badge(0, 2900) == "✂ to 0:02.9"
