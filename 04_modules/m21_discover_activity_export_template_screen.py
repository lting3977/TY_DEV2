"""
M21 — Discover Activity Export Template Screen.

Spreadsheet/XLSX -> Next -> Activities -> Next once -> OCR template screen evidence, then cancel.
No template selection, path entry, or Finish.
"""

from __future__ import annotations

import argparse
import json
import sys
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
    capture_and_ocr_step,
    load_json,
    write_json,
)
from eye.ocr import is_easyocr_available  # noqa: E402
from accessibility.hand import window_tools  # noqa: E402
from m16_discover_p6_export_menu import (  # noqa: E402
    close_export_dialog,
    detect_m16_blocking_popup,
    export_file_created,
    find_export_evidence_words,
    snapshot_export_files,
)
from m18_select_spreadsheet_export_format_discovery_only import (  # noqa: E402
    detect_wizard_buttons,
    finish_pressed_in_steps,
)
from export_wizard_common import (  # noqa: E402
    count_next_after_marker,
    count_next_presses,
    find_template_evidence_words,
    open_to_template_screen,
    prepare_project_activities,
    template_screen_detected,
    unsafe_steps_detected,
)

MODULE_NAME = "m21_discover_activity_export_template_screen"

PASS_STATUSES = frozenset(
    {"PASS_TEMPLATE_SCREEN_DISCOVERY", "PASS_TEMPLATE_SCREEN_DISCOVERY_PARTIAL"}
)

