# Clip trim + unified edit screen

**Date:** 2026-09-10
**Status:** Approved by user (design conversation, this session)

## Purpose

Let the user choose which *part* of a clip plays. A downloaded clip usually has
dead air at the front and a tail after the punchline; today the only options are
"play all of it" or re-cut the file in an external editor. This adds a per-clip
start/end trim, set by dragging handles over a drawn waveform.

The trim control needs a window to live in, and MicDrop has no clip edit screen
at all — label, volume and hotkey are three separate `simpledialog` popups off
the right-click menu. So the same change introduces one **Edit clip** screen
holding all four properties.

## Scope note: `.wav` import already works

The feature request also asked for `.wav` support. It is already there and
always has been:

- `AUDIO_FILETYPES` (`main.py`) has listed `*.wav` since the first commit
  (`75e69ad`), and `dist/MicDrop.exe` was built after the last source change, so
  the packaged build has it too.
- Decoding goes through `soundfile` / libsndfile 1.2.2. Verified in this
  project's venv: PCM_16, PCM_24, FLOAT and PCM_U8 WAVs all round-trip through
  the exact `sf.read(path, dtype="float32", always_2d=True)` call the player
  uses.

No work needed. Recorded here so it is not re-raised. If a *specific* `.wav`
ever fails it will be an exotic codec (ADPCM, GSM), and that is a separate bug.

## UI — the Edit clip screen

Opened from the right-click menu's new **Edit clip…** item.

```
┌─ Edit clip ─────────────────────────────────┐
│ Label  [ Airhorn                          ] │
│                                             │
│  ░░░▓▓█▓▓██▓▓▓█▓▓▓▓▓▓█▓▓░░░░░░░░░░░░░░░░░   │
│  ░░░▓▓█▓▓██▓▓▓█▓▓▓▓▓▓█▓▓░░░░░░░░░░░░░░░░░   │
│     ▲                  ▲                    │
│   start                end                  │
│  Start 0:00.4      End 0:02.9      (2.5 s)  │
│                                             │
│ Volume  ──────●────────  110%               │
│ Hotkey  ctrl+alt+1     [ Set… ] [ Clear ]   │
│                                             │
│ [ ▶ Preview ]  [ Reset trim ]      [ Close ] │
└─────────────────────────────────────────────┘
```

- **Waveform** on a `tk.Canvas`. The kept region is drawn in the accent colour,
  the trimmed-away region dimmed to `muted`, so the cut reads at a glance.
- **Handles** are draggable vertical bars. A click within 8 px of a handle grabs
  it; clicks elsewhere on the waveform do nothing, so the trim cannot be changed
  by accident. Arrow keys nudge the grabbed handle 10 ms, Shift+arrow 100 ms —
  pixel dragging cannot hit a tight cut on a long clip.
- **Minimum range 50 ms.** Handles cannot cross or collapse.
- **Preview** plays only the trimmed range, and only to the **monitor** device,
  so trimming mid-game does not spam the voice party. With no monitor device
  selected it is silent, and the dialog says so rather than failing.
- **Reset trim** restores full-clip playback (`start_ms = end_ms = 0`).
- Modal (`transient` + `grab_set`), themed from `theme.py`, Escape closes.

### Live edits, not Save/Cancel

Edits apply to the in-memory `Clip` immediately; `config.json` is written once
on close, not on every slider pixel. **Close**, not OK/Cancel.

This is deliberate (see Rejected alternatives). Every existing edit in MicDrop
applies immediately, and hotkey assignment in particular *must*:
`_on_hotkey_assigned` de-duplicates the new combo against every other clip and
saves. A dialog where the hotkey commits instantly but the label and trim wait
for OK is inconsistent in a way users hit. **Reset trim** covers the realistic
regret.

### Global hotkeys suspend while the screen is open

Clip hotkeys are OS-level (`keyboard` library) and fire regardless of window
focus, so a stray `ctrl+alt+1` would launch a clip mid-trim. The editor disables
them on open and restores the previous state on close. This nests correctly with
`_capture_hotkey`, which does its own save/restore around capture.

## Engine — `micdrop/audio.py`

Decoded audio is already cached per `(path, mtime, samplerate)`, so a trim is a
**slice of an array already in memory**: no re-decode, no cache invalidation, no
change to `preload` / `_warm_cache`.

Two new module-level pure functions, mirroring `mix_block` — a free function
that `_Voice` calls, and the seam `test_audio_mix.py` already tests through:

- `trim_segment(data, samplerate, start_ms, end_ms) -> np.ndarray`
  Converts ms to frames at *that voice's* samplerate, clamps, and applies a
  **5 ms fade at both edges**. A cut lands on an arbitrary non-zero sample;
  without the fade that step pops, which is audible and ugly over voice chat.
  `_make_chime` already uses the same ramp trick.

  **Contract: never mutates `data`, always returns a new array.** A numpy slice
  is a *view*, so fading `data[i0:i1]` in place would write straight into the
  cached clip and corrupt every later play of it. The slice is copied before the
  fade is applied.

  Clamping rules, in order: `end_ms == 0` means the natural end; both bounds are
  clipped into `[0, len(data)]`; then `i1 = max(i1, i0 + 1)` so the result is
  never empty or negative-length. The 50 ms minimum range is a *UI* constraint —
  the engine only guarantees it never produces a bad slice, because a
  hand-edited `config.json` can hold anything.

  **Only edges that were actually cut are faded** — fade in only when
  `i0 > 0`, fade out only when `i1 < len(data)`. A pop can only happen at a cut;
  fading an untrimmed clip would soften the attack on every clip already on the
  board (an airhorn is exactly the wrong thing to ramp up over 5 ms) and would
  silently change playback that works today. An untrimmed clip therefore comes
  back sample-identical to the decode.
