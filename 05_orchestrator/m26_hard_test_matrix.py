"""
M26 Hard Testing — 6-test matrix.

Proves M26 builds export preflight plans from M25, enforces manual approval,
and fails on empty project, missing M25 plan, invalid paths, and safety flags.
No P6 interaction.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
ORCH = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ORCH))
sys.path.insert(0, str(ROOT / "04_modules"))

from m06_go_to_activities import write_json  # noqa: E402
from m25_export_output_sandbox_guard_no_p6 import DEFAULT_SANDBOX_ROOT  # noqa: E402
from m26_export_preflight_plan_no_p6 import run_m26  # noqa: E402
from m26_hard_summary import write_hard_summary  # noqa: E402

PASS_PREFLIGHT = frozenset({"PASS_PREFLIGHT_PLAN"})
TEST_02_OK = frozenset({"FAIL_PROJECT_NAME_EMPTY"})
TEST_03_OK = frozenset({"FAIL_M25_PLAN_MISSING"})
TEST_04_OK = frozenset({"FAIL_OUTPUT_PATH_INVALID"})
TEST_05_OK = frozenset({"FAIL_MANUAL_APPROVAL_NOT_REQUIRED"})
TEST_06_OK = frozenset({"ERROR"})

PREFLIGHT_FILES = ("export_preflight_plan.json", "export_preflight_plan.md")


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_test_folder(matrix_run_id: str, test_id: str, slug: str) -> Path:
    folder = (
        ROOT / "06_output" / "runs" / matrix_run_id / "m26_hard_test_6" / f"test_{test_id}_{slug}"
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def preflight_files_ok(m26_result: Dict[str, Any]) -> Tuple[bool, List[str]]:
    missing: List[str] = []
    for path_str in m26_result.get("preflight_files", []):
        if not Path(path_str).exists():
            missing.append(path_str)
    return len(missing) == 0, missing


def score_result(
    test_id: str,
    m26_status: str,
    m26_result: Dict[str, Any],
) -> Tuple[int, str, str]:
    plan = m26_result.get("preflight_plan") or {}

    if test_id == "01":
        if m26_status not in PASS_PREFLIGHT:
            return 0, "FALSE_PASS", f"Expected PASS_PREFLIGHT_PLAN, got {m26_status}"
        files_ok, missing = preflight_files_ok(m26_result)
        if not files_ok:
            return 0, "FALSE_PASS", f"Preflight files missing: {missing}"
        for key in (
            "export_format",
            "export_type",
            "planned_output_path",
            "sandbox_root",
            "manual_approval_required",
            "allow_real_export",
            "finish_allowed",
            "overwrite_allowed",
            "safety_checks",
            "next_required_user_action",
        ):
            if key not in plan:
                return 0, "FALSE_PASS", f"Preflight plan missing field: {key}"
        if plan.get("export_format") != "Spreadsheet/XLSX":
            return 0, "FALSE_PASS", "export_format must be Spreadsheet/XLSX"
        if plan.get("export_type") != "Activities":
            return 0, "FALSE_PASS", "export_type must be Activities"
        if not plan.get("manual_approval_required"):
            return 0, "FALSE_PASS", "manual_approval_required must be true"
        if plan.get("allow_real_export") or plan.get("finish_allowed") or plan.get("overwrite_allowed"):
            return 0, "FALSE_PASS", "real export/finish/overwrite flags must be false"
        return 1, m26_status, "Valid preflight plan with required safety fields"

    if test_id == "02":
        if m26_status not in TEST_02_OK:
            return 0, "FALSE_PASS", f"Expected FAIL_PROJECT_NAME_EMPTY, got {m26_status}"
        return 1, m26_status, "Empty project name rejected"

    if test_id == "03":
        if m26_status not in TEST_03_OK:
            return 0, "FALSE_PASS", f"Expected FAIL_M25_PLAN_MISSING, got {m26_status}"
        return 1, m26_status, "Missing M25 plan correctly blocked"

    if test_id == "04":
        if m26_status not in TEST_04_OK:
            return 0, "FALSE_PASS", f"Expected FAIL_OUTPUT_PATH_INVALID, got {m26_status}"
        return 1, m26_status, "Invalid output path rejected"

    if test_id == "05":
        if m26_status not in TEST_05_OK:
            return 0, "FALSE_PASS", f"Expected FAIL_MANUAL_APPROVAL_NOT_REQUIRED, got {m26_status}"
        return 1, m26_status, "Manual approval safety gate enforced"

    if test_id == "06":
        if m26_status not in TEST_06_OK:
            return 0, "FALSE_PASS", f"Expected ERROR, got {m26_status}"
        return 1, m26_status, "Invalid sandbox root produced ERROR path"

    return 0, "UNKNOWN_TEST", f"Unhandled test id {test_id}"


def finish_hard_test(
    test_folder: Path,
    test_def: Dict[str, Any],
    m26_result: Dict[str, Any],
    setup_notes: List[str],
) -> Dict[str, Any]:
    m26_status = m26_result.get("status", "ERROR")
    score, status, score_reason = score_result(test_def["id"], m26_status, m26_result)

    result = {
        "test_id": test_def["id"],
        "test_name": test_def["name"],
        "slug": test_def["slug"],
        "m26_run_id": m26_result.get("run_id", ""),
        "m26_status": m26_status,
        "m26_reason": m26_result.get("reason", ""),
        "planned_output_path": m26_result.get("planned_output_path", ""),
        "preflight_files": m26_result.get("preflight_files", []),
        "score": score,
        "status": status,
        "score_reason": score_reason,
        "test_folder": str(test_folder),
        "setup_notes": setup_notes,
    }
    write_json(test_folder / "test_summary.json", result)
    lines = [
        f"# M26 Hard Test {test_def['id']} — {test_def['name']}",
        "",
        f"- M26 run ID: {m26_result.get('run_id', '')}",
        f"- M26 status: {m26_status}",
        f"- Hard test score: {score}",
        f"- Score reason: {score_reason}",
        f"- Planned output path: {result['planned_output_path']}",
        "",
        "## Setup notes",
    ]
    for note in setup_notes:
        lines.append(f"- {note}")
    lines.extend(["", "## M26 reason", m26_result.get("reason", "")])
    (test_folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def ensure_sandbox() -> Path:
    DEFAULT_SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    return DEFAULT_SANDBOX_ROOT


def run_test_01(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Run M26 with valid project name"]
    ensure_sandbox()
    m26_result = run_m26(ctx["project"], run_id=f"{ctx['matrix_run_id']}_t01")
    return finish_hard_test(test_folder, ctx["test_def"], m26_result, notes)


def run_test_02(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Run M26 with empty project name"]
    m26_result = run_m26("", run_id=f"{ctx['matrix_run_id']}_t02")
    return finish_hard_test(test_folder, ctx["test_def"], m26_result, notes)


def run_test_03(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Run M26 with force_m25_error hook"]
    ensure_sandbox()
    m26_result = run_m26(
        ctx["project"],
        run_id=f"{ctx['matrix_run_id']}_t03",
        force_m25_error=True,
    )
    return finish_hard_test(test_folder, ctx["test_def"], m26_result, notes)


def run_test_04(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Run M26 with outside-sandbox output path via M25"]
    ensure_sandbox()
    outside = ROOT / "06_output" / "exports" / "preflight_outside.xlsx"
    notes.append(f"Outside path: {outside}")
    m26_result = run_m26(
        ctx["project"],
        output_path=str(outside),
        run_id=f"{ctx['matrix_run_id']}_t04",
    )
    return finish_hard_test(test_folder, ctx["test_def"], m26_result, notes)


def run_test_05(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Run M26 with force_manual_approval_off hook"]
    ensure_sandbox()
    m26_result = run_m26(
        ctx["project"],
        run_id=f"{ctx['matrix_run_id']}_t05",
        force_manual_approval_off=True,
    )
    return finish_hard_test(test_folder, ctx["test_def"], m26_result, notes)


def run_test_06(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Run M26 with invalid sandbox root"]
    invalid_root = ROOT / "06_output" / "exports" / "not_a_real_sandbox_dir_for_m26"
    if invalid_root.exists():
        if invalid_root.is_dir():
            invalid_root.rmdir()
        else:
            invalid_root.unlink()
    notes.append(f"Invalid sandbox root (file, not dir): {invalid_root}")
    invalid_root.write_text("not a directory", encoding="utf-8")
    try:
        m26_result = run_m26(
            ctx["project"],
            sandbox_root=invalid_root,
            run_id=f"{ctx['matrix_run_id']}_t06",
        )
    finally:
        if invalid_root.exists() and invalid_root.is_file():
            invalid_root.unlink()
    return finish_hard_test(test_folder, ctx["test_def"], m26_result, notes)


HARD_TESTS: List[Dict[str, Any]] = [
    {
        "id": "01",
        "slug": "valid_preflight_plan",
        "name": "Valid preflight plan",
        "runner": run_test_01,
    },
    {
        "id": "02",
        "slug": "empty_project_name",
        "name": "Empty project name rejected",
        "runner": run_test_02,
    },
    {
        "id": "03",
        "slug": "m25_plan_missing",
        "name": "M25 plan missing blocked",
        "runner": run_test_03,
    },
    {
        "id": "04",
        "slug": "invalid_output_path",
        "name": "Invalid output path rejected",
        "runner": run_test_04,
    },
    {
        "id": "05",
        "slug": "manual_approval_required",
        "name": "Manual approval safety gate",
        "runner": run_test_05,
    },
    {
        "id": "06",
        "slug": "invalid_sandbox_root",
        "name": "Invalid sandbox root error",
        "runner": run_test_06,
    },
]


def run_matrix(project: str) -> Dict[str, Any]:
    matrix_run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / matrix_run_id
    (run_root / "m26_hard_test_6").mkdir(parents=True, exist_ok=True)

    print("M26 Hard Testing — 6-test matrix")
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
            traceback.print_exc()
            result = {
                "test_id": test_def["id"],
                "test_name": test_def["name"],
                "slug": test_def["slug"],
                "m26_run_id": "",
                "m26_status": "CRASH",
                "m26_reason": str(exc),
                "score": 0,
                "status": "CRASH",
                "score_reason": traceback.format_exc(),
                "test_folder": str(test_folder),
                "setup_notes": [f"crash: {exc}"],
            }
            write_json(test_folder / "test_summary.json", result)
        results.append(result)
        print(
            f"  -> score={result.get('score')} status={result.get('status')} "
            f"m26={result.get('m26_status')}"
        )

    summary = write_hard_summary(matrix_run_id, run_root, results, project)
    print("=" * 60)
    print(f"Final score: {summary['final_score']}/{summary['max_score']}")
    print(f"Decision: {summary['decision']}")
    print(f"Summary: {run_root / 'm26_hard_test_6_summary.json'}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M26 Hard Testing 6-test matrix")
    parser.add_argument("--project", default="Talison 1275", help="Project name for preflight")
    args = parser.parse_args()
    summary = run_matrix(args.project.strip())
    if summary.get("decision") == "M26 STABLE":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
