"""
Shared export wizard helpers for M20+ (does not modify frozen M03-M19).

Read-only imports from frozen modules; new discovery flow helpers live here.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[assignment,misc]

from m06_go_to_activities import (
    CONFIG_PATH,
    SCREEN_RULE_PATH,
    STABILITY_WAIT,
    capture_and_ocr_step,
    confirm_project_open,
    confirms_activities_workspace,
    load_json,
    navigate_to_activities,
    write_json,
)
from eye.ocr import collect_text_blob, is_easyocr_available, normalize_text, ocr_to_entries, run_easyocr, save_ocr_results
from eye.screenshot import P6Rect, capture_p6_window_only
from hand.p6_prepare import get_fresh_p6_rect, prepare_p6_for_test
from accessibility.hand import window_tools
from accessibility.hand import keyboard_tools
from m16_discover_p6_export_menu import (
    click_ocr_entry,
    detect_m16_blocking_popup,
    export_dialog_detected,
    export_file_created,
    find_cancel_entry,
    find_export_evidence_words,
    open_export_menu,
    partial_export_discovery,
    refresh_p6_rect,
    snapshot_export_files,
)
from eye.ocr import check_ocr_pollution
from m18_select_spreadsheet_export_format_discovery_only import (
    confirm_spreadsheet_selected,
    detect_spreadsheet_in_blob,
    detect_wizard_buttons,
    find_next_entry,
    find_spreadsheet_entry,
    finish_pressed_in_steps,
)
from m19_discover_spreadsheet_export_type_options import (
    export_type_screen_detected,
    extract_export_type_dialog_blob,
    find_export_type_evidence_words,
)

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_STEP_MARKERS = (
    'press_key("y")',
    "press_key('y')",
    'press_key("n")',
    "press_key('n')",
    'press_key("finish")',
    "press_key('finish')",
    "ctrl+s",
    "ctrl+p",
    "f9",
    "browse",
    "modify template",
    "delete template",
    "add template",
)


@dataclass
class ExportWizardEvidence:
    run_id: str
    folder: Path
    module_name: str
    screenshots_dir: Path
    ocr_dir: Path
    classification_dir: Path
    popup_dir: Path
    discovery_dir: Path
    steps: List[str] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    ocr_files: List[str] = field(default_factory=list)
    classification_files: List[str] = field(default_factory=list)
    popup_files: List[str] = field(default_factory=list)
    discovery_files: List[str] = field(default_factory=list)


def build_export_evidence(run_id: str, module_name: str) -> ExportWizardEvidence:
    folder = ROOT / "06_output" / "runs" / run_id / module_name
    for sub in ("screenshots", "ocr", "classification", "popup", "discovery"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    return ExportWizardEvidence(
        run_id=run_id,
        folder=folder,
        module_name=module_name,
        screenshots_dir=folder / "screenshots",
        ocr_dir=folder / "ocr",
        classification_dir=folder / "classification",
        popup_dir=folder / "popup",
        discovery_dir=folder / "discovery",
    )


def save_discovery(evidence: ExportWizardEvidence, filename: str, payload: Dict[str, Any]) -> str:
    path = evidence.discovery_dir / filename
    write_json(path, payload)
    evidence.discovery_files.append(str(path))
    return str(path)


def count_next_presses(steps: List[str]) -> int:
    count = 0
    for step in steps:
        lowered = step.lower()
        if "press next once" in lowered or "ocr-confirmed next click" in lowered:
            count += 1
        elif 'press_key("next")' in lowered or "press_key('next')" in lowered:
            count += 1
    return count


def count_next_after_marker(steps: List[str], marker: str) -> int:
    seen = False
    count = 0
    for step in steps:
        lowered = step.lower()
        if not seen:
            if marker in lowered:
                seen = True
            continue
        if "press next once" in lowered or "ocr-confirmed next click" in lowered:
            count += 1
    return count


def unsafe_steps_detected(steps: List[str]) -> Tuple[bool, List[str]]:
    hits: List[str] = []
    for step in steps:
        lowered = step.lower()
        for marker in FORBIDDEN_STEP_MARKERS:
            if marker in lowered:
                hits.append(f"{step} ({marker})")
    return len(hits) == 0, hits


def capture_p6_with_pollution_retry(
    evidence: ExportWizardEvidence,
    label: str,
    p6_rect: P6Rect,
    p6_keyword: str,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
) -> Tuple[Dict[str, Any], P6Rect]:
    """Capture P6 crop; on OCR pollution retry prepare/focus once then recapture."""
    cap, p6_rect, _, err = m20_step_capture(
        evidence, label, p6_rect, p6_keyword, config, screen_rule, min_confidence
    )
    if err:
        cap = cap or {"ok": False, "error": err.get("reason", "capture failed"), "polluted": True}
    return cap, p6_rect


M20_POLLUTION_KEYWORDS = (
    "agent",
    "cursor",
    "chatgpt",
    "ty_dev2",
    "task",
    "orchestrator",
)


def check_m20_pollution(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Dict[str, Any]:
    return check_ocr_pollution(list(entries), list(M20_POLLUTION_KEYWORDS), min_confidence)


def entry_bbox_dict(entry: Optional[Dict[str, Any]]) -> Optional[List[List[float]]]:
    if not entry:
        return None
    return entry.get("bbox")


def bbox_center(entry: Dict[str, Any]) -> Tuple[float, float]:
    xs = [p[0] for p in entry["bbox"]]
    ys = [p[1] for p in entry["bbox"]]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def click_point_from_entry(p6_rect: P6Rect, entry: Dict[str, Any]) -> Dict[str, int]:
    cx, cy = bbox_center(entry)
    return {"x": int(p6_rect.left + cx), "y": int(p6_rect.top + cy)}


def estimate_wizard_bounds(entries: List[Dict[str, Any]], min_confidence: float) -> Dict[str, float]:
    y_min = 400.0
    y_max = 950.0
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        norm = entry.get("normalized", "")
        if norm in ("cancel", "next", "back", "finish") or "export type" in norm or "export format" in norm:
            ys = [p[1] for p in entry["bbox"]]
            yc = sum(ys) / len(ys)
            y_min = min(y_min, yc - 80)
            y_max = max(y_max, yc + 40)
    return {"y_min": y_min, "y_max": y_max}


def next_in_wizard_bounds(entry: Dict[str, Any], bounds: Dict[str, float]) -> bool:
    cx, yc = bbox_center(entry)
    x_min = bounds.get("x_min", 0.0)
    x_max = bounds.get("x_max", 99999.0)
    y_min = bounds.get("y_min", 400.0)
    y_max = bounds.get("y_max", 950.0)
    return x_min <= cx <= x_max and y_min <= yc <= y_max and yc >= 700


def m20_build_step_evidence(
    *,
    entry: Optional[Dict[str, Any]],
    p6_rect: P6Rect,
    cap: Dict[str, Any],
    pollution_meta: Dict[str, Any],
    p6_keyword: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    entries = cap.get("entries", [])
    bounds = estimate_wizard_bounds(entries, 0.5)
    payload: Dict[str, Any] = {
        "ocr_text": entry.get("text", "") if entry else "",
        "target_bbox": entry_bbox_dict(entry),
        "click_point": click_point_from_entry(p6_rect, entry) if entry else None,
        "wizard_bounds": bounds,
        "screen_classification": cap.get("screen_state", "unknown"),
        "pollution_detected": bool(pollution_meta.get("pollution_detected")),
        "pollution_recovered": bool(pollution_meta.get("pollution_recovered")),
        "pollution_words": pollution_meta.get("pollution_words", []),
        "foreground_title": window_tools.get_window_state(p6_keyword).get("title") or "",
    }
    if extra:
        payload.update(extra)
    return payload


def m20_step_capture(
    evidence: ExportWizardEvidence,
    label: str,
    p6_rect: P6Rect,
    p6_keyword: str,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    *,
    wizard_bounds: Optional[Dict[str, float]] = None,
) -> Tuple[Dict[str, Any], P6Rect, Dict[str, Any], Optional[Dict[str, Any]]]:
    """P6 foreground + crop OCR with single pollution retry; wizard crop when bounds cached."""
    pollution_meta: Dict[str, Any] = {
        "pollution_detected": False,
        "pollution_recovered": False,
        "pollution_words": [],
    }
    window_tools.activate_window_by_title(p6_keyword)
    time.sleep(0.3)

    def do_capture(lbl: str) -> Dict[str, Any]:
        if wizard_bounds:
            return ocr_wizard_crop(
                evidence, lbl, p6_rect, wizard_bounds, config, screen_rule, min_confidence
            )
        cap = capture_and_ocr_step(evidence, lbl, p6_rect, config, screen_rule)
        if cap.get("ok"):
            blob = collect_text_blob(cap["entries"], min_confidence)
            cap["screen_state"] = classify_m20_screen_state(cap["entries"], blob, min_confidence)
            cap["ocr_mode"] = "p6_full"
        return cap

    cap = do_capture(label)
    polluted = (not cap.get("ok") and cap.get("polluted")) or (
        cap.get("ok") and check_m20_pollution(cap.get("entries", []), min_confidence)["polluted"]
    )
    if polluted:
        pollution_meta["pollution_detected"] = True
        words = check_m20_pollution(cap.get("entries", []), min_confidence).get("pollution_words", [])
        if not words and cap.get("error"):
            pollution_meta["pollution_words"] = [
                w.strip().strip("'") for w in cap.get("error", "").replace("OCR pollution:", "").strip("[]").split(",")
            ]
        else:
            pollution_meta["pollution_words"] = list(words)
        evidence.steps.append(f"M20: pollution on {label} — refocus P6 and recapture once")
        prepare_p6_for_test(p6_keyword)
        window_tools.activate_window_by_title(p6_keyword)
        time.sleep(1.0)
        p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
        cap = do_capture(f"{label}_pollution_retry")
        still_polluted = (not cap.get("ok") and cap.get("polluted")) or (
            cap.get("ok") and check_m20_pollution(cap.get("entries", []), min_confidence)["polluted"]
        )
        if still_polluted:
            words2 = check_m20_pollution(cap.get("entries", []), min_confidence).get("pollution_words", [])
            pollution_meta["pollution_words"] = list(words2 or pollution_meta["pollution_words"])
            return cap, p6_rect, pollution_meta, {
                "status": "MANUAL_REVIEW_CANNOT_CONFIRM",
                "reason": f"OCR pollution persists after retry: {pollution_meta['pollution_words']}",
                "manual_review_required": True,
            }
        pollution_meta["pollution_recovered"] = True
    if not cap.get("ok"):
        return cap, p6_rect, pollution_meta, {
            "status": "MANUAL_REVIEW_CANNOT_CONFIRM" if cap.get("polluted") else "FAIL_P6_WINDOW_NOT_READY",
            "reason": cap.get("error", f"{label} capture failed"),
            "manual_review_required": bool(cap.get("polluted")),
        }
    return cap, p6_rect, pollution_meta, None


def open_project_dialog_detected(cap: Dict[str, Any], min_confidence: float) -> bool:
    if cap.get("screen_state") == "open_project_dialog":
        return True
    entries = cap.get("entries", [])
    norm = normalize_text(collect_text_blob(entries, min_confidence))
    return ("open project" in norm or "select project" in norm) and (
        "project id" in norm or "project name" in norm or "cancel" in norm
    )


def try_close_dialog_once(
    evidence: ExportWizardEvidence,
    p6_rect: P6Rect,
    entries: List[Dict[str, Any]],
    p6_keyword: str,
    min_confidence: float,
    *,
    dialog_name: str,
    confirmed: bool,
) -> str:
    cancel_entry = find_cancel_entry(entries, min_confidence)
    if cancel_entry is not None:
        evidence.steps.append(f"M20 preflight: OCR-confirmed Cancel once on {dialog_name}")
        click_ocr_entry(p6_rect, cancel_entry)
        return "cancel_click"
    if confirmed:
        evidence.steps.append(f"M20 preflight: Esc once on confirmed {dialog_name}")
        keyboard_tools.press_escape()
        return "esc"
    return "none"


CLOSE_PROJECT_CONFIRM_MARKERS = (
    "close this project",
    "close project",
    "want to close",
    "cloze this project",
    "cloze project",
    "close the project",
)


def is_close_project_confirmation(blob: str) -> bool:
    """Detect P6 close-project Yes/No prompt (tolerates OCR garble like cloze/8ure)."""
    norm = normalize_text(blob)
    if any(m in norm for m in CLOSE_PROJECT_CONFIRM_MARKERS):
        return True
    if "project" in norm and ("close" in norm or "cloze" in norm):
        if any(w in norm for w in ("sure", "8ure", "want", "yo")):
            return True
    return False


def find_yes_button_entry(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Optional[Dict[str, Any]]:
    for entry in entries:
        if entry["confidence"] < min_confidence * 0.45:
            continue
        norm = entry.get("normalized", "").strip()
        if norm in ("yes", "ves", "yas", "ye5") or norm.startswith("yes"):
            return entry
    return None


def try_resolve_stale_close_project_popup(
    evidence: ExportWizardEvidence,
    p6_rect: P6Rect,
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Tuple[bool, str]:
    """Complete or dismiss stale close-project Yes/No left by M05 hard-test setup."""
    blob = collect_text_blob(entries, min_confidence)
    if not is_close_project_confirmation(blob):
        return False, ""

    import pyautogui

    evidence.steps.append("M20: Alt+Y on stale close-project confirmation (M05-style)")
    pyautogui.hotkey("alt", "y")
    time.sleep(STABILITY_WAIT)
    return True, "alt+y"


M22_TOOLBAR_POPUP_WORDS = frozenset({"remove", "delete", "overwrite", "save"})


def m22_hard_prep_false_positive_unsafe(
    entries: List[Dict[str, Any]],
    min_confidence: float,
    reason: str,
    screen_state: str = "",
) -> bool:
    """
    P6 Activities toolbar/menu OCR often yields remove/delete/save as button labels
    without a modal Yes/No — not a blocking confirmation popup.
    """
    from m16_discover_p6_export_menu import exact_button_labels  # noqa: WPS433

    state = (screen_state or "").lower()
    if state and state not in (
        "activities_workspace",
        "p6_main",
        "unknown",
        "wbs_workspace",
        "projects_workspace",
    ):
        return False

    reason_l = (reason or "").lower()
    if not any(word in reason_l for word in M22_TOOLBAR_POPUP_WORDS):
        return False

    exact = exact_button_labels(entries, min_confidence)
    if ("yes" in exact and "no" in exact) or ("ok" in exact and "cancel" in exact):
        return False

    if reason_l.startswith("unsafe confirmation phrase:"):
        return state in ("activities_workspace", "p6_main", "unknown", "", "wbs_workspace", "projects_workspace")

    toolbar_hits = exact.intersection(M22_TOOLBAR_POPUP_WORDS)
    if toolbar_hits and len(exact) >= 3:
        return True
    return False


def m22_hard_prep_blocking_popup(
    entries: List[Dict[str, Any]],
    min_confidence: float,
    screen_state: str = "",
) -> Tuple[bool, str]:
    from m16_discover_p6_export_menu import detect_m16_blocking_popup  # noqa: WPS433

    blocking, block_reason = detect_m16_blocking_popup(entries, min_confidence)
    if blocking and m22_hard_prep_false_positive_unsafe(
        entries, min_confidence, block_reason, screen_state
    ):
        return False, ""
    return blocking, block_reason


def m20_hard_dismiss_stale_dialogs(
    p6_keyword: str,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
) -> List[str]:
    """Dismiss stale Open Project or export wizard before M03-M06 hard-test chain."""
    notes: List[str] = []
    prep = prepare_p6_for_test(p6_keyword)
    if not prep.get("success") or not prep.get("rect"):
        notes.append(f"hard prep: prepare_p6 failed ({prep.get('message', 'unknown')})")
        return notes

    p6_rect: P6Rect = prep["rect"]
    window_tools.activate_window_by_title(p6_keyword)
    time.sleep(0.5)

    tmp = Path(tempfile.gettempdir()) / "m20_hard_prep"
    for sub in ("screenshots", "ocr", "classification", "popup", "discovery"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)
    evidence = ExportWizardEvidence(
        run_id="hard_prep",
        folder=tmp,
        module_name="m20_hard_prep",
        screenshots_dir=tmp / "screenshots",
        ocr_dir=tmp / "ocr",
        classification_dir=tmp / "classification",
        popup_dir=tmp / "popup",
        discovery_dir=tmp / "discovery",
        steps=[],
    )

    for attempt in range(4):
        cap = capture_and_ocr_step(evidence, f"hard_prep_{attempt}", p6_rect, config, screen_rule)
        if not cap.get("ok"):
            keyboard_tools.press_escape()
            time.sleep(0.6)
            p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
            notes.append(f"hard prep attempt {attempt}: capture failed — Esc")
            continue

        entries = cap.get("entries", [])
        blob = collect_text_blob(entries, min_confidence)
        norm = normalize_text(blob)
        evidence_words = find_export_evidence_words(blob)

        if is_close_project_confirmation(blob):
            notes.append(f"hard prep attempt {attempt}: completing close-project confirmation")
            resolved, method = try_resolve_stale_close_project_popup(
                evidence, p6_rect, entries, min_confidence
            )
            if not resolved:
                yes_entry = find_yes_button_entry(entries, min_confidence)
                if yes_entry is not None:
                    click_ocr_entry(p6_rect, yes_entry)
                    notes.append("hard prep: OCR Yes on close-project confirmation")
                else:
                    keyboard_tools.press_key("y")
                    notes.append("hard prep: Y key on close-project confirmation")
            else:
                notes.append(f"hard prep: close-project resolved via {method}")
            time.sleep(0.8)
            p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
            continue

        blocking, block_reason = m22_hard_prep_blocking_popup(
            entries, min_confidence, cap.get("screen_state", "")
        )
        if blocking:
            if is_close_project_confirmation(blob):
                notes.append(
                    f"hard prep attempt {attempt}: completing close-project confirmation ({block_reason})"
                )
                try_resolve_stale_close_project_popup(evidence, p6_rect, entries, min_confidence)
            else:
                notes.append(f"hard prep attempt {attempt}: dismissing blocking popup ({block_reason})")
                keyboard_tools.press_escape()
            time.sleep(0.8)
            p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
            continue

        if open_project_dialog_detected(cap, min_confidence):
            notes.append(f"hard prep attempt {attempt}: dismissing Open Project dialog")
            try_close_dialog_once(
                evidence,
                p6_rect,
                entries,
                p6_keyword,
                min_confidence,
                dialog_name="Open Project dialog",
                confirmed=True,
            )
            time.sleep(0.8)
            p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
            continue

        validation_hit, _ = m21_validation_popup_in_entries(entries, min_confidence)
        if m21_projects_validation_popup_detected(norm) or validation_hit:
            notes.append(f"hard prep attempt {attempt}: dismissing projects validation popup")
            m21_dismiss_projects_validation_popup(
                evidence, p6_rect, p6_keyword, config, screen_rule, min_confidence, entries
            )
            time.sleep(0.8)
            p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
            continue

        wizard_open = export_wizard_open_in_capture(entries, min_confidence)[0] or export_dialog_detected(
            evidence_words
        )
        projects_export_screen = "projects to export" in norm and "open projects" in norm
        if wizard_open or projects_export_screen:
            notes.append(f"hard prep attempt {attempt}: dismissing stale export wizard")
            cancel_entry = find_cancel_entry(entries, min_confidence)
            if cancel_entry is not None:
                click_ocr_entry(p6_rect, cancel_entry)
            else:
                keyboard_tools.press_escape()
            time.sleep(0.8)
            p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
            continue

        notes.append("hard prep: P6 dialog-free")
        return notes

    notes.append("hard prep: max dismiss attempts reached")
    return notes


def export_type_screen_visible(entries: List[Dict[str, Any]], min_confidence: float) -> bool:
    blob = collect_text_blob(entries, min_confidence)
    norm = normalize_text(blob)
    if "export type" not in norm:
        return False
    return (
        "data to export" in norm
        or "type of data" in norm
        or "select the type" in norm
    )


def wizard_chrome_visible(entries: List[Dict[str, Any]], min_confidence: float) -> bool:
    blob = collect_text_blob(entries, min_confidence)
    norm = normalize_text(blob)
    buttons = detect_wizard_buttons(blob)
    return bool(buttons.get("cancel_button_detected")) and (
        "next" in norm.split() or buttons.get("finish_button_detected")
    )


def wizard_truly_closed(entries: List[Dict[str, Any]], min_confidence: float) -> bool:
    """Wizard is closed only when chrome/export markers are gone AND Activities grid is visible."""
    if wizard_chrome_visible(entries, min_confidence):
        return False
    if export_wizard_open_in_capture(entries, min_confidence)[0]:
        return False
    blob = collect_text_blob(entries, min_confidence)
    norm = normalize_text(blob)
    if "export type" in norm or "export format" in norm or "data to export" in norm:
        return False
    tokens = set(norm.split())
    if "cancel" in tokens and "next" in tokens:
        return False
    if "cancel" in tokens and "prev" in tokens:
        return False
    in_activities, _ = confirms_activities_workspace(entries, min_confidence)
    grid = "activity name" in norm and ("layout:" in norm or "wbs filter" in norm)
    return bool(in_activities or grid)


def returned_to_activities_workspace(
    cap: Dict[str, Any],
    min_confidence: float,
) -> bool:
    entries = cap.get("entries", [])
    return wizard_truly_closed(entries, min_confidence)


def find_export_type_anchor_ys(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Tuple[Optional[float], Optional[float]]:
    export_type_y: Optional[float] = None
    relationships_y: Optional[float] = None
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        norm = entry.get("normalized", "")
        ys = [p[1] for p in entry["bbox"]]
        yc = sum(ys) / len(ys)
        if yc < 400:
            continue
        if norm == "export type" or "data to export" in norm:
            export_type_y = yc if export_type_y is None else min(export_type_y, yc)
        if "activity relationships" in norm or norm == "relationships" or "activity relationship" in norm:
            relationships_y = yc if relationships_y is None else min(relationships_y, yc)
    return export_type_y, relationships_y


def score_activities_export_type_entry(norm: str) -> float:
    """Score OCR row as Activities export-type list item (tolerates activitie? / ectivities)."""
    if "activity relationships" in norm or ("relationships" in norm and "activit" not in norm):
        return 0.0
    if "resource" in norm or "expense" in norm:
        return 0.0
    if "filter:" in norm or "activity name" in norm or "new activity" in norm or norm == "activity":
        return 0.0
    if norm in ("ectivities",):
        return 28.0
    if norm in ("activities",):
        return 24.0
    if "ectivities" in norm:
        return 22.0
    if "activities" in norm:
        return 18.0
    if norm.startswith("activitie") or norm.startswith("allactivitie"):
        return 20.0
    if norm.startswith("activit") and "relationship" not in norm:
        return 16.0
    return 0.0


def find_activities_export_type_entry(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Tuple[Optional[Dict[str, Any]], str]:
    export_type_y, relationships_y = find_export_type_anchor_ys(entries, min_confidence)
    neighbor_norms = {
        e.get("normalized", "")
        for e in entries
        if e["confidence"] >= min_confidence
        and sum(p[1] for p in e.get("bbox", [[0, 0]])) / max(len(e.get("bbox", [1])), 1) >= 400
    }
    has_neighbors = any(
        k in " ".join(neighbor_norms)
        for k in (
            "activity relationships",
            "activity relationship",
            "relationships",
            "resources",
            "expenses",
            "resource assignments",
        )
    )
    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    best_text = ""
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        norm = entry.get("normalized", "")
        raw = entry.get("text", "")
        score = score_activities_export_type_entry(norm)
        if score <= 0:
            continue
        ys = [p[1] for p in entry.get("bbox", [[0, 0]])]
        y_center = sum(ys) / len(ys)
        if y_center < 400:
            score *= 0.05
        elif export_type_y is not None and y_center <= export_type_y + 5:
            score *= 0.1
        elif relationships_y is not None and y_center >= relationships_y - 5:
            score *= 0.05
        if has_neighbors and export_type_y and relationships_y:
            if not (export_type_y < y_center < relationships_y):
                score *= 0.1
        if score > best_score:
            best = entry
            best_score = score
            best_text = raw or norm
    return best, best_text


def find_wizard_next_button(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, float]]]:
    bounds = estimate_wizard_bounds(entries, min_confidence)
    next_entry = find_next_entry(entries, min_confidence)
    if next_entry is None:
        return None, bounds
    if not next_in_wizard_bounds(next_entry, bounds):
        return None, bounds
    return next_entry, bounds


def activities_export_type_selected(blob: str, pre_blob: str) -> bool:
    norm = normalize_text(blob)
    if "select template" in norm or "template" in norm:
        return True
    dialog_pre = extract_export_type_dialog_blob(pre_blob)
    dialog_post = extract_export_type_dialog_blob(blob) if "export type" in norm else norm
    if "ectivities" in dialog_pre and "ectivities" not in dialog_post:
        return True
    return False


def template_screen_detected(blob: str) -> bool:
    norm = normalize_text(blob)
    markers = (
        "select template",
        "modify template",
        "template name",
        "activity template",
        "spreadsheet template",
    )
    return any(m in norm for m in markers)


def find_template_evidence_words(blob: str) -> List[str]:
    norm = normalize_text(blob)
    words = (
        "select template",
        "modify template",
        "template",
        "activity",
        "activities",
        "spreadsheet",
        "columns",
        "add",
        "delete",
        "next",
        "back",
        "cancel",
        "finish",
    )
    found: List[str] = []
    for w in words:
        if w in norm and w not in found:
            found.append(w)
    return sorted(set(found))


def post_template_screen_detected(blob: str) -> bool:
    norm = normalize_text(blob)
    markers = (
        "file name",
        "output file",
        "browse",
        "export file",
        "select file",
        "save as",
    )
    return any(m in norm for m in markers)


def find_post_template_evidence_words(blob: str) -> List[str]:
    norm = normalize_text(blob)
    words = (
        "file name",
        "output file",
        "browse",
        "export file",
        "select file",
        "spreadsheet",
        "next",
        "back",
        "cancel",
        "finish",
    )
    found: List[str] = []
    for w in words:
        if w in norm and w not in found:
            found.append(w)
    return sorted(set(found))


def default_template_detected(blob: str) -> Tuple[bool, str]:
    norm = normalize_text(blob)
    if "select template" not in norm and "template" not in norm:
        return False, ""
    for marker in ("default", "selected", "highlight", "current template"):
        if marker in norm:
            idx = norm.find(marker)
            return True, norm[max(0, idx - 30) : idx + 60]
    if re.search(r"template\s+\w+", norm):
        return True, norm[:120]
    return False, ""


def prepare_project_activities(
    evidence: ExportWizardEvidence,
    project_name: str,
    p6_keyword: str,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
) -> Tuple[Optional[P6Rect], str, str, Optional[Dict[str, Any]]]:
    """Returns (p6_rect, window_title_before, screen_state_before, error_result)."""
    evidence.steps.append("prepare_p6_for_test")
    prep = prepare_p6_for_test(p6_keyword)
    if not prep.get("success") or not prep.get("rect"):
        return None, "", "", {"status": "FAIL_P6_WINDOW_NOT_READY", "reason": prep.get("message", "P6 not ready")}

    p6_rect: P6Rect = prep["rect"]
    window_title_before = window_tools.get_window_state(p6_keyword).get("title") or ""

    evidence.steps.append("capture before_action")
    before = capture_and_ocr_step(evidence, "01_before", p6_rect, config, screen_rule)
    if not before.get("ok"):
        polluted = before.get("polluted")
        return None, window_title_before, "unknown", {
            "status": "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_P6_WINDOW_NOT_READY",
            "reason": before.get("error", "before capture failed"),
            "manual_review_required": bool(polluted),
        }

    screen_state_before = before["screen_state"]
    if before.get("unsafe"):
        return None, window_title_before, screen_state_before, {
            "status": "MANUAL_REVIEW_UNSAFE_POPUP",
            "reason": before.get("unsafe_reason", "unsafe popup"),
            "manual_review_required": True,
        }

    open_ok, open_reason, _ = confirm_project_open(
        before["entries"], project_name, window_title_before, min_confidence
    )
    if not open_ok:
        return None, window_title_before, screen_state_before, {
            "status": "FAIL_PROJECT_NOT_OPEN",
            "reason": open_reason,
        }

    in_activities, _ = confirms_activities_workspace(before["entries"], min_confidence)
    if not in_activities:
        evidence.steps.append("not in Activities — navigate via M06-style Alt+P, A")
        navigate_to_activities(evidence)
        fresh = refresh_p6_rect(p6_keyword, p6_rect)
        nav_cap = capture_and_ocr_step(evidence, "02_after_nav", fresh, config, screen_rule)
        if not nav_cap.get("ok"):
            polluted = nav_cap.get("polluted")
            return None, window_title_before, screen_state_before, {
                "status": "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_ACTIVITIES_NOT_FOUND",
                "reason": nav_cap.get("error", "Activities not confirmed"),
                "manual_review_required": bool(polluted),
            }
        in_activities, _ = confirms_activities_workspace(nav_cap["entries"], min_confidence)
        p6_rect = fresh
        screen_state_before = nav_cap["screen_state"]
        if not in_activities:
            return None, window_title_before, screen_state_before, {
                "status": "FAIL_ACTIVITIES_NOT_FOUND",
                "reason": "Activities workspace not confirmed after navigation",
            }
        if nav_cap.get("unsafe"):
            return None, window_title_before, screen_state_before, {
                "status": "MANUAL_REVIEW_UNSAFE_POPUP",
                "reason": nav_cap.get("unsafe_reason", "unsafe after nav"),
                "manual_review_required": True,
            }

    return p6_rect, window_title_before, screen_state_before, None


def export_wizard_open_in_capture(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Tuple[bool, List[str], str]:
    """Detect export wizard/dialog in P6 crop — strict markers only (no Activities-table false positives)."""
    blob = collect_text_blob(entries, min_confidence)
    words = find_export_evidence_words(blob)
    norm = normalize_text(blob)
    strict_markers = (
        "export format",
        "export type",
        "select the type",
        "data to export",
        "select template",
        "modify template",
        "file name",
        "output file",
        "browse",
    )
    wizard_open = any(m in norm for m in strict_markers)
    if not wizard_open and "spreadsheet" in norm and "export" in norm:
        wizard_chrome = sum(1 for token in ("cancel", "next", "back") if token in norm.split())
        if wizard_chrome >= 2:
            wizard_open = True
    return wizard_open, words, blob


def m20_preflight_reset_before_export(
    evidence: ExportWizardEvidence,
    project_name: str,
    p6_keyword: str,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
) -> Tuple[Optional[P6Rect], str, str, Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    M20 start-state safety before File > Export.

    Prepares P6, clears stale export wizards, retries on OCR pollution, and confirms
    Activities workspace. Does not open File > Export.
    """
    preflight: Dict[str, Any] = {
        "p6_pollution_detected": False,
        "p6_pollution_words": [],
        "p6_pollution_retry_used": False,
        "pollution_recovered": False,
        "old_open_project_detected": False,
        "old_open_project_closed": False,
        "open_project_close_method": "",
        "old_export_wizard_detected": False,
        "old_export_wizard_closed": False,
        "stale_wizard_close_method": "",
        "p6_foreground_confirmed": False,
        "p6_maximized_confirmed": False,
    }

    evidence.steps.append("M20 preflight: prepare_p6_for_test")
    prep = prepare_p6_for_test(p6_keyword)
    if not prep.get("success") or not prep.get("rect"):
        return None, "", "", preflight, {
            "status": "FAIL_P6_WINDOW_NOT_READY",
            "reason": prep.get("message", "P6 not ready"),
        }

    p6_rect: P6Rect = prep["rect"]
    window_title = window_tools.get_window_state(p6_keyword).get("title") or ""

    evidence.steps.append("M20 preflight: confirm P6 foreground and maximised")
    activate = window_tools.activate_window_by_title(p6_keyword)
    maximize = window_tools.maximize_window_by_title(p6_keyword)
    time.sleep(0.5)
    state = window_tools.get_window_state(p6_keyword)
    if not state.get("found"):
        return None, window_title, "unknown", preflight, {
            "status": "FAIL_P6_WINDOW_NOT_READY",
            "reason": "P6 window not found after prepare",
        }
    preflight["p6_foreground_confirmed"] = bool(activate.get("success"))
    preflight["p6_maximized_confirmed"] = bool(state.get("is_maximized")) or bool(maximize.get("success"))

    def capture_preflight(label: str, rect: P6Rect) -> Dict[str, Any]:
        return capture_and_ocr_step(evidence, label, rect, config, screen_rule)

    cap = capture_preflight("preflight_01_initial", p6_rect)
    pollution = check_ocr_pollution(
        cap.get("entries", []), config.get("pollution_keywords"), min_confidence
    ) if cap.get("ok") else {"polluted": cap.get("polluted"), "pollution_words": []}

    if not cap.get("ok") and not pollution.get("polluted"):
        return None, window_title, "unknown", preflight, {
            "status": "FAIL_P6_WINDOW_NOT_READY",
            "reason": cap.get("error", "preflight initial capture failed"),
        }

    if pollution.get("polluted") or (not cap.get("ok") and cap.get("polluted")):
        preflight["p6_pollution_detected"] = True
        preflight["p6_pollution_words"] = list(pollution.get("pollution_words") or [])
        if not preflight["p6_pollution_words"] and cap.get("error"):
            preflight["p6_pollution_words"] = [
                w.strip() for w in cap.get("error", "").replace("OCR pollution:", "").strip("[]").split(",")
            ]
        preflight["p6_pollution_retry_used"] = True
        evidence.steps.append(
            "M20 preflight: OCR pollution in P6 crop — retry prepare_p6 and bring P6 to foreground"
        )
        prepare_p6_for_test(p6_keyword)
        window_tools.activate_window_by_title(p6_keyword)
        time.sleep(1.0)
        p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
        cap = capture_preflight("preflight_02_after_pollution_retry", p6_rect)
        if not cap.get("ok"):
            polluted = cap.get("polluted")
            return None, window_title, "unknown", preflight, {
                "status": "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_P6_WINDOW_NOT_READY",
                "reason": cap.get("error", "preflight retry capture failed"),
                "manual_review_required": bool(polluted),
            }
        pollution_retry = check_ocr_pollution(
            cap["entries"], config.get("pollution_keywords"), min_confidence
        )
        if pollution_retry["polluted"]:
            preflight["p6_pollution_words"] = list(pollution_retry["pollution_words"])
            return None, window_title, cap.get("screen_state", "unknown"), preflight, {
                "status": "MANUAL_REVIEW_CANNOT_CONFIRM",
                "reason": f"OCR pollution persists in P6 crop: {pollution_retry['pollution_words']}",
                "manual_review_required": True,
            }
        preflight["pollution_recovered"] = True

    if not cap.get("ok"):
        return None, window_title, "unknown", preflight, {
            "status": "MANUAL_REVIEW_CANNOT_CONFIRM",
            "reason": cap.get("error", "preflight capture failed after pollution retry"),
            "manual_review_required": True,
        }

    pollution = check_ocr_pollution(
        cap["entries"], config.get("pollution_keywords"), min_confidence
    )
    if pollution["polluted"]:
        preflight["p6_pollution_detected"] = True
        preflight["p6_pollution_words"] = list(pollution["pollution_words"])
        return None, window_title, cap.get("screen_state", "unknown"), preflight, {
            "status": "MANUAL_REVIEW_CANNOT_CONFIRM",
            "reason": f"OCR pollution in P6 crop: {pollution['pollution_words']}",
            "manual_review_required": True,
        }

    screen_state = cap["screen_state"]
    entries = cap["entries"]

    if cap.get("unsafe"):
        if m22_hard_prep_false_positive_unsafe(
            entries, min_confidence, cap.get("unsafe_reason", ""), screen_state
        ):
            evidence.steps.append(
                f"M20 preflight: ignored toolbar false-positive unsafe ({cap.get('unsafe_reason', '')})"
            )
            cap["unsafe"] = False
        else:
            resolved, resolve_method = try_resolve_stale_close_project_popup(
                evidence, p6_rect, entries, min_confidence
            )
            if resolved:
                preflight["stale_close_confirm_resolved"] = resolve_method
                time.sleep(1.0)
                p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
                cap = capture_preflight("preflight_01b_after_close_confirm", p6_rect)
                if not cap.get("ok"):
                    polluted = cap.get("polluted")
                    return None, window_title, screen_state, preflight, {
                        "status": "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_P6_WINDOW_NOT_READY",
                        "reason": cap.get("error", "preflight post close-confirm capture failed"),
                        "manual_review_required": bool(polluted),
                    }
                screen_state = cap["screen_state"]
                entries = cap["entries"]
            if cap.get("unsafe") and not m22_hard_prep_false_positive_unsafe(
                entries, min_confidence, cap.get("unsafe_reason", ""), screen_state
            ):
                return None, window_title, screen_state, preflight, {
                    "status": "MANUAL_REVIEW_UNSAFE_POPUP",
                    "reason": cap.get("unsafe_reason", "unsafe popup during preflight"),
                    "manual_review_required": True,
                }

    if open_project_dialog_detected(cap, min_confidence):
        preflight["old_open_project_detected"] = True
        close_method = try_close_dialog_once(
            evidence, p6_rect, entries, p6_keyword, min_confidence,
            dialog_name="Open Project dialog", confirmed=True,
        )
        preflight["open_project_close_method"] = close_method
        time.sleep(1.0)
        p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
        cap = capture_preflight("preflight_02_after_open_project_close", p6_rect)
        if not cap.get("ok"):
            polluted = cap.get("polluted")
            return None, window_title, screen_state, preflight, {
                "status": "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_P6_WINDOW_NOT_READY",
                "reason": cap.get("error", "preflight post Open Project close capture failed"),
                "manual_review_required": bool(polluted),
            }
        if open_project_dialog_detected(cap, min_confidence):
            return None, window_title, screen_state, preflight, {
                "status": "MANUAL_REVIEW_CANNOT_CONFIRM",
                "reason": "Open Project dialog still open after single Cancel/Esc",
                "manual_review_required": True,
            }
        preflight["old_open_project_closed"] = True
        screen_state = cap["screen_state"]
        entries = cap["entries"]

    wizard_open, evidence_words, _ = export_wizard_open_in_capture(entries, min_confidence)
    if wizard_open:
        preflight["old_export_wizard_detected"] = True
        evidence.steps.append("M20 preflight: stale export wizard detected before File > Export")
        cancel_entry = find_cancel_entry(entries, min_confidence)
        if cancel_entry is not None:
            evidence.steps.append("M20 preflight: OCR-confirmed Cancel once on stale export wizard")
            click_ocr_entry(p6_rect, cancel_entry)
            preflight["stale_wizard_close_method"] = "cancel_click"
        else:
            evidence.steps.append("M20 preflight: Esc once on confirmed stale export wizard")
            keyboard_tools.press_escape()
            preflight["stale_wizard_close_method"] = "esc"
        time.sleep(1.0)
        p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
        cap = capture_preflight("preflight_03_after_stale_wizard_close", p6_rect)
        if not cap.get("ok"):
            polluted = cap.get("polluted")
            return None, window_title, screen_state, preflight, {
                "status": "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_P6_WINDOW_NOT_READY",
                "reason": cap.get("error", "preflight post-close capture failed"),
                "manual_review_required": bool(polluted),
            }
        wizard_still, _, _ = export_wizard_open_in_capture(cap["entries"], min_confidence)
        if wizard_still:
            return None, window_title, screen_state, preflight, {
                "status": "MANUAL_REVIEW_CANNOT_CONFIRM",
                "reason": "Stale export wizard still open after single Cancel/Esc; cannot open File > Export",
                "manual_review_required": True,
            }
        preflight["old_export_wizard_closed"] = True
        screen_state = cap["screen_state"]
        entries = cap["entries"]

    open_ok, open_reason, _ = confirm_project_open(
        entries, project_name, window_title, min_confidence
    )
    if not open_ok:
        return None, window_title, screen_state, preflight, {
            "status": "FAIL_PROJECT_NOT_OPEN",
            "reason": open_reason,
        }

    in_activities, _ = confirms_activities_workspace(entries, min_confidence)
    if not in_activities:
        evidence.steps.append("M20 preflight: not in Activities — navigate via M06-style Alt+P, A")
        navigate_to_activities(evidence)
        p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
        window_tools.activate_window_by_title(p6_keyword)
        window_tools.maximize_window_by_title(p6_keyword)
        time.sleep(0.8)
        cap = capture_preflight("preflight_04_activities_confirm", p6_rect)
        if not cap.get("ok") and cap.get("polluted"):
            evidence.steps.append(
                "M20 preflight: pollution on activities confirm — refocus P6 and recapture once"
            )
            prepare_p6_for_test(p6_keyword)
            window_tools.activate_window_by_title(p6_keyword)
            window_tools.maximize_window_by_title(p6_keyword)
            time.sleep(1.0)
            p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
            cap = capture_preflight("preflight_04_activities_confirm_retry", p6_rect)
        if not cap.get("ok"):
            polluted = cap.get("polluted")
            return None, window_title, screen_state, preflight, {
                "status": "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_ACTIVITIES_NOT_FOUND",
                "reason": cap.get("error", "Activities not confirmed after navigation"),
                "manual_review_required": bool(polluted),
            }
        in_activities, _ = confirms_activities_workspace(cap["entries"], min_confidence)
        screen_state = cap["screen_state"]
        entries = cap["entries"]
        if not in_activities:
            return None, window_title, screen_state, preflight, {
                "status": "FAIL_ACTIVITIES_NOT_FOUND",
                "reason": "Activities workspace not confirmed before File > Export",
            }
        if cap.get("unsafe") and not m22_hard_prep_false_positive_unsafe(
            entries, min_confidence, cap.get("unsafe_reason", ""), screen_state
        ):
            return None, window_title, screen_state, preflight, {
                "status": "MANUAL_REVIEW_UNSAFE_POPUP",
                "reason": cap.get("unsafe_reason", "unsafe popup after Activities navigation"),
                "manual_review_required": True,
            }
        wizard_late, _, _ = export_wizard_open_in_capture(entries, min_confidence)
        if wizard_late:
            return None, window_title, screen_state, preflight, {
                "status": "MANUAL_REVIEW_CANNOT_CONFIRM",
                "reason": "Export wizard detected after Activities confirm; stale dialog not cleared",
                "manual_review_required": True,
            }

    save_discovery(
        evidence,
        "preflight_reset.json",
        {
            **preflight,
            "screen_state_before_export": screen_state,
            "window_title": window_title,
            "activities_workspace_confirmed": True,
        },
    )

    return p6_rect, window_title, screen_state, preflight, None


