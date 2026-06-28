"""Build M08 hard 6-test matrix summary."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PASS_TARGET_MIN = 5
PASS_TARGET_EXCELLENT = 6

PASS_OUTCOMES = frozenset({"PASS", "PASS_WITH_LOW_CONFIDENCE_ROWS"})


def write_hard_summary(
    run_id: str,
    run_root: Path,
    results: List[Dict[str, Any]],
    project: str,
) -> Dict[str, Any]:
    total_score = sum(int(r.get("score", 0)) for r in results)
    crashes = sum(1 for r in results if r.get("status") == "CRASH")
    false_pass = sum(1 for r in results if r.get("status") == "FALSE_PASS")
    raw_overwritten = sum(1 for r in results if r.get("status") == "RAW_OCR_OVERWRITTEN")
    p6_touched = sum(1 for r in results if r.get("status") == "P6_TOUCHED_WHEN_M07_FOLDER_PROVIDED")
    scored = sum(1 for r in results if int(r.get("score", 0)) == 1)
    failed = len(results) - scored

    parse_tests = [r for r in results if r.get("m08_status") in PASS_OUTCOMES]
    row_count_total = sum(int(r.get("row_count", 0)) for r in parse_tests)
    high_conf_total = sum(int(r.get("high_confidence_count", 0)) for r in parse_tests)
    low_conf_total = sum(int(r.get("low_confidence_count", 0)) for r in parse_tests)
    csv_saved = sum(1 for r in parse_tests if r.get("csv_saved"))
    normalized_candidates = sum(int(r.get("normalized_candidate_count", 0)) for r in results)
    raw_preserved = all(r.get("raw_ocr_preserved", True) for r in parse_tests) if parse_tests else True

    stable = (
        total_score >= PASS_TARGET_MIN
        and crashes == 0
        and false_pass == 0
        and raw_overwritten == 0
        and p6_touched == 0
    )

    problems: List[str] = []
    if crashes:
        problems.append(f"{crashes} crash(es)")
    if false_pass:
        problems.append(f"{false_pass} false PASS case(s)")
    if raw_overwritten:
        problems.append(f"{raw_overwritten} raw OCR overwritten case(s)")
    if p6_touched:
        problems.append(f"{p6_touched} P6 touched when --m07-folder provided")
    if total_score < PASS_TARGET_MIN:
        problems.append(f"Score {total_score}/6 below minimum {PASS_TARGET_MIN}")
    while len(problems) < 3:
        problems.append("None significant")

    fixes: List[str] = []
    if raw_overwritten or p6_touched:
        fixes.append("Audit M08 parsing and chain invocation paths")
    if false_pass:
        fixes.append("Tighten M08 hard-test scoring gates")
    if not fixes:
        fixes.append("Monitor with TY_TEST_M08_HARD_6.bat")
        fixes.append("Human sign-off before next module")
        fixes.append("Keep M08 read-only constraints enabled")
    while len(fixes) < 3:
        fixes.append("Re-run hard matrix after M07 OCR changes")

    decision = "M08 STABLE" if stable else "M08 NEEDS FIX"
    next_rec = "READY FOR NEXT MODULE" if stable else "FIX M08 AGAIN"

    summary = {
        "run_id": run_id,
        "project": project,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_tests": len(results),
        "passed_scored": scored,
        "failed_unscored": failed,
        "crashes": crashes,
        "false_pass_cases": false_pass,
        "raw_ocr_overwritten_cases": raw_overwritten,
        "p6_touched_when_m07_folder_provided": p6_touched,
        "final_score": total_score,
        "max_score": len(results),
        "percentage": round(total_score / len(results) * 100, 1) if results else 0,
        "decision": decision,
        "next_recommendation": next_rec,
        "structured_extraction": {
            "row_count": row_count_total,
            "high_confidence_count": high_conf_total,
            "low_confidence_count": low_conf_total,
            "raw_ocr_preserved": raw_preserved,
            "normalized_candidates_created": normalized_candidates,
            "csv_saved": csv_saved,
        },
        "top_issues": problems[:3],
        "fixes_applied": fixes[:3],
        "tests": results,
    }

    json_path = run_root / "m08_hard_test_6_summary.json"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    lines = [
        "# M08 HARD TESTING SUMMARY",
        "",
        f"Run ID: {run_id}",
        f"Project: {project}",
        "Total tests: 6",
        f"Passed/scored: {scored}",
        f"Failed/unscored: {failed}",
        f"Crashes: {crashes}",
        f"False PASS cases: {false_pass}",
        f"Raw OCR overwritten cases: {raw_overwritten}",
        f"P6 touched when --m07-folder provided: {p6_touched}",
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
            f"{r.get('m08_status')} (score {r.get('score')}) — {r.get('score_reason', '')}"
        )

    lines.extend(
        [
            "",
            "Structured extraction evidence:",
            "",
            f"- Row count: {row_count_total}",
            f"- High confidence count: {high_conf_total}",
            f"- Low confidence count: {low_conf_total}",
            f"- Raw OCR preserved: {raw_preserved}",
            f"- Normalized candidates created: {normalized_candidates}",
            f"- CSV saved: {csv_saved}",
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
            str(run_root / "m08_hard_test_6_summary.md"),
        ]
    )

    md_path = run_root / "m08_hard_test_6_summary.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
