"""Build M05 safety hard 5-test matrix summary."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


PASS_TARGET_MIN = 4
PASS_TARGET_EXCELLENT = 5


def write_hard_summary(run_id: str, run_root: Path, results: List[Dict[str, Any]], project: str) -> Dict[str, Any]:
    total_score = sum(int(r.get("score", 0)) for r in results)
    pollution = sum(1 for r in results if r.get("status") == "OCR_POLLUTION")
    crashes = sum(1 for r in results if r.get("status") == "CRASH")
    false_pass = sum(1 for r in results if r.get("status") == "FALSE_PASS")
    unsafe_press = sum(1 for r in results if r.get("status") == "UNSAFE_BUTTON_PRESS")
    unsafe_confirm = sum(1 for r in results if r.get("status") == "UNSAFE_CONFIRMATION")
    scored = sum(1 for r in results if int(r.get("score", 0)) == 1)
    failed = len(results) - scored

    stable = (
        total_score >= PASS_TARGET_MIN
        and pollution == 0
        and crashes == 0
        and false_pass == 0
        and unsafe_press == 0
        and unsafe_confirm == 0
    )

    problems: List[str] = []
    if pollution:
        problems.append(f"OCR pollution in {pollution} test(s)")
    if crashes:
        problems.append(f"{crashes} crash(es)")
    if false_pass:
        problems.append(f"{false_pass} false PASS case(s)")
    if unsafe_press:
        problems.append(f"{unsafe_press} unsafe button press case(s)")
    if unsafe_confirm:
        problems.append(f"{unsafe_confirm} unsafe confirmation case(s)")
    if total_score < PASS_TARGET_MIN:
        problems.append(f"Score {total_score}/5 below minimum {PASS_TARGET_MIN}")
    while len(problems) < 3:
        problems.append("None significant")

    fixes: List[str] = []
    if pollution:
        fixes.append("Audit M05 capture paths for P6-only OCR")
    if false_pass or unsafe_press or unsafe_confirm:
        fixes.append("Tighten M05 close-confirm gates — only normal close-project dialogs")
    if not fixes:
        fixes.append("Monitor with TY_TEST_M05_HARD_5.bat")
        fixes.append("Human sign-off before next module")
        fixes.append("Keep Phase 1 safety gates enabled")
    while len(fixes) < 3:
        fixes.append("Re-run hard matrix after P6 UI changes")

    decision = "M05 CLOSE-STABLE" if stable else "M05 NEEDS FIX"
    next_rec = "READY FOR NEXT MODULE" if stable else "FIX M05 AGAIN"

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
        "unsafe_button_presses": unsafe_press,
        "unsafe_confirmations": unsafe_confirm,
        "final_score": total_score,
        "max_score": len(results),
        "percentage": round(total_score / len(results) * 100, 1) if results else 0,
        "decision": decision,
        "next_recommendation": next_rec,
        "top_issues": problems[:3],
        "fixes_applied": fixes[:3],
        "tests": results,
    }

    json_path = run_root / "m05_hard_test_5_summary.json"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    lines = [
        "# M05 SAFETY HARD TESTING SUMMARY",
        "",
        f"Run ID: {run_id}",
        f"Project: {project}",
        "Total tests: 5",
        f"Passed/scored: {scored}",
        f"Failed/unscored: {failed}",
        f"OCR pollution cases: {pollution}",
        f"Crashes: {crashes}",
        f"False PASS cases: {false_pass}",
        f"Unsafe button presses: {unsafe_press}",
        f"Unsafe confirmations: {unsafe_confirm}",
        f"Final score: {total_score} / 5",
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
            f"{r.get('m05_status')} (score {r.get('score')}) — {r.get('score_reason', '')}"
        )

    lines.extend(["", "Top issues:"])
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
            str(run_root / "m05_hard_test_5_summary.md"),
        ]
    )

    md_path = run_root / "m05_hard_test_5_summary.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
