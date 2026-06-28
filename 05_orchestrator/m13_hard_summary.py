"""Build M13 hard 6-test matrix summary."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PASS_TARGET_MIN = 5
PASS_TARGET_EXCELLENT = 6

PASS_CLIPBOARD = frozenset({"PASS", "PASS_PARTIAL_CLIPBOARD"})
TEST_06_OK = frozenset(
    {
        "FAIL_ACTIVITIES_NOT_FOUND",
        "FAIL_TABLE_NOT_DETECTED",
        "MANUAL_REVIEW_CANNOT_CONFIRM",
    }
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
    pollution_cases = sum(1 for r in results if r.get("status") == "CLIPBOARD_POLLUTION")
    fg_not_confirmed = sum(
        1 for r in results if r.get("status") == "P6_FOREGROUND_NOT_CONFIRMED_BEFORE_COPY"
    )
    grid_outside = sum(1 for r in results if r.get("status") == "GRID_CLICK_OUTSIDE_P6")
    unsafe = sum(1 for r in results if r.get("status") == "UNSAFE_ACTION")
    not_restored = sum(1 for r in results if r.get("status") == "CLIPBOARD_NOT_RESTORED")
    scored = sum(1 for r in results if int(r.get("score", 0)) == 1)
    failed = len(results) - scored

    clipboard_success = [
        r
        for r in results
        if r.get("m13_status") in PASS_CLIPBOARD and int(r.get("score", 0)) == 1
    ]

    stable = (
        total_score >= PASS_TARGET_MIN
        and crashes == 0
        and false_pass == 0
        and pollution_cases == 0
        and fg_not_confirmed == 0
        and grid_outside == 0
        and unsafe == 0
        and not_restored == 0
    )

    problems: List[str] = []
    if crashes:
        problems.append(f"{crashes} crash(es)")
    if false_pass:
        problems.append(f"{false_pass} false PASS case(s)")
    if pollution_cases:
        problems.append(f"{pollution_cases} clipboard pollution case(s)")
    if fg_not_confirmed:
        problems.append(f"{fg_not_confirmed} P6 foreground not confirmed before copy")
    if grid_outside:
        problems.append(f"{grid_outside} grid click outside P6 crop case(s)")
    if unsafe:
        problems.append(f"{unsafe} unsafe action case(s)")
    if not_restored:
        problems.append(f"{not_restored} clipboard not restored case(s)")
    if total_score < PASS_TARGET_MIN:
        problems.append(f"Score {total_score}/6 below minimum {PASS_TARGET_MIN}")
    while len(problems) < 3:
        problems.append("None significant")

    fixes: List[str] = []
    if pollution_cases or fg_not_confirmed or grid_outside:
        fixes.append("Audit M13 foreground confirmation and grid click targeting")
    if not_restored:
        fixes.append("Verify clipboard restore path after copy failures")
    if false_pass:
        fixes.append("Tighten M13 hard-test scoring gates")
    if not fixes:
        fixes.append("Monitor with TY_TEST_M13_HARD_6.bat")
        fixes.append("Human sign-off before next module")
        fixes.append("Keep M13 read-only clipboard constraints enabled")
    while len(fixes) < 3:
        fixes.append("Re-run hard matrix after P6 layout changes")

    decision = "M13 STABLE" if stable else "M13 NEEDS FIX"
    next_rec = "READY FOR NEXT MODULE" if stable else "FIX M13 AGAIN"

    pass_count = sum(1 for r in clipboard_success if r.get("m13_status") == "PASS")
    partial_count = sum(
        1 for r in clipboard_success if r.get("m13_status") == "PASS_PARTIAL_CLIPBOARD"
    )
    line_counts = [int(r.get("clipboard_line_count", 0)) for r in clipboard_success]
    activity_counts = [int(r.get("activity_like_row_count", 0)) for r in clipboard_success]
    avg_lines = round(sum(line_counts) / len(line_counts), 1) if line_counts else 0
    avg_activity = round(sum(activity_counts) / len(activity_counts), 1) if activity_counts else 0
    headers_union: List[str] = []
    for r in clipboard_success:
        for h in r.get("headers_detected", []) or []:
            if h not in headers_union:
                headers_union.append(h)
    copy_methods = sorted({r.get("copy_method_used", "") for r in results if r.get("copy_method_used")})
    restored_count = sum(1 for r in results if r.get("clipboard_restored"))

    summary = {
        "run_id": run_id,
        "project": project,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_tests": len(results),
        "passed_scored": scored,
        "failed_unscored": failed,
        "crashes": crashes,
        "false_pass_cases": false_pass,
        "clipboard_pollution_cases": pollution_cases,
        "p6_foreground_not_confirmed_before_copy": fg_not_confirmed,
        "grid_click_outside_p6_cases": grid_outside,
        "unsafe_actions": unsafe,
        "clipboard_not_restored_cases": not_restored,
        "final_score": total_score,
        "max_score": len(results),
        "percentage": round(total_score / len(results) * 100, 1) if results else 0,
        "decision": decision,
        "next_recommendation": next_rec,
        "clipboard_evidence": {
            "successful_clipboard_tests": len(clipboard_success),
            "pass_count": pass_count,
            "pass_partial_clipboard_count": partial_count,
            "average_clipboard_line_count": avg_lines,
            "average_activity_like_row_count": avg_activity,
            "headers_detected": headers_union,
            "copy_methods_used": copy_methods,
            "clipboard_restored_count": restored_count,
        },
        "per_test_results": results,
        "top_issues": problems[:3],
        "fixes_applied": fixes[:3],
    }

    json_path = run_root / "m13_hard_test_6_summary.json"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    md_lines = [
        "# M13 Hard Testing Summary",
        "",
        f"- Run ID: {run_id}",
        f"- Project: {project}",
        f"- Total tests: {len(results)}",
        f"- Passed/scored: {scored}",
        f"- Failed/unscored: {failed}",
        f"- Crashes: {crashes}",
        f"- False PASS cases: {false_pass}",
        f"- Clipboard pollution cases: {pollution_cases}",
        f"- P6 foreground not confirmed before copy: {fg_not_confirmed}",
        f"- Grid click outside P6 cases: {grid_outside}",
        f"- Unsafe actions: {unsafe}",
        f"- Clipboard not restored cases: {not_restored}",
        f"- Final score: {total_score} / {len(results)}",
        f"- Percentage: {summary['percentage']}%",
        f"- Decision: {decision}",
        "",
        "## Per-test result",
    ]
    for r in results:
        md_lines.append(
            f"- {r.get('test_id')} {r.get('test_name')}: "
            f"score={r.get('score')} status={r.get('status')} "
            f"m13={r.get('m13_status', '')} reason={r.get('score_reason', '')}"
        )
    md_lines.extend(
        [
            "",
            "## Clipboard evidence",
            f"- Successful clipboard tests: {len(clipboard_success)}",
            f"- PASS count: {pass_count}",
            f"- PASS_PARTIAL_CLIPBOARD count: {partial_count}",
            f"- Average clipboard line count: {avg_lines}",
            f"- Average activity-like row count: {avg_activity}",
            f"- Headers detected: {headers_union}",
            f"- Copy methods used: {copy_methods}",
            f"- Clipboard restored: {restored_count}",
            "",
            "## Top issues",
        ]
    )
    for i, issue in enumerate(problems[:3], start=1):
        md_lines.append(f"{i}. {issue}")
    md_lines.extend(["", "## Fixes applied"])
    for i, fix in enumerate(fixes[:3], start=1):
        md_lines.append(f"{i}. {fix}")
    md_lines.extend(
        [
            "",
            "## Next recommendation",
            next_rec,
            "",
            "## Evidence",
            str(json_path),
            str(run_root / "m13_hard_test_6_summary.md"),
        ]
    )
    md_path = run_root / "m13_hard_test_6_summary.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return summary
