"""Safe keyboard actions for Phase 1 observation only."""

from __future__ import annotations

import time

try:
    import pyautogui
except ImportError:
    pyautogui = None  # type: ignore[assignment]

FORBIDDEN_KEYS = {
    "enter",
    "return",
    "y",
    "n",
    "delete",
    "backspace",
}

SAFE_KEYS = {"alt", "tab", "esc", "escape"}


def _require_pyautogui() -> None:
    if pyautogui is None:
        raise ImportError("pyautogui is not installed. Install with: pip install pyautogui")
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.5


def press_key(key: str) -> None:
    _require_pyautogui()
    normalized = key.lower()
    if normalized in FORBIDDEN_KEYS:
        raise ValueError(f"Forbidden key in Phase 1: {key}")
    pyautogui.press(key)


def hotkey(*keys: str) -> None:
    _require_pyautogui()
    lowered = [k.lower() for k in keys]
    if any(k in FORBIDDEN_KEYS for k in lowered):
        raise ValueError(f"Forbidden hotkey in Phase 1: {'+'.join(keys)}")
    pyautogui.hotkey(*keys)


def press_escape() -> None:
    press_key("esc")


def alt_tab_once() -> None:
    hotkey("alt", "tab")
    time.sleep(0.8)


def open_dialog_ctrl_o() -> None:
    """Safe observation shortcut — opens Open Project dialog only."""
    hotkey("ctrl", "o")
    time.sleep(1.0)
