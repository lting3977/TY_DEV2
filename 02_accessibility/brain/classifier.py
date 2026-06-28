"""Screen classification and popup detection for Phase 1."""

from __future__ import annotations

from typing import Any, Dict, List


def classify_screen_change(difference_percent: float) -> str:
    if difference_percent < 0.5:
        return "no visible change"
    if difference_percent < 5.0:
        return "small change"
    if difference_percent <= 25.0:
        return "medium change"
    return "major change"


def classify_p6_presence(
    entries: List[Dict[str, Any]],
    p6_keywords: List[str],
    min_confidence: float,
) -> Dict[str, Any]:
    blob = " ".join(
        e["normalized"] for e in entries if e.get("confidence", 0) >= min_confidence
    )
    hits = [kw for kw in p6_keywords if kw.lower() in blob]
    if len(hits) >= 2:
        level = "strong"
    elif len(hits) == 1:
        level = "weak"
    else:
        level = "none"
    return {"level": level, "hits": hits, "blob_excerpt": blob[:500]}


def classify_workspace(entries: List[Dict[str, Any]], min_confidence: float) -> str:
    blob = " ".join(
        e["normalized"] for e in entries if e.get("confidence", 0) >= min_confidence
    )
    if "activities" in blob:
        return "activities"
    if "projects" in blob or "eps" in blob:
        return "projects"
    return "unknown"


def classify_popup_buttons(
    entries: List[Dict[str, Any]],
    button_keywords: List[str],
    min_confidence: float,
) -> Dict[str, bool]:
    blob = " ".join(
        e["normalized"] for e in entries if e.get("confidence", 0) >= min_confidence
    )
    return {kw: kw.lower() in blob for kw in button_keywords}


def classify_unknown_screen(
    p6_presence: Dict[str, Any],
    popup_buttons: Dict[str, bool],
    open_project_visible: bool,
) -> bool:
    if open_project_visible:
        return False
    if p6_presence["level"] == "strong":
        return False
    if any(popup_buttons.values()):
        return False
    return True
