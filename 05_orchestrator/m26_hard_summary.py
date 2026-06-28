"""Build M26 hard 6-test matrix summary."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PASS_TARGET_MIN = 5

PASS_PREFLIGHT = frozenset({"PASS_PREFLIGHT_PLAN"})


def write_hard_summary(
    run_id: str,
    run_root: Path,
    results: List[Dict[str, Any]],
    project: str,
) -> Dict[str, Any]:
    total_score = sum(int(r.get("score", 0)) for r in results)
    crashes = sum(1 for r in results if r.get("status") == "CRASH")
    false_pass = sum(1 for r in results if r.get("status") == "FALSE_PASS")
    scored = sum(1 for r in results if int(r.get("score", 0)) == 1)
    failed = len(results) - scored

    stable = total_score >= PASS_TARGET_MIN and crashes == 0 and false_pass == 0

    problems: List[str] = []
    if crashes:
        problems.append(f"{crashes} crash(es)")
    if false_pass:
        problems.append(f"{false_pass} false PASS case(s)")
    if total_score < PASS_TARGET_MIN:
        problems.append(f"Score {total_score}/6 below minimum {PASS_TARGET_MIN}")
    while len(problems) < 3:
        problems.append("None significant")

    fixes: List[str] = []
    if false_pass:
        fixes.append("Tighten M26 preflight safety flag gates")
    if not fixes:
        fixes.append("Monitor with TY_TEST_M26_HARD_6.bat")
        fixes.append("Human sign-off before real export module")
        fixes.append("Keep manual_approval_required=true in preflight plans")
    while len(fixes) < 3:
        fixes.append("Re-run hard matrix after preflight policy changes")

    decision = "M26 STABLE" if stable else "M26 NEEDS FIX"
    next_rec = "READY FOR NEXT MODULE" if stable else "FIX M26 AGAIN"

    summary = {
        "run_id": run_id,
        "project": project,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_tests": len(results),
        "passed_scored": scored,
        "failed_unscored": failed,
        "crashes": crashes,
        "false_pass_cases": false_pass,
        "final_score": total_score,
        "max_score": len(results),
        "percentage": round(100.0 * total_score / len(results), 1) if results else 0,
        "decision": decision,
        "next_recommendation": next_rec,
        "per_test_results": results,
        "top_issues": problems[:3],
        "fixes_applied": fixes[:3],
    }

    json_path = run_root / "m26_hard_test_6_summary.json"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    md_lines = [
        "# M26 Hard Testing Summary",
        "",
        f"- Run ID: {run_id}",
        f"- Project: {project}",
        f"- Total tests: {len(results)}",
        f"- Passed/scored: {scored}",
        f"- Failed/unscored: {failed}",
        f"- Crashes: {crashes}",
        f"- False PASS cases: {false_pass}",
        f"- Final score: {total_score} / {len(results)}",
        f"- Percentage: {summary['percentage']}%",
        f"- Decision: {decision}",
        "",
        "## Per-test result",
    ]
    for r in results:
        md_lines.append(
            f"- {r.get('test_id')} {r.get('test_name')}: score={r.get('score')} "
            f"status={r.get('status')} m26={r.get('m26_status')} reason={r.get('score_reason', '')}"
        )
    md_lines.extend(["", "## Top issues"])
    for idx, issue in enumerate(problems[:3], start=1):
        md_lines.append(f"{idx}. {issue}")
    md_lines.extend(["", "## Next recommendation", next_rec])
    md_path = run_root / "m26_hard_test_6_summary.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return summary
