"""Build M15 hard 6-test matrix summary."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PASS_TARGET_MIN = 5
PASS_TARGET_EXCELLENT = 6

PASS_REPORT = frozenset({"PASS", "PASS_WITH_WARNINGS"})

REPORT_FILES = (
    "clipboard_health_report.md",
    "clipboard_health_report.json",
    "clipboard_activity_rows.csv",
    "clipboard_warning_register.csv",
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
    p6_touched = sum(1 for r in results if r.get("status") == "P6_TOUCHED_WHEN_SOURCE_FOLDERS_PROVIDED")
    date_invented = sum(1 for r in results if r.get("status") == "DATA_DATE_INVENTED")
    evidence_lost = sum(1 for r in results if r.get("status") == "SOURCE_EVIDENCE_LOST")
    report_missing = sum(1 for r in results if r.get("status") == "REPORT_FILES_MISSING")
    scored = sum(1 for r in results if int(r.get("score", 0)) == 1)
    failed = len(results) - scored

    report_success = [
        r
        for r in results
        if r.get("m15_status") in PASS_REPORT and int(r.get("score", 0)) == 1
    ]

    stable = (
        total_score >= PASS_TARGET_MIN
        and crashes == 0
        and false_pass == 0
        and p6_touched == 0
        and date_invented == 0
        and evidence_lost == 0
        and report_missing == 0
    )

    problems: List[str] = []
    if crashes:
        problems.append(f"{crashes} crash(es)")
    if false_pass:
        problems.append(f"{false_pass} false PASS case(s)")
    if p6_touched:
        problems.append(f"{p6_touched} P6 touch when source folders provided")
    if date_invented:
        problems.append(f"{date_invented} invented Data Date case(s)")
    if evidence_lost:
        problems.append(f"{evidence_lost} source evidence lost case(s)")
    if report_missing:
        problems.append(f"{report_missing} report files missing case(s)")
    if total_score < PASS_TARGET_MIN:
        problems.append(f"Score {total_score}/6 below minimum {PASS_TARGET_MIN}")
    while len(problems) < 3:
        problems.append("None significant")

    fixes: List[str] = []
    if p6_touched:
        fixes.append("Ensure M15 skips P6 chain when source folders are provided")
    if date_invented:
        fixes.append("Tighten M15 Data Date parsing — do not invent when M09 empty")
    if report_missing or false_pass:
        fixes.append("Audit M15 report generation and hard-test scoring gates")
    if not fixes:
        fixes.append("Monitor with TY_TEST_M15_HARD_6.bat")
        fixes.append("Human sign-off before next module")
        fixes.append("Keep M15 read-only report constraints enabled")
    while len(fixes) < 3:
        fixes.append("Re-run hard matrix after clipboard layout changes")

    decision = "M15 STABLE" if stable else "M15 NEEDS FIX"
    next_rec = "READY FOR NEXT MODULE" if stable else "FIX M15 AGAIN"

    data_dates = [r.get("data_date_parsed", "") for r in report_success if r.get("data_date_parsed")]
    rows_checked = [int(r.get("clipboard_rows_checked", 0)) for r in report_success]
    limitation_stated = sum(1 for r in report_success if r.get("limitation_stated"))

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
        "data_date_invented_cases": date_invented,
        "source_evidence_lost_cases": evidence_lost,
        "report_files_missing_cases": report_missing,
        "final_score": total_score,
        "max_score": len(results),
        "percentage": round(100.0 * total_score / len(results), 1) if results else 0,
        "decision": decision,
        "next_recommendation": next_rec,
        "clipboard_health_evidence": {
            "successful_report_tests": len(report_success),
            "data_date_parsed_samples": data_dates[:4],
            "clipboard_rows_checked_total": sum(rows_checked),
            "start_before_data_date_total": sum(
                int(r.get("start_before_data_date_count", 0)) for r in report_success
            ),
            "finish_before_data_date_total": sum(
                int(r.get("finish_before_data_date_count", 0)) for r in report_success
            ),
            "date_parse_issue_total": sum(
                int(r.get("date_parse_issue_count", 0)) for r in report_success
            ),
            "warning_total": sum(int(r.get("warning_count", 0)) for r in report_success),
            "high_severity_total": sum(int(r.get("high_severity_count", 0)) for r in report_success),
            "medium_severity_total": sum(
                int(r.get("medium_severity_count", 0)) for r in report_success
            ),
            "low_severity_total": sum(int(r.get("low_severity_count", 0)) for r in report_success),
            "limitation_stated_count": limitation_stated,
        },
        "per_test_results": results,
        "top_issues": problems[:3],
        "fixes_applied": fixes[:3],
    }

    json_path = run_root / "m15_hard_test_6_summary.json"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    md_lines = [
        "# M15 Hard Testing Summary",
        "",
        f"- Run ID: {run_id}",
        f"- Project: {project}",
        f"- Total tests: {len(results)}",
        f"- Passed/scored: {scored}",
        f"- Failed/unscored: {failed}",
        f"- Crashes: {crashes}",
        f"- False PASS cases: {false_pass}",
        f"- P6 touched when source folders provided: {p6_touched}",
        f"- Data Date invented cases: {date_invented}",
        f"- Source evidence lost cases: {evidence_lost}",
        f"- Report files missing cases: {report_missing}",
        f"- Final score: {total_score} / {len(results)}",
        f"- Percentage: {summary['percentage']}%",
        f"- Decision: {decision}",
        "",
        "## Per-test result",
    ]
    for r in results:
        md_lines.append(
            f"- {r.get('test_id')} {r.get('test_name')}: score={r.get('score')} "
            f"status={r.get('status')} m15={r.get('m15_status')} reason={r.get('score_reason', '')}"
        )

    ce = summary["clipboard_health_evidence"]
    md_lines.extend(
        [
            "",
            "## Clipboard health evidence",
            f"- Successful report tests: {ce['successful_report_tests']}",
            f"- Data Date parsed: {ce['data_date_parsed_samples']}",
            f"- Clipboard rows checked: {ce['clipboard_rows_checked_total']}",
            f"- Start before Data Date count: {ce['start_before_data_date_total']}",
            f"- Finish before Data Date count: {ce['finish_before_data_date_total']}",
            f"- Date parse issue count: {ce['date_parse_issue_total']}",
            f"- Warning count: {ce['warning_total']}",
            f"- High severity count: {ce['high_severity_total']}",
            f"- Medium severity count: {ce['medium_severity_total']}",
            f"- Low severity count: {ce['low_severity_total']}",
            f"- Selected-visible-rows limitation stated: {ce['limitation_stated_count']}",
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
            str(run_root / "m15_hard_test_6_summary.md"),
        ]
    )
    md_path = run_root / "m15_hard_test_6_summary.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return summary
