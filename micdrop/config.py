"""JSON persistence for the soundboard (clips, hotkeys, device, volume).

The output device is stored by *name* (stable across reboots) rather than index.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields

# config.json lives in the project root, next to the micdrop/ package.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")


@dataclass
class Clip:
    path: str
    label: str
    hotkey: str = ""  # e.g. "ctrl+alt+1"; empty = no hotkey
    volume: float = 1.0  # per-clip gain (0.0–1.0), multiplied with master volume

    @classmethod
    def from_dict(cls, d: dict) -> "Clip":
        """Build a Clip, ignoring unknown keys so newer configs never crash us."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Config:
    output_device_name: str = ""
    output_device_hostapi: str = ""
    monitor_device_name: str = ""  # "" = no monitor (don't play to a second device)
    monitor_device_hostapi: str = ""
    master_volume: float = 0.8
    hotkeys_enabled: bool = True
    stop_hotkey: str = "ctrl+alt+s"
    theme: str = "dark"  # "dark" | "light"
    play_delay_ms: int = 0  # global delay before a clip plays (after the chime)
    chime_enabled: bool = False  # play a short chime before each clip
    clips: list[Clip] = field(default_factory=list)

    # -- serialisation ----------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        clips = [Clip.from_dict(c) for c in d.get("clips", []) if isinstance(c, dict)]
        theme = d.get("theme", "dark")
        if theme not in ("dark", "light"):
            theme = "dark"
        return cls(
            output_device_name=d.get("output_device_name", ""),
            output_device_hostapi=d.get("output_device_hostapi", ""),
            monitor_device_name=d.get("monitor_device_name", ""),
            monitor_device_hostapi=d.get("monitor_device_hostapi", ""),
            master_volume=float(d.get("master_volume", 0.8)),
            hotkeys_enabled=bool(d.get("hotkeys_enabled", True)),
            stop_hotkey=d.get("stop_hotkey", "ctrl+alt+s"),
            theme=theme,
            play_delay_ms=int(d.get("play_delay_ms", 0)),
            chime_enabled=bool(d.get("chime_enabled", False)),
            clips=clips,
        )


def load_config(path: str = CONFIG_PATH) -> Config:
    if not os.path.exists(path):
        return Config()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return Config.from_dict(json.load(f))
    except (json.JSONDecodeError, TypeError, ValueError):
        # Corrupt config: start fresh rather than crash.
        return Config()


def save_config(cfg: Config, path: str = CONFIG_PATH) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg.to_dict(), f, indent=2)
    os.replace(tmp, path)
