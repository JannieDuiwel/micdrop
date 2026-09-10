"""Live loopback routing.

Captures what is playing on a chosen playback device (WASAPI loopback via the
`soundcard` library) on a worker thread and feeds gained float32 blocks into
the Player's mic voice, where they are mixed with clip playback.

soundcard is imported lazily so the app still starts if it is missing/broken;
the Route tab then reports the error instead of crashing on launch.
"""

from __future__ import annotations

import contextlib
import threading
import warnings
from collections.abc import Callable

import numpy as np

BLOCK_FRAMES = 960  # ~20 ms at 48 kHz: low latency without hammering the CPU


def list_route_sources() -> list[str]:
    """Names of playback devices whose audio can be captured via loopback."""
    import soundcard as sc

    return [s.name for s in sc.all_speakers()]


@contextlib.contextmanager
def _open_loopback_recorder(source_name: str, samplerate: int, blocksize: int):
    # soundcard only CoInitializes the thread that first imported it; this runs
    # on the Router's worker thread, which must join COM's MTA itself or WASAPI
    # activation fails with 0x800401f0 once sounddevice streams are running.
    import ctypes

    ctypes.windll.ole32.CoInitializeEx(None, 0)  # 0 = COINIT_MULTITHREADED

    import soundcard as sc

    warnings.filterwarnings("ignore", module="soundcard")  # capture-gap noise
    spk = next((s for s in sc.all_speakers() if s.name == source_name), None)
    if spk is None:
        raise RuntimeError(f"Playback device not found: {source_name}")
    mic = sc.get_microphone(spk.id, include_loopback=True)
    with mic.recorder(samplerate=samplerate, channels=2, blocksize=blocksize) as rec:
        yield rec


class Router:
    """Owns one capture thread routing a playback device into the mic feed.

    WASAPI loopback yields no packets while the source is silent, so a
    `record()` call can block indefinitely; `stop()` therefore joins with a
    timeout and abandons the daemon thread. A generation token keeps such a
    stale thread from feeding audio or raising errors afterwards.
    """

    def __init__(
        self,
        feed: Callable[[np.ndarray], None],
        on_error: Callable[[str], None],
        open_recorder=None,
    ) -> None:
        self._feed = feed
        self._on_error = on_error
        self._open_recorder = open_recorder or _open_loopback_recorder
        self._thread: threading.Thread | None = None
        self._gen = 0
        self._gain = 1.0
        self.peak = 0.0
        self.source_name = ""

    @property
    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def set_gain(self, gain: float) -> None:
        self._gain = max(0.0, min(2.0, float(gain)))

    def start(self, source_name: str, samplerate: int, channels: int) -> None:
        self.stop()
        self._gen += 1
        self.source_name = source_name
        self._thread = threading.Thread(
            target=self._run,
            args=(self._gen, source_name, samplerate, channels),
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._gen += 1  # invalidate the running thread immediately
        t = self._thread
        if t is not None:
            t.join(timeout=1.0)
        self._thread = None
        self.peak = 0.0
        self.source_name = ""

    def _run(self, gen: int, source_name: str, samplerate: int, channels: int) -> None:
        try:
            with self._open_recorder(source_name, samplerate, BLOCK_FRAMES) as rec:
                while gen == self._gen:
                    data = rec.record(numframes=BLOCK_FRAMES)
                    if gen != self._gen:
                        break
                    block = np.asarray(data, dtype="float32") * self._gain
                    if channels == 1 and block.ndim == 2 and block.shape[1] > 1:
                        block = block.mean(axis=1, keepdims=True)
                    self.peak = float(np.max(np.abs(block))) if block.size else 0.0
                    self._feed(block)
        except Exception as exc:  # noqa: BLE001 - any capture failure ends routing
            if gen == self._gen:
                self.peak = 0.0
                self._on_error(f"Routing stopped: {exc}")
