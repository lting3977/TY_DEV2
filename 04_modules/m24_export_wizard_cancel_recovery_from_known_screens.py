"""
M24 — Export Wizard Cancel Recovery From Known Screens.

Opens the export wizard to a known screen depth (format, export_type, template,
post_template), cancels via close_export_dialog, and verifies safe recovery.
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
    export_dialog_detected,
    export_file_created,
    find_export_evidence_words,
    snapshot_export_files,
)
from m18_select_spreadsheet_export_format_discovery_only import (  # noqa: E402
    detect_wizard_buttons,
    finish_pressed_in_steps,
)
from export_wizard_common import (  # noqa: E402
    WIZARD_DEPTH_OPENERS,
    count_next_presses,
    open_wizard_to_export_type_screen,
    open_wizard_to_format_screen,
    open_wizard_to_post_template_screen,
    open_wizard_to_template_screen,
    prepare_project_activities,
    unsafe_steps_detected,
)

# Depth openers re-exported for orchestrators / hard-test matrices.
__all__ = [
    "run_m24",
    "open_wizard_to_format_screen",
    "open_wizard_to_export_type_screen",
    "open_wizard_to_template_screen",
    "open_wizard_to_post_template_screen",
    "VALID_SCREENS",
    "PASS_STATUSES",
    "ALLOWED_STATUSES",
]

MODULE_NAME = "m24_export_wizard_cancel_recovery_from_known_screens"

VALID_SCREENS = ("format", "export_type", "template", "post_template")

PASS_STATUSES = frozenset({"PASS_CANCEL_RECOVERY", "PASS_CANCEL_RECOVERY_PARTIAL"})

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
        "FAIL_NO_EXPORT_WIZARD_FOUND",
        "FAIL_DIALOG_STILL_OPEN",
        "FAIL_CANCEL_RECOVERY",
        "FAIL_INVALID_SCREEN_DEPTH",
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


def activities_like_state(screen_state: str, window_title: str) -> bool:
    state = (screen_state or "").lower()
    title = (window_title or "").lower()
    if state.startswith("activities"):
        return True
    return "primavera" in title or "talison" in title


def decide_status(
    *,
    screen_depth: str,
    target_reached: bool,
    wizard_detected: bool,
    dialog_closed: bool,
    close_method: str,
    file_created: bool,
    finish_pressed: bool,
    blocking_after: bool,
    returned_to_project: bool,
) -> Tuple[str, str]:
    if file_created or finish_pressed:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Export file may have been created or Finish pressed"
    if blocking_after:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Blocking popup after cancel recovery"
    if not target_reached:
        return "FAIL_CANCEL_RECOVERY", f"Could not reach {screen_depth} screen before cancel"
    if not wizard_detected:
        return "FAIL_EXPORT_WIZARD_NOT_FOUND", "Export wizard not detected at target depth"
    if not dialog_closed:
        return "FAIL_DIALOG_STILL_OPEN", "Export dialog not closed after cancel recovery"
    if returned_to_project and close_method in ("cancel_click", "esc", "alt_f4", "esc_or_partial_close"):
        return (
            "PASS_CANCEL_RECOVERY",
            f"Cancel recovery from {screen_depth} screen; dialog closed via {close_method}",
        )
    if dialog_closed and not file_created:
        return (
            "PASS_CANCEL_RECOVERY_PARTIAL",
            f"Partial cancel recovery from {screen_depth}; close_method={close_method}",
        )
    return "FAIL_CANCEL_RECOVERY", "Cancel recovery not confirmed"


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
        "# M24 Export Wizard Cancel Recovery Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project: {result.get('project_name', '')}",
        f"- Screen depth: {result.get('screen_depth', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Export wizard detected: {result.get('export_wizard_detected')}",
        f"- Export dialog closed: {result.get('export_dialog_closed')}",
        f"- Close method: {result.get('close_method_used', '')}",
        f"- Returned to project: {result.get('returned_to_project')}",
        f"- Export file created: {result.get('export_file_created')}",
        f"- Next pressed count: {result.get('next_pressed_count', 0)}",
        "",
        "## Final decision",
        result["status"],
    ]
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_m24(
    project_name: str,
    *,
    screen_depth: str = "format",
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
    force_cancel_recovery_fail: bool = False,
    skip_wizard_open: bool = False,
) -> Dict[str, Any]:
    evidence = evidence or build_evidence(run_id or new_run_id())
    config = load_json(CONFIG_PATH)
    screen_rule = load_json(SCREEN_RULE_PATH)
    p6_keyword = config["p6_window_title_keyword"]
    min_confidence = float(config.get("min_ocr_confidence", 0.5))

    project_name = (project_name or "").strip()
    if not project_name:
        return finish_result(evidence, "", "FAIL_PROJECT_NAME_EMPTY", "project_name is empty")

    screen_depth = (screen_depth or "format").strip().lower()
    if screen_depth not in VALID_SCREENS:
        return finish_result(
            evidence,
            project_name,
            "FAIL_INVALID_SCREEN_DEPTH",
            f"screen_depth must be one of {VALID_SCREENS}",
            screen_depth=screen_depth,
        )

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

        if skip_wizard_open:
            evidence.steps.append("hook: force_skip_export_open — skip opening export wizard")
            after = capture_and_ocr_step(evidence, "03_no_wizard", p6_rect, config, screen_rule)
            blob = ""
            entries: List[Dict[str, Any]] = []
            if after.get("ok"):
                from eye.ocr import collect_text_blob  # noqa: WPS433

                entries = after["entries"]
                blob = collect_text_blob(entries, min_confidence)
            evidence_words = find_export_evidence_words(blob) if blob else []
            wizard_detected = export_dialog_detected(evidence_words) if blob else False
            return finish_result(
                evidence,
                project_name,
                "FAIL_NO_EXPORT_WIZARD_FOUND",
                "Export wizard not open (skip_wizard_open hook)",
                screen_depth=screen_depth,
                window_title_before=window_title_before,
                window_title_after=window_tools.get_window_state(p6_keyword).get("title") or "",
                screen_state_before=screen_state_before,
                screen_state_after=after.get("screen_state", "unknown") if after.get("ok") else "unknown",
                export_wizard_detected=wizard_detected,
                target_screen_reached=False,
                export_dialog_closed=not wizard_detected,
            )

        opener = WIZARD_DEPTH_OPENERS[screen_depth]
        p6_rect, ctx, open_err = opener(
            evidence, p6_keyword, p6_rect, config, screen_rule, min_confidence
        )
        if open_err:
            return finish_result(
                evidence,
                project_name,
                open_err["status"],
                open_err.get("reason", ""),
                screen_depth=screen_depth,
                window_title_before=window_title_before,
                screen_state_before=screen_state_before,
                export_wizard_detected=ctx.get("wizard_detected", False),
                target_screen_reached=False,
            )

        if screen_depth == "format":
            blob = ctx.get("wizard_blob", "")
            entries = ctx.get("entries", [])
        elif screen_depth == "export_type":
            blob = ctx.get("export_type_blob", "")
            entries = ctx.get("export_type_entries", [])
        elif screen_depth == "template":
            blob = ctx.get("template_blob", ctx.get("post_activities_blob", ""))
            entries = ctx.get("template_entries", ctx.get("post_activities_entries", []))
        else:
            blob = ctx.get("post_template_blob", "")
            entries = ctx.get("post_template_entries", [])

        evidence_words = find_export_evidence_words(blob)
        wizard_detected = export_dialog_detected(evidence_words) or "export" in normalize_text(blob)
        buttons = detect_wizard_buttons(blob)

        save_discovery(
            evidence,
            "cancel_recovery_target.json",
            {
                "screen_depth": screen_depth,
                "wizard_detected": wizard_detected,
                "evidence_words": evidence_words,
                "blob_excerpt": blob[:500],
                "ctx_keys": sorted(ctx.keys()),
            },
        )

        closed = False
        close_method = ""
        if force_cancel_recovery_fail:
            closed = False
            close_method = "hook_blocked"
        else:
            closed, close_method, p6_rect = close_export_dialog(
                evidence, p6_keyword, p6_rect, config, screen_rule, entries, evidence_words
            )

        after_close = capture_and_ocr_step(evidence, "10_after_close", p6_rect, config, screen_rule)
        blocking_after, _ = (
            detect_m16_blocking_popup(after_close.get("entries", []), min_confidence)
            if after_close.get("ok")
            else (False, "")
        )
        file_created = export_file_created(export_snap_before, snapshot_export_files())
        finish_pressed = finish_pressed_in_steps(evidence.steps)
        next_count = count_next_presses(evidence.steps)
        safe_steps, _ = unsafe_steps_detected(evidence.steps)

        window_title_after = window_tools.get_window_state(p6_keyword).get("title") or ""
        screen_state_after = after_close.get("screen_state", "unknown") if after_close.get("ok") else "unknown"
        returned = activities_like_state(screen_state_after, window_title_after)

        status, reason = decide_status(
            screen_depth=screen_depth,
            target_reached=True,
            wizard_detected=wizard_detected,
            dialog_closed=closed,
            close_method=close_method,
            file_created=file_created,
            finish_pressed=finish_pressed,
            blocking_after=blocking_after,
            returned_to_project=returned,
        )
        if not safe_steps:
            status, reason = "MANUAL_REVIEW_UNSAFE_POPUP", "Unsafe step detected in M24 run"

        return finish_result(
            evidence,
            project_name,
            status,
            reason,
            screen_depth=screen_depth,
            window_title_before=window_title_before,
            window_title_after=window_title_after,
            screen_state_before=screen_state_before,
            screen_state_after=screen_state_after,
            export_wizard_detected=wizard_detected,
            target_screen_reached=True,
            export_dialog_closed=closed,
            close_method_used=close_method,
            returned_to_project=returned,
            next_pressed_count=next_count,
            finish_button_detected=buttons.get("finish_button_detected", False),
            finish_pressed=finish_pressed,
            cancel_button_detected=buttons.get("cancel_button_detected", False),
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
            screen_depth=screen_depth,
            window_title_before=window_title_before,
            screen_state_before=screen_state_before,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="M24 Export wizard cancel recovery")
    parser.add_argument("--project", required=True)
    parser.add_argument("--screen", default="format", choices=VALID_SCREENS)
    parser.add_argument("--run-id")
    parser.add_argument("--force-cancel-recovery-fail", action="store_true")
    args = parser.parse_args()
    result = run_m24(
        args.project,
        screen_depth=args.screen,
        run_id=args.run_id,
        force_cancel_recovery_fail=args.force_cancel_recovery_fail,
    )
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in PASS_STATUSES else 1)


if __name__ == "__main__":
    main()
