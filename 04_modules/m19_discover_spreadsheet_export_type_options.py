"""
M19 — Discover Spreadsheet Export Type Options (Phase 18).

Opens File > Export, selects Spreadsheet/XLSX, presses Next once, OCR-reads
Export Type options on the next wizard screen, then safely cancels.
Does not select export types, press Next again, Finish, or save files.
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
    click_ocr_entry,
    close_export_dialog,
    detect_m16_blocking_popup,
    export_dialog_detected,
    export_file_created,
    find_export_evidence_words,
    open_export_menu,
    refresh_p6_rect,
    snapshot_export_files,
)
from m18_select_spreadsheet_export_format_discovery_only import (  # noqa: E402
    confirm_spreadsheet_selected,
    count_next_in_steps,
    detect_spreadsheet_in_blob,
    detect_wizard_buttons,
    find_next_entry,
    find_next_screen_evidence_words,
    find_spreadsheet_entry,
    finish_pressed_in_steps,
    next_screen_detected,
)

MODULE_NAME = "m19_discover_spreadsheet_export_type_options"

EXPORT_TYPE_SCREEN_PHRASES = (
    "export type",
    "select the type",
    "type data to export",
)
EXPORT_TYPE_EVIDENCE_TOKENS = (
    "activities",
    "activity relationships",
    "projects",
    "wbs",
    "resources",
    "resource assignments",
    "roles",
    "expenses",
    "back",
    "cancel",
    "next",
    "finish",
)

EXPORT_TYPE_DETECTORS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("Projects", ("projects",)),
    ("WBS", ("wbs",)),
    ("Activities", ("activities", "ectivities")),
    ("Activity Relationships", ("activity relationships", "relationships")),
    ("Resources", ("resources",)),
    (
        "Resource Assignments",
        ("resource assignments", "resource agsignments", "agsignments", "assignments"),
    ),
    ("Roles", ("roles",)),
    ("Expenses", ("expenses",)),
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


def extract_export_type_dialog_blob(blob: str) -> str:
    norm = normalize_text(blob)
    if "export type" not in norm:
        return norm
    start = norm.find("export type")
    end = len(norm)
    for marker in ("cancel prev next", "cancel prev", " access mode"):
        pos = norm.find(marker, start + 10)
        if pos > start:
            end = min(end, pos)
    return norm[start:end]


def find_export_type_evidence_words(blob: str) -> List[str]:
    norm = normalize_text(blob)
    found: List[str] = []
    for phrase in EXPORT_TYPE_SCREEN_PHRASES:
        if phrase in norm:
            found.append(phrase)
    dialog = extract_export_type_dialog_blob(blob)
    tokens = set(re.split(r"[\s|;,]+", dialog))
    for token in EXPORT_TYPE_EVIDENCE_TOKENS:
        if token in tokens or token in dialog:
            if token not in found:
                found.append(token)
    return sorted(set(found))


def export_type_screen_detected(evidence_words: List[str], blob: str) -> bool:
    norm = normalize_text(blob)
    if "export type" in norm:
        return True
    if any(p in evidence_words for p in ("export type", "select the type", "type data to export")):
        return True
    return next_screen_detected(evidence_words, blob)


def detect_export_type_options(blob: str) -> List[str]:
    dialog = extract_export_type_dialog_blob(blob)
    norm = normalize_text(dialog)
    found: List[str] = []
    for name, patterns in EXPORT_TYPE_DETECTORS:
        if any(p in norm for p in patterns):
            if name == "Projects" and "projects" in norm:
                if "export type" not in normalize_text(blob):
                    continue
            found.append(name)
    return found


def extract_raw_export_type_examples(blob: str) -> List[str]:
    dialog = extract_export_type_dialog_blob(blob)
    norm = normalize_text(dialog)
    keywords = (
        "projects",
        "wbs",
        "activities",
        "ectivities",
        "activity relationships",
        "relationships",
        "resources",
        "resource assignments",
        "agsignments",
        "roles",
        "expenses",
        "export type",
    )
    examples: List[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        idx = 0
        while True:
            pos = norm.find(keyword, idx)
            if pos < 0:
                break
            start = max(0, pos - 25)
            end = min(len(norm), pos + len(keyword) + 40)
            snippet = norm[start:end].strip()
            if snippet and snippet not in seen:
                seen.add(snippet)
                examples.append(snippet)
            idx = pos + len(keyword)
    return examples[:20]


def next_pressed_after_export_type(steps: List[str]) -> bool:
    seen_export_type = False
    next_count_after = 0
    for step in steps:
        lowered = step.lower()
        if "capture after_export_type" in lowered or "export type screen" in lowered:
            seen_export_type = True
        if seen_export_type and (
            "press next once" in lowered or "ocr-confirmed next click" in lowered
        ):
            next_count_after += 1
    return next_count_after > 0


def export_type_selected_in_steps(steps: List[str]) -> bool:
    for step in steps:
        lowered = step.lower()
        if "select export type" in lowered or "export type option click" in lowered:
            return True
    return False


def decide_status(
    *,
    wizard_detected: bool,
    spreadsheet_detected: bool,
    spreadsheet_selected: bool,
    next_pressed_count: int,
    export_type_screen_ok: bool,
    export_type_options: List[str],
    export_type_selected: bool,
    next_after_type: bool,
    dialog_closed: bool,
    file_created: bool,
    blocking_after: bool,
    finish_pressed: bool,
) -> Tuple[str, str]:
    if file_created or finish_pressed:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Export file may have been created or Finish pressed"
    if blocking_after:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Blocking confirmation popup after close attempt"
    if next_after_type:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Next pressed after Export Type screen"
    if export_type_selected:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Export type option was selected during discovery"
    if not wizard_detected:
        return "FAIL_EXPORT_WIZARD_NOT_FOUND", "File > Export did not open export wizard"
    if not spreadsheet_detected:
        return "FAIL_SPREADSHEET_OPTION_NOT_FOUND", "Spreadsheet/XLSX option not found on wizard"
    if next_pressed_count == 0:
        return "FAIL_EXPORT_TYPE_SCREEN_NOT_FOUND", "Next was not pressed; Export Type screen not reached"
    if next_pressed_count > 1:
        return "MANUAL_REVIEW_UNSAFE_POPUP", f"Next pressed {next_pressed_count} times (max 1 allowed)"
    if not spreadsheet_selected:
        spreadsheet_selected = spreadsheet_selected or (
            next_pressed_count == 1 and export_type_screen_ok
        )
    if not spreadsheet_selected:
        return "FAIL_SPREADSHEET_OPTION_NOT_FOUND", "Spreadsheet option selection not confirmed"
    if not export_type_screen_ok:
        return "FAIL_EXPORT_TYPE_SCREEN_NOT_FOUND", "Export Type screen not confirmed after Next"
    if not export_type_options:
        return "FAIL_EXPORT_TYPE_OPTIONS_NOT_FOUND", "Export Type screen detected but no options found"
    if len(export_type_options) >= 2 and dialog_closed:
        return (
            "PASS_EXPORT_TYPE_DISCOVERY",
            f"Detected {len(export_type_options)} export type option(s); wizard safely closed",
        )
    if len(export_type_options) >= 1 and dialog_closed:
        return (
            "PASS_EXPORT_TYPE_DISCOVERY_PARTIAL",
            f"Partial export type discovery: {len(export_type_options)} option(s); wizard safely closed",
        )
    return "FAIL_EXPORT_TYPE_OPTIONS_NOT_FOUND", "Export type options or safe close not confirmed"


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
    next_pressed_count: int = 0,
    export_type_screen_detected_flag: bool = False,
    export_type_evidence_words: Optional[List[str]] = None,
    export_type_options_detected: Optional[List[str]] = None,
    export_type_selected: bool = False,
    next_pressed_after_export_type: bool = False,
    finish_button_detected: bool = False,
    finish_pressed: bool = False,
    cancel_button_detected: bool = False,
    export_dialog_closed: bool = False,
    close_method_used: str = "",
    export_file_created_flag: bool = False,
    manual_review_required: bool = False,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    options = export_type_options_detected or []
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
        "next_pressed_count": next_pressed_count,
        "export_type_screen_detected": export_type_screen_detected_flag,
        "export_type_evidence_words": export_type_evidence_words or [],
        "export_type_options_detected": options,
        "export_type_option_count": len(options),
        "export_type_selected": export_type_selected,
        "next_pressed_after_export_type": next_pressed_after_export_type,
        "finish_button_detected": finish_button_detected,
        "finish_pressed": finish_pressed,
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
        "# M19 Discover Spreadsheet Export Type Options Report",
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
        f"- Next pressed count: {result.get('next_pressed_count', 0)}",
        f"- Export Type screen detected: {result.get('export_type_screen_detected')}",
        f"- Export Type evidence words: {result.get('export_type_evidence_words', [])}",
        f"- Export Type options detected: {result.get('export_type_options_detected', [])}",
        f"- Export Type option count: {result.get('export_type_option_count', 0)}",
        f"- Export Type selected: {result.get('export_type_selected')}",
        f"- Next pressed after Export Type: {result.get('next_pressed_after_export_type')}",
        f"- Finish button detected: {result.get('finish_button_detected')}",
        f"- Finish pressed: {result.get('finish_pressed')}",
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
    if result["status"] in ("PASS_EXPORT_TYPE_DISCOVERY", "PASS_EXPORT_TYPE_DISCOVERY_PARTIAL"):
        lines.append("Ready for M19 hard testing.")
    elif result["status"].startswith("MANUAL_REVIEW"):
        lines.append("Review screenshots and popup evidence before retrying.")
    else:
        lines.append("Fix the reported issue and re-run TY_TEST_M19_DISCOVER_SPREADSHEET_EXPORT_TYPES.bat")
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_m19(
    project_name: str,
    *,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
    force_export_type_options_not_found: bool = False,
    force_export_type_screen_not_found: bool = False,
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
            ss_entry_probe, ss_text = find_spreadsheet_entry(after_wizard["entries"], min_confidence)
            if ss_entry_probe is not None:
                spreadsheet_detected = True
                spreadsheet_option_text = ss_text
        buttons = detect_wizard_buttons(wizard_blob)

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
                export_wizard_detected=False,
                spreadsheet_option_detected=spreadsheet_detected,
                spreadsheet_option_text=spreadsheet_option_text,
                export_dialog_closed=closed,
                close_method_used=close_method,
                export_file_created_flag=export_file_created(export_snap_before, snapshot_export_files()),
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
                export_dialog_closed=closed,
                close_method_used=close_method,
                export_file_created_flag=export_file_created(export_snap_before, snapshot_export_files()),
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
                "FAIL_EXPORT_TYPE_SCREEN_NOT_FOUND",
                "Next button not found after Spreadsheet selection",
                window_title_before=window_title_before,
                window_title_after=window_tools.get_window_state(p6_keyword).get("title") or "",
                screen_state_before=screen_state_before,
                export_wizard_detected=wizard_detected,
                spreadsheet_option_detected=True,
                spreadsheet_option_text=spreadsheet_option_text,
                spreadsheet_option_selected=spreadsheet_selected,
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
        evidence.steps.append("capture after_export_type_screen")
        after_type = capture_and_ocr_step(evidence, "05_after_export_type", p6_rect, config, screen_rule)
        type_blob = ""
        type_evidence_words: List[str] = []
        export_type_screen_ok = False
        export_type_options: List[str] = []
        raw_examples: List[str] = []

        if after_type.get("ok"):
            type_blob = collect_text_blob(after_type["entries"], min_confidence)
            type_evidence_words = find_export_type_evidence_words(type_blob)
            export_type_screen_ok = export_type_screen_detected(type_evidence_words, type_blob)
            export_type_options = detect_export_type_options(type_blob)
            raw_examples = extract_raw_export_type_examples(type_blob)
            if export_type_screen_ok:
                spreadsheet_selected = True
            if force_export_type_screen_not_found:
                evidence.steps.append(
                    "force_export_type_screen_not_found: hard test mode — Export Type screen suppressed"
                )
                export_type_screen_ok = False
            if force_export_type_options_not_found:
                evidence.steps.append(
                    "force_export_type_options_not_found: hard test mode — export type options suppressed"
                )
                export_type_options = []
                raw_examples = []
            buttons.update(detect_wizard_buttons(type_blob))

            blocking, blocking_reason = detect_m16_blocking_popup(after_type["entries"], min_confidence)
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
                    export_type_screen_detected_flag=export_type_screen_ok,
                    export_type_evidence_words=type_evidence_words,
                    export_type_options_detected=export_type_options,
                    finish_button_detected=buttons["finish_button_detected"],
                    cancel_button_detected=buttons["cancel_button_detected"],
                    manual_review_required=True,
                )

        save_discovery(
            evidence,
            "export_type_screen_evidence.json",
            {
                "export_type_screen_detected": export_type_screen_ok,
                "export_type_evidence_words": type_evidence_words,
                "export_type_options_detected": export_type_options,
                "export_type_option_count": len(export_type_options),
                "next_pressed_count": next_pressed_count,
                "force_export_type_screen_not_found": force_export_type_screen_not_found,
                "force_export_type_options_not_found": force_export_type_options_not_found,
                "dialog_blob_excerpt": extract_export_type_dialog_blob(type_blob)[:1500],
                "ocr_blob_excerpt": type_blob[:2500],
                "screen_state": after_type.get("screen_state", "") if after_type.get("ok") else "",
                "classification": after_type.get("classification", {}) if after_type.get("ok") else {},
                "wizard_buttons": buttons,
            },
        )

        save_discovery(
            evidence,
            "export_type_options.json",
            {
                "export_type_options_detected": export_type_options,
                "export_type_option_count": len(export_type_options),
                "raw_option_examples": raw_examples,
                "export_type_evidence_words": type_evidence_words,
                "detection_method": "ocr_only_export_type_screen",
                "force_export_type_options_not_found": force_export_type_options_not_found,
                "dialog_blob_excerpt": extract_export_type_dialog_blob(type_blob)[:1500],
            },
        )

        type_evidence_for_close = find_export_evidence_words(type_blob) if type_blob else evidence_words
        close_entries = after_type.get("entries", select_entries) if after_type.get("ok") else select_entries
        closed, close_method, p6_rect = close_export_dialog(
            evidence,
            p6_keyword,
            p6_rect,
            config,
            screen_rule,
            close_entries,
            type_evidence_for_close if type_blob else evidence_words,
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
                    export_type_screen_detected_flag=export_type_screen_ok,
                    export_type_evidence_words=type_evidence_words,
                    export_type_options_detected=export_type_options,
                    export_dialog_closed=closed,
                    close_method_used=close_method,
                    finish_pressed=finish_pressed_in_steps(evidence.steps),
                    export_file_created_flag=export_file_created(
                        export_snap_before, snapshot_export_files()
                    ),
                    finish_button_detected=buttons["finish_button_detected"],
                    cancel_button_detected=buttons["cancel_button_detected"],
                    manual_review_required=True,
                )

        file_created = export_file_created(export_snap_before, snapshot_export_files())
        finish_pressed = finish_pressed_in_steps(evidence.steps)
        next_pressed_count = max(next_pressed_count, count_next_in_steps(evidence.steps))
        type_selected = export_type_selected_in_steps(evidence.steps)
        next_after_type = next_pressed_after_export_type(evidence.steps)

        status, reason = decide_status(
            wizard_detected=wizard_detected,
            spreadsheet_detected=spreadsheet_detected,
            spreadsheet_selected=spreadsheet_selected,
            next_pressed_count=next_pressed_count,
            export_type_screen_ok=export_type_screen_ok,
            export_type_options=export_type_options,
            export_type_selected=type_selected,
            next_after_type=next_after_type,
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
            next_pressed_count=next_pressed_count,
            export_type_screen_detected_flag=export_type_screen_ok,
            export_type_evidence_words=type_evidence_words,
            export_type_options_detected=export_type_options,
            export_type_selected=type_selected,
            next_pressed_after_export_type=next_after_type,
            finish_button_detected=buttons["finish_button_detected"],
            finish_pressed=finish_pressed,
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
    parser = argparse.ArgumentParser(description="M19 Discover Spreadsheet Export Type Options")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    args = parser.parse_args()
    result = run_m19(args.project.strip())
    print(f"M19 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Export wizard detected: {result.get('export_wizard_detected')}")
    print(f"Spreadsheet option selected: {result.get('spreadsheet_option_selected')}")
    print(f"Next pressed count: {result.get('next_pressed_count', 0)}")
    print(f"Export Type screen detected: {result.get('export_type_screen_detected')}")
    print(f"Export Type options detected: {result.get('export_type_options_detected', [])}")
    print(f"Export Type option count: {result.get('export_type_option_count', 0)}")
    print(f"Finish pressed: {result.get('finish_pressed')}")
    print(f"Export dialog closed: {result.get('export_dialog_closed')}")
    print(f"Export file created: {result.get('export_file_created')}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")
    if result["status"] in ("PASS_EXPORT_TYPE_DISCOVERY", "PASS_EXPORT_TYPE_DISCOVERY_PARTIAL"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
