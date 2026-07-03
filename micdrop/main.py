"""Tkinter GUI for MicDrop.

Threading model: hotkey callbacks and audio playback run off the Tk main thread.
GUI-affecting results are posted to a queue that the main thread drains in `_pump`.
`trigger_play` is safe to call from any thread (it spawns its own audio worker).
"""

from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import sounddevice as sd

from . import audio, config, hotkeys

AUDIO_FILETYPES = [
    ("Audio files", "*.wav *.flac *.ogg *.mp3 *.aiff *.aif"),
    ("All files", "*.*"),
]
GRID_COLUMNS = 3


class MicDropApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MicDrop")
        self.root.geometry("740x580")
        self.root.minsize(560, 420)

        self.cfg = config.load_config()
        self.player = audio.Player()
        self.player.set_volume(self.cfg.master_volume)
        self.hotkeys = hotkeys.HotkeyManager()

        self._cmd_q: queue.Queue = queue.Queue()
        self._last_played = ""
        self._capturing = False
        self._devices: list[audio.DeviceInfo] = []
        self._device_warning = ""
        self._msg = ""
        self._msg_until = 0.0
        self._menu_index = 0

        self._build_menu()
        self._build_top_bar()
        self._build_clip_area()
        self._build_status_bar()

        self._load_devices()
        self._init_device()
        self._init_monitor()
        if not self.cfg.hotkeys_enabled:
            self.hotkeys.set_enabled(False)
        self._apply_hotkeys()
        self._warm_cache()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._pump)

    # ===================================================================
    # UI construction
    # ===================================================================
    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)

        filem = tk.Menu(menubar, tearoff=0)
        filem.add_command(label="Add clips…", command=self._add_clips, accelerator="Ctrl+O")
        filem.add_separator()
        filem.add_command(label="Quit", command=self._on_close)
        menubar.add_cascade(label="File", menu=filem)

        hk = tk.Menu(menubar, tearoff=0)
        self.hk_enabled_var = tk.BooleanVar(value=self.cfg.hotkeys_enabled)
        hk.add_checkbutton(
            label="Enable global hotkeys",
            variable=self.hk_enabled_var,
            command=self._toggle_hotkeys,
        )
        hk.add_command(label="Set Stop hotkey…", command=lambda: self._capture_hotkey("stop"))
        menubar.add_cascade(label="Hotkeys", menu=hk)

        helpm = tk.Menu(menubar, tearoff=0)
        helpm.add_command(label="Setup guide (README)", command=self._open_readme)
        helpm.add_command(label="About", command=self._about)
        menubar.add_cascade(label="Help", menu=helpm)

        self.root.config(menu=menubar)
        self.root.bind("<Control-o>", lambda e: self._add_clips())

    def _build_top_bar(self) -> None:
        bar = ttk.Frame(self.root, padding=8)
        bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(bar, text="Mic output (others hear):").grid(row=0, column=0, sticky="w")
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(
            bar, textvariable=self.device_var, state="readonly", width=50
        )
        self.device_combo.grid(row=0, column=1, sticky="we", padx=6)
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_select)

        ttk.Label(bar, text="Also hear on (monitor):").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.monitor_var = tk.StringVar()
        self.monitor_combo = ttk.Combobox(
            bar, textvariable=self.monitor_var, state="readonly", width=50
        )
        self.monitor_combo.grid(row=1, column=1, sticky="we", padx=6, pady=(6, 0))
        self.monitor_combo.bind("<<ComboboxSelected>>", self._on_monitor_select)

        ttk.Label(bar, text="Volume:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.volume_scale = ttk.Scale(
            bar, from_=0, to=100, orient="horizontal", command=self._on_volume
        )
        self.volume_scale.set(self.cfg.master_volume * 100)
        self.volume_scale.grid(row=2, column=1, sticky="we", padx=6, pady=(6, 0))
        self.volume_scale.bind("<ButtonRelease-1>", lambda e: self._save())

        side = ttk.Frame(bar)
        side.grid(row=0, column=2, rowspan=3, padx=(10, 0))
        ttk.Button(side, text="■  Stop", command=self.player.stop).pack(fill=tk.X)
        ttk.Button(side, text="＋  Add clips", command=self._add_clips).pack(fill=tk.X, pady=(4, 0))

        bar.columnconfigure(1, weight=1)

    def _build_clip_area(self) -> None:
        container = ttk.Frame(self.root)
        container.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8)

        self.canvas = tk.Canvas(container, highlightthickness=0)
        vbar = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.grid_frame = ttk.Frame(self.canvas, padding=4)
        self._grid_window = self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind(
            "<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind(
            "<Configure>", lambda e: self.canvas.itemconfigure(self._grid_window, width=e.width)
        )
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        self.clip_menu = tk.Menu(self.root, tearoff=0)
        self.clip_menu.add_command(label="Play", command=self._menu_play)
        self.clip_menu.add_command(label="Set / change hotkey…", command=self._menu_set_hotkey)
        self.clip_menu.add_command(label="Clear hotkey", command=self._menu_clear_hotkey)
        self.clip_menu.add_separator()
        self.clip_menu.add_command(label="Rename…", command=self._menu_rename)
        self.clip_menu.add_command(label="Remove", command=self._menu_remove)

        self._build_clip_buttons()

    def _build_status_bar(self) -> None:
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(
            self.root, textvariable=self.status_var, relief="sunken", anchor="w", padding=(6, 2)
        ).pack(side=tk.BOTTOM, fill=tk.X)

    # ===================================================================
    # Clip grid
    # ===================================================================
    def _build_clip_buttons(self) -> None:
        for child in self.grid_frame.winfo_children():
            child.destroy()

        if not self.cfg.clips:
            ttk.Label(
                self.grid_frame,
                text="No clips yet.\n\nClick  ＋ Add clips  to choose audio files,\nthen right-click a button to assign a hotkey.",
                anchor="center",
                justify="center",
                padding=30,
            ).grid(row=0, column=0)
            return

        for col in range(GRID_COLUMNS):
            self.grid_frame.columnconfigure(col, weight=1, uniform="clipcol")

        for i, clip in enumerate(self.cfg.clips):
            row, col = divmod(i, GRID_COLUMNS)
            label = clip.label
            if clip.hotkey:
                label += f"\n⌨ {clip.hotkey}"
            btn = ttk.Button(
                self.grid_frame, text=label, command=lambda c=clip: self.trigger_play(c)
            )
            btn.grid(row=row, column=col, sticky="nsew", padx=4, pady=4, ipady=10)
            btn.bind("<Button-3>", lambda e, idx=i: self._popup_menu(e, idx))

    def _popup_menu(self, event: tk.Event, index: int) -> None:
        self._menu_index = index
        try:
            self.clip_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.clip_menu.grab_release()

    def _on_mousewheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    # -- context menu actions --------------------------------------------
    def _menu_play(self) -> None:
        self.trigger_play(self.cfg.clips[self._menu_index])

    def _menu_set_hotkey(self) -> None:
        self._capture_hotkey(self._menu_index)

    def _menu_clear_hotkey(self) -> None:
        clip = self.cfg.clips[self._menu_index]
        if clip.hotkey:
            self.hotkeys.unbind(clip.hotkey)
            clip.hotkey = ""
            self._save()
            self._build_clip_buttons()
            self._apply_hotkeys()

    def _menu_rename(self) -> None:
        clip = self.cfg.clips[self._menu_index]
        new = simpledialog.askstring(
            "Rename clip", "Label:", initialvalue=clip.label, parent=self.root
        )
        if new and new.strip():
            clip.label = new.strip()
            self._save()
            self._build_clip_buttons()

    def _menu_remove(self) -> None:
        clip = self.cfg.clips.pop(self._menu_index)
        if clip.hotkey:
            self.hotkeys.unbind(clip.hotkey)
        self._save()
        self._build_clip_buttons()
        self._apply_hotkeys()

    # ===================================================================
    # Devices
    # ===================================================================
    MONITOR_NONE = "None (don't monitor)"

    def _load_devices(self) -> None:
        self._devices = audio.list_output_devices()
        names = [d.display_name for d in self._devices]
        self.device_combo["values"] = names
        self.monitor_combo["values"] = [self.MONITOR_NONE] + names

    def _resolve_device(self, name: str, hostapi: str) -> audio.DeviceInfo | None:
        """Find a saved device by (name, hostapi), falling back to name-only."""
        if not name:
            return None
        for d in self._devices:
            if d.name == name and d.hostapi == hostapi:
                return d
        for d in self._devices:
            if d.name == name:
                return d
        return None

    def _init_device(self) -> None:
        target = self._resolve_device(self.cfg.output_device_name, self.cfg.output_device_hostapi)
        if target is None:
            target = audio.find_voicemeeter_input(self._devices)
            if target is None:
                self._device_warning = (
                    "VoiceMeeter not found — clips play locally only. "
                    "Install VoiceMeeter Banana (see Help ▸ Setup guide) so others hear them."
                )
        if target is None:
            target = self._default_output_device()
        if target is not None:
            self._apply_device(target)

    def _init_monitor(self) -> None:
        d = self._resolve_device(self.cfg.monitor_device_name, self.cfg.monitor_device_hostapi)
        if d is not None:
            self.player.set_monitor_device(d.index)
            self.monitor_var.set(d.display_name)
        else:
            self.player.set_monitor_device(None)
            self.monitor_var.set(self.MONITOR_NONE)

    def _default_output_device(self) -> audio.DeviceInfo | None:
        out_idx = None
        try:
            out_idx = sd.default.device[1]
        except (IndexError, TypeError, sd.PortAudioError):
            out_idx = None
        for d in self._devices:
            if d.index == out_idx:
                return d
        return self._devices[0] if self._devices else None

    def _apply_device(self, d: audio.DeviceInfo) -> None:
        """Switch the player + UI to a device. Does NOT persist (so an auto-detected
        fallback never gets baked into config, letting VoiceMeeter win once installed)."""
        self.player.set_device(d.index)
        self.device_var.set(d.display_name)
        if "voicemeeter" in d.name.lower():
            self._device_warning = ""

    def _on_device_select(self, _event: tk.Event) -> None:
        idx = self.device_combo.current()
        if 0 <= idx < len(self._devices):
            d = self._devices[idx]
            self._apply_device(d)
            self.cfg.output_device_name = d.name
            self.cfg.output_device_hostapi = d.hostapi
            self._save()
            self._warm_cache()

    def _on_monitor_select(self, _event: tk.Event) -> None:
        idx = self.monitor_combo.current()  # 0 = None, then device i-1
        if idx <= 0:
            self.player.set_monitor_device(None)
            self.cfg.monitor_device_name = ""
            self.cfg.monitor_device_hostapi = ""
        else:
            d = self._devices[idx - 1]
            try:
                self.player.set_monitor_device(d.index)
            except sd.PortAudioError as exc:
                self._set_status(f"Couldn't open monitor device: {exc}", 6)
                return
            self.cfg.monitor_device_name = d.name
            self.cfg.monitor_device_hostapi = d.hostapi
        self._save()
        self._warm_cache()

    # ===================================================================
    # Volume
    # ===================================================================
    def _on_volume(self, value: str) -> None:
        v = float(value) / 100.0
        self.player.set_volume(v)
        self.cfg.master_volume = v

    # ===================================================================
    # Clips: add / play
    # ===================================================================
    def _add_clips(self) -> None:
        paths = filedialog.askopenfilenames(title="Add audio clips", filetypes=AUDIO_FILETYPES)
        if not paths:
            return
        for p in paths:
            label = os.path.splitext(os.path.basename(p))[0]
            self.cfg.clips.append(config.Clip(path=p, label=label, hotkey=""))
        self._save()
        self._build_clip_buttons()
        self._apply_hotkeys()
        self._warm_cache()

    def trigger_play(self, clip: config.Clip) -> None:
        """Play a clip. Safe to call from any thread (hotkey or GUI)."""
        self._last_played = clip.label

        def work() -> None:
            try:
                self.player.play(clip.path)
            except Exception as exc:  # noqa: BLE001 - surface any decode/device error
                self._cmd_q.put(("error", f"Couldn't play '{clip.label}': {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def _warm_cache(self) -> None:
        if self.player.device_index is None:
            return
        clips = list(self.cfg.clips)

        def work() -> None:
            for c in clips:
                try:
                    self.player.preload(c.path)
                except Exception as exc:  # noqa: BLE001
                    self._cmd_q.put(("error", f"Can't load '{c.label}': {exc}"))
                    return

        threading.Thread(target=work, daemon=True).start()

    # ===================================================================
    # Hotkeys
    # ===================================================================
    def _apply_hotkeys(self) -> None:
        mapping: dict[str, object] = {}
        for clip in self.cfg.clips:
            if clip.hotkey:
                mapping[clip.hotkey] = (lambda c=clip: self.trigger_play(c))
        if self.cfg.stop_hotkey:
            mapping[self.cfg.stop_hotkey] = self.player.stop
        errors = self.hotkeys.rebind_all(mapping)
        if errors:
            self._set_status("Hotkey problem: " + "; ".join(errors), 6)

    def _toggle_hotkeys(self) -> None:
        enabled = self.hk_enabled_var.get()
        self.cfg.hotkeys_enabled = enabled
        self.hotkeys.set_enabled(enabled)
        self._save()
        self._set_status("Global hotkeys " + ("enabled" if enabled else "disabled"), 3)

    def _capture_hotkey(self, target) -> None:
        if self._capturing:
            return
        self._capturing = True
        prev = self.hotkeys.enabled
        self.hotkeys.set_enabled(False)

        def work() -> None:
            try:
                hk = hotkeys.read_next_hotkey()
            except Exception:  # noqa: BLE001
                hk = ""
            self._cmd_q.put(("assigned", target, hk, prev))

        threading.Thread(target=work, daemon=True).start()

    def _on_hotkey_assigned(self, target, hk: str, prev: bool) -> None:
        self._capturing = False
        self.hotkeys.set_enabled(prev)
        if hk and hk.lower() != "esc":
            if target == "stop":
                self.cfg.stop_hotkey = hk
            else:
                for c in self.cfg.clips:
                    if c.hotkey == hk:
                        c.hotkey = ""
                self.cfg.clips[target].hotkey = hk
            self._save()
            self._build_clip_buttons()
            self._apply_hotkeys()
            self._set_status(f"Hotkey set: {hk}", 3)
        else:
            self._set_status("Hotkey assignment cancelled", 2)

    # ===================================================================
    # Status + main loop pump
    # ===================================================================
    def _set_status(self, text: str, seconds: float = 4.0) -> None:
        self._msg = text
        self._msg_until = time.monotonic() + seconds

    def _base_status(self) -> str:
        if self._device_warning:
            return "⚠ " + self._device_warning
        dev = self.cfg.output_device_name or "no device"
        state = "hotkeys ON" if self.hotkeys.enabled else "hotkeys OFF"
        return f"Output: {dev}   |   {state}   |   {len(self.cfg.clips)} clips"

    def _handle_cmd(self, cmd: tuple) -> None:
        kind = cmd[0]
        if kind == "error":
            self._set_status(cmd[1], 6)
        elif kind == "assigned":
            self._on_hotkey_assigned(cmd[1], cmd[2], cmd[3])
        elif kind == "status":
            self._set_status(cmd[1])

    def _pump(self) -> None:
        try:
            while True:
                self._handle_cmd(self._cmd_q.get_nowait())
        except queue.Empty:
            pass

        now = time.monotonic()
        if self._capturing:
            text = "🎯 Press a key combination now…  (Esc to cancel)"
        elif now < self._msg_until:
            text = self._msg
        elif self.player.is_playing():
            text = f"● Playing: {self._last_played}"
        else:
            text = self._base_status()
        self.status_var.set(text)
        self.root.after(80, self._pump)

    # ===================================================================
    # Misc
    # ===================================================================
    def _save(self) -> None:
        try:
            config.save_config(self.cfg)
        except OSError as exc:
            self._set_status(f"Could not save config: {exc}", 6)

    def _open_readme(self) -> None:
        readme = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "README.md")
        try:
            os.startfile(readme)  # type: ignore[attr-defined]
        except (OSError, AttributeError):
            messagebox.showinfo("Setup guide", f"Open the README manually:\n{readme}")

    def _about(self) -> None:
        messagebox.showinfo(
            "MicDrop",
            "MicDrop\n\n"
            "Drop audio clips straight into your mic: plays clips into a virtual "
            "microphone (SteelSeries Sonar / VoiceMeeter) so teammates hear them over "
            "your in-game voice while you keep talking.\n\n"
            "See Help ▸ Setup guide for the one-time routing.",
        )

    def _on_close(self) -> None:
        try:
            self._save()
        finally:
            self.hotkeys.clear()
            self.player.close()
            self.root.destroy()


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    MicDropApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
