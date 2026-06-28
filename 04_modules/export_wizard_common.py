"""
Shared export wizard helpers for M20+ (does not modify frozen M03-M19).

Read-only imports from frozen modules; new discovery flow helpers live here.
"""

from __future__ import annotations

import re
import shutil
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
from hand.p6_prepare import prepare_p6_for_test
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
        if "activity relationships" in norm or norm == "relationships":
            relationships_y = yc if relationships_y is None else min(relationships_y, yc)
    return export_type_y, relationships_y


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
        for k in ("activity relationships", "relationships", "resources", "expenses", "resource assignments")
    )
    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    best_text = ""
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        norm = entry.get("normalized", "")
        raw = entry.get("text", "")
        if "activity relationships" in norm or ("relationships" in norm and "activit" not in norm):
            continue
        if "resource" in norm or "expense" in norm:
            continue
        if "filter:" in norm or "activity name" in norm or "new activity" in norm or norm == "activity":
            continue
        score = 0.0
        if norm in ("ectivities",):
            score = 28.0
        elif norm in ("activities",):
            score = 24.0
        elif "ectivities" in norm:
            score = 22.0
        elif "activities" in norm:
            score = 18.0
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
        cap = capture_preflight("preflight_04_activities_confirm", p6_rect)
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
        if cap.get("unsafe"):
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


def m20_controlled_wizard_to_post_activities(
    evidence: ExportWizardEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    min_confidence: float,
    project_name: str = "",
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

    open_export_menu(evidence)
    p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
    after_wizard, p6_rect, pol, err = m20_step_capture(
        evidence, "03_after_wizard", p6_rect, p6_keyword, config, screen_rule, min_confidence
    )
    ctx["pollution_detected"] = ctx["pollution_detected"] or pol.get("pollution_detected", False)
    ctx["pollution_recovered"] = ctx["pollution_recovered"] or pol.get("pollution_recovered", False)
    if pol.get("pollution_words"):
        ctx["pollution_words"] = pol["pollution_words"]
    if err:
        return p6_rect, ctx, err

    wizard_blob = collect_text_blob(after_wizard["entries"], min_confidence)
    evidence_words = find_export_evidence_words(wizard_blob)
    wizard_detected = export_dialog_detected(evidence_words) or "export format" in normalize_text(wizard_blob)
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
    x0 = max(0, int(wizard_bounds["x_min"] - margin))
    y0 = max(0, int(wizard_bounds["y_min"] - margin))
    x1 = min(p6_rect.width, int(wizard_bounds["x_max"] + margin))
    y1 = min(p6_rect.height, int(wizard_bounds["y_max"] + margin))
    if x1 <= x0 or y1 <= y0:
        return {"ok": False, "error": "Invalid wizard bounds for crop", "screen_state": "unknown"}

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
        for k in ("activity relationships", "relationships", "resources", "expenses", "resource assignments")
    )
    candidates: List[Dict[str, Any]] = []
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        norm = entry.get("normalized", "")
        if "activity relationships" in norm or ("relationships" in norm and "activit" not in norm):
            continue
        if "resource" in norm or "expense" in norm:
            continue
        if "filter:" in norm or "activity name" in norm or "new activity" in norm:
            continue
        score = 0.0
        if norm in ("ectivities",):
            score = 28.0
        elif norm in ("activities",):
            score = 24.0
        elif "ectivities" in norm:
            score = 22.0
        elif norm in ("activity",):
            score = 12.0
        elif "activities" in norm:
            score = 18.0
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
