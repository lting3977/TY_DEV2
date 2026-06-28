"""
M17 — Discover Export Format Options (Phase 16).

Export-format discovery only: opens File > Export, OCR-reads available format
options on the first wizard screen, then safely cancels. Does not press Next,
Finish, or create export files.
"""

from __future__ import annotations

import argparse
import json
import re
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
    close_export_dialog,
    detect_m16_blocking_popup,
    export_dialog_detected,
    export_file_created,
    find_export_evidence_words,
    open_export_menu,
    refresh_p6_rect,
    snapshot_export_files,
)

MODULE_NAME = "m17_discover_export_format_options"

FORMAT_OPTION_DETECTORS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("XER", ("(xer)", " xer", "xer)", "contractor", "primavera contractor")),
    ("XML", ("(xml)", " xml", "xml)", "primavera p3", "uncefact", "ipmdar", "cpp format")),
    ("Spreadsheet", ("spreadsheet", "xlsx", "microsoft excel", "(xlsx)")),
    ("Microsoft Project", ("microsoft project", "project xml 2002", "project xml 2003")),
    ("Primavera PM", ("primavera pm", "ere 23", "pm 23")),
    ("Project", ("project export",)),
    ("Resources", ("resources only", "resource export", "export resources")),
    ("Roles", ("roles only", "role export", "export roles")),
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


