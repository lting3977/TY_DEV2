"""
M07 — Open Activity Layout By Name (Phase 6).

Opens a named Activities layout when project is open and Activities workspace is active.
Uses Alt+V, O (View > Layout > Open). P6-window-only OCR.
No import, create, save, delete, export, or schedule edit.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "02_eye"))
sys.path.insert(0, str(ROOT / "02_hand"))
sys.path.insert(0, str(ROOT / "02_accessibility"))
sys.path.insert(0, str(ROOT / "04_modules"))

import importlib.util


def _bootstrap() -> None:
    acc = ROOT / "02_accessibility"
    for name, folder in [
        ("accessibility", acc),
        ("accessibility.eye", acc / "eye"),
        ("accessibility.hand", acc / "hand"),
        ("accessibility.brain", acc / "brain"),
        ("eye", ROOT / "02_eye"),
        ("hand", ROOT / "02_hand"),
    ]:
        init = folder / "__init__.py"
        spec = importlib.util.spec_from_file_location(
            name, init, submodule_search_locations=[str(folder)]
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load {name}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)


_bootstrap()

from accessibility.hand import keyboard_tools, window_tools  # noqa: E402
from eye.ocr import (  # noqa: E402
    collect_text_blob,
    find_keywords,
    is_easyocr_available,
    normalize_text,
)
from eye.screenshot import P6Rect, crop_center_percent_of_image  # noqa: E402
from hand.p6_prepare import get_fresh_p6_rect, prepare_p6_for_test  # noqa: E402
from m06_go_to_activities import (  # noqa: E402
    CONFIG_PATH,
    SCREEN_RULE_PATH,
    STABILITY_WAIT,
    capture_and_ocr_step,
    classify_screen_state,
    confirm_project_open,
    confirms_activities_workspace,
    detect_unsafe_popup,
    load_json,
    navigate_to_activities,
    write_json,
)

MODULE_NAME = "m07_open_activity_layout_by_name"
LAYOUT_DIALOG_RULE_PATH = ROOT / "03_screen_library" / "p6_open_layout" / "screen_rule.json"

OPEN_LAYOUT_DIALOG_HINTS = (
    "open layout",
    "layout",
    "available to",
    "user",
    "project",
    "global",
    "close",
)
LAYOUT_DIALOG_BUTTONS = {"open", "close", "import", "export", "delete"}
FORBIDDEN_LAYOUT_ACTIONS = {"import", "export", "delete", "save", "yes", "no", "overwrite", "remove"}
SEARCH_BOX_HINTS = ("find", "search", "filter")


@dataclass
class RunEvidence:
    run_id: str
    folder: Path
    screenshots_dir: Path
    ocr_dir: Path
    classification_dir: Path
    popup_dir: Path
    steps: List[str] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    ocr_files: List[str] = field(default_factory=list)
    classification_files: List[str] = field(default_factory=list)
    popup_files: List[str] = field(default_factory=list)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_evidence(run_id: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / run_id / MODULE_NAME
    for sub in ("screenshots", "ocr", "classification", "popup"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    return RunEvidence(
        run_id=run_id,
        folder=folder,
        screenshots_dir=folder / "screenshots",
        ocr_dir=folder / "ocr",
        classification_dir=folder / "classification",
        popup_dir=folder / "popup",
    )


def normalize_layout_name(name: str) -> str:
    return re.sub(r"\s+", " ", normalize_text(name)).strip()


def layout_text_matches(ocr_text: str, layout_name: str) -> bool:
    norm_target = normalize_layout_name(layout_name)
    norm_ocr = normalize_layout_name(ocr_text)
    if not norm_target:
        return False
    if norm_ocr == norm_target:
        return True
    if norm_target in norm_ocr:
        extra = norm_ocr.replace(norm_target, "").strip()
        if not extra or len(extra) <= 2:
            return True
    target_words = norm_target.split()
    if len(target_words) >= 2:
        ocr_words = norm_ocr.split()
        if all(w in ocr_words for w in target_words) and len(ocr_words) == len(target_words):
            return True
    return False


def is_partial_layout_match(ocr_text: str, layout_name: str) -> bool:
    norm_target = normalize_layout_name(layout_name)
    norm_ocr = normalize_layout_name(ocr_text)
    if layout_text_matches(ocr_text, layout_name):
        return False
    target_words = norm_target.split()
    if len(target_words) < 2:
        return False
    if norm_ocr in target_words or norm_ocr in norm_target.split():
        return True
    return any(w == norm_ocr for w in target_words)


def confirms_active_layout(entries: List[Dict[str, Any]], layout_name: str, min_confidence: float) -> bool:
    blob = collect_text_blob(entries, min_confidence)
    norm_target = normalize_layout_name(layout_name)
    if f"layout:{norm_target}" in blob.replace(" ", ""):
        return True
    if f"layout: {norm_target}" in blob:
        return True
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        text = entry["normalized"]
        if "layout:" in text and layout_text_matches(text.split("layout:", 1)[-1], layout_name):
            return True
        if layout_text_matches(text, layout_name) and "layout" in blob:
            if "open layout" not in blob:
                return True
    return False


def confirms_open_layout_dialog(entries: List[Dict[str, Any]], min_confidence: float) -> Tuple[bool, List[str]]:
    blob = collect_text_blob(entries, min_confidence)
    if "save layout as" in blob and "new layout" in blob:
        return False, []
    hits = [h for h in OPEN_LAYOUT_DIALOG_HINTS if h in blob]
    has_dialog_buttons = ("import" in blob and "export" in blob) or (
        "available to" in blob and "close" in blob
    )
    ok = has_dialog_buttons and "save layout as" not in blob
    return ok, hits


def detect_unsafe_popup_layout(
    classification: Dict[str, Any],
    entries: List[Dict[str, Any]],
    min_confidence: float,
    *,
    in_layout_dialog: bool = False,
) -> Tuple[bool, str]:
    if in_layout_dialog:
        blob = (classification.get("ocr_blob_excerpt") or "").lower()
        unsafe_phrases = (
            "want to save",
            "do you want to",
            "unsaved changes",
            "commit changes",
        )
        for phrase in unsafe_phrases:
            if phrase in blob:
                return True, f"Unsafe confirmation phrase: {phrase}"
        exact = {
            e["normalized"].strip()
            for e in entries
            if e["confidence"] >= min_confidence and e["normalized"].strip() in FORBIDDEN_LAYOUT_ACTIONS
        }
        if "yes" in exact and "no" in exact:
            return True, "Yes/No confirmation outside expected layout dialog"
        return False, ""

    return detect_unsafe_popup(classification, entries, min_confidence)


def bbox_center(bbox: List[List[float]]) -> Tuple[float, float]:
    xs = [p[0] for p in bbox]
    ys = [p[1] for p in bbox]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def image_point_to_screen(
    p6_rect: P6Rect,
    image_x: float,
    image_y: float,
    crop_origin_x: int = 0,
    crop_origin_y: int = 0,
) -> Tuple[int, int]:
    return (
        int(p6_rect.left + crop_origin_x + image_x),
        int(p6_rect.top + crop_origin_y + image_y),
    )


def click_entry_on_screen(
    entry: Dict[str, Any],
    p6_rect: P6Rect,
    crop_origin: Tuple[int, int],
) -> Tuple[int, int]:
    import pyautogui

    cx, cy = bbox_center(entry["bbox"])
    sx, sy = image_point_to_screen(p6_rect, cx, cy, crop_origin[0], crop_origin[1])
    pyautogui.click(sx, sy)
    return sx, sy


def find_layout_matches(
    entries: List[Dict[str, Any]],
    layout_name: str,
    min_confidence: float,
) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        text = entry.get("text", "")
        if is_partial_layout_match(text, layout_name):
            continue
        if layout_text_matches(text, layout_name):
            matches.append(entry)
    return matches


def find_dialog_open_button(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        if entry["normalized"].strip() == "open":
            candidates.append(entry)
    if not candidates:
        return None
    return max(candidates, key=lambda e: bbox_center(e["bbox"])[1])


def find_search_box(entries: List[Dict[str, Any]], min_confidence: float) -> Optional[Dict[str, Any]]:
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        text = entry["normalized"]
        if any(h in text for h in SEARCH_BOX_HINTS):
            return entry
    return None


def find_layout_bar_entry(entries: List[Dict[str, Any]], min_confidence: float) -> Optional[Dict[str, Any]]:
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        cy = bbox_center(entry["bbox"])[1]
        if cy > 220:
            continue
        text = entry["normalized"]
        if "layout:" in text:
            return entry
    return None


def detect_blocking_confirmation(
    entries: List[Dict[str, Any]], min_confidence: float
) -> Tuple[bool, str]:
    exact = {
        e["normalized"].strip()
        for e in entries
        if e["confidence"] >= min_confidence
    }
    blob = collect_text_blob(entries, min_confidence)
    if confirms_open_layout_dialog(entries, min_confidence)[0]:
        return False, ""
    if "import" in blob and "export" in blob:
        return False, ""
    has_confirm = ("yes" in exact and "no" in exact) or (
        "no" in exact and "cancel" in exact
    )
    if has_confirm and any(p in blob for p in ("save", "layout", "changes", "would")):
        return True, "Blocking Yes/No/Cancel confirmation (likely save layout prompt)"
    return False, ""


def find_open_layout_menu_item(
    entries: List[Dict[str, Any]], min_confidence: float
) -> Optional[Dict[str, Any]]:
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        text = entry["normalized"]
        if "open layout" in text or text.startswith("open lay"):
            return entry
    return None


def layout_submenu_visible(entries: List[Dict[str, Any]], min_confidence: float) -> bool:
    blob = collect_text_blob(entries, min_confidence)
    return ("new layout" in blob and "open layout" in blob) and not confirms_open_layout_dialog(
        entries, min_confidence
    )[0]


def open_layout_dialog_hotkey(evidence: RunEvidence) -> None:
    evidence.steps.append("open_layout_dialog: Alt+V, O (View -> Layout submenu)")
    keyboard_tools.press_escape()
    time.sleep(0.3)
    keyboard_tools.hotkey("alt", "v")
    time.sleep(0.5)
    keyboard_tools.press_key("o")
    time.sleep(0.5)


def capture_dialog_ocr(
    evidence: RunEvidence,
    label: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    layout_dialog_rule: Dict[str, Any],
) -> Dict[str, Any]:
    min_confidence = float(config.get("min_ocr_confidence", 0.5))
    base = capture_and_ocr_step(evidence, label, p6_rect, config, screen_rule)
    if not base.get("ok"):
        return base

    crop_region = layout_dialog_rule.get("crop_region_percent")
    if not crop_region:
        return base

    full_image = evidence.screenshots_dir / f"{label}_p6_crop.png"
    popup_path = str(evidence.screenshots_dir / f"{label}_dialog_crop.png")
    crop_center_percent_of_image(str(full_image), popup_path, crop_region)
    evidence.screenshots.append(popup_path)

    from eye.ocr import check_ocr_pollution, ocr_to_entries, run_easyocr, save_ocr_results

    raw = run_easyocr(popup_path)
    ocr_path = str(evidence.ocr_dir / f"{label}_dialog_ocr.json")
    save_ocr_results(raw, ocr_path)
    evidence.ocr_files.append(ocr_path)

    entries = ocr_to_entries(raw)
    pollution = check_ocr_pollution(entries, config.get("pollution_keywords"), min_confidence)
    if pollution["polluted"]:
        return {
            "ok": False,
            "error": f"OCR pollution: {pollution['pollution_words']}",
            "polluted": True,
        }

    crop_ox = int(p6_rect.width * float(crop_region["left"]))
    crop_oy = int(p6_rect.height * float(crop_region["top"]))
    return {
        **base,
        "full_entries": base.get("entries", []),
        "entries": entries,
        "dialog_ocr_path": ocr_path,
        "crop_origin": (crop_ox, crop_oy),
        "dialog_image": popup_path,
    }


def ensure_activities_workspace(
    evidence: RunEvidence,
    project_name: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    p6_keyword: str,
    min_confidence: float,
    before: Dict[str, Any],
    window_title: str,
) -> Tuple[Optional[Dict[str, Any]], str, P6Rect]:
    before_state = before["screen_state"]
    in_activities, _hits = confirms_activities_workspace(before["entries"], min_confidence)
    if before_state == "activities_workspace" and in_activities:
        return None, before_state, p6_rect

    navigate_to_activities(evidence)
    fresh = get_fresh_p6_rect(p6_keyword)
    if fresh.get("success") and fresh.get("rect"):
        p6_rect = fresh["rect"]

    after_nav = capture_and_ocr_step(evidence, "01b_after_activities_nav", p6_rect, config, screen_rule)
    if not after_nav.get("ok"):
        return after_nav, before_state, p6_rect
    if after_nav.get("unsafe"):
        return after_nav, before_state, p6_rect

    in_activities_after, _ = confirms_activities_workspace(after_nav["entries"], min_confidence)
    if not in_activities_after and after_nav["screen_state"] != "activities_workspace":
        return after_nav, before_state, p6_rect
    return None, after_nav["screen_state"], p6_rect


def finish_result(
    evidence: RunEvidence,
    project_name: str,
    layout_name: str,
    status: str,
    reason: str,
    *,
    window_title_before: str = "",
    window_title_after: str = "",
    before_screen_state: str = "",
    after_screen_state: str = "",
    layout_dialog_detected: bool = False,
    layout_found: bool = False,
    layout_opened: bool = False,
    active_layout_confirmed: bool = False,
    matched_layout_text: str = "",
    manual_review_required: bool = False,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    result = {
        "run_id": evidence.run_id,
        "module": MODULE_NAME,
        "project_name": project_name,
        "layout_name": layout_name,
        "status": status,
        "reason": reason,
        "window_title_before": window_title_before,
        "window_title_after": window_title_after,
        "screenshots": evidence.screenshots,
        "ocr_files": evidence.ocr_files,
        "classification_files": evidence.classification_files,
        "popup_files": evidence.popup_files,
        "before_screen_state": before_screen_state,
        "after_screen_state": after_screen_state,
        "layout_dialog_detected": layout_dialog_detected,
        "layout_found": layout_found,
        "layout_opened": layout_opened,
        "active_layout_confirmed": active_layout_confirmed,
        "matched_layout_text": matched_layout_text,
        "manual_review_required": manual_review_required,
        "error": error,
        "steps": evidence.steps,
    }
    write_json(evidence.folder / "result.json", result)
    write_report(evidence, result)
    return result


def write_report(evidence: RunEvidence, result: Dict[str, Any]) -> None:
    ocr_summary: List[str] = []
    for path in result.get("ocr_files", []):
        try:
            data = load_json(Path(path))
            texts = [e.get("text", "") for e in data.get("entries", [])[:12]]
            ocr_summary.append(f"{path}: {', '.join(texts)}")
        except Exception:  # noqa: BLE001
            ocr_summary.append(path)

    lines = [
        "# M07 Open Activity Layout By Name Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project name: {result.get('project_name', '')}",
        f"- Layout name: {result.get('layout_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Window title before: {result.get('window_title_before', '')}",
        f"- Window title after: {result.get('window_title_after', '')}",
        f"- Before screen state: {result.get('before_screen_state', '')}",
        f"- After screen state: {result.get('after_screen_state', '')}",
        f"- Layout dialog detected: {result.get('layout_dialog_detected')}",
        f"- Layout found: {result.get('layout_found')}",
        f"- Matched layout text: {result.get('matched_layout_text', '')}",
        f"- Layout opened: {result.get('layout_opened')}",
        f"- Active layout confirmed: {result.get('active_layout_confirmed')}",
        "",
        "## Screenshot list",
    ]
    for path in result.get("screenshots", []):
        lines.append(f"- {path}")

    lines.extend(["", "## OCR summary"])
    for item in ocr_summary or ["(none)"]:
        lines.append(f"- {item}")

    lines.extend(["", "## Popup detection summary"])
    for path in result.get("popup_files", []):
        lines.append(f"- {path}")

    lines.extend(
        [
            "",
            "## Final decision",
            result["status"],
            "",
            "## Next recommendation",
        ]
    )
    if result["status"] in ("PASS", "PASS_ALREADY_ACTIVE_LAYOUT"):
        lines.append("Ready for M07 hard testing.")
    elif result["status"] == "FAIL_LAYOUT_NOT_FOUND":
        lines.append(
            f"Layout '{result.get('layout_name')}' is not available in the current P6 layout list."
        )
    elif result["status"].startswith("MANUAL_REVIEW"):
        lines.append("Review screenshots and popup evidence before retrying.")
    else:
        lines.append("Fix the reported issue and re-run TY_TEST_M07_OPEN_LAYOUT.bat")

    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_m07(
    project_name: str,
    layout_name: str,
    *,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    evidence = evidence or build_evidence(run_id or new_run_id())
    config = load_json(CONFIG_PATH)
    screen_rule = load_json(SCREEN_RULE_PATH)
    layout_dialog_rule = (
        load_json(LAYOUT_DIALOG_RULE_PATH)
        if LAYOUT_DIALOG_RULE_PATH.exists()
        else {"crop_region_percent": {"left": 0.15, "top": 0.1, "right": 0.85, "bottom": 0.85}}
    )
    p6_keyword = config["p6_window_title_keyword"]
    min_confidence = float(config.get("min_ocr_confidence", 0.5))

    project_name = (project_name or "").strip()
    layout_name = (layout_name or "").strip()

    if not project_name:
        return finish_result(
            evidence, "", layout_name, "FAIL_PROJECT_NAME_EMPTY", "project_name is empty"
        )
    if not layout_name:
        return finish_result(
            evidence, project_name, "", "FAIL_LAYOUT_NAME_EMPTY", "layout_name is empty"
        )

    evidence.steps.append("validate project_name and layout_name")

    if not is_easyocr_available():
        return finish_result(
            evidence,
            project_name,
            layout_name,
            "ERROR",
            "EasyOCR not installed",
            error="pip install easyocr",
        )

    try:
        for _ in range(3):
            try:
                keyboard_tools.press_escape()
                time.sleep(0.3)
            except Exception:  # noqa: BLE001
                pass

        evidence.steps.append("prepare_p6_for_test")
        prep = prepare_p6_for_test(p6_keyword)
        if not prep.get("success") or not prep.get("rect"):
            return finish_result(
                evidence,
                project_name,
                layout_name,
                "FAIL_P6_WINDOW_NOT_READY",
                prep.get("message", "P6 window not ready"),
            )

        p6_rect: P6Rect = prep["rect"]
        window_title_before = window_tools.get_window_state(p6_keyword).get("title") or ""

        evidence.steps.append("capture before_action")
        before = capture_and_ocr_step(evidence, "01_before", p6_rect, config, screen_rule)
        if not before.get("ok"):
            polluted = before.get("polluted")
            return finish_result(
                evidence,
                project_name,
                layout_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_P6_WINDOW_NOT_READY",
                before.get("error", "before capture failed"),
                window_title_before=window_title_before,
                before_screen_state="unknown",
                manual_review_required=bool(polluted),
            )

        before_state = before["screen_state"]
        if before.get("unsafe"):
            return finish_result(
                evidence,
                project_name,
                layout_name,
                "MANUAL_REVIEW_UNSAFE_POPUP",
                before.get("unsafe_reason", "unsafe popup"),
                window_title_before=window_title_before,
                before_screen_state=before_state,
                manual_review_required=True,
            )

        open_ok, open_reason, _open_words = confirm_project_open(
            before["entries"], project_name, window_title_before, min_confidence
        )
        if not open_ok:
            return finish_result(
                evidence,
                project_name,
                layout_name,
                "FAIL_PROJECT_NOT_OPEN",
                open_reason,
                window_title_before=window_title_before,
                before_screen_state=before_state,
            )

        nav_issue, activities_state, p6_rect = ensure_activities_workspace(
            evidence,
            project_name,
            p6_rect,
            config,
            screen_rule,
            p6_keyword,
            min_confidence,
            before,
            window_title_before,
        )
        if nav_issue is not None:
            if nav_issue.get("unsafe"):
                return finish_result(
                    evidence,
                    project_name,
                    layout_name,
                    "MANUAL_REVIEW_UNSAFE_POPUP",
                    nav_issue.get("unsafe_reason", "unsafe popup during activities navigation"),
                    window_title_before=window_title_before,
                    before_screen_state=before_state,
                    manual_review_required=True,
                )
            if not nav_issue.get("ok"):
                polluted = nav_issue.get("polluted")
                return finish_result(
                    evidence,
                    project_name,
                    layout_name,
                    "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_ACTIVITIES_NOT_FOUND",
                    nav_issue.get("error", "Activities navigation failed"),
                    window_title_before=window_title_before,
                    before_screen_state=before_state,
                    manual_review_required=bool(polluted),
                )
            return finish_result(
                evidence,
                project_name,
                layout_name,
                "FAIL_ACTIVITIES_NOT_FOUND",
                "Activities workspace could not be confirmed",
                window_title_before=window_title_before,
                before_screen_state=before_state,
            )

        before_state = activities_state
        working_entries = before["entries"]
        if any("01b_after_activities_nav" in p for p in evidence.ocr_files):
            last_ocr = load_json(Path(evidence.ocr_files[-1]))
            from eye.ocr import ocr_to_entries as _ocr_entries

            working_entries = last_ocr.get("entries") or _ocr_entries([])

        if confirms_active_layout(working_entries, layout_name, min_confidence):
            return finish_result(
                evidence,
                project_name,
                layout_name,
                "PASS_ALREADY_ACTIVE_LAYOUT",
                f"Layout '{layout_name}' is already active",
                window_title_before=window_title_before,
                window_title_after=window_title_before,
                before_screen_state=before_state,
                after_screen_state=before_state,
                active_layout_confirmed=True,
                matched_layout_text=layout_name,
            )

        open_layout_dialog_hotkey(evidence)
        fresh = get_fresh_p6_rect(p6_keyword)
        if fresh.get("success") and fresh.get("rect"):
            p6_rect = fresh["rect"]

        evidence.steps.append("capture layout_dialog")
        dialog = capture_dialog_ocr(
            evidence, "02_layout_dialog", p6_rect, config, screen_rule, layout_dialog_rule
        )

        full_entries = dialog.get("full_entries") or dialog.get("entries", [])
        dialog_entries = dialog.get("entries", [])
        crop_origin = dialog.get("crop_origin", (0, 0))

        if not dialog.get("ok"):
            polluted = dialog.get("polluted")
            return finish_result(
                evidence,
                project_name,
                layout_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_LAYOUT_DIALOG_NOT_FOUND",
                dialog.get("error", "layout dialog capture failed"),
                window_title_before=window_title_before,
                before_screen_state=before_state,
                manual_review_required=bool(polluted),
            )

        dialog_detected, dialog_hits = confirms_open_layout_dialog(dialog_entries, min_confidence)
        if not dialog_detected:
            dialog_detected, dialog_hits = confirms_open_layout_dialog(full_entries, min_confidence)
            if dialog_detected:
                dialog_entries = full_entries
                crop_origin = (0, 0)

        dialog_class = classify_screen_state(
            dialog_entries if dialog_detected else full_entries, screen_rule, config, min_confidence
        )
        unsafe_dialog, unsafe_reason = detect_unsafe_popup_layout(
            dialog_class,
            dialog_entries if dialog_detected else full_entries,
            min_confidence,
            in_layout_dialog=dialog_detected,
        )
        if unsafe_dialog:
            return finish_result(
                evidence,
                project_name,
                layout_name,
                "MANUAL_REVIEW_UNSAFE_POPUP",
                unsafe_reason,
                window_title_before=window_title_before,
                before_screen_state=before_state,
                layout_dialog_detected=dialog_detected,
                manual_review_required=True,
            )

        if not dialog_detected:
            if layout_submenu_visible(full_entries, min_confidence):
                menu_item = find_open_layout_menu_item(full_entries, min_confidence)
                if menu_item:
                    evidence.steps.append("click Open Layout menu item (OCR bbox)")
                    click_entry_on_screen(menu_item, p6_rect, (0, 0))
                    time.sleep(STABILITY_WAIT)
                    fresh = get_fresh_p6_rect(p6_keyword)
                    if fresh.get("success") and fresh.get("rect"):
                        p6_rect = fresh["rect"]
                    dialog = capture_dialog_ocr(
                        evidence, "02a_layout_menu_click", p6_rect, config, screen_rule, layout_dialog_rule
                    )
                    full_entries = dialog.get("full_entries") or dialog.get("entries", [])
                    dialog_entries = dialog.get("entries", [])
                    crop_origin = dialog.get("crop_origin", (0, 0))
                    blocked, block_reason = detect_blocking_confirmation(full_entries, min_confidence)
                    if blocked:
                        return finish_result(
                            evidence,
                            project_name,
                            layout_name,
                            "MANUAL_REVIEW_UNSAFE_POPUP",
                            block_reason,
                            window_title_before=window_title_before,
                            before_screen_state=before_state,
                            manual_review_required=True,
                        )
                    dialog_detected, dialog_hits = confirms_open_layout_dialog(
                        dialog_entries, min_confidence
                    )
                    if not dialog_detected:
                        dialog_detected, dialog_hits = confirms_open_layout_dialog(
                            full_entries, min_confidence
                        )
                        if dialog_detected:
                            dialog_entries = full_entries
                            crop_origin = (0, 0)

        if not dialog_detected:
            evidence.steps.append("fallback: Layout Options bar click")
            fresh_before = capture_and_ocr_step(
                evidence, "02d_layout_bar", p6_rect, config, screen_rule
            )
            bar = find_layout_bar_entry(fresh_before.get("entries") or [], min_confidence)
            if bar:
                sx, sy = click_entry_on_screen(bar, p6_rect, (0, 0))
                evidence.steps.append(f"clicked layout bar at ({sx},{sy})")
                time.sleep(STABILITY_WAIT)
                fresh = get_fresh_p6_rect(p6_keyword)
                if fresh.get("success") and fresh.get("rect"):
                    p6_rect = fresh["rect"]
                dialog = capture_dialog_ocr(
                    evidence, "02b_layout_dialog", p6_rect, config, screen_rule, layout_dialog_rule
                )
                dialog_entries = dialog.get("entries", [])
                crop_origin = dialog.get("crop_origin", (0, 0))
                dialog_detected, dialog_hits = confirms_open_layout_dialog(
                    dialog_entries, min_confidence
                )

        if not dialog_detected:
            return finish_result(
                evidence,
                project_name,
                layout_name,
                "FAIL_LAYOUT_DIALOG_NOT_FOUND",
                "Open Layout dialog not detected after Alt+V, O",
                window_title_before=window_title_before,
                before_screen_state=before_state,
                layout_dialog_detected=False,
            )

        layout_matches = find_layout_matches(dialog_entries, layout_name, min_confidence)
        matched_text = layout_matches[0].get("text", "") if layout_matches else ""

        if not layout_matches:
            combined = list(dialog_entries) + list(full_entries)
            layout_matches = find_layout_matches(combined, layout_name, min_confidence)
            matched_text = layout_matches[0].get("text", "") if layout_matches else ""

        if not layout_matches:
            search_box = find_search_box(dialog_entries, min_confidence)
            if search_box:
                evidence.steps.append("search box detected — typing layout filter")
                click_entry_on_screen(search_box, p6_rect, crop_origin)
                time.sleep(0.3)
                import pyautogui

                pyautogui.write(layout_name, interval=0.04)
                time.sleep(0.8)
                dialog = capture_dialog_ocr(
                    evidence, "02c_layout_search", p6_rect, config, screen_rule, layout_dialog_rule
                )
                dialog_entries = dialog.get("entries", [])
                crop_origin = dialog.get("crop_origin", (0, 0))
                layout_matches = find_layout_matches(dialog_entries, layout_name, min_confidence)
                matched_text = layout_matches[0].get("text", "") if layout_matches else ""

        if not layout_matches:
            blob = collect_text_blob(dialog_entries, min_confidence)
            norm_target = normalize_layout_name(layout_name)
            if norm_target in blob and not any(
                is_partial_layout_match(e.get("text", ""), layout_name) for e in dialog_entries
            ):
                for entry in dialog_entries:
                    if layout_text_matches(entry.get("text", ""), layout_name):
                        layout_matches.append(entry)
                        matched_text = entry.get("text", "")
                        break

        if not layout_matches:
            return finish_result(
                evidence,
                project_name,
                layout_name,
                "FAIL_LAYOUT_NOT_FOUND",
                f"Layout '{layout_name}' not found in visible Open Layout list",
                window_title_before=window_title_before,
                before_screen_state=before_state,
                layout_dialog_detected=True,
                layout_found=False,
            )

        evidence.steps.append(f"select layout row: {matched_text!r}")
        click_entry_on_screen(layout_matches[0], p6_rect, crop_origin)
        time.sleep(0.5)

        open_btn = find_dialog_open_button(dialog_entries, min_confidence)
        if open_btn:
            evidence.steps.append("click Open button (OCR bbox)")
            click_entry_on_screen(open_btn, p6_rect, crop_origin)
        else:
            evidence.steps.append("confirm_open: Alt+O (dialog context confirmed)")
            keyboard_tools.hotkey("alt", "o")

        time.sleep(STABILITY_WAIT)
        keyboard_tools.press_escape()
        time.sleep(0.5)

        fresh = get_fresh_p6_rect(p6_keyword)
        if fresh.get("success") and fresh.get("rect"):
            p6_rect = fresh["rect"]
        window_title_after = window_tools.get_window_state(p6_keyword).get("title") or ""

        evidence.steps.append("capture after_action")
        after = capture_and_ocr_step(evidence, "03_after", p6_rect, config, screen_rule)
        if not after.get("ok"):
            polluted = after.get("polluted")
            return finish_result(
                evidence,
                project_name,
                layout_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_P6_WINDOW_NOT_READY",
                after.get("error", "after capture failed"),
                window_title_before=window_title_before,
                window_title_after=window_title_after,
                before_screen_state=before_state,
                layout_dialog_detected=True,
                layout_found=True,
                layout_opened=True,
                matched_layout_text=matched_text,
                manual_review_required=bool(polluted),
            )

        if after.get("unsafe"):
            return finish_result(
                evidence,
                project_name,
                layout_name,
                "MANUAL_REVIEW_UNSAFE_POPUP",
                after.get("unsafe_reason", "unsafe popup after layout open"),
                window_title_before=window_title_before,
                window_title_after=window_title_after,
                before_screen_state=before_state,
                after_screen_state=after["screen_state"],
                layout_dialog_detected=True,
                layout_found=True,
                layout_opened=True,
                matched_layout_text=matched_text,
                manual_review_required=True,
            )

        in_activities, _ = confirms_activities_workspace(after["entries"], min_confidence)
        active = confirms_active_layout(after["entries"], layout_name, min_confidence)
        after_state = after["screen_state"]

        if not in_activities and after_state != "activities_workspace":
            return finish_result(
                evidence,
                project_name,
                layout_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM",
                "Activities workspace not confirmed after opening layout",
                window_title_before=window_title_before,
                window_title_after=window_title_after,
                before_screen_state=before_state,
                after_screen_state=after_state,
                layout_dialog_detected=True,
                layout_found=True,
                layout_opened=True,
                matched_layout_text=matched_text,
                manual_review_required=True,
            )

        if active:
            return finish_result(
                evidence,
                project_name,
                layout_name,
                "PASS",
                f"Opened layout '{layout_name}' for project '{project_name}'",
                window_title_before=window_title_before,
                window_title_after=window_title_after,
                before_screen_state=before_state,
                after_screen_state=after_state,
                layout_dialog_detected=True,
                layout_found=True,
                layout_opened=True,
                active_layout_confirmed=True,
                matched_layout_text=matched_text,
            )

        return finish_result(
            evidence,
            project_name,
            layout_name,
            "PASS",
            f"Layout '{layout_name}' opened; active layout not fully confirmed in OCR",
            window_title_before=window_title_before,
            window_title_after=window_title_after,
            before_screen_state=before_state,
            after_screen_state=after_state,
            layout_dialog_detected=True,
            layout_found=True,
            layout_opened=True,
            active_layout_confirmed=False,
            matched_layout_text=matched_text,
        )

    except Exception as exc:  # noqa: BLE001
        evidence.steps.append(f"exception: {exc}")
        evidence.steps.append(traceback.format_exc())
        return finish_result(
            evidence,
            project_name,
            layout_name,
            "ERROR",
            str(exc),
            error=traceback.format_exc(),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="M07 Open Activity Layout By Name")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    parser.add_argument("--layout", required=True, help='Layout name e.g. "TP01 Main WBS"')
    args = parser.parse_args()

    result = run_m07(args.project.strip(), args.layout.strip())
    print(f"M07 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Layout dialog detected: {result.get('layout_dialog_detected')}")
    print(f"Layout found: {result.get('layout_found')}")
    print(f"Matched layout text: {result.get('matched_layout_text', '')}")
    print(f"Layout opened: {result.get('layout_opened')}")
    print(f"Active layout confirmed: {result.get('active_layout_confirmed')}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")
    if result["status"] in ("PASS", "PASS_ALREADY_ACTIVE_LAYOUT"):
        return 0
    if result["status"].startswith("MANUAL_REVIEW"):
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
