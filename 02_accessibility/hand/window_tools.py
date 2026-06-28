"""Window management with optional pygetwindow support."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


def _pygetwindow_available() -> bool:
    try:
        import pygetwindow  # noqa: F401
        return True
    except ImportError:
        return False


def _missing_pygetwindow_message() -> str:
    return "pygetwindow is not installed. Install with: pip install pygetwindow"


def find_windows_by_title(title_keyword: str) -> List[Any]:
    if not _pygetwindow_available():
        return []
    import pygetwindow as gw

    return [
        window
        for window in gw.getAllWindows()
        if title_keyword.lower() in (window.title or "").lower()
    ]


def get_window_state(title_keyword: str) -> Dict[str, Any]:
    matches = find_windows_by_title(title_keyword)
    if not matches:
        return {
            "found": False,
            "title": None,
            "is_minimized": None,
            "is_maximized": None,
            "left": None,
            "top": None,
            "width": None,
            "height": None,
        }
    window = matches[0]
    return {
        "found": True,
        "title": window.title,
        "is_minimized": bool(getattr(window, "isMinimized", False)),
        "is_maximized": bool(getattr(window, "isMaximized", False)),
        "left": getattr(window, "left", None),
        "top": getattr(window, "top", None),
        "width": getattr(window, "width", None),
        "height": getattr(window, "height", None),
    }


def activate_window_by_title(title_keyword: str) -> Dict[str, Any]:
    if not _pygetwindow_available():
        message = _missing_pygetwindow_message()
        return {"success": False, "message": message}

    matches = find_windows_by_title(title_keyword)
    if not matches:
        return {"success": False, "message": f"No window found matching: {title_keyword}"}

    window = matches[0]
    try:
        if window.isMinimized:
            window.restore()
            time.sleep(0.5)
        window.activate()
        time.sleep(0.6)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": f"Failed to activate '{window.title}': {exc}"}
    return {"success": True, "title": window.title}


def maximize_window_by_title(title_keyword: str) -> Dict[str, Any]:
    if not _pygetwindow_available():
        return {"success": False, "message": _missing_pygetwindow_message()}

    matches = find_windows_by_title(title_keyword)
    if not matches:
        return {"success": False, "message": f"No window found matching: {title_keyword}"}

    window = matches[0]
    try:
        if window.isMinimized:
            window.restore()
            time.sleep(0.5)
        window.activate()
        time.sleep(0.6)
        if not window.isMaximized:
            window.maximize()
        time.sleep(0.5)
        window.activate()
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": f"Failed to maximize '{window.title}': {exc}"}
    return {"success": True, "title": window.title}


def minimize_window_by_title(title_keyword: str) -> Dict[str, Any]:
    if not _pygetwindow_available():
        return {"success": False, "message": _missing_pygetwindow_message()}

    matches = find_windows_by_title(title_keyword)
    if not matches:
        return {"success": False, "message": f"No window found matching: {title_keyword}"}

    window = matches[0]
    try:
        window.minimize()
        time.sleep(0.5)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": f"Failed to minimize '{window.title}': {exc}"}
    return {"success": True, "title": window.title}


def restore_without_maximize(title_keyword: str) -> Dict[str, Any]:
    if not _pygetwindow_available():
        return {"success": False, "message": _missing_pygetwindow_message()}

    matches = find_windows_by_title(title_keyword)
    if not matches:
        return {"success": False, "message": f"No window found matching: {title_keyword}"}

    window = matches[0]
    try:
        if window.isMinimized:
            window.restore()
            time.sleep(0.5)
        if window.isMaximized:
            window.restore()
            time.sleep(0.5)
        window.activate()
        time.sleep(0.6)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": f"Failed to restore '{window.title}': {exc}"}
    return {"success": True, "title": window.title, "is_maximized": window.isMaximized}


def move_window_by_title(title_keyword: str, left: int, top: int) -> Dict[str, Any]:
    if not _pygetwindow_available():
        return {"success": False, "message": _missing_pygetwindow_message()}

    matches = find_windows_by_title(title_keyword)
    if not matches:
        return {"success": False, "message": f"No window found matching: {title_keyword}"}

    window = matches[0]
    try:
        window.moveTo(left, top)
        time.sleep(0.5)
        window.activate()
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": f"Failed to move '{window.title}': {exc}"}
    return {"success": True, "title": window.title, "left": left, "top": top}


def activate_window_by_partial_title(title_keyword: str) -> Dict[str, Any]:
    return activate_window_by_title(title_keyword)


def get_active_window_title() -> Optional[str]:
    if not _pygetwindow_available():
        return None
    import pygetwindow as gw

    window = gw.getActiveWindow()
    return window.title if window else None