def _wizard_open_from_capture(
    cap: Dict[str, Any],
    min_confidence: float,
) -> Tuple[bool, str, List[str], List[Dict[str, Any]]]:
    entries = cap.get("entries", [])
    wizard_blob = collect_text_blob(entries, min_confidence)
    evidence_words = find_export_evidence_words(wizard_blob)
    wizard_detected = export_dialog_detected(evidence_words) or "export format" in normalize_text(wizard_blob)
    return wizard_detected, wizard_blob, evidence_words, entries


def m20_open_export_wizard_with_retry(
    evidence: ExportWizardEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    *,
    first_label: str = "03_after_wizard",
    retry_label: str = "03_after_wizard_retry",
) -> Tuple[P6Rect, Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]]:
    """Open File > Export with one safe retry when wizard not detected."""
    pollution_acc: Dict[str, Any] = {
        "pollution_detected": False,
        "pollution_recovered": False,
        "pollution_words": [],
    }
    open_export_menu(evidence)
    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
    after_wizard, p6_rect, pol, err = m20_step_capture(
        evidence, first_label, p6_rect, p6_keyword, config, screen_rule, min_confidence
    )
    pollution_acc["pollution_detected"] = pollution_acc["pollution_detected"] or pol.get("pollution_detected", False)
    pollution_acc["pollution_recovered"] = pollution_acc["pollution_recovered"] or pol.get("pollution_recovered", False)
    if pol.get("pollution_words"):
        pollution_acc["pollution_words"] = pol["pollution_words"]
    if err:
        return p6_rect, after_wizard, pollution_acc, err

    wizard_detected, wizard_blob, evidence_words, entries = _wizard_open_from_capture(
        after_wizard, min_confidence
    )
    attempt_meta = {
        "attempt": 1,
        "label": first_label,
        "wizard_detected": wizard_detected,
        "evidence_words": evidence_words,
        "ocr_excerpt": wizard_blob[:500],
    }

    if not wizard_detected:
        safe_esc = True
        if after_wizard.get("ok"):
            blob = collect_text_blob(entries, min_confidence)
            blocking, block_reason = detect_m16_blocking_popup(entries, min_confidence)
            if blocking and not is_close_project_confirmation(blob):
                safe_esc = False
                attempt_meta["retry_blocked"] = block_reason
            elif is_close_project_confirmation(blob):
                safe_esc = False
                attempt_meta["retry_blocked"] = "close_project_confirmation_visible"
        if safe_esc:
            evidence.steps.append("M20: export wizard not detected — Esc once, refocus P6, retry Alt+F,E")
            keyboard_tools.press_escape()
            time.sleep(0.6)
        window_tools.activate_window_by_title(p6_keyword)
        time.sleep(0.5)
        p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
        nav_cap = capture_and_ocr_step(evidence, "03_retry_activities_check", p6_rect, config, screen_rule)
        if nav_cap.get("ok"):
            in_act, _ = confirms_activities_workspace(nav_cap["entries"], min_confidence)
            if not in_act:
                navigate_to_activities(evidence)
                p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
        open_export_menu(evidence)
        p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
        after_retry, p6_rect, pol2, err2 = m20_step_capture(
            evidence, retry_label, p6_rect, p6_keyword, config, screen_rule, min_confidence
        )
        pollution_acc["pollution_detected"] = pollution_acc["pollution_detected"] or pol2.get("pollution_detected", False)
        pollution_acc["pollution_recovered"] = pollution_acc["pollution_recovered"] or pol2.get("pollution_recovered", False)
        if pol2.get("pollution_words"):
            pollution_acc["pollution_words"] = pol2["pollution_words"]
        if err2:
            return p6_rect, after_wizard, pollution_acc, err2
        wizard_detected, wizard_blob, evidence_words, entries = _wizard_open_from_capture(
            after_retry, min_confidence
        )
        after_wizard = after_retry
        attempt_meta = {
            "attempt": 2,
            "label": retry_label,
            "wizard_detected": wizard_detected,
            "evidence_words": evidence_words,
            "ocr_excerpt": wizard_blob[:500],
        }

    after_wizard["wizard_detected"] = wizard_detected
    after_wizard["wizard_blob"] = wizard_blob
    after_wizard["evidence_words"] = evidence_words
    after_wizard["export_open_attempt"] = attempt_meta
    return p6_rect, after_wizard, pollution_acc, None


def probe_export_wizard_open(
    evidence: ExportWizardEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    *,
    label: str = "probe_after_wizard",
) -> Tuple[P6Rect, Dict[str, Any]]:
    """Open export wizard once for hard-test precheck; returns probe payload (caller closes wizard)."""
    p6_rect, cap, pol, err = m20_open_export_wizard_with_retry(
        evidence,
        p6_keyword,
        p6_rect,
        config,
        screen_rule,
        min_confidence,
        first_label=label,
        retry_label=f"{label}_retry",
    )
    wizard_detected = bool(cap.get("wizard_detected"))
    payload: Dict[str, Any] = {
        "wizard_opened": wizard_detected,
        "evidence_words": cap.get("evidence_words", []),
        "ocr_excerpt": (cap.get("wizard_blob") or "")[:500],
        "export_open_attempt": cap.get("export_open_attempt", {}),
        "pollution": pol,
        "capture_error": err,
        "screen_state": cap.get("screen_state", "unknown"),
    }
    if err:
        payload["wizard_opened"] = False
        payload["error"] = err
    return p6_rect, payload


def m20_controlled_wizard_to_post_activities(
    evidence: ExportWizardEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    project_name: str = "",
    *,
    force_post_activities_screen_not_found_after_second_next: bool = False,
) -> Tuple[P6Rect, Dict[str, Any], Optional[Dict[str, Any]]]:
    """M20 controlled export wizard path with step pollution gates and discovery evidence."""
    ctx: Dict[str, Any] = {
        "pollution_detected": False,
        "pollution_recovered": False,
        "pollution_words": [],
        "first_next_clicked_by_ocr_bbox": False,
        "second_next_clicked_by_ocr_bbox": False,
        "wizard_still_open_after_activities_click": False,
        "wizard_closed_unexpectedly": False,
    }

    p6_rect, after_wizard, pol, err = m20_open_export_wizard_with_retry(
        evidence, p6_keyword, p6_rect, config, screen_rule, min_confidence
    )
    ctx["pollution_detected"] = ctx["pollution_detected"] or pol.get("pollution_detected", False)
    ctx["pollution_recovered"] = ctx["pollution_recovered"] or pol.get("pollution_recovered", False)
    if pol.get("pollution_words"):
        ctx["pollution_words"] = pol["pollution_words"]
    if err:
        return p6_rect, ctx, err

    wizard_detected = bool(after_wizard.get("wizard_detected"))
    wizard_blob = after_wizard.get("wizard_blob", "")
    evidence_words = after_wizard.get("evidence_words", [])
    ctx["export_open_attempt"] = after_wizard.get("export_open_attempt", {})
    save_discovery(evidence, "export_open_attempt.json", ctx["export_open_attempt"])
    spreadsheet_detected, spreadsheet_text = detect_spreadsheet_in_blob(wizard_blob)
    ss_entry, ss_click = find_spreadsheet_entry(after_wizard["entries"], min_confidence)
    if ss_entry is not None and not spreadsheet_detected:
        spreadsheet_detected = True
        spreadsheet_text = ss_click

    ctx.update(
        {
            "wizard_detected": wizard_detected,
            "spreadsheet_detected": spreadsheet_detected,
            "spreadsheet_text": spreadsheet_text,
            "wizard_blob": wizard_blob,
            "evidence_words": evidence_words,
            "entries": after_wizard["entries"],
        }
    )
    if not wizard_detected:
        return p6_rect, ctx, {"status": "FAIL_EXPORT_WIZARD_NOT_FOUND", "reason": "Export wizard not opened"}
    if not spreadsheet_detected or ss_entry is None:
        return p6_rect, ctx, {"status": "FAIL_SPREADSHEET_OPTION_NOT_FOUND", "reason": "Spreadsheet not found"}

    wizard_bounds = detect_export_wizard_bounds(
        after_wizard["entries"], min_confidence, p6_rect.width, p6_rect.height
    )
    ctx["wizard_bounds"] = wizard_bounds
    save_discovery(evidence, "wizard_bounds.json", wizard_bounds)

    save_discovery(
        evidence,
        "spreadsheet_selection_evidence.json",
        m20_build_step_evidence(
            entry=ss_entry,
            p6_rect=p6_rect,
            cap=after_wizard,
            pollution_meta=pol,
            p6_keyword=p6_keyword,
            extra={"spreadsheet_text": spreadsheet_text, "action": "before_spreadsheet_click"},
        ),
    )

    evidence.steps.append(f"select Spreadsheet option: OCR-click bbox '{ss_click[:60]}'")
    click_ocr_entry(p6_rect, ss_entry)
    time.sleep(0.8)
    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

    after_select, p6_rect, pol2, err2 = m20_step_capture(
        evidence, "04_after_spreadsheet_select", p6_rect, p6_keyword, config, screen_rule, min_confidence,
        wizard_bounds=wizard_bounds,
    )
    ctx["pollution_detected"] = ctx["pollution_detected"] or pol2.get("pollution_detected", False)
    ctx["pollution_recovered"] = ctx["pollution_recovered"] or pol2.get("pollution_recovered", False)
    if err2:
        return p6_rect, ctx, err2

    select_blob = collect_text_blob(after_select["entries"], min_confidence)
    ctx["spreadsheet_selected"] = confirm_spreadsheet_selected(select_blob, wizard_blob, click_attempted=True)

    next_entry, bounds = find_wizard_next_button(after_select.get("entries", after_wizard["entries"]), min_confidence)
    if next_entry is None:
        next_entry = find_next_entry(after_select.get("entries", after_wizard["entries"]), min_confidence)
        if next_entry and not next_in_wizard_bounds(next_entry, bounds or estimate_wizard_bounds(after_select["entries"], min_confidence)):
            return p6_rect, ctx, {
                "status": "MANUAL_REVIEW_CANNOT_CONFIRM",
                "reason": "First Next button bbox not inside wizard bounds",
            }
    if next_entry is None:
        return p6_rect, ctx, {"status": "MANUAL_REVIEW_CANNOT_CONFIRM", "reason": "First Next button not detected"}

    evidence.steps.append("press Next once: OCR-confirmed Next click (to Export Type)")
    click_ocr_entry(p6_rect, next_entry)
    ctx["first_next_clicked_by_ocr_bbox"] = True
    time.sleep(STABILITY_WAIT)
    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

    after_type, p6_rect, pol3, err3 = m20_step_capture(
        evidence, "05_after_export_type", p6_rect, p6_keyword, config, screen_rule, min_confidence,
        wizard_bounds=wizard_bounds,
    )
    ctx["pollution_detected"] = ctx["pollution_detected"] or pol3.get("pollution_detected", False)
    ctx["pollution_recovered"] = ctx["pollution_recovered"] or pol3.get("pollution_recovered", False)
    if err3:
        return p6_rect, ctx, err3

    type_blob = collect_text_blob(after_type["entries"], min_confidence)
    type_words = find_export_type_evidence_words(type_blob)
    type_ok = export_type_screen_detected(type_words, type_blob) and export_type_screen_visible(
        after_type["entries"], min_confidence
    )
    ctx.update(
        {
            "export_type_blob": type_blob,
            "export_type_words": type_words,
            "export_type_screen_ok": type_ok,
            "export_type_entries": after_type["entries"],
        }
    )

    save_discovery(
        evidence,
        "export_type_screen_evidence.json",
        m20_build_step_evidence(
            entry=None,
            p6_rect=p6_rect,
            cap=after_type,
            pollution_meta=pol3,
            p6_keyword=p6_keyword,
            extra={
                "action": "export_type_screen_detected",
                "export_type_screen_ok": type_ok,
                "export_type_words": type_words,
                "first_next_bbox": entry_bbox_dict(next_entry),
            },
        ),
    )

    if not type_ok:
        return p6_rect, ctx, {"status": "FAIL_EXPORT_TYPE_SCREEN_NOT_FOUND", "reason": "Export Type screen not confirmed"}

    act_entry, act_text = find_activities_export_type_entry(after_type["entries"], min_confidence)
    if act_entry is None:
        return p6_rect, ctx, {"status": "FAIL_ACTIVITIES_EXPORT_TYPE_NOT_FOUND", "reason": "Activities option not found in export type list"}

    save_discovery(
        evidence,
        "activities_selection_evidence.json",
        m20_build_step_evidence(
            entry=act_entry,
            p6_rect=p6_rect,
            cap=after_type,
            pollution_meta=pol3,
            p6_keyword=p6_keyword,
            extra={"activities_option_text": act_text, "action": "before_activities_click"},
        ),
    )

    evidence.steps.append(f"select Activities export type: OCR-click bbox '{act_text[:60]}'")
    click_ocr_entry(p6_rect, act_entry)
    time.sleep(0.8)
    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

    after_act, p6_rect, pol4, err4 = m20_step_capture(
        evidence, "06_after_activities_select", p6_rect, p6_keyword, config, screen_rule, min_confidence,
        wizard_bounds=wizard_bounds,
    )
    if err4:
        return p6_rect, ctx, err4

    if returned_to_activities_workspace(after_act, min_confidence):
        ctx["wizard_closed_unexpectedly"] = True
        save_discovery(
            evidence,
            "wizard_state_after_activities_click.json",
            m20_build_step_evidence(
                entry=act_entry,
                p6_rect=p6_rect,
                cap=after_act,
                pollution_meta=pol4,
                p6_keyword=p6_keyword,
                extra={"wizard_still_open": False, "returned_to_activities": True},
            ),
        )
        return p6_rect, ctx, {
            "status": "FAIL_WIZARD_CLOSED_UNEXPECTEDLY",
            "reason": "P6 returned to Activities workspace immediately after Activities click",
        }

    if not export_type_screen_visible(after_act["entries"], min_confidence) and not wizard_chrome_visible(
        after_act["entries"], min_confidence
    ):
        ctx["wizard_closed_unexpectedly"] = True
        return p6_rect, ctx, {
            "status": "FAIL_WIZARD_CLOSED_UNEXPECTEDLY",
            "reason": "Export wizard not visible after Activities click",
        }

    ctx["wizard_still_open_after_activities_click"] = True
    ctx["activities_selected"] = activities_export_type_selected(
        collect_text_blob(after_act["entries"], min_confidence), type_blob
    ) or bool(act_text)
    ctx["activities_click_text"] = act_text
    ctx["activities_option_bbox"] = entry_bbox_dict(act_entry)

    save_discovery(
        evidence,
        "wizard_state_after_activities_click.json",
        m20_build_step_evidence(
            entry=act_entry,
            p6_rect=p6_rect,
            cap=after_act,
            pollution_meta=pol4,
            p6_keyword=p6_keyword,
            extra={
                "wizard_still_open": True,
                "export_type_screen_visible": export_type_screen_visible(after_act["entries"], min_confidence),
                "wizard_chrome_visible": wizard_chrome_visible(after_act["entries"], min_confidence),
                "activities_selected": ctx["activities_selected"],
            },
        ),
    )

    next2, bounds2 = find_wizard_next_button(after_act["entries"], min_confidence)
    if next2 is None:
        return p6_rect, ctx, {"status": "MANUAL_REVIEW_CANNOT_CONFIRM", "reason": "Second Next button not detected in wizard"}
    if not next_in_wizard_bounds(next2, bounds2 or estimate_wizard_bounds(after_act["entries"], min_confidence)):
        return p6_rect, ctx, {
            "status": "MANUAL_REVIEW_CANNOT_CONFIRM",
            "reason": "Second Next button bbox not inside wizard bounds",
        }

    save_discovery(
        evidence,
        "second_next_click_evidence.json",
        m20_build_step_evidence(
            entry=next2,
            p6_rect=p6_rect,
            cap=after_act,
            pollution_meta=pol4,
            p6_keyword=p6_keyword,
            extra={"action": "before_second_next", "next_pressed_count_before": count_next_presses(evidence.steps)},
        ),
    )

    evidence.steps.append("press Next once: OCR-confirmed Next click (after Activities)")
    click_ocr_entry(p6_rect, next2)
    ctx["second_next_clicked_by_ocr_bbox"] = True
    time.sleep(1.5)
    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

    after_post, p6_rect, pol5, err5 = m20_step_capture(
        evidence, "07_after_activities_next", p6_rect, p6_keyword, config, screen_rule, min_confidence,
        wizard_bounds=wizard_bounds,
    )
    if err5:
        return p6_rect, ctx, err5

    if returned_to_activities_workspace(after_post, min_confidence):
        ctx["wizard_closed_unexpectedly"] = True
        post_blob = collect_text_blob(after_post["entries"], min_confidence)
        ctx["post_activities_blob"] = post_blob
        ctx["post_activities_entries"] = after_post["entries"]
        post_class = classify_post_activities_next_screen(
            after_post["entries"], min_confidence, project_name=project_name
        )
        ctx.update(
            {
                "post_activities_screen_type": post_class["post_activities_screen_type"],
                "post_activities_evidence_words": post_class["evidence_words"],
                "post_screen_ok": post_class["post_screen_ok"],
                "post_activities_classification_status": post_class["status"],
                "post_activities_classification_reason": post_class["reason"],
                "wizard_still_open": post_class["wizard_still_open"],
            }
        )
        save_discovery(
            evidence,
            "post_activities_next_screen_evidence.json",
            {
                "post_activities_screen_type": post_class["post_activities_screen_type"],
                "evidence_words": post_class["evidence_words"],
                "raw_ocr_text": post_class["raw_ocr_text"],
                "next_pressed_count_total": count_next_presses(evidence.steps),
                "finish_pressed": finish_pressed_in_steps(evidence.steps),
                "export_file_created": False,
                "wizard_still_open": post_class["wizard_still_open"],
                "returned_to_activities": True,
            },
        )
        return p6_rect, ctx, {
            "status": "FAIL_WIZARD_CLOSED_UNEXPECTEDLY",
            "reason": "Activities Next caused wizard to close or wrong control was clicked",
        }

    post_blob = collect_text_blob(after_post["entries"], min_confidence)
    ctx["post_activities_blob"] = post_blob
    ctx["post_activities_entries"] = after_post["entries"]
    ctx["next_pressed_after_activities"] = 1

    post_class = classify_post_activities_next_screen(
        after_post["entries"], min_confidence, project_name=project_name
    )

    if force_post_activities_screen_not_found_after_second_next:
        evidence.steps.append("Hook: force_post_activities_screen_not_found_after_second_next")
        ctx["forced_hook_activation"] = {
            "spreadsheet_selected": bool(ctx.get("spreadsheet_selected")),
            "spreadsheet_detected": bool(ctx.get("spreadsheet_detected")),
            "first_next_pressed": bool(ctx.get("first_next_clicked_by_ocr_bbox")),
            "export_type_screen_detected": bool(ctx.get("export_type_screen_ok")),
            "activities_selected": bool(ctx.get("activities_selected")),
            "second_next_pressed": bool(ctx.get("second_next_clicked_by_ocr_bbox")),
            "hook_applied_after_second_next": True,
            "finish_pressed": finish_pressed_in_steps(evidence.steps),
            "export_file_created": False,
            "wizard_still_open_before_hook": post_class.get("wizard_still_open", True),
            "original_post_screen_type": post_class.get("post_activities_screen_type", "unknown"),
            "original_evidence_words": list(post_class.get("evidence_words", [])),
        }
        save_discovery(evidence, "forced_hook_activation.json", ctx["forced_hook_activation"])
        post_class = {
            "post_activities_screen_type": "unknown",
            "evidence_words": [],
            "raw_ocr_text": post_class.get("raw_ocr_text", "")[:4000],
            "post_screen_ok": False,
            "wizard_still_open": post_class.get("wizard_still_open", True),
            "status": "FAIL_ACTIVITIES_NEXT_SCREEN_NOT_FOUND",
            "reason": "Hook: force_post_activities_screen_not_found_after_second_next",
        }

    ctx.update(
        {
            "post_activities_screen_type": post_class["post_activities_screen_type"],
            "post_activities_evidence_words": post_class["evidence_words"],
            "post_screen_ok": post_class["post_screen_ok"],
            "post_activities_classification_status": post_class["status"],
            "post_activities_classification_reason": post_class["reason"],
            "wizard_still_open": post_class["wizard_still_open"],
            "template_screen_ok": post_class["post_activities_screen_type"] == "template",
            "post_template_ok": post_class["post_activities_screen_type"] == "file_path",
        }
    )

    save_discovery(
        evidence,
        "post_activities_next_screen_evidence.json",
        {
            "post_activities_screen_type": post_class["post_activities_screen_type"],
            "evidence_words": post_class["evidence_words"],
            "raw_ocr_text": post_class["raw_ocr_text"],
            "next_pressed_count_total": count_next_presses(evidence.steps),
            "finish_pressed": finish_pressed_in_steps(evidence.steps),
            "export_file_created": False,
            "wizard_still_open": post_class["wizard_still_open"],
            "hook_applied": force_post_activities_screen_not_found_after_second_next,
        },
    )
    return p6_rect, ctx, None


