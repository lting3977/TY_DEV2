"""Build M12 hard 6-test matrix summary."""

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
    master_missing = sum(1 for r in results if r.get("status") == "MASTER_FILES_MISSING")
    evidence_lost = sum(1 for r in results if r.get("status") == "STEP_EVIDENCE_LOST")
    chain_continued = sum(
        1 for r in results if r.get("status") == "CHAIN_CONTINUED_AFTER_CRITICAL_FAILURE"
    )
    unsafe = sum(1 for r in results if r.get("status") == "UNSAFE_ACTION")
    scored = sum(1 for r in results if int(r.get("score", 0)) == 1)
    failed = len(results) - scored

    success_tests = [
        r
        for r in results
        if r.get("m12_status") in PASS_OUTCOMES and int(r.get("steps_completed", 0)) == 8
    ]
    critical_stop_tests = [r for r in results if r.get("test_id") in ("04", "05", "06")]

    stable = (
        total_score >= PASS_TARGET_MIN
        and crashes == 0
        and false_pass == 0
        and master_missing == 0
        and evidence_lost == 0
        and chain_continued == 0
        and unsafe == 0
    )

    problems: List[str] = []
    if crashes:
        problems.append(f"{crashes} crash(es)")
    if false_pass:
        problems.append(f"{false_pass} false PASS case(s)")
    if master_missing:
        problems.append(f"{master_missing} master files missing case(s)")
    if evidence_lost:
        problems.append(f"{evidence_lost} step evidence lost case(s)")
    if chain_continued:
        problems.append(f"{chain_continued} chain continued after critical failure")
    if unsafe:
        problems.append(f"{unsafe} unsafe action case(s)")
    if total_score < PASS_TARGET_MIN:
        problems.append(f"Score {total_score}/6 below minimum {PASS_TARGET_MIN}")
    while len(problems) < 3:
        problems.append("None significant")

    fixes: List[str] = []
    if master_missing or evidence_lost or chain_continued:
        fixes.append("Audit M12 stop rules and master summary file generation")
    if false_pass:
        fixes.append("Tighten M12 hard-test scoring gates")
    if not fixes:
        fixes.append("Monitor with TY_TEST_M12_HARD_6.bat")
        fixes.append("Human sign-off before next module")
        fixes.append("Keep M12 read-only orchestrator constraints enabled")
    while len(fixes) < 3:
        fixes.append("Re-run hard matrix after downstream module changes")

    decision = "M12 STABLE" if stable else "M12 NEEDS FIX"
    next_rec = "READY FOR NEXT MODULE" if stable else "FIX M12 AGAIN"

    avg_steps = (
        round(
            sum(int(r.get("steps_completed", 0)) for r in success_tests) / len(success_tests),
            1,
        )
        if success_tests
        else 0
    )

    summary = {
        "run_id": run_id,
        "project": project,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_tests": len(results),
        "passed_scored": scored,
        "failed_unscored": failed,
        "crashes": crashes,
        "false_pass_cases": false_pass,
        "master_files_missing_cases": master_missing,
        "step_evidence_lost_cases": evidence_lost,
        "chain_continued_after_critical_failure": chain_continued,
        "unsafe_actions": unsafe,
        "final_score": total_score,
        "max_score": len(results),
        "percentage": round(total_score / len(results) * 100, 1) if results else 0,
        "decision": decision,
        "next_recommendation": next_rec,
        "workflow_evidence": {
            "successful_full_chain_tests": len(success_tests),
            "average_steps_completed": avg_steps,
            "final_m11_reports_recorded": sum(1 for r in success_tests if r.get("final_m11_report_path")),
            "master_reports_created": sum(1 for r in success_tests if r.get("master_files_ok")),
            "critical_stop_tests": len(critical_stop_tests),
            "failed_step_correctly_identified": sum(
                1 for r in critical_stop_tests if r.get("failed_step_identified")
            ),
        },
        "top_issues": problems[:3],
        "fixes_applied": fixes[:3],
        "tests": results,
    }

    json_path = run_root / "m12_hard_test_6_summary.json"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    ev = summary["workflow_evidence"]
    lines = [
        "# M12 HARD TESTING SUMMARY",
        "",
        f"Run ID: {run_id}",
        f"Project: {project}",
        "Total tests: 6",
        f"Passed/scored: {scored}",
        f"Failed/unscored: {failed}",
        f"Crashes: {crashes}",
        f"False PASS cases: {false_pass}",
        f"Master files missing cases: {master_missing}",
        f"Step evidence lost cases: {evidence_lost}",
        f"Chain continued after critical failure: {chain_continued}",
        f"Unsafe actions: {unsafe}",
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
            f"{r.get('m12_status')} (score {r.get('score')}) — {r.get('score_reason', '')}"
        )

    lines.extend(
        [
            "",
            "Workflow evidence:",
            f"Successful full-chain tests: {ev.get('successful_full_chain_tests', 0)}",
            f"Average steps completed: {ev.get('average_steps_completed', 0)}",
            f"Final M11 reports recorded: {ev.get('final_m11_reports_recorded', 0)}",
            f"Master reports created: {ev.get('master_reports_created', 0)}",
            f"Critical stop tests: {ev.get('critical_stop_tests', 0)}",
            f"Failed step correctly identified: {ev.get('failed_step_correctly_identified', 0)}",
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
            str(run_root / "m12_hard_test_6_summary.md"),
        ]
    )

    md_path = run_root / "m12_hard_test_6_summary.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
