"""Build M07 hard 6-test matrix summary."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


PASS_TARGET_MIN = 5
PASS_TARGET_EXCELLENT = 6


def write_hard_summary(run_id: str, run_root: Path, results: List[Dict[str, Any]], project: str) -> Dict[str, Any]:
    total_score = sum(int(r.get("score", 0)) for r in results)
    pollution = sum(1 for r in results if r.get("status") == "OCR_POLLUTION")
    crashes = sum(1 for r in results if r.get("status") == "CRASH")
    false_pass = sum(1 for r in results if r.get("status") == "FALSE_PASS")
    full_screen_ocr = sum(1 for r in results if r.get("status") == "FULL_SCREEN_OCR")
    unsafe_action = sum(1 for r in results if r.get("status") == "UNSAFE_ACTION")
    scored = sum(1 for r in results if int(r.get("score", 0)) == 1)
    failed = len(results) - scored

    header_detected_tests = [r for r in results if r.get("header_detected")]
    footer_filtered_total = sum(int(r.get("footer_filtered_count", 0)) for r in results)

    stable = (
        total_score >= PASS_TARGET_MIN
        and pollution == 0
        and crashes == 0
        and false_pass == 0
        and full_screen_ocr == 0
        and unsafe_action == 0
    )

    problems: List[str] = []
    if pollution:
        problems.append(f"OCR pollution in {pollution} test(s)")
    if crashes:
        problems.append(f"{crashes} crash(es)")
    if false_pass:
        problems.append(f"{false_pass} false PASS case(s)")
    if full_screen_ocr:
        problems.append(f"{full_screen_ocr} full-screen OCR case(s)")
    if unsafe_action:
        problems.append(f"{unsafe_action} unsafe action case(s)")
    if total_score < PASS_TARGET_MIN:
        problems.append(f"Score {total_score}/6 below minimum {PASS_TARGET_MIN}")
    while len(problems) < 3:
        problems.append("None significant")

    fixes: List[str] = []
    if pollution or full_screen_ocr:
        fixes.append("Audit M07 capture paths for P6-only OCR")
    if false_pass:
        fixes.append("Tighten M07 table extraction and scoring gates")
    if not fixes:
        fixes.append("Monitor with TY_TEST_M07_HARD_6.bat")
        fixes.append("Human sign-off before next module")
        fixes.append("Keep Phase 1 safety gates enabled")
    while len(fixes) < 3:
        fixes.append("Re-run hard matrix after P6 UI changes")

    decision = "M07 STABLE" if stable else "M07 NEEDS FIX"
    next_rec = "READY FOR NEXT MODULE" if stable else "FIX M07 AGAIN"

    summary = {
        "run_id": run_id,
        "project": project,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_tests": len(results),
        "passed_scored": scored,
        "failed_unscored": failed,
        "ocr_pollution_cases": pollution,
        "crashes": crashes,
        "false_pass_cases": false_pass,
        "full_screen_ocr_cases": full_screen_ocr,
        "unsafe_actions": unsafe_action,
        "final_score": total_score,
        "max_score": len(results),
        "percentage": round(total_score / len(results) * 100, 1) if results else 0,
        "decision": decision,
        "next_recommendation": next_rec,
        "header_detected_tests": [r.get("test_id") for r in header_detected_tests],
        "footer_filtered_total": footer_filtered_total,
        "top_issues": problems[:3],
        "fixes_applied": fixes[:3],
        "tests": results,
    }

    json_path = run_root / "m07_hard_test_6_summary.json"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    lines = [
        "# M07 HARD TESTING SUMMARY",
        "",
        f"Run ID: {run_id}",
        f"Project: {project}",
        "Total tests: 6",
        f"Passed/scored: {scored}",
        f"Failed/unscored: {failed}",
        f"OCR pollution cases: {pollution}",
        f"Crashes: {crashes}",
        f"False PASS cases: {false_pass}",
        f"Full-screen OCR cases: {full_screen_ocr}",
        f"Unsafe actions: {unsafe_action}",
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
            f"{r.get('m07_status')} (score {r.get('score')}) — {r.get('score_reason', '')}"
        )

    lines.extend(
        [
            "",
            "Extraction evidence:",
            "",
            f"- Header detected tests: {', '.join(summary['header_detected_tests']) or '(none)'}",
            f"- Footer/status rows filtered (total): {footer_filtered_total}",
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
            str(run_root / "m07_hard_test_6_summary.md"),
        ]
    )

    md_path = run_root / "m07_hard_test_6_summary.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
