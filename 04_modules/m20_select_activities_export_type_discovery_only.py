"""
M20 — Select Activities Export Type Discovery Only.

Spreadsheet/XLSX -> Next -> select Activities -> Next once -> inspect post-Activities
screen, then cancel safely. No template selection, path entry, or Finish.

Use --diagnostic for single-pass checkpoint tracing with hard timeouts.
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
    confirms_activities_workspace,
    load_json,
    write_json,
)
from eye.ocr import collect_text_blob, is_easyocr_available, normalize_text  # noqa: E402
from eye.screenshot import P6Rect  # noqa: E402
from accessibility.hand import window_tools  # noqa: E402
from m16_discover_p6_export_menu import (  # noqa: E402
    close_export_dialog,
    detect_m16_blocking_popup,
    export_dialog_detected,
    export_file_created,
    find_export_evidence_words,
    refresh_p6_rect,
    snapshot_export_files,
)
from m18_select_spreadsheet_export_format_discovery_only import (  # noqa: E402
    detect_wizard_buttons,
    finish_pressed_in_steps,
)
from export_wizard_common import (  # noqa: E402
    count_next_after_marker,
    count_next_presses,
    find_post_template_evidence_words,
    find_template_evidence_words,
    m20_controlled_wizard_to_post_activities,
    m20_preflight_reset_before_export,
    m20_run_diagnostic,
    post_template_screen_detected,
    template_screen_detected,
    unsafe_steps_detected,
)

MODULE_NAME = "m20_select_activities_export_type_discovery_only"

PASS_STATUSES = frozenset(
    {"PASS_ACTIVITIES_NEXT_DISCOVERY", "PASS_ACTIVITIES_NEXT_DISCOVERY_PARTIAL"}
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
    diagnostic_dir: Optional[Path] = None
    steps: List[str] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    ocr_files: List[str] = field(default_factory=list)
    classification_files: List[str] = field(default_factory=list)
    popup_files: List[str] = field(default_factory=list)
    discovery_files: List[str] = field(default_factory=list)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_evidence(run_id: str, *, diagnostic: bool = False) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / run_id / MODULE_NAME
    for sub in ("screenshots", "ocr", "classification", "popup", "discovery"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    diagnostic_dir = None
    if diagnostic:
        diagnostic_dir = folder / "diagnostic"
        diagnostic_dir.mkdir(parents=True, exist_ok=True)
    return RunEvidence(
        run_id=run_id,
        folder=folder,
        screenshots_dir=folder / "screenshots",
        ocr_dir=folder / "ocr",
        classification_dir=folder / "classification",
        popup_dir=folder / "popup",
        discovery_dir=folder / "discovery",
        diagnostic_dir=diagnostic_dir,
    )


def save_discovery(evidence: RunEvidence, filename: str, payload: Dict[str, Any]) -> str:
    path = evidence.discovery_dir / filename
    write_json(path, payload)
    evidence.discovery_files.append(str(path))
    return str(path)


def post_activities_screen_ok(blob: str, evidence_words: List[str]) -> bool:
    if template_screen_detected(blob) or post_template_screen_detected(blob):
        return True
    norm = normalize_text(blob)
    if any(m in norm for m in ("select template", "modify template", "file name", "output file", "browse")):
        return True
    if "activity name" in norm and ("layout:" in norm or "wbs filter" in norm):
        return False
    if "export type" in norm and "data to export" in norm:
        return False
    return False


def decide_status(
    *,
    wizard_detected: bool,
    spreadsheet_detected: bool,
    export_type_screen_ok: bool,
    activities_selected: bool,
    next_pressed_count: int,
    post_screen_ok: bool,
    post_evidence_count: int,
    dialog_closed: bool,
    file_created: bool,
    finish_pressed: bool,
    blocking_after: bool,
    next_after_post: bool,
    wizard_closed_unexpectedly: bool = False,
    post_activities_screen_type: str = "unknown",
    post_classification_status: str = "",
    post_classification_reason: str = "",
) -> Tuple[str, str]:
    if wizard_closed_unexpectedly:
        return "FAIL_WIZARD_CLOSED_UNEXPECTEDLY", "Export wizard closed before intentional Cancel"
    if file_created or finish_pressed:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Export file may have been created or Finish pressed"
    if blocking_after:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Blocking popup after close attempt"
    if next_after_post:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Next pressed after post-Activities screen"
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
        return "FAIL_ACTIVITIES_NEXT_SCREEN_NOT_FOUND", "Second Next (after Activities) not confirmed"
    if post_classification_status == "FAIL_WIZARD_CLOSED_UNEXPECTEDLY":
        return post_classification_status, post_classification_reason or "Export wizard closed unexpectedly"
    if post_classification_status == "FAIL_ACTIVITIES_NEXT_SCREEN_NOT_FOUND":
        return post_classification_status, post_classification_reason or "Post-Activities screen not confirmed"
    if not post_screen_ok:
        return "FAIL_ACTIVITIES_NEXT_SCREEN_NOT_FOUND", "Post-Activities screen not confirmed"
    if post_classification_status == "PASS_ACTIVITIES_NEXT_DISCOVERY" and dialog_closed:
        return post_classification_status, post_classification_reason or (
            f"Post-Activities {post_activities_screen_type} screen discovered; wizard closed"
        )
    if post_classification_status == "PASS_ACTIVITIES_NEXT_DISCOVERY_PARTIAL" and dialog_closed:
        return post_classification_status, post_classification_reason or (
            f"Partial post-Activities discovery ({post_evidence_count} evidence words); wizard closed"
        )
    if dialog_closed and post_activities_screen_type in ("projects_to_export", "template", "file_path"):
        return (
            "PASS_ACTIVITIES_NEXT_DISCOVERY",
            post_classification_reason
            or f"Post-Activities {post_activities_screen_type} screen discovered; wizard closed",
        )
    if dialog_closed and post_activities_screen_type == "generic_wizard":
        return (
            "PASS_ACTIVITIES_NEXT_DISCOVERY_PARTIAL",
            f"Partial post-Activities discovery ({post_evidence_count} evidence words); wizard closed",
        )
    if dialog_closed and post_evidence_count >= 3:
        return (
            "PASS_ACTIVITIES_NEXT_DISCOVERY",
            f"Post-Activities screen discovered ({post_evidence_count} evidence words); wizard closed",
        )
    if dialog_closed and post_evidence_count >= 1:
        return (
            "PASS_ACTIVITIES_NEXT_DISCOVERY_PARTIAL",
            f"Partial post-Activities discovery ({post_evidence_count} evidence words); wizard closed",
        )
    return "FAIL_ACTIVITIES_NEXT_SCREEN_NOT_FOUND", "Post-Activities evidence or safe close not confirmed"


def diagnostic_recommendation(status: str, failed_checkpoint: str, diag: Dict[str, Any]) -> str:
    if status in PASS_STATUSES:
        return "M20 wizard flow is working; promote diagnostic findings to standard run."
    if status == "FAIL_TIMEOUT_CONTROLLED":
        return "Reduce OCR scope or pre-warm EasyOCR; run timed out before completing checkpoints."
    if status == "FAIL_WIZARD_CLOSED_UNEXPECTEDLY":
        if failed_checkpoint in ("checkpoint_04_activities_selected", "checkpoint_06_post_activities_screen"):
            return "Activities click or second Next likely hit wrong bbox or closed wizard; inspect all_click_targets.json vs screenshots."
        return "Wizard closed unexpectedly; verify Cancel/Next bbox alignment in wizard chrome."
    if status == "FAIL_STEP_ACTIVITIES_CLICK_UNCONFIRMED":
        return "M20 NEEDS TARGET PATCH — Activities OCR bbox may be menu/toolbar row not export-type list row."
    if status.startswith("FAIL_STEP_"):
        return f"M20 NEEDS TARGET PATCH — failed at {failed_checkpoint}; inspect checkpoint OCR and click bbox."
    if status == "MANUAL_REVIEW_CANNOT_CONFIRM":
        return "M20 NEEDS MANUAL SCREEN REVIEW — OCR pollution or ambiguous screen state."
    if status == "FAIL_ACTIVITIES_NEXT_SCREEN_NOT_FOUND":
        return "Wizard stayed open but post-Activities screen markers missing; review checkpoint_06 OCR."
    return "M20 NEEDS MANUAL SCREEN REVIEW — inspect diagnostic checkpoints."


def finish_result(evidence: RunEvidence, project_name: str, status: str, reason: str, **fields: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "run_id": evidence.run_id,
        "module": MODULE_NAME,
        "project_name": project_name,
        "status": status,
        "reason": reason,
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
    if result.get("diagnostic_mode"):
        lines = [
            "# M20 Diagnostic Report",
            "",
            f"- Run ID: {result['run_id']}",
            f"- Status: {result['status']}",
            f"- Failed checkpoint: {result.get('failed_checkpoint', '')}",
            f"- Last successful checkpoint: {result.get('last_successful_checkpoint', '')}",
            f"- Next pressed count: {result.get('next_pressed_count_total', 0)}",
            f"- Finish pressed: {result.get('finish_pressed', False)}",
            f"- Export file created: {result.get('export_file_created', False)}",
            f"- Wizard closed unexpectedly: {result.get('wizard_closed_unexpectedly', False)}",
        f"- EasyOCR prewarmed: {result.get('easyocr_prewarmed', False)}",
        f"- OCR mode after CP01: {result.get('ocr_mode_after_cp01', '')}",
        f"- Diagnostic duration (s): {result.get('diagnostic_duration_seconds', 0)}",
        f"- Dialog closed: {result.get('dialog_closed', False)}",
            "",
            "## Click targets used",
            json.dumps(result.get("click_targets", []), indent=2),
            "",
            "## OCR evidence by checkpoint",
            f"- Diagnostic trace: {result.get('diagnostic_trace_file', '')}",
            f"- All click targets: {result.get('all_click_targets_file', '')}",
            f"- Post-Activities evidence words: {result.get('post_activities_evidence_words', [])}",
            "",
            "## Final recommendation",
            result.get("final_recommendation", diagnostic_recommendation(
                result["status"],
                result.get("failed_checkpoint", ""),
                result,
            )),
        ]
    else:
        lines = [
            "# M20 Select Activities Export Type Discovery Report",
            "",
            f"- Run ID: {result['run_id']}",
            f"- Project: {result.get('project_name', '')}",
            f"- Status: {result['status']}",
            f"- Reason: {result['reason']}",
            f"- Next pressed count: {result.get('next_pressed_count', 0)}",
            f"- Activities selected: {result.get('activities_export_type_selected')}",
            f"- Post-Activities screen type: {result.get('post_activities_screen_type', 'unknown')}",
            f"- Post-Activities screen: {result.get('post_activities_screen_detected')}",
            f"- Post evidence words: {result.get('post_activities_evidence_words', [])}",
            f"- Export dialog closed: {result.get('export_dialog_closed')}",
            f"- Export file created: {result.get('export_file_created')}",
            "",
            "## Final decision",
            result["status"],
        ]
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_m20_diagnostic(
    project_name: str,
    *,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
    diagnostic_max_sec: int = 180,
    ui_wait_sec: int = 8,
) -> Dict[str, Any]:
    evidence = evidence or build_evidence(run_id or new_run_id(), diagnostic=True)
    if evidence.diagnostic_dir is None:
        evidence.diagnostic_dir = evidence.folder / "diagnostic"
        evidence.diagnostic_dir.mkdir(parents=True, exist_ok=True)

    config = load_json(CONFIG_PATH)
    screen_rule = load_json(SCREEN_RULE_PATH)
    p6_keyword = config["p6_window_title_keyword"]
    min_confidence = float(config.get("min_ocr_confidence", 0.5))

    project_name = (project_name or "").strip()
    if not project_name:
        return finish_result(evidence, "", "FAIL_PROJECT_NAME_EMPTY", "project_name is empty", diagnostic_mode=True)

    if not is_easyocr_available():
        return finish_result(
            evidence, project_name, "ERROR", "EasyOCR not installed",
            diagnostic_mode=True, error="pip install easyocr",
        )

    try:
        diag = m20_run_diagnostic(
            evidence,
            project_name,
            p6_keyword,
            config,
            screen_rule,
            min_confidence,
            evidence.diagnostic_dir,
            diagnostic_max_sec=diagnostic_max_sec,
            ui_wait_sec=ui_wait_sec,
        )
        status = diag.get("status", "ERROR")
        reason = diag.get("reason", "")
        recommendation = diagnostic_recommendation(status, diag.get("failed_checkpoint", ""), diag)

        return finish_result(
            evidence,
            project_name,
            status,
            reason,
            diagnostic_mode=True,
            failed_checkpoint=diag.get("failed_checkpoint", ""),
            last_successful_checkpoint=diag.get("last_successful_checkpoint", ""),
            next_pressed_count_total=diag.get("next_pressed_count_total", 0),
            next_pressed_count=diag.get("next_pressed_count_total", 0),
            finish_pressed=diag.get("finish_pressed", False),
            export_file_created=diag.get("export_file_created", False),
            wizard_closed_unexpectedly=diag.get("wizard_closed_unexpectedly", False),
            final_screen_state=diag.get("final_screen_state", "unknown"),
            diagnostic_trace_file=diag.get("diagnostic_trace_file", ""),
            all_click_targets_file=diag.get("all_click_targets_file", ""),
            click_targets=diag.get("click_targets", []),
            post_activities_evidence_words=diag.get("post_activities_evidence_words", []),
            spreadsheet_target_bbox=diag.get("spreadsheet_target_bbox"),
            first_next_bbox=diag.get("first_next_bbox"),
            activities_target_bbox=diag.get("activities_target_bbox"),
            second_next_bbox=diag.get("second_next_bbox"),
            activities_option_text=diag.get("activities_option_text", ""),
            activities_candidates=diag.get("activities_candidates", []),
            screen_after_activities_click=diag.get("screen_after_activities_click", ""),
            screen_after_second_next=diag.get("screen_after_second_next", ""),
            post_activities_screen_detected=diag.get("post_activities_screen_detected", False),
            easyocr_prewarmed=diag.get("easyocr_prewarmed", False),
            ocr_mode_after_cp01=diag.get("ocr_mode_after_cp01", ""),
            diagnostic_duration_seconds=diag.get("diagnostic_duration_seconds", 0),
            wizard_bounds_cached=diag.get("wizard_bounds_cached"),
            dialog_closed=diag.get("dialog_closed", False),
            final_recommendation=recommendation,
            manual_review_required=status.startswith("MANUAL_REVIEW"),
        )
    except Exception as exc:  # noqa: BLE001
        return finish_result(
            evidence,
            project_name,
            "ERROR",
            str(exc),
            diagnostic_mode=True,
            error=traceback.format_exc(),
        )


def run_m20(
    project_name: str,
    *,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
    diagnostic: bool = False,
    diagnostic_max_sec: int = 180,
    ui_wait_sec: int = 8,
    force_activities_export_type_not_found: bool = False,
    force_post_activities_next_screen_not_found: bool = False,
    force_post_activities_screen_not_found_after_second_next: bool = False,
) -> Dict[str, Any]:
    if diagnostic:
        return run_m20_diagnostic(
            project_name,
            evidence=evidence,
            run_id=run_id,
            diagnostic_max_sec=diagnostic_max_sec,
            ui_wait_sec=ui_wait_sec,
        )

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
    preflight_meta: Dict[str, Any] = {}

    if force_activities_export_type_not_found:
        evidence.steps.append("Hook: force_activities_export_type_not_found")
    if force_post_activities_screen_not_found_after_second_next:
        evidence.steps.append("Hook: force_post_activities_screen_not_found_after_second_next (armed)")
    elif force_post_activities_next_screen_not_found:
        evidence.steps.append("Hook: force_post_activities_next_screen_not_found (legacy)")

    try:
        p6_rect, window_title_before, screen_state_before, preflight_meta, prep_err = (
            m20_preflight_reset_before_export(
                evidence, project_name, p6_keyword, config, screen_rule, min_confidence
            )
        )
        if prep_err:
            return finish_result(evidence, project_name, prep_err["status"], prep_err.get("reason", ""), **{
                k: v for k, v in prep_err.items() if k not in ("status", "reason")
            }, **preflight_meta)

        use_late_hook = force_post_activities_screen_not_found_after_second_next or force_post_activities_next_screen_not_found
        p6_rect, ctx, path_err = m20_controlled_wizard_to_post_activities(
            evidence,
            p6_keyword,
            p6_rect,
            config,
            screen_rule,
            min_confidence,
            project_name,
            force_post_activities_screen_not_found_after_second_next=use_late_hook,
        )
        if path_err:
            err_fields = {
                "window_title_before": window_title_before,
                "screen_state_before": screen_state_before,
                "export_wizard_detected": ctx.get("wizard_detected", False),
                "spreadsheet_option_detected": ctx.get("spreadsheet_detected", False),
                "pollution_detected": ctx.get("pollution_detected", False),
                "pollution_recovered": ctx.get("pollution_recovered", False),
                "wizard_closed_unexpectedly": ctx.get("wizard_closed_unexpectedly", False),
            }
            if path_err.get("status") == "FAIL_WIZARD_CLOSED_UNEXPECTEDLY":
                err_fields["wizard_closed_unexpectedly"] = True
            return finish_result(
                evidence,
                project_name,
                path_err["status"],
                path_err.get("reason", ""),
                **err_fields,
                **{
                    k: v
                    for k, v in preflight_meta.items()
                    if k not in ("pollution_detected", "pollution_recovered")
                },
            )

        if force_activities_export_type_not_found:
            post_entries = ctx.get("post_activities_entries") or ctx.get("export_type_entries", [])
            post_blob = ctx.get("post_activities_blob") or ctx.get("export_type_blob", "")
            evidence_words = find_export_evidence_words(post_blob)
            closed, close_method, p6_rect = close_export_dialog(
                evidence,
                p6_keyword,
                p6_rect,
                config,
                screen_rule,
                post_entries,
                evidence_words,
            )
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
                activities_export_type_selected=False,
                next_pressed_count=count_next_presses(evidence.steps),
                export_dialog_closed=closed,
                close_method_used=close_method,
                finish_pressed=finish_pressed_in_steps(evidence.steps),
                export_file_created=export_file_created(export_snap_before, snapshot_export_files()),
                **{
                    k: v
                    for k, v in preflight_meta.items()
                    if k not in ("pollution_detected", "pollution_recovered")
                },
            )

        post_blob = ctx.get("post_activities_blob", "")
        post_entries = ctx.get("post_activities_entries", [])
        post_words = ctx.get("post_activities_evidence_words") or []
        post_type = ctx.get("post_activities_screen_type", "unknown")
        post_ok = bool(ctx.get("post_screen_ok", False))
        post_class_status = ctx.get("post_activities_classification_status", "")
        post_class_reason = ctx.get("post_activities_classification_reason", "")
        in_activities_after_next, _ = confirms_activities_workspace(post_entries, min_confidence)
        returned_to_activities = in_activities_after_next and post_type == "unknown"

        activities_selected = ctx.get("activities_selected", False) or bool(ctx.get("activities_click_text"))

        save_discovery(
            evidence,
            "post_activities_discovery.json",
            {
                "post_activities_blob_excerpt": post_blob[:500],
                "post_activities_evidence_words": post_words,
                "post_activities_screen_type": post_type,
                "template_screen_detected": post_type == "template",
                "post_template_screen_detected": post_type == "file_path",
            },
        )

        buttons = detect_wizard_buttons(post_blob)
        after_entries = post_entries
        evidence_words = find_export_evidence_words(post_blob)

        if returned_to_activities:
            evidence.steps.append(
                "post-Activities capture shows Activities workspace — wizard not on template screen"
            )
            closed = True
            close_method = "returned_to_activities_workspace"
        else:
            closed, close_method, p6_rect = close_export_dialog(
                evidence, p6_keyword, p6_rect, config, screen_rule, after_entries, evidence_words
            )
            if closed and close_method == "none_dialog_not_open":
                close_method = "wizard_already_closed"

        after_close = capture_and_ocr_step(evidence, "08_after_close", p6_rect, config, screen_rule)
        blocking_after, blocking_reason = (
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
            activities_selected=activities_selected,
            next_pressed_count=next_count,
            post_screen_ok=post_ok,
            post_evidence_count=len(post_words),
            dialog_closed=closed,
            file_created=file_created,
            finish_pressed=finish_pressed,
            blocking_after=blocking_after,
            next_after_post=count_next_after_marker(evidence.steps, "after activities") > 0,
            wizard_closed_unexpectedly=ctx.get("wizard_closed_unexpectedly", False),
            post_activities_screen_type=post_type,
            post_classification_status=post_class_status,
            post_classification_reason=post_class_reason,
        )
        if not safe_steps:
            status, reason = "MANUAL_REVIEW_UNSAFE_POPUP", "Unsafe step detected in M20 run"

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
            spreadsheet_option_selected=ctx.get("spreadsheet_selected", False),
            export_type_screen_detected=ctx.get("export_type_screen_ok", False),
            activities_export_type_selected=activities_selected,
            activities_export_type_detected=activities_selected,
            activities_click_text=ctx.get("activities_click_text", ""),
            activities_option_bbox=ctx.get("activities_option_bbox"),
            first_next_clicked_by_ocr_bbox=ctx.get("first_next_clicked_by_ocr_bbox", False),
            second_next_clicked_by_ocr_bbox=ctx.get("second_next_clicked_by_ocr_bbox", False),
            wizard_still_open_after_activities_click=ctx.get("wizard_still_open_after_activities_click", False),
            wizard_closed_unexpectedly=ctx.get("wizard_closed_unexpectedly", False),
            pollution_detected=ctx.get("pollution_detected", False) or preflight_meta.get("p6_pollution_detected", False),
            pollution_recovered=ctx.get("pollution_recovered", False) or preflight_meta.get("pollution_recovered", False),
            next_pressed_count=next_count,
            post_activities_screen_type=post_type,
            post_activities_screen_detected=post_ok,
            post_activities_evidence_words=post_words,
            finish_button_detected=buttons.get("finish_button_detected", False),
            finish_pressed=finish_pressed,
            cancel_button_detected=buttons.get("cancel_button_detected", False),
            export_dialog_closed=closed,
            close_method_used=close_method,
            export_file_created=file_created,
            manual_review_required=status.startswith("MANUAL_REVIEW"),
            forced_hook_activation=ctx.get("forced_hook_activation"),
            export_open_attempt=ctx.get("export_open_attempt"),
            **{
                k: v
                for k, v in preflight_meta.items()
                if k not in ("pollution_detected", "pollution_recovered")
            },
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
    parser = argparse.ArgumentParser(description="M20 Activities export type discovery")
    parser.add_argument("--project", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--diagnostic", action="store_true", help="Single-pass checkpoint diagnostic mode")
    parser.add_argument("--diagnostic-max-sec", type=int, default=180)
    parser.add_argument("--ui-wait-sec", type=int, default=8)
    parser.add_argument("--force-activities-export-type-not-found", action="store_true")
    parser.add_argument("--force-post-activities-next-screen-not-found", action="store_true")
    parser.add_argument("--force-post-activities-screen-not-found-after-second-next", action="store_true")
    args = parser.parse_args()
    result = run_m20(
        args.project,
        run_id=args.run_id,
        diagnostic=args.diagnostic,
        diagnostic_max_sec=args.diagnostic_max_sec,
        ui_wait_sec=args.ui_wait_sec,
        force_activities_export_type_not_found=args.force_activities_export_type_not_found,
        force_post_activities_next_screen_not_found=args.force_post_activities_next_screen_not_found,
        force_post_activities_screen_not_found_after_second_next=(
            args.force_post_activities_screen_not_found_after_second_next
            or args.force_post_activities_next_screen_not_found
        ),
    )
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in PASS_STATUSES else 1)


if __name__ == "__main__":
    main()
