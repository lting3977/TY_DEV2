"""Build M10 hard 6-test matrix summary."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PASS_TARGET_MIN = 5
PASS_TARGET_EXCELLENT = 6

PASS_OUTCOMES = frozenset({"PASS", "PASS_WITH_WARNINGS"})


def write_hard_summary(
    run_id: str,
    run_root: Path,
    results: List[Dict[str, Any]],
    project: str,
) -> Dict[str, Any]:
    total_score = sum(int(r.get("score", 0)) for r in results)
    crashes = sum(1 for r in results if r.get("status") == "CRASH")
    false_pass = sum(1 for r in results if r.get("status") == "FALSE_PASS")
    p6_touched = sum(1 for r in results if r.get("status") == "P6_TOUCHED_WHEN_SOURCE_FOLDERS_PROVIDED")
    data_date_invented = sum(1 for r in results if r.get("status") == "DATA_DATE_INVENTED")
    raw_lost = sum(1 for r in results if r.get("status") == "RAW_EVIDENCE_LOST")
    scored = sum(1 for r in results if int(r.get("score", 0)) == 1)
    failed = len(results) - scored

    compare_tests = [r for r in results if r.get("m10_status") in PASS_OUTCOMES]
    best = compare_tests[0] if compare_tests else (results[0] if results else {})

    stable = (
        total_score >= PASS_TARGET_MIN
        and crashes == 0
        and false_pass == 0
        and p6_touched == 0
        and data_date_invented == 0
        and raw_lost == 0
    )

    problems: List[str] = []
    if crashes:
        problems.append(f"{crashes} crash(es)")
    if false_pass:
        problems.append(f"{false_pass} false PASS case(s)")
    if p6_touched:
        problems.append(f"{p6_touched} P6 touched when source folders provided")
    if data_date_invented:
        problems.append(f"{data_date_invented} invented Data Date case(s)")
    if raw_lost:
        problems.append(f"{raw_lost} raw evidence lost case(s)")
    if total_score < PASS_TARGET_MIN:
        problems.append(f"Score {total_score}/6 below minimum {PASS_TARGET_MIN}")
    while len(problems) < 3:
        problems.append("None significant")

    fixes: List[str] = []
    if p6_touched or raw_lost or data_date_invented:
        fixes.append("Audit M10 source-folder isolation and evidence preservation")
    if false_pass:
        fixes.append("Tighten M10 hard-test scoring gates")
    if not fixes:
        fixes.append("Monitor with TY_TEST_M10_HARD_6.bat")
        fixes.append("Human sign-off before next module")
        fixes.append("Keep M10 read-only constraints enabled")
    while len(fixes) < 3:
        fixes.append("Re-run hard matrix after M08/M09 OCR changes")

    decision = "M10 STABLE" if stable else "M10 NEEDS FIX"
    next_rec = "READY FOR NEXT MODULE" if stable else "FIX M10 AGAIN"

    warnings_json_saved = sum(1 for r in compare_tests if r.get("warnings_json_saved"))
    comparison_csv_saved = sum(1 for r in compare_tests if r.get("comparison_csv_saved"))

    summary = {
        "run_id": run_id,
        "project": project,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_tests": len(results),
        "passed_scored": scored,
        "failed_unscored": failed,
        "crashes": crashes,
        "false_pass_cases": false_pass,
        "p6_touched_when_source_folders_provided": p6_touched,
        "data_date_invented_cases": data_date_invented,
        "raw_evidence_lost_cases": raw_lost,
        "final_score": total_score,
        "max_score": len(results),
        "percentage": round(total_score / len(results) * 100, 1) if results else 0,
        "decision": decision,
        "next_recommendation": next_rec,
        "comparison_evidence": {
            "data_date_parsed": best.get("data_date_parsed", ""),
            "activity_rows_checked": sum(int(r.get("activity_rows_checked", 0)) for r in compare_tests),
            "start_before_data_date_count": sum(int(r.get("start_before_data_date_count", 0)) for r in compare_tests),
            "finish_before_data_date_count": sum(int(r.get("finish_before_data_date_count", 0)) for r in compare_tests),
            "date_parse_issue_count": sum(int(r.get("date_parse_issue_count", 0)) for r in compare_tests),
            "low_confidence_count": sum(int(r.get("low_confidence_count", 0)) for r in compare_tests),
            "warning_count": sum(int(r.get("warning_count", 0)) for r in compare_tests),
            "warnings_json_saved": warnings_json_saved,
            "comparison_csv_saved": comparison_csv_saved,
        },
        "top_issues": problems[:3],
        "fixes_applied": fixes[:3],
        "tests": results,
    }

    json_path = run_root / "m10_hard_test_6_summary.json"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    ev = summary["comparison_evidence"]
    lines = [
        "# M10 HARD TESTING SUMMARY",
        "",
        f"Run ID: {run_id}",
        f"Project: {project}",
        "Total tests: 6",
        f"Passed/scored: {scored}",
        f"Failed/unscored: {failed}",
        f"Crashes: {crashes}",
        f"False PASS cases: {false_pass}",
        f"P6 touched when source folders provided: {p6_touched}",
        f"Data Date invented cases: {data_date_invented}",
        f"Raw evidence lost cases: {raw_lost}",
        f"Final score: {total_score} / 6",
        f"Percentage: {summary['percentage']}%",
        "",
        "Decision:",
        decision,
        "",
        "Per-test result:",
    ]
    for r in results:
        lines.append(
            f"{r.get('test_id')} {r.get('test_name')}: "
            f"{r.get('m10_status')} (score {r.get('score')}) — {r.get('score_reason', '')}"
        )

    lines.extend(
        [
            "",
            "Comparison evidence:",
            "",
            f"- Data Date parsed: {ev.get('data_date_parsed', '')}",
            f"- Activity rows checked: {ev.get('activity_rows_checked', 0)}",
            f"- Start before Data Date count: {ev.get('start_before_data_date_count', 0)}",
            f"- Finish before Data Date count: {ev.get('finish_before_data_date_count', 0)}",
            f"- Date parse issue count: {ev.get('date_parse_issue_count', 0)}",
            f"- Low confidence count: {ev.get('low_confidence_count', 0)}",
            f"- Warning count: {ev.get('warning_count', 0)}",
            f"- Warnings JSON saved: {ev.get('warnings_json_saved', 0)}",
            f"- Comparison CSV saved: {ev.get('comparison_csv_saved', 0)}",
            "",
            "Top issues:",
        ]
    )
    for i, p in enumerate(problems[:3], 1):
        lines.append(f"{i}. {p}")

    lines.extend(["", "Fixes applied:"])
    for i, fix in enumerate(fixes[:3], 1):
        lines.append(f"{i}. {fix}")

    lines.extend(["", "Next recommendation:", next_rec])
    lines.extend(
        [
            "",
            "Evidence:",
            str(json_path),
            str(run_root / "m10_hard_test_6_summary.md"),
        ]
    )

    md_path = run_root / "m10_hard_test_6_summary.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
