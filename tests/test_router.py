import contextlib
import threading
import time

import numpy as np

from micdrop.routing import Router


class FakeRecorder:
    """Yields constant 0.5 blocks; raises after `fail_after` records if set."""

    def __init__(self, fail_after=None):
        self.fail_after = fail_after
        self.records = 0

    def record(self, numframes):
        if self.fail_after is not None and self.records >= self.fail_after:
            raise RuntimeError("device vanished")
        self.records += 1
        time.sleep(0.001)
        return np.full((numframes, 2), 0.5, dtype="float32")


def make_open_recorder(rec):
    @contextlib.contextmanager
    def open_recorder(source_name, samplerate, blocksize):
        yield rec

    return open_recorder


def collect_feed(store, done_after=3):
    ev = threading.Event()

    def feed(block):
        store.append(block)
        if len(store) >= done_after:
            ev.set()

    return feed, ev


def test_routes_blocks_with_gain():
    blocks, errors = [], []
    feed, got_enough = collect_feed(blocks)
    r = Router(feed, errors.append, open_recorder=make_open_recorder(FakeRecorder()))
    r.set_gain(0.5)
    r.start("Fake Device", 48000, 2)
    assert got_enough.wait(timeout=2.0)
    r.stop()
    assert not r.running
    assert np.allclose(blocks[0], 0.25)  # 0.5 sample * 0.5 gain
    assert blocks[0].dtype == np.float32 and blocks[0].shape[1] == 2
    assert r.peak == 0.0  # reset on stop
    assert errors == []


def test_downmixes_to_mono_voice():
    blocks = []
    feed, got_enough = collect_feed(blocks)
    r = Router(feed, lambda m: None, open_recorder=make_open_recorder(FakeRecorder()))
    r.start("Fake Device", 48000, 1)
    assert got_enough.wait(timeout=2.0)
    r.stop()
    assert blocks[0].shape[1] == 1


def test_capture_error_reports_and_stops():
    errors = []
    r = Router(lambda b: None, errors.append, open_recorder=make_open_recorder(FakeRecorder(fail_after=2)))
    r.start("Fake Device", 48000, 2)
    for _ in range(200):
        if errors:
            break
        time.sleep(0.01)
    assert errors and "device vanished" in errors[0]
    assert not r.running


def test_stop_produces_no_error():
    errors = []
    r = Router(lambda b: None, errors.append, open_recorder=make_open_recorder(FakeRecorder()))
    r.start("Fake Device", 48000, 2)
    time.sleep(0.05)
    r.stop()
    time.sleep(0.05)
    assert errors == []


def test_restart_ignores_stale_thread_feed():
    blocks_a, blocks_b = [], []
    r = Router(blocks_a.append, lambda m: None, open_recorder=make_open_recorder(FakeRecorder()))
    r.start("A", 48000, 2)
    time.sleep(0.05)
    r.stop()
    n = len(blocks_a)
    r._feed = blocks_b.append  # simulate re-wiring for a new session
    r.start("B", 48000, 2)
    time.sleep(0.05)
    r.stop()
    assert len(blocks_a) == n  # old session stopped feeding
    assert blocks_b