- `compute_peaks(data, columns) -> np.ndarray`
  Mono mixdown, absolute peak per column, normalised 0–1, for drawing. Handles
  clips shorter than `columns` and all-silence input.

`Player.play()` gains `start_ms=0, end_ms=0, monitor_only=False`:

- Order per voice: fetch cached `data` → `seg = trim_segment(...)` (a fresh
  array) → `out = np.ascontiguousarray(seg * level)`. The cached array is only
  ever read. A regression test pins this: play a clip trimmed, then assert the
  cache entry still equals the original decode.
- `monitor_only=True` selects only the monitor voice, and returns `0.0` instead
  of raising when no monitor is configured — the dialog shows a hint instead.

`Player.peaks(path, columns) -> (peaks, duration_s)` is the dialog's only entry
point to audio data, so the UI never touches `numpy` or `soundfile` directly.

### Waveform cost

Peaks are computed **once per dialog open** at 2000 columns, then re-bucketed to
the current canvas width on every draw. A 5-minute stereo clip is ~14M floats:
fine once, a stutter if recomputed on every resize-drag frame. Re-bucketing 2000
elements is free.

## Config — `micdrop/config.py`

Two new `Clip` fields, in milliseconds to match the existing `play_delay_ms`
convention and to avoid float drift through JSON:

```python
start_ms: int = 0   # playback starts here
end_ms: int = 0     # 0 = play to the natural end
```

`Clip.from_dict` already drops unknown keys and falls back to dataclass
defaults, so existing configs load untouched and every current clip reads as
untrimmed. An older MicDrop build reading a newer `config.json` ignores both
keys and plays the full clip — degrades quietly, never crashes.

## Wiring — `micdrop/main.py`

- New `micdrop/clipedit.py` holds `ClipEditor`. A new module, not more
  `main.py`: that file is already 1044 lines and carries the menu, top bar, clip
  grid, Route tab, devices, hotkeys and the main loop.
- Right-click menu gains **Edit clip…** at the top. *Rename…* and *Set volume…*
  become shortcuts that open the same editor, and their two `simpledialog`
  handlers (`_menu_rename`, `_menu_set_volume`) are deleted — `main.py` should
  end up shorter.
- **No double-click to open.** Tk fires `<Button-1>` before `<Double-Button-1>`,
  so double-clicking a tile would play the clip *and* open the editor.
  Right-click only.
- Tile badge `✂ 0:00.4–0:02.9` alongside the existing `⌨` and `🔊` badges, so a
  trimmed clip is identifiable without opening it.
- `trigger_play` forwards `start_ms` / `end_ms` to `player.play`.
- **`_on_mousewheel` guard.** `main.py:250` uses
  `canvas.bind_all("<MouseWheel>")`, and `bind_all` writes to the `all` bindtag
  shared by every window in the application — scrolling over the new dialog
  would scroll the clip grid behind it. Ignore events whose `winfo_toplevel()`
  is not the main window.

`root.bind` shortcuts (Space, Escape, Ctrl+F, Ctrl+O) do **not** need a guard: a
`Toplevel` has its own bindtag and never sees them.

## Errors

- Clip file missing or undecodable when the editor opens: show the message where
  the waveform would be, keep label/volume/hotkey editable.
- Preview with no monitor device: hint text, no exception.
- `end_ms` beyond the real clip length (the file was replaced with a shorter one
  since the trim was set): clamped at play time by `trim_segment`, never an
  out-of-range slice.
- The existing status-bar / `_cmd_q` mechanism is reused for anything raised on
  the playback thread.

## Testing

Existing tests are pure logic with no Tk; this matches that.

- `tests/test_config.py` — trim fields round-trip; a config written before this
  change loads with `start_ms = end_ms = 0`.
- `tests/test_trim.py` (new):
  - ms to frames at 44100 and 48000
  - clamping: negative start, end past the tail, end <= start, `end_ms == 0`
    meaning full length
  - fade present at both edges, samples in the middle untouched
  - **the cached array is not mutated** after a trimmed play
  - `compute_peaks`: output shape, 0–1 range, all-silence input, and a clip
    shorter than the requested column count

The dialog gets no unit tests — there is no Tk harness in this project — and is
verified by running the app.

Baseline before any of this: **16 tests passing** at commit `af5bd43`.

## Rejected alternatives

- **Save / Cancel on the edit screen.** Rejected because hotkey assignment has
  to commit immediately (it de-duplicates globally), so half the dialog would be
  deferred and half instant.
- **Dual-handle slider with no waveform.** Much less code, but trimming becomes
  guess-and-check by ear; seeing where the silence and the punchline sit is the
  whole point.
- **Inline trim panel under the clip grid.** Avoids a second window, but
  competes with the grid for space and would require tile *selection*, which
  today conflicts with tile click = play.
- **Destructive trim (write a cut copy of the file).** Simpler playback, but it
  loses the original and re-trimming compounds. The trim stays metadata.
- **Trimming inside `_prepare` / the cache key.** Would re-decode on every trim
  change and multiply cache entries per clip. Slicing at play time is free.

## Out of scope (YAGNI)

Fade lengths as a user setting (5 ms is fixed and inaudible), looping, multiple
regions per clip, waveform zoom/scroll, snap-to-silence or auto-trim,
pitch/speed, per-clip pan, undo history, drag-and-drop import, bulk folder
import.