def open_spreadsheet_to_export_type(
    evidence: ExportWizardEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
) -> Tuple[P6Rect, Dict[str, Any], Optional[Dict[str, Any]]]:
    """Open wizard, select Spreadsheet, Next once to Export Type screen."""
    open_export_menu(evidence)
    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

    after_wizard, p6_rect = capture_p6_with_pollution_retry(
        evidence, "03_after_wizard", p6_rect, p6_keyword, config, screen_rule, min_confidence
    )
    if not after_wizard.get("ok"):
        polluted = after_wizard.get("polluted")
        return p6_rect, {}, {
            "status": "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_P6_WINDOW_NOT_READY",
            "reason": after_wizard.get("error", "wizard capture failed"),
        }

    blocking, blocking_reason = detect_m16_blocking_popup(after_wizard["entries"], min_confidence)
    if blocking:
        return p6_rect, {}, {"status": "MANUAL_REVIEW_UNSAFE_POPUP", "reason": blocking_reason}

    wizard_blob = collect_text_blob(after_wizard["entries"], min_confidence)
    evidence_words = find_export_evidence_words(wizard_blob)
    wizard_detected = export_dialog_detected(evidence_words) or "export format" in normalize_text(wizard_blob)
    spreadsheet_detected, spreadsheet_text = detect_spreadsheet_in_blob(wizard_blob)
    if not spreadsheet_detected:
        ss_entry, ss_text = find_spreadsheet_entry(after_wizard["entries"], min_confidence)
        if ss_entry is not None:
            spreadsheet_detected = True
            spreadsheet_text = ss_text

    ctx = {
        "wizard_detected": wizard_detected,
        "spreadsheet_detected": spreadsheet_detected,
        "spreadsheet_text": spreadsheet_text,
        "wizard_blob": wizard_blob,
        "evidence_words": evidence_words,
        "entries": after_wizard["entries"],
        "buttons": detect_wizard_buttons(wizard_blob),
    }

    if not wizard_detected:
        return p6_rect, ctx, {"status": "FAIL_EXPORT_WIZARD_NOT_FOUND", "reason": "Export wizard not opened"}
    if not spreadsheet_detected:
        return p6_rect, ctx, {"status": "FAIL_SPREADSHEET_OPTION_NOT_FOUND", "reason": "Spreadsheet not found"}

    ss_entry, ss_click = find_spreadsheet_entry(after_wizard["entries"], min_confidence)
    if ss_entry is None:
        return p6_rect, ctx, {"status": "FAIL_SPREADSHEET_OPTION_NOT_FOUND", "reason": "No Spreadsheet bbox"}

    evidence.steps.append(f"select Spreadsheet option: OCR click on '{ss_click[:60]}'")
    click_ocr_entry(p6_rect, ss_entry)
    time.sleep(0.8)
    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

    after_select = capture_and_ocr_step(evidence, "04_after_spreadsheet_select", p6_rect, config, screen_rule)
    select_blob = collect_text_blob(after_select.get("entries", []), min_confidence) if after_select.get("ok") else ""
    ctx["spreadsheet_selected"] = confirm_spreadsheet_selected(select_blob, wizard_blob, click_attempted=True)

    select_entries = after_select.get("entries", after_wizard["entries"])
    next_entry = find_next_entry(select_entries, min_confidence) or find_next_entry(after_wizard["entries"], min_confidence)
    if next_entry is None:
        return p6_rect, ctx, {"status": "FAIL_EXPORT_TYPE_SCREEN_NOT_FOUND", "reason": "Next not found"}

    evidence.steps.append("press Next once: OCR-confirmed Next click (to Export Type)")
    click_ocr_entry(p6_rect, next_entry)
    time.sleep(STABILITY_WAIT)
    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

    after_type = capture_and_ocr_step(evidence, "05_after_export_type", p6_rect, config, screen_rule)
    if not after_type.get("ok"):
        return p6_rect, ctx, {"status": "MANUAL_REVIEW_CANNOT_CONFIRM", "reason": after_type.get("error", "type capture failed")}

    type_blob = collect_text_blob(after_type["entries"], min_confidence)
    type_words = find_export_type_evidence_words(type_blob)
    type_ok = export_type_screen_detected(type_words, type_blob)
    ctx.update(
        {
            "export_type_blob": type_blob,
            "export_type_words": type_words,
            "export_type_screen_ok": type_ok,
            "export_type_entries": after_type["entries"],
            "next_pressed_to_type": 1,
        }
    )
    if not type_ok:
        return p6_rect, ctx, {"status": "FAIL_EXPORT_TYPE_SCREEN_NOT_FOUND", "reason": "Export Type screen not confirmed"}

    return p6_rect, ctx, None


def select_activities_and_next(
    evidence: ExportWizardEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    ctx: Dict[str, Any],
) -> Tuple[P6Rect, Dict[str, Any], Optional[Dict[str, Any]]]:
    entries = ctx.get("export_type_entries", [])
    type_blob = ctx.get("export_type_blob", "")
    act_entry, act_text = find_activities_export_type_entry(entries, min_confidence)
    if act_entry is None:
        return p6_rect, ctx, {"status": "FAIL_ACTIVITIES_EXPORT_TYPE_NOT_FOUND", "reason": "Activities option not found"}

    evidence.steps.append(f"select Activities export type: OCR click on '{act_text[:60]}'")
    click_ocr_entry(p6_rect, act_entry)
    time.sleep(0.6)
    evidence.steps.append("select Activities export type: safe focus click repeat in export type list")
    click_ocr_entry(p6_rect, act_entry)
    time.sleep(1.5)
    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

    after_act = capture_and_ocr_step(evidence, "06_after_activities_select", p6_rect, config, screen_rule)
    act_blob = collect_text_blob(after_act.get("entries", []), min_confidence) if after_act.get("ok") else ""
    ctx["activities_selected"] = activities_export_type_selected(act_blob or type_blob, type_blob)
    ctx["activities_click_text"] = act_text

    if "export type" not in normalize_text(act_blob):
        return p6_rect, ctx, {
            "status": "FAIL_ACTIVITIES_EXPORT_TYPE_NOT_FOUND",
            "reason": "Lost Export Type screen after Activities click",
        }

    next_entry = find_next_entry(after_act.get("entries", entries), min_confidence) or find_next_entry(entries, min_confidence)
    if next_entry is None:
        return p6_rect, ctx, {"status": "FAIL_ACTIVITIES_NEXT_SCREEN_NOT_FOUND", "reason": "Next not found after Activities"}

    evidence.steps.append("press Next once: OCR-confirmed Next click (after Activities)")
    click_ocr_entry(p6_rect, next_entry)
    time.sleep(STABILITY_WAIT)
    time.sleep(2.0)
    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

    after_post, p6_rect = capture_p6_with_pollution_retry(
        evidence, "07_after_activities_next", p6_rect, p6_keyword, config, screen_rule, min_confidence
    )
    if not after_post.get("ok"):
        return p6_rect, ctx, {"status": "MANUAL_REVIEW_CANNOT_CONFIRM", "reason": after_post.get("error", "post capture failed")}

    post_blob = collect_text_blob(after_post["entries"], min_confidence)
    ctx["post_activities_blob"] = post_blob
    ctx["post_activities_entries"] = after_post["entries"]
    ctx["template_screen_ok"] = template_screen_detected(post_blob)
    ctx["post_template_ok"] = post_template_screen_detected(post_blob)
    ctx["next_pressed_after_activities"] = 1
    return p6_rect, ctx, None


def open_to_template_screen(
    evidence: ExportWizardEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
) -> Tuple[P6Rect, Dict[str, Any], Optional[Dict[str, Any]]]:
    """M20 path ending on template screen (Spreadsheet -> Export Type -> Activities -> Next)."""
    p6_rect, ctx, err = open_spreadsheet_to_export_type(
        evidence, p6_keyword, p6_rect, config, screen_rule, min_confidence
    )
    if err:
        return p6_rect, ctx, err
    return select_activities_and_next(
        evidence, p6_keyword, p6_rect, config, screen_rule, min_confidence, ctx
    )


def find_template_list_focus_entry(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Safe focus-click target inside template list (not wizard chrome buttons)."""
    skip = (
        "modify template",
        "delete template",
        "add template",
        "select template",
        "next",
        "back",
        "cancel",
        "finish",
        "export type",
        "export format",
    )
    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    best_text = ""
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        norm = entry.get("normalized", "")
        raw = entry.get("text", "")
        if any(s in norm for s in skip):
            continue
        if norm.strip() in {"next", "back", "cancel", "finish", "add", "delete"}:
            continue
        score = 0.0
        if "template" in norm:
            score += 8.0
        elif "activit" in norm:
            score += 6.0
        elif "spreadsheet" in norm or "column" in norm:
            score += 4.0
        elif len(norm) >= 4 and entry["confidence"] >= min_confidence + 0.1:
            score += 2.0
        if score > best_score:
            best = entry
            best_score = score
            best_text = raw or norm
    return best, best_text


def confirm_default_template_on_screen(
    evidence: ExportWizardEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    blob: str,
    entries: List[Dict[str, Any]],
    *,
    capture_tag: str = "07_after_template_focus",
) -> Tuple[bool, str, str, P6Rect, str, List[Dict[str, Any]]]:
    """Detect default/highlighted template; optional safe focus click in template list."""
    detected, excerpt = default_template_detected(blob)
    if detected:
        return True, excerpt, "", p6_rect, blob, entries

    focus_entry, focus_text = find_template_list_focus_entry(entries, min_confidence)
    if focus_entry is None:
        return False, "", "", p6_rect, blob, entries

    evidence.steps.append(f"focus template list: OCR click on '{focus_text[:60]}'")
    click_ocr_entry(p6_rect, focus_entry)
    time.sleep(0.8)
    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
    cap = capture_and_ocr_step(evidence, capture_tag, p6_rect, config, screen_rule)
    if not cap.get("ok"):
        return False, "", focus_text, p6_rect, blob, entries

    new_blob = collect_text_blob(cap["entries"], min_confidence)
    new_entries = cap["entries"]
    detected, excerpt = default_template_detected(new_blob)
    return detected, excerpt, focus_text, p6_rect, new_blob, new_entries


def press_next_from_template_screen(
    evidence: ExportWizardEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    entries: List[Dict[str, Any]],
    *,
    capture_tag: str = "08_after_template_next",
) -> Tuple[P6Rect, str, List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Press Next once from template screen and capture post-template path screen."""
    next_entry = find_next_entry(entries, min_confidence)
    if next_entry is None:
        return p6_rect, "", entries, {
            "status": "FAIL_POST_TEMPLATE_NEXT_SCREEN_NOT_FOUND",
            "reason": "Next not found on template screen",
        }

    evidence.steps.append("press Next once: OCR-confirmed Next click (after template)")
    click_ocr_entry(p6_rect, next_entry)
    time.sleep(STABILITY_WAIT)
    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

    after = capture_and_ocr_step(evidence, capture_tag, p6_rect, config, screen_rule)
    if not after.get("ok"):
        return p6_rect, "", entries, {
            "status": "MANUAL_REVIEW_CANNOT_CONFIRM",
            "reason": after.get("error", "post-template capture failed"),
        }

    post_blob = collect_text_blob(after["entries"], min_confidence)
    return p6_rect, post_blob, after["entries"], None


def open_wizard_to_format_screen(
    evidence: ExportWizardEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
) -> Tuple[P6Rect, Dict[str, Any], Optional[Dict[str, Any]]]:
    """Open File > Export and confirm first wizard (format) screen."""
    open_export_menu(evidence)
    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

    cap = capture_and_ocr_step(evidence, "03_after_wizard", p6_rect, config, screen_rule)
    if not cap.get("ok"):
        polluted = cap.get("polluted")
        return p6_rect, {}, {
            "status": "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_P6_WINDOW_NOT_READY",
            "reason": cap.get("error", "wizard capture failed"),
        }

    blocking, blocking_reason = detect_m16_blocking_popup(cap["entries"], min_confidence)
    if blocking:
        return p6_rect, {}, {"status": "MANUAL_REVIEW_UNSAFE_POPUP", "reason": blocking_reason}

    blob = collect_text_blob(cap["entries"], min_confidence)
    evidence_words = find_export_evidence_words(blob)
    wizard_detected = export_dialog_detected(evidence_words) or "export format" in normalize_text(blob)
    ctx = {
        "screen_depth": "format",
        "wizard_detected": wizard_detected,
        "wizard_blob": blob,
        "evidence_words": evidence_words,
        "entries": cap["entries"],
        "buttons": detect_wizard_buttons(blob),
    }
    if not wizard_detected:
        return p6_rect, ctx, {"status": "FAIL_EXPORT_WIZARD_NOT_FOUND", "reason": "Export wizard not opened"}
    return p6_rect, ctx, None


def open_wizard_to_export_type_screen(
    evidence: ExportWizardEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
) -> Tuple[P6Rect, Dict[str, Any], Optional[Dict[str, Any]]]:
    """Open wizard through Spreadsheet selection to Export Type screen."""
    p6_rect, ctx, err = open_spreadsheet_to_export_type(
        evidence, p6_keyword, p6_rect, config, screen_rule, min_confidence
    )
    if err:
        return p6_rect, ctx, err
    ctx["screen_depth"] = "export_type"
    return p6_rect, ctx, None


def open_wizard_to_template_screen(
    evidence: ExportWizardEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
) -> Tuple[P6Rect, Dict[str, Any], Optional[Dict[str, Any]]]:
    """Open wizard through Activities selection to template screen."""
    p6_rect, ctx, err = open_to_template_screen(
        evidence, p6_keyword, p6_rect, config, screen_rule, min_confidence
    )
    if err:
        return p6_rect, ctx, err
    ctx["screen_depth"] = "template"
    ctx["template_blob"] = ctx.get("post_activities_blob", "")
    ctx["template_entries"] = ctx.get("post_activities_entries", [])
    return p6_rect, ctx, None


def open_wizard_to_post_template_screen(
    evidence: ExportWizardEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
) -> Tuple[P6Rect, Dict[str, Any], Optional[Dict[str, Any]]]:
    """Open wizard through default template confirmation to path/output screen."""
    p6_rect, ctx, err = open_wizard_to_template_screen(
        evidence, p6_keyword, p6_rect, config, screen_rule, min_confidence
    )
    if err:
        return p6_rect, ctx, err

    blob = ctx.get("template_blob", "")
    entries = ctx.get("template_entries", [])
    default_ok, excerpt, focus_text, p6_rect, blob, entries = confirm_default_template_on_screen(
        evidence, p6_keyword, p6_rect, config, screen_rule, min_confidence, blob, entries
    )
    ctx["default_template_detected"] = default_ok
    ctx["default_template_excerpt"] = excerpt
    ctx["template_focus_click_text"] = focus_text
    ctx["template_blob"] = blob
    ctx["template_entries"] = entries
    if not default_ok:
        return p6_rect, ctx, {
            "status": "FAIL_DEFAULT_TEMPLATE_NOT_FOUND",
            "reason": "Default template not confirmed before post-template Next",
        }

    p6_rect, post_blob, post_entries, next_err = press_next_from_template_screen(
        evidence, p6_keyword, p6_rect, config, screen_rule, min_confidence, entries
    )
    if next_err:
        return p6_rect, ctx, next_err

    ctx["screen_depth"] = "post_template"
    ctx["post_template_blob"] = post_blob
    ctx["post_template_entries"] = post_entries
    return p6_rect, ctx, None


WIZARD_DEPTH_OPENERS = {
    "format": open_wizard_to_format_screen,
    "export_type": open_wizard_to_export_type_screen,
    "template": open_wizard_to_template_screen,
    "post_template": open_wizard_to_post_template_screen,
}

# --- M20 diagnostic mode (single-pass, bounded timeouts) ---

M20_DIAG_DEFAULT_MAX_RUN_SEC = 180
M20_DIAG_DEFAULT_MAX_WAIT_SEC = 8
WIZARD_CROP_MARGIN = 12

M20_POST_ACTIVITIES_EVIDENCE_WORDS = (
    "select template",
    "template",
    "modify template",
    "add",
    "delete",
    "export",
    "activity",
    "activities",
    "columns",
    "next",
    "back",
    "cancel",
    "finish",
    "file name",
    "output file",
    "browse",
)

M20_WIZARD_BOUNDS_MARKERS = (
    "export",
    "export format",
    "export type",
    "cancel",
    "next",
    "finish",
    "back",
    "prev",
    "spreadsheet",
    "data to export",
    "select template",
    "file name",
)


def m20_prewarm_easyocr() -> bool:
    """Load EasyOCR once on a tiny crop; not counted as evidence OCR."""
    if not is_easyocr_available() or Image is None:
        return False
    try:
        tmp = Path(tempfile.gettempdir()) / "m20_easyocr_prewarm.png"
        Image.new("RGB", (48, 48), color=(240, 240, 240)).save(tmp)
        run_easyocr(str(tmp))
        return True
    except Exception:  # noqa: BLE001
        return False


def classify_m20_screen_state(
    entries: List[Dict[str, Any]],
    blob: str,
    min_confidence: float,
) -> str:
    """Prefer export wizard overlay over activities_workspace when modal OCR bleeds through."""
    norm = normalize_text(blob)
    tokens = set(norm.split())
    has_export_type = "export type" in norm or "data to export" in norm
    has_export_format = "export format" in norm
    has_chrome = bool(tokens.intersection({"cancel", "next", "finish", "back", "prev"}))
    if has_export_type and has_chrome:
        return "export_type_screen"
    if has_export_format or (has_chrome and "export" in norm):
        return "export_wizard_overlay"
    if has_chrome and any(m in norm for m in ("spreadsheet", "template", "browse", "file name")):
        return "export_wizard_overlay"
    if template_screen_detected(blob):
        return "export_wizard_overlay"
    if post_template_screen_detected(blob) and has_chrome:
        return "export_wizard_overlay"
    in_activities, _ = confirms_activities_workspace(entries, min_confidence)
    if in_activities:
        return "activities_workspace"
    return "unknown"


def detect_export_wizard_bounds(
    entries: List[Dict[str, Any]],
    min_confidence: float,
    p6_width: int,
    p6_height: int,
) -> Dict[str, float]:
    """Detect export wizard modal rectangle in P6 crop coordinates."""
    xs: List[float] = []
    ys: List[float] = []
    chrome_norms = {"cancel", "next", "finish", "back", "prev"}
    for entry in entries:
        if entry.get("confidence", 0) < min_confidence:
            continue
        norm = entry.get("normalized", "")
        raw = entry.get("text", "").lower()
        pt_ys = [p[1] for p in entry.get("bbox", [[0, 0]])]
        yc = sum(pt_ys) / len(pt_ys)
        is_chrome = norm in chrome_norms
        hit = is_chrome or any(m in norm or m in raw for m in M20_WIZARD_BOUNDS_MARKERS)
        if not hit:
            continue
        if yc < 350 and not is_chrome and "export type" not in norm and "export format" not in norm:
            continue
        for pt in entry.get("bbox", []):
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))
    fb = {
        "x_min": float(p6_width) * 0.32,
        "y_min": float(p6_height) * 0.34,
        "x_max": float(p6_width) * 0.88,
        "y_max": float(p6_height) * 0.72,
        "source": "geometry_fallback",
    }
    if xs and ys:
        pad = 24.0
        bounds = {
            "x_min": max(0.0, min(xs) - pad),
            "y_min": max(0.0, min(ys) - pad),
            "x_max": min(float(p6_width), max(xs) + pad),
            "y_max": min(float(p6_height), max(ys) + pad),
            "source": "ocr_anchors",
        }
        if bounds["y_max"] - bounds["y_min"] < 280:
            bounds = {
                "x_min": min(bounds["x_min"], fb["x_min"]),
                "y_min": min(bounds["y_min"], fb["y_min"]),
                "x_max": max(bounds["x_max"], fb["x_max"]),
                "y_max": max(bounds["y_max"], fb["y_max"]),
                "source": "ocr_anchors+expanded",
            }
        return bounds
    return fb


def ocr_wizard_crop(
    evidence: ExportWizardEvidence,
    label: str,
    p6_rect: P6Rect,
    wizard_bounds: Dict[str, float],
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
) -> Dict[str, Any]:
    """Capture P6 window, OCR only wizard/dialog sub-crop; bbox coords in P6 crop space."""
    if Image is None:
        return {"ok": False, "error": "Pillow not available", "screen_state": "unknown"}
    meta_path = evidence.ocr_dir / f"{label}_wizard_capture_metadata.json"
    capture = capture_p6_window_only(
        evidence.screenshots_dir,
        f"{label}_p6_crop.png",
        p6_rect,
        metadata_path=meta_path,
    )
    if not capture.get("success"):
        return {"ok": False, "error": capture.get("error", "capture failed"), "screen_state": "unknown"}

    evidence.screenshots.append(capture["image_path"])
    margin = WIZARD_CROP_MARGIN
    x0 = max(0, int(wizard_bounds.get("x_min", 0) - margin))
    y0 = max(0, int(wizard_bounds.get("y_min", 0) - margin))
    x1 = min(p6_rect.width, int(wizard_bounds.get("x_max", p6_rect.width) + margin))
    y1 = min(p6_rect.height, int(wizard_bounds.get("y_max", p6_rect.height) + margin))
    if x1 <= x0 or y1 <= y0:
        evidence.steps.append(f"M21/M20: wizard crop bounds invalid for {label} — fallback to full P6 OCR")
        cap_full = capture_and_ocr_step(evidence, label, p6_rect, config, screen_rule)
        if cap_full.get("ok"):
            blob = collect_text_blob(cap_full["entries"], min_confidence)
            cap_full["screen_state"] = classify_m20_screen_state(cap_full["entries"], blob, min_confidence)
            cap_full["ocr_mode"] = "p6_full_fallback"
        return cap_full

    wizard_crop_path = evidence.screenshots_dir / f"{label}_wizard_crop.png"
    with Image.open(capture["image_path"]) as img:
        img.crop((x0, y0, x1, y1)).save(wizard_crop_path)
    evidence.screenshots.append(str(wizard_crop_path))

    if not is_easyocr_available():
        return {"ok": False, "error": "EasyOCR not available", "screen_state": "unknown"}

    raw = run_easyocr(str(wizard_crop_path))
    ocr_path = str(evidence.ocr_dir / f"{label}_wizard_ocr.json")
    save_ocr_results(
        raw,
        ocr_path,
        metadata={
            **(capture.get("metadata") or {}),
            "wizard_bounds": wizard_bounds,
            "crop_offset": [x0, y0],
            "ocr_mode": "wizard_crop",
        },
    )
    evidence.ocr_files.append(ocr_path)

    entries = ocr_to_entries(raw)
    for entry in entries:
        entry["bbox"] = [[p[0] + x0, p[1] + y0] for p in entry.get("bbox", [])]

    polluted = check_m20_pollution(entries, min_confidence)
    if polluted["polluted"]:
        return {
            "ok": False,
            "error": f"OCR pollution: {polluted['pollution_words']}",
            "polluted": True,
            "entries": entries,
            "screen_state": "unknown",
        }

    blob = collect_text_blob(entries, min_confidence)
    screen_state = classify_m20_screen_state(entries, blob, min_confidence)
    txt_path = evidence.ocr_dir / f"{label}_wizard_ocr.txt"
    txt_path.write_text(blob[:8000], encoding="utf-8")

    return {
        "ok": True,
        "entries": entries,
        "screen_state": screen_state,
        "ocr_mode": "wizard_crop",
        "wizard_crop_path": str(wizard_crop_path),
        "cached_wizard_bounds": wizard_bounds,
    }


M20_POST_PROJECTS_TO_EXPORT_WORDS = (
    "projects to export",
    "open projects",
    "export project",
    "project",
    "001",
    "talison",
    "next",
    "back",
    "cancel",
    "finish",
)

M20_POST_TEMPLATE_SCREEN_WORDS = (
    "select template",
    "template",
    "modify template",
    "add",
    "delete",
    "columns",
    "next",
    "back",
    "cancel",
    "finish",
)

M20_POST_FILE_PATH_SCREEN_WORDS = (
    "file name",
    "output file",
    "browse",
    "select file",
    "spreadsheet",
    "next",
    "back",
    "cancel",
    "finish",
)


def collect_post_activities_marker_words(blob: str, markers: Tuple[str, ...]) -> List[str]:
    norm = normalize_text(blob)
    return sorted({w for w in markers if w in norm})


def classify_post_activities_next_screen(
    entries: List[Dict[str, Any]],
    min_confidence: float,
    *,
    project_name: str = "",
) -> Dict[str, Any]:
    """Classify post-Activities wizard screen after second Next."""
    blob = collect_text_blob(entries, min_confidence)
    norm = normalize_text(blob)
    chrome_tokens = {"next", "back", "cancel", "finish", "prev"}
    has_chrome = bool(set(norm.split()).intersection(chrome_tokens)) or wizard_chrome_visible(
        entries, min_confidence
    )
    wizard_still_open = (
        export_wizard_open_in_capture(entries, min_confidence)[0]
        or wizard_chrome_visible(entries, min_confidence)
    ) and not wizard_truly_closed(entries, min_confidence)

    evidence_words = collect_post_activities_diagnostic_evidence(blob)
    for token in project_name.replace("-", " ").split():
        tok = normalize_text(token)
        if len(tok) >= 2 and tok in norm and tok not in evidence_words:
            evidence_words.append(tok)
    evidence_words = sorted(set(evidence_words))

    if wizard_truly_closed(entries, min_confidence):
        return {
            "post_activities_screen_type": "unknown",
            "evidence_words": evidence_words,
            "raw_ocr_text": blob[:4000],
            "post_screen_ok": False,
            "wizard_still_open": False,
            "status": "FAIL_WIZARD_CLOSED_UNEXPECTEDLY",
            "reason": "Wizard closed; Activities workspace visible after second Next",
        }

    projects_words = collect_post_activities_marker_words(blob, M20_POST_PROJECTS_TO_EXPORT_WORDS)
    template_words = collect_post_activities_marker_words(blob, M20_POST_TEMPLATE_SCREEN_WORDS)
    file_words = collect_post_activities_marker_words(blob, M20_POST_FILE_PATH_SCREEN_WORDS)

    projects_detected = has_chrome and (
        ("projects to export" in norm or "open projects" in norm)
        and ("export project" in norm or "project name" in norm)
    )
    template_detected = has_chrome and (
        any(m in norm for m in ("select template", "modify template"))
        or ("template" in norm and any(m in norm for m in ("add", "delete", "columns")))
    )
    file_detected = has_chrome and any(
        m in norm for m in ("file name", "output file", "browse", "select file")
    )

    if projects_detected:
        words = sorted(set(projects_words + evidence_words))
        return {
            "post_activities_screen_type": "projects_to_export",
            "evidence_words": words,
            "raw_ocr_text": blob[:4000],
            "post_screen_ok": True,
            "wizard_still_open": wizard_still_open,
            "status": "PASS_ACTIVITIES_NEXT_DISCOVERY",
            "reason": "Activities selected, second Next reached Projects-to-export screen",
        }

    if template_detected:
        words = sorted(set(template_words + evidence_words))
        return {
            "post_activities_screen_type": "template",
            "evidence_words": words,
            "raw_ocr_text": blob[:4000],
            "post_screen_ok": True,
            "wizard_still_open": wizard_still_open,
            "status": "PASS_ACTIVITIES_NEXT_DISCOVERY",
            "reason": "Post-Activities template screen discovered",
        }

    if file_detected:
        words = sorted(set(file_words + evidence_words))
        return {
            "post_activities_screen_type": "file_path",
            "evidence_words": words,
            "raw_ocr_text": blob[:4000],
            "post_screen_ok": True,
            "wizard_still_open": wizard_still_open,
            "status": "PASS_ACTIVITIES_NEXT_DISCOVERY",
            "reason": "Post-Activities file/path screen discovered",
        }

    non_bg = [w for w in evidence_words if w not in ("export", "next", "back", "cancel", "finish")]
    if wizard_still_open and has_chrome and len(non_bg) >= 1:
        return {
            "post_activities_screen_type": "generic_wizard",
            "evidence_words": evidence_words,
            "raw_ocr_text": blob[:4000],
            "post_screen_ok": True,
            "wizard_still_open": True,
            "status": "PASS_ACTIVITIES_NEXT_DISCOVERY_PARTIAL",
            "reason": f"Partial post-Activities discovery ({len(evidence_words)} evidence words)",
        }

    if wizard_still_open:
        return {
            "post_activities_screen_type": "unknown",
            "evidence_words": evidence_words,
            "raw_ocr_text": blob[:4000],
            "post_screen_ok": False,
            "wizard_still_open": True,
            "status": "FAIL_ACTIVITIES_NEXT_SCREEN_NOT_FOUND",
            "reason": "Wizard open but post-Activities next screen not detected",
        }

    return {
        "post_activities_screen_type": "unknown",
        "evidence_words": evidence_words,
        "raw_ocr_text": blob[:4000],
        "post_screen_ok": False,
        "wizard_still_open": False,
        "status": "FAIL_WIZARD_CLOSED_UNEXPECTEDLY",
        "reason": "Export wizard not detected after second Next",
    }


