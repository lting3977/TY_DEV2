"""
M18 — Select Spreadsheet Export Format Discovery Only (Phase 17).

Opens File > Export, selects Spreadsheet/XLSX, presses Next once, captures the
next wizard screen evidence, then safely cancels. Does not press Finish or save files.
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
sys.path.insert(0, str(ROOT / "04_modules"))

from m06_go_to_activities import (  # noqa: E402
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
from eye.ocr import collect_text_blob, is_easyocr_available, normalize_text  # noqa: E402
from eye.screenshot import P6Rect  # noqa: E402
from hand.p6_prepare import prepare_p6_for_test  # noqa: E402
from accessibility.hand import window_tools  # noqa: E402
from m16_discover_p6_export_menu import (  # noqa: E402
    bbox_center,
    click_ocr_entry,
    close_export_dialog,
    detect_m16_blocking_popup,
    export_dialog_detected,
    export_file_created,
    find_cancel_entry,
    find_export_evidence_words,
    open_export_menu,
    refresh_p6_rect,
    snapshot_export_files,
)

MODULE_NAME = "m18_select_spreadsheet_export_format_discovery_only"

NEXT_SCREEN_PHRASES = (
    "export type",
    "activity relationships",
    "select template",
    "modify template",
)
NEXT_SCREEN_TOKENS = (
    "projects",
    "activities",
    "resources",
    "excel",
    "spreadsheet",
    "back",
    "template",
    "relationships",
    "wbs",
)

SPREADSHEET_MARKERS = (
    "spreadsheet",
    "xlsx",
    "(xlsx)",
    "spreadsheet - (xlsx)",
)


@dataclass
class RunEvidence:
    run_id: str
    folder: Path
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


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_evidence(run_id: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / run_id / MODULE_NAME
    for sub in ("screenshots", "ocr", "classification", "popup", "discovery"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    return RunEvidence(
        run_id=run_id,
        folder=folder,
        screenshots_dir=folder / "screenshots",
        ocr_dir=folder / "ocr",
        classification_dir=folder / "classification",
        popup_dir=folder / "popup",
        discovery_dir=folder / "discovery",
    )


def save_discovery(evidence: RunEvidence, filename: str, payload: Dict[str, Any]) -> str:
    path = evidence.discovery_dir / filename
    write_json(path, payload)
    evidence.discovery_files.append(str(path))
    return str(path)


def detect_spreadsheet_in_blob(blob: str) -> Tuple[bool, str]:
    norm = normalize_text(blob)
    for marker in ("spreadsheet - (xlsx)", "spreadsheet -", "(xlsx)", "spreadsheet", "xlsx"):
        if marker in norm:
            idx = norm.find(marker)
            start = max(0, idx - 15)
            end = min(len(norm), idx + 50)
            return True, norm[start:end].strip()
    return False, ""


def is_non_spreadsheet_format(norm: str) -> bool:
    if "spreadsheet" in norm or "xlsx" in norm:
        return False
    blockers = (
        "(xer)",
        "contractor",
        "primavera p3",
        "microsoft project",
        "uncefact",
        "ipmdar",
        "cpp format",
        "primavera pm",
    )
    return any(b in norm for b in blockers)


def find_spreadsheet_entry(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Tuple[Optional[Dict[str, Any]], str]:
    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    best_text = ""

    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        norm = entry.get("normalized", "")
        raw = entry.get("text", "")
        if is_non_spreadsheet_format(norm):
            continue
        score = 0.0
        if "spreadsheet" in norm:
            score += 10.0
        if "xlsx" in norm or "(xlsx)" in norm:
            score += 8.0
        if norm.strip() in {"spreadsheet -", "spreadsheet"}:
            score += 3.0
        if score > best_score:
            best = entry
            best_score = score
            best_text = raw or norm

    if best is not None:
        return best, best_text

    blob = collect_text_blob(entries, min_confidence)
    detected, snippet = detect_spreadsheet_in_blob(blob)
    if not detected:
        return None, ""

    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        norm = entry.get("normalized", "")
        if "spreadsheet" in norm or "xlsx" in norm:
            if not is_non_spreadsheet_format(norm):
                return entry, entry.get("text", norm)
    return None, snippet


def find_next_entry(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None
    best_conf = 0.0
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        norm = entry.get("normalized", "").strip()
        if norm == "next" and entry["confidence"] >= best_conf:
            best = entry
            best_conf = entry["confidence"]
    return best


def detect_wizard_buttons(blob: str) -> Dict[str, bool]:
    norm = normalize_text(blob)
    tokens = set(re.split(r"[\s|;,]+", norm))
    return {
        "next_button_detected": "next" in tokens or "next" in norm,
        "finish_button_detected": "finish" in tokens or "finish" in norm,
        "cancel_button_detected": "cancel" in tokens or "cancel" in norm,
    }


def confirm_spreadsheet_selected(blob: str, pre_blob: str, *, click_attempted: bool = False) -> bool:
    norm = normalize_text(blob)
    if "export type" in norm:
        return True
    detected, _ = detect_spreadsheet_in_blob(norm)
    if detected:
        return True
    pre_norm = normalize_text(pre_blob)
    if click_attempted and "export format" in pre_norm:
        return True
    return False


def find_next_screen_evidence_words(blob: str) -> List[str]:
    norm = normalize_text(blob)
    found: List[str] = []
    for phrase in NEXT_SCREEN_PHRASES:
        if phrase in norm:
            found.append(phrase)
    tokens = set(re.split(r"[\s|;,]+", norm))
    for token in NEXT_SCREEN_TOKENS:
        if token in tokens or token in norm:
            if token not in found:
                found.append(token)
    for word in ("next", "back", "cancel", "finish"):
        if word in tokens and word not in found:
            found.append(word)
    return sorted(set(found))


def next_screen_detected(evidence_words: List[str], blob: str) -> bool:
    norm = normalize_text(blob)
    if "export type" in norm:
        return True
    strong = {"export type", "activity relationships", "select template", "modify template"}
    if any(w in evidence_words for w in strong):
        return True
    wizard_words = find_export_evidence_words(blob)
    if not export_dialog_detected(wizard_words):
        return False
    content_hits = [
        w
        for w in evidence_words
        if w in {"projects", "activities", "resources", "excel", "template", "relationships", "wbs"}
    ]
    has_nav = "back" in evidence_words or "next" in evidence_words
    return len(content_hits) >= 2 and has_nav


def partial_next_screen(evidence_words: List[str], blob: str) -> bool:
    if next_screen_detected(evidence_words, blob):
        return False
    norm = normalize_text(blob)
    if "export type" in norm:
        return False
    return len(evidence_words) >= 1 and export_dialog_detected(find_export_evidence_words(blob))


def finish_pressed_in_steps(steps: List[str]) -> bool:
    blob = " ".join(steps).lower()
    return 'press_key("finish")' in blob or "press_key('finish')" in blob


def count_next_in_steps(steps: List[str]) -> int:
    count = 0
    for step in steps:
        lowered = step.lower()
        if "press next once" in lowered or "ocr-confirmed next click" in lowered:
            count += 1
        elif 'press_key("next")' in lowered or "press_key('next')" in lowered:
            count += 1
    return count


def decide_status(
    *,
    wizard_detected: bool,
    spreadsheet_detected: bool,
    spreadsheet_selected: bool,
    next_pressed_count: int,
    next_screen_ok: bool,
    partial_next: bool,
    dialog_closed: bool,
    file_created: bool,
    blocking_after: bool,
    finish_pressed: bool,
) -> Tuple[str, str]:
    if file_created or finish_pressed:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Export file may have been created or Finish pressed"
    if blocking_after:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Blocking confirmation popup after close attempt"
    if not wizard_detected:
        return "FAIL_EXPORT_WIZARD_NOT_FOUND", "File > Export did not open export wizard"
    if not spreadsheet_detected:
        return "FAIL_SPREADSHEET_OPTION_NOT_FOUND", "Spreadsheet/XLSX option not found on wizard"
    if next_pressed_count == 0:
        return "FAIL_NEXT_SCREEN_NOT_FOUND", "Next was not pressed; next screen not reached"
    if next_pressed_count > 1:
        return "MANUAL_REVIEW_UNSAFE_POPUP", f"Next pressed {next_pressed_count} times (max 1 allowed)"
    if not spreadsheet_selected:
        spreadsheet_selected = spreadsheet_selected or (
            next_pressed_count == 1 and (next_screen_ok or partial_next)
        )

    if not spreadsheet_selected:
        return "FAIL_SPREADSHEET_OPTION_NOT_FOUND", "Spreadsheet option selection not confirmed"
    if not next_screen_ok and not partial_next:
        return "FAIL_NEXT_SCREEN_NOT_FOUND", "Next pressed once but next wizard screen not confirmed"
    if next_screen_ok and dialog_closed:
        return (
            "PASS_SPREADSHEET_NEXT_DISCOVERY",
            "Spreadsheet selected, Next pressed once, next screen detected, wizard safely closed",
        )
    if partial_next and dialog_closed:
        return (
            "PASS_SPREADSHEET_NEXT_DISCOVERY_PARTIAL",
            "Spreadsheet selected, Next pressed once, partial next screen evidence, wizard safely closed",
        )
    return "FAIL_NEXT_SCREEN_NOT_FOUND", "Next screen or safe close not confirmed"


def finish_result(
    evidence: RunEvidence,
    project_name: str,
    status: str,
    reason: str,
    *,
    window_title_before: str = "",
    window_title_after: str = "",
    screen_state_before: str = "",
    screen_state_after: str = "",
    export_wizard_detected: bool = False,
    spreadsheet_option_detected: bool = False,
    spreadsheet_option_text: str = "",
    spreadsheet_option_selected: bool = False,
    next_button_detected: bool = False,
    next_pressed_count: int = 0,
    finish_button_detected: bool = False,
    finish_pressed: bool = False,
    next_screen_detected_flag: bool = False,
    next_screen_evidence_words: Optional[List[str]] = None,
    cancel_button_detected: bool = False,
    export_dialog_closed: bool = False,
    close_method_used: str = "",
    export_file_created_flag: bool = False,
    manual_review_required: bool = False,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    result = {
        "run_id": evidence.run_id,
        "module": MODULE_NAME,
        "project_name": project_name,
        "status": status,
        "reason": reason,
        "window_title_before": window_title_before,
        "window_title_after": window_title_after,
        "screen_state_before": screen_state_before,
        "screen_state_after": screen_state_after,
        "export_wizard_detected": export_wizard_detected,
        "spreadsheet_option_detected": spreadsheet_option_detected,
        "spreadsheet_option_text": spreadsheet_option_text,
        "spreadsheet_option_selected": spreadsheet_option_selected,
        "next_button_detected": next_button_detected,
        "next_pressed_count": next_pressed_count,
        "finish_button_detected": finish_button_detected,
        "finish_pressed": finish_pressed,
        "next_screen_detected": next_screen_detected_flag,
        "next_screen_evidence_words": next_screen_evidence_words or [],
        "cancel_button_detected": cancel_button_detected,
        "export_dialog_closed": export_dialog_closed,
        "close_method_used": close_method_used,
        "export_file_created": export_file_created_flag,
        "screenshots": evidence.screenshots,
        "ocr_files": evidence.ocr_files,
        "classification_files": evidence.classification_files,
        "popup_files": evidence.popup_files,
        "discovery_files": evidence.discovery_files,
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

    discovery_summary = ""
    for path in result.get("discovery_files", []):
        try:
            discovery_summary += json.dumps(load_json(Path(path)), indent=2) + "\n"
        except Exception:  # noqa: BLE001
            discovery_summary += path + "\n"

    lines = [
        "# M18 Select Spreadsheet Export Format Discovery Only Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project name: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Window title before: {result.get('window_title_before', '')}",
        f"- Window title after: {result.get('window_title_after', '')}",
        f"- Screen state before: {result.get('screen_state_before', '')}",
        f"- Screen state after: {result.get('screen_state_after', '')}",
        f"- Export wizard detected: {result.get('export_wizard_detected')}",
        f"- Spreadsheet option detected: {result.get('spreadsheet_option_detected')}",
        f"- Spreadsheet option text: {result.get('spreadsheet_option_text', '')}",
        f"- Spreadsheet option selected: {result.get('spreadsheet_option_selected')}",
        f"- Next button detected: {result.get('next_button_detected')}",
        f"- Next pressed count: {result.get('next_pressed_count', 0)}",
        f"- Finish button detected: {result.get('finish_button_detected')}",
        f"- Finish pressed: {result.get('finish_pressed')}",
        f"- Next screen detected: {result.get('next_screen_detected')}",
        f"- Next screen evidence words: {result.get('next_screen_evidence_words', [])}",
        f"- Cancel button detected: {result.get('cancel_button_detected')}",
        f"- Export dialog closed: {result.get('export_dialog_closed')}",
        f"- Close method used: {result.get('close_method_used', '')}",
        f"- Export file created: {result.get('export_file_created')}",
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

    lines.extend(["", "## Discovery evidence summary", discovery_summary or "(none)", "", "## Final decision"])
    lines.append(result["status"])
    lines.extend(["", "## Next recommendation"])
    if result["status"] in ("PASS_SPREADSHEET_NEXT_DISCOVERY", "PASS_SPREADSHEET_NEXT_DISCOVERY_PARTIAL"):
        lines.append("Ready for M18 hard testing.")
    elif result["status"].startswith("MANUAL_REVIEW"):
        lines.append("Review screenshots and popup evidence before retrying.")
    else:
        lines.append("Fix the reported issue and re-run TY_TEST_M18_DISCOVER_SPREADSHEET_EXPORT_NEXT.bat")
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_m18(
    project_name: str,
    *,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
    force_spreadsheet_not_found: bool = False,
    force_next_screen_not_found: bool = False,
) -> Dict[str, Any]:
    evidence = evidence or build_evidence(run_id or new_run_id())
    config = load_json(CONFIG_PATH)
    screen_rule = load_json(SCREEN_RULE_PATH)
    p6_keyword = config["p6_window_title_keyword"]
    min_confidence = float(config.get("min_ocr_confidence", 0.5))

    project_name = (project_name or "").strip()
    if not project_name:
        return finish_result(evidence, "", "FAIL_PROJECT_NAME_EMPTY", "project_name is empty")

    evidence.steps.append("validate project_name")

    if not is_easyocr_available():
        return finish_result(
            evidence,
            project_name,
            "ERROR",
            "EasyOCR not installed",
            error="pip install easyocr",
        )

    export_snap_before = snapshot_export_files()
    next_pressed_count = 0
    wizard_detected = False
    spreadsheet_detected = False
    spreadsheet_option_text = ""
    spreadsheet_selected = False
    buttons: Dict[str, bool] = {
        "next_button_detected": False,
        "finish_button_detected": False,
        "cancel_button_detected": False,
    }

    try:
        evidence.steps.append("prepare_p6_for_test")
        prep = prepare_p6_for_test(p6_keyword)
        if not prep.get("success") or not prep.get("rect"):
            return finish_result(
                evidence,
                project_name,
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
                "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_P6_WINDOW_NOT_READY",
                before.get("error", "before capture failed"),
                window_title_before=window_title_before,
                screen_state_before="unknown",
                manual_review_required=bool(polluted),
            )

        screen_state_before = before["screen_state"]
        if before.get("unsafe"):
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_UNSAFE_POPUP",
                before.get("unsafe_reason", "unsafe popup before action"),
                window_title_before=window_title_before,
                screen_state_before=screen_state_before,
                manual_review_required=True,
            )

        open_ok, open_reason, _ = confirm_project_open(
            before["entries"], project_name, window_title_before, min_confidence
        )
        if not open_ok:
            return finish_result(
                evidence,
                project_name,
                "FAIL_PROJECT_NOT_OPEN",
                open_reason,
                window_title_before=window_title_before,
                screen_state_before=screen_state_before,
            )

        in_activities, _ = confirms_activities_workspace(before["entries"], min_confidence)
        if not in_activities:
            evidence.steps.append("not in Activities — navigate via M06-style Alt+P, A")
            navigate_to_activities(evidence)
            fresh = refresh_p6_rect(p6_keyword, p6_rect)
            nav_cap = capture_and_ocr_step(evidence, "02_after_nav", fresh, config, screen_rule)
            if not nav_cap.get("ok"):
                return finish_result(
                    evidence,
                    project_name,
                    "FAIL_ACTIVITIES_NOT_FOUND",
                    nav_cap.get("error", "Activities not confirmed after navigation"),
                    window_title_before=window_title_before,
                    screen_state_before=screen_state_before,
                )
            in_activities, _ = confirms_activities_workspace(nav_cap["entries"], min_confidence)
            p6_rect = fresh
            screen_state_before = nav_cap["screen_state"]
            if not in_activities:
                return finish_result(
                    evidence,
                    project_name,
                    "FAIL_ACTIVITIES_NOT_FOUND",
                    "Activities workspace not confirmed after M06-style navigation",
                    window_title_before=window_title_before,
                    screen_state_before=screen_state_before,
                )
            if nav_cap.get("unsafe"):
                return finish_result(
                    evidence,
                    project_name,
                    "MANUAL_REVIEW_UNSAFE_POPUP",
                    nav_cap.get("unsafe_reason", "unsafe popup after navigation"),
                    window_title_before=window_title_before,
                    screen_state_before=screen_state_before,
                    manual_review_required=True,
                )

        open_export_menu(evidence)
        p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

        evidence.steps.append("capture after_export_wizard_open")
        after_wizard = capture_and_ocr_step(evidence, "03_after_wizard", p6_rect, config, screen_rule)
        if not after_wizard.get("ok"):
            polluted = after_wizard.get("polluted")
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_P6_WINDOW_NOT_READY",
                after_wizard.get("error", "after wizard capture failed"),
                window_title_before=window_title_before,
                screen_state_before=screen_state_before,
                manual_review_required=bool(polluted),
            )

        blocking, blocking_reason = detect_m16_blocking_popup(after_wizard["entries"], min_confidence)
        if blocking:
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_UNSAFE_POPUP",
                blocking_reason,
                window_title_before=window_title_before,
                screen_state_before=screen_state_before,
                manual_review_required=True,
            )

        wizard_blob = collect_text_blob(after_wizard["entries"], min_confidence)
        evidence_words = find_export_evidence_words(wizard_blob)
        wizard_detected = export_dialog_detected(evidence_words) or "export format" in normalize_text(wizard_blob)
        spreadsheet_detected, spreadsheet_option_text = detect_spreadsheet_in_blob(wizard_blob)
        if not spreadsheet_option_text:
            ss_entry, ss_text = find_spreadsheet_entry(after_wizard["entries"], min_confidence)
            if ss_entry is not None:
                spreadsheet_detected = True
                spreadsheet_option_text = ss_text
        buttons = detect_wizard_buttons(wizard_blob)

        if force_spreadsheet_not_found:
            evidence.steps.append(
                "force_spreadsheet_not_found: hard test mode — Spreadsheet/XLSX detection suppressed"
            )
            spreadsheet_detected = False
            spreadsheet_option_text = ""

        save_discovery(
            evidence,
            "spreadsheet_selection_evidence.json",
            {
                "export_wizard_detected": wizard_detected,
                "spreadsheet_option_detected": spreadsheet_detected,
                "spreadsheet_option_text": spreadsheet_option_text,
                "export_evidence_words": evidence_words,
                "wizard_buttons": buttons,
                "ocr_blob_excerpt": wizard_blob[:2500],
                "screen_state": after_wizard.get("screen_state", ""),
                "classification": after_wizard.get("classification", {}),
            },
        )

        if not wizard_detected:
            closed, close_method, p6_rect = close_export_dialog(
                evidence,
                p6_keyword,
                p6_rect,
                config,
                screen_rule,
                after_wizard["entries"],
                evidence_words,
            )
            return finish_result(
                evidence,
                project_name,
                "FAIL_EXPORT_WIZARD_NOT_FOUND",
                "File > Export did not open export wizard",
                window_title_before=window_title_before,
                window_title_after=window_tools.get_window_state(p6_keyword).get("title") or "",
                screen_state_before=screen_state_before,
                screen_state_after=after_wizard.get("screen_state", ""),
                export_wizard_detected=False,
                spreadsheet_option_detected=spreadsheet_detected,
                spreadsheet_option_text=spreadsheet_option_text,
                export_dialog_closed=closed,
                close_method_used=close_method,
                export_file_created_flag=export_file_created(export_snap_before, snapshot_export_files()),
                next_button_detected=buttons["next_button_detected"],
                finish_button_detected=buttons["finish_button_detected"],
                cancel_button_detected=buttons["cancel_button_detected"],
            )

        if not spreadsheet_detected:
            closed, close_method, p6_rect = close_export_dialog(
                evidence,
                p6_keyword,
                p6_rect,
                config,
                screen_rule,
                after_wizard["entries"],
                evidence_words,
            )
            return finish_result(
                evidence,
                project_name,
                "FAIL_SPREADSHEET_OPTION_NOT_FOUND",
                "Spreadsheet/XLSX option not found on export wizard",
                window_title_before=window_title_before,
                window_title_after=window_tools.get_window_state(p6_keyword).get("title") or "",
                screen_state_before=screen_state_before,
                export_wizard_detected=wizard_detected,
                spreadsheet_option_detected=False,
                export_dialog_closed=closed,
                close_method_used=close_method,
                export_file_created_flag=export_file_created(export_snap_before, snapshot_export_files()),
                next_button_detected=buttons["next_button_detected"],
                finish_button_detected=buttons["finish_button_detected"],
                cancel_button_detected=buttons["cancel_button_detected"],
            )

        ss_entry, ss_click_text = find_spreadsheet_entry(after_wizard["entries"], min_confidence)
        if ss_entry is None:
            closed, close_method, p6_rect = close_export_dialog(
                evidence,
                p6_keyword,
                p6_rect,
                config,
                screen_rule,
                after_wizard["entries"],
                evidence_words,
            )
            return finish_result(
                evidence,
                project_name,
                "FAIL_SPREADSHEET_OPTION_NOT_FOUND",
                "Spreadsheet option visible in OCR blob but no clickable bbox found",
                window_title_before=window_title_before,
                window_title_after=window_tools.get_window_state(p6_keyword).get("title") or "",
                screen_state_before=screen_state_before,
                export_wizard_detected=wizard_detected,
                spreadsheet_option_detected=True,
                spreadsheet_option_text=spreadsheet_option_text,
                export_dialog_closed=closed,
                close_method_used=close_method,
                export_file_created_flag=export_file_created(export_snap_before, snapshot_export_files()),
                next_button_detected=buttons["next_button_detected"],
                finish_button_detected=buttons["finish_button_detected"],
                cancel_button_detected=buttons["cancel_button_detected"],
            )

        evidence.steps.append(
            f"select Spreadsheet option: OCR click on '{ss_click_text[:60]}'"
        )
        click_ocr_entry(p6_rect, ss_entry)
        time.sleep(0.8)

        p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
        after_select = capture_and_ocr_step(
            evidence, "04_after_spreadsheet_select", p6_rect, config, screen_rule
        )
        select_blob = ""
        if after_select.get("ok"):
            select_blob = collect_text_blob(after_select["entries"], min_confidence)
            spreadsheet_selected = confirm_spreadsheet_selected(
                select_blob, wizard_blob, click_attempted=True
            )
        else:
            spreadsheet_selected = True

        save_discovery(
            evidence,
            "spreadsheet_selection_evidence.json",
            {
                "export_wizard_detected": wizard_detected,
                "spreadsheet_option_detected": spreadsheet_detected,
                "spreadsheet_option_text": spreadsheet_option_text,
                "spreadsheet_option_selected": spreadsheet_selected,
                "spreadsheet_click_text": ss_click_text,
                "export_evidence_words": evidence_words,
                "wizard_buttons": buttons,
                "ocr_blob_excerpt": wizard_blob[:2500],
                "post_select_blob_excerpt": select_blob[:2500],
                "screen_state": after_wizard.get("screen_state", ""),
                "classification": after_wizard.get("classification", {}),
            },
        )

        if after_select.get("ok"):
            blocking, blocking_reason = detect_m16_blocking_popup(after_select["entries"], min_confidence)
            if blocking:
                return finish_result(
                    evidence,
                    project_name,
                    "MANUAL_REVIEW_UNSAFE_POPUP",
                    blocking_reason,
                    window_title_before=window_title_before,
                    screen_state_before=screen_state_before,
                    export_wizard_detected=wizard_detected,
                    spreadsheet_option_detected=True,
                    spreadsheet_option_text=spreadsheet_option_text,
                    spreadsheet_option_selected=spreadsheet_selected,
                    next_button_detected=buttons["next_button_detected"],
                    manual_review_required=True,
                )

        select_entries = after_select.get("entries", after_wizard["entries"])
        next_entry = find_next_entry(select_entries, min_confidence)
        if next_entry is None:
            next_entry = find_next_entry(after_wizard["entries"], min_confidence)

        if next_entry is None:
            closed, close_method, p6_rect = close_export_dialog(
                evidence,
                p6_keyword,
                p6_rect,
                config,
                screen_rule,
                select_entries,
                evidence_words,
            )
            return finish_result(
                evidence,
                project_name,
                "FAIL_NEXT_SCREEN_NOT_FOUND",
                "Next button not found after Spreadsheet selection",
                window_title_before=window_title_before,
                window_title_after=window_tools.get_window_state(p6_keyword).get("title") or "",
                screen_state_before=screen_state_before,
                export_wizard_detected=wizard_detected,
                spreadsheet_option_detected=True,
                spreadsheet_option_text=spreadsheet_option_text,
                spreadsheet_option_selected=spreadsheet_selected,
                next_button_detected=False,
                export_dialog_closed=closed,
                close_method_used=close_method,
                export_file_created_flag=export_file_created(export_snap_before, snapshot_export_files()),
                finish_button_detected=buttons["finish_button_detected"],
                cancel_button_detected=buttons["cancel_button_detected"],
            )

        evidence.steps.append("press Next once: OCR-confirmed Next click")
        click_ocr_entry(p6_rect, next_entry)
        next_pressed_count = 1
        time.sleep(STABILITY_WAIT)

        p6_rect = refresh_p6_rect(p6_keyword, p6_rect)
        evidence.steps.append("capture after_next_screen")
        after_next = capture_and_ocr_step(evidence, "05_after_next", p6_rect, config, screen_rule)
        next_blob = ""
        next_screen_words: List[str] = []
        next_screen_ok = False
        partial_next = False

        if after_next.get("ok"):
            next_blob = collect_text_blob(after_next["entries"], min_confidence)
            next_screen_words = find_next_screen_evidence_words(next_blob)
            next_screen_ok = next_screen_detected(next_screen_words, next_blob)
            partial_next = partial_next_screen(next_screen_words, next_blob)
            if next_screen_ok or partial_next:
                spreadsheet_selected = True
            if force_next_screen_not_found:
                evidence.steps.append(
                    "force_next_screen_not_found: hard test mode — next screen evidence suppressed"
                )
                next_screen_ok = False
                partial_next = False
            buttons_after = detect_wizard_buttons(next_blob)
            buttons.update(buttons_after)

            blocking, blocking_reason = detect_m16_blocking_popup(after_next["entries"], min_confidence)
            if blocking:
                return finish_result(
                    evidence,
                    project_name,
                    "MANUAL_REVIEW_UNSAFE_POPUP",
                    blocking_reason,
                    window_title_before=window_title_before,
                    screen_state_before=screen_state_before,
                    export_wizard_detected=wizard_detected,
                    spreadsheet_option_detected=True,
                    spreadsheet_option_text=spreadsheet_option_text,
                    spreadsheet_option_selected=spreadsheet_selected,
                    next_pressed_count=next_pressed_count,
                    next_button_detected=buttons["next_button_detected"],
                    finish_button_detected=buttons["finish_button_detected"],
                    cancel_button_detected=buttons["cancel_button_detected"],
                    manual_review_required=True,
                )

        save_discovery(
            evidence,
            "next_screen_evidence.json",
            {
                "next_screen_detected": next_screen_ok,
                "partial_next_screen": partial_next,
                "next_screen_evidence_words": next_screen_words,
                "next_pressed_count": next_pressed_count,
                "force_next_screen_not_found": force_next_screen_not_found,
                "ocr_blob_excerpt": next_blob[:2500],
                "screen_state": after_next.get("screen_state", "") if after_next.get("ok") else "",
                "classification": after_next.get("classification", {}) if after_next.get("ok") else {},
                "wizard_buttons": buttons,
            },
        )

        next_evidence_words = find_export_evidence_words(next_blob) if next_blob else evidence_words
        close_entries = after_next.get("entries", select_entries) if after_next.get("ok") else select_entries
        closed, close_method, p6_rect = close_export_dialog(
            evidence,
            p6_keyword,
            p6_rect,
            config,
            screen_rule,
            close_entries,
            next_evidence_words if next_blob else evidence_words,
        )

        evidence.steps.append("capture final_after_close")
        final_cap = capture_and_ocr_step(evidence, "07_final", p6_rect, config, screen_rule)
        screen_state_after = final_cap.get("screen_state", "unknown") if final_cap.get("ok") else "unknown"
        window_title_after = window_tools.get_window_state(p6_keyword).get("title") or ""

        blocking_after = False
        if final_cap.get("ok"):
            blocking_after, blocking_reason = detect_m16_blocking_popup(
                final_cap["entries"], min_confidence
            )
            if blocking_after:
                return finish_result(
                    evidence,
                    project_name,
                    "MANUAL_REVIEW_UNSAFE_POPUP",
                    blocking_reason,
                    window_title_before=window_title_before,
                    window_title_after=window_title_after,
                    screen_state_before=screen_state_before,
                    screen_state_after=screen_state_after,
                    export_wizard_detected=wizard_detected,
                    spreadsheet_option_detected=True,
                    spreadsheet_option_text=spreadsheet_option_text,
                    spreadsheet_option_selected=spreadsheet_selected,
                    next_pressed_count=next_pressed_count,
                    next_screen_detected_flag=next_screen_ok,
                    next_screen_evidence_words=next_screen_words,
                    export_dialog_closed=closed,
                    close_method_used=close_method,
                    finish_pressed=finish_pressed_in_steps(evidence.steps),
                    export_file_created_flag=export_file_created(
                        export_snap_before, snapshot_export_files()
                    ),
                    next_button_detected=buttons["next_button_detected"],
                    finish_button_detected=buttons["finish_button_detected"],
                    cancel_button_detected=buttons["cancel_button_detected"],
                    manual_review_required=True,
                )

        file_created = export_file_created(export_snap_before, snapshot_export_files())
        finish_pressed = finish_pressed_in_steps(evidence.steps)
        next_pressed_count = max(next_pressed_count, count_next_in_steps(evidence.steps))

        status, reason = decide_status(
            wizard_detected=wizard_detected,
            spreadsheet_detected=spreadsheet_detected,
            spreadsheet_selected=spreadsheet_selected,
            next_pressed_count=next_pressed_count,
            next_screen_ok=next_screen_ok,
            partial_next=partial_next,
            dialog_closed=closed,
            file_created=file_created,
            blocking_after=blocking_after,
            finish_pressed=finish_pressed,
        )

        return finish_result(
            evidence,
            project_name,
            status,
            reason,
            window_title_before=window_title_before,
            window_title_after=window_title_after,
            screen_state_before=screen_state_before,
            screen_state_after=screen_state_after,
            export_wizard_detected=wizard_detected,
            spreadsheet_option_detected=spreadsheet_detected,
            spreadsheet_option_text=spreadsheet_option_text,
            spreadsheet_option_selected=spreadsheet_selected,
            next_button_detected=buttons["next_button_detected"],
            next_pressed_count=next_pressed_count,
            finish_button_detected=buttons["finish_button_detected"],
            finish_pressed=finish_pressed,
            next_screen_detected_flag=next_screen_ok,
            next_screen_evidence_words=next_screen_words,
            cancel_button_detected=buttons["cancel_button_detected"],
            export_dialog_closed=closed,
            close_method_used=close_method,
            export_file_created_flag=file_created,
            manual_review_required=status.startswith("MANUAL_REVIEW"),
        )

    except Exception as exc:  # noqa: BLE001
        evidence.steps.append(f"exception: {exc}")
        evidence.steps.append(traceback.format_exc())
        return finish_result(
            evidence,
            project_name,
            "ERROR",
            str(exc),
            export_wizard_detected=wizard_detected,
            spreadsheet_option_detected=spreadsheet_detected,
            spreadsheet_option_text=spreadsheet_option_text,
            spreadsheet_option_selected=spreadsheet_selected,
            next_pressed_count=next_pressed_count,
            export_file_created_flag=export_file_created(
                export_snap_before, snapshot_export_files()
            ),
            error=traceback.format_exc(),
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="M18 Select Spreadsheet Export Format Discovery Only"
    )
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    args = parser.parse_args()
    result = run_m18(args.project.strip())
    print(f"M18 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Export wizard detected: {result.get('export_wizard_detected')}")
    print(f"Spreadsheet option detected: {result.get('spreadsheet_option_detected')}")
    print(f"Spreadsheet option selected: {result.get('spreadsheet_option_selected')}")
    print(f"Next pressed count: {result.get('next_pressed_count', 0)}")
    print(f"Next screen detected: {result.get('next_screen_detected')}")
    print(f"Next screen evidence words: {result.get('next_screen_evidence_words', [])}")
    print(f"Finish pressed: {result.get('finish_pressed')}")
    print(f"Export dialog closed: {result.get('export_dialog_closed')}")
    print(f"Export file created: {result.get('export_file_created')}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")
    if result["status"] in ("PASS_SPREADSHEET_NEXT_DISCOVERY", "PASS_SPREADSHEET_NEXT_DISCOVERY_PARTIAL"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
