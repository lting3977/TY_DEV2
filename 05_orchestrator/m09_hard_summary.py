"""Build M09 hard 6-test matrix summary."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PASS_TARGET_MIN = 5
PASS_TARGET_EXCELLENT = 6

PASS_OUTCOMES = frozenset({"PASS", "PASS_WITH_DATE_CANDIDATES"})
STRONG_LABELS = ("data date", "current data date", "project data date")


def write_hard_summary(
    run_id: str,
    run_root: Path,
    results: List[Dict[str, Any]],
    project: str,
) -> Dict[str, Any]:
    total_score = sum(int(r.get("score", 0)) for r in results)
    crashes = sum(1 for r in results if r.get("status") == "CRASH")
    false_pass = sum(1 for r in results if r.get("status") == "FALSE_PASS")
    ocr_pollution = sum(1 for r in results if r.get("status") == "OCR_POLLUTION")
    full_screen_ocr = sum(1 for r in results if r.get("status") == "FULL_SCREEN_OCR")
    unsafe_action = sum(1 for r in results if r.get("status") == "UNSAFE_ACTION")
    p6_touched = sum(
        1 for r in results if r.get("status") == "P6_TOUCHED_WHEN_TEST_OCR_SOURCE_PROVIDED"
    )
    scored = sum(1 for r in results if int(r.get("score", 0)) == 1)
    failed = len(results) - scored

    pass_tests = [r for r in results if r.get("m09_status") == "PASS"]
    candidate_tests = [
        r for r in results if r.get("m09_status") in ("PASS_WITH_DATE_CANDIDATES", "MANUAL_REVIEW_CANNOT_CONFIRM")
    ]
    failure_tests = [
        r
        for r in results
        if r.get("m09_status")
        in (
            "FAIL_PROJECT_NOT_OPEN",
            "FAIL_P6_WINDOW_NOT_READY",
            "FAIL_DATA_DATE_NOT_FOUND",
            "FAIL_ACTIVITIES_NOT_FOUND",
        )
    ]

    best_pass = pass_tests[0] if pass_tests else (results[0] if results else {})

    stable = (
        total_score >= PASS_TARGET_MIN
        and crashes == 0
        and false_pass == 0
        and ocr_pollution == 0
        and full_screen_ocr == 0
        and unsafe_action == 0
        and p6_touched == 0
    )

    problems: List[str] = []
    if crashes:
        problems.append(f"{crashes} crash(es)")
    if false_pass:
        problems.append(f"{false_pass} false PASS case(s)")
    if ocr_pollution:
        problems.append(f"{ocr_pollution} OCR pollution case(s)")
    if full_screen_ocr:
        problems.append(f"{full_screen_ocr} full-screen OCR case(s)")
    if unsafe_action:
        problems.append(f"{unsafe_action} unsafe action case(s)")
    if p6_touched:
        problems.append(f"{p6_touched} P6 touched when test OCR source provided")
    if total_score < PASS_TARGET_MIN:
        problems.append(f"Score {total_score}/6 below minimum {PASS_TARGET_MIN}")
    while len(problems) < 3:
        problems.append("None significant")

    fixes: List[str] = []
    if ocr_pollution or full_screen_ocr:
        fixes.append("Audit M09 capture paths for P6-only OCR")
    if false_pass or p6_touched:
        fixes.append("Tighten M09 hard-test scoring and test-OCR isolation")
    if not fixes:
        fixes.append("Monitor with TY_TEST_M09_HARD_6.bat")
        fixes.append("Human sign-off before next module")
        fixes.append("Keep M09 read-only constraints enabled")
    while len(fixes) < 3:
        fixes.append("Re-run hard matrix after P6 UI changes")

    decision = "M09 STABLE" if stable else "M09 NEEDS FIX"
    next_rec = "READY FOR NEXT MODULE" if stable else "FIX M09 AGAIN"

    summary = {
        "run_id": run_id,
        "project": project,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_tests": len(results),
        "passed_scored": scored,
        "failed_unscored": failed,
        "crashes": crashes,
        "false_pass_cases": false_pass,
        "ocr_pollution_cases": ocr_pollution,
        "full_screen_ocr_cases": full_screen_ocr,
        "unsafe_actions": unsafe_action,
        "p6_touched_when_test_ocr_source_provided": p6_touched,
        "final_score": total_score,
        "max_score": len(results),
        "percentage": round(total_score / len(results) * 100, 1) if results else 0,
        "decision": decision,
        "next_recommendation": next_rec,
        "data_date_evidence": {
            "data_date_raw": best_pass.get("data_date_raw", ""),
            "data_date_normalized_candidate": best_pass.get("data_date_normalized_candidate", ""),
            "confidence": best_pass.get("confidence", 0.0),
            "candidate_count": best_pass.get("candidate_count", 0),
            "pass_candidate_tests": [r.get("test_id") for r in pass_tests],
            "manual_review_candidate_tests": [r.get("test_id") for r in candidate_tests],
            "failure_tests": [r.get("test_id") for r in failure_tests],
        },
        "top_issues": problems[:3],
        "fixes_applied": fixes[:3],
        "tests": results,
    }

    json_path = run_root / "m09_hard_test_6_summary.json"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    lines = [
        "# M09 HARD TESTING SUMMARY",
        "",
        f"Run ID: {run_id}",
        f"Project: {project}",
        "Total tests: 6",
        f"Passed/scored: {scored}",
        f"Failed/unscored: {failed}",
        f"Crashes: {crashes}",
        f"False PASS cases: {false_pass}",
        f"OCR pollution cases: {ocr_pollution}",
        f"Full-screen OCR cases: {full_screen_ocr}",
        f"Unsafe actions: {unsafe_action}",
        f"P6 touched when test OCR source provided: {p6_touched}",
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
            f"{r.get('m09_status')} (score {r.get('score')}) — {r.get('score_reason', '')}"
        )

    ev = summary["data_date_evidence"]
    lines.extend(
        [
            "",
            "Data date evidence:",
            "",
            f"- Data date raw: {ev.get('data_date_raw', '')}",
            f"- Data date normalized candidate: {ev.get('data_date_normalized_candidate', '')}",
            f"- Confidence: {ev.get('confidence', 0.0)}",
            f"- Candidate count: {ev.get('candidate_count', 0)}",
            f"- PASS candidate tests: {', '.join(ev.get('pass_candidate_tests', [])) or '(none)'}",
            f"- Manual review candidate tests: {', '.join(ev.get('manual_review_candidate_tests', [])) or '(none)'}",
            f"- Failure tests: {', '.join(ev.get('failure_tests', [])) or '(none)'}",
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
            str(run_root / "m09_hard_test_6_summary.md"),
        ]
    )

    md_path = run_root / "m09_hard_test_6_summary.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
