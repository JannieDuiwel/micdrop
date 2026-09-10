"""Audio playback engine.

Loads clips, resamples them per output device's samplerate, and plays them to a
primary device (the mic channel others hear) plus an optional monitor device (so
you hear the clip yourself). Each device gets a persistent, click-free output
stream fed from a callback; starting a clip swaps the buffer, so playback is
instant and a new clip replaces the previous one.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

import numpy as np
import sounddevice as sd
import soundfile as sf
import soxr


# Host API preference order for nicer names + good latency (WASAPI first).
_HOSTAPI_RANK = {"Windows WASAPI": 0, "Windows DirectSound": 1, "MME": 2, "Windows WDM-KS": 3}

# Fade applied to a trimmed edge. A cut lands on an arbitrary non-zero sample,
# and that step pops; 5 ms ramps it away without being audible as a fade.
FADE_S = 0.005


@dataclass
class DeviceInfo:
    index: int
    name: str
    samplerate: int
    max_output_channels: int
    hostapi: str

    @property
    def display_name(self) -> str:
        return f"{self.name}  —  {self.hostapi}"


def list_output_devices() -> list[DeviceInfo]:
    """Return all devices that can output audio, sorted with WASAPI first."""
    hostapis = sd.query_hostapis()
    devices = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_output_channels"] > 0:
            devices.append(
                DeviceInfo(
                    index=idx,
                    name=dev["name"],
                    samplerate=int(dev["default_samplerate"]),
                    max_output_channels=int(dev["max_output_channels"]),
                    hostapi=hostapis[dev["hostapi"]]["name"],
                )
            )
    devices.sort(key=lambda d: (_HOSTAPI_RANK.get(d.hostapi, 9), d.name.lower()))
    return devices


def find_voicemeeter_input(devices: list[DeviceInfo]) -> DeviceInfo | None:
    """Pick the VoiceMeeter virtual input device if present.

    Prefers the main VAIO ("VoiceMeeter Input") over the AUX VAIO, and (because
    `devices` is already host-API ranked) the WASAPI entry over MME/DirectSound.
    """
    candidates = [d for d in devices if "voicemeeter" in d.name.lower() and "input" in d.name.lower()]
    if not candidates:
        return None
    for d in candidates:
        if "aux" not in d.name.lower():
            return d
    return candidates[0]


def find_sonar_input(devices: list[DeviceInfo]) -> DeviceInfo | None:
    """Pick the SteelSeries Sonar virtual microphone if present.

    Since `devices` is host-API ranked, the WASAPI entry is preferred.
    """
    for d in devices:
        name = d.name.lower()
        if "sonar" in name and "microphone" in name:
            return d
    return None


def find_virtual_mic(devices: list[DeviceInfo]) -> DeviceInfo | None:
    """Preferred injection target: SteelSeries Sonar first, then VoiceMeeter."""
    return find_sonar_input(devices) or find_voicemeeter_input(devices)


class RingBuffer:
    """Fixed-capacity float32 frame buffer; one writer thread, one reader thread.

    `write` drops the oldest frames on overflow; `read` returns fewer frames
    than asked on underrun (possibly zero rows).
    """

    def __init__(self, capacity_frames: int, channels: int) -> None:
        self._buf = np.zeros((capacity_frames, channels), dtype="float32")
        self._cap = capacity_frames
        self.channels = channels
        self._r = 0  # absolute frames read
        self._w = 0  # absolute frames written
        self._lock = threading.Lock()

    def write(self, data: np.ndarray) -> None:
        with self._lock:
            n = data.shape[0]
            if n >= self._cap:
                data = data[-self._cap :]
                n = data.shape[0]
            start = self._w % self._cap
            first = min(n, self._cap - start)
            self._buf[start : start + first] = data[:first]
            if n > first:
                self._buf[: n - first] = data[first:]
            self._w += n
            if self._w - self._r > self._cap:
                self._r = self._w - self._cap

    def read(self, frames: int) -> np.ndarray:
        with self._lock:
            n = min(frames, self._w - self._r)
            out = np.empty((n, self.channels), dtype="float32")
            start = self._r % self._cap
            first = min(n, self._cap - start)
            out[:first] = self._buf[start : start + first]
            if n > first:
                out[first:] = self._buf[: n - first]
            self._r += n
            return out


def mix_block(
    outdata: np.ndarray,
    buf: np.ndarray | None,
    pos: int,
    live: RingBuffer | None,
) -> tuple[np.ndarray | None, int]:
    """Fill one output block from the clip buffer plus the live feed.

    Returns the advanced (buf, pos); buf becomes None when the clip ends.
    Whenever live audio is mixed in, the result is hard-clipped to ±1.
    """
    frames = outdata.shape[0]
    if buf is None:
        outdata.fill(0)
    else:
        chunk = buf[pos : pos + frames]
        n = chunk.shape[0]
        if n:
            outdata[:n] = chunk
        if n < frames:
            outdata[n:] = 0
            buf, pos = None, 0
        else:
            pos += frames
    if live is not None:
        lchunk = live.read(frames)
        m = lchunk.shape[0]
        if m:
            outdata[:m] += lchunk
            np.clip(outdata, -1.0, 1.0, out=outdata)
    return buf, pos


def trim_segment(
    data: np.ndarray, samplerate: int, start_ms: int, end_ms: int
) -> np.ndarray:
    """Return the [start_ms, end_ms) slice of `data`, faded at the cut edges.

    `end_ms` of 0 (or past the tail) means "to the natural end". Only an edge
    that was actually cut is faded, so an untrimmed clip comes back
    sample-identical — an airhorn should not ramp up over its first 5 ms.

    Always returns a fresh array. A numpy slice is a view, and fading one in
    place would write straight into the caller's cached decode.
    """
    n = data.shape[0]
    if n == 0:
        return data.copy()

    i0 = int(max(0, start_ms) * samplerate / 1000)
    i0 = min(i0, n - 1)
    i1 = n if end_ms <= 0 else int(end_ms * samplerate / 1000)
    i1 = min(max(i1, i0 + 1), n)

    seg = np.array(data[i0:i1], dtype="float32", copy=True)

    fade = min(int(samplerate * FADE_S), seg.shape[0] // 2)
    if fade > 0:
        ramp = np.linspace(0.0, 1.0, fade, dtype="float32")[:, None]
        if i0 > 0:
            seg[:fade] *= ramp
        if i1 < n:
            seg[-fade:] *= ramp[::-1]
    return seg


def compute_peaks(data: np.ndarray, columns: int) -> np.ndarray:
    """Absolute peak per column, normalised to 0..1, for drawing a waveform.

    Normalising to the clip's own maximum means a quiet clip still fills the
    display, which is what you want when hunting for the start of the audio.
    """
    if columns < 1:
        return np.zeros(0, dtype="float32")
    n = data.shape[0]
    if n == 0:
        return np.zeros(columns, dtype="float32")

    mono = data.mean(axis=1) if data.ndim > 1 else data
    cols = min(columns, n)
    per = int(np.ceil(n / cols))
    pad = per * cols - n
    if pad:
        mono = np.concatenate([mono, np.zeros(pad, dtype="float32")])

    peaks = np.abs(mono.reshape(cols, per)).max(axis=1)
    top = float(peaks.max())
    if top > 0:
        peaks = peaks / top
    if cols < columns:  # clip shorter than the requested resolution
        peaks = np.repeat(peaks, int(np.ceil(columns / cols)))[:columns]
    return peaks.astype("float32")


def rebucket_peaks(peaks: np.ndarray, width: int) -> list[float]:
    """Reduce a peak envelope to `width` columns, keeping the loudest per bucket.

    Peaks are computed once at high resolution and re-bucketed on every redraw,
    so a resize drag never re-reads the audio. Returns plain floats to keep
    numpy out of the UI layer.
    """
    if width < 1 or peaks.size == 0:
        return []
    edges = np.minimum(
        (np.arange(width) * (peaks.size / width)).astype(int), peaks.size - 1
    )
    return [float(v) for v in np.maximum.reduceat(peaks, edges)]


class _Voice:
    """A persistent output stream to one device, fed by a swappable buffer."""

    def __init__(self, device_index: int, samplerate: int, channels: int) -> None:
        self.device_index = device_index
        self.samplerate = samplerate
        self.channels = channels
        self._buf: np.ndarray | None = None
        self._pos = 0
        self.live: RingBuffer | None = None  # routed live audio, mixed over clips
        self._lock = threading.Lock()
        self.stream = sd.OutputStream(
            device=device_index,
            samplerate=samplerate,
            channels=channels,
            dtype="float32",
            callback=self._callback,
        )
        self.stream.start()

    def _callback(self, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        with self._lock:
            self._buf, self._pos = mix_block(outdata, self._buf, self._pos, self.live)

    def play(self, data: np.ndarray) -> None:
        with self._lock:
            self._buf = data
            self._pos = 0

    def stop(self) -> None:
        with self._lock:
            self._buf = None
            self._pos = 0

    def active(self) -> bool:
        with self._lock:
            return self._buf is not None

    def close(self) -> None:
        try:
            self.stream.stop()
            self.stream.close()
        except sd.PortAudioError:
            pass


class Player:
    """Plays clips to a primary device and an optional monitor device at once.

    Starting a new clip replaces the previous one. Decoded+resampled audio is
    cached per (path, mtime, samplerate) so repeated triggers are instant.
    """

    def __init__(self) -> None:
        self.volume: float = 1.0
        self._cache: dict[tuple, np.ndarray] = {}
        self._chime_cache: dict[int, np.ndarray] = {}
        self._primary: _Voice | None = None
        self._monitor: _Voice | None = None
        self._lock = threading.Lock()

    # -- device selection -------------------------------------------------
    @property
    def device_index(self) -> int | None:
        return self._primary.device_index if self._primary else None

    @property
    def monitor_index(self) -> int | None:
        return self._monitor.device_index if self._monitor else None

    def _make_voice(self, index: int) -> _Voice:
        info = sd.query_devices(index)
        sr = int(info["default_samplerate"])
        ch = 2 if int(info["max_output_channels"]) >= 2 else 1
        return _Voice(index, sr, ch)

    def set_device(self, index: int) -> None:
        with self._lock:
            if self._primary is not None:
                self._primary.close()
            self._primary = self._make_voice(index)

    def set_monitor_device(self, index: int | None) -> None:
        with self._lock:
            if self._monitor is not None:
                self._monitor.close()
                self._monitor = None
            if index is not None:
                self._monitor = self._make_voice(index)

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, float(volume)))

    # -- live routing -------------------------------------------------------
    def enable_live(self) -> tuple[int, int]:
        """Attach a live ring buffer (~1 s) to the mic voice for routed audio.

        Returns (samplerate, channels) the feed must match.
        """
        with self._lock:
            v = self._primary
            if v is None:
                raise RuntimeError("No output device selected")
            v.live = RingBuffer(v.samplerate, v.channels)
            return v.samplerate, v.channels

    def feed_live(self, data: np.ndarray) -> None:
        v = self._primary
        if v is not None and v.live is not None:
            v.live.write(data)

    def disable_live(self) -> None:
        with self._lock:
            if self._primary is not None:
                self._primary.live = None

    # -- loading ----------------------------------------------------------
    def _prepare(self, path: str, samplerate: int) -> np.ndarray:
        """Return clip as contiguous float32 stereo resampled to `samplerate`."""
        mtime = os.path.getmtime(path)
        key = (os.path.abspath(path), mtime, samplerate)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        data, sr = sf.read(path, dtype="float32", always_2d=True)
        if data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        elif data.shape[1] > 2:
            data = data[:, :2]
        if sr != samplerate:
            data = soxr.resample(data, sr, samplerate).astype("float32")

        data = np.ascontiguousarray(data, dtype="float32")
        self._cache[key] = data
        return data

    def preload(self, path: str) -> None:
        """Decode + resample into the cache for every active voice's samplerate."""
        rates = {v.samplerate for v in (self._primary, self._monitor) if v is not None}
        for sr in rates or {48000}:
            self._prepare(path, sr)

    # -- playback ---------------------------------------------------------
    def play(
        self,
        path: str,
        gain: float = 1.0,
        start_ms: int = 0,
        end_ms: int = 0,
        monitor_only: bool = False,
    ) -> float:
        """Play a clip to all active devices. Returns duration; raises on failure.

        `gain` is a per-clip multiplier applied on top of the master volume.
        `start_ms`/`end_ms` trim the clip (`end_ms` of 0 = to the natural end).
        `monitor_only` keeps the clip off the mic device, for previewing a trim
        without spamming the voice party; it returns 0.0 when no monitor is set
        rather than raising, so the caller can show a hint.
        """
        if monitor_only:
            if self._monitor is None:
                return 0.0
            voices = [self._monitor]
        else:
            voices = [v for v in (self._primary, self._monitor) if v is not None]
            if not voices:
                raise RuntimeError("No output device selected")
        level = self.volume * max(0.0, gain)
        duration = 0.0
        for v in voices:
            data = trim_segment(self._prepare(path, v.samplerate), v.samplerate, start_ms, end_ms)
            if v.channels == 1:
                data = data.mean(axis=1, keepdims=True).astype("float32")
            out = np.ascontiguousarray(data * level, dtype="float32")
            v.play(out)
            duration = max(duration, out.shape[0] / v.samplerate)
        return duration

    def peaks(self, path: str, columns: int = 2000) -> tuple[np.ndarray, float]:
        """Peak envelope + duration in seconds, for drawing a waveform.

        Reuses the same decode cache playback uses, so opening the editor on a
        clip that has already played costs nothing.
        """
        sr = self._primary.samplerate if self._primary is not None else 48000
        data = self._prepare(path, sr)
        return compute_peaks(data, columns), data.shape[0] / sr

    def _make_chime(self, samplerate: int) -> np.ndarray:
        """A short two-tone 'di-ding' as contiguous float32 stereo, click-free."""
        cached = self._chime_cache.get(samplerate)
        if cached is not None:
            return cached
        seg = 0.09  # seconds per tone
        fade = max(1, int(samplerate * 0.008))
        ramp = np.linspace(0.0, 1.0, fade, dtype="float32")
        parts = []
        for freq in (880.0, 1320.0):
            n = int(samplerate * seg)
            t = np.arange(n, dtype="float32") / samplerate
            wave = np.sin(2 * np.pi * freq * t).astype("float32")
            wave[:fade] *= ramp
            wave[-fade:] *= ramp[::-1]
            parts.append(wave)
        mono = np.concatenate(parts) * np.float32(0.3)
        stereo = np.ascontiguousarray(np.repeat(mono[:, None], 2, axis=1), dtype="float32")
        self._chime_cache[samplerate] = stereo
        return stereo

    def play_chime(self) -> float:
        """Play the chime to all active devices (scaled by master volume). Returns duration."""
        voices = [v for v in (self._primary, self._monitor) if v is not None]
        if not voices:
            return 0.0
        duration = 0.0
        for v in voices:
            data = self._make_chime(v.samplerate)
            if v.channels == 1:
                data = data.mean(axis=1, keepdims=True).astype("float32")
            out = np.ascontiguousarray(data * self.volume, dtype="float32")
            v.play(out)
            duration = max(duration, out.shape[0] / v.samplerate)
        return duration

    def stop(self) -> None:
        for v in (self._primary, self._monitor):
            if v is not None:
                v.stop()

    def is_playing(self) -> bool:
        return any(v.active() for v in (self._primary, self._monitor) if v is not None)

    def close(self) -> None:
        for v in (self._primary, self._monitor):
            if v is not None:
                v.close()
        self._primary = None
        self._monitor = None