def classify_cp06_post_activities(
    entries: List[Dict[str, Any]],
    min_confidence: float,
    *,
    project_name: str = "",
) -> Tuple[str, str, List[str]]:
    """Classify post-Activities wizard screen for diagnostic CP06."""
    result = classify_post_activities_next_screen(entries, min_confidence, project_name=project_name)
    return result["status"], result["reason"], result["evidence_words"]


@dataclass
class M20DiagnosticState:
    diagnostic_dir: Path
    max_run_sec: int = M20_DIAG_DEFAULT_MAX_RUN_SEC
    max_wait_sec: int = M20_DIAG_DEFAULT_MAX_WAIT_SEC
    timer_start: float = field(default_factory=time.monotonic)
    trace: List[Dict[str, Any]] = field(default_factory=list)
    click_targets: List[Dict[str, Any]] = field(default_factory=list)
    next_pressed_count_total: int = 0
    last_successful_checkpoint: str = ""
    failed_checkpoint: str = ""
    wizard_closed_unexpectedly: bool = False
    export_snap_before: Optional[set] = None
    cached_wizard_bounds: Optional[Dict[str, float]] = None
    ocr_mode_after_cp01: str = "p6_full"
    easyocr_prewarmed: bool = False
    dialog_closed: bool = False

    def elapsed(self) -> float:
        return time.monotonic() - self.timer_start

    def timed_out(self) -> bool:
        return self.elapsed() >= self.max_run_sec

    def timeout_result(self, checkpoint: str) -> Dict[str, Any]:
        return {
            "status": "FAIL_TIMEOUT_CONTROLLED",
            "reason": f"M20 diagnostic exceeded {self.max_run_sec}s at {checkpoint}",
            "failed_checkpoint": checkpoint,
        }

    def sleep(self, seconds: float) -> None:
        cap = min(seconds, self.max_wait_sec, max(0.0, self.max_run_sec - self.elapsed()))
        if cap > 0:
            time.sleep(cap)


def p6_rect_to_dict(p6_rect: P6Rect) -> Dict[str, int]:
    return {"left": int(p6_rect.left), "top": int(p6_rect.top), "width": int(p6_rect.width), "height": int(p6_rect.height)}


def ocr_words_with_bbox(entries: List[Dict[str, Any]], min_confidence: float) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for entry in entries:
        if entry.get("confidence", 0) < min_confidence:
            continue
        out.append(
            {
                "text": entry.get("text", ""),
                "normalized": entry.get("normalized", ""),
                "bbox": entry.get("bbox"),
                "confidence": entry.get("confidence"),
            }
        )
    return out


def entry_center_in_wizard(entry: Dict[str, Any], bounds: Dict[str, float]) -> bool:
    cx, yc = bbox_center(entry)
    return (
        bounds.get("x_min", 0.0) <= cx <= bounds.get("x_max", 99999.0)
        and bounds.get("y_min", 350.0) <= yc <= bounds.get("y_max", 950.0)
    )


