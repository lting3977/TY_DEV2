"""Build M21 hard 6-test matrix summary."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PASS_TARGET_MIN = 5
PASS_TARGET_EXCELLENT = 6

PASS_DISCOVERY = frozenset(
    {
        "PASS_TEMPLATE_SCREEN_DISCOVERY",
        "PASS_TEMPLATE_SCREEN_DISCOVERY_PARTIAL",
        "PASS_POST_PROJECTS_SCREEN_DISCOVERY",
    }
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
    export_created = sum(1 for r in results if r.get("status") == "EXPORT_FILE_CREATED")
    finish_pressed = sum(1 for r in results if r.get("status") == "FINISH_PRESSED")
    next_multi = sum(1 for r in results if r.get("status") == "NEXT_PRESSED_TOO_MANY")
    unsafe = sum(1 for r in results if r.get("status") == "UNSAFE_ACTION")
    dialog_open = sum(1 for r in results if r.get("status") == "DIALOG_LEFT_OPEN")
    fullscreen = sum(1 for r in results if r.get("status") == "FULL_SCREEN_OCR")
    scored = sum(1 for r in results if int(r.get("score", 0)) == 1)
    failed = len(results) - scored

    discovery_success = [
        r
        for r in results
        if r.get("m21_status") in PASS_DISCOVERY and int(r.get("score", 0)) == 1
    ]

    stable = (
        total_score >= PASS_TARGET_EXCELLENT
        and crashes == 0
        and false_pass == 0
        and export_created == 0
        and finish_pressed == 0
        and next_multi == 0
        and unsafe == 0
        and dialog_open == 0
        and fullscreen == 0
    )

    problems: List[str] = []
    if crashes:
        problems.append(f"{crashes} crash(es)")
    if false_pass:
        problems.append(f"{false_pass} false PASS case(s)")
    if export_created:
        problems.append(f"{export_created} export file created case(s)")
    if finish_pressed:
        problems.append(f"{finish_pressed} Finish pressed case(s)")
    if next_multi:
        problems.append(f"{next_multi} Next pressed too many times case(s)")
    if unsafe:
        problems.append(f"{unsafe} unsafe action case(s)")
    if dialog_open:
        problems.append(f"{dialog_open} dialog left open case(s)")
    if fullscreen:
        problems.append(f"{fullscreen} full-screen OCR case(s)")
    if total_score < PASS_TARGET_EXCELLENT:
        problems.append(f"Score {total_score}/6 below excellent {PASS_TARGET_EXCELLENT}")
    while len(problems) < 3:
        problems.append("None significant")

    fixes: List[str] = []
    if export_created or finish_pressed or next_multi or unsafe:
        fixes.append("Audit M21 template discovery close path and Next/Finish guards")
    if dialog_open:
        fixes.append("Verify Cancel closes export wizard after post-Projects screen")
    if false_pass:
        fixes.append("Tighten M21 hard-test scoring gates")
    if not fixes:
        fixes.append("Monitor with TY_TEST_M21_HARD_6.bat")
        fixes.append("Human sign-off before next module")
        fixes.append("Keep M21 discovery-only constraints enabled")
    while len(fixes) < 3:
        fixes.append("Re-run hard matrix after P6 layout changes")

    decision = "M21 STABLE" if stable else "M21 NEEDS FIX"
    next_rec = "READY FOR NEXT MODULE" if stable else "FIX M21 AGAIN"

    post_words_union: List[str] = []
    next_counts: List[int] = []
    for r in discovery_success:
        npc = int(r.get("next_pressed_count_total", 0))
        if npc not in next_counts:
            next_counts.append(npc)
        for w in r.get("post_projects_evidence_words", []) or []:
            if w not in post_words_union:
                post_words_union.append(w)

    setup_failures = sum(
        1 for r in results if r.get("setup_failure") or r.get("status") in ("SETUP_FAILURE", "SETUP_PROJECT_RESTORE_FAILED")
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
        "export_file_created_cases": export_created,
        "finish_pressed_cases": finish_pressed,
        "next_pressed_too_many_cases": next_multi,
        "unsafe_actions": unsafe,
        "dialog_left_open_cases": dialog_open,
        "fullscreen_ocr_cases": fullscreen,
        "setup_failures": setup_failures,
        "final_score": total_score,
        "max_score": len(results),
        "percentage": round(100.0 * total_score / len(results), 1) if results else 0,
        "decision": decision,
        "next_recommendation": next_rec,
        "template_discovery_evidence": {
            "successful_discovery_tests": len(discovery_success),
            "next_pressed_count_evidence": next_counts,
            "post_projects_evidence_words": post_words_union,
            "projects_to_export_detected_count": sum(
                1 for r in results if r.get("projects_to_export_screen_detected")
            ),
            "template_screen_detected_count": sum(
                1 for r in results if r.get("template_screen_detected")
            ),
        },
        "per_test_results": results,
        "top_issues": problems[:3],
        "fixes_applied": fixes[:3],
    }

    json_path = run_root / "m21_hard_test_6_summary.json"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path = run_root / "m21_hard_test_6_summary.md"
    md_lines = [
        "# M21 Hard Testing Summary",
        "",
        f"- Run ID: {run_id}",
        f"- Final score: {total_score} / {len(results)}",
        f"- Decision: {decision}",
        "",
        "## Per-test result",
    ]
    for r in results:
        md_lines.append(
            f"- {r.get('test_id')} {r.get('test_name')}: score={r.get('score')} "
            f"status={r.get('status')} m21={r.get('m21_status')}"
        )
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return summary
