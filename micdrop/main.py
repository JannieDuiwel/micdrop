"""Tkinter GUI for MicDrop.

Threading model: hotkey callbacks and audio playback run off the Tk main thread.
GUI-affecting results are posted to a queue that the main thread drains in `_pump`.
`trigger_play` is safe to call from any thread (it spawns its own audio worker).
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import sounddevice as sd

from . import audio, config, hotkeys, theme

AUDIO_FILETYPES = [
    ("Audio files", "*.wav *.flac *.ogg *.mp3 *.aiff *.aif"),
    ("All files", "*.*"),
]
TILE_MIN_WIDTH = 210  # px; number of grid columns adapts to the window width

if getattr(sys, "frozen", False):
    _ASSETS = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(__file__)), "assets")
else:
    _ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")

SETUP_GUIDE = """MicDrop plays audio clips into a virtual microphone so teammates hear them in
your in-game voice chat — while you keep talking, and can hear the clips yourself.

A program can't inject into a real mic, so clips are played INTO a device the game
uses as its mic. You need ONE of these installed:

── Option A: SteelSeries Sonar (recommended, no extra software) ──
1. Mic output (others hear):  choose  "SteelSeries Sonar - Microphone".
2. Also hear on (monitor):  pick your headphones, or a Sonar channel you monitor
   (e.g. Aux / Media), or leave it on None if you don't want to hear clips.
3. In the game, set your microphone to  "SteelSeries Sonar - Microphone".

── Option B: VoiceMeeter Banana (free, vb-audio.com) ──
1. Hardware Input 1 = your mic (enable B1).
2. Mic output (others hear) = "VoiceMeeter Input"  (enable A1 + B1).
3. A1 = your headphones. Point the game's mic at "VoiceMeeter Out B1".

── Using it ──
• Add clips (＋), then right-click a tile to set a hotkey, volume, rename, reorder.
• Click a tile or press its hotkey to play.  ■ Stop  (or Space / your Stop hotkey) cuts it.
• Delay + Chime (top-right) add a pre-roll before each clip: chime → delay → clip.
• Master Volume scales everything; per-clip volume rides on top.
• Search filters tiles (Ctrl+F). Dark/Light under the View menu.