def find_activities_export_type_candidates(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> List[Dict[str, Any]]:
    export_type_y, relationships_y = find_export_type_anchor_ys(entries, min_confidence)
    neighbor_norms = {
        e.get("normalized", "")
        for e in entries
        if e["confidence"] >= min_confidence
        and sum(p[1] for p in e.get("bbox", [[0, 0]])) / max(len(e.get("bbox", [1])), 1) >= 400
    }
    has_neighbors = any(
        k in " ".join(neighbor_norms)
        for k in (
            "activity relationships",
            "activity relationship",
            "relationships",
            "resources",
            "expenses",
            "resource assignments",
        )
    )
    candidates: List[Dict[str, Any]] = []
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        norm = entry.get("normalized", "")
        score = score_activities_export_type_entry(norm)
        if score <= 0:
            continue
        ys = [p[1] for p in entry.get("bbox", [[0, 0]])]
        y_center = sum(ys) / len(ys)
        if y_center < 400:
            score *= 0.05
        elif export_type_y is not None and y_center <= export_type_y + 5:
            score *= 0.1
        elif relationships_y is not None and y_center >= relationships_y - 5:
            score *= 0.05
        if has_neighbors and export_type_y and relationships_y:
            if not (export_type_y < y_center < relationships_y):
                score *= 0.1
        if score >= 8.0:
            candidates.append(
                {
                    "text": entry.get("text", ""),
                    "normalized": norm,
                    "bbox": entry.get("bbox"),
                    "score": round(score, 2),
                    "y_center": round(y_center, 1),
                }
            )
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


def collect_post_activities_diagnostic_evidence(blob: str) -> List[str]:
    norm = normalize_text(blob)
    return sorted({w for w in M20_POST_ACTIVITIES_EVIDENCE_WORDS if w in norm})


def m20_diagnostic_capture_once(
    evidence: ExportWizardEvidence,
    label: str,
    p6_rect: P6Rect,
    p6_keyword: str,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    *,
    allow_pollution_retry: bool = True,
    wizard_bounds: Optional[Dict[str, float]] = None,
) -> Tuple[Dict[str, Any], P6Rect, bool, List[str]]:
    """Full P6 crop (CP00/CP01) or wizard crop (CP02+) with single pollution retry."""
    window_tools.activate_window_by_title(p6_keyword)
    time.sleep(0.2)

    def do_capture(lbl: str) -> Dict[str, Any]:
        if wizard_bounds:
            return ocr_wizard_crop(
                evidence, lbl, p6_rect, wizard_bounds, config, screen_rule, min_confidence
            )
        cap = capture_and_ocr_step(evidence, lbl, p6_rect, config, screen_rule)
        if cap.get("ok"):
            blob = collect_text_blob(cap["entries"], min_confidence)
            cap["screen_state"] = classify_m20_screen_state(cap["entries"], blob, min_confidence)
            cap["ocr_mode"] = "p6_full"
        return cap

    cap = do_capture(label)
    polluted = (not cap.get("ok") and cap.get("polluted")) or (
        cap.get("ok") and check_m20_pollution(cap.get("entries", []), min_confidence)["polluted"]
    )
    pollution_words: List[str] = []
    if polluted:
        pollution_words = check_m20_pollution(cap.get("entries", []), min_confidence).get("pollution_words", [])
        if not pollution_words and cap.get("error"):
            pollution_words = [
                w.strip().strip("'")
                for w in cap.get("error", "").replace("OCR pollution:", "").strip("[]").split(",")
            ]
        if allow_pollution_retry:
            evidence.steps.append(f"M20 diagnostic: pollution on {label} — single refocus recapture")
            window_tools.activate_window_by_title(p6_keyword)
            time.sleep(1.0)
            p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
            cap = do_capture(f"{label}_retry")
            still = (not cap.get("ok") and cap.get("polluted")) or (
                cap.get("ok") and check_m20_pollution(cap.get("entries", []), min_confidence)["polluted"]
            )
            if still:
                pollution_words = check_m20_pollution(cap.get("entries", []), min_confidence).get("pollution_words", [])
                return cap, p6_rect, True, pollution_words
            return cap, p6_rect, False, pollution_words
    return cap, p6_rect, bool(polluted), pollution_words


def m20_diagnostic_save_checkpoint(
    state: M20DiagnosticState,
    evidence: ExportWizardEvidence,
    checkpoint_name: str,
    *,
    p6_rect: P6Rect,
    p6_keyword: str,
    cap: Dict[str, Any],
    min_confidence: float,
    target_clicked: bool = False,
    target_bbox: Optional[Any] = None,
    click_point: Optional[Dict[str, int]] = None,
    status_at_checkpoint: str = "OK",
    reason: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    base = checkpoint_name
    entries = cap.get("entries", []) if cap.get("ok") else []
    bounds = cap.get("cached_wizard_bounds") or estimate_wizard_bounds(entries, min_confidence) if entries else {"y_min": 400.0, "y_max": 950.0}
    if isinstance(bounds, dict) and "x_min" not in bounds:
        bounds = {**bounds, "x_min": 0.0, "x_max": p6_rect.width}
    blob = collect_text_blob(entries, min_confidence) if entries else ""
    polluted, pollution_words = False, []
    if entries:
        pol = check_m20_pollution(entries, min_confidence)
        polluted = pol["polluted"]
        pollution_words = pol.get("pollution_words", [])

    png_src = cap.get("wizard_crop_path") or (evidence.screenshots[-1] if evidence.screenshots else "")
    png_dst = str(state.diagnostic_dir / f"{base}.png")
    if png_src and Path(png_src).exists():
        shutil.copy2(png_src, png_dst)

    txt_path = state.diagnostic_dir / f"{base}.txt"
    txt_path.write_text(blob[:8000] if blob else cap.get("error", ""), encoding="utf-8")

    payload: Dict[str, Any] = {
        "checkpoint_name": checkpoint_name,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "foreground_window_title": window_tools.get_window_state(p6_keyword).get("title") or "",
        "p6_rect": p6_rect_to_dict(p6_rect),
        "wizard_bounds": bounds,
        "screen_state": cap.get("screen_state", "unknown"),
        "ocr_text": blob[:4000],
        "ocr_words_with_bbox": ocr_words_with_bbox(entries, min_confidence),
        "pollution_detected": polluted or bool(pollution_words),
        "pollution_words": pollution_words,
        "target_clicked": target_clicked,
        "target_bbox": target_bbox,
        "click_point": click_point,
        "next_pressed_count_total": state.next_pressed_count_total,
        "finish_pressed": finish_pressed_in_steps(evidence.steps),
        "export_file_created": export_file_created(state.export_snap_before or set(), snapshot_export_files()),
        "status_at_checkpoint": status_at_checkpoint,
        "reason": reason,
        "screenshot": png_dst,
        "ocr_txt": str(txt_path),
    }
    if extra:
        payload.update(extra)

    json_path = state.diagnostic_dir / f"{base}.json"
    write_json(json_path, payload)
    state.trace.append({"checkpoint": checkpoint_name, "status": status_at_checkpoint, "reason": reason})
    if status_at_checkpoint == "OK":
        state.last_successful_checkpoint = checkpoint_name
    return str(json_path)


def m20_diagnostic_cancel_wizard(
    evidence: ExportWizardEvidence,
    p6_rect: P6Rect,
    p6_keyword: str,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    *,
    wizard_bounds: Optional[Dict[str, float]] = None,
) -> Tuple[Dict[str, Any], P6Rect, bool]:
    cap, p6_rect, _, _ = m20_diagnostic_capture_once(
        evidence, "diag_cp07_before_cancel", p6_rect, p6_keyword, config, screen_rule, min_confidence,
        wizard_bounds=wizard_bounds,
    )
    closed = False
    if cap.get("ok"):
        cancel_entry = find_cancel_entry(cap["entries"], min_confidence)
        if cancel_entry is not None:
            evidence.steps.append("M20 diagnostic: OCR-confirmed Cancel on export wizard")
            click_ocr_entry(p6_rect, cancel_entry)
            time.sleep(1.0)
            p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
            closed = True
    cap2, p6_rect, _, _ = m20_diagnostic_capture_once(
        evidence, "diag_cp07_final", p6_rect, p6_keyword, config, screen_rule, min_confidence,
        allow_pollution_retry=False,
        wizard_bounds=wizard_bounds,
    )
    if cap2.get("ok") and not wizard_chrome_visible(cap2["entries"], min_confidence):
        closed = True
    return cap2, p6_rect, closed


def m20_run_diagnostic(
    evidence: ExportWizardEvidence,
    project_name: str,
    p6_keyword: str,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    diagnostic_dir: Path,
    *,
    diagnostic_max_sec: int = M20_DIAG_DEFAULT_MAX_RUN_SEC,
    ui_wait_sec: int = M20_DIAG_DEFAULT_MAX_WAIT_SEC,
) -> Dict[str, Any]:
    """Single-pass M20 diagnostic with checkpoints 00-07 and hard timeouts."""
    state = M20DiagnosticState(
        diagnostic_dir=diagnostic_dir,
        max_run_sec=diagnostic_max_sec,
        max_wait_sec=ui_wait_sec,
        export_snap_before=snapshot_export_files(),
        easyocr_prewarmed=m20_prewarm_easyocr(),
    )
    trace_path = diagnostic_dir / "m20_diagnostic_trace.json"
    targets_path = diagnostic_dir / "all_click_targets.json"

    def persist_artifacts() -> None:
        write_json(trace_path, {"checkpoints": state.trace, "elapsed_sec": round(state.elapsed(), 2)})
        write_json(targets_path, {"click_targets": state.click_targets})

    def fail(
        status: str,
        reason: str,
        checkpoint: str,
        *,
        cap: Optional[Dict[str, Any]] = None,
        p6_rect: Optional[P6Rect] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        state.failed_checkpoint = checkpoint
        already = any(t.get("checkpoint") == checkpoint for t in state.trace)
        if cap is not None and p6_rect is not None and not already:
            m20_diagnostic_save_checkpoint(
                state, evidence, checkpoint,
                p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap, min_confidence=min_confidence,
                status_at_checkpoint=status, reason=reason, extra=extra,
            )
        persist_artifacts()
        ss_bbox = next((t.get("bbox") for t in state.click_targets if t.get("step") == "spreadsheet"), None)
        n1_bbox = next((t.get("bbox") for t in state.click_targets if t.get("step") == "first_next"), None)
        act_bbox = next((t.get("bbox") for t in state.click_targets if t.get("step") == "activities"), None)
        n2_bbox = next((t.get("bbox") for t in state.click_targets if t.get("step") == "second_next"), None)
        act_target = next((t for t in state.click_targets if t.get("step") == "activities"), {})
        return {
            "status": status,
            "reason": reason,
            "failed_checkpoint": checkpoint,
            "last_successful_checkpoint": state.last_successful_checkpoint,
            "next_pressed_count_total": state.next_pressed_count_total,
            "finish_pressed": finish_pressed_in_steps(evidence.steps),
            "export_file_created": export_file_created(state.export_snap_before or set(), snapshot_export_files()),
            "wizard_closed_unexpectedly": state.wizard_closed_unexpectedly,
            "diagnostic_trace_file": str(trace_path),
            "all_click_targets_file": str(targets_path),
            "click_targets": state.click_targets,
            "spreadsheet_target_bbox": ss_bbox,
            "first_next_bbox": n1_bbox,
            "activities_target_bbox": act_bbox,
            "second_next_bbox": n2_bbox,
            "activities_option_text": act_target.get("text", ""),
            "activities_candidates": act_target.get("candidates", []),
            "easyocr_prewarmed": state.easyocr_prewarmed,
            "ocr_mode_after_cp01": state.ocr_mode_after_cp01,
            "diagnostic_duration_seconds": round(state.elapsed(), 2),
            "wizard_bounds_cached": state.cached_wizard_bounds,
            "dialog_closed": state.dialog_closed,
        }

    # --- Checkpoint 00: clean start ---
    if state.timed_out():
        t = state.timeout_result("checkpoint_00_clean_start")
        return fail(t["status"], t["reason"], t["failed_checkpoint"])

    evidence.steps.append("M20 diagnostic CP00: prepare_p6_for_test")
    prep = prepare_p6_for_test(p6_keyword)
    if not prep.get("success") or not prep.get("rect"):
        cap = {"ok": False, "error": prep.get("message", "P6 not ready"), "screen_state": "unknown"}
        return fail("MANUAL_REVIEW_CANNOT_CONFIRM", cap["error"], "checkpoint_00_clean_start", cap=cap, p6_rect=P6Rect(0, 0, 800, 600))

    p6_rect: P6Rect = prep["rect"]
    window_tools.activate_window_by_title(p6_keyword)
    window_tools.maximize_window_by_title(p6_keyword)
    state.sleep(0.5)

    cap, p6_rect, polluted, pol_words = m20_diagnostic_capture_once(
        evidence, "diag_cp00", p6_rect, p6_keyword, config, screen_rule, min_confidence
    )
    if polluted and not cap.get("ok"):
        return fail("MANUAL_REVIEW_CANNOT_CONFIRM", f"OCR pollution at CP00: {pol_words}", "checkpoint_00_clean_start", cap=cap, p6_rect=p6_rect)

    if cap.get("ok") and open_project_dialog_detected(cap, min_confidence):
        try_close_dialog_once(evidence, p6_rect, cap["entries"], p6_keyword, min_confidence, dialog_name="Open Project", confirmed=True)
        state.sleep(1.0)
        p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
        cap, p6_rect, _, _ = m20_diagnostic_capture_once(
            evidence, "diag_cp00_after_open_project", p6_rect, p6_keyword, config, screen_rule, min_confidence
        )

    if cap.get("ok"):
        wizard_open, _, _ = export_wizard_open_in_capture(cap["entries"], min_confidence)
        if wizard_open:
            cancel_entry = find_cancel_entry(cap["entries"], min_confidence)
            if cancel_entry:
                click_ocr_entry(p6_rect, cancel_entry)
            else:
                keyboard_tools.press_escape()
            state.sleep(1.0)
            p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
            cap, p6_rect, _, _ = m20_diagnostic_capture_once(
                evidence, "diag_cp00_after_wizard_close", p6_rect, p6_keyword, config, screen_rule, min_confidence
            )

    window_title = window_tools.get_window_state(p6_keyword).get("title") or ""
    if not cap.get("ok"):
        return fail("MANUAL_REVIEW_CANNOT_CONFIRM", cap.get("error", "CP00 capture failed"), "checkpoint_00_clean_start", cap=cap, p6_rect=p6_rect)

    open_ok, open_reason, _ = confirm_project_open(cap["entries"], project_name, window_title, min_confidence)
    if not open_ok:
        m20_diagnostic_save_checkpoint(
            state, evidence, "checkpoint_00_clean_start",
            p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap, min_confidence=min_confidence,
            status_at_checkpoint="FAIL_PROJECT_NOT_OPEN", reason=open_reason,
        )
        return fail("FAIL_PROJECT_NOT_OPEN", open_reason, "checkpoint_00_clean_start", cap=cap, p6_rect=p6_rect)

    in_activities, _ = confirms_activities_workspace(cap["entries"], min_confidence)
    if not in_activities:
        navigate_to_activities(evidence)
        state.sleep(STABILITY_WAIT)
        p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
        cap, p6_rect, _, _ = m20_diagnostic_capture_once(
            evidence, "diag_cp00_activities", p6_rect, p6_keyword, config, screen_rule, min_confidence
        )
        in_activities, _ = confirms_activities_workspace(cap.get("entries", []), min_confidence) if cap.get("ok") else (False, [])
        if not in_activities:
            return fail("FAIL_ACTIVITIES_NOT_FOUND", "Activities workspace not confirmed", "checkpoint_00_clean_start", cap=cap, p6_rect=p6_rect)

    m20_diagnostic_save_checkpoint(
        state, evidence, "checkpoint_00_clean_start",
        p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap, min_confidence=min_confidence,
        status_at_checkpoint="OK", reason="Clean start confirmed",
    )

    # --- Checkpoint 01: open export wizard ---
    if state.timed_out():
        t = state.timeout_result("checkpoint_01_export_wizard")
        return fail(t["status"], t["reason"], t["failed_checkpoint"])

    evidence.steps.append("M20 diagnostic CP01: Alt+F, E (File > Export)")
    keyboard_tools.press_escape()
    state.sleep(0.3)
    keyboard_tools.hotkey("alt", "f")
    state.sleep(0.6)
    keyboard_tools.press_key("e")
    state.sleep(min(STABILITY_WAIT, state.max_wait_sec))
    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

    cap, p6_rect, polluted, pol_words = m20_diagnostic_capture_once(
        evidence, "diag_cp01", p6_rect, p6_keyword, config, screen_rule, min_confidence
    )
    if polluted and not cap.get("ok"):
        return fail("MANUAL_REVIEW_CANNOT_CONFIRM", f"OCR pollution at CP01: {pol_words}", "checkpoint_01_export_wizard", cap=cap, p6_rect=p6_rect)
    if not cap.get("ok"):
        return fail("FAIL_STEP_FILE_EXPORT_NOT_OPENED", cap.get("error", "Capture failed after File > Export"), "checkpoint_01_export_wizard", cap=cap, p6_rect=p6_rect)

    wizard_blob = collect_text_blob(cap["entries"], min_confidence)
    wizard_words = find_export_evidence_words(wizard_blob)
    wizard_detected = export_dialog_detected(wizard_words) or "export format" in normalize_text(wizard_blob)
    m20_diagnostic_save_checkpoint(
        state, evidence, "checkpoint_01_export_wizard",
        p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap, min_confidence=min_confidence,
        status_at_checkpoint="OK" if wizard_detected else "FAIL_STEP_FILE_EXPORT_NOT_OPENED",
        reason="Export wizard visible" if wizard_detected else "Export wizard not detected",
        extra={"wizard_detected": wizard_detected, "evidence_words": wizard_words},
    )
    if not wizard_detected:
        return fail("FAIL_STEP_FILE_EXPORT_NOT_OPENED", "Export wizard not visible after File > Export", "checkpoint_01_export_wizard", cap=cap, p6_rect=p6_rect)

    state.cached_wizard_bounds = detect_export_wizard_bounds(
        cap["entries"], min_confidence, p6_rect.width, p6_rect.height
    )
    state.ocr_mode_after_cp01 = "wizard_crop"
    write_json(diagnostic_dir / "wizard_bounds.json", state.cached_wizard_bounds)
    wb = state.cached_wizard_bounds

    pre_wizard_blob = wizard_blob

    # --- Checkpoint 02: Spreadsheet ---
    if state.timed_out():
        t = state.timeout_result("checkpoint_02_spreadsheet_selected")
        return fail(t["status"], t["reason"], t["failed_checkpoint"])

    bounds = wb
    ss_entry, ss_text = find_spreadsheet_entry(cap["entries"], min_confidence)
    ss_bbox = entry_bbox_dict(ss_entry)
    click_pt = click_point_from_entry(p6_rect, ss_entry) if ss_entry else None
    ss_in_bounds = ss_entry is not None and entry_center_in_wizard(ss_entry, bounds)

    if ss_entry is not None and ss_in_bounds:
        state.click_targets.append({"step": "spreadsheet", "text": ss_text, "bbox": ss_bbox, "click_point": click_pt})
        evidence.steps.append(f"M20 diagnostic CP02: OCR-click Spreadsheet '{ss_text[:50]}'")
        click_ocr_entry(p6_rect, ss_entry)
        state.sleep(0.8)
        p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
        target_clicked = True
    else:
        target_clicked = False

    cap, p6_rect, _, _ = m20_diagnostic_capture_once(
        evidence, "diag_cp02", p6_rect, p6_keyword, config, screen_rule, min_confidence,
        wizard_bounds=wb,
    )
    if not cap.get("ok"):
        return fail("FAIL_STEP_SPREADSHEET_CLICK_UNCONFIRMED", cap.get("error", "Capture failed after Spreadsheet"), "checkpoint_02_spreadsheet_selected", cap=cap, p6_rect=p6_rect)

    select_blob = collect_text_blob(cap["entries"], min_confidence)
    ss_selected = confirm_spreadsheet_selected(select_blob, pre_wizard_blob, click_attempted=target_clicked)
    cp02_status = "OK" if ss_selected else "FAIL_STEP_SPREADSHEET_CLICK_UNCONFIRMED"
    m20_diagnostic_save_checkpoint(
        state, evidence, "checkpoint_02_spreadsheet_selected",
        p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap, min_confidence=min_confidence,
        target_clicked=target_clicked, target_bbox=ss_bbox, click_point=click_pt,
        status_at_checkpoint=cp02_status,
        reason="Spreadsheet selected" if ss_selected else "Spreadsheet selection not confirmed",
        extra={"spreadsheet_text": ss_text, "spreadsheet_in_wizard_bounds": ss_in_bounds},
    )
    if not ss_selected:
        cap_cancel, p6_rect, _ = m20_diagnostic_cancel_wizard(
            evidence, p6_rect, p6_keyword, config, screen_rule, min_confidence, wizard_bounds=wb
        )
        m20_diagnostic_save_checkpoint(state, evidence, "checkpoint_07_final", p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap_cancel, min_confidence=min_confidence, status_at_checkpoint="ABORT", reason="Cancel after CP02 fail")
        return fail("FAIL_STEP_SPREADSHEET_CLICK_UNCONFIRMED", "Spreadsheet click/selection not confirmed", "checkpoint_02_spreadsheet_selected", cap=cap, p6_rect=p6_rect)

    # --- Checkpoint 03: first Next ---
    if state.timed_out():
        t = state.timeout_result("checkpoint_03_export_type_screen")
        return fail(t["status"], t["reason"], t["failed_checkpoint"])

    next_entry, nbounds = find_wizard_next_button(cap["entries"], min_confidence)
    if next_entry is None:
        next_entry = find_next_entry(cap["entries"], min_confidence)
    next_bbox = entry_bbox_dict(next_entry)
    next_pt = click_point_from_entry(p6_rect, next_entry) if next_entry else None
    bounds_use = wb
    next_valid = next_entry is not None and entry_center_in_wizard(next_entry, bounds_use) and next_in_wizard_bounds(next_entry, bounds_use)

    if not next_valid:
        cap_cancel, p6_rect, _ = m20_diagnostic_cancel_wizard(
            evidence, p6_rect, p6_keyword, config, screen_rule, min_confidence, wizard_bounds=wb
        )
        m20_diagnostic_save_checkpoint(state, evidence, "checkpoint_07_final", p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap_cancel, min_confidence=min_confidence, status_at_checkpoint="ABORT", reason="Cancel after CP03 Next not found")
        return fail("FAIL_STEP_FIRST_NEXT_UNCONFIRMED", "First Next button not found inside wizard bounds", "checkpoint_03_export_type_screen", cap=cap, p6_rect=p6_rect)

    state.click_targets.append({"step": "first_next", "bbox": next_bbox, "click_point": next_pt})
    evidence.steps.append("press Next once: OCR-confirmed Next click (to Export Type)")
    click_ocr_entry(p6_rect, next_entry)
    state.next_pressed_count_total = 1
    state.sleep(STABILITY_WAIT)
    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

    cap, p6_rect, _, _ = m20_diagnostic_capture_once(
        evidence, "diag_cp03", p6_rect, p6_keyword, config, screen_rule, min_confidence,
        wizard_bounds=wb,
    )
    if not cap.get("ok"):
        return fail("FAIL_STEP_FIRST_NEXT_UNCONFIRMED", cap.get("error", "Capture failed after first Next"), "checkpoint_03_export_type_screen", cap=cap, p6_rect=p6_rect)

    type_blob = collect_text_blob(cap["entries"], min_confidence)
    type_words = find_export_type_evidence_words(type_blob)
    type_ok = export_type_screen_detected(type_words, type_blob) and export_type_screen_visible(cap["entries"], min_confidence)
    cp03_status = "OK" if type_ok else "FAIL_STEP_EXPORT_TYPE_NOT_CONFIRMED"
    m20_diagnostic_save_checkpoint(
        state, evidence, "checkpoint_03_export_type_screen",
        p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap, min_confidence=min_confidence,
        target_clicked=True, target_bbox=next_bbox, click_point=next_pt,
        status_at_checkpoint=cp03_status,
        reason="Export Type screen confirmed" if type_ok else "Export Type screen not confirmed",
        extra={"export_type_words": type_words},
    )
    if not type_ok:
        cap_cancel, p6_rect, _ = m20_diagnostic_cancel_wizard(
            evidence, p6_rect, p6_keyword, config, screen_rule, min_confidence, wizard_bounds=wb
        )
        m20_diagnostic_save_checkpoint(state, evidence, "checkpoint_07_final", p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap_cancel, min_confidence=min_confidence, status_at_checkpoint="ABORT", reason="Cancel after CP03 fail")
        return fail("FAIL_STEP_EXPORT_TYPE_NOT_CONFIRMED", "Export Type screen not confirmed after first Next", "checkpoint_03_export_type_screen", cap=cap, p6_rect=p6_rect)

    type_blob_before_activities = type_blob

    # --- Checkpoint 04: Activities ---
    if state.timed_out():
        t = state.timeout_result("checkpoint_04_activities_selected")
        return fail(t["status"], t["reason"], t["failed_checkpoint"])

    candidates = find_activities_export_type_candidates(cap["entries"], min_confidence)
    act_entry, act_text = find_activities_export_type_entry(cap["entries"], min_confidence)
    act_bbox = entry_bbox_dict(act_entry)
    act_pt = click_point_from_entry(p6_rect, act_entry) if act_entry else None
    act_valid = act_entry is not None and entry_center_in_wizard(act_entry, estimate_wizard_bounds(cap["entries"], min_confidence))

    if act_entry is not None and act_valid:
        state.click_targets.append({"step": "activities", "text": act_text, "bbox": act_bbox, "click_point": act_pt, "candidates": candidates})
        evidence.steps.append(f"M20 diagnostic CP04: OCR-click Activities '{act_text[:50]}'")
        click_ocr_entry(p6_rect, act_entry)
        state.sleep(0.8)
        p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
        act_clicked = True
    else:
        act_clicked = False

    cap, p6_rect, _, _ = m20_diagnostic_capture_once(
        evidence, "diag_cp04", p6_rect, p6_keyword, config, screen_rule, min_confidence,
        wizard_bounds=wb,
    )
    if not cap.get("ok"):
        return fail("FAIL_STEP_ACTIVITIES_CLICK_UNCONFIRMED", cap.get("error", "Capture failed after Activities click"), "checkpoint_04_activities_selected", cap=cap, p6_rect=p6_rect)

    cp04_screen_state = cap.get("screen_state", "unknown")
    if returned_to_activities_workspace(cap, min_confidence):
        state.wizard_closed_unexpectedly = True
        m20_diagnostic_save_checkpoint(
            state, evidence, "checkpoint_04_activities_selected",
            p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap, min_confidence=min_confidence,
            target_clicked=act_clicked, target_bbox=act_bbox, click_point=act_pt,
            status_at_checkpoint="FAIL_WIZARD_CLOSED_UNEXPECTEDLY",
            reason="Returned to Activities workspace after Activities click",
            extra={"activities_candidates": candidates, "activities_option_text": act_text},
        )
        cap_cancel, p6_rect, _ = m20_diagnostic_cancel_wizard(
            evidence, p6_rect, p6_keyword, config, screen_rule, min_confidence, wizard_bounds=wb
        )
        m20_diagnostic_save_checkpoint(state, evidence, "checkpoint_07_final", p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap_cancel, min_confidence=min_confidence, status_at_checkpoint="ABORT", reason="Wizard already closed")
        return fail("FAIL_WIZARD_CLOSED_UNEXPECTEDLY", "P6 returned to Activities workspace after Activities click", "checkpoint_04_activities_selected", cap=cap, p6_rect=p6_rect, extra={"activities_candidates": candidates})

    wizard_still = export_type_screen_visible(cap["entries"], min_confidence) or wizard_chrome_visible(cap["entries"], min_confidence)
    act_confirmed = act_clicked and wizard_still
    cp04_status = "OK" if act_confirmed else "FAIL_STEP_ACTIVITIES_CLICK_UNCONFIRMED"
    m20_diagnostic_save_checkpoint(
        state, evidence, "checkpoint_04_activities_selected",
        p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap, min_confidence=min_confidence,
        target_clicked=act_clicked, target_bbox=act_bbox, click_point=act_pt,
        status_at_checkpoint=cp04_status,
        reason="Activities selected; wizard still open" if act_confirmed else "Activities click unconfirmed or wizard closed",
        extra={"activities_candidates": candidates, "activities_option_text": act_text, "wizard_still_open": wizard_still},
    )
    if not act_confirmed and not (wizard_still and act_entry is not None):
        cap_cancel, p6_rect, _ = m20_diagnostic_cancel_wizard(
            evidence, p6_rect, p6_keyword, config, screen_rule, min_confidence, wizard_bounds=wb
        )
        m20_diagnostic_save_checkpoint(state, evidence, "checkpoint_07_final", p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap_cancel, min_confidence=min_confidence, status_at_checkpoint="ABORT", reason="Cancel after CP04 fail")
        return fail("FAIL_STEP_ACTIVITIES_CLICK_UNCONFIRMED", "Activities selection not confirmed", "checkpoint_04_activities_selected", cap=cap, p6_rect=p6_rect)

    # --- Checkpoint 05: second Next ---
    if state.timed_out():
        t = state.timeout_result("checkpoint_05_after_second_next")
        return fail(t["status"], t["reason"], t["failed_checkpoint"])

    next2, bounds2 = find_wizard_next_button(cap["entries"], min_confidence)
    if next2 is None:
        next2 = find_next_entry(cap["entries"], min_confidence)
    next2_bbox = entry_bbox_dict(next2)
    next2_pt = click_point_from_entry(p6_rect, next2) if next2 else None
    b2 = wb
    next2_valid = next2 is not None and next_in_wizard_bounds(next2, b2)

    if not next2_valid:
        cap_cancel, p6_rect, _ = m20_diagnostic_cancel_wizard(
            evidence, p6_rect, p6_keyword, config, screen_rule, min_confidence, wizard_bounds=wb
        )
        m20_diagnostic_save_checkpoint(state, evidence, "checkpoint_07_final", p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap_cancel, min_confidence=min_confidence, status_at_checkpoint="ABORT", reason="Cancel after CP05 Next not found")
        return fail("FAIL_STEP_SECOND_NEXT_UNCONFIRMED", "Second Next button not found inside wizard bounds", "checkpoint_05_after_second_next", cap=cap, p6_rect=p6_rect)

    state.click_targets.append({"step": "second_next", "bbox": next2_bbox, "click_point": next2_pt})
    save_discovery(
        evidence,
        "second_next_click_evidence.json",
        m20_build_step_evidence(
            entry=next2,
            p6_rect=p6_rect,
            cap=cap,
            pollution_meta={"pollution_detected": False, "pollution_recovered": False, "pollution_words": []},
            p6_keyword=p6_keyword,
            extra={"action": "before_second_next", "next_pressed_count_before": state.next_pressed_count_total},
        ),
    )
    evidence.steps.append("press Next once: OCR-confirmed Next click (after Activities)")
    click_ocr_entry(p6_rect, next2)
    state.next_pressed_count_total = 2
    state.sleep(1.5)
    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

    cap, p6_rect, _, _ = m20_diagnostic_capture_once(
        evidence, "diag_cp05", p6_rect, p6_keyword, config, screen_rule, min_confidence,
        wizard_bounds=wb,
    )
    if not cap.get("ok"):
        return fail("FAIL_STEP_SECOND_NEXT_UNCONFIRMED", cap.get("error", "Capture failed after second Next"), "checkpoint_05_after_second_next", cap=cap, p6_rect=p6_rect)

    if cap.get("ok") and cap.get("entries"):
        redetected = detect_export_wizard_bounds(
            cap["entries"], min_confidence, p6_rect.width, p6_rect.height
        )
        if redetected.get("source") == "ocr_anchors":
            state.cached_wizard_bounds = redetected
            wb = redetected

    m20_diagnostic_save_checkpoint(
        state, evidence, "checkpoint_05_after_second_next",
        p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap, min_confidence=min_confidence,
        target_clicked=True, target_bbox=next2_bbox, click_point=next2_pt,
        status_at_checkpoint="OK", reason="Second Next clicked",
        extra={"ocr_mode": cap.get("ocr_mode", "wizard_crop")},
    )

    # --- Checkpoint 06: post-Activities screen ---
    if state.timed_out():
        t = state.timeout_result("checkpoint_06_post_activities_screen")
        return fail(t["status"], t["reason"], t["failed_checkpoint"])

    cp05_screen_state = cap.get("screen_state", "unknown")
    post_class = classify_post_activities_next_screen(cap["entries"], min_confidence, project_name=project_name)
    final_status = post_class["status"]
    final_reason = post_class["reason"]
    post_words = post_class["evidence_words"]
    save_discovery(
        evidence,
        "post_activities_next_screen_evidence.json",
        {
            "post_activities_screen_type": post_class["post_activities_screen_type"],
            "evidence_words": post_words,
            "raw_ocr_text": post_class["raw_ocr_text"],
            "next_pressed_count_total": state.next_pressed_count_total,
            "finish_pressed": finish_pressed_in_steps(evidence.steps),
            "export_file_created": export_file_created(state.export_snap_before or set(), snapshot_export_files()),
            "wizard_still_open": post_class["wizard_still_open"],
        },
    )
    if final_status == "FAIL_WIZARD_CLOSED_UNEXPECTEDLY":
        state.wizard_closed_unexpectedly = True
        m20_diagnostic_save_checkpoint(
            state, evidence, "checkpoint_06_post_activities_screen",
            p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap, min_confidence=min_confidence,
            status_at_checkpoint=final_status,
            reason=final_reason,
            extra={"post_activities_evidence_words": post_words},
        )
        cap_cancel, p6_rect, _ = m20_diagnostic_cancel_wizard(
            evidence, p6_rect, p6_keyword, config, screen_rule, min_confidence, wizard_bounds=wb
        )
        m20_diagnostic_save_checkpoint(state, evidence, "checkpoint_07_final", p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap_cancel, min_confidence=min_confidence, status_at_checkpoint="ABORT", reason="Wizard already closed")
        return fail(final_status, final_reason, "checkpoint_06_post_activities_screen", cap=cap, p6_rect=p6_rect, extra={"post_activities_evidence_words": post_words})

    m20_diagnostic_save_checkpoint(
        state, evidence, "checkpoint_06_post_activities_screen",
        p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap, min_confidence=min_confidence,
        status_at_checkpoint=final_status,
        reason=final_reason,
        extra={"post_activities_evidence_words": post_words},
    )

    # --- Checkpoint 07: cancel safely ---
    cap_final, p6_rect, state.dialog_closed = m20_diagnostic_cancel_wizard(
        evidence, p6_rect, p6_keyword, config, screen_rule, min_confidence, wizard_bounds=wb
    )
    if not state.dialog_closed and cap_final.get("ok") and wizard_chrome_visible(cap_final.get("entries", []), min_confidence):
        return fail(
            "MANUAL_REVIEW_CANNOT_CONFIRM",
            "Export wizard may still be open after Cancel attempt",
            "checkpoint_07_final",
            cap=cap_final,
            p6_rect=p6_rect,
            extra={"post_activities_evidence_words": post_words},
        )

    m20_diagnostic_save_checkpoint(
        state, evidence, "checkpoint_07_final",
        p6_rect=p6_rect, p6_keyword=p6_keyword, cap=cap_final, min_confidence=min_confidence,
        status_at_checkpoint="OK", reason="Diagnostic complete; wizard cancel attempted",
    )

    persist_artifacts()
    file_created = export_file_created(state.export_snap_before or set(), snapshot_export_files())
    finish_pressed = finish_pressed_in_steps(evidence.steps)

    return {
        "status": final_status,
        "reason": final_reason,
        "failed_checkpoint": state.failed_checkpoint,
        "last_successful_checkpoint": state.last_successful_checkpoint or "checkpoint_06_post_activities_screen",
        "next_pressed_count_total": state.next_pressed_count_total,
        "finish_pressed": finish_pressed,
        "export_file_created": file_created,
        "wizard_closed_unexpectedly": state.wizard_closed_unexpectedly,
        "final_screen_state": cap_final.get("screen_state", "unknown"),
        "diagnostic_trace_file": str(trace_path),
        "all_click_targets_file": str(targets_path),
        "click_targets": state.click_targets,
        "post_activities_evidence_words": post_words,
        "post_activities_screen_detected": final_status in ("PASS_ACTIVITIES_NEXT_DISCOVERY", "PASS_ACTIVITIES_NEXT_DISCOVERY_PARTIAL"),
        "spreadsheet_target_bbox": ss_bbox,
        "first_next_bbox": next_bbox,
        "activities_target_bbox": act_bbox,
        "second_next_bbox": next2_bbox,
        "screen_after_activities_click": cp04_screen_state,
        "screen_after_second_next": cp05_screen_state,
        "activities_option_text": act_text,
        "activities_candidates": candidates,
        "easyocr_prewarmed": state.easyocr_prewarmed,
        "ocr_mode_after_cp01": state.ocr_mode_after_cp01,
        "diagnostic_duration_seconds": round(state.elapsed(), 2),
        "wizard_bounds_cached": state.cached_wizard_bounds,
        "dialog_closed": state.dialog_closed,
    }


# --- M21: Projects-to-export -> post-projects next screen discovery ---

M21_PROJECTS_TO_EXPORT_MARKERS = M20_POST_PROJECTS_TO_EXPORT_WORDS

M21_POST_PROJECTS_TEMPLATE_WORDS = (
    "select template",
    "template",
    "modify template",
    "add",
    "delete",
    "columns",
    "spreadsheet",
    "next",
    "back",
    "cancel",
    "finish",
)

M21_POST_PROJECTS_FILE_PATH_WORDS = (
    "file name",
    "output file",
    "browse",
    "select file",
    "spreadsheet",
    "xlsx",
    "next",
    "back",
    "cancel",
    "finish",
)

M21_MAX_RUN_SEC = 180
M21_MAX_WAIT_SEC = 8
M21_MAX_RESTORE_ATTEMPTS = 3

M21_PROJECTS_VALIDATION_MARKERS = (
    "select one or more projects",
    "one or more projects to export",
    "pleaze gelect",
    "please select one or more",
    "please belect one or more",
    "belect one or more",
    "gelect one or more",
    "projects to export",
)

M21_VALIDATION_POPUP_MIN_CONF = 0.35

_M21_ORIG_CAPTURE_P6: Any = None
_M21_RECT_CLIP_PATCH_ACTIVE = False
_M21_LAST_RECT_CLIP: Dict[str, Any] = {}


def _m21_screen_size() -> Tuple[int, int]:
    try:
        import pyautogui  # noqa: WPS433

        size = pyautogui.size()
        return int(size.width), int(size.height)
    except Exception:  # noqa: BLE001
        return 3840, 2160


def m21_clip_p6_rect_for_capture(p6_rect: P6Rect) -> Tuple[P6Rect, Dict[str, Any], Dict[str, Any]]:
    """Clip negative/off-screen maximized window coords for reliable OCR capture."""
    before = p6_rect.to_dict()
    sw, sh = _m21_screen_size()
    left = max(int(p6_rect.left), 0)
    top = max(int(p6_rect.top), 0)
    right = min(sw, int(p6_rect.left + p6_rect.width))
    bottom = min(sh, int(p6_rect.top + p6_rect.height))
    width = max(right - left, 0)
    height = max(bottom - top, 0)
    if width < 400 or height < 300:
        adj_w = int(p6_rect.width) - max(0, -int(p6_rect.left))
        adj_h = int(p6_rect.height) - max(0, -int(p6_rect.top))
        width = max(width, min(adj_w, sw - left))
        height = max(height, min(adj_h, sh - top))
    width = max(min(width, sw - left), min(400, sw))
    height = max(min(height, sh - top), min(300, sh))
    clipped = P6Rect(left, top, width, height)
    after = clipped.to_dict()
    after["clipped"] = before != after or p6_rect.left < 0 or p6_rect.top < 0
    return clipped, before, after


def m21_install_rect_clip_capture_patch() -> None:
    """Patch P6 capture to clip negative maximized rects; prefer ImageGrab when needed."""
    global _M21_ORIG_CAPTURE_P6, _M21_RECT_CLIP_PATCH_ACTIVE, _M21_LAST_RECT_CLIP
    if _M21_RECT_CLIP_PATCH_ACTIVE:
        return

    import eye.screenshot as ss  # noqa: WPS433

    _M21_ORIG_CAPTURE_P6 = ss.capture_p6_window_only

    def _patched_capture(
        output_folder: Path,
        filename: str,
        p6_rect: P6Rect,
        metadata_path: Optional[Path] = None,
        save_debug_fullscreen_label: Optional[str] = None,
    ) -> Dict[str, Any]:
        global _M21_LAST_RECT_CLIP
        clipped, before, after = m21_clip_p6_rect_for_capture(p6_rect)
        _M21_LAST_RECT_CLIP = {"rect_before_clip": before, "rect_after_clip": after}
        use_original_for_imagegrab = p6_rect.left < 0 or p6_rect.top < 0

        if use_original_for_imagegrab:
            ss._require_pillow()
            output_folder.mkdir(parents=True, exist_ok=True)
            image_path = output_folder / filename
            debug_dir = output_folder / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            ig_debug = debug_dir / f"imagegrab_clip_{Path(filename).stem}.png"
            ok, err = ss._capture_imagegrab_full_crop(p6_rect, image_path, ig_debug)
            if not ok:
                result = _M21_ORIG_CAPTURE_P6(
                    output_folder,
                    filename,
                    clipped,
                    metadata_path=metadata_path,
                    save_debug_fullscreen_label=save_debug_fullscreen_label,
                )
            else:
                metadata = {
                    "image_path": str(image_path),
                    "source": "p6_crop_only",
                    "capture_method": "m21_imagegrab_negative_rect",
                    "p6_rect": p6_rect.to_dict(),
                    "clipped_p6_rect": after,
                    "rect_before_clip": before,
                    "rect_after_clip": after,
                    "width": clipped.width,
                    "height": clipped.height,
                    "used_for_ocr": True,
                    "full_screen_used_for_crop_only": True,
                    "full_screen_ocr_allowed": False,
                    "debug_imagegrab_clip_path": str(ig_debug),
                    "capture_errors": {},
                }
                if metadata_path:
                    metadata_path.parent.mkdir(parents=True, exist_ok=True)
                    with metadata_path.open("w", encoding="utf-8") as handle:
                        json.dump(metadata, handle, indent=2, ensure_ascii=False)
                result = {
                    "success": True,
                    "image_path": str(image_path),
                    "metadata": metadata,
                    "error": None,
                }
        else:
            capture_rect = clipped if after.get("clipped") else p6_rect
            result = _M21_ORIG_CAPTURE_P6(
                output_folder,
                filename,
                capture_rect,
                metadata_path=metadata_path,
                save_debug_fullscreen_label=save_debug_fullscreen_label,
            )
            if result.get("success") and result.get("metadata"):
                result["metadata"]["rect_before_clip"] = before
                result["metadata"]["rect_after_clip"] = after

        return result

    ss.capture_p6_window_only = _patched_capture
    for mod_name in (
        "m03_open_project_by_name",
        "m04_check_project_opened",
        "m06_go_to_activities",
    ):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "capture_p6_window_only"):
            setattr(mod, "capture_p6_window_only", _patched_capture)
    _M21_RECT_CLIP_PATCH_ACTIVE = True


def m21_remove_rect_clip_capture_patch() -> None:
    global _M21_ORIG_CAPTURE_P6, _M21_RECT_CLIP_PATCH_ACTIVE
    if not _M21_RECT_CLIP_PATCH_ACTIVE or _M21_ORIG_CAPTURE_P6 is None:
        return
    import eye.screenshot as ss  # noqa: WPS433

    ss.capture_p6_window_only = _M21_ORIG_CAPTURE_P6
    for mod_name in (
        "m03_open_project_by_name",
        "m04_check_project_opened",
        "m06_go_to_activities",
    ):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "capture_p6_window_only"):
            setattr(mod, "capture_p6_window_only", _M21_ORIG_CAPTURE_P6)
    _M21_RECT_CLIP_PATCH_ACTIVE = False


def m21_collect_low_conf_blob(entries: List[Dict[str, Any]], min_confidence: float) -> str:
    threshold = min(min_confidence, M21_VALIDATION_POPUP_MIN_CONF)
    return collect_text_blob(entries, threshold)


def m21_projects_validation_popup_detected(norm: str) -> bool:
    if not norm:
        return False
    if any(m in norm for m in M21_PROJECTS_VALIDATION_MARKERS[:7]):
        return True
    return "projects to export" in norm and "ok" in norm.split()


def m21_validation_popup_in_entries(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Tuple[bool, str]:
    for entry in entries:
        norm = entry.get("normalized", "")
        if entry.get("confidence", 0) >= M21_VALIDATION_POPUP_MIN_CONF and m21_projects_validation_popup_detected(norm):
            return True, norm
    blob = m21_collect_low_conf_blob(entries, min_confidence)
    norm = normalize_text(blob)
    if m21_projects_validation_popup_detected(norm):
        return True, norm
    return False, norm


def m21_extract_validation_popup_text(blob: str) -> str:
    norm = normalize_text(blob)
    for marker in M21_PROJECTS_VALIDATION_MARKERS:
        if marker in norm:
            return marker
    if "projects to export" in norm:
        return "projects to export validation"
    return ""


def m21_dismiss_projects_validation_popup(
    evidence: ExportWizardEvidence,
    p6_rect: P6Rect,
    p6_keyword: str,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    entries: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[bool, str, P6Rect]:
    """Dismiss projects validation popup via Esc or OCR-confirmed OK inside popup."""
    blob_low = m21_collect_low_conf_blob(entries or [], min_confidence) if entries else ""
    norm = normalize_text(blob_low)
    if entries and not m21_projects_validation_popup_detected(norm):
        validation_hit, _ = m21_validation_popup_in_entries(entries, min_confidence)
        if not validation_hit:
            return False, "not_validation_popup", p6_rect

    ok_entry = None
    ok_threshold = min(min_confidence, M21_VALIDATION_POPUP_MIN_CONF)
    for entry in entries or []:
        if entry.get("confidence", 0) >= ok_threshold and entry.get("normalized") == "ok":
            ok_entry = entry
            break

    if ok_entry is not None:
        evidence.steps.append("M21: OCR-confirmed OK on projects validation popup")
        click_ocr_entry(p6_rect, ok_entry)
        time.sleep(min(M21_MAX_WAIT_SEC, 0.8))
    else:
        evidence.steps.append("M21: Esc once on projects validation popup")
        keyboard_tools.press_escape()
        time.sleep(min(M21_MAX_WAIT_SEC, 0.8))

    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
    after = capture_and_ocr_step(evidence, "validation_popup_dismiss_check", p6_rect, config, screen_rule)
    if after.get("ok"):
        after_norm = normalize_text(collect_text_blob(after["entries"], min_confidence))
        if not m21_projects_validation_popup_detected(after_norm):
            return True, "ok_click" if ok_entry else "esc", p6_rect
    return True, "esc_assumed", p6_rect


def project_001_talison_detected(blob: str, project_name: str) -> bool:
    """True when Projects-to-export OCR shows 001 and Talison/1275 project tokens."""
    norm = normalize_text(blob)
    has_001 = "001" in norm.split() or "001" in norm
    has_talison = "talison" in norm or "1275" in norm
    for token in project_name.replace("-", " ").split():
        tok = normalize_text(token)
        if len(tok) >= 3 and tok in norm:
            has_talison = True
    return bool(has_001 and has_talison)


def classify_projects_to_export_screen(
    entries: List[Dict[str, Any]],
    min_confidence: float,
    *,
    project_name: str = "",
) -> Dict[str, Any]:
    """Classify Projects-to-export wizard screen (after second Next in M21 path)."""
    result = classify_post_activities_next_screen(entries, min_confidence, project_name=project_name)
    blob = result.get("raw_ocr_text") or collect_text_blob(entries, min_confidence)
    projects_words = collect_post_activities_marker_words(blob, M21_PROJECTS_TO_EXPORT_MARKERS)
    project_row = project_001_talison_detected(blob, project_name)
    detected = result.get("post_activities_screen_type") == "projects_to_export" or (
        ("projects to export" in normalize_text(blob) or "open projects" in normalize_text(blob))
        and ("export project" in normalize_text(blob) or "project" in normalize_text(blob))
    )
    return {
        "projects_to_export_screen_detected": detected,
        "project_001_talison_detected": project_row,
        "evidence_words": sorted(set(projects_words + result.get("evidence_words", []))),
        "raw_ocr_text": blob[:4000] if isinstance(blob, str) else collect_text_blob(entries, min_confidence)[:4000],
        "wizard_still_open": result.get("wizard_still_open", False),
        "screen_type": "projects_to_export" if detected else result.get("post_activities_screen_type", "unknown"),
        "status": "OK" if detected else "FAIL_PROJECTS_TO_EXPORT_SCREEN_NOT_FOUND",
        "reason": (
            "Projects-to-export screen confirmed"
            if detected
            else "Projects-to-export screen not confirmed after Activities Next"
        ),
    }


def classify_post_projects_next_screen(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Dict[str, Any]:
    """Classify wizard screen after third Next (from Projects-to-export)."""
    blob = collect_text_blob(entries, min_confidence)
    blob_low = m21_collect_low_conf_blob(entries, min_confidence)
    norm = normalize_text(blob_low)
    has_chrome = wizard_chrome_visible(entries, min_confidence)
    wizard_still_open = (
        export_wizard_open_in_capture(entries, min_confidence)[0]
        or wizard_chrome_visible(entries, min_confidence)
    ) and not wizard_truly_closed(entries, min_confidence)

    validation_hit, validation_norm = m21_validation_popup_in_entries(entries, min_confidence)
    if validation_hit:
        words = sorted(
            set(
                collect_post_activities_marker_words(blob_low, M21_PROJECTS_TO_EXPORT_MARKERS)
                + ["select one or more projects", "ok"]
            )
        )
        return {
            "post_projects_screen_type": "projects_validation_popup",
            "evidence_words": words,
            "raw_ocr_text": blob_low[:4000],
            "post_screen_ok": True,
            "wizard_still_open": True,
            "template_screen_detected": False,
            "status": "PASS_POST_PROJECTS_SCREEN_DISCOVERY",
            "reason": "Projects-to-export validation popup discovered after 3rd Next",
            "validation_popup_detected": True,
            "validation_popup_text": m21_extract_validation_popup_text(blob_low) or validation_norm[:200],
            "manual_review_required": False,
        }

    if wizard_truly_closed(entries, min_confidence):
        return {
            "post_projects_screen_type": "unknown",
            "evidence_words": [],
            "raw_ocr_text": blob[:4000],
            "post_screen_ok": False,
            "wizard_still_open": False,
            "template_screen_detected": False,
            "status": "FAIL_WIZARD_CLOSED_UNEXPECTEDLY",
            "reason": "Wizard chrome disappeared before intentional Cancel",
        }

    template_words = collect_post_activities_marker_words(blob, M21_POST_PROJECTS_TEMPLATE_WORDS)
    file_words = collect_post_activities_marker_words(blob, M21_POST_PROJECTS_FILE_PATH_WORDS)
    projects_words = collect_post_activities_marker_words(blob, M21_PROJECTS_TO_EXPORT_MARKERS)

    template_detected = has_chrome and (
        template_screen_detected(blob)
        or any(m in norm for m in ("select template", "modify template"))
        or ("template" in norm and any(m in norm for m in ("add", "delete", "columns")))
    )
    file_detected = has_chrome and (
        post_template_screen_detected(blob)
        or any(m in norm for m in ("file name", "output file", "browse", "select file", "xlsx"))
    )
    projects_still = has_chrome and (
        ("projects to export" in norm or "open projects" in norm)
        and ("export project" in norm or "001" in norm or "talison" in norm or "1275" in norm)
    )

    if template_detected:
        words = sorted(set(template_words))
        return {
            "post_projects_screen_type": "template",
            "evidence_words": words,
            "raw_ocr_text": blob[:4000],
            "post_screen_ok": True,
            "wizard_still_open": wizard_still_open,
            "template_screen_detected": True,
            "status": "PASS_TEMPLATE_SCREEN_DISCOVERY",
            "reason": "Template screen discovered after Projects-to-export Next",
        }

    if file_detected:
        words = sorted(set(file_words))
        return {
            "post_projects_screen_type": "file_path",
            "evidence_words": words,
            "raw_ocr_text": blob[:4000],
            "post_screen_ok": True,
            "wizard_still_open": wizard_still_open,
            "template_screen_detected": False,
            "status": "PASS_POST_PROJECTS_SCREEN_DISCOVERY",
            "reason": "File/path screen discovered after Projects-to-export Next",
        }

    if projects_still:
        words = sorted(set(projects_words))
        return {
            "post_projects_screen_type": "projects_to_export_still",
            "evidence_words": words,
            "raw_ocr_text": blob[:4000],
            "post_screen_ok": True,
            "wizard_still_open": wizard_still_open,
            "template_screen_detected": False,
            "status": "PASS_POST_PROJECTS_SCREEN_DISCOVERY",
            "reason": "Still on Projects-to-export screen after third Next (known wizard step)",
        }

    chrome_words = collect_post_activities_marker_words(
        blob, ("export", "next", "back", "cancel", "finish", "spreadsheet", "xlsx")
    )
    non_bg = [w for w in chrome_words if w not in ("next", "back", "cancel", "finish", "export")]
    if wizard_still_open and has_chrome and len(non_bg) >= 1:
        return {
            "post_projects_screen_type": "generic_wizard",
            "evidence_words": chrome_words,
            "raw_ocr_text": blob[:4000],
            "post_screen_ok": True,
            "wizard_still_open": True,
            "template_screen_detected": False,
            "status": "PASS_TEMPLATE_SCREEN_DISCOVERY_PARTIAL",
            "reason": f"Partial post-Projects discovery ({len(chrome_words)} evidence words)",
        }

    if wizard_still_open:
        return {
            "post_projects_screen_type": "unknown",
            "evidence_words": chrome_words,
            "raw_ocr_text": blob[:4000],
            "post_screen_ok": False,
            "wizard_still_open": True,
            "template_screen_detected": False,
            "status": "FAIL_POST_PROJECTS_NEXT_SCREEN_NOT_FOUND",
            "reason": "Wizard open but post-Projects next screen not detected",
        }

    return {
        "post_projects_screen_type": "unknown",
        "evidence_words": [],
        "raw_ocr_text": blob[:4000],
        "post_screen_ok": False,
        "wizard_still_open": False,
        "template_screen_detected": False,
        "status": "FAIL_WIZARD_CLOSED_UNEXPECTEDLY",
        "reason": "Export wizard not detected after third Next",
    }


def wizard_bounds_valid(bounds: Optional[Dict[str, float]], p6_width: int, p6_height: int) -> bool:
    if not bounds:
        return False
    x0 = float(bounds.get("x_min", 0))
    x1 = float(bounds.get("x_max", 0))
    y0 = float(bounds.get("y_min", 0))
    y1 = float(bounds.get("y_max", 0))
    if x1 <= x0 or y1 <= y0:
        return False
    if (x1 - x0) < 250 or (y1 - y0) < 200:
        return False
    if x1 > p6_width + 2 or y1 > p6_height + 2:
        return False
    return True


def m21_capture_after_projects_next(
    evidence: ExportWizardEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    *,
    cached_bounds: Optional[Dict[str, float]] = None,
) -> Tuple[Dict[str, Any], P6Rect, Dict[str, Any]]:
    """
    Capture post-3rd-Next screen: full P6 OCR first, redetect bounds, optional wizard crop.
    Never fails solely due to stale/invalid cached bounds.
    """
    meta: Dict[str, Any] = {
        "cached_wizard_bounds": cached_bounds,
        "redetected_wizard_bounds": None,
        "fallback_ocr_used": True,
        "capture_mode": "p6_full",
    }
    window_tools.activate_window_by_title(p6_keyword)
    window_tools.maximize_window_by_title(p6_keyword)
    time.sleep(min(M21_MAX_WAIT_SEC, 0.6))
    prep = prepare_p6_for_test(p6_keyword)
    if prep.get("success") and prep.get("rect"):
        p6_rect = prep["rect"]

    full_cap = capture_and_ocr_step(evidence, "08_after_projects_next", p6_rect, config, screen_rule)
    if not full_cap.get("ok"):
        meta["capture_error"] = full_cap.get("error", "P6 crop capture failed")
        return full_cap, p6_rect, meta

    entries = full_cap.get("entries", [])
    blob = collect_text_blob(entries, min_confidence)
    full_cap["screen_state"] = classify_m20_screen_state(entries, blob, min_confidence)
    full_cap["ocr_mode"] = "p6_full"
    meta["capture_mode"] = "p6_full"
    meta["fallback_ocr_used"] = True

    redetected = detect_export_wizard_bounds(entries, min_confidence, p6_rect.width, p6_rect.height)
    meta["redetected_wizard_bounds"] = redetected

    if wizard_bounds_valid(redetected, p6_rect.width, p6_rect.height):
        crop_cap = ocr_wizard_crop(
            evidence,
            "08_after_projects_next_wiz",
            p6_rect,
            redetected,
            config,
            screen_rule,
            min_confidence,
        )
        if crop_cap.get("ok"):
            crop_blob = collect_text_blob(crop_cap["entries"], min_confidence)
            if len(crop_cap.get("entries", [])) >= 3 and len(crop_blob) > 40:
                cb = crop_blob
                crop_cap["screen_state"] = classify_m20_screen_state(crop_cap["entries"], cb, min_confidence)
                crop_cap["ocr_mode"] = "wizard_crop_redetected"
                meta["fallback_ocr_used"] = False
                meta["capture_mode"] = "wizard_crop_redetected"
                return crop_cap, p6_rect, meta

    evidence.steps.append("M21: post-Projects capture used full P6 OCR (small/invalid bounds or weak crop)")
    return full_cap, p6_rect, meta


def safe_cancel_export_wizard_if_open(
    evidence: ExportWizardEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    *,
    cached_bounds: Optional[Dict[str, float]] = None,
) -> Tuple[Dict[str, Any], P6Rect]:
    """Best-effort export wizard cancel; never press Finish/Yes/No."""
    outcome: Dict[str, Any] = {
        "cleanup_attempted": True,
        "cleanup_success": False,
        "cleanup_method": "",
        "cleanup_reason": "",
    }
    try:
        window_tools.activate_window_by_title(p6_keyword)
        time.sleep(0.4)
        p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

        pre = capture_and_ocr_step(evidence, "cleanup_precheck", p6_rect, config, screen_rule)
        if not pre.get("ok"):
            outcome["cleanup_reason"] = pre.get("error", "cleanup precheck capture failed")
            return outcome, p6_rect

        entries = pre.get("entries", [])
        blob = collect_text_blob(entries, min_confidence)
        wizard_open, _, _ = export_wizard_open_in_capture(entries, min_confidence)
        chrome = wizard_chrome_visible(entries, min_confidence)

        if not wizard_open and not chrome:
            norm = normalize_text(collect_text_blob(entries, min_confidence))
            if m21_projects_validation_popup_detected(norm):
                evidence.steps.append("M21 cleanup: Esc once on projects validation popup")
                keyboard_tools.press_escape()
                time.sleep(min(M21_MAX_WAIT_SEC, 0.8))
                p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
                outcome["cleanup_success"] = True
                outcome["cleanup_method"] = "esc_validation_popup"
                outcome["cleanup_reason"] = "Dismissed projects validation popup with Esc"
                return outcome, p6_rect
            outcome["cleanup_success"] = True
            outcome["cleanup_method"] = "none_wizard_not_open"
            outcome["cleanup_reason"] = "Export wizard not detected; no cleanup needed"
            return outcome, p6_rect

        evidence_words = find_export_evidence_words(blob)
        closed, method, p6_rect = close_export_dialog(
            evidence, p6_keyword, p6_rect, config, screen_rule, entries, evidence_words
        )
        if closed and method not in ("none_dialog_not_open", ""):
            outcome["cleanup_success"] = True
            outcome["cleanup_method"] = method or "cancel_click"
            outcome["cleanup_reason"] = "Export wizard closed via close_export_dialog"
            return outcome, p6_rect

        cancel_entry = find_cancel_entry(entries, min_confidence)
        if cancel_entry is not None:
            evidence.steps.append("M21 cleanup: OCR-confirmed Cancel on export wizard")
            click_ocr_entry(p6_rect, cancel_entry)
            time.sleep(min(M21_MAX_WAIT_SEC, 1.0))
            p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
            after = capture_and_ocr_step(evidence, "cleanup_after_cancel", p6_rect, config, screen_rule)
            if after.get("ok") and not export_wizard_open_in_capture(after["entries"], min_confidence)[0]:
                outcome["cleanup_success"] = True
                outcome["cleanup_method"] = "cancel_click"
                outcome["cleanup_reason"] = "Cancel click closed export wizard"
                return outcome, p6_rect

        if wizard_open or chrome:
            blocking, block_reason = detect_m16_blocking_popup(entries, min_confidence)
            if not blocking:
                evidence.steps.append("M21 cleanup: Esc once on confirmed export wizard")
                keyboard_tools.press_escape()
                time.sleep(min(M21_MAX_WAIT_SEC, 0.8))
                p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
                after_esc = capture_and_ocr_step(evidence, "cleanup_after_esc", p6_rect, config, screen_rule)
                if after_esc.get("ok") and not export_wizard_open_in_capture(after_esc["entries"], min_confidence)[0]:
                    outcome["cleanup_success"] = True
                    outcome["cleanup_method"] = "esc"
                    outcome["cleanup_reason"] = "Esc closed export wizard"
                    return outcome, p6_rect
                outcome["cleanup_reason"] = "Esc did not confirm wizard closed"
            else:
                outcome["cleanup_reason"] = f"Blocking popup during cleanup: {block_reason}"
        else:
            outcome["cleanup_success"] = True
            outcome["cleanup_method"] = "none_wizard_not_open"
            outcome["cleanup_reason"] = "Wizard not confirmed open at cleanup"

    except Exception as exc:  # noqa: BLE001
        outcome["cleanup_reason"] = f"cleanup exception: {exc}"

    return outcome, p6_rect


M03_OPEN_PROJECT_SCREEN_RULE = ROOT / "03_screen_library" / "p6_open_project" / "screen_rule.json"


def m21_ocr_open_project_dialog(
    p6_rect: P6Rect,
    screenshots_dir: Path,
    label: str,
    open_screen_rule: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Tuple[int, int], str]:
    """OCR Open Project dialog via popup crop with low-confidence threshold."""
    from m03_open_project_by_name import popup_crop_offsets  # noqa: WPS433
    from eye.screenshot import crop_center_percent_of_image  # noqa: WPS433

    cap = capture_p6_window_only(
        screenshots_dir,
        f"{label}_p6_crop.png",
        p6_rect,
    )
    if not cap.get("success"):
        return [], (0, 0), cap.get("error", "capture failed")

    popup_path = str(screenshots_dir / f"{label}_popup_crop.png")
    crop_center_percent_of_image(
        cap["image_path"], popup_path, open_screen_rule["crop_region_percent"]
    )
    crop_ox, crop_oy = popup_crop_offsets(open_screen_rule, p6_rect.width, p6_rect.height)
    raw = run_easyocr(popup_path)
    return ocr_to_entries(raw), (crop_ox, crop_oy), ""


def m21_open_dialog_detected(entries: List[Dict[str, Any]], min_confidence: float) -> bool:
    blob = m21_collect_low_conf_blob(entries, min_confidence)
    norm = normalize_text(blob)
    markers = ("open project", "project name", "project id", "portfolio", "select project", "open")
    return sum(1 for m in markers if m in norm) >= 2


def m21_open_project_restore_fallback(
    project_name: str,
    chain_id: str,
    *,
    p6_keyword: str,
    config: Dict[str, Any],
    min_confidence: float,
    evidence_steps: List[str],
) -> Dict[str, Any]:
    """M21-native Open Project when frozen M03 dialog OCR fails (low-conf popup OCR)."""
    from m03_open_project_by_name import (  # noqa: WPS433
        click_entry_on_screen,
        confirm_open_with_alt_o,
        find_project_matches,
        image_point_to_screen,
        open_project_dialog,
        title_indicates_project_open,
        type_filter_project,
    )

    outcome: Dict[str, Any] = {"success": False, "reason": "", "method": "m21_open_fallback"}
    open_rule = load_json(M03_OPEN_PROJECT_SCREEN_RULE)
    tmp = Path(tempfile.gettempdir()) / f"m21_open_{chain_id}"
    screenshots_dir = tmp / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    prep = prepare_p6_for_test(p6_keyword)
    if not prep.get("success") or not prep.get("rect"):
        outcome["reason"] = prep.get("message", "P6 not ready for open fallback")
        return outcome

    p6_rect: P6Rect = prep["rect"]
    title = window_tools.get_window_state(p6_keyword).get("title") or ""
    if title_indicates_project_open(title, project_name):
        outcome["success"] = True
        outcome["reason"] = f"Project already open: {title}"
        outcome["method"] = "title_already_open"
        return outcome

    evidence_steps.append("M21 restore fallback: Ctrl+O Open Project dialog")
    open_project_dialog()
    time.sleep(1.2)
    fresh = get_fresh_p6_rect(p6_keyword)
    if fresh.get("success") and fresh.get("rect"):
        p6_rect = fresh["rect"]

    low_conf = min(min_confidence, M21_VALIDATION_POPUP_MIN_CONF)
    entries, crop_origin, cap_err = m21_ocr_open_project_dialog(
        p6_rect, screenshots_dir, "fb_dialog", open_rule
    )
    if cap_err:
        outcome["reason"] = cap_err
        keyboard_tools.press_escape()
        return outcome

    if not m21_open_dialog_detected(entries, min_confidence):
        evidence_steps.append("M21 restore fallback: dialog not detected — type filter and re-OCR")
        crop = open_rule["crop_region_percent"]
        list_x = p6_rect.width * (float(crop["left"]) + float(crop["right"])) / 2
        list_y = p6_rect.height * (float(crop["top"]) + float(crop["bottom"])) / 2
        sx, sy = image_point_to_screen(p6_rect, list_x, list_y)
        import pyautogui  # noqa: WPS433

        pyautogui.click(sx, sy)
        time.sleep(0.3)
        type_filter_project(project_name)
        time.sleep(0.8)
        entries, crop_origin, cap_err = m21_ocr_open_project_dialog(
            p6_rect, screenshots_dir, "fb_dialog_filter", open_rule
        )

    matches = find_project_matches(entries, project_name, low_conf)
    if not matches:
        keyboard_tools.press_escape()
        outcome["reason"] = "Open Project fallback: project row not found in low-conf OCR"
        return outcome

    selected = max(matches, key=lambda m: m.get("confidence", 0))
    evidence_steps.append(f"M21 restore fallback: click project row '{selected.get('text', '')}'")
    click_entry_on_screen(selected, p6_rect, crop_origin)
    time.sleep(0.5)
    confirm_open_with_alt_o()
    time.sleep(STABILITY_WAIT)

    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
    title = window_tools.get_window_state(p6_keyword).get("title") or ""
    open_ok, _, _ = confirm_project_open(
        entries, project_name, title, low_conf
    )
    if title_indicates_project_open(title, project_name) or open_ok:
        outcome["success"] = True
        outcome["reason"] = f"Project opened via M21 fallback: {title}"
        return outcome

    keyboard_tools.press_escape()
    outcome["reason"] = "Open Project fallback: open action did not confirm in title/OCR"
    return outcome


def m21_run_m03_with_open_dialog_fallback(
    project_name: str,
    chain_id: str,
    *,
    p6_keyword: str,
    evidence_steps: List[str],
) -> Dict[str, Any]:
    """Run frozen M03; on Open Project OCR empty, Esc once and retry once."""
    from m03_open_project_by_name import run_m03  # noqa: WPS433

    run_id = f"{chain_id}_m03"
    m03 = run_m03(project_name, run_id=run_id)
    if m03.get("status") not in ("PASS", "PASS_ALREADY_OPEN") and "OPEN_DIALOG" in m03.get("status", ""):
        evidence_steps.append("M21 restore: M03 Open Project OCR failed — Esc once and retry M03")
        window_tools.activate_window_by_title(p6_keyword)
        window_tools.maximize_window_by_title(p6_keyword)
        keyboard_tools.press_escape()
        time.sleep(1.0)
        prepare_p6_for_test(p6_keyword)
        m03 = run_m03(project_name, run_id=f"{run_id}_retry")
    return m03


def m21_restore_workspace_via_m03_chain(
    project_name: str,
    parent_run_id: str,
    *,
    p6_keyword: str,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    evidence_steps: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run frozen M03/M04/M06 once to restore Talison Activities workspace after dirty start."""
    from m04_check_project_opened import run_m04  # noqa: WPS433
    from m06_go_to_activities import run_m06  # noqa: WPS433

    steps = evidence_steps if evidence_steps is not None else []
    chain_id = f"{parent_run_id}_restore"
    outcome: Dict[str, Any] = {
        "chain_run_id": chain_id,
        "m03_status": "",
        "m04_status": "",
        "m06_status": "",
        "success": False,
        "reason": "",
        "prep_notes": [],
        "rect_before_clip": _M21_LAST_RECT_CLIP.get("rect_before_clip"),
        "rect_after_clip": _M21_LAST_RECT_CLIP.get("rect_after_clip"),
    }
    outcome["prep_notes"] = m20_hard_dismiss_stale_dialogs(p6_keyword, config, screen_rule, min_confidence)
    prep = prepare_p6_for_test(p6_keyword)
    if prep.get("success"):
        window_tools.activate_window_by_title(p6_keyword)
        window_tools.maximize_window_by_title(p6_keyword)
        time.sleep(1.0)
    else:
        outcome["reason"] = f"P6 prepare failed before M03 restore: {prep.get('message', 'unknown')}"
        return outcome

    m21_install_rect_clip_capture_patch()
    try:
        m03 = m21_run_m03_with_open_dialog_fallback(
            project_name, chain_id, p6_keyword=p6_keyword, evidence_steps=steps
        )
        outcome["m03_status"] = m03.get("status", "")
        outcome["rect_before_clip"] = _M21_LAST_RECT_CLIP.get("rect_before_clip")
        outcome["rect_after_clip"] = _M21_LAST_RECT_CLIP.get("rect_after_clip")
        m03_ok = m03.get("status") in ("PASS", "PASS_ALREADY_OPEN")
        if not m03_ok:
            evidence_steps.append("M21 restore: M03 failed — trying M21 Open Project fallback")
            fallback = m21_open_project_restore_fallback(
                project_name,
                chain_id,
                p6_keyword=p6_keyword,
                config=config,
                min_confidence=min_confidence,
                evidence_steps=steps,
            )
            outcome["m03_fallback"] = fallback
            outcome["rect_before_clip"] = _M21_LAST_RECT_CLIP.get("rect_before_clip")
            outcome["rect_after_clip"] = _M21_LAST_RECT_CLIP.get("rect_after_clip")
            if not fallback.get("success"):
                outcome["reason"] = fallback.get("reason") or f"M03 restore failed: {m03.get('reason', m03.get('status'))}"
                return outcome
            outcome["m03_status"] = "PASS_M21_OPEN_FALLBACK"
            m03_ok = True

        if not m03_ok:
            outcome["reason"] = f"M03 restore failed: {m03.get('reason', m03.get('status'))}"
            return outcome

        m04 = run_m04(project_name, run_id=f"{chain_id}_m04")
        outcome["m04_status"] = m04.get("status", "")
        if m04.get("status") != "PASS":
            outcome["reason"] = f"M04 restore failed: {m04.get('reason', m04.get('status'))}"
            return outcome

        m06 = run_m06(project_name, run_id=f"{chain_id}_m06")
        outcome["m06_status"] = m06.get("status", "")
        if m06.get("status") not in ("PASS", "PASS_ALREADY_IN_ACTIVITIES"):
            outcome["reason"] = f"M06 restore failed: {m06.get('reason', m06.get('status'))}"
            return outcome

        outcome["success"] = True
        outcome["reason"] = "M03/M04/M06 workspace restore chain completed"
        return outcome
    finally:
        pass  # caller manages rect-clip patch lifecycle


def m21_preflight_with_restore_loop(
    evidence: ExportWizardEvidence,
    project_name: str,
    p6_keyword: str,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
) -> Tuple[Optional[P6Rect], str, str, Dict[str, Any], Optional[Dict[str, Any]]]:
    """Dirty-start preflight with up to M21_MAX_RESTORE_ATTEMPTS workspace restore attempts."""
    preflight_meta: Dict[str, Any] = {
        "project_restore_attempts": 0,
        "project_restore_success": False,
        "rect_before_clip": None,
        "rect_after_clip": None,
    }
    restore_history: List[Dict[str, Any]] = []
    last_err: Optional[Dict[str, Any]] = None

    m21_install_rect_clip_capture_patch()
    try:
        for attempt in range(1, M21_MAX_RESTORE_ATTEMPTS + 1):
            preflight_meta["project_restore_attempts"] = attempt
            evidence.steps.append(f"M21 preflight restore attempt {attempt}/{M21_MAX_RESTORE_ATTEMPTS}")

            p6_rect, title, state, meta, err = m21_dirty_start_preflight_once(
                evidence, project_name, p6_keyword, config, screen_rule, min_confidence
            )
            preflight_meta.update(meta)
            preflight_meta["rect_before_clip"] = _M21_LAST_RECT_CLIP.get("rect_before_clip")
            preflight_meta["rect_after_clip"] = _M21_LAST_RECT_CLIP.get("rect_after_clip")

            if not err:
                preflight_meta["project_restore_success"] = True
                return p6_rect, title, state, preflight_meta, None

            last_err = err
            if err.get("status") not in ("FAIL_PROJECT_NOT_OPEN", "FAIL_ACTIVITIES_NOT_FOUND"):
                return p6_rect, title, state, preflight_meta, err

            if attempt >= M21_MAX_RESTORE_ATTEMPTS:
                break

            evidence.steps.append(
                f"M21 preflight: {err.get('status')} — restore workspace (attempt {attempt})"
            )
            restore = m21_restore_workspace_via_m03_chain(
                project_name,
                f"{evidence.run_id}_a{attempt}",
                p6_keyword=p6_keyword,
                config=config,
                screen_rule=screen_rule,
                min_confidence=min_confidence,
                evidence_steps=evidence.steps,
            )
            restore_history.append(restore)
            preflight_meta["rect_before_clip"] = restore.get("rect_before_clip") or preflight_meta.get(
                "rect_before_clip"
            )
            preflight_meta["rect_after_clip"] = restore.get("rect_after_clip") or preflight_meta.get("rect_after_clip")
            save_discovery(evidence, "preflight_workspace_restore.json", {
                "attempt": attempt,
                "restore": restore,
                "restore_history": restore_history,
            })

        preflight_meta["project_restore_success"] = False
        preflight_meta["restore_history"] = restore_history
        fail_reason = last_err.get("reason", "Project restore failed") if last_err else "Project restore failed"
        return (
            None,
            window_tools.get_window_state(p6_keyword).get("title") or "",
            "unknown",
            preflight_meta,
            {
                "status": "FAIL_PROJECT_RESTORE_FAILED",
                "reason": fail_reason,
            },
        )
    finally:
        pass  # caller manages rect-clip patch lifecycle


def m21_dirty_start_preflight_once(
    evidence: ExportWizardEvidence,
    project_name: str,
    p6_keyword: str,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
) -> Tuple[Optional[P6Rect], str, str, Dict[str, Any], Optional[Dict[str, Any]]]:
    """Single dirty-start pass: dismiss stale dialogs, then M20 preflight."""
    evidence.steps.append("M21 dirty-start: dismiss stale export/Open Project dialogs")
    dismiss_notes = m20_hard_dismiss_stale_dialogs(p6_keyword, config, screen_rule, min_confidence)
    preflight_meta: Dict[str, Any] = {"dirty_start_dismiss_notes": dismiss_notes}
    p6_rect, title, state, meta, err = m20_preflight_reset_before_export(
        evidence, project_name, p6_keyword, config, screen_rule, min_confidence
    )
    preflight_meta.update(meta)
    return p6_rect, title, state, preflight_meta, err


def m21_dirty_start_preflight(
    evidence: ExportWizardEvidence,
    project_name: str,
    p6_keyword: str,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
) -> Tuple[Optional[P6Rect], str, str, Dict[str, Any], Optional[Dict[str, Any]]]:
    """Dismiss stale dialogs, restore project if needed (single pass — prefer m21_preflight_with_restore_loop)."""
    p6_rect, title, state, meta, err = m21_dirty_start_preflight_once(
        evidence, project_name, p6_keyword, config, screen_rule, min_confidence
    )
    if err and err.get("status") in ("FAIL_PROJECT_NOT_OPEN", "FAIL_ACTIVITIES_NOT_FOUND"):
        evidence.steps.append(
            f"M21 dirty-start: {err.get('status')} — restore workspace via M03/M04/M06 chain"
        )
        restore = m21_restore_workspace_via_m03_chain(
            project_name,
            evidence.run_id,
            p6_keyword=p6_keyword,
            config=config,
            screen_rule=screen_rule,
            min_confidence=min_confidence,
            evidence_steps=evidence.steps,
        )
        meta["workspace_restore"] = restore
        save_discovery(evidence, "preflight_workspace_restore.json", restore)
        if restore.get("success"):
            p6_rect, title, state, meta2, err = m20_preflight_reset_before_export(
                evidence, project_name, p6_keyword, config, screen_rule, min_confidence
            )
            meta.update(meta2)
    return p6_rect, title, state, meta, err


def m21_controlled_wizard_to_post_projects_next(
    evidence: ExportWizardEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    project_name: str = "",
    *,
    force_post_projects_next_screen_not_found_after_third_next: bool = False,
    force_projects_to_export_screen_not_found: bool = False,
    force_projects_export_blocked_after_third_next: bool = False,
) -> Tuple[P6Rect, Dict[str, Any], Optional[Dict[str, Any]]]:
    """M21 controlled path: through Projects-to-export, third Next, classify following screen."""
    p6_rect, ctx, err = m20_controlled_wizard_to_post_activities(
        evidence,
        p6_keyword,
        p6_rect,
        config,
        screen_rule,
        min_confidence,
        project_name,
    )
    if err:
        return p6_rect, ctx, err

    post_entries = ctx.get("post_activities_entries") or ctx.get("export_type_entries", [])
    post_blob = ctx.get("post_activities_blob", "")
    projects_class = classify_projects_to_export_screen(post_entries, min_confidence, project_name=project_name)
    ctx["projects_to_export_screen_detected"] = projects_class["projects_to_export_screen_detected"]
    ctx["project_001_talison_detected"] = projects_class["project_001_talison_detected"]
    ctx["projects_to_export_evidence_words"] = projects_class["evidence_words"]

    save_discovery(
        evidence,
        "projects_to_export_screen_evidence.json",
        m20_build_step_evidence(
            entry=None,
            p6_rect=p6_rect,
            cap={"entries": post_entries, "screen_state": "projects_to_export"},
            pollution_meta={
                "pollution_detected": ctx.get("pollution_detected", False),
                "pollution_recovered": ctx.get("pollution_recovered", False),
                "pollution_words": ctx.get("pollution_words", []),
            },
            p6_keyword=p6_keyword,
            extra={
                "action": "projects_to_export_screen_detected",
                "projects_to_export_screen_detected": projects_class["projects_to_export_screen_detected"],
                "project_001_talison_detected": projects_class["project_001_talison_detected"],
                "evidence_words": projects_class["evidence_words"],
                "next_pressed_count_before_third": count_next_presses(evidence.steps),
            },
        ),
    )

    if force_projects_to_export_screen_not_found:
        evidence.steps.append("Hook: force_projects_to_export_screen_not_found")
        ctx["projects_to_export_screen_detected"] = False
        ctx["project_001_talison_detected"] = False
        ctx["forced_hook_activation"] = {"force_projects_to_export_screen_not_found": True}
        return p6_rect, ctx, {
            "status": "FAIL_PROJECTS_TO_EXPORT_SCREEN_NOT_FOUND",
            "reason": "Hook: force_projects_to_export_screen_not_found",
        }

    if not projects_class["projects_to_export_screen_detected"]:
        return p6_rect, ctx, {
            "status": "FAIL_PROJECTS_TO_EXPORT_SCREEN_NOT_FOUND",
            "reason": projects_class["reason"],
        }

    if not projects_class["project_001_talison_detected"]:
        return p6_rect, ctx, {
            "status": "FAIL_PROJECTS_TO_EXPORT_SCREEN_NOT_FOUND",
            "reason": "Projects-to-export visible but 001/Talison 1275 project row not confirmed in OCR",
        }

    wizard_bounds = ctx.get("wizard_bounds") or detect_export_wizard_bounds(
        post_entries, min_confidence, p6_rect.width, p6_rect.height
    )
    next3, bounds3 = find_wizard_next_button(post_entries, min_confidence)
    if next3 is None:
        return p6_rect, ctx, {
            "status": "MANUAL_REVIEW_CANNOT_CONFIRM",
            "reason": "Third Next button not detected on Projects-to-export screen",
        }
    if not next_in_wizard_bounds(next3, bounds3 or estimate_wizard_bounds(post_entries, min_confidence)):
        return p6_rect, ctx, {
            "status": "MANUAL_REVIEW_CANNOT_CONFIRM",
            "reason": "Third Next button bbox not inside wizard bounds",
        }

    blocking, block_reason = detect_m16_blocking_popup(post_entries, min_confidence)
    if blocking:
        return p6_rect, ctx, {"status": "MANUAL_REVIEW_UNSAFE_POPUP", "reason": block_reason}

    evidence.steps.append("press Next once: OCR-confirmed Next click (from Projects-to-export)")
    click_ocr_entry(p6_rect, next3)
    ctx["third_next_clicked_by_ocr_bbox"] = True
    time.sleep(min(M21_MAX_WAIT_SEC, 1.5))
    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

    cached_bounds = ctx.get("wizard_bounds")
    after_post, p6_rect, capture_meta = m21_capture_after_projects_next(
        evidence,
        p6_keyword,
        p6_rect,
        config,
        screen_rule,
        min_confidence,
        cached_bounds=cached_bounds,
    )
    ctx["post_projects_capture_meta"] = capture_meta
    ctx["fallback_ocr_used"] = capture_meta.get("fallback_ocr_used", True)

    if not after_post.get("ok"):
        post_class = {
            "post_projects_screen_type": "unknown",
            "evidence_words": [],
            "raw_ocr_text": "",
            "post_screen_ok": False,
            "wizard_still_open": True,
            "template_screen_detected": False,
            "status": "FAIL_P6_WINDOW_NOT_READY",
            "reason": capture_meta.get("capture_error", "P6 screenshot/crop could not be obtained after third Next"),
        }
    else:
        post_class = classify_post_projects_next_screen(after_post["entries"], min_confidence)

    if force_post_projects_next_screen_not_found_after_third_next:
        evidence.steps.append("Hook: force_post_projects_next_screen_not_found_after_third_next")
        hook_payload = m21_build_expected_stage_hook_payload(ctx, evidence, post_class, min_confidence)
        hook_payload["forced_condition"] = "post_projects_next_screen_not_found"
        hook_payload["hook_applied_after_third_next"] = True
        hook_payload["original_post_screen_type"] = post_class.get("post_projects_screen_type", "unknown")
        hook_payload["original_evidence_words"] = list(post_class.get("evidence_words", []))
        ctx["forced_hook_activation"] = hook_payload
        save_discovery(evidence, "forced_hook_activation.json", hook_payload)
        post_class = {
            "post_projects_screen_type": "unknown",
            "evidence_words": [],
            "raw_ocr_text": post_class.get("raw_ocr_text", "")[:4000],
            "post_screen_ok": False,
            "wizard_still_open": post_class.get("wizard_still_open", True),
            "template_screen_detected": False,
            "status": "FAIL_POST_PROJECTS_NEXT_SCREEN_NOT_FOUND",
            "reason": "Hook: force_post_projects_next_screen_not_found_after_third_next",
        }

    if force_projects_export_blocked_after_third_next:
        evidence.steps.append("Hook: force_projects_export_blocked_after_third_next")
        hook_payload = m21_build_expected_stage_hook_payload(ctx, evidence, post_class, min_confidence)
        hook_payload["forced_condition"] = "projects_export_blocked_after_third_next"
        ctx["forced_hook_activation"] = hook_payload
        save_discovery(evidence, "forced_hook_activation.json", hook_payload)
        if not hook_payload.get("validation_popup_detected"):
            ctx["projects_to_export_screen_detected"] = False
            ctx["project_001_talison_detected"] = False
            ctx["post_projects_screen_ok"] = False
            return p6_rect, ctx, {
                "status": "FAIL_PROJECTS_TO_EXPORT_SCREEN_NOT_FOUND",
                "reason": "Hook: projects export blocked after third Next (no validation popup)",
            }
        ctx["validation_popup_detected"] = True
        ctx["post_projects_screen_type"] = post_class.get("post_projects_screen_type", "projects_validation_popup")
        ctx["post_projects_screen_ok"] = True

    ctx.update(
        {
            "post_projects_blob": collect_text_blob(after_post.get("entries", []), min_confidence),
            "post_projects_entries": after_post.get("entries", []),
            "post_projects_screen_type": post_class["post_projects_screen_type"],
            "post_projects_evidence_words": post_class["evidence_words"],
            "post_projects_screen_ok": post_class["post_screen_ok"],
            "post_projects_classification_status": post_class["status"],
            "post_projects_classification_reason": post_class["reason"],
            "template_screen_detected": post_class.get("template_screen_detected", False),
            "raw_ocr_text": post_class.get("raw_ocr_text", ""),
            "validation_popup_detected": post_class.get("validation_popup_detected", False),
            "validation_popup_text": post_class.get("validation_popup_text", ""),
        }
    )

    save_discovery(
        evidence,
        "post_projects_next_screen_evidence.json",
        {
            "post_projects_screen_type": post_class["post_projects_screen_type"],
            "evidence_words": post_class["evidence_words"],
            "raw_ocr_text": post_class.get("raw_ocr_text", ""),
            "fallback_ocr_used": ctx.get("fallback_ocr_used", True),
            "cached_wizard_bounds": capture_meta.get("cached_wizard_bounds"),
            "redetected_wizard_bounds": capture_meta.get("redetected_wizard_bounds"),
            "rect_before_clip": capture_meta.get("rect_before_clip") or _M21_LAST_RECT_CLIP.get("rect_before_clip"),
            "rect_after_clip": capture_meta.get("rect_after_clip") or _M21_LAST_RECT_CLIP.get("rect_after_clip"),
            "capture_mode": capture_meta.get("capture_mode", ""),
            "next_pressed_count_total": count_next_presses(evidence.steps),
            "finish_pressed": finish_pressed_in_steps(evidence.steps),
            "export_file_created": False,
            "wizard_still_open": post_class.get("wizard_still_open", False),
            "validation_popup_detected": post_class.get("validation_popup_detected", False),
            "validation_popup_text": post_class.get("validation_popup_text", ""),
            "hook_applied": force_post_projects_next_screen_not_found_after_third_next,
            "classification_status": post_class.get("status", ""),
            "classification_reason": post_class.get("reason", ""),
        },
    )

    return p6_rect, ctx, None


# --- M22: Projects-to-export project row selection -> post-selection next screen ---

M22_MAX_RUN_SEC = 240
M22_MAX_WAIT_SEC = 8
M22_PYAUTOGUI_CORNER_MARGIN = 20

_M22_ORIG_PYAUTO_CLICK: Any = None
_M22_ORIG_PYAUTO_MOVE: Any = None
_M22_PYAUTOGUI_GUARD_ACTIVE = False
_M22_PYAUTOGUI_GUARD_STATE: Dict[str, Any] = {}


class M22FailSafeError(Exception):
    """PyAutoGUI fail-safe triggered during M22 run."""


class M22UnsafeClickError(Exception):
    """Click point rejected by M22 safety validation."""


def m22_screen_size() -> Tuple[int, int]:
    try:
        import pyautogui  # noqa: WPS433

        size = pyautogui.size()
        return int(size.width), int(size.height)
    except Exception:  # noqa: BLE001
        return 3840, 2160


def m22_point_near_screen_corner(sx: int, sy: int, margin: int = M22_PYAUTOGUI_CORNER_MARGIN) -> bool:
    sw, sh = m22_screen_size()
    return sx <= margin or sy <= margin or sx >= sw - margin or sy >= sh - margin


def m22_validate_click_point(
    sx: int,
    sy: int,
    p6_rect: P6Rect,
    wizard_bounds: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Validate screen click is inside P6 and away from screen corners."""
    inside_p6 = (
        p6_rect.left + 4 <= sx <= p6_rect.left + p6_rect.width - 4
        and p6_rect.top + 4 <= sy <= p6_rect.top + p6_rect.height - 4
    )
    near_corner = m22_point_near_screen_corner(sx, sy)
    inside_wizard = True
    if wizard_bounds:
        wx0 = int(p6_rect.left + wizard_bounds.get("x_min", 0))
        wy0 = int(p6_rect.top + wizard_bounds.get("y_min", 0))
        wx1 = int(p6_rect.left + wizard_bounds.get("x_max", p6_rect.width))
        wy1 = int(p6_rect.top + wizard_bounds.get("y_max", p6_rect.height))
        inside_wizard = wx0 <= sx <= wx1 and wy0 <= sy <= wy1
    safe = inside_p6 and not near_corner
    reason = ""
    if near_corner:
        reason = f"click within {M22_PYAUTOGUI_CORNER_MARGIN}px of screen corner"
    elif not inside_p6:
        reason = "click outside P6 window bounds"
    return {
        "safe": safe,
        "inside_p6": inside_p6,
        "inside_wizard": inside_wizard,
        "near_corner": near_corner,
        "click_point": {"x": sx, "y": sy},
        "reason": reason,
    }


def m22_record_click_evidence(
    evidence: Optional[ExportWizardEvidence],
    payload: Dict[str, Any],
    *,
    label: str,
) -> None:
    if evidence is None:
        return
    path = evidence.discovery_dir / "pyautogui_click_safety.json"
    existing: List[Dict[str, Any]] = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            existing = []
    existing.append({"label": label, **payload})
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def m22_safe_pyautogui_click(
    sx: int,
    sy: int,
    p6_rect: P6Rect,
    *,
    wizard_bounds: Optional[Dict[str, float]] = None,
    evidence: Optional[ExportWizardEvidence] = None,
    label: str = "click",
) -> Dict[str, Any]:
    import pyautogui  # noqa: WPS433

    validation = m22_validate_click_point(sx, sy, p6_rect, wizard_bounds)
    m22_record_click_evidence(evidence, validation, label=label)
    if not validation["safe"]:
        return {"ok": False, "validation": validation, "error": validation["reason"]}
    try:
        pyautogui.click(int(sx), int(sy))
        return {"ok": True, "validation": validation, "click_point": {"x": sx, "y": sy}}
    except pyautogui.FailSafeException as exc:
        m22_record_click_evidence(
            evidence,
            {"failsafe": True, "error": str(exc), "click_point": {"x": sx, "y": sy}},
            label=f"{label}_failsafe",
        )
        raise M22FailSafeError(str(exc)) from exc


def m22_safe_pyautogui_move(
    sx: int,
    sy: int,
    p6_rect: P6Rect,
    *,
    evidence: Optional[ExportWizardEvidence] = None,
    label: str = "move",
    duration: float = 0.15,
) -> Dict[str, Any]:
    import pyautogui  # noqa: WPS433

    validation = m22_validate_click_point(sx, sy, p6_rect, None)
    m22_record_click_evidence(evidence, validation, label=label)
    if not validation["safe"]:
        return {"ok": False, "validation": validation, "error": validation["reason"]}
    try:
        pyautogui.moveTo(int(sx), int(sy), duration=max(0.05, duration))
        return {"ok": True, "validation": validation, "click_point": {"x": sx, "y": sy}}
    except pyautogui.FailSafeException as exc:
        m22_record_click_evidence(
            evidence,
            {"failsafe": True, "error": str(exc), "click_point": {"x": sx, "y": sy}},
            label=f"{label}_failsafe",
        )
        raise M22FailSafeError(str(exc)) from exc


def m22_move_mouse_p6_neutral(
    p6_rect: P6Rect,
    *,
    evidence: Optional[ExportWizardEvidence] = None,
) -> Dict[str, Any]:
    """Move mouse to P6 centre (outside wizard chrome) to avoid fail-safe corners."""
    nx = int(p6_rect.left + p6_rect.width * 0.5)
    ny = int(p6_rect.top + p6_rect.height * 0.42)
    result = m22_safe_pyautogui_move(nx, ny, p6_rect, evidence=evidence, label="p6_neutral")
    if not result.get("ok"):
        for frac_y in (0.35, 0.5, 0.55):
            ny = int(p6_rect.top + p6_rect.height * frac_y)
            result = m22_safe_pyautogui_move(nx, ny, p6_rect, evidence=evidence, label="p6_neutral_retry")
            if result.get("ok"):
                break
    return result


def m22_install_pyautogui_guard(
    get_p6_rect: Any,
    get_wizard_bounds: Any,
    evidence: Optional[ExportWizardEvidence] = None,
) -> None:
    """Guard pyautogui click/move during M22 runs; does not disable FAILSAFE."""
    global _M22_ORIG_PYAUTO_CLICK, _M22_ORIG_PYAUTO_MOVE, _M22_PYAUTOGUI_GUARD_ACTIVE
    import pyautogui  # noqa: WPS433

    if _M22_PYAUTOGUI_GUARD_ACTIVE:
        return
    _M22_ORIG_PYAUTO_CLICK = pyautogui.click
    _M22_ORIG_PYAUTO_MOVE = pyautogui.moveTo
    _M22_PYAUTOGUI_GUARD_STATE["evidence"] = evidence

    def guarded_click(x: Any, y: Any, *args: Any, **kwargs: Any) -> Any:
        p6_rect = get_p6_rect()
        if p6_rect is None:
            return _M22_ORIG_PYAUTO_CLICK(x, y, *args, **kwargs)
        sx, sy = int(x), int(y)
        wb = get_wizard_bounds()
        validation = m22_validate_click_point(sx, sy, p6_rect, wb)
        m22_record_click_evidence(evidence, validation, label="guarded_click")
        if not validation["safe"]:
            raise M22UnsafeClickError(validation.get("reason", "unsafe click point"))
        try:
            return _M22_ORIG_PYAUTO_CLICK(x, y, *args, **kwargs)
        except pyautogui.FailSafeException as exc:
            m22_record_click_evidence(
                evidence,
                {"failsafe": True, "error": str(exc), "click_point": {"x": sx, "y": sy}},
                label="guarded_click_failsafe",
            )
            raise M22FailSafeError(str(exc)) from exc

    def guarded_move(x: Any, y: Any, *args: Any, **kwargs: Any) -> Any:
        p6_rect = get_p6_rect()
        if p6_rect is None:
            return _M22_ORIG_PYAUTO_MOVE(x, y, *args, **kwargs)
        sx, sy = int(x), int(y)
        validation = m22_validate_click_point(sx, sy, p6_rect, None)
        m22_record_click_evidence(evidence, validation, label="guarded_move")
        if not validation["safe"]:
            raise M22UnsafeClickError(validation.get("reason", "unsafe move point"))
        try:
            return _M22_ORIG_PYAUTO_MOVE(x, y, *args, **kwargs)
        except pyautogui.FailSafeException as exc:
            raise M22FailSafeError(str(exc)) from exc

    pyautogui.click = guarded_click
    pyautogui.moveTo = guarded_move
    _M22_PYAUTOGUI_GUARD_ACTIVE = True


def m22_remove_pyautogui_guard() -> None:
    global _M22_ORIG_PYAUTO_CLICK, _M22_ORIG_PYAUTO_MOVE, _M22_PYAUTOGUI_GUARD_ACTIVE
    if not _M22_PYAUTOGUI_GUARD_ACTIVE:
        return
    import pyautogui  # noqa: WPS433

    if _M22_ORIG_PYAUTO_CLICK is not None:
        pyautogui.click = _M22_ORIG_PYAUTO_CLICK
    if _M22_ORIG_PYAUTO_MOVE is not None:
        pyautogui.moveTo = _M22_ORIG_PYAUTO_MOVE
    _M22_PYAUTOGUI_GUARD_ACTIVE = False


def ensure_clean_p6_for_m22_hard(project_name: str, run_id: str) -> Dict[str, Any]:
    """M22 hard-test precondition: M21 restore chain + neutral mouse inside P6."""
    result = ensure_clean_p6_for_m21_hard(project_name, run_id)
    if not result.get("ok"):
        return result
    try:
        from hand.p6_prepare import prepare_p6_for_test  # noqa: WPS433

        config = load_json(CONFIG_PATH)
        p6_keyword = config["p6_window_title_keyword"]
        prep = prepare_p6_for_test(p6_keyword)
        if prep.get("success") and prep.get("rect"):
            move = m22_move_mouse_p6_neutral(prep["rect"])
            result["mouse_neutral"] = move
            if not move.get("ok"):
                result["notes"] = list(result.get("notes", []))
                result["notes"].append(f"mouse neutral skipped: {move.get('error', 'unsafe point')}")
    except M22FailSafeError as exc:
        result["pyautogui_failsafe"] = True
        result["notes"] = list(result.get("notes", []))
        result["notes"].append(f"mouse neutral failsafe: {exc}")
    except Exception as exc:  # noqa: BLE001
        result["notes"] = list(result.get("notes", []))
        result["notes"].append(f"mouse neutral error: {exc}")
    return result


# --- M23: Template screen discovery (M22-proven wizard path) ---

M23_MAX_RUN_SEC = 240

M23_TEMPLATE_EVIDENCE_MARKERS = M20_POST_TEMPLATE_SCREEN_WORDS + ("spreadsheet",)

M23_TEMPLATE_UI_LABELS = frozenset(
    {
        "select template",
        "template",
        "modify template",
        "add",
        "delete",
        "columns",
        "next",
        "back",
        "cancel",
        "finish",
        "spreadsheet",
        "browse",
        "ok",
    }
)


def m23_extract_template_screen_evidence(
    entries: List[Dict[str, Any]],
    min_confidence: float,
    blob: Optional[str] = None,
) -> Dict[str, Any]:
    """Read-only template screen evidence from wizard OCR (no clicks)."""
    blob = blob if blob is not None else collect_text_blob(entries, min_confidence)
    norm = normalize_text(blob)
    evidence_words = collect_post_activities_marker_words(blob, M23_TEMPLATE_EVIDENCE_MARKERS)
    exact_labels: set = set()
    threshold = min(min_confidence, 0.45)
    for entry in entries:
        if entry.get("confidence", 0) < threshold:
            continue
        text = (entry.get("normalized") or "").strip()
        if text:
            exact_labels.add(text)

    modify_detected = "modify template" in norm or "modify template" in exact_labels
    add_detected = "add" in exact_labels
    delete_detected = "delete" in exact_labels
    finish_detected = "finish" in exact_labels

    template_names: List[str] = []
    for entry in entries:
        if entry.get("confidence", 0) < threshold:
            continue
        text = (entry.get("text") or entry.get("normalized") or "").strip()
        norm_entry = normalize_text(text)
        if not text or len(norm_entry) < 4:
            continue
        if norm_entry in M23_TEMPLATE_UI_LABELS:
            continue
        if norm_entry.isdigit():
            continue
        if any(skip in norm_entry for skip in ("export", "wizard", "microsoft", "primavera")):
            continue
        if text not in template_names and len(template_names) < 12:
            template_names.append(text)

    template_detected = (
        template_screen_detected(blob)
        or "select template" in norm
        or (modify_detected and ("template" in norm or "columns" in norm))
        or (len(evidence_words) >= 3 and "template" in evidence_words)
    )
    partial = (
        not template_detected
        and wizard_chrome_visible(entries, min_confidence)
        and len(evidence_words) >= 2
    )

    return {
        "template_screen_detected": template_detected,
        "template_screen_partial": partial and not template_detected,
        "template_evidence_words": sorted(set(evidence_words)),
        "template_names_detected": template_names,
        "modify_template_button_detected": modify_detected,
        "add_button_detected": add_detected,
        "delete_button_detected": delete_detected,
        "finish_button_detected": finish_detected,
        "raw_ocr_text": blob[:4000],
        "exact_button_labels": sorted(exact_labels),
    }


def m23_build_template_hook_payload(ctx: Dict[str, Any], evidence: ExportWizardEvidence, tmpl: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "spreadsheet_selected": bool(ctx.get("spreadsheet_selected")),
        "spreadsheet_detected": bool(ctx.get("spreadsheet_detected")),
        "export_type_screen_detected": bool(ctx.get("export_type_screen_ok")),
        "activities_selected": bool(ctx.get("activities_selected")),
        "projects_to_export_screen_detected": bool(ctx.get("projects_to_export_screen_detected")),
        "project_001_talison_detected": bool(ctx.get("project_001_talison_detected")),
        "project_row_detected": bool(ctx.get("project_row_detected")),
        "project_row_selected": bool(ctx.get("project_row_selected")),
        "project_selection_attempted": bool(ctx.get("project_selection_attempted")),
        "next_from_projects_pressed": bool(ctx.get("project_selection_next_clicked")),
        "template_screen_detected": bool(tmpl.get("template_screen_detected")),
        "hook_applied_at_expected_stage": True,
        "finish_pressed": finish_pressed_in_steps(evidence.steps),
        "export_file_created": False,
        "next_pressed_count_total": count_next_presses(evidence.steps),
        "template_evidence_words": list(tmpl.get("template_evidence_words", [])),
    }


def m23_controlled_wizard_to_template_screen(
    evidence: ExportWizardEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    project_name: str = "",
    *,
    force_project_row_not_found: bool = False,
    force_template_screen_not_found: bool = False,
) -> Tuple[P6Rect, Dict[str, Any], Optional[Dict[str, Any]]]:
    """M23: M22 wizard path to Template screen + read-only template evidence."""
    p6_rect, ctx, err = m22_controlled_wizard_to_post_project_selection_next(
        evidence,
        p6_keyword,
        p6_rect,
        config,
        screen_rule,
        min_confidence,
        project_name,
        force_project_row_not_found=force_project_row_not_found,
        force_post_project_selection_screen_not_found=False,
        suppress_post_screen_failure_return=force_template_screen_not_found,
    )
    if err:
        return p6_rect, ctx, err

    entries = ctx.get("post_project_selection_entries") or []
    blob = ctx.get("post_project_selection_blob") or collect_text_blob(entries, min_confidence)
    tmpl = m23_extract_template_screen_evidence(entries, min_confidence, blob)
    ctx["template_evidence"] = tmpl
    ctx["template_evidence_words"] = tmpl["template_evidence_words"]
    ctx["template_names_detected"] = tmpl["template_names_detected"]
    ctx["modify_template_button_detected"] = tmpl["modify_template_button_detected"]
    ctx["add_button_detected"] = tmpl["add_button_detected"]
    ctx["delete_button_detected"] = tmpl["delete_button_detected"]
    ctx["template_screen_detected"] = tmpl["template_screen_detected"]

    save_discovery(
        evidence,
        "template_screen_evidence.json",
        {
            **tmpl,
            "post_project_selection_screen_type": ctx.get("post_project_selection_screen_type", ""),
            "project_row_text": ctx.get("project_row_text", ""),
            "project_row_selected": bool(ctx.get("project_row_selected")),
            "next_pressed_count_total": count_next_presses(evidence.steps),
            "finish_pressed": finish_pressed_in_steps(evidence.steps),
        },
    )

    if force_template_screen_not_found:
        stage_reached = all(
            ctx.get(k)
            for k in (
                "spreadsheet_selected",
                "activities_selected",
                "projects_to_export_screen_detected",
                "project_selection_attempted",
                "project_selection_next_clicked",
            )
        ) and bool(ctx.get("export_type_screen_ok") or ctx.get("project_row_detected"))
        if stage_reached:
            evidence.steps.append("Hook: force_template_screen_not_found after Template screen stage")
            hook_payload = m23_build_template_hook_payload(ctx, evidence, tmpl)
            hook_payload["hook_applied_after_template_screen"] = True
            hook_payload["hook_applied_at_expected_stage"] = True
            hook_payload["original_template_screen_detected"] = bool(tmpl.get("template_screen_detected"))
            hook_payload["forced_condition"] = "template_screen_not_found"
            if not hook_payload.get("project_row_detected"):
                hook_payload["project_row_detected"] = bool(ctx.get("project_row_detected"))
            if not hook_payload.get("next_from_projects_pressed"):
                hook_payload["next_from_projects_pressed"] = bool(ctx.get("project_selection_next_clicked"))
            ctx["forced_hook_activation"] = hook_payload
            save_discovery(evidence, "forced_hook_activation.json", hook_payload)
            return p6_rect, ctx, {
                "status": "FAIL_TEMPLATE_SCREEN_NOT_FOUND",
                "reason": "Hook: force_template_screen_not_found",
            }

    post_type = ctx.get("post_project_selection_screen_type", "unknown")
    if post_type == "projects_validation_popup" or ctx.get("validation_popup_detected_after_project_selection"):
        return p6_rect, ctx, {
            "status": "FAIL_PROJECT_SELECTION_NOT_CONFIRMED",
            "reason": "Validation popup after project selection Next",
        }

    if not tmpl["template_screen_detected"] and not tmpl.get("template_screen_partial"):
        if post_type not in ("template", "generic_wizard"):
            return p6_rect, ctx, {
                "status": "FAIL_TEMPLATE_SCREEN_NOT_FOUND",
                "reason": "Template screen not confirmed after project selection Next",
            }

    return p6_rect, ctx, None


def ensure_clean_p6_for_m23_hard(project_name: str, run_id: str) -> Dict[str, Any]:
    """M23 hard-test precondition: M22 restore chain + neutral mouse inside P6."""
    return ensure_clean_p6_for_m22_hard(project_name, run_id)


M22_PROJECT_ROW_SKIP = frozenset(
    {
        "next",
        "back",
        "cancel",
        "finish",
        "browse",
        "ok",
        "project",
        "projects",
        "export",
        "open",
        "open projects",
        "projects to export",
        "export project",
    }
)


def entry_center_in_wizard_bounds(entry: Dict[str, Any], bounds: Dict[str, float]) -> bool:
    cx, cy = bbox_center(entry)
    return (
        bounds.get("x_min", 0.0) <= cx <= bounds.get("x_max", 99999.0)
        and bounds.get("y_min", 0.0) <= cy <= bounds.get("y_max", 99999.0)
    )


def _m22_row_center_y(entry: Dict[str, Any]) -> float:
    ys = [p[1] for p in entry.get("bbox", [[0, 0]])]
    return sum(ys) / len(ys)


def _m22_is_project_id_001_entry(norm: str) -> bool:
    if not norm:
        return False
    compact = re.sub(r"\s+", "", norm)
    if compact in ("001", "1"):
        return True
    return bool(re.fullmatch(r"0*1{1,3}", compact)) or (
        bool(re.search(r"\b001\b", norm)) and len(norm) <= 8
    )


def score_m22_project_row_entry(norm: str, project_name: str) -> float:
    if not norm:
        return 0.0
    if norm in M22_PROJECT_ROW_SKIP:
        return 0.0
    if any(skip in norm for skip in ("projects to export", "open projects", "export project")):
        return 0.0
    if norm in ("next", "back", "cancel", "finish", "browse"):
        return 0.0
    pn = normalize_text(project_name)
    has_001 = _m22_is_project_id_001_entry(norm)
    has_talison = "talison" in norm or "talizon" in norm or "1275" in norm
    for token in pn.replace("-", " ").split():
        tok = normalize_text(token)
        if len(tok) >= 3 and tok in norm:
            has_talison = True
    score = 0.0
    if has_001 and has_talison:
        score = 32.0
    elif has_talison and len(norm) >= 8:
        score = 22.0
    elif has_001:
        score = 18.0
    if ("talison" in norm or "talizon" in norm) and has_001:
        score += 4.0
    return score


def find_project_row_on_projects_to_export(
    entries: List[Dict[str, Any]],
    project_name: str,
    min_confidence: float,
    wizard_bounds: Dict[str, float],
) -> Tuple[Optional[Dict[str, Any]], str, float, Optional[Dict[str, Any]]]:
    """Return click target (prefer 001 id/checkbox column) paired with project name row."""
    row_threshold = min(min_confidence, 0.45)
    id_entries: List[Dict[str, Any]] = []
    name_candidates: List[Tuple[Dict[str, Any], float, str]] = []

    for entry in entries:
        if entry.get("confidence", 0) < row_threshold:
            continue
        norm = entry.get("normalized", "")
        cy = _m22_row_center_y(entry)
        if cy > wizard_bounds.get("y_max", 950.0) - 70:
            continue
        if cy < wizard_bounds.get("y_min", 0.0) + 50:
            continue
        if not entry_center_in_wizard_bounds(entry, wizard_bounds):
            continue
        if _m22_is_project_id_001_entry(norm):
            id_entries.append(entry)
            continue
        score = score_m22_project_row_entry(norm, project_name)
        if score >= 10.0:
            name_candidates.append((entry, score, entry.get("text", norm)))

    best_click: Optional[Dict[str, Any]] = None
    best_name_entry: Optional[Dict[str, Any]] = None
    best_score = 0.0
    best_text = ""
    y_tol = 20.0

    for id_entry in id_entries:
        id_y = _m22_row_center_y(id_entry)
        for name_entry, name_score, name_text in name_candidates:
            if abs(id_y - _m22_row_center_y(name_entry)) > y_tol:
                continue
            pair_score = 42.0 + name_score
            display = f"001 {name_text}".strip()
            if pair_score > best_score:
                best_click = id_entry
                best_name_entry = name_entry
                best_score = pair_score
                best_text = display

    if best_click is not None:
        return best_click, best_text, best_score, best_name_entry

    for name_entry, name_score, name_text in name_candidates:
        if name_score > best_score:
            best_click = name_entry
            best_name_entry = name_entry
            best_score = name_score
            best_text = name_text

    if best_click is None:
        for entry in entries:
            if entry.get("confidence", 0) < row_threshold:
                continue
            norm = entry.get("normalized", "")
            score = score_m22_project_row_entry(norm, project_name)
            if score <= 0:
                continue
            _, cy = bbox_center(entry)
            if cy > wizard_bounds.get("y_max", 950.0) - 70:
                score *= 0.08
            if cy < wizard_bounds.get("y_min", 0.0) + 50:
                score *= 0.2
            if not entry_center_in_wizard_bounds(entry, wizard_bounds):
                score *= 0.05
            if score > best_score:
                best_click = entry
                best_name_entry = entry
                best_score = score
                best_text = entry.get("text", norm)

    return best_click, best_text, best_score, best_name_entry


def m22_find_export_checkbox_x(
    entries: List[Dict[str, Any]],
    row_entry: Dict[str, Any],
    wizard_bounds: Dict[str, float],
    min_confidence: float,
) -> float:
    """Export-column checkbox x for a project row (left of Project ID)."""
    row_y = _m22_row_center_y(row_entry)
    threshold = min(min_confidence, 0.45)
    best_x: Optional[float] = None
    best_dy = 9999.0
    for entry in entries:
        if entry.get("confidence", 0) < threshold:
            continue
        norm = entry.get("normalized", "")
        if norm != "export":
            continue
        _, cy = bbox_center(entry)
        if cy >= row_y - 8:
            continue
        dy = row_y - cy
        if dy > 120.0:
            continue
        if dy < best_dy:
            xs = [p[0] for p in entry["bbox"]]
            best_x = min(xs) + 12.0
            best_dy = dy
    if best_x is not None:
        return best_x

    xs = [p[0] for p in row_entry["bbox"]]
    id_left = min(xs)
    return max(wizard_bounds.get("x_min", 0.0) + 36.0, id_left - 88.0)


def m22_click_project_row_safe(
    p6_rect: P6Rect,
    entry: Dict[str, Any],
    wizard_bounds: Dict[str, float],
    *,
    name_entry: Optional[Dict[str, Any]] = None,
    list_entries: Optional[List[Dict[str, Any]]] = None,
    min_confidence: float = 0.5,
    strategy: str = "export_checkbox",
    evidence: Optional[ExportWizardEvidence] = None,
) -> Dict[str, Any]:
    """Click Export-column checkbox for confirmed project row inside wizard bounds."""
    from accessibility.hand import keyboard_tools
    from m16_discover_p6_export_menu import click_ocr_entry

    norm = entry.get("normalized", "")
    xs = [p[0] for p in entry["bbox"]]
    ys = [p[1] for p in entry["bbox"]]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)

    if strategy == "name_focus_export" and name_entry is not None:
        click_ocr_entry(p6_rect, name_entry)
        time.sleep(0.35)

    if list_entries:
        click_x = m22_find_export_checkbox_x(list_entries, entry, wizard_bounds, min_confidence)
        click_target = f"export_column_checkbox_{strategy}"
    elif _m22_is_project_id_001_entry(norm):
        click_x = cx
        click_target = "001_checkbox"
    elif name_entry is not None:
        name_xs = [p[0] for p in name_entry["bbox"]]
        click_x = min(name_xs) - 55.0
        click_target = "project_row_left"
    else:
        width = max(xs) - min(xs)
        click_x = min(xs) + min(max(18.0, width * 0.12), width * 0.35)
        click_target = "project_row"

    x_min = wizard_bounds.get("x_min", 0.0) + 12.0
    x_max = wizard_bounds.get("x_max", float(p6_rect.width)) - 12.0
    click_x = max(x_min, min(click_x, x_max))
    click_y = max(
        wizard_bounds.get("y_min", 0.0) + 8.0,
        min(cy, wizard_bounds.get("y_max", 950.0) - 8.0),
    )
    sx = int(p6_rect.left + click_x)
    sy = int(p6_rect.top + click_y)
    click_result = m22_safe_pyautogui_click(
        sx, sy, p6_rect, wizard_bounds=wizard_bounds, evidence=evidence, label="project_row_checkbox"
    )
    if not click_result.get("ok"):
        raise M22UnsafeClickError(click_result.get("error", "unsafe project row click"))
    time.sleep(0.25)
    keyboard_tools.press_key("space")
    time.sleep(0.45)
    return {
        "x": sx,
        "y": sy,
        "row_cx": int(p6_rect.left + cx),
        "row_cy": int(p6_rect.top + cy),
        "click_target": click_target,
        "strategy": strategy,
        "click_validation": click_result.get("validation"),
    }


def classify_post_project_selection_next_screen(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Dict[str, Any]:
    """Classify wizard screen after project row selection + Next (M22 path)."""
    validation_hit, validation_norm = m21_validation_popup_in_entries(entries, min_confidence)
    if validation_hit:
        blob_low = m21_collect_low_conf_blob(entries, min_confidence)
        words = sorted(
            set(
                collect_post_activities_marker_words(blob_low, M21_PROJECTS_TO_EXPORT_MARKERS)
                + ["select one or more projects", "ok"]
            )
        )
        return {
            "post_project_selection_screen_type": "projects_validation_popup",
            "evidence_words": words,
            "raw_ocr_text": blob_low[:4000],
            "post_screen_ok": False,
            "wizard_still_open": True,
            "template_screen_detected": False,
            "status": "FAIL_PROJECT_SELECTION_NOT_CONFIRMED",
            "reason": "Validation popup after project selection Next — project row not selected",
            "validation_popup_detected": True,
            "validation_popup_text": m21_extract_validation_popup_text(blob_low) or validation_norm[:200],
        }

    post = classify_post_projects_next_screen(entries, min_confidence)
    screen_type = post.get("post_projects_screen_type", "unknown")
    status = post.get("status", "")
    if screen_type == "projects_validation_popup":
        return {
            "post_project_selection_screen_type": "projects_validation_popup",
            "evidence_words": post.get("evidence_words", []),
            "raw_ocr_text": post.get("raw_ocr_text", ""),
            "post_screen_ok": False,
            "wizard_still_open": True,
            "template_screen_detected": False,
            "status": "FAIL_PROJECT_SELECTION_NOT_CONFIRMED",
            "reason": "Validation popup after project selection Next",
            "validation_popup_detected": True,
            "validation_popup_text": post.get("validation_popup_text", ""),
        }
    if screen_type == "template":
        return {
            "post_project_selection_screen_type": "template",
            "evidence_words": post.get("evidence_words", []),
            "raw_ocr_text": post.get("raw_ocr_text", ""),
            "post_screen_ok": True,
            "wizard_still_open": post.get("wizard_still_open", True),
            "template_screen_detected": True,
            "status": "PASS_PROJECT_SELECTION_NEXT_DISCOVERY",
            "reason": "Template screen discovered after project selection Next",
            "validation_popup_detected": False,
        }
    if screen_type == "file_path":
        return {
            "post_project_selection_screen_type": "file_path",
            "evidence_words": post.get("evidence_words", []),
            "raw_ocr_text": post.get("raw_ocr_text", ""),
            "post_screen_ok": True,
            "wizard_still_open": post.get("wizard_still_open", True),
            "template_screen_detected": False,
            "status": "PASS_PROJECT_SELECTION_NEXT_DISCOVERY",
            "reason": "File/path screen discovered after project selection Next",
            "validation_popup_detected": False,
        }
    if screen_type == "generic_wizard" and post.get("post_screen_ok"):
        return {
            "post_project_selection_screen_type": "generic_wizard",
            "evidence_words": post.get("evidence_words", []),
            "raw_ocr_text": post.get("raw_ocr_text", ""),
            "post_screen_ok": True,
            "wizard_still_open": post.get("wizard_still_open", True),
            "template_screen_detected": False,
            "status": "PASS_PROJECT_SELECTION_NEXT_DISCOVERY_PARTIAL",
            "reason": "Partial post-project-selection wizard discovery",
            "validation_popup_detected": False,
        }
    if post.get("wizard_still_open") and post.get("post_screen_ok"):
        return {
            "post_project_selection_screen_type": screen_type,
            "evidence_words": post.get("evidence_words", []),
            "raw_ocr_text": post.get("raw_ocr_text", ""),
            "post_screen_ok": True,
            "wizard_still_open": True,
            "template_screen_detected": post.get("template_screen_detected", False),
            "status": "PASS_PROJECT_SELECTION_NEXT_DISCOVERY",
            "reason": post.get("reason", "Post-project-selection screen discovered"),
            "validation_popup_detected": False,
        }
    return {
        "post_project_selection_screen_type": screen_type,
        "evidence_words": post.get("evidence_words", []),
        "raw_ocr_text": post.get("raw_ocr_text", ""),
        "post_screen_ok": False,
        "wizard_still_open": post.get("wizard_still_open", False),
        "template_screen_detected": False,
        "status": "FAIL_POST_PROJECT_SELECTION_SCREEN_NOT_FOUND",
        "reason": post.get("reason", "Post-project-selection next screen not classified"),
        "validation_popup_detected": False,
    }


def m22_build_expected_stage_hook_payload(
    ctx: Dict[str, Any],
    evidence: ExportWizardEvidence,
    post_class: Dict[str, Any],
) -> Dict[str, Any]:
    screen_type = post_class.get("post_project_selection_screen_type") or post_class.get(
        "post_projects_screen_type", ""
    )
    return {
        "spreadsheet_selected": bool(ctx.get("spreadsheet_selected")),
        "spreadsheet_detected": bool(ctx.get("spreadsheet_detected")),
        "export_type_screen_detected": bool(ctx.get("export_type_screen_ok")),
        "activities_selected": bool(ctx.get("activities_selected")),
        "projects_to_export_screen_detected": bool(ctx.get("projects_to_export_screen_detected")),
        "project_001_talison_detected": bool(ctx.get("project_001_talison_detected")),
        "project_row_detected": bool(ctx.get("project_row_detected")),
        "project_row_selected": bool(ctx.get("project_row_selected")),
        "project_selection_attempted": bool(ctx.get("project_selection_attempted")),
        "project_selection_next_clicked": bool(ctx.get("project_selection_next_clicked")),
        "next_from_projects_pressed": bool(ctx.get("project_selection_next_clicked")),
        "hook_applied_at_expected_stage": True,
        "finish_pressed": finish_pressed_in_steps(evidence.steps),
        "export_file_created": False,
        "next_pressed_count_total": count_next_presses(evidence.steps),
        "post_project_selection_screen_type": screen_type,
        "validation_popup_detected": bool(post_class.get("validation_popup_detected")),
    }


def m22_controlled_wizard_to_post_project_selection_next(
    evidence: ExportWizardEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    project_name: str = "",
    *,
    force_skip_project_row_select: bool = False,
    force_project_row_not_found: bool = False,
    force_post_project_selection_screen_not_found: bool = False,
    suppress_post_screen_failure_return: bool = False,
) -> Tuple[P6Rect, Dict[str, Any], Optional[Dict[str, Any]]]:
    """M22: Spreadsheet -> Export Type -> Activities -> Projects-to-export -> select row -> Next."""
    p6_rect, ctx, err = m20_controlled_wizard_to_post_activities(
        evidence,
        p6_keyword,
        p6_rect,
        config,
        screen_rule,
        min_confidence,
        project_name,
    )
    if err:
        return p6_rect, ctx, err

    post_entries = ctx.get("post_activities_entries") or ctx.get("export_type_entries", [])
    projects_class = classify_projects_to_export_screen(post_entries, min_confidence, project_name=project_name)
    ctx["projects_to_export_screen_detected"] = projects_class["projects_to_export_screen_detected"]
    ctx["project_001_talison_detected"] = projects_class["project_001_talison_detected"]
    ctx["projects_to_export_evidence_words"] = projects_class["evidence_words"]

    save_discovery(
        evidence,
        "projects_to_export_screen_evidence.json",
        m20_build_step_evidence(
            entry=None,
            p6_rect=p6_rect,
            cap={"entries": post_entries, "screen_state": "projects_to_export"},
            pollution_meta={
                "pollution_detected": ctx.get("pollution_detected", False),
                "pollution_recovered": ctx.get("pollution_recovered", False),
                "pollution_words": ctx.get("pollution_words", []),
            },
            p6_keyword=p6_keyword,
            extra={
                "projects_to_export_screen_detected": projects_class["projects_to_export_screen_detected"],
                "project_001_talison_detected": projects_class["project_001_talison_detected"],
                "evidence_words": projects_class["evidence_words"],
                "next_pressed_count_before_project_select": count_next_presses(evidence.steps),
            },
        ),
    )

    if not projects_class["projects_to_export_screen_detected"]:
        return p6_rect, ctx, {
            "status": "FAIL_PROJECTS_TO_EXPORT_SCREEN_NOT_FOUND",
            "reason": projects_class["reason"],
        }
    if not projects_class["project_001_talison_detected"]:
        return p6_rect, ctx, {
            "status": "FAIL_PROJECT_ROW_NOT_FOUND",
            "reason": "001/Talison 1275 not visible on Projects-to-export screen",
        }

    wizard_bounds = ctx.get("wizard_bounds") or detect_export_wizard_bounds(
        post_entries, min_confidence, p6_rect.width, p6_rect.height
    )
    ctx["wizard_bounds"] = wizard_bounds

    projects_cap, p6_rect, pol_proj, err_proj = m20_step_capture(
        evidence,
        "07a_projects_to_export_confirm",
        p6_rect,
        p6_keyword,
        config,
        screen_rule,
        min_confidence,
        wizard_bounds=wizard_bounds,
    )
    if err_proj:
        return p6_rect, ctx, err_proj
    row_entries = projects_cap.get("entries", post_entries)

    if force_project_row_not_found:
        evidence.steps.append("Hook: force_project_row_not_found after Projects-to-export screen")
        ctx["project_row_detected"] = False
        ctx["forced_hook_activation"] = m22_build_expected_stage_hook_payload(
            ctx,
            evidence,
            {"post_project_selection_screen_type": "", "validation_popup_detected": False},
        )
        ctx["forced_hook_activation"]["hook_applied_at_expected_stage"] = True
        ctx["forced_hook_activation"]["project_row_detected"] = False
        save_discovery(evidence, "forced_hook_activation.json", ctx["forced_hook_activation"])
        return p6_rect, ctx, {
            "status": "FAIL_PROJECT_ROW_NOT_FOUND",
            "reason": "Hook: force_project_row_not_found after Projects-to-export screen",
        }

    row_entry, row_text, row_score, name_entry = find_project_row_on_projects_to_export(
        row_entries, project_name, min_confidence, wizard_bounds
    )
    if row_entry is None or row_score < 10.0:
        return p6_rect, ctx, {
            "status": "FAIL_PROJECT_ROW_NOT_FOUND",
            "reason": "Could not confirm 001 Talison 1275 project row bbox on Projects-to-export screen",
        }

    ctx["project_row_detected"] = True
    ctx["project_row_text"] = row_text
    save_discovery(
        evidence,
        "project_row_selection_evidence.json",
        m20_build_step_evidence(
            entry=row_entry,
            p6_rect=p6_rect,
            cap={"entries": post_entries, "screen_state": "projects_to_export"},
            pollution_meta={},
            p6_keyword=p6_keyword,
            extra={
                "project_row_text": row_text,
                "project_row_score": row_score,
                "wizard_bounds": wizard_bounds,
                "action": "before_project_row_click",
                "click_target_entry": row_entry.get("text", "") if row_entry else "",
                "paired_name_entry": name_entry.get("text", "") if name_entry else "",
            },
        ),
    )

    blocking, block_reason = detect_m16_blocking_popup(row_entries, min_confidence)
    if blocking:
        return p6_rect, ctx, {"status": "MANUAL_REVIEW_UNSAFE_POPUP", "reason": block_reason}

    strategies = ("export_checkbox", "name_focus_export")
    post_class: Dict[str, Any] = {}
    after_next: Dict[str, Any] = {}
    capture_meta: Dict[str, Any] = {}
    click_pt: Dict[str, Any] = {}
    after_row = projects_cap
    pol_row: Dict[str, Any] = pol_proj

    if force_skip_project_row_select:
        evidence.steps.append("Hook: force_skip_project_row_select — Next without project row select")
    else:
        for attempt_idx, strategy in enumerate(strategies):
            if attempt_idx > 0:
                evidence.steps.append(f"M22: project row select retry strategy={strategy}")
                m21_dismiss_projects_validation_popup(
                    evidence,
                    p6_rect,
                    p6_keyword,
                    config,
                    screen_rule,
                    min_confidence,
                    None,
                )
                recapture, p6_rect, pol_retry, err_retry = m20_step_capture(
                    evidence,
                    f"07a_projects_retry_{attempt_idx}",
                    p6_rect,
                    p6_keyword,
                    config,
                    screen_rule,
                    min_confidence,
                    wizard_bounds=wizard_bounds,
                )
                if err_retry:
                    return p6_rect, ctx, err_retry
                row_entries = recapture.get("entries", row_entries)
                row_entry, row_text, row_score, name_entry = find_project_row_on_projects_to_export(
                    row_entries, project_name, min_confidence, wizard_bounds
                )
                if row_entry is None or row_score < 10.0:
                    break
                pol_row = pol_retry

            evidence.steps.append(
                f"M22: safe project row select '{row_text[:60]}' strategy={strategy}"
            )
            click_pt = m22_click_project_row_safe(
                p6_rect,
                row_entry,
                wizard_bounds,
                name_entry=name_entry,
                list_entries=row_entries,
                min_confidence=min_confidence,
                strategy=strategy,
                evidence=evidence,
            )
            ctx["project_selection_attempted"] = True
            time.sleep(min(M22_MAX_WAIT_SEC, 1.0))
            p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

            after_row, p6_rect, pol_row, err_row = m20_step_capture(
                evidence,
                f"07b_after_project_row_select_{attempt_idx}",
                p6_rect,
                p6_keyword,
                config,
                screen_rule,
                min_confidence,
                wizard_bounds=wizard_bounds,
            )
            if err_row:
                return p6_rect, ctx, err_row

            row_entries_after = after_row.get("entries", row_entries)
            next_entry, bounds = find_wizard_next_button(row_entries_after, min_confidence)
            if next_entry is None:
                next_entry = find_next_entry(row_entries_after, min_confidence)
                if next_entry and not next_in_wizard_bounds(
                    next_entry, bounds or estimate_wizard_bounds(row_entries, min_confidence)
                ):
                    return p6_rect, ctx, {
                        "status": "MANUAL_REVIEW_CANNOT_CONFIRM",
                        "reason": "Next button bbox not inside wizard bounds after project row select",
                    }
            if next_entry is None:
                return p6_rect, ctx, {
                    "status": "MANUAL_REVIEW_CANNOT_CONFIRM",
                    "reason": "Next button not detected on Projects-to-export after project row select",
                }

            blocking2, block_reason2 = detect_m16_blocking_popup(row_entries_after, min_confidence)
            if blocking2:
                return p6_rect, ctx, {"status": "MANUAL_REVIEW_UNSAFE_POPUP", "reason": block_reason2}

            evidence.steps.append(
                "press Next once: OCR-confirmed Next click (from Projects-to-export after project select)"
            )
            click_ocr_entry(p6_rect, next_entry)
            ctx["project_selection_next_clicked"] = True
            time.sleep(min(M22_MAX_WAIT_SEC, 1.5))
            p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

            after_next, p6_rect, capture_meta = m21_capture_after_projects_next(
                evidence,
                p6_keyword,
                p6_rect,
                config,
                screen_rule,
                min_confidence,
                cached_bounds=wizard_bounds,
            )
            if not after_next.get("ok"):
                return p6_rect, ctx, {
                    "status": "FAIL_P6_WINDOW_NOT_READY",
                    "reason": capture_meta.get("capture_error", "capture failed after project selection Next"),
                }

            post_class = classify_post_project_selection_next_screen(after_next["entries"], min_confidence)
            if not post_class.get("validation_popup_detected"):
                break

    if force_skip_project_row_select:
        row_entries_after = row_entries
        next_entry, bounds = find_wizard_next_button(row_entries_after, min_confidence)
        if next_entry is None:
            next_entry = find_next_entry(row_entries_after, min_confidence)
        if next_entry is None:
            return p6_rect, ctx, {
                "status": "MANUAL_REVIEW_CANNOT_CONFIRM",
                "reason": "Next button not detected for skip-project-row hook",
            }
        evidence.steps.append(
            "press Next once: OCR-confirmed Next click (hook skip project row select)"
        )
        click_ocr_entry(p6_rect, next_entry)
        ctx["project_selection_next_clicked"] = True
        time.sleep(min(M22_MAX_WAIT_SEC, 1.5))
        p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
        after_next, p6_rect, capture_meta = m21_capture_after_projects_next(
            evidence,
            p6_keyword,
            p6_rect,
            config,
            screen_rule,
            min_confidence,
            cached_bounds=wizard_bounds,
        )
        if not after_next.get("ok"):
            return p6_rect, ctx, {
                "status": "FAIL_P6_WINDOW_NOT_READY",
                "reason": capture_meta.get("capture_error", "capture failed after hook Next"),
            }
        post_class = classify_post_project_selection_next_screen(after_next["entries"], min_confidence)
        ctx["forced_hook_activation"] = m22_build_expected_stage_hook_payload(ctx, evidence, post_class)
        save_discovery(evidence, "forced_hook_activation.json", ctx["forced_hook_activation"])

    if force_post_project_selection_screen_not_found and not post_class.get("validation_popup_detected"):
        evidence.steps.append("Hook: force_post_project_selection_screen_not_found after project selection Next")
        ctx["forced_hook_activation"] = m22_build_expected_stage_hook_payload(ctx, evidence, post_class)
        ctx["forced_hook_activation"]["hook_applied_after_project_selection_next"] = True
        save_discovery(evidence, "forced_hook_activation.json", ctx["forced_hook_activation"])
        post_class = {
            "post_project_selection_screen_type": "unknown",
            "evidence_words": [],
            "raw_ocr_text": post_class.get("raw_ocr_text", "")[:4000],
            "post_screen_ok": False,
            "wizard_still_open": True,
            "template_screen_detected": False,
            "status": "FAIL_POST_PROJECT_SELECTION_SCREEN_NOT_FOUND",
            "reason": "Hook: force_post_project_selection_screen_not_found",
            "validation_popup_detected": False,
        }

    row_entries_after = after_row.get("entries", row_entries)
    validation_on_row, _ = m21_validation_popup_in_entries(row_entries_after, min_confidence)
    ctx["project_row_selected"] = (
        not validation_on_row
        and not post_class.get("validation_popup_detected")
        and row_score >= 40.0
    )
    save_discovery(
        evidence,
        "project_row_selection_evidence.json",
        {
            **m20_build_step_evidence(
                entry=row_entry,
                p6_rect=p6_rect,
                cap=after_row,
                pollution_meta=pol_row,
                p6_keyword=p6_keyword,
                extra={
                    "project_row_text": row_text,
                    "project_row_score": row_score,
                    "project_row_selected": ctx["project_row_selected"],
                    "project_selection_attempted": True,
                    "click_point": click_pt,
                    "action": "after_project_row_click",
                    "selection_strategies_tried": (
                        []
                        if force_skip_project_row_select
                        else list(strategies[: attempt_idx + 1])
                    ),
                },
            ),
        },
    )

    ctx["post_selection_capture_meta"] = capture_meta
    ctx["fallback_ocr_used"] = capture_meta.get("fallback_ocr_used", True)
    ctx.update(
        {
            "post_project_selection_blob": collect_text_blob(after_next["entries"], min_confidence),
            "post_project_selection_entries": after_next["entries"],
            "post_project_selection_screen_type": post_class["post_project_selection_screen_type"],
            "post_project_selection_evidence_words": post_class["evidence_words"],
            "post_project_selection_screen_ok": post_class["post_screen_ok"],
            "post_project_selection_classification_status": post_class["status"],
            "post_project_selection_classification_reason": post_class["reason"],
            "validation_popup_detected_after_project_selection": post_class.get("validation_popup_detected", False),
            "template_screen_detected": post_class.get("template_screen_detected", False),
        }
    )

    save_discovery(
        evidence,
        "post_project_selection_next_screen_evidence.json",
        {
            "post_project_selection_screen_type": post_class["post_project_selection_screen_type"],
            "evidence_words": post_class["evidence_words"],
            "raw_ocr_text": post_class.get("raw_ocr_text", ""),
            "project_row_text": row_text,
            "project_row_selected": ctx.get("project_row_selected"),
            "project_selection_attempted": ctx.get("project_selection_attempted"),
            "next_pressed_count_total": count_next_presses(evidence.steps),
            "finish_pressed": finish_pressed_in_steps(evidence.steps),
            "validation_popup_detected": post_class.get("validation_popup_detected", False),
            "classification_status": post_class.get("status", ""),
            "classification_reason": post_class.get("reason", ""),
        },
    )

    if post_class["status"] == "FAIL_PROJECT_SELECTION_NOT_CONFIRMED":
        return p6_rect, ctx, {
            "status": "FAIL_PROJECT_SELECTION_NOT_CONFIRMED",
            "reason": post_class.get("reason", "Project selection not confirmed"),
        }
    if post_class["status"] == "FAIL_POST_PROJECT_SELECTION_SCREEN_NOT_FOUND":
        if suppress_post_screen_failure_return:
            return p6_rect, ctx, None
        return p6_rect, ctx, {
            "status": "FAIL_POST_PROJECT_SELECTION_SCREEN_NOT_FOUND",
            "reason": post_class.get("reason", "Post-project-selection screen not found"),
        }

    return p6_rect, ctx, None


def m21_build_expected_stage_hook_payload(
    ctx: Dict[str, Any],
    evidence: ExportWizardEvidence,
    post_class: Dict[str, Any],
    min_confidence: float,
) -> Dict[str, Any]:
    """Record wizard stage evidence when M21 hard-test hook fires after third Next."""
    post_type = post_class.get("post_projects_screen_type", "")
    validation_popup = bool(
        post_class.get("validation_popup_detected")
        or post_type == "projects_validation_popup"
        or ctx.get("validation_popup_detected")
    )
    return {
        "spreadsheet_selected": bool(ctx.get("spreadsheet_selected")),
        "spreadsheet_detected": bool(ctx.get("spreadsheet_detected")),
        "export_type_screen_detected": bool(ctx.get("export_type_screen_ok")),
        "activities_selected": bool(ctx.get("activities_selected")),
        "projects_to_export_screen_detected": bool(ctx.get("projects_to_export_screen_detected")),
        "project_001_talison_detected": bool(ctx.get("project_001_talison_detected")),
        "third_next_pressed": bool(ctx.get("third_next_clicked_by_ocr_bbox")),
        "validation_popup_detected": validation_popup,
        "hook_applied_at_expected_stage": True,
        "finish_pressed": finish_pressed_in_steps(evidence.steps),
        "export_file_created": False,
        "next_pressed_count_total": count_next_presses(evidence.steps),
        "post_projects_screen_type": post_type,
    }


def _m21_hard_verify_clean_state(
    project_name: str,
    p6_keyword: str,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    evidence: ExportWizardEvidence,
) -> Dict[str, Any]:
    """Confirm Talison project open, Activities workspace, no blocking dialog."""
    outcome: Dict[str, Any] = {
        "ok": False,
        "project_open": False,
        "activities_workspace": False,
        "blocking_dialog": False,
        "window_title": "",
        "screen_state": "",
        "reason": "",
        "rect_before_clip": _M21_LAST_RECT_CLIP.get("rect_before_clip"),
        "rect_after_clip": _M21_LAST_RECT_CLIP.get("rect_after_clip"),
    }
    p6_rect, title, state, meta, err = m21_dirty_start_preflight_once(
        evidence, project_name, p6_keyword, config, screen_rule, min_confidence
    )
    outcome["window_title"] = title
    outcome["screen_state"] = state
    outcome["preflight_meta"] = meta
    outcome["rect_before_clip"] = _M21_LAST_RECT_CLIP.get("rect_before_clip")
    outcome["rect_after_clip"] = _M21_LAST_RECT_CLIP.get("rect_after_clip")
    if err:
        outcome["reason"] = err.get("reason", err.get("status", "preflight failed"))
        outcome["preflight_error"] = err
        return outcome
    if p6_rect is None:
        outcome["reason"] = "P6 rect unavailable after preflight"
        return outcome
    outcome["ok"] = True
    outcome["project_open"] = True
    outcome["activities_workspace"] = state == "activities_workspace" or meta.get(
        "activities_workspace_confirmed", True
    )
    outcome["reason"] = "Talison project open; Activities workspace; no blocking dialog"
    return outcome


def ensure_clean_p6_for_m21_hard(
    project_name: str,
    run_id: str,
) -> Dict[str, Any]:
    """
    Deterministic M21 hard-test precondition: foreground P6, dismiss stale dialogs,
    restore Talison + Activities when needed (up to 3 attempts).
    """
    from m04_check_project_opened import run_m04  # noqa: WPS433
    from m06_go_to_activities import run_m06  # noqa: WPS433

    config = load_json(CONFIG_PATH)
    screen_rule = load_json(SCREEN_RULE_PATH)
    p6_keyword = config["p6_window_title_keyword"]
    min_confidence = float(config.get("min_ocr_confidence", 0.5))

    result: Dict[str, Any] = {
        "run_id": run_id,
        "project_name": project_name,
        "ok": False,
        "status": "SETUP_PROJECT_RESTORE_FAILED",
        "reason": "",
        "notes": [],
        "attempts": [],
        "rect_before_clip": None,
        "rect_after_clip": None,
        "window_title": "",
        "screen_state": "",
    }

    tmp = Path(tempfile.gettempdir()) / f"m21_hard_clean_{run_id}"
    for sub in ("screenshots", "ocr", "classification", "popup", "discovery"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)
    evidence = ExportWizardEvidence(
        run_id=run_id,
        folder=tmp,
        module_name="m21_hard_clean",
        screenshots_dir=tmp / "screenshots",
        ocr_dir=tmp / "ocr",
        classification_dir=tmp / "classification",
        popup_dir=tmp / "popup",
        discovery_dir=tmp / "discovery",
        steps=[],
    )

    m21_install_rect_clip_capture_patch()
    try:
        for attempt in range(3):
            attempt_log: Dict[str, Any] = {"attempt": attempt + 1, "methods": []}
            result["notes"].append(f"--- restore attempt {attempt + 1}/3 ---")

            window_tools.activate_window_by_title(p6_keyword)
            window_tools.maximize_window_by_title(p6_keyword)
            time.sleep(0.5)

            dismiss_notes = m20_hard_dismiss_stale_dialogs(p6_keyword, config, screen_rule, min_confidence)
            attempt_log["dismiss_notes"] = dismiss_notes
            result["notes"].extend(dismiss_notes)

            verify = _m21_hard_verify_clean_state(
                project_name, p6_keyword, config, screen_rule, min_confidence, evidence
            )
            attempt_log["verify_after_dismiss"] = verify
            if verify.get("ok"):
                result.update(
                    {
                        "ok": True,
                        "status": "READY",
                        "reason": verify.get("reason", "clean"),
                        "rect_before_clip": verify.get("rect_before_clip"),
                        "rect_after_clip": verify.get("rect_after_clip"),
                        "window_title": verify.get("window_title", ""),
                        "screen_state": verify.get("screen_state", ""),
                    }
                )
                result["attempts"].append(attempt_log)
                return result

            chain_id = f"{run_id}_a{attempt}"
            restore_a = m21_restore_workspace_via_m03_chain(
                project_name,
                chain_id,
                p6_keyword=p6_keyword,
                config=config,
                screen_rule=screen_rule,
                min_confidence=min_confidence,
                evidence_steps=evidence.steps,
            )
            attempt_log["methods"].append({"method": "A_m03_m04_m06", "result": restore_a})
            verify_a = _m21_hard_verify_clean_state(
                project_name, p6_keyword, config, screen_rule, min_confidence, evidence
            )
            if verify_a.get("ok"):
                result.update(
                    {
                        "ok": True,
                        "status": "READY",
                        "reason": "Restored via M03/M04/M06 chain",
                        "rect_before_clip": verify_a.get("rect_before_clip"),
                        "rect_after_clip": verify_a.get("rect_after_clip"),
                        "window_title": verify_a.get("window_title", ""),
                        "screen_state": verify_a.get("screen_state", ""),
                    }
                )
                result["attempts"].append(attempt_log)
                return result

            m03_status = restore_a.get("m03_status", "")
            if not restore_a.get("success") or "OPEN" in m03_status:
                fallback = m21_open_project_restore_fallback(
                    project_name,
                    f"{chain_id}_fb",
                    p6_keyword=p6_keyword,
                    config=config,
                    min_confidence=min_confidence,
                    evidence_steps=evidence.steps,
                )
                attempt_log["methods"].append({"method": "B_open_project_fallback", "result": fallback})
                if fallback.get("success"):
                    m04 = run_m04(project_name, run_id=f"{chain_id}_fb_m04")
                    m06 = run_m06(project_name, run_id=f"{chain_id}_fb_m06")
                    attempt_log["methods"].append(
                        {"method": "B_m04_m06", "m04": m04.get("status"), "m06": m06.get("status")}
                    )
                    verify_b = _m21_hard_verify_clean_state(
                        project_name, p6_keyword, config, screen_rule, min_confidence, evidence
                    )
                    if verify_b.get("ok"):
                        result.update(
                            {
                                "ok": True,
                                "status": "READY",
                                "reason": "Restored via M21 Open Project fallback + M04/M06",
                                "rect_before_clip": verify_b.get("rect_before_clip"),
                                "rect_after_clip": verify_b.get("rect_after_clip"),
                                "window_title": verify_b.get("window_title", ""),
                                "screen_state": verify_b.get("screen_state", ""),
                            }
                        )
                        result["attempts"].append(attempt_log)
                        return result

            result["notes"].append("Attempt C: Esc once, prepare P6, retry M03/M04/M06")
            keyboard_tools.press_escape()
            time.sleep(1.0)
            prepare_p6_for_test(p6_keyword)
            restore_c = m21_restore_workspace_via_m03_chain(
                project_name,
                f"{chain_id}_c",
                p6_keyword=p6_keyword,
                config=config,
                screen_rule=screen_rule,
                min_confidence=min_confidence,
                evidence_steps=evidence.steps,
            )
            attempt_log["methods"].append({"method": "C_esc_retry_chain", "result": restore_c})
            verify_c = _m21_hard_verify_clean_state(
                project_name, p6_keyword, config, screen_rule, min_confidence, evidence
            )
            if verify_c.get("ok"):
                result.update(
                    {
                        "ok": True,
                        "status": "READY",
                        "reason": "Restored after Esc + M03/M04/M06 retry",
                        "rect_before_clip": verify_c.get("rect_before_clip"),
                        "rect_after_clip": verify_c.get("rect_after_clip"),
                        "window_title": verify_c.get("window_title", ""),
                        "screen_state": verify_c.get("screen_state", ""),
                    }
                )
                result["attempts"].append(attempt_log)
                return result

            attempt_log["last_verify"] = verify_c
            result["attempts"].append(attempt_log)
            result["reason"] = verify_c.get("reason") or restore_c.get("reason", "restore failed")
            time.sleep(2.0)

        result["status"] = "SETUP_PROJECT_RESTORE_FAILED"
        if not result["reason"]:
            result["reason"] = "Project restore failed after 3 attempts"
        return result
    finally:
        m21_remove_rect_clip_capture_patch()
