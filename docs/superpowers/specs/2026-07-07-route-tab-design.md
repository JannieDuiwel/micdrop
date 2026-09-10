# Route Tab — live audio routing into the mic

**Date:** 2026-07-07
**Status:** Approved by user (design conversation, this session)

## Purpose

Let the user pick a playback device that music is already playing on (Spotify,
YouTube, etc. — e.g. `SteelSeries Sonar - Media` or their headphones) and route
that live audio into MicDrop's **Mic output** (the virtual mic, e.g.
`SteelSeries Sonar - Microphone`), so friends in the same voice party hear the
music. Soundboard clips keep working and are **mixed** with the routed audio.

## Why a new library

Capturing "what is playing on an output device" on Windows requires WASAPI
loopback. The PortAudio build bundled with `sounddevice` 0.5.2 does **not**
expose loopback devices (verified on this machine: zero `[Loopback]` entries,
`WasapiSettings` has no loopback flag). The `soundcard` library (pure Python +
cffi, v0.4.6, installs cleanly on Python 3.13) records loopback from any
playback device and coexists with the existing `sounddevice` output engine.

Rejected alternatives:

- **PyAudioWPatch** — works, but ships a second PortAudio DLL next to
  sounddevice's (two audio stacks in one process, fatter frozen build).
- **No new dependency** (capture from `Sonar - Stream` / Stereo Mix via
  sounddevice input streams) — only carries music if Sonar Streamer mode is
  enabled and configured; may drag voice/game audio along. Clunky.

## UI

The main window content becomes a `ttk.Notebook` with two tabs. The top bar
(Mic output, monitor, master volume) and the status bar stay **outside** the
notebook — they apply globally.

- **Soundboard tab** — the existing options bar (delay/chime/search) and clip
  grid, unchanged in behavior.
- **Route tab**:
  - **Source** dropdown — playback devices enumerated via `soundcard`
    (loopback-capturable). The currently selected **Mic output device is
    excluded** (routing it into itself is a feedback loop). If the source is
    the monitor device, show a hint that clips will echo for friends.
  - **▶ Route to mic / ■ Stop routing** toggle button with a status line
    ("Routing: <source> → <mic device>").
  - **Music volume** slider — independent gain for routed audio (does not
    affect clip volume).
  - **Level meter** — small peak meter fed by the capture thread so the user
    can see audio is flowing (the #1 "is it working?" question).

Nothing is sent to the monitor device: the user already hears the music on the
source device itself.

## Engine

### `micdrop/routing.py` (new)

`Router` — owns one background capture thread.

- `start(source_name, samplerate, channels)` — resolves the soundcard loopback
  microphone for the named speaker, opens a recorder at the **mic voice's
  samplerate** (WASAPI shared mode auto-converts, so no resampling in our
  code), reads ~20 ms blocks in a loop.
- Each block: apply gain → down/upmix to the voice's channel count → push into
  the Player's live ring buffer → update a decaying peak level for the meter.
- `stop()` — signals the thread and joins it; `set_gain(float)`; `peak` property.
- Any exception in the loop (device vanished, Sonar off) stops routing and
  reports the error through a callback; the UI posts it to the existing
  `_cmd_q` queue and flips the toggle off. No crash.
- soundcard runs in a worker thread; COM init is handled by the library.
  Suppress its data-discontinuity runtime warnings.

### `micdrop/audio.py` changes

- New `RingBuffer` — fixed-capacity float32 numpy buffer (~1 s), thread-safe
  write (router thread) and read (audio callback). Underrun returns what is
  available; overrun drops oldest.
- `_Voice` gets an optional live ring buffer. `_callback` mixes: clip chunk
  (existing logic) **+** whatever the ring buffer yields, then `np.clip` to
  [-1, 1]. No live buffer attached → exactly today's behavior.
- `Player` exposes the primary voice's samplerate/channels and its ring buffer
  so the Router can feed it.

### Device-change interaction

Changing the Mic output while routing is active: the UI stops the router,
applies the device change (which rebuilds the voice, possibly at a new
samplerate), then restarts the router against the new voice. Selecting the
routed source as the new Mic output stops routing with a status message.

## Config

Two new persisted fields (with the same unknown-key-tolerant loading as the
rest): `route_source_name: str = ""`, `route_volume: float = 0.8`.
Routing always starts **off** at launch.

## Errors

- Source device disappears / capture fails → routing stops, toggle resets,
  status bar shows the error (existing `_msg` mechanism).
- No Mic output selected when Route is clicked → status message, no start.
- Empty source list (soundcard finds nothing) → dropdown shows a placeholder,
  toggle disabled.

## Testing

- Unit tests (pytest, new `tests/` dir): `RingBuffer` write/read/wraparound/
  underrun/overrun; `_Voice` callback mixing (clip only, live only, both,
  clipping at ±1); config round-trip with the new fields.
- Manual end-to-end: play music to `Sonar - Media`, route it, confirm the
  level meter moves and Sonar's Microphone channel meter shows the music;
  fire a clip mid-song and confirm both are audible on the mic.

## Packaging / docs

- `micdrop/requirements.txt` gains `soundcard>=0.4.6`.
- `MicDrop.spec`: verify the frozen build imports soundcard (cffi); add a
  hidden import if PyInstaller misses it.
- README: new "Route music" section.

## Out of scope (YAGNI)

Hotkey for the route toggle, per-app capture, routing to the monitor,
multiple simultaneous sources, EQ/ducking.
