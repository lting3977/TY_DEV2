"""Build Phase 1 eye+hand 20-test summary artifacts."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


PASS_TARGET_MIN = 16
PASS_TARGET_GOOD = 18
PASS_TARGET_EXCELLENT = 20


def load_test_results(run_root: Path) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for folder in sorted(run_root.glob("test_*")):
        result_path = folder / "result.json"
        if result_path.is_file():
            with result_path.open("r", encoding="utf-8") as handle:
                results.append(json.load(handle))
    return results


def build_summary(
    run_id: str,
    run_root: Path,
    results: List[Dict[str, Any]],
    previous: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    counts = Counter(item.get("status", "FAIL") for item in results)
    total_score = sum(int(item.get("score", 0)) for item in results)
    total_tests = len(results)
    percentage = round((total_score / 20) * 100, 1) if total_tests else 0.0

    pollution_cases = counts.get("OCR_POLLUTION", 0)
    crashes = counts.get("CRASH", 0)
    failed = counts.get("FAIL", 0)
    false_manual = counts.get("FALSE_MANUAL_REVIEW", 0)
    fail_p6_not_ready = counts.get("FAIL_P6_WINDOW_NOT_READY", 0)

    open_project_test = next((t for t in results if t.get("test_id") == "08"), None)
    open_project_ok = open_project_test and open_project_test.get("status") in {
        "PASS",
        "MANUAL_REVIEW_EXPECTED",
    } and open_project_test.get("status") != "FALSE_MANUAL_REVIEW"

    ready = (
        total_score >= PASS_TARGET_MIN
        and pollution_cases == 0
        and crashes == 0
        and false_manual == 0
        and open_project_ok
    )

    problems: List[str] = []
    if pollution_cases:
        problems.append(f"OCR pollution detected in {pollution_cases} test(s) — serious fail")
    if crashes:
        problems.append(f"{crashes} test(s) crashed")
    if false_manual:
        problems.append(f"{false_manual} false manual-review classification(s)")
    if counts.get("FAIL", 0):
        problems.append(f"{counts.get('FAIL', 0)} hard failure(s)")
    if total_score < PASS_TARGET_MIN:
        problems.append(f"Score {total_score}/20 below minimum pass threshold {PASS_TARGET_MIN}")
    if open_project_test and open_project_test.get("status") == "FALSE_MANUAL_REVIEW":
        problems.append("Open Project dialog test is false manual review")

    next_fixes: List[str] = []
    if pollution_cases:
        next_fixes.append("Verify all OCR paths use P6-window crop only; re-audit 02_eye")
    if fail_p6_not_ready:
        next_fixes.append(f"Review {fail_p6_not_ready} FAIL_P6_WINDOW_NOT_READY cases for expected vs unexpected")
    if not pollution_cases and total_score < PASS_TARGET_MIN:
        next_fixes.append("Strengthen P6 screen library rules for remaining manual-review cases")
    if not next_fixes:
        next_fixes.append("Re-run matrix after any UI/theme change")
        next_fixes.append("Human sign-off before any m03 dry-run planning")
        next_fixes.append("Keep Phase 1 safety gates enabled")

    while len(problems) < 3:
        problems.append("None significant")
    while len(next_fixes) < 3:
        next_fixes.append("Monitor regression with TY_TEST_EYE_HAND_20.bat")

    decision = "READY FOR m03" if ready else "NOT READY FOR m03"
    if pollution_cases:
        reason = "OCR pollution from Cursor/chat/desktop — unsafe for m03"
    elif total_score >= PASS_TARGET_EXCELLENT and pollution_cases == 0:
        reason = "Excellent stability score with zero pollution"
    elif total_score >= PASS_TARGET_GOOD:
        reason = "Good pass but review remaining manual-review cases"
    elif total_score >= PASS_TARGET_MIN:
        reason = "Minimum pass — fix top problems before m03"
    else:
        reason = "Below minimum pass threshold"

    prev_score = (previous or {}).get("final_score", 14)
    prev_pollution = (previous or {}).get("ocr_pollution_cases", 6)

    return {
        "run_id": run_id,
        "phase": "Phase 1 — Eye + Hand Stability — Fix Round 1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_tests": total_tests,
        "passed": counts.get("PASS", 0),
        "expected_manual_review": counts.get("MANUAL_REVIEW_EXPECTED", 0),
        "controlled_unknown": counts.get("CONTROLLED_UNKNOWN", 0),
        "failed": failed,
        "fail_p6_window_not_ready": fail_p6_not_ready,
        "false_manual_review": false_manual,
        "ocr_pollution_cases": pollution_cases,
        "crashes": crashes,
        "final_score": total_score,
        "max_score": 20,
        "percentage": percentage,
        "pass_targets": {
            "minimum": PASS_TARGET_MIN,
            "good": PASS_TARGET_GOOD,
            "excellent": PASS_TARGET_EXCELLENT,
        },
        "decision": decision,
        "reason": reason,
        "top_problems": problems[:3],
        "next_fix": next_fixes[:3],
        "comparison": {
            "previous_score": prev_score,
            "previous_ocr_pollution": prev_pollution,
            "new_score": total_score,
            "new_ocr_pollution": pollution_cases,
            "score_delta": total_score - prev_score,
            "pollution_delta": pollution_cases - prev_pollution,
        },
        "tests": results,
    }


def write_summary_files(run_root: Path, summary: Dict[str, Any]) -> None:
    json_path = run_root / "phase1_eye_hand_20_summary.json"
    md_path = run_root / "phase1_eye_hand_20_summary.md"

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    cmp_ = summary.get("comparison", {})
    lines = [
        "# PHASE 1 — EYE + HAND STABILITY SCORE AFTER FIX ROUND 1",
        "",
        f"Run ID: {summary['run_id']}",
        f"Generated: {summary['generated_at']}",
        "",
        f"Total tests: {summary['total_tests']}",
        f"Passed: {summary['passed']}",
        f"Expected manual review: {summary['expected_manual_review']}",
        f"Controlled unknown: {summary['controlled_unknown']}",
        f"Failed: {summary['failed']}",
        f"False manual review: {summary['false_manual_review']}",
        f"OCR pollution cases: {summary['ocr_pollution_cases']}",
        f"Crashes: {summary['crashes']}",
        "",
        f"Final score: {summary['final_score']} / 20",
        f"Percentage: {summary['percentage']}%",
        "",
        "Decision:",
        summary["decision"],
        "",
        "Reason:",
        summary["reason"],
        "",
        "Compare against previous run:",
        f"Previous score: {cmp_.get('previous_score', '?')} / 20",
        f"Previous OCR pollution: {cmp_.get('previous_ocr_pollution', '?')}",
        f"New score: {cmp_.get('new_score', '?')} / 20",
        f"New OCR pollution: {cmp_.get('new_ocr_pollution', '?')}",
        "",
        "Top problems found:",
    ]
    for idx, problem in enumerate(summary["top_problems"], start=1):
        lines.append(f"{idx}. {problem}")

    lines.extend(["", "Next fix:"])
    for idx, fix in enumerate(summary["next_fix"], start=1):
        lines.append(f"{idx}. {fix}")

    lines.extend(["", "## Per-test results", ""])
    for test in summary["tests"]:
        lines.append(
            f"- Test {test.get('test_id')} {test.get('test_name')}: "
            f"{test.get('status')} (score {test.get('score')})"
        )

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
