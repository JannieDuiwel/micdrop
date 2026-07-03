"""Global hotkey manager built on the `keyboard` library.

Hotkeys are system-wide (fire while a game is focused). They are *not* suppressed,
so the key still reaches the game. Registration can be toggled off entirely for
anti-cheat-sensitive games.

Callbacks run on the keyboard listener thread, so GUI code passed in as a callback
must marshal back to the Tk main thread itself (the GUI uses root.after for this).
"""

from __future__ import annotations

from typing import Callable

import keyboard


class HotkeyManager:
    def __init__(self) -> None:
        self._enabled = True
        self._bindings: dict[str, Callable[[], None]] = {}
        self._handles: dict[str, object] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if enabled:
            self._apply_all()
        else:
            self._remove_all()

    def bind(self, hotkey: str, callback: Callable[[], None]) -> tuple[bool, str]:
        """Add or replace a single binding. Returns (ok, error_message)."""
        if not hotkey:
            return True, ""
        self.unbind(hotkey)
        self._bindings[hotkey] = callback
        if self._enabled:
            return self._add(hotkey)
        return True, ""

    def unbind(self, hotkey: str) -> None:
        self._bindings.pop(hotkey, None)
        handle = self._handles.pop(hotkey, None)
        if handle is not None:
            try:
                keyboard.remove_hotkey(handle)
            except (KeyError, ValueError):
                pass

    def rebind_all(self, mapping: dict[str, Callable[[], None]]) -> list[str]:
        """Replace every binding. Returns a list of human-readable errors."""
        self.clear()
        errors = []
        for hotkey, callback in mapping.items():
            if not hotkey:
                continue
            ok, err = self.bind(hotkey, callback)
            if not ok:
                errors.append(f"{hotkey}: {err}")
        return errors

    def clear(self) -> None:
        self._remove_all()
        self._bindings.clear()

    # -- internals --------------------------------------------------------
    def _add(self, hotkey: str) -> tuple[bool, str]:
        try:
            handle = keyboard.add_hotkey(hotkey, self._bindings[hotkey], suppress=False)
            self._handles[hotkey] = handle
            return True, ""
        except (ValueError, ImportError, OSError) as exc:
            return False, str(exc)

    def _apply_all(self) -> None:
        for hotkey in list(self._bindings):
            self._add(hotkey)

    def _remove_all(self) -> None:
        for handle in self._handles.values():
            try:
                keyboard.remove_hotkey(handle)
            except (KeyError, ValueError):
                pass
        self._handles.clear()


def read_next_hotkey() -> str:
    """Block until the user presses a key combo; return it like 'ctrl+alt+1'.

    Must be called from a worker thread (it blocks). Not suppressed.
    """
    return keyboard.read_hotkey(suppress=False)
