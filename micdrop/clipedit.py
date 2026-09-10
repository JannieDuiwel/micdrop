"""The Edit clip screen: label, waveform trim, volume and hotkey in one place.

Edits apply to the Clip immediately and config.json is written once on close --
matching the rest of the app, where every edit commits as you make it. Hotkey
assignment in particular has to be immediate, because it de-duplicates the new
combo against every other clip.
"""

from __future__ import annotations


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