def extract_raw_option_examples(blob: str) -> List[str]:
    """Pull raw OCR snippets that look like export format option lines."""
    norm = normalize_text(blob)
    keywords = (
        "xer",
        "xml",
        "spreadsheet",
        "xlsx",
        "microsoft project",
        "primavera contractor",
        "primavera p3",
        "primavera pm",
        "uncefact",
        "ipmdar",
        "cpp format",
        "export format",
    )
    examples: List[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        idx = 0
        while True:
            pos = norm.find(keyword, idx)
            if pos < 0:
                break
            start = max(0, pos - 40)
            end = min(len(norm), pos + len(keyword) + 60)
            snippet = norm[start:end].strip()
            if snippet and snippet not in seen:
                seen.add(snippet)
                examples.append(snippet)
            idx = pos + len(keyword)
    return examples[:20]


def detect_format_options(blob: str) -> List[str]:
    norm = normalize_text(blob)
    found: List[str] = []
    for name, patterns in FORMAT_OPTION_DETECTORS:
        if any(p in norm for p in patterns):
            found.append(name)
    return found


def detect_wizard_buttons(blob: str) -> Dict[str, bool]:
    norm = normalize_text(blob)
    tokens = set(re.split(r"[\s|;,]+", norm))
    return {
        "next_button_detected": "next" in tokens or "next" in norm,
        "finish_button_detected": "finish" in tokens or "finish" in norm,
        "cancel_button_detected": "cancel" in tokens or "cancel" in norm,
    }


def next_or_finish_in_steps(steps: List[str]) -> Tuple[bool, bool]:
    blob = " ".join(steps).lower()
    next_pressed = 'press_key("next")' in blob or "press_key('next')" in blob
    finish_pressed = 'press_key("finish")' in blob or "press_key('finish')" in blob
    return next_pressed, finish_pressed


def save_format_options(
    evidence: RunEvidence,
    payload: Dict[str, Any],
) -> str:
    path = evidence.discovery_dir / "export_format_options.json"
    write_json(path, payload)
    evidence.discovery_files.append(str(path))
    return str(path)


def decide_status(
    wizard_detected: bool,
    format_options: List[str],
    dialog_closed: bool,
    file_created: bool,
    blocking_after: bool,
) -> Tuple[str, str]:
    if file_created:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Export file may have been created — manual review required"
    if blocking_after:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Blocking confirmation popup after close attempt"
    if not wizard_detected:
        return "FAIL_EXPORT_WIZARD_NOT_FOUND", "File > Export did not open export wizard"
    if not format_options:
        return "FAIL_FORMAT_OPTIONS_NOT_FOUND", "Export wizard opened but no format options detected"
    if len(format_options) >= 2 and dialog_closed:
        return (
            "PASS_FORMAT_DISCOVERY",
            f"Detected {len(format_options)} export format option(s); wizard safely closed",
        )
    if len(format_options) >= 1 and dialog_closed:
        return (
            "PASS_FORMAT_DISCOVERY_PARTIAL",
            f"Partial format discovery: {len(format_options)} option(s); wizard safely closed",
        )
    return "FAIL_FORMAT_OPTIONS_NOT_FOUND", "Format options not confirmed or wizard not closed safely"


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
    format_options_detected: Optional[List[str]] = None,
    next_button_detected: bool = False,
    finish_button_detected: bool = False,
    cancel_button_detected: bool = False,
    next_pressed: bool = False,
    finish_pressed: bool = False,
    export_dialog_closed: bool = False,
    close_method_used: str = "",
    export_file_created_flag: bool = False,
    manual_review_required: bool = False,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    options = format_options_detected or []
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
        "format_options_detected": options,
        "format_option_count": len(options),
        "next_button_detected": next_button_detected,
        "finish_button_detected": finish_button_detected,
        "cancel_button_detected": cancel_button_detected,
        "next_pressed": next_pressed,
        "finish_pressed": finish_pressed,
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
            discovery_summary = json.dumps(load_json(Path(path)), indent=2)
        except Exception:  # noqa: BLE001
            discovery_summary = path

    lines = [
        "# M17 Discover Export Format Options Report",
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
        f"- Format options detected: {result.get('format_options_detected', [])}",
        f"- Format option count: {result.get('format_option_count', 0)}",
        f"- Next button detected: {result.get('next_button_detected')}",
        f"- Finish button detected: {result.get('finish_button_detected')}",
        f"- Cancel button detected: {result.get('cancel_button_detected')}",
        f"- Next pressed: {result.get('next_pressed')}",
        f"- Finish pressed: {result.get('finish_pressed')}",
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
    if result["status"] in ("PASS_FORMAT_DISCOVERY", "PASS_FORMAT_DISCOVERY_PARTIAL"):
        lines.append("Ready for M17 hard testing.")
    elif result["status"].startswith("MANUAL_REVIEW"):
        lines.append("Review screenshots and popup evidence before retrying.")
    else:
        lines.append("Fix the reported issue and re-run TY_TEST_M17_DISCOVER_EXPORT_FORMATS.bat")
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_m17(
    project_name: str,
    *,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
    block_activities_navigation: bool = False,
    force_skip_export_open: bool = False,
    force_no_format_options: bool = False,
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
            if block_activities_navigation:
                evidence.steps.append("block_activities_navigation: skip M06-style navigation")
                return finish_result(
                    evidence,
                    project_name,
                    "FAIL_ACTIVITIES_NOT_FOUND",
                    "Activities workspace not confirmed; navigation blocked for hard test",
                    window_title_before=window_title_before,
                    screen_state_before=screen_state_before,
                )
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

        if force_skip_export_open:
            evidence.steps.append("force_skip_export_open: hard test mode — export wizard not opened")
            return finish_result(
                evidence,
                project_name,
                "FAIL_EXPORT_WIZARD_NOT_FOUND",
                "Export wizard blocked for hard test; File > Export not opened",
                window_title_before=window_title_before,
                window_title_after=window_tools.get_window_state(p6_keyword).get("title") or "",
                screen_state_before=screen_state_before,
                screen_state_after=screen_state_before,
                export_wizard_detected=False,
                format_options_detected=[],
                export_dialog_closed=True,
                close_method_used="none_blocked",
                export_file_created_flag=export_file_created(
                    export_snap_before, snapshot_export_files()
                ),
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

        blob = collect_text_blob(after_wizard["entries"], min_confidence)
        evidence_words = find_export_evidence_words(blob)
        wizard_detected = export_dialog_detected(evidence_words) or "export format" in normalize_text(blob)
        format_options = detect_format_options(blob)
        raw_option_examples = extract_raw_option_examples(blob)
        buttons = detect_wizard_buttons(blob)

        if force_no_format_options:
            evidence.steps.append("force_no_format_options: hard test mode — format options suppressed")
            format_options = []
            raw_option_examples = []

        save_format_options(
            evidence,
            {
                "export_wizard_detected": wizard_detected,
                "format_options_detected": format_options,
                "format_option_count": len(format_options),
                "raw_option_examples": raw_option_examples,
                "export_evidence_words": evidence_words,
                "wizard_buttons": buttons,
                "ocr_blob_excerpt": blob[:2500],
                "screen_state": after_wizard.get("screen_state", ""),
                "classification": after_wizard.get("classification", {}),
                "detection_method": "ocr_only_first_screen",
                "force_no_format_options": force_no_format_options,
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
                format_options_detected=format_options,
                export_dialog_closed=closed,
                close_method_used=close_method,
                export_file_created_flag=export_file_created(export_snap_before, snapshot_export_files()),
                **buttons,
            )

        closed, close_method, p6_rect = close_export_dialog(
            evidence,
            p6_keyword,
            p6_rect,
            config,
            screen_rule,
            after_wizard["entries"],
            evidence_words,
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
                next_p, finish_p = next_or_finish_in_steps(evidence.steps)
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
                    format_options_detected=format_options,
                    export_dialog_closed=closed,
                    close_method_used=close_method,
                    next_pressed=next_p,
                    finish_pressed=finish_p,
                    export_file_created_flag=export_file_created(
                        export_snap_before, snapshot_export_files()
                    ),
                    manual_review_required=True,
                    **buttons,
                )

        file_created = export_file_created(export_snap_before, snapshot_export_files())
        next_p, finish_p = next_or_finish_in_steps(evidence.steps)
        status, reason = decide_status(
            wizard_detected,
            format_options,
            closed,
            file_created,
            blocking_after,
        )

        if next_p or finish_p:
            status = "MANUAL_REVIEW_UNSAFE_POPUP"
            reason = "Next or Finish was pressed during format discovery"

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
            format_options_detected=format_options,
            export_dialog_closed=closed,
            close_method_used=close_method,
            next_pressed=next_p,
            finish_pressed=finish_p,
            export_file_created_flag=file_created,
            manual_review_required=status.startswith("MANUAL_REVIEW"),
            **buttons,
        )

    except Exception as exc:  # noqa: BLE001
        evidence.steps.append(f"exception: {exc}")
        evidence.steps.append(traceback.format_exc())
        return finish_result(
            evidence,
            project_name,
            "ERROR",
            str(exc),
            export_file_created_flag=export_file_created(
                export_snap_before, snapshot_export_files()
            ),
            error=traceback.format_exc(),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="M17 Discover Export Format Options")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    args = parser.parse_args()
    result = run_m17(args.project.strip())
    print(f"M17 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Export wizard detected: {result.get('export_wizard_detected')}")
    print(f"Format options detected: {result.get('format_options_detected', [])}")
    print(f"Format option count: {result.get('format_option_count', 0)}")
    print(f"Next pressed: {result.get('next_pressed')}")
    print(f"Finish pressed: {result.get('finish_pressed')}")
    print(f"Export dialog closed: {result.get('export_dialog_closed')}")
    print(f"Export file created: {result.get('export_file_created')}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")
    if result["status"] in ("PASS_FORMAT_DISCOVERY", "PASS_FORMAT_DISCOVERY_PARTIAL"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
