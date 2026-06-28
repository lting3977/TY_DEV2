"""Prepare Primavera P6 for safe Phase 1 observation tests."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from accessibility.hand import window_tools
from eye.screenshot import rect_from_window_state, validate_p6_rect


STABILITY_WAIT_SECONDS = 1.5
POST_MAXIMIZE_WAIT_SECONDS = 0.8


def get_fresh_p6_rect(title_keyword: str) -> Dict[str, Any]:
    """Read current P6 window rectangle without changing window state."""
    state = window_tools.get_window_state(title_keyword)
    rect = rect_from_window_state(state)
    valid, reason = validate_p6_rect(rect, is_minimized=state.get("is_minimized"))
    return {
        "success": valid,
        "rect": rect,
        "rect_dict": rect.to_dict() if rect else None,
        "window_state": state,
        "message": reason if not valid else "ok",
    }


def prepare_p6_for_test(title_keyword: str) -> Dict[str, Any]:
    """
    Restore, focus, maximise P6, wait for stability, return fresh rectangle.

    Never OCRs. Never falls back to full-screen capture.
    """
    state = window_tools.get_window_state(title_keyword)
    if not state.get("found"):
        return {
            "success": False,
            "rect": None,
            "rect_dict": None,
            "window_state": state,
            "message": f"No P6 window found matching: {title_keyword}",
            "steps": ["find_window:fail"],
        }

    steps = []

    if state.get("is_minimized"):
        result = window_tools.activate_window_by_title(title_keyword)
        steps.append(f"restore_minimised:{result.get('success')}")
        time.sleep(0.6)

    activate = window_tools.activate_window_by_title(title_keyword)
    steps.append(f"activate:{activate.get('success')}")
    time.sleep(0.6)

    maximize = window_tools.maximize_window_by_title(title_keyword)
    steps.append(f"maximize:{maximize.get('success')}")
    time.sleep(POST_MAXIMIZE_WAIT_SECONDS)

    time.sleep(STABILITY_WAIT_SECONDS)

    fresh = get_fresh_p6_rect(title_keyword)
    fresh["steps"] = steps
    if fresh["success"]:
        fresh["message"] = "P6 prepared and rectangle validated"
    else:
        fresh["message"] = (
            "P6 preparation completed but rectangle invalid: " + fresh["message"]
        )
    return fresh
