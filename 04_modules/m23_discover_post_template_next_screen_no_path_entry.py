"""
M23 — Discover Post-Template Next Screen (No Path Entry).

If default template confirmed, press Next once from template screen, OCR path/output
screen evidence, then cancel. No browse, path typing, or Finish.
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
from eye.ocr import is_easyocr_available, normalize_text  # noqa: E402
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
    confirm_default_template_on_screen,
    count_next_after_marker,
    count_next_presses,
    find_post_template_evidence_words,
    open_to_template_screen,
    post_template_screen_detected,
    prepare_project_activities,
    press_next_from_template_screen,
    template_screen_detected,
    unsafe_steps_detected,
)

MODULE_NAME = "m23_discover_post_template_next_screen_no_path_entry"

PASS_STATUSES = frozenset(
    {"PASS_POST_TEMPLATE_NEXT_DISCOVERY", "PASS_POST_TEMPLATE_NEXT_DISCOVERY_PARTIAL"}
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
        "FAIL_DEFAULT_TEMPLATE_NOT_FOUND",
        "FAIL_POST_TEMPLATE_NEXT_SCREEN_NOT_FOUND",
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


def path_screen_ok(blob: str, evidence_words: List[str]) -> bool:
    if post_template_screen_detected(blob):
        return True
    if len(evidence_words) >= 2:
        return True
    norm = normalize_text(blob)
    return any(m in norm for m in ("file name", "output file", "export file", "browse"))


def path_entry_steps_detected(steps: List[str]) -> bool:
    forbidden = ("browse", "type path", "type file", "finish", "press_key(\"finish\")")
    for step in steps:
        lowered = step.lower()
        if any(f in lowered for f in forbidden):
            return True
    return False


def decide_status(
    *,
    wizard_detected: bool,
    spreadsheet_detected: bool,
    export_type_screen_ok: bool,
    activities_selected: bool,
    template_ok: bool,
    default_ok: bool,
    next_pressed_count: int,
    post_ok: bool,
    post_evidence_count: int,
    dialog_closed: bool,
    file_created: bool,
    finish_pressed: bool,
    blocking_after: bool,
    path_entry_attempted: bool,
    next_after_post: bool,
) -> Tuple[str, str]:
    if file_created or finish_pressed or path_entry_attempted:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Export file, Finish, or path entry may have occurred"
    if blocking_after:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Blocking popup after close attempt"
    if next_after_post:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Next pressed after post-template screen"
    if next_pressed_count > 3:
        return "MANUAL_REVIEW_UNSAFE_POPUP", f"Next pressed {next_pressed_count} times (max 3 allowed)"
    if not wizard_detected:
        return "FAIL_EXPORT_WIZARD_NOT_FOUND", "Export wizard not opened"
    if not spreadsheet_detected:
        return "FAIL_SPREADSHEET_OPTION_NOT_FOUND", "Spreadsheet option not found"
    if not export_type_screen_ok:
        return "FAIL_EXPORT_TYPE_SCREEN_NOT_FOUND", "Export Type screen not reached"
    if not activities_selected:
        return "FAIL_ACTIVITIES_EXPORT_TYPE_NOT_FOUND", "Activities export type not confirmed"
    if not template_ok:
        return "FAIL_TEMPLATE_SCREEN_NOT_FOUND", "Template screen not reached"
    if not default_ok:
        return "FAIL_DEFAULT_TEMPLATE_NOT_FOUND", "Default template not confirmed before Next"
    if next_pressed_count < 3:
        return "FAIL_POST_TEMPLATE_NEXT_SCREEN_NOT_FOUND", "Third Next (after template) not confirmed"
    if not post_ok:
        return "FAIL_POST_TEMPLATE_NEXT_SCREEN_NOT_FOUND", "Post-template path screen not confirmed"
    if dialog_closed and post_evidence_count >= 3:
        return (
            "PASS_POST_TEMPLATE_NEXT_DISCOVERY",
            f"Post-template path screen discovered ({post_evidence_count} evidence words); wizard closed",
        )
    if dialog_closed and post_evidence_count >= 1:
        return (
            "PASS_POST_TEMPLATE_NEXT_DISCOVERY_PARTIAL",
            f"Partial post-template discovery ({post_evidence_count} evidence words); wizard closed",
        )
    return "FAIL_POST_TEMPLATE_NEXT_SCREEN_NOT_FOUND", "Post-template evidence or safe close not confirmed"


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
        "# M23 Post-Template Next Screen Discovery Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Next pressed count: {result.get('next_pressed_count', 0)}",
        f"- Default template detected: {result.get('default_template_detected')}",
        f"- Post-template screen detected: {result.get('post_template_screen_detected')}",
        f"- Post-template evidence words: {result.get('post_template_evidence_words', [])}",
        f"- Export dialog closed: {result.get('export_dialog_closed')}",
        f"- Export file created: {result.get('export_file_created')}",
        "",
        "## Final decision",
        result["status"],
    ]
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_m23(
    project_name: str,
    *,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
    force_post_template_next_screen_not_found: bool = False,
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
        template_entries = ctx.get("post_activities_entries", [])
        template_ok = template_screen_detected(template_blob)

        default_ok, default_excerpt, focus_text, p6_rect, template_blob, template_entries = (
            confirm_default_template_on_screen(
                evidence,
                p6_keyword,
                p6_rect,
                config,
                screen_rule,
                min_confidence,
                template_blob,
                template_entries,
            )
        )
        if not default_ok:
            return finish_result(
                evidence,
                project_name,
                "FAIL_DEFAULT_TEMPLATE_NOT_FOUND",
                "Default template not confirmed before post-template Next",
                window_title_before=window_title_before,
                screen_state_before=screen_state_before,
                export_wizard_detected=ctx.get("wizard_detected", False),
                spreadsheet_option_detected=ctx.get("spreadsheet_detected", False),
                export_type_screen_detected=ctx.get("export_type_screen_ok", False),
                activities_export_type_selected=ctx.get("activities_selected", False),
                template_screen_detected=template_ok,
                default_template_detected=False,
            )

        p6_rect, post_blob, post_entries, next_err = press_next_from_template_screen(
            evidence, p6_keyword, p6_rect, config, screen_rule, min_confidence, template_entries
        )
        if next_err:
            return finish_result(
                evidence,
                project_name,
                next_err["status"],
                next_err.get("reason", ""),
                window_title_before=window_title_before,
                screen_state_before=screen_state_before,
                export_wizard_detected=True,
                default_template_detected=True,
                default_template_excerpt=default_excerpt,
            )

        post_words = find_post_template_evidence_words(post_blob)
        post_ok = path_screen_ok(post_blob, post_words)

        if force_post_template_next_screen_not_found:
            post_ok = False
            post_words = []

        save_discovery(
            evidence,
            "post_template_discovery.json",
            {
                "post_template_blob_excerpt": post_blob[:500],
                "post_template_evidence_words": post_words,
                "post_template_screen_detected": post_template_screen_detected(post_blob),
                "default_template_excerpt": default_excerpt,
                "template_focus_click_text": focus_text,
            },
        )

        buttons = detect_wizard_buttons(post_blob)
        evidence_words = find_export_evidence_words(post_blob)
        closed, close_method, p6_rect = close_export_dialog(
            evidence, p6_keyword, p6_rect, config, screen_rule, post_entries, evidence_words
        )

        after_close = capture_and_ocr_step(evidence, "09_after_close", p6_rect, config, screen_rule)
        blocking_after, _ = (
            detect_m16_blocking_popup(after_close.get("entries", []), min_confidence)
            if after_close.get("ok")
            else (False, "")
        )
        file_created = export_file_created(export_snap_before, snapshot_export_files())
        finish_pressed = finish_pressed_in_steps(evidence.steps)
        next_count = count_next_presses(evidence.steps)
        safe_steps, _ = unsafe_steps_detected(evidence.steps)
        path_entry = path_entry_steps_detected(evidence.steps)

        status, reason = decide_status(
            wizard_detected=ctx.get("wizard_detected", False),
            spreadsheet_detected=ctx.get("spreadsheet_detected", False),
            export_type_screen_ok=ctx.get("export_type_screen_ok", False),
            activities_selected=ctx.get("activities_selected", False),
            template_ok=template_ok,
            default_ok=default_ok,
            next_pressed_count=next_count,
            post_ok=post_ok,
            post_evidence_count=len(post_words),
            dialog_closed=closed,
            file_created=file_created,
            finish_pressed=finish_pressed,
            blocking_after=blocking_after,
            path_entry_attempted=path_entry,
            next_after_post=count_next_after_marker(evidence.steps, "after template") > 1,
        )
        if not safe_steps:
            status, reason = "MANUAL_REVIEW_UNSAFE_POPUP", "Unsafe step detected in M23 run"

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
            next_pressed_count=next_count,
            template_screen_detected=template_ok,
            default_template_detected=default_ok,
            default_template_excerpt=default_excerpt,
            template_focus_click_text=focus_text,
            post_template_screen_detected=post_ok,
            post_template_evidence_words=post_words,
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
    parser = argparse.ArgumentParser(description="M23 Post-template next screen discovery")
    parser.add_argument("--project", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--force-post-template-next-screen-not-found", action="store_true")
    args = parser.parse_args()
    result = run_m23(
        args.project,
        run_id=args.run_id,
        force_post_template_next_screen_not_found=args.force_post_template_next_screen_not_found,
    )
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in PASS_STATUSES else 1)


if __name__ == "__main__":
    main()
