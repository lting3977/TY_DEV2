"""Build M11 hard 6-test matrix summary."""

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
    p6_touched = sum(
        1 for r in results if r.get("status") == "P6_TOUCHED_WHEN_SOURCE_FOLDERS_PROVIDED"
    )
    source_lost = sum(1 for r in results if r.get("status") == "SOURCE_EVIDENCE_LOST")
    report_missing = sum(1 for r in results if r.get("status") == "REPORT_FILES_MISSING")
    scored = sum(1 for r in results if int(r.get("score", 0)) == 1)
    failed = len(results) - scored

    report_tests = [r for r in results if r.get("m11_status") in PASS_OUTCOMES]
    best = report_tests[0] if report_tests else (results[0] if results else {})

    stable = (
        total_score >= PASS_TARGET_MIN
        and crashes == 0
        and false_pass == 0
        and p6_touched == 0
        and source_lost == 0
        and report_missing == 0
    )

    problems: List[str] = []
    if crashes:
        problems.append(f"{crashes} crash(es)")
    if false_pass:
        problems.append(f"{false_pass} false PASS case(s)")
    if p6_touched:
        problems.append(f"{p6_touched} P6 touched when source folders provided")
    if source_lost:
        problems.append(f"{source_lost} source evidence lost case(s)")
    if report_missing:
        problems.append(f"{report_missing} report files missing case(s)")
    if total_score < PASS_TARGET_MIN:
        problems.append(f"Score {total_score}/6 below minimum {PASS_TARGET_MIN}")
    while len(problems) < 3:
        problems.append("None significant")

    fixes: List[str] = []
    if p6_touched or source_lost or report_missing:
        fixes.append("Audit M11 source-folder isolation and report generation")
    if false_pass:
        fixes.append("Tighten M11 hard-test scoring gates")
    if not fixes:
        fixes.append("Monitor with TY_TEST_M11_HARD_6.bat")
        fixes.append("Human sign-off before next module")
        fixes.append("Keep M11 report-only constraints enabled")
    while len(fixes) < 3:
        fixes.append("Re-run hard matrix after M08/M09/M10 OCR changes")

    decision = "M11 STABLE" if stable else "M11 NEEDS FIX"
    next_rec = "READY FOR NEXT MODULE" if stable else "FIX M11 AGAIN"

    report_md_saved = sum(1 for r in report_tests if r.get("planning_health_report_md_saved"))
    report_json_saved = sum(1 for r in report_tests if r.get("planning_health_report_json_saved"))
    summary_csv_saved = sum(1 for r in report_tests if r.get("planning_health_summary_csv_saved"))
    warning_csv_saved = sum(1 for r in report_tests if r.get("warning_register_csv_saved"))

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
        "source_evidence_lost_cases": source_lost,
        "report_files_missing_cases": report_missing,
        "final_score": total_score,
        "max_score": len(results),
        "percentage": round(total_score / len(results) * 100, 1) if results else 0,
        "decision": decision,
        "next_recommendation": next_rec,
        "report_evidence": {
            "data_date": best.get("data_date", ""),
            "activity_rows_checked": sum(int(r.get("activity_rows_checked", 0)) for r in report_tests),
            "warning_count": sum(int(r.get("warning_count", 0)) for r in report_tests),
            "high_severity_count": sum(int(r.get("high_severity_count", 0)) for r in report_tests),
            "medium_severity_count": sum(int(r.get("medium_severity_count", 0)) for r in report_tests),
            "low_severity_count": sum(int(r.get("low_severity_count", 0)) for r in report_tests),
            "planning_health_report_md_saved": report_md_saved,
            "planning_health_report_json_saved": report_json_saved,
            "planning_health_summary_csv_saved": summary_csv_saved,
            "warning_register_csv_saved": warning_csv_saved,
            "executive_summary_present": all(r.get("executive_summary_present") for r in report_tests),
            "warning_register_present": all(r.get("warning_register_present") for r in report_tests),
            "limitations_present": all(r.get("limitations_present") for r in report_tests),
            "next_recommendation_present": all(r.get("next_recommendation_present") for r in report_tests),
            "visible_table_only_limitation_stated": all(
                r.get("visible_table_only_limitation_stated") for r in report_tests
            ),
        },
        "top_issues": problems[:3],
        "fixes_applied": fixes[:3],
        "tests": results,
    }

    json_path = run_root / "m11_hard_test_6_summary.json"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    ev = summary["report_evidence"]
    lines = [
        "# M11 HARD TESTING SUMMARY",
        "",
        f"Run ID: {run_id}",
        f"Project: {project}",
        "Total tests: 6",
        f"Passed/scored: {scored}",
        f"Failed/unscored: {failed}",
        f"Crashes: {crashes}",
        f"False PASS cases: {false_pass}",
        f"P6 touched when source folders provided: {p6_touched}",
        f"Source evidence lost cases: {source_lost}",
        f"Report files missing cases: {report_missing}",
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
            f"{r.get('m11_status')} (score {r.get('score')}) — {r.get('score_reason', '')}"
        )

    lines.extend(
        [
            "",
            "Report evidence:",
            f"Planning report MD saved: {ev.get('planning_health_report_md_saved', 0)}",
            f"Planning report JSON saved: {ev.get('planning_health_report_json_saved', 0)}",
            f"Planning summary CSV saved: {ev.get('planning_health_summary_csv_saved', 0)}",
            f"Warning register CSV saved: {ev.get('warning_register_csv_saved', 0)}",
            f"Executive summary present: {ev.get('executive_summary_present', False)}",
            f"Warning register present: {ev.get('warning_register_present', False)}",
            f"Limitations present: {ev.get('limitations_present', False)}",
            f"Next recommendation present: {ev.get('next_recommendation_present', False)}",
            f"Visible-table-only limitation stated: {ev.get('visible_table_only_limitation_stated', False)}",
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
            str(run_root / "m11_hard_test_6_summary.md"),
        ]
    )

    md_path = run_root / "m11_hard_test_6_summary.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