Push-to-talk games: hold your PTT key while a clip plays (clips only transmit while
your mic is open). Anti-cheat: global hotkeys use a system-wide hook — for the most
aggressive anti-cheats, turn hotkeys off (Hotkeys menu) and click the tiles instead.
"""


class MicDropApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MicDrop")
        self.root.geometry("760x600")
        self.root.minsize(560, 420)

        self.cfg = config.load_config()
        self.player = audio.Player()
        self.player.set_volume(self.cfg.master_volume)
        self.hotkeys = hotkeys.HotkeyManager()

        self._cmd_q: queue.Queue = queue.Queue()
        self._last_played = ""
        self._playing_index: int | None = None
        self._play_token = 0
        self._capturing = False
        self._devices: list[audio.DeviceInfo] = []
        self._device_warning = ""
        self._msg = ""
        self._msg_until = 0.0
        self._menu_index = 0
        self._search = ""
        self._columns = 3
        self._tiles: dict[int, tk.Frame] = {}
        self._hl_index: int | None = None

        self.palette = theme.apply_theme(self.root, self.cfg.theme)
        self._set_icon()

        self._build_menu()
        self._build_top_bar()
        self._build_options_bar()
        self._build_clip_area()
        self._build_status_bar()
        self._bind_shortcuts()

        self._load_devices()
        self._init_device()
        self._init_monitor()
        if not self.cfg.hotkeys_enabled:
            self.hotkeys.set_enabled(False)
        self._apply_hotkeys()
        self._warm_cache()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._pump)

    def _set_icon(self) -> None:
        icon = os.path.join(_ASSETS, "icon.ico")
        if os.path.exists(icon):
            try:
                self.root.iconbitmap(icon)
            except tk.TclError:
                pass

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

        viewm = tk.Menu(menubar, tearoff=0)
        self.theme_var = tk.StringVar(value=self.cfg.theme)
        viewm.add_radiobutton(
            label="Dark theme", variable=self.theme_var, value="dark", command=self._on_theme_change
        )
        viewm.add_radiobutton(
            label="Light theme", variable=self.theme_var, value="light", command=self._on_theme_change
        )
        menubar.add_cascade(label="View", menu=viewm)

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
        helpm.add_command(label="Setup guide", command=self._show_setup_guide)
        helpm.add_command(label="About", command=self._about)
        menubar.add_cascade(label="Help", menu=helpm)

        self.root.config(menu=menubar)

    def _build_top_bar(self) -> None:
        bar = ttk.Frame(self.root, padding=(8, 8, 8, 4))
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
        ttk.Button(side, text="■  Stop", style="Danger.TButton", command=self._stop).pack(fill=tk.X)
        ttk.Button(
            side, text="＋  Add clips", style="Accent.TButton", command=self._add_clips
        ).pack(fill=tk.X, pady=(6, 0))

        bar.columnconfigure(1, weight=1)

    def _build_options_bar(self) -> None:
        bar = ttk.Frame(self.root, padding=(8, 0, 8, 6))
        bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(bar, text="🔎 Search:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(bar, textvariable=self.search_var)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 14))
        self.search_var.trace_add("write", lambda *_: self._on_search())

        # Right group (packed right-to-left): [Delay (ms):] [spin]   [ ] Chime
        self.chime_var = tk.BooleanVar(value=self.cfg.chime_enabled)
        ttk.Checkbutton(
            bar, text="Chime before clip", variable=self.chime_var, command=self._on_chime_toggle
        ).pack(side=tk.RIGHT)
        self.delay_spin = ttk.Spinbox(
            bar, from_=0, to=5000, increment=100, width=6, command=self._on_delay_change
        )
        self.delay_spin.set(int(self.cfg.play_delay_ms))
        self.delay_spin.pack(side=tk.RIGHT, padx=(6, 16))
        self.delay_spin.bind("<FocusOut>", lambda e: self._on_delay_change())
        self.delay_spin.bind("<Return>", lambda e: self._on_delay_change())
        ttk.Label(bar, text="Delay (ms):").pack(side=tk.RIGHT)

    def _build_clip_area(self) -> None:
        container = ttk.Frame(self.root)
        container.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8)

        self.canvas = tk.Canvas(container, highlightthickness=0, bg=self.palette["bg"])
        vbar = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.grid_frame = ttk.Frame(self.canvas, padding=4)
        self._grid_window = self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind(
            "<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        self.clip_menu = tk.Menu(self.root, tearoff=0)
        self.clip_menu.add_command(label="Play", command=self._menu_play)
        self.clip_menu.add_command(label="Set / change hotkey…", command=self._menu_set_hotkey)
        self.clip_menu.add_command(label="Clear hotkey", command=self._menu_clear_hotkey)
        self.clip_menu.add_command(label="Set volume…", command=self._menu_set_volume)
        self.clip_menu.add_separator()
        self.clip_menu.add_command(label="Move up", command=lambda: self._menu_move(-1))
        self.clip_menu.add_command(label="Move down", command=lambda: self._menu_move(1))
        self.clip_menu.add_separator()
        self.clip_menu.add_command(label="Rename…", command=self._menu_rename)
        self.clip_menu.add_command(label="Remove", command=self._menu_remove)

        self._build_clip_buttons()

    def _build_status_bar(self) -> None:
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(
            self.root, textvariable=self.status_var, style="Status.TLabel", anchor="w"
        ).pack(side=tk.BOTTOM, fill=tk.X)

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-o>", lambda e: self._add_clips())
        self.root.bind("<Control-f>", self._focus_search)
        self.root.bind("<Escape>", self._on_escape)
        self.root.bind("<space>", self._on_space)

    # ===================================================================
    # Clip grid
    # ===================================================================
    def _compute_columns(self, width: int) -> int:
        return max(1, int(width) // TILE_MIN_WIDTH)

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._grid_window, width=event.width)
        cols = self._compute_columns(event.width)
        if cols != self._columns:
            self._columns = cols
            self._build_clip_buttons()

    def _empty_label(self, text: str) -> None:
        lbl = tk.Label(
            self.grid_frame,
            text=text,
            bg=self.palette["bg"],
            fg=self.palette["muted"],
            font=theme.FONT_BASE,
            justify="center",
        )
        lbl.grid(row=0, column=0, padx=30, pady=40)
        self.grid_frame.columnconfigure(0, weight=1, uniform="")

    def _build_clip_buttons(self) -> None:
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self._tiles = {}
        self._hl_index = None

        # Reset any column weights from a previous (wider) layout.
        for c in range(24):
            self.grid_frame.columnconfigure(c, weight=0, uniform="")

        if not self.cfg.clips:
            self._empty_label(
                "No clips yet.\n\nClick  ＋ Add clips  to choose audio files,\n"
                "then right-click a tile to assign a hotkey or set its volume."
            )
            return

        items = list(enumerate(self.cfg.clips))
        if self._search:
            items = [(i, c) for (i, c) in items if self._search in c.label.lower()]
        if not items:
            self._empty_label(f"No clips match “{self.search_var.get()}”.")
            return

        cols = max(1, self._columns)
        for c in range(cols):
            self.grid_frame.columnconfigure(c, weight=1, uniform="clipcol")
        avail = self.canvas.winfo_width() or 720
        wraplen = max(110, avail // cols - 44)

        for pos, (i, clip) in enumerate(items):
            row, col = divmod(pos, cols)
            tile = self._make_tile(self.grid_frame, clip, i, wraplen)
            tile.grid(row=row, column=col, sticky="nsew", padx=5, pady=5)
            self._tiles[i] = tile

    def _tile_border(self, index: int) -> str:
        if index == self._playing_index and self.player.is_playing():
            return self.palette["accent"]
        return self.palette["surface"]

    def _make_tile(self, parent: tk.Widget, clip: config.Clip, index: int, wraplen: int) -> tk.Frame:
        p = self.palette
        border = self._tile_border(index)
        tile = tk.Frame(
            parent,
            bg=p["surface"],
            highlightthickness=2,
            highlightbackground=border,
            highlightcolor=border,
            cursor="hand2",
        )
        name = tk.Label(
            tile,
            text=clip.label,
            bg=p["surface"],
            fg=p["text"],
            font=theme.FONT_TILE,
            wraplength=wraplen,
            justify="center",
        )
        widgets = [tile, name]

        badges = []
        if clip.hotkey:
            badges.append(f"⌨ {clip.hotkey}")
        if abs(getattr(clip, "volume", 1.0) - 1.0) > 1e-3:
            badges.append(f"🔊 {int(round(clip.volume * 100))}%")
        if badges:
            name.pack(fill="x", padx=10, pady=(12, 2))
            badge = tk.Label(
                tile, text="   ".join(badges), bg=p["surface"], fg=p["muted"], font=theme.FONT_BADGE
            )
            badge.pack(pady=(0, 12))
            widgets.append(badge)
        else:
            name.pack(fill="x", padx=10, pady=16)

        def set_bg(color: str) -> None:
            for w in widgets:
                w.configure(bg=color)

        def on_enter(_e: tk.Event) -> None:
            set_bg(p["surface_hover"])
            tile.configure(highlightbackground=p["surface_hover"], highlightcolor=p["surface_hover"])

        def check_leave() -> None:
            under = self.root.winfo_containing(*self.root.winfo_pointerxy())
            if under not in widgets:
                set_bg(p["surface"])
                b = self._tile_border(index)
                tile.configure(highlightbackground=b, highlightcolor=b)

        def on_leave(_e: tk.Event) -> None:
            self.root.after_idle(check_leave)

        for w in widgets:
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)
            w.bind("<Button-1>", lambda _e, c=clip: self.trigger_play(c))
            w.bind("<Button-3>", lambda e, idx=index: self._popup_menu(e, idx))
        return tile

    def _refresh_highlight(self) -> None:
        playing = self._playing_index if self.player.is_playing() else None
        if playing == self._hl_index:
            return
        old = self._tiles.get(self._hl_index) if self._hl_index is not None else None
        if old is not None:
            s = self.palette["surface"]
            old.configure(highlightbackground=s, highlightcolor=s)
        new = self._tiles.get(playing) if playing is not None else None
        if new is not None:
            a = self.palette["accent"]
            new.configure(highlightbackground=a, highlightcolor=a)
        self._hl_index = playing

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

    def _menu_set_volume(self) -> None:
        clip = self.cfg.clips[self._menu_index]
        cur = int(round(getattr(clip, "volume", 1.0) * 100))
        val = simpledialog.askinteger(
            "Clip volume",
            f"Volume for “{clip.label}” (%)\n100 = normal, up to 200 to boost:",
            initialvalue=cur,
            minvalue=0,
            maxvalue=200,
            parent=self.root,
        )
        if val is not None:
            clip.volume = val / 100.0
            self._save()
            self._build_clip_buttons()

    def _menu_move(self, delta: int) -> None:
        i = self._menu_index
        j = i + delta
        if 0 <= j < len(self.cfg.clips):
            self.cfg.clips[i], self.cfg.clips[j] = self.cfg.clips[j], self.cfg.clips[i]
            self._save()
            self._build_clip_buttons()

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
        clip = self.cfg.clips[self._menu_index]
        if not messagebox.askyesno(
            "Remove clip", f"Remove “{clip.label}” from the board?", parent=self.root
        ):
            return
        self.cfg.clips.pop(self._menu_index)
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
    # Volume / delay / chime / theme / search
    # ===================================================================
    def _on_volume(self, value: str) -> None:
        v = float(value) / 100.0
        self.player.set_volume(v)
        self.cfg.master_volume = v

    def _on_delay_change(self) -> None:
        raw = self.delay_spin.get()
        try:
            v = int(float(raw))
        except (TypeError, ValueError):
            v = 0
        v = max(0, min(5000, v))
        if str(v) != str(raw):
            self.delay_spin.set(v)
        if v != self.cfg.play_delay_ms:
            self.cfg.play_delay_ms = v
            self._save()

    def _on_chime_toggle(self) -> None:
        self.cfg.chime_enabled = self.chime_var.get()
        self._save()
        self._set_status("Chime " + ("on" if self.cfg.chime_enabled else "off"), 2)

    def _on_theme_change(self) -> None:
        mode = self.theme_var.get()
        self.cfg.theme = mode
        self.palette = theme.apply_theme(self.root, mode)
        self.canvas.configure(bg=self.palette["bg"])
        self._build_clip_buttons()
        self._save()

    def _on_search(self) -> None:
        self._search = self.search_var.get().strip().lower()
        self._build_clip_buttons()

    def _focus_search(self, _event: tk.Event) -> str:
        self.search_entry.focus_set()
        self.search_entry.select_range(0, tk.END)
        return "break"

    def _on_escape(self, _event: tk.Event) -> str:
        if self.root.focus_get() is self.search_entry:
            if self.search_var.get():
                self.search_var.set("")
            else:
                self.root.focus_set()
            return "break"
        self._stop()
        return "break"

    def _on_space(self, _event: tk.Event) -> str | None:
        w = self.root.focus_get()
        if isinstance(w, (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Spinbox, tk.Button, ttk.Button)):
            return None
        self._stop()
        return "break"

    # ===================================================================
    # Clips: add / play / stop
    # ===================================================================
    def _add_clips(self) -> None:
        paths = filedialog.askopenfilenames(title="Add audio clips", filetypes=AUDIO_FILETYPES)
        if not paths:
            return
        for p in paths:
            label = os.path.splitext(os.path.basename(p))[0]
            self.cfg.clips.append(config.Clip(path=p, label=label))
        self._save()
        self._build_clip_buttons()
        self._apply_hotkeys()
        self._warm_cache()

    def trigger_play(self, clip: config.Clip) -> None:
        """Play a clip: chime → delay → clip. Safe to call from any thread."""
        self._last_played = clip.label
        self._playing_index = next(
            (i for i, c in enumerate(self.cfg.clips) if c is clip), None
        )
        self._play_token += 1
        token = self._play_token
        chime = self.cfg.chime_enabled
        delay = max(0, self.cfg.play_delay_ms) / 1000.0
        gain = float(getattr(clip, "volume", 1.0))

        def work() -> None:
            try:
                if chime:
                    dur = self.player.play_chime()
                    if dur:
                        time.sleep(dur)
                if delay:
                    time.sleep(delay)
                if token != self._play_token:  # superseded or stopped during pre-roll
                    return
                self.player.play(clip.path, gain=gain)
            except Exception as exc:  # noqa: BLE001 - surface any decode/device error
                self._cmd_q.put(("error", f"Couldn't play '{clip.label}': {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def _stop(self) -> None:
        self._play_token += 1  # cancel any pending chime/delay pre-roll
        self.player.stop()

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
            mapping[self.cfg.stop_hotkey] = self._stop
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

        self._refresh_highlight()

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

    def _show_setup_guide(self) -> None:
        p = self.palette
        win = tk.Toplevel(self.root)
        win.title("MicDrop — Setup guide")
        win.geometry("580x560")
        win.configure(bg=p["bg"])
        win.transient(self.root)
        try:
            win.iconbitmap(os.path.join(_ASSETS, "icon.ico"))
        except tk.TclError:
            pass

        frame = ttk.Frame(win, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        txt = tk.Text(
            frame,
            wrap="word",
            bg=p["surface"],
            fg=p["text"],
            insertbackground=p["text"],
            relief="flat",
            padx=14,
            pady=12,
            font=theme.FONT_BASE,
            highlightthickness=0,
        )
        sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        txt.insert("1.0", SETUP_GUIDE)
        txt.configure(state="disabled")
        ttk.Button(win, text="Close", style="Accent.TButton", command=win.destroy).pack(pady=(0, 10))

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
    MicDropApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
