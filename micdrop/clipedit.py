"""The Edit clip screen: label, waveform trim, volume and hotkey in one place.

Edits apply to the Clip immediately and config.json is written once on close --
matching the rest of the app, where every edit commits as you make it. Hotkey
assignment in particular has to be immediate, because it de-duplicates the new
combo against every other clip.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import audio

PEAK_COLUMNS = 2000  # resolution peaks are computed at, then re-bucketed to fit
MIN_RANGE_MS = 50  # handles may not collapse closer than this
NUDGE_MS = 10  # arrow-key step
NUDGE_COARSE_MS = 100  # shift+arrow step
HANDLE_GRAB_PX = 8  # click this close to a handle to grab it
WAVE_HEIGHT = 110


def format_ms(ms: int) -> str:
    """Render a position as m:ss.t -- e.g. 400 -> '0:00.4'.

    Truncates rather than rounds, so the readout never claims a position past
    the handle the user is dragging.
    """
    tenths = max(0, int(ms)) // 100
    minutes, rem = divmod(tenths, 600)
    seconds, tenth = divmod(rem, 10)
    return f"{minutes}:{seconds:02d}.{tenth}"


def trim_badge(start_ms: int, end_ms: int) -> str:
    """Tile badge describing a trim, or '' when the clip plays in full."""
    if start_ms <= 0 and end_ms <= 0:
        return ""
    if start_ms > 0 and end_ms > 0:
        return f"\u2702 {format_ms(start_ms)}\u2013{format_ms(end_ms)}"
    if start_ms > 0:
        return f"\u2702 from {format_ms(start_ms)}"
    return f"\u2702 to {format_ms(end_ms)}"


class ClipEditor(tk.Toplevel):
    """Modal editor for one clip. Edits apply live; config is saved on close."""

    def __init__(self, app, index: int) -> None:
        super().__init__(app.root)
        self.app = app
        self.index = index
        self.clip = app.cfg.clips[index]
        self.p = app.palette

        self._peaks = None
        self._duration_ms = 0
        self._start = 0
        self._end = 0
        self._drag: str | None = None
        self._grabbed = "start"  # which handle the arrow keys move
        self._capture_is_mine = False  # did WE start the capture that is in flight?

        self.title(f"Edit clip \u2014 {self.clip.label}")
        self.configure(bg=self.p["bg"])
        self.transient(app.root)
        self.resizable(True, False)

        # Clip hotkeys are OS-level and fire regardless of focus; a stray
        # ctrl+alt+1 mid-trim would launch a clip. Restored on close.
        self._hotkeys_were = app.hotkeys.enabled
        app.hotkeys.set_enabled(False)

        self._build()
        self._load_audio()

        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", self._on_escape)
        self.bind("<Left>", lambda e: self._on_arrow(e, -1))
        self.bind("<Right>", lambda e: self._on_arrow(e, +1))
        self.grab_set()
        self.focus_set()

    # -- construction -----------------------------------------------------
    def _build(self) -> None:
        p = self.p
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        row = ttk.Frame(outer)
        row.pack(fill="x")
        ttk.Label(row, text="Label").pack(side="left")
        self.label_var = tk.StringVar(value=self.clip.label)
        self.label_var.trace_add("write", self._on_label)
        ttk.Entry(row, textvariable=self.label_var).pack(
            side="left", fill="x", expand=True, padx=(10, 0)
        )

        self.canvas = tk.Canvas(
            outer,
            height=WAVE_HEIGHT,
            bg=p["surface"],
            highlightthickness=1,
            highlightbackground=p["border"],
        )
        self.canvas.pack(fill="x", pady=(12, 4))
        self.canvas.bind("<Configure>", lambda _e: self._redraw())
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        self.times = ttk.Label(outer, style="Muted.TLabel", text="")
        self.times.pack(fill="x")

        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(12, 0))
        ttk.Label(row, text="Volume").pack(side="left")
        self.vol_var = tk.DoubleVar(value=self.clip.volume * 100)
        ttk.Scale(
            row, from_=0, to=200, variable=self.vol_var, command=self._on_volume
        ).pack(side="left", fill="x", expand=True, padx=10)
        self.vol_label = ttk.Label(row, width=5, text=f"{int(round(self.clip.volume * 100))}%")
        self.vol_label.pack(side="left")

        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(10, 0))
        ttk.Label(row, text="Hotkey").pack(side="left")
        self.hotkey_label = ttk.Label(row, style="Muted.TLabel", text="")
        self.hotkey_label.pack(side="left", padx=10)
        ttk.Button(row, text="Clear", command=self._clear_hotkey).pack(side="right")
        ttk.Button(row, text="Set\u2026", command=self._set_hotkey).pack(side="right", padx=(0, 6))
        self.refresh_hotkey()

        self.hint = ttk.Label(outer, style="Muted.TLabel", text="")
        self.hint.pack(fill="x", pady=(10, 0))

        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(
            row, text="\u25b6  Preview", style="Accent.TButton", command=self._preview
        ).pack(side="left")
        ttk.Button(row, text="Reset trim", command=self._reset_trim).pack(side="left", padx=6)
        ttk.Button(row, text="Close", command=self._close).pack(side="right")

    def _load_audio(self) -> None:
        try:
            peaks, duration_s = self.app.player.peaks(self.clip.path, PEAK_COLUMNS)
        except Exception as exc:  # noqa: BLE001 - any decode/IO error is the same to us
            self._peaks = None
            self.canvas.delete("all")
            self.canvas.create_text(
                10,
                WAVE_HEIGHT // 2,
                anchor="w",
                fill=self.p["muted"],
                text=f"Can't read this clip: {exc}",
            )
            self.times.configure(text="")
            return
        self._peaks = peaks
        self._duration_ms = max(1, int(round(duration_s * 1000)))
        self._start = min(max(0, self.clip.start_ms), self._duration_ms - MIN_RANGE_MS)
        self._start = max(0, self._start)
        end = self.clip.end_ms
        self._end = self._duration_ms if end <= 0 or end > self._duration_ms else end
        self._end = max(self._end, self._start + MIN_RANGE_MS)
        self._end = min(self._end, self._duration_ms)  # a clip under MIN_RANGE_MS must not push the handle off-canvas
        self._apply()  # sync the clip before the first paint, so Preview matches what is drawn
        self._redraw()
        self._update_times()

    # -- geometry ---------------------------------------------------------
    def _ms_to_x(self, ms: int, w: int) -> int:
        return int(round(ms / self._duration_ms * (w - 1))) if self._duration_ms else 0

    def _x_to_ms(self, x: int, w: int) -> int:
        return int(round(x / (w - 1) * self._duration_ms)) if w > 1 else 0

    def _envelope(self, cols: list[float], lo: int, hi: int, h: int) -> list[float]:
        """Polygon points tracing the top of the envelope and back along the bottom."""
        mid = h / 2
        amp = mid - 4
        pts: list[float] = []
        for x in range(lo, hi + 1):
            pts += [float(x), mid - cols[x] * amp]
        for x in range(hi, lo - 1, -1):
            pts += [float(x), mid + cols[x] * amp]
        return pts

    # -- drawing ----------------------------------------------------------
    def _redraw(self) -> None:
        c = self.canvas
        if self._peaks is None:
            return
        c.delete("all")
        w = max(2, c.winfo_width())
        h = max(2, c.winfo_height())
        cols = audio.rebucket_peaks(self._peaks, w)
        if len(cols) < 2:
            return

        # Two polygons, not one line per pixel: a drag redraws hundreds of
        # times and per-pixel canvas items make it crawl.
        c.create_polygon(self._envelope(cols, 0, len(cols) - 1, h), fill=self.p["muted"], outline="")
        ix0 = max(0, min(self._ms_to_x(self._start, w), len(cols) - 1))
        ix1 = max(0, min(self._ms_to_x(self._end, w), len(cols) - 1))
        if ix1 > ix0:
            c.create_polygon(self._envelope(cols, ix0, ix1, h), fill=self.p["accent"], outline="")
        for x in (ix0, ix1):
            c.create_line(x, 0, x, h, fill=self.p["text"], width=2)

    def _update_times(self) -> None:
        kept = max(0, self._end - self._start) / 1000.0
        self.times.configure(
            text=f"Start {format_ms(self._start)}     "
            f"End {format_ms(self._end)}     ({kept:.1f} s)"
        )

    def _set_hint(self, text: str) -> None:
        self.hint.configure(text=text)

    # -- trim editing -----------------------------------------------------
    def _set_handle(self, which: str, ms: int) -> None:
        ms = max(0, min(int(ms), self._duration_ms))
        if which == "start":
            self._start = max(0, min(ms, self._end - MIN_RANGE_MS))
        else:
            self._end = min(self._duration_ms, max(ms, self._start + MIN_RANGE_MS))
        self._apply()
        self._redraw()
        self._update_times()

    def _apply(self) -> None:
        """Push handle positions onto the Clip.

        An end at the tail is stored as 0 ('natural end') so the trim survives
        the file being replaced with a longer or shorter one.
        """
        if self._peaks is None:
            return  # audio never loaded; the clip's stored trim must survive untouched
        self.clip.start_ms = int(self._start) if self._start > 0 else 0
        self.clip.end_ms = 0 if self._end >= self._duration_ms else int(self._end)

    def _on_press(self, event: tk.Event) -> None:
        if self._peaks is None:
            return
        w = max(2, self.canvas.winfo_width())
        d0 = abs(event.x - self._ms_to_x(self._start, w))
        d1 = abs(event.x - self._ms_to_x(self._end, w))
        if min(d0, d1) > HANDLE_GRAB_PX:
            return  # clicks away from a handle do nothing, so trims survive a stray click
        self._drag = "start" if d0 <= d1 else "end"
        self._grabbed = self._drag

    def _on_motion(self, event: tk.Event) -> None:
        if self._drag is None:
            return
        self._set_handle(self._drag, self._x_to_ms(event.x, max(2, self.canvas.winfo_width())))

    def _on_release(self, _event: tk.Event) -> None:
        self._drag = None

    def _on_arrow(self, event: tk.Event, direction: int) -> str | None:
        # Arrow keys must still move the caret when the label field has focus,
        # and TScale's own class bindings must not also nudge a trim handle.
        if isinstance(self.focus_get(), (tk.Entry, ttk.Entry, ttk.Scale)):
            return None
        if self._peaks is None:
            return None
        step = NUDGE_COARSE_MS if (event.state & 0x0001) else NUDGE_MS
        current = self._start if self._grabbed == "start" else self._end
        self._set_handle(self._grabbed, current + direction * step)
        return "break"

    def _reset_trim(self) -> None:
        if self._peaks is None:
            return
        self._start = 0
        self._end = self._duration_ms
        self._apply()
        self._redraw()
        self._update_times()
        self._set_hint("Trim reset \u2014 the whole clip plays.")

    # -- other fields -----------------------------------------------------
    def _on_label(self, *_args) -> None:
        text = self.label_var.get().strip()
        if text:  # ignore an empty box mid-typing rather than blanking the tile
            self.clip.label = text

    def _on_volume(self, _value: str) -> None:
        self.clip.volume = self.vol_var.get() / 100.0
        self.vol_label.configure(text=f"{int(round(self.vol_var.get()))}%")

    def refresh_hotkey(self) -> None:
        """Re-read the clip's hotkey. Called by main.py when a capture finishes."""
        self.hotkey_label.configure(text=self.clip.hotkey or "none")

    def _set_hotkey(self) -> None:
        # _capture_hotkey silently no-ops when a capture is already running
        # (started from the main window, or by another editor); without this
        # guard we would claim ownership of -- and later tear down -- a
        # capture that was never ours to begin with.
        if getattr(self.app, "_capturing", False):
            self._set_hint("A hotkey capture is already running.")
            return
        self._capture_is_mine = True
        self.app._capture_hotkey(self.index)
        self._set_hint("Press a key combination now\u2026  (Esc cancels)")

    def _clear_hotkey(self) -> None:
        if self.clip.hotkey:
            self.app.hotkeys.unbind(self.clip.hotkey)
            self.clip.hotkey = ""
            self.refresh_hotkey()
            self._set_hint("Hotkey cleared.")

    def _preview(self) -> None:
        try:
            duration = self.app.player.play(
                self.clip.path,
                gain=float(self.clip.volume),
                start_ms=self.clip.start_ms,
                end_ms=self.clip.end_ms,
                monitor_only=True,
            )
        except Exception as exc:  # noqa: BLE001
            self._set_hint(f"Preview failed: {exc}")
            return
        if duration == 0.0:
            self._set_hint("Preview is silent \u2014 pick a monitor device on the main window.")
        else:
            self._set_hint("")

    # -- teardown ---------------------------------------------------------
    def _on_escape(self, _event: tk.Event) -> None:
        # The "(Esc cancels)" hint refers to the hotkey capture, not the
        # dialog; while one is in flight Escape must not tear down the
        # editor out from under it.
        if self._capture_is_mine and getattr(self.app, "_capturing", False):
            return  # Escape cancels OUR capture; do not tear the dialog down
        self._close()

    def _close(self) -> None:
        self._apply()
        if self._capture_is_mine and getattr(self.app, "_capturing", False):
            # A capture WE started is still running. Its completion handler
            # would restore the state it sampled while we had hotkeys
            # suspended, undoing the line below; tell it we have already
            # done the restoring. A capture we never started (e.g. one
            # pending from the main window) is not ours to claim -- its own
            # completion handler must still run its own restore.
            self.app._hotkeys_restore_pending = True
        self.app.hotkeys.set_enabled(self._hotkeys_were)
        self.app._editor = None
        self.app._save()
        self.app._build_clip_buttons()
        self.app._apply_hotkeys()
        self.grab_release()
        self.destroy()
