"""Build M17 hard 6-test matrix summary."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PASS_TARGET_MIN = 5
PASS_TARGET_EXCELLENT = 6

PASS_DISCOVERY = frozenset({"PASS_FORMAT_DISCOVERY", "PASS_FORMAT_DISCOVERY_PARTIAL"})
CANONICAL_FORMAT_OPTIONS = frozenset(
    {"XER", "XML", "Spreadsheet", "Microsoft Project", "Primavera PM"}
)


def write_hard_summary(
    run_id: str,
    run_root: Path,
    results: List[Dict[str, Any]],
    project: str,
) -> Dict[str, Any]:
    total_score = sum(int(r.get("score", 0)) for r in results)
    crashes = sum(1 for r in results if r.get("status") == "CRASH")
    false_pass = sum(1 for r in results if r.get("status") == "FALSE_PASS")
    export_created = sum(1 for r in results if r.get("status") == "EXPORT_FILE_CREATED")
    next_finish = sum(1 for r in results if r.get("status") == "NEXT_OR_FINISH_PRESSED")
    unsafe = sum(1 for r in results if r.get("status") == "UNSAFE_ACTION")
    dialog_open = sum(1 for r in results if r.get("status") == "DIALOG_LEFT_OPEN")
    fullscreen = sum(1 for r in results if r.get("status") == "FULL_SCREEN_OCR")
    option_click = sum(1 for r in results if r.get("status") == "OPTION_SELECTED_UNNECESSARILY")
    scored = sum(1 for r in results if int(r.get("score", 0)) == 1)
    failed = len(results) - scored

    discovery_success = [
        r
        for r in results
        if r.get("m17_status") in PASS_DISCOVERY and int(r.get("score", 0)) == 1
    ]

    stable = (
        total_score >= PASS_TARGET_MIN
        and crashes == 0
        and false_pass == 0
        and export_created == 0
        and next_finish == 0
        and unsafe == 0
        and dialog_open == 0
        and fullscreen == 0
        and option_click == 0
    )

    problems: List[str] = []
    if crashes:
        problems.append(f"{crashes} crash(es)")
    if false_pass:
        problems.append(f"{false_pass} false PASS case(s)")
    if export_created:
        problems.append(f"{export_created} export file created case(s)")
    if next_finish:
        problems.append(f"{next_finish} Next/Finish pressed case(s)")
    if unsafe:
        problems.append(f"{unsafe} unsafe action case(s)")
    if dialog_open:
        problems.append(f"{dialog_open} dialog left open case(s)")
    if fullscreen:
        problems.append(f"{fullscreen} full-screen OCR case(s)")
    if option_click:
        problems.append(f"{option_click} unnecessary option selection case(s)")
    if total_score < PASS_TARGET_MIN:
        problems.append(f"Score {total_score}/6 below minimum {PASS_TARGET_MIN}")
    while len(problems) < 3:
        problems.append("None significant")

    fixes: List[str] = []
    if export_created or next_finish or unsafe:
        fixes.append("Audit M17 format discovery close path and forbidden key guards")
    if dialog_open:
        fixes.append("Verify Esc/Cancel closes export wizard completely")
    if false_pass:
        fixes.append("Tighten M17 hard-test scoring gates")
    if option_click:
        fixes.append("Ensure M17 does not click format options during OCR-only discovery")
    if not fixes:
        fixes.append("Monitor with TY_TEST_M17_HARD_6.bat")
        fixes.append("Human sign-off before next module")
        fixes.append("Keep M17 discovery-only constraints enabled")
    while len(fixes) < 3:
        fixes.append("Re-run hard matrix after P6 layout changes")

    decision = "M17 STABLE" if stable else "M17 NEEDS FIX"
    next_rec = "READY FOR NEXT MODULE" if stable else "FIX M17 AGAIN"

    pass_full = sum(
        1 for r in discovery_success if r.get("m17_status") == "PASS_FORMAT_DISCOVERY"
    )
    pass_partial = sum(
        1 for r in discovery_success if r.get("m17_status") == "PASS_FORMAT_DISCOVERY_PARTIAL"
    )

    canonical_union: List[str] = []
    raw_examples_union: List[str] = []
    for r in discovery_success:
        for opt in r.get("format_options_detected", []) or []:
            if opt not in canonical_union:
                canonical_union.append(opt)
        for raw in r.get("raw_option_examples", []) or []:
            if raw not in raw_examples_union:
                raw_examples_union.append(raw)

    close_methods = sorted(
        {r.get("close_method_used", "") for r in results if r.get("close_method_used")}
    )
    closed_count = sum(1 for r in results if r.get("export_dialog_closed"))
    files_created = sum(1 for r in results if r.get("export_file_created"))
    returned_ok = sum(
        1
        for r in discovery_success
        if (r.get("screen_state_after") or "").startswith("activities")
        or "talison" in (r.get("window_title_after") or "").lower()
    )
    next_detected = sum(1 for r in results if r.get("next_button_detected"))
    finish_detected = sum(1 for r in results if r.get("finish_button_detected"))
    cancel_detected = sum(1 for r in results if r.get("cancel_button_detected"))

    summary = {
        "run_id": run_id,
        "project": project,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_tests": len(results),
        "passed_scored": scored,
        "failed_unscored": failed,
        "crashes": crashes,
        "false_pass_cases": false_pass,
        "export_file_created_cases": export_created,
        "next_finish_pressed_cases": next_finish,
        "unsafe_actions": unsafe,
        "dialog_left_open_cases": dialog_open,
        "fullscreen_ocr_cases": fullscreen,
        "unnecessary_option_selection_cases": option_click,
        "final_score": total_score,
        "max_score": len(results),
        "percentage": round(100.0 * total_score / len(results), 1) if results else 0,
        "decision": decision,
        "next_recommendation": next_rec,
        "format_discovery_evidence": {
            "successful_discovery_tests": len(discovery_success),
            "pass_format_discovery_count": pass_full,
            "pass_format_discovery_partial_count": pass_partial,
            "format_options_detected": canonical_union,
            "canonical_option_labels_detected": [
                o for o in canonical_union if o in CANONICAL_FORMAT_OPTIONS
            ],
            "raw_option_examples": raw_examples_union[:15],
            "next_button_detected_count": next_detected,
            "finish_button_detected_count": finish_detected,
            "cancel_button_detected_count": cancel_detected,
            "close_methods_used": close_methods,
            "export_dialog_closed_count": closed_count,
            "export_files_created_count": files_created,
            "returned_to_activities_or_project_count": returned_ok,
        },
        "per_test_results": results,
        "top_issues": problems[:3],
        "fixes_applied": fixes[:3],
    }

    json_path = run_root / "m17_hard_test_6_summary.json"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    de = summary["format_discovery_evidence"]
    md_lines = [
        "# M17 Hard Testing Summary",
        "",
        f"- Run ID: {run_id}",
        f"- Project: {project}",
        f"- Total tests: {len(results)}",
        f"- Passed/scored: {scored}",
        f"- Failed/unscored: {failed}",
        f"- Crashes: {crashes}",
        f"- False PASS cases: {false_pass}",
        f"- Export file created cases: {export_created}",
        f"- Next/Finish pressed cases: {next_finish}",
        f"- Unsafe actions: {unsafe}",
        f"- Dialog left open cases: {dialog_open}",
        f"- Full-screen OCR cases: {fullscreen}",
        f"- Unnecessary option selection cases: {option_click}",
        f"- Final score: {total_score} / {len(results)}",
        f"- Percentage: {summary['percentage']}%",
        f"- Decision: {decision}",
        "",
        "## Per-test result",
    ]
    for r in results:
        md_lines.append(
            f"- {r.get('test_id')} {r.get('test_name')}: score={r.get('score')} "
            f"status={r.get('status')} m17={r.get('m17_status')} reason={r.get('score_reason', '')}"
        )

    md_lines.extend(
        [
            "",
            "## Export format discovery evidence",
            f"- Successful discovery tests: {de['successful_discovery_tests']}",
            f"- PASS_FORMAT_DISCOVERY count: {de['pass_format_discovery_count']}",
            f"- PASS_FORMAT_DISCOVERY_PARTIAL count: {de['pass_format_discovery_partial_count']}",
            f"- Format options detected: {de['format_options_detected']}",
            f"- Canonical option labels detected: {de['canonical_option_labels_detected']}",
            f"- Raw option examples: {de['raw_option_examples']}",
            f"- Next detected: {de['next_button_detected_count']}",
            f"- Finish detected: {de['finish_button_detected_count']}",
            f"- Cancel detected: {de['cancel_button_detected_count']}",
            f"- Close methods used: {de['close_methods_used']}",
            f"- Export dialog closed: {de['export_dialog_closed_count']}",
            f"- Export files created: {de['export_files_created_count']}",
            f"- Final screen returned to Activities/project window: {de['returned_to_activities_or_project_count']}",
            "",
            "## Top issues",
        ]
    )
    for idx, issue in enumerate(problems[:3], start=1):
        md_lines.append(f"{idx}. {issue}")
    md_lines.extend(["", "## Fixes applied"])
    for idx, fix in enumerate(fixes[:3], start=1):
        md_lines.append(f"{idx}. {fix}")
    md_lines.extend(
        [
            "",
            "## Next recommendation",
            next_rec,
            "",
            "## Evidence",
            str(json_path),
            str(run_root / "m17_hard_test_6_summary.md"),
        ]
    )
    md_path = run_root / "m17_hard_test_6_summary.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return summary
