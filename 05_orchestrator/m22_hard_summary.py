"""Build M22 hard 6-test matrix summary."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PASS_TARGET_MIN = 6


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
    unsafe = sum(1 for r in results if r.get("status") == "UNSAFE_ACTION")
    dialog_open = sum(1 for r in results if r.get("status") == "DIALOG_LEFT_OPEN")
    fullscreen = sum(1 for r in results if r.get("status") == "FULL_SCREEN_OCR")
    pyautogui_failsafe = sum(
        1
        for r in results
        if r.get("status") == "SETUP_FAILURE_PYAUTOGUI_FAILSAFE" or r.get("pyautogui_failsafe")
    )
    scored = sum(1 for r in results if int(r.get("score", 0)) == 1)

    stable = (
        total_score >= PASS_TARGET_MIN
        and crashes == 0
        and false_pass == 0
        and export_created == 0
        and finish_pressed == 0
        and unsafe == 0
        and dialog_open == 0
        and fullscreen == 0
        and pyautogui_failsafe == 0
    )

    problems: List[str] = []
    if crashes:
        problems.append(f"{crashes} crash(es)")
    if pyautogui_failsafe:
        problems.append(f"{pyautogui_failsafe} PyAutoGUI fail-safe case(s)")
    if false_pass:
        problems.append(f"{false_pass} false PASS case(s)")
    if export_created:
        problems.append(f"{export_created} export file created case(s)")
    if total_score < PASS_TARGET_MIN:
        problems.append(f"Score {total_score}/6 below minimum {PASS_TARGET_MIN}")
    while len(problems) < 3:
        problems.append("None significant")

    fixes: List[str] = []
    if pyautogui_failsafe:
        fixes.append("Keep mouse away from screen corners during hard matrix runs")
    if export_created or unsafe:
        fixes.append("Audit M22 project-selection Next path and unsafe-action guards")
    if false_pass:
        fixes.append("Tighten M22 hard-test scoring gates")
    if not fixes:
        fixes.append("Monitor with TY_TEST_M22_HARD_6.bat")
        fixes.append("Human sign-off before next module")
        fixes.append("Keep M22 discovery-only constraints enabled")
    while len(fixes) < 3:
        fixes.append("Re-run hard matrix after P6 layout changes")

    decision = "M22 STABLE" if stable else "M22 NEEDS FIX"
    next_rec = "READY FOR M23" if stable else "M22 NEEDS FIX"

    pass_full = sum(1 for r in results if r.get("m22_status") == "PASS_PROJECT_SELECTION_NEXT_DISCOVERY")
    pass_partial = sum(
        1 for r in results if r.get("m22_status") == "PASS_PROJECT_SELECTION_NEXT_DISCOVERY_PARTIAL"
    )

    summary = {
        "run_id": run_id,
        "project": project,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_tests": len(results),
        "passed_scored": scored,
        "failed_unscored": len(results) - scored,
        "crashes": crashes,
        "false_pass_cases": false_pass,
        "pyautogui_failsafe_cases": pyautogui_failsafe,
        "export_file_created_cases": export_created,
        "finish_pressed_cases": finish_pressed,
        "unsafe_actions": unsafe,
        "dialog_left_open_cases": dialog_open,
        "final_score": total_score,
        "max_score": len(results) if results else 6,
        "percentage": round(100.0 * total_score / len(results), 1) if results else 0,
        "decision": decision,
        "next_recommendation": next_rec,
        "discovery_evidence": {
            "pass_project_selection_next_discovery_count": pass_full,
            "pass_project_selection_next_discovery_partial_count": pass_partial,
            "project_row_detected_count": sum(1 for r in results if r.get("project_row_detected")),
            "project_row_selected_count": sum(1 for r in results if r.get("project_row_selected")),
            "post_project_selection_screen_detected_count": sum(
                1 for r in results if r.get("post_project_selection_next_screen_detected")
            ),
            "template_screen_detected_count": sum(1 for r in results if r.get("template_screen_detected")),
            "export_dialog_closed_count": sum(1 for r in results if r.get("export_dialog_closed")),
        },
        "per_test_results": results,
        "top_issues": problems[:3],
        "fixes_applied": fixes[:3],
    }

    json_path = run_root / "m22_hard_test_6_summary.json"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    md_lines = [
        "# M22 Hard Testing Summary",
        "",
        f"- Run ID: {run_id}",
        f"- Project: {project}",
        f"- Final score: {total_score} / {len(results)}",
        f"- PyAutoGUI fail-safe cases: {pyautogui_failsafe}",
        f"- Decision: {decision}",
        "",
        "## Per-test result",
    ]
    for r in results:
        md_lines.append(
            f"- {r.get('test_id')} {r.get('test_name')}: score={r.get('score')} "
            f"status={r.get('status')} m22={r.get('m22_status')}"
        )
    md_lines.extend(["", "## Next recommendation", next_rec, "", "## Evidence", str(json_path)])
    (run_root / "m22_hard_test_6_summary.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return summary
