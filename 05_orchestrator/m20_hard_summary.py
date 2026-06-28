"""Build M20 hard 6-test matrix summary."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PASS_TARGET_MIN = 5
PASS_TARGET_EXCELLENT = 6

PASS_DISCOVERY = frozenset(
    {"PASS_ACTIVITIES_NEXT_DISCOVERY", "PASS_ACTIVITIES_NEXT_DISCOVERY_PARTIAL"}
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
    next_multi = sum(1 for r in results if r.get("status") == "NEXT_PRESSED_TOO_MANY")
    unsafe = sum(1 for r in results if r.get("status") == "UNSAFE_ACTION")
    dialog_open = sum(1 for r in results if r.get("status") == "DIALOG_LEFT_OPEN")
    fullscreen = sum(1 for r in results if r.get("status") == "FULL_SCREEN_OCR")
    scored = sum(1 for r in results if int(r.get("score", 0)) == 1)
    failed = len(results) - scored

    discovery_success = [
        r
        for r in results
        if r.get("m20_status") in PASS_DISCOVERY and int(r.get("score", 0)) == 1
    ]

    stable = (
        total_score >= PASS_TARGET_MIN
        and crashes == 0
        and false_pass == 0
        and export_created == 0
        and finish_pressed == 0
        and next_multi == 0
        and unsafe == 0
        and dialog_open == 0
        and fullscreen == 0
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
        problems.append(f"{next_multi} Next pressed too many times case(s)")
    if unsafe:
        problems.append(f"{unsafe} unsafe action case(s)")
    if dialog_open:
        problems.append(f"{dialog_open} dialog left open case(s)")
    if fullscreen:
        problems.append(f"{fullscreen} full-screen OCR case(s)")
    if total_score < PASS_TARGET_MIN:
        problems.append(f"Score {total_score}/6 below minimum {PASS_TARGET_MIN}")
    while len(problems) < 3:
        problems.append("None significant")

    fixes: List[str] = []
    if export_created or finish_pressed or next_multi or unsafe:
        fixes.append("Audit M20 Activities Next discovery close path and Next/Finish guards")
    if dialog_open:
        fixes.append("Verify Esc/Cancel closes export wizard after post-Activities screen")
    if false_pass:
        fixes.append("Tighten M20 hard-test scoring gates")
    if not fixes:
        fixes.append("Monitor with TY_TEST_M20_HARD_6.bat")
        fixes.append("Human sign-off before next module")
        fixes.append("Keep M20 discovery-only constraints enabled")
    while len(fixes) < 3:
        fixes.append("Re-run hard matrix after P6 layout changes")

    decision = "M20 STABLE" if stable else "M20 NEEDS FIX"
    next_rec = "READY FOR NEXT MODULE" if stable else "FIX M20 AGAIN"

    pass_full = sum(
        1 for r in discovery_success if r.get("m20_status") == "PASS_ACTIVITIES_NEXT_DISCOVERY"
    )
    pass_partial = sum(
        1
        for r in discovery_success
        if r.get("m20_status") == "PASS_ACTIVITIES_NEXT_DISCOVERY_PARTIAL"
    )

    post_words_union: List[str] = []
    next_counts: List[int] = []
    for r in discovery_success:
        npc = int(r.get("next_pressed_count", 0))
        if npc not in next_counts:
            next_counts.append(npc)
        for w in r.get("post_activities_evidence_words", []) or []:
            if w not in post_words_union:
                post_words_union.append(w)

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
    act_selected = sum(1 for r in results if r.get("activities_export_type_selected"))
    post_screen_count = sum(1 for r in results if r.get("post_activities_screen_detected"))
    finish_pressed_count = sum(1 for r in results if r.get("finish_pressed"))

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
        "next_pressed_too_many_cases": next_multi,
        "unsafe_actions": unsafe,
        "dialog_left_open_cases": dialog_open,
        "fullscreen_ocr_cases": fullscreen,
        "final_score": total_score,
        "max_score": len(results),
        "percentage": round(100.0 * total_score / len(results), 1) if results else 0,
        "decision": decision,
        "next_recommendation": next_rec,
        "activities_next_discovery_evidence": {
            "successful_discovery_tests": len(discovery_success),
            "pass_activities_next_discovery_count": pass_full,
            "pass_activities_next_discovery_partial_count": pass_partial,
            "activities_export_type_selected_count": act_selected,
            "next_pressed_count_evidence": next_counts,
            "post_activities_screen_detected_count": post_screen_count,
            "post_activities_evidence_words": post_words_union,
            "finish_pressed_count": finish_pressed_count,
            "close_methods_used": close_methods,
            "export_dialog_closed_count": closed_count,
            "export_files_created_count": files_created,
            "returned_to_activities_or_project_count": returned_ok,
        },
        "per_test_results": results,
        "top_issues": problems[:3],
        "fixes_applied": fixes[:3],
    }

    json_path = run_root / "m20_hard_test_6_summary.json"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    de = summary["activities_next_discovery_evidence"]
    md_lines = [
        "# M20 Hard Testing Summary",
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
        f"- Next pressed too many cases: {next_multi}",
        f"- Unsafe actions: {unsafe}",
        f"- Dialog left open cases: {dialog_open}",
        f"- Full-screen OCR cases: {fullscreen}",
        f"- Final score: {total_score} / {len(results)}",
        f"- Percentage: {summary['percentage']}%",
        f"- Decision: {decision}",
        "",
        "## Per-test result",
    ]
    for r in results:
        md_lines.append(
            f"- {r.get('test_id')} {r.get('test_name')}: score={r.get('score')} "
            f"status={r.get('status')} m20={r.get('m20_status')} reason={r.get('score_reason', '')}"
        )

    md_lines.extend(
        [
            "",
            "## Activities Next discovery evidence",
            f"- Successful discovery tests: {de['successful_discovery_tests']}",
            f"- PASS_ACTIVITIES_NEXT_DISCOVERY count: {de['pass_activities_next_discovery_count']}",
            f"- PASS_ACTIVITIES_NEXT_DISCOVERY_PARTIAL count: {de['pass_activities_next_discovery_partial_count']}",
            f"- Activities export type selected: {de['activities_export_type_selected_count']}",
            f"- Next pressed count evidence: {de['next_pressed_count_evidence']}",
            f"- Post-Activities screen detected: {de['post_activities_screen_detected_count']}",
            f"- Post-Activities evidence words: {de['post_activities_evidence_words']}",
            f"- Finish pressed: {de['finish_pressed_count']}",
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
            str(run_root / "m20_hard_test_6_summary.md"),
        ]
    )
    md_path = run_root / "m20_hard_test_6_summary.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return summary