ALLOWED_STATUSES = frozenset(
    {
        *PASS_STATUSES,
        "FAIL_PROJECT_NAME_EMPTY",
        "ERROR",
        "FAIL_P6_WINDOW_NOT_READY",
        "FAIL_PROJECT_NOT_OPEN",
        "FAIL_ACTIVITIES_NOT_FOUND",
        "MANUAL_REVIEW_CANNOT_CONFIRM",
        "MANUAL_REVIEW_UNSAFE_POPUP",
        "FAIL_EXPORT_WIZARD_NOT_FOUND",
        "FAIL_SPREADSHEET_OPTION_NOT_FOUND",
        "FAIL_EXPORT_TYPE_SCREEN_NOT_FOUND",
        "FAIL_ACTIVITIES_EXPORT_TYPE_NOT_FOUND",
        "FAIL_TEMPLATE_SCREEN_NOT_FOUND",
    }
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


def template_screen_ok(blob: str, evidence_words: List[str]) -> bool:
    if template_screen_detected(blob):
        return True
    if len(evidence_words) >= 3 and any(w in evidence_words for w in ("template", "select template")):
        return True
    return False


def decide_status(
    *,
    wizard_detected: bool,
    spreadsheet_detected: bool,
    export_type_screen_ok: bool,
    activities_selected: bool,
    next_pressed_count: int,
    template_ok: bool,
    template_evidence_count: int,
    dialog_closed: bool,
    file_created: bool,
    finish_pressed: bool,
    blocking_after: bool,
    next_after_template: bool,
) -> Tuple[str, str]:
    if file_created or finish_pressed:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Export file may have been created or Finish pressed"
    if blocking_after:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Blocking popup after close attempt"
    if next_after_template:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Next pressed after template screen discovery"
    if next_pressed_count > 2:
        return "MANUAL_REVIEW_UNSAFE_POPUP", f"Next pressed {next_pressed_count} times (max 2 allowed)"
    if not wizard_detected:
        return "FAIL_EXPORT_WIZARD_NOT_FOUND", "Export wizard not opened"
    if not spreadsheet_detected:
        return "FAIL_SPREADSHEET_OPTION_NOT_FOUND", "Spreadsheet option not found"
    if not export_type_screen_ok:
        return "FAIL_EXPORT_TYPE_SCREEN_NOT_FOUND", "Export Type screen not reached"
    if not activities_selected:
        return "FAIL_ACTIVITIES_EXPORT_TYPE_NOT_FOUND", "Activities export type not confirmed"
    if next_pressed_count < 2:
        return "FAIL_TEMPLATE_SCREEN_NOT_FOUND", "Second Next (after Activities) not confirmed"
    if not template_ok:
        return "FAIL_TEMPLATE_SCREEN_NOT_FOUND", "Template screen not confirmed"
    if dialog_closed and template_evidence_count >= 3:
        return (
            "PASS_TEMPLATE_SCREEN_DISCOVERY",
            f"Template screen discovered ({template_evidence_count} evidence words); wizard closed",
        )
    if dialog_closed and template_evidence_count >= 1:
        return (
            "PASS_TEMPLATE_SCREEN_DISCOVERY_PARTIAL",
            f"Partial template screen discovery ({template_evidence_count} evidence words); wizard closed",
        )
    return "FAIL_TEMPLATE_SCREEN_NOT_FOUND", "Template evidence or safe close not confirmed"


def finish_result(evidence: RunEvidence, project_name: str, status: str, reason: str, **fields: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "run_id": evidence.run_id,
        "module": MODULE_NAME,
        "project_name": project_name,
        "status": status,
        "reason": reason,
        "allowed_statuses": sorted(ALLOWED_STATUSES),
        "screenshots": evidence.screenshots,
        "ocr_files": evidence.ocr_files,
        "classification_files": evidence.classification_files,
        "popup_files": evidence.popup_files,
        "discovery_files": evidence.discovery_files,
        "steps": evidence.steps,
    }
    result.update(fields)
    write_json(evidence.folder / "result.json", result)
    write_report(evidence, result)
    return result


def write_report(evidence: RunEvidence, result: Dict[str, Any]) -> None:
    lines = [
        "# M21 Activity Export Template Screen Discovery Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Next pressed count: {result.get('next_pressed_count', 0)}",
        f"- Activities selected: {result.get('activities_export_type_selected')}",
        f"- Template screen detected: {result.get('template_screen_detected')}",
        f"- Template evidence words: {result.get('template_evidence_words', [])}",
        f"- Export dialog closed: {result.get('export_dialog_closed')}",
        f"- Export file created: {result.get('export_file_created')}",
        "",
        "## Final decision",
        result["status"],
    ]
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_m21(
    project_name: str,
    *,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
    force_activities_export_type_not_found: bool = False,
    force_template_screen_not_found: bool = False,
) -> Dict[str, Any]:
    evidence = evidence or build_evidence(run_id or new_run_id())
    config = load_json(CONFIG_PATH)
    screen_rule = load_json(SCREEN_RULE_PATH)
    p6_keyword = config["p6_window_title_keyword"]
    min_confidence = float(config.get("min_ocr_confidence", 0.5))

    project_name = (project_name or "").strip()
    if not project_name:
        return finish_result(evidence, "", "FAIL_PROJECT_NAME_EMPTY", "project_name is empty")

    if not is_easyocr_available():
        return finish_result(evidence, project_name, "ERROR", "EasyOCR not installed", error="pip install easyocr")

    export_snap_before = snapshot_export_files()
    window_title_before = ""
    screen_state_before = ""

    try:
        p6_rect, window_title_before, screen_state_before, prep_err = prepare_project_activities(
            evidence, project_name, p6_keyword, config, screen_rule, min_confidence
        )
        if prep_err:
            return finish_result(evidence, project_name, prep_err["status"], prep_err.get("reason", ""), **{
                k: v for k, v in prep_err.items() if k not in ("status", "reason")
            })

        if force_activities_export_type_not_found:
            return finish_result(
                evidence,
                project_name,
                "FAIL_ACTIVITIES_EXPORT_TYPE_NOT_FOUND",
                "Hook: force_activities_export_type_not_found",
                window_title_before=window_title_before,
                screen_state_before=screen_state_before,
                export_wizard_detected=True,
                spreadsheet_option_detected=True,
                export_type_screen_detected=True,
            )

        p6_rect, ctx, path_err = open_to_template_screen(
            evidence, p6_keyword, p6_rect, config, screen_rule, min_confidence
        )
        if path_err:
            return finish_result(
                evidence,
                project_name,
                path_err["status"],
                path_err.get("reason", ""),
                window_title_before=window_title_before,
                screen_state_before=screen_state_before,
                export_wizard_detected=ctx.get("wizard_detected", False),
                spreadsheet_option_detected=ctx.get("spreadsheet_detected", False),
                export_type_screen_detected=ctx.get("export_type_screen_ok", False),
                activities_export_type_selected=ctx.get("activities_selected", False),
            )

        template_blob = ctx.get("post_activities_blob", "")
        template_words = find_template_evidence_words(template_blob)
        template_ok = template_screen_ok(template_blob, template_words)

        if force_template_screen_not_found:
            template_ok = False
            template_words = []

        save_discovery(
            evidence,
            "template_screen_discovery.json",
            {
                "template_blob_excerpt": template_blob[:500],
                "template_evidence_words": template_words,
                "template_screen_detected": template_screen_detected(template_blob),
            },
        )

        buttons = detect_wizard_buttons(template_blob)
        after_entries = ctx.get("post_activities_entries", [])
        evidence_words = find_export_evidence_words(template_blob)
        closed, close_method, p6_rect = close_export_dialog(
            evidence, p6_keyword, p6_rect, config, screen_rule, after_entries, evidence_words
        )

        after_close = capture_and_ocr_step(evidence, "08_after_close", p6_rect, config, screen_rule)
        blocking_after, _ = (
            detect_m16_blocking_popup(after_close.get("entries", []), min_confidence)
            if after_close.get("ok")
            else (False, "")
        )
        file_created = export_file_created(export_snap_before, snapshot_export_files())
        finish_pressed = finish_pressed_in_steps(evidence.steps)
        next_count = count_next_presses(evidence.steps)
        safe_steps, _ = unsafe_steps_detected(evidence.steps)

        status, reason = decide_status(
            wizard_detected=ctx.get("wizard_detected", False),
            spreadsheet_detected=ctx.get("spreadsheet_detected", False),
            export_type_screen_ok=ctx.get("export_type_screen_ok", False),
            activities_selected=ctx.get("activities_selected", False),
            next_pressed_count=next_count,
            template_ok=template_ok,
            template_evidence_count=len(template_words),
            dialog_closed=closed,
            file_created=file_created,
            finish_pressed=finish_pressed,
            blocking_after=blocking_after,
            next_after_template=count_next_after_marker(evidence.steps, "after template") > 0,
        )
        if not safe_steps:
            status, reason = "MANUAL_REVIEW_UNSAFE_POPUP", "Unsafe step detected in M21 run"

        return finish_result(
            evidence,
            project_name,
            status,
            reason,
            window_title_before=window_title_before,
            window_title_after=window_tools.get_window_state(p6_keyword).get("title") or "",
            screen_state_before=screen_state_before,
            screen_state_after=after_close.get("screen_state", "unknown") if after_close.get("ok") else "unknown",
            export_wizard_detected=ctx.get("wizard_detected", False),
            spreadsheet_option_detected=ctx.get("spreadsheet_detected", False),
            export_type_screen_detected=ctx.get("export_type_screen_ok", False),
            activities_export_type_selected=ctx.get("activities_selected", False),
            activities_click_text=ctx.get("activities_click_text", ""),
            next_pressed_count=next_count,
            template_screen_detected=template_ok,
            template_evidence_words=template_words,
            finish_button_detected=buttons.get("finish_button_detected", False),
            finish_pressed=finish_pressed,
            cancel_button_detected=buttons.get("cancel_button_detected", False),
            export_dialog_closed=closed,
            close_method_used=close_method,
            export_file_created=file_created,
            manual_review_required=status.startswith("MANUAL_REVIEW"),
        )
    except Exception as exc:  # noqa: BLE001
        return finish_result(
            evidence,
            project_name,
            "ERROR",
            str(exc),
            error=traceback.format_exc(),
            window_title_before=window_title_before,
            screen_state_before=screen_state_before,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="M21 Activity export template screen discovery")
    parser.add_argument("--project", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--force-activities-export-type-not-found", action="store_true")
    parser.add_argument("--force-template-screen-not-found", action="store_true")
    args = parser.parse_args()
    result = run_m21(
        args.project,
        run_id=args.run_id,
        force_activities_export_type_not_found=args.force_activities_export_type_not_found,
        force_template_screen_not_found=args.force_template_screen_not_found,
    )
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in PASS_STATUSES else 1)


if __name__ == "__main__":
    main()
