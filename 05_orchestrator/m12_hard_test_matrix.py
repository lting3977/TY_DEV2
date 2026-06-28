"""
M12 Hard Testing — 6-test matrix.

Proves M12 reliably runs the read-only health check chain, stops on critical
failures, preserves step evidence, and creates master summary outputs.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
ORCH = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ORCH))
sys.path.insert(0, str(ROOT / "04_modules"))
sys.path.insert(0, str(ROOT / "02_accessibility"))

from m12_hard_summary import write_hard_summary  # noqa: E402
from m12_run_read_only_health_check import (  # noqa: E402
    M03_MODULE,
    M04_MODULE,
    M06_MODULE,
    M07_MODULE,
    MODULE_NAME as M12_MODULE_NAME,
    STEPS_TOTAL,
    is_allowed,
    load_json,
    run_m12,
)

PASS_OUTCOMES = frozenset({"PASS", "PASS_WITH_WARNINGS"})
FAKE_PROJECT = "**NO_SUCH_PROJECT_FOR_M12_TEST**"
DOWNSTREAM_AFTER_PROJECT_FAIL = frozenset(
    {
        M06_MODULE,
        M07_MODULE,
        "m08_read_activity_table_structured",
        "m09_read_project_data_date",
        "m10_compare_data_date_to_activity_dates",
        "m11_generate_planning_health_report",
    }
)

MASTER_FILES = (
    "result.json",
    "report.md",
    "read_only_health_check_summary.json",
    "read_only_health_check_summary.csv",
    "read_only_health_check_master_report.md",
)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def m12_output_folder(m12_run_id: str) -> Path:
    return ROOT / "06_output" / "runs" / m12_run_id / M12_MODULE_NAME


def build_test_folder(matrix_run_id: str, test_id: str, slug: str) -> Path:
    folder = ROOT / "06_output" / "runs" / matrix_run_id / "m12_hard_test_6" / f"test_{test_id}_{slug}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def master_files_ok(m12_folder: Path) -> Tuple[bool, Dict[str, bool]]:
    checks = {name: (m12_folder / name).exists() for name in MASTER_FILES}
    return all(checks.values()), checks


def check_step_evidence(m12_result: Dict[str, Any]) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    for step in m12_result.get("step_results", []):
        result_path = step.get("result_json", "")
        if result_path and not Path(result_path).exists():
            issues.append(f"missing step result.json: {result_path}")
        output_folder = step.get("output_folder", "")
        if output_folder and not Path(output_folder).exists():
            issues.append(f"missing step output folder: {output_folder}")
    return len(issues) == 0, issues


def check_chain_stopped(m12_result: Dict[str, Any]) -> Tuple[bool, str]:
    status = m12_result.get("status", "")
    steps = m12_result.get("step_results", [])
    if status != "FAIL_STEP_FAILED":
        return True, ""
    if len(steps) >= STEPS_TOTAL:
        return False, "FAIL_STEP_FAILED but all 8 steps recorded"
    if not steps:
        return False, "FAIL_STEP_FAILED with no step evidence"
    last = steps[-1]
    if is_allowed(last.get("module", ""), last.get("status", "")):
        return False, f"FAIL_STEP_FAILED but last step {last.get('module')} status is allowed"
    return True, ""


def check_no_downstream_after_project_fail(m12_result: Dict[str, Any]) -> Tuple[bool, str]:
    modules = [s.get("module", "") for s in m12_result.get("step_results", [])]
    hit = [m for m in modules if m in DOWNSTREAM_AFTER_PROJECT_FAIL]
    if hit:
        return False, f"chain continued to downstream modules after project failure: {hit}"
    return True, ""


def check_visible_table_limitation(m12_folder: Path) -> bool:
    master = m12_folder / "read_only_health_check_master_report.md"
    if not master.exists():
        return False
    text = master.read_text(encoding="utf-8").lower()
    return "visible activity table" in text or "visible-table" in text


def check_ocr_pollution_from_steps(m12_result: Dict[str, Any]) -> Tuple[bool, List[str]]:
    sys.path.insert(0, str(ROOT / "02_eye"))
    from ocr import check_ocr_pollution  # noqa: WPS433

    config_path = ROOT / "01_config" / "ty_config.json"
    pollution_keywords: List[str] = []
    if config_path.exists():
        pollution_keywords = load_json(config_path).get("pollution_keywords", [])

    issues: List[str] = []
    for step in m12_result.get("step_results", []):
        ocr_dir = Path(step.get("output_folder", "")) / "ocr"
        if not ocr_dir.exists():
            continue
        for ocr_file in ocr_dir.glob("*.json"):
            try:
                data = load_json(ocr_file)
                entries = data.get("entries", data if isinstance(data, list) else [])
                result = check_ocr_pollution(entries, pollution_keywords)
                if result.get("polluted"):
                    issues.append(f"{ocr_file.name}: {result.get('pollution_words', [])}")
            except (json.JSONDecodeError, OSError):
                continue
    return len(issues) == 0, issues


def check_p6_crop_only(m12_result: Dict[str, Any]) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    for step in m12_result.get("step_results", []):
        shots_dir = Path(step.get("output_folder", "")) / "screenshots"
        if not shots_dir.exists():
            continue
        for shot in shots_dir.glob("*.png"):
            if "desktop" in shot.name.lower() or "fullscreen" in shot.name.lower():
                issues.append(f"possible full-screen capture: {shot}")
    return len(issues) == 0, issues


def get_p6_keyword() -> str:
    config_path = ROOT / "01_config" / "ty_config.json"
    if config_path.exists():
        return load_json(config_path).get("p6_window_title_keyword", "Primavera")
    return "Primavera"


def bring_cursor_to_front() -> Dict[str, Any]:
    try:
        import pygetwindow as gw  # noqa: WPS433

        for window in gw.getAllWindows():
            title = window.title or ""
            if "cursor" in title.lower():
                window.activate()
                return {"success": True, "title": title}
        return {"success": False, "message": "No Cursor window found"}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": str(exc)}


def minimize_p6() -> Dict[str, Any]:
    from accessibility.hand import window_tools  # noqa: WPS433

    return window_tools.minimize_window_by_title(get_p6_keyword())


def failed_step_info(m12_result: Dict[str, Any]) -> Dict[str, Any]:
    for step in m12_result.get("step_results", []):
        if not is_allowed(step.get("module", ""), step.get("status", "")):
            return {
                "module": step.get("module", ""),
                "status": step.get("status", ""),
                "reason": step.get("reason", ""),
                "result_json": step.get("result_json", ""),
            }
    return {}


def score_result(
    test_id: str,
    m12_result: Dict[str, Any],
    expected: Set[str],
    *,
    require_full_chain: bool,
    require_master_files: bool,
    require_visible_limitation: bool,
    require_no_pollution: bool,
    require_p6_crop_only: bool,
    require_early_stop: bool,
    require_no_downstream: bool,
    evidence_ok: bool,
    evidence_issues: List[str],
    master_ok: bool,
    chain_ok: bool,
    chain_reason: str,
    downstream_ok: bool,
    downstream_reason: str,
    pollution_ok: bool,
    crop_ok: bool,
    visible_ok: bool,
    failed_step: Dict[str, Any],
) -> Tuple[int, str, str]:
    m12_status = m12_result.get("status", "ERROR")
    steps_completed = int(m12_result.get("steps_completed", 0))

    if m12_status in ("CRASH", "ERROR"):
        return 0, m12_status, "Unhandled error or crash"

    if not evidence_ok:
        return 0, "STEP_EVIDENCE_LOST", "; ".join(evidence_issues[:3]) or "Step evidence lost"

    if test_id == "06":
        if m12_status != "FAIL_PROJECT_NAME_EMPTY":
            return 0, "FALSE_PASS", f"Test 06 expected FAIL_PROJECT_NAME_EMPTY, got {m12_status}"
        if steps_completed > 0:
            return 0, "CHAIN_CONTINUED_AFTER_CRITICAL_FAILURE", "Module chain ran with empty project name"
        return 1, m12_status, "Controlled failure for empty project name"

    if test_id == "05":
        if m12_status != "FAIL_STEP_FAILED":
            return 0, "FALSE_PASS", f"Test 05 expected FAIL_STEP_FAILED, got {m12_status}"
        if not downstream_ok:
            return 0, "CHAIN_CONTINUED_AFTER_CRITICAL_FAILURE", downstream_reason
        if not chain_ok:
            return 0, "FALSE_PASS", chain_reason
        failed_mod = failed_step.get("module", "")
        if failed_mod not in (M03_MODULE, M04_MODULE):
            return 0, "FALSE_PASS", f"Expected M03/M04 failure, got {failed_mod}"
        if not failed_step:
            return 0, "FALSE_PASS", "Failed step not identified in master report evidence"
        return 1, m12_status, f"Stopped at {failed_mod}: {failed_step.get('status', '')}"

    if test_id == "04":
        if m12_status in PASS_OUTCOMES:
            if require_full_chain and steps_completed != STEPS_TOTAL:
                return 0, "FALSE_PASS", f"Expected 8 steps, got {steps_completed}"
            if require_master_files and not master_ok:
                return 0, "MASTER_FILES_MISSING", "Master summary files missing"
            if require_no_pollution and not pollution_ok:
                return 0, "FALSE_PASS", "OCR pollution detected"
            if require_p6_crop_only and not crop_ok:
                return 0, "FALSE_PASS", "Possible full-screen OCR capture"
            return 1, m12_status, "P6 restored from minimised state; full chain completed"
        if m12_status == "FAIL_STEP_FAILED":
            if not chain_ok:
                return 0, "FALSE_PASS", chain_reason
            if failed_step.get("status") == "FAIL_P6_WINDOW_NOT_READY":
                return 1, m12_status, "Controlled stop on FAIL_P6_WINDOW_NOT_READY"
            return 1, m12_status, f"Controlled stop at {failed_step.get('module', '')}"
        return 0, "FALSE_PASS", f"Test 04 unexpected status {m12_status}"

    if m12_status not in expected:
        return 0, "FALSE_PASS", f"Expected {sorted(expected)}, got {m12_status}"

    if require_full_chain and steps_completed != STEPS_TOTAL:
        return 0, "FALSE_PASS", f"Expected 8 steps, got {steps_completed}"

    if require_master_files and not master_ok:
        return 0, "MASTER_FILES_MISSING", "Master summary files missing"

    if not m12_result.get("final_m11_report_path") and require_full_chain:
        return 0, "FALSE_PASS", "Final M11 report path not recorded"

    if require_visible_limitation and not visible_ok:
        return 0, "FALSE_PASS", "Visible-table-only limitation not stated in master report"

    if require_no_pollution and not pollution_ok:
        return 0, "FALSE_PASS", "OCR pollution detected in step outputs"

    if require_p6_crop_only and not crop_ok:
        return 0, "FALSE_PASS", "Possible full-screen OCR capture"

    if test_id == "02":
        m03_steps = [s for s in m12_result.get("step_results", []) if s.get("module") == M03_MODULE]
        if m03_steps and m03_steps[0].get("status") not in ("PASS", "PASS_ALREADY_OPEN"):
            return 0, "FALSE_PASS", "M03 did not return allowed already-open status"

    return 1, m12_status, f"Expected outcome: {m12_status}"


def finish_hard_test(
    test_folder: Path,
    test_def: Dict[str, Any],
    m12_run_id: str,
    m12_result: Dict[str, Any],
    setup_notes: List[str],
) -> Dict[str, Any]:
    m12_folder = m12_output_folder(m12_run_id)
    evidence_ok, evidence_issues = check_step_evidence(m12_result)
    master_ok, master_checks = master_files_ok(m12_folder)
    chain_ok, chain_reason = check_chain_stopped(m12_result)
    downstream_ok, downstream_reason = check_no_downstream_after_project_fail(m12_result)
    pollution_ok, pollution_issues = check_ocr_pollution_from_steps(m12_result)
    crop_ok, crop_issues = check_p6_crop_only(m12_result)
    visible_ok = check_visible_table_limitation(m12_folder)
    failed_step = failed_step_info(m12_result)

    m12_status = m12_result.get("status", "ERROR")
    score, status_label, score_reason = score_result(
        test_def["id"],
        m12_result,
        test_def["expected"],
        require_full_chain=bool(test_def.get("require_full_chain")),
        require_master_files=bool(test_def.get("require_master_files")),
        require_visible_limitation=bool(test_def.get("require_visible_limitation")),
        require_no_pollution=bool(test_def.get("require_no_pollution")),
        require_p6_crop_only=bool(test_def.get("require_p6_crop_only")),
        require_early_stop=bool(test_def.get("require_early_stop")),
        require_no_downstream=bool(test_def.get("require_no_downstream")),
        evidence_ok=evidence_ok,
        evidence_issues=evidence_issues,
        master_ok=master_ok,
        chain_ok=chain_ok,
        chain_reason=chain_reason,
        downstream_ok=downstream_ok,
        downstream_reason=downstream_reason,
        pollution_ok=pollution_ok,
        crop_ok=crop_ok,
        visible_ok=visible_ok,
        failed_step=failed_step,
    )

    test_summary = {
        "test_id": test_def["id"],
        "test_slug": test_def["slug"],
        "test_name": test_def["name"],
        "m12_run_id": m12_run_id,
        "m12_output_folder": str(m12_folder),
        "m12_status": m12_status,
        "m12_reason": m12_result.get("reason", ""),
        "steps_completed": m12_result.get("steps_completed", 0),
        "final_m11_report_path": m12_result.get("final_m11_report_path", ""),
        "master_files_ok": master_ok,
        "master_file_checks": master_checks,
        "step_evidence_ok": evidence_ok,
        "visible_table_limitation_stated": visible_ok,
        "ocr_pollution_ok": pollution_ok,
        "p6_crop_only_ok": crop_ok,
        "failed_step": failed_step,
        "setup_notes": setup_notes,
    }
    write_json(test_folder / "test_summary.json", test_summary)

    result = {
        "test_id": test_def["id"],
        "test_slug": test_def["slug"],
        "test_name": test_def["name"],
        "m12_run_id": m12_run_id,
        "m12_status": m12_status,
        "status": status_label,
        "score": score,
        "score_reason": score_reason,
        "expected": sorted(test_def["expected"]),
        "reason": m12_result.get("reason"),
        "steps_completed": m12_result.get("steps_completed", 0),
        "steps_failed": m12_result.get("steps_failed", 0),
        "warning_or_partial_steps": m12_result.get("warning_or_partial_steps", 0),
        "final_m11_report_path": m12_result.get("final_m11_report_path", ""),
        "master_files_ok": master_ok,
        "step_evidence_ok": evidence_ok,
        "step_evidence_issues": evidence_issues,
        "ocr_pollution_ok": pollution_ok,
        "ocr_pollution_issues": pollution_issues,
        "p6_crop_only_ok": crop_ok,
        "failed_step_identified": bool(failed_step) if m12_status == "FAIL_STEP_FAILED" else True,
        "failed_step": failed_step,
        "setup_notes": setup_notes,
        "m12_output_folder": str(m12_folder),
        "m12_result_json": str(m12_folder / "result.json"),
        "m12_report_md": str(m12_folder / "report.md"),
    }
    write_json(test_folder / "result.json", result)

    lines = [
        f"# M12 Hard Test {test_def['id']} — {test_def['name']}",
        "",
        f"- M12 run ID: {m12_run_id}",
        f"- M12 status: {m12_status}",
        f"- Hard test score: {score}",
        f"- Score reason: {score_reason}",
        f"- Steps completed: {m12_result.get('steps_completed', 0)} / {STEPS_TOTAL}",
        f"- Final M11 report: {m12_result.get('final_m11_report_path', '')}",
        f"- Master files OK: {master_ok}",
        f"- Step evidence OK: {evidence_ok}",
        f"- OCR pollution OK: {pollution_ok}",
        "",
        "## Setup notes",
    ]
    for note in setup_notes:
        lines.append(f"- {note}")
    lines.extend(["", "## M12 reason", m12_result.get("reason", "")])
    (test_folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def run_test_01(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Run M12 normally for Talison 1275"]
    m12_run_id = f"{ctx['matrix_run_id']}_t01"
    m12_result = run_m12(ctx["project"], run_id=m12_run_id)
    return finish_hard_test(test_folder, ctx["test_def"], m12_run_id, m12_result, notes)


def run_test_02(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    from m03_open_project_by_name import run_m03  # noqa: WPS433

    notes = ["Ensure Talison 1275 is open via M03 before M12"]
    prep = run_m03(ctx["project"], run_id=f"{ctx['matrix_run_id']}_t02_prep_m03")
    notes.append(f"Prep M03 status: {prep.get('status')}")
    m12_run_id = f"{ctx['matrix_run_id']}_t02"
    m12_result = run_m12(ctx["project"], run_id=m12_run_id)
    return finish_hard_test(test_folder, ctx["test_def"], m12_run_id, m12_result, notes)


def run_test_03(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Bring Cursor or another window in front before M12"]
    cursor = bring_cursor_to_front()
    notes.append(f"Cursor focus: {cursor}")
    m12_run_id = f"{ctx['matrix_run_id']}_t03"
    m12_result = run_m12(ctx["project"], run_id=m12_run_id)
    return finish_hard_test(test_folder, ctx["test_def"], m12_run_id, m12_result, notes)


def run_test_04(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Minimise P6 before M12"]
    mini = minimize_p6()
    notes.append(f"Minimise P6: {mini}")
    m12_run_id = f"{ctx['matrix_run_id']}_t04"
    m12_result = run_m12(ctx["project"], run_id=m12_run_id)
    return finish_hard_test(test_folder, ctx["test_def"], m12_run_id, m12_result, notes)


def run_test_05(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = [f"Run M12 with fake project name: {FAKE_PROJECT}"]
    m12_run_id = f"{ctx['matrix_run_id']}_t05"
    m12_result = run_m12(FAKE_PROJECT, run_id=m12_run_id)
    return finish_hard_test(test_folder, ctx["test_def"], m12_run_id, m12_result, notes)


def run_test_06(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Run M12 with empty project name via run_m12('')"]
    m12_run_id = f"{ctx['matrix_run_id']}_t06"
    m12_result = run_m12("", run_id=m12_run_id)
    return finish_hard_test(test_folder, ctx["test_def"], m12_run_id, m12_result, notes)


HARD_TESTS: List[Dict[str, Any]] = [
    {
        "id": "01",
        "slug": "normal_full_health_check",
        "name": "Normal full health check",
        "expected": PASS_OUTCOMES,
        "require_full_chain": True,
        "require_master_files": True,
        "require_visible_limitation": True,
        "runner": run_test_01,
    },
    {
        "id": "02",
        "slug": "already_open_project_path",
        "name": "Already-open project path",
        "expected": PASS_OUTCOMES,
        "require_full_chain": True,
        "require_master_files": True,
        "runner": run_test_02,
    },
    {
        "id": "03",
        "slug": "p6_behind_cursor_focus_recovery",
        "name": "P6 behind Cursor focus recovery",
        "expected": PASS_OUTCOMES,
        "require_full_chain": True,
        "require_master_files": True,
        "require_no_pollution": True,
        "require_p6_crop_only": True,
        "runner": run_test_03,
    },
    {
        "id": "04",
        "slug": "p6_minimised_restore_path",
        "name": "P6 minimised restore path",
        "expected": PASS_OUTCOMES | {"FAIL_STEP_FAILED"},
        "require_full_chain": False,
        "require_master_files": False,
        "require_no_pollution": True,
        "require_p6_crop_only": True,
        "runner": run_test_04,
    },
    {
        "id": "05",
        "slug": "invalid_project_name",
        "name": "Invalid project name",
        "expected": {"FAIL_STEP_FAILED"},
        "require_early_stop": True,
        "require_no_downstream": True,
        "runner": run_test_05,
    },
    {
        "id": "06",
        "slug": "empty_project_name",
        "name": "Empty project name",
        "expected": {"FAIL_PROJECT_NAME_EMPTY"},
        "runner": run_test_06,
    },
]


def run_matrix(project: str) -> Dict[str, Any]:
    matrix_run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / matrix_run_id
    (run_root / "m12_hard_test_6").mkdir(parents=True, exist_ok=True)

    print("M12 Hard Testing — 6-test matrix")
    print(f"Run ID: {matrix_run_id}")
    print(f"Project: {project}")
    print("=" * 60)

    results: List[Dict[str, Any]] = []
    for index, test_def in enumerate(HARD_TESTS, start=1):
        print(f"[{index}/6] {test_def['id']} {test_def['name']}")
        test_folder = build_test_folder(matrix_run_id, test_def["id"], test_def["slug"])
        ctx = {"matrix_run_id": matrix_run_id, "project": project, "test_def": test_def}
        try:
            result = test_def["runner"](ctx, test_folder)
        except Exception as exc:  # noqa: BLE001
            result = {
                "test_id": test_def["id"],
                "test_slug": test_def["slug"],
                "test_name": test_def["name"],
                "m12_status": "CRASH",
                "status": "CRASH",
                "score": 0,
                "score_reason": str(exc),
                "reason": traceback.format_exc(),
            }
            write_json(test_folder / "result.json", result)
            write_json(
                test_folder / "test_summary.json",
                {"error": str(exc), "traceback": traceback.format_exc()},
            )
            (test_folder / "report.md").write_text(
                f"# CRASH\n\n{traceback.format_exc()}\n", encoding="utf-8"
            )
        results.append(result)
        print(f"  -> {result.get('m12_status')} score={result.get('score')}")

    summary = write_hard_summary(matrix_run_id, run_root, results, project)
    print("=" * 60)
    print(f"Final score: {summary['final_score']} / 6")
    print(f"Decision: {summary['decision']}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M12 hard 6-test matrix")
    parser.add_argument("--project", default="Talison 1275")
    args = parser.parse_args()
    summary = run_matrix(args.project.strip())
    ok = (
        summary["crashes"] == 0
        and summary["false_pass_cases"] == 0
        and summary["master_files_missing_cases"] == 0
        and summary["step_evidence_lost_cases"] == 0
        and summary["chain_continued_after_critical_failure"] == 0
        and summary["unsafe_actions"] == 0
        and summary["final_score"] >= 5
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
