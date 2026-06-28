"""Build M19 hard 6-test matrix summary."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PASS_TARGET_MIN = 5
PASS_TARGET_EXCELLENT = 6

PASS_DISCOVERY = frozenset(
    {"PASS_EXPORT_TYPE_DISCOVERY", "PASS_EXPORT_TYPE_DISCOVERY_PARTIAL"}
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
    finish_pressed = sum(1 for r in results if r.get("status") == "FINISH_PRESSED")
    next_multi = sum(1 for r in results if r.get("status") == "NEXT_PRESSED_MORE_THAN_ONCE")
    next_after_type = sum(1 for r in results if r.get("status") == "NEXT_PRESSED_AFTER_EXPORT_TYPE")
    type_selected = sum(1 for r in results if r.get("status") == "EXPORT_TYPE_SELECTED")
    unsafe = sum(1 for r in results if r.get("status") == "UNSAFE_ACTION")
    dialog_open = sum(1 for r in results if r.get("status") == "DIALOG_LEFT_OPEN")
    fullscreen = sum(1 for r in results if r.get("status") == "FULL_SCREEN_OCR")
    wrong_format = sum(1 for r in results if r.get("status") == "WRONG_FORMAT_SELECTED")
    scored = sum(1 for r in results if int(r.get("score", 0)) == 1)
    failed = len(results) - scored

    discovery_success = [
        r
        for r in results
        if r.get("m19_status") in PASS_DISCOVERY and int(r.get("score", 0)) == 1
    ]

    stable = (
        total_score >= PASS_TARGET_MIN
        and crashes == 0
        and false_pass == 0
        and export_created == 0
        and finish_pressed == 0
        and next_multi == 0
        and next_after_type == 0
        and type_selected == 0
        and unsafe == 0
        and dialog_open == 0
        and fullscreen == 0
        and wrong_format == 0
    )

    problems: List[str] = []
    if crashes:
        problems.append(f"{crashes} crash(es)")
    if false_pass:
        problems.append(f"{false_pass} false PASS case(s)")
    if export_created:
        problems.append(f"{export_created} export file created case(s)")
    if finish_pressed:
        problems.append(f"{finish_pressed} Finish pressed case(s)")
    if next_multi:
        problems.append(f"{next_multi} Next pressed more than once case(s)")
    if next_after_type:
        problems.append(f"{next_after_type} Next pressed after Export Type case(s)")
    if type_selected:
        problems.append(f"{type_selected} export type selected case(s)")
    if unsafe:
        problems.append(f"{unsafe} unsafe action case(s)")
    if dialog_open:
        problems.append(f"{dialog_open} dialog left open case(s)")
    if fullscreen:
        problems.append(f"{fullscreen} full-screen OCR case(s)")
    if wrong_format:
        problems.append(f"{wrong_format} wrong format selected case(s)")
    if total_score < PASS_TARGET_MIN:
        problems.append(f"Score {total_score}/6 below minimum {PASS_TARGET_MIN}")
    while len(problems) < 3:
        problems.append("None significant")

    fixes: List[str] = []
    if export_created or finish_pressed or next_multi or next_after_type or type_selected:
        fixes.append("Audit M19 Export Type discovery close path and Next/Finish guards")
    if dialog_open:
        fixes.append("Verify Esc/Cancel closes export wizard completely after Export Type screen")
    if wrong_format:
        fixes.append("Tighten Spreadsheet-only OCR click targeting")
    if false_pass:
        fixes.append("Tighten M19 hard-test scoring gates")
    if not fixes:
        fixes.append("Monitor with TY_TEST_M19_HARD_6.bat")
        fixes.append("Human sign-off before next module")
        fixes.append("Keep M19 discovery-only constraints enabled")
    while len(fixes) < 3:
        fixes.append("Re-run hard matrix after P6 layout changes")

    decision = "M19 STABLE" if stable else "M19 NEEDS FIX"
    next_rec = "READY FOR NEXT MODULE" if stable else "FIX M19 AGAIN"

    pass_full = sum(
        1 for r in discovery_success if r.get("m19_status") == "PASS_EXPORT_TYPE_DISCOVERY"
    )
    pass_partial = sum(
        1
        for r in discovery_success
        if r.get("m19_status") == "PASS_EXPORT_TYPE_DISCOVERY_PARTIAL"
    )

    text_examples: List[str] = []
    next_counts: List[int] = []
    type_words_union: List[str] = []
    type_options_union: List[str] = []
    for r in discovery_success:
        text = r.get("spreadsheet_option_text", "")
        if text and text not in text_examples:
            text_examples.append(text)
        npc = int(r.get("next_pressed_count", 0))
        if npc not in next_counts:
            next_counts.append(npc)
        for w in r.get("export_type_evidence_words", []) or []:
            if w not in type_words_union:
                type_words_union.append(w)
        for o in r.get("export_type_options_detected", []) or []:
            if o not in type_options_union:
                type_options_union.append(o)

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
    ss_detected = sum(1 for r in results if r.get("spreadsheet_option_detected"))
    ss_selected = sum(1 for r in results if r.get("spreadsheet_option_selected"))
    type_screen_count = sum(1 for r in results if r.get("export_type_screen_detected"))
    type_selected_count = sum(1 for r in results if r.get("export_type_selected"))
    next_after_count = sum(1 for r in results if r.get("next_pressed_after_export_type"))
    finish_detected = sum(1 for r in results if r.get("finish_button_detected"))
    finish_pressed_count = sum(1 for r in results if r.get("finish_pressed"))
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
        "finish_pressed_cases": finish_pressed,
        "next_pressed_more_than_once_cases": next_multi,
        "next_pressed_after_export_type_cases": next_after_type,
        "export_type_selected_cases": type_selected,
        "unsafe_actions": unsafe,
        "dialog_left_open_cases": dialog_open,
        "fullscreen_ocr_cases": fullscreen,
        "wrong_format_selected_cases": wrong_format,
        "final_score": total_score,
        "max_score": len(results),
        "percentage": round(100.0 * total_score / len(results), 1) if results else 0,
        "decision": decision,
        "next_recommendation": next_rec,
        "spreadsheet_export_type_evidence": {
            "successful_discovery_tests": len(discovery_success),
            "pass_export_type_discovery_count": pass_full,
            "pass_export_type_discovery_partial_count": pass_partial,
            "spreadsheet_option_detected_count": ss_detected,
            "spreadsheet_option_selected_count": ss_selected,
            "spreadsheet_option_text_examples": text_examples[:10],
            "next_pressed_count_evidence": next_counts,
            "export_type_screen_detected_count": type_screen_count,
            "export_type_evidence_words": type_words_union,
            "export_type_options_detected": type_options_union,
            "export_type_option_count": len(type_options_union),
            "export_type_selected_count": type_selected_count,
            "next_pressed_after_export_type_count": next_after_count,
            "finish_button_detected_count": finish_detected,
            "finish_pressed_count": finish_pressed_count,
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

    json_path = run_root / "m19_hard_test_6_summary.json"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    de = summary["spreadsheet_export_type_evidence"]
    md_lines = [
        "# M19 Hard Testing Summary",
        "",
        f"- Run ID: {run_id}",
        f"- Project: {project}",
        f"- Total tests: {len(results)}",
        f"- Passed/scored: {scored}",
        f"- Failed/unscored: {failed}",
        f"- Crashes: {crashes}",
        f"- False PASS cases: {false_pass}",
        f"- Export file created cases: {export_created}",
        f"- Finish pressed cases: {finish_pressed}",
        f"- Next pressed more than once cases: {next_multi}",
        f"- Next pressed after Export Type cases: {next_after_type}",
        f"- Export Type selected cases: {type_selected}",
        f"- Unsafe actions: {unsafe}",
        f"- Dialog left open cases: {dialog_open}",
        f"- Full-screen OCR cases: {fullscreen}",
        f"- Wrong format selected cases: {wrong_format}",
        f"- Final score: {total_score} / {len(results)}",
        f"- Percentage: {summary['percentage']}%",
        f"- Decision: {decision}",
        "",
        "## Per-test result",
    ]
    for r in results:
        md_lines.append(
            f"- {r.get('test_id')} {r.get('test_name')}: score={r.get('score')} "
            f"status={r.get('status')} m19={r.get('m19_status')} reason={r.get('score_reason', '')}"
        )

    md_lines.extend(
        [
            "",
            "## Spreadsheet Export Type discovery evidence",
            f"- Successful discovery tests: {de['successful_discovery_tests']}",
            f"- PASS_EXPORT_TYPE_DISCOVERY count: {de['pass_export_type_discovery_count']}",
            f"- PASS_EXPORT_TYPE_DISCOVERY_PARTIAL count: {de['pass_export_type_discovery_partial_count']}",
            f"- Spreadsheet option detected: {de['spreadsheet_option_detected_count']}",
            f"- Spreadsheet option selected: {de['spreadsheet_option_selected_count']}",
            f"- Spreadsheet option text examples: {de['spreadsheet_option_text_examples']}",
            f"- Next pressed count evidence: {de['next_pressed_count_evidence']}",
            f"- Export Type screen detected: {de['export_type_screen_detected_count']}",
            f"- Export Type evidence words: {de['export_type_evidence_words']}",
            f"- Export Type options detected: {de['export_type_options_detected']}",
            f"- Export Type option count: {de['export_type_option_count']}",
            f"- Export Type selected: {de['export_type_selected_count']}",
            f"- Next pressed after Export Type: {de['next_pressed_after_export_type_count']}",
            f"- Finish detected: {de['finish_button_detected_count']}",
            f"- Finish pressed: {de['finish_pressed_count']}",
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
            str(run_root / "m19_hard_test_6_summary.md"),
        ]
    )
    md_path = run_root / "m19_hard_test_6_summary.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return summary
