# MicDrop

Drop audio clips straight into your **microphone** so teammates hear them in **in-game
voice chat** — while you can still **talk at the same time**, and hear the clips yourself.
A Python GUI with clickable clip buttons and global hotkeys.

```
 Your mic ─► SteelSeries Sonar ─┐
                                ▼
   Soundboard app ─► "Sonar - Microphone" ─► (Sonar mixes voice + clips) ─► Game mic
                  └─► monitor device ─► your headphones (so YOU hear the clips)
```

A program can't inject audio into a normal mic, so the clip audio has to reach a device
the game uses as its microphone. **SteelSeries Sonar's "Microphone" is also a *playback*
device** — anything you play *into* it is mixed with your real voice and sent out as your
mic. The app uses that as the **Mic output**, and a second optional **monitor output** so
you also hear the clips.

---

## Setup (SteelSeries Sonar — recommended, no extra software)

1. **Run MicDrop** — double-click the **MicDrop** desktop shortcut (or `run.bat`).
2. **Mic output (others hear):** in the top dropdown choose
   **`SteelSeries Sonar - Microphone`**. This injects clips into your mic.
3. **Also hear on (monitor):** choose how *you* hear the clips. Two good options:
   - Pick your **headphones** directly (e.g. `Headphones (Arctis 5 Game)`), or
   - Pick a **Sonar channel** you already monitor, e.g. `SteelSeries Sonar - Aux` or
     `SteelSeries Sonar - Media`, then that channel plays through your headset at its
     own volume. Leave it on **None** if you don't want to hear clips yourself.
4. **Point the game's mic** at `SteelSeries Sonar - Microphone` (you're likely already
   using this as your mic in-game).
5. **Add clips** with **＋ Add clips** (`.mp3 / .wav / .ogg / .flac`), then **right-click**
   a clip → **Set / change hotkey…** and press a combo like `Ctrl+Alt+1` (works while the
   game is focused).

That's it. Click a button (or press its hotkey) to fire a clip. **■ Stop** (or the Stop
hotkey, default `Ctrl+Alt+S`) cuts playback. The **Mic output** carries the clip to
teammates; the **monitor output** lets you hear it too — both play simultaneously.

> Tip: keep the master **Volume** a bit below max so clips sit at a sensible level over
> your voice.

---

## Alternative setup: VoiceMeeter Banana

If you'd rather mix through VoiceMeeter (or aren't using Sonar), install
**VoiceMeeter Banana** (free, <https://vb-audio.com/Voicemeeter/banana.htm>) and reboot.
Set **Hardware Input 1** to your mic (enable **B1**), set the soundboard's **Mic output**
to **`VoiceMeeter Input`** (the Virtual Input strip; enable **A1 + B1**), set **A1** to
your headphones, and point the game's mic at **`VoiceMeeter Out B1`**. With VoiceMeeter
handling monitoring via A1 you can leave the app's monitor output on **None**.

---

## Using it

- **Click** a clip tile or press its **global hotkey** to play. **Space** or the Stop
  hotkey (default `Ctrl+Alt+S`) cuts playback.
- **Master volume** scales all clips; set a single clip's level with
  **right-click ▸ Set volume…**.
- **Delay** + **Chime before clip** (top-right) add a pre-roll to every clip — order is
  **chime → delay → clip**.
- **Search** filters tiles (**Ctrl+F**); the grid reflows to the window width.
- **Right-click** a tile for: Play, Set/change hotkey, Clear hotkey, Set volume, Move
  up/down, Rename, Remove.
- **View ▸ Dark / Light** switches the theme.
- Settings save automatically — to `config.json` next to the app, or
  `%APPDATA%\MicDrop\config.json` in the packaged build.

### Push-to-talk games

Clips only transmit while your mic is actually open. If the game uses **push-to-talk**,
hold your PTT key while firing a clip, or switch that game's voice mode to open-mic /
voice-activated.

### Anti-cheat note

Global hotkeys use a system-wide keyboard hook (the same approach as Soundpad and
similar tools). This is fine for most games. The most aggressive anti-cheats (e.g.
Valorant's Vanguard) can be sensitive to global hooks — for those, turn off
**Hotkeys ▸ Enable global hotkeys** and just click the on-screen buttons, or Alt-Tab.
If a hotkey won't fire over a fullscreen game, try running the app **as administrator**
(right-click `run.bat` ▸ Run as administrator).

---

## Standalone build

Build a version that runs without a Python install:

```powershell
.\.venv\Scripts\pip install -r requirements-build.txt
.\.venv\Scripts\pyinstaller MicDrop.spec
```

This produces **`dist\MicDrop\MicDrop.exe`** — a one-folder build with the audio libraries
(PortAudio, libsndfile) bundled. Zip `dist\MicDrop` to distribute it.

Run `MicDrop.exe` to launch; settings are stored under `%APPDATA%\MicDrop`. The machine
still needs **SteelSeries Sonar** or **VoiceMeeter** for the virtual mic. Windows
SmartScreen may warn on first run of an unsigned `.exe` (**More info ▸ Run anyway**).

---

## Troubleshooting

- **Others can't hear clips** — make sure the **Mic output** dropdown is
  `SteelSeries Sonar - Microphone` (or your VoiceMeeter input) and that the game's mic is
  set to the same Sonar Microphone. In SteelSeries Sonar, check the Mic channel isn't muted.
- **I can't hear clips myself** — set the **Also hear on (monitor)** dropdown to your
  headphones (or a Sonar channel you monitor). Leave **Mic output** as is — the two play
  at the same time.
- **I hear the clip but teammates don't** — you've only set the monitor; the **Mic output**
  must be the Sonar Microphone / virtual mic, not your headphones.
- **Nothing plays / device error** — pick a different entry for the same device in the
  dropdown (the list is sorted with **WASAPI** first; try the MME copy if WASAPI errors).
  Clips are auto-resampled to each device's rate, so rate mismatches shouldn't occur.
- **An `.mp3` won't load** — most do (the bundled `libsndfile` decodes MP3). For an
  unusual file, convert it to `.wav`, or install the optional fallback decoder:
  `.venv\Scripts\pip install pydub imageio-ffmpeg` (then re-add the clip).
- **See the actual error** — launch with **`run-debug.bat`** to get a console + traceback.

---

## How it works (for the curious)

- `micdrop/audio.py` — loads a clip with `soundfile`, downmixes/upmixes to stereo,
  resamples with `soxr`, and plays it through a persistent, click-free `sounddevice`
  output stream to the **mic** device and an optional **monitor** device at once. Decoded
  clips are cached per samplerate so repeats are instant.
- `micdrop/hotkeys.py` — global hotkeys via the `keyboard` library (not suppressed,
  so keys still reach the game); can be toggled off entirely.
- `micdrop/config.py` — saves/loads `config.json` (clips, hotkeys, mic + monitor
  devices, volume).
- `micdrop/main.py` — the Tkinter GUI. Audio and hotkey callbacks run off the UI
  thread and post results to a queue the UI drains.

### Developer run

```powershell
.\.venv\Scripts\python.exe -m micdrop             # run with console
.\.venv\Scripts\pip install -r micdrop\requirements.txt      # (re)install deps
```
