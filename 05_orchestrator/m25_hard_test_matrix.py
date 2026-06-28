"""
M25 Hard Testing — 6-test matrix.

Proves M25 validates sandbox export paths, rejects overwrite/outside-sandbox/
wrong-extension/unsafe-filename/path-traversal cases. No P6 interaction.
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
from m25_export_output_sandbox_guard_no_p6 import (  # noqa: E402
    DEFAULT_SANDBOX_ROOT,
    build_timestamped_filename,
    run_m25,
)
from m25_hard_summary import write_hard_summary  # noqa: E402

PASS_PLAN = frozenset({"PASS_EXPORT_PATH_PLAN"})
TEST_02_OK = frozenset({"FAIL_TARGET_ALREADY_EXISTS"})
TEST_03_OK = frozenset({"FAIL_PATH_OUTSIDE_SANDBOX"})
TEST_04_OK = frozenset({"FAIL_EXTENSION_NOT_ALLOWED"})
TEST_05_OK = frozenset({"FAIL_FILENAME_UNSAFE"})
TEST_06_OK = frozenset({"FAIL_PATH_OUTSIDE_SANDBOX"})


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_test_folder(matrix_run_id: str, test_id: str, slug: str) -> Path:
    folder = (
        ROOT / "06_output" / "runs" / matrix_run_id / "m25_hard_test_6" / f"test_{test_id}_{slug}"
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def score_result(
    test_id: str,
    m25_status: str,
    m25_result: Dict[str, Any],
) -> Tuple[int, str, str]:
    planned = m25_result.get("planned_output_path", "")
    checks = m25_result.get("safety_checks") or {}

    if test_id == "01":
        if m25_status not in PASS_PLAN:
            return 0, "FALSE_PASS", f"Expected PASS_EXPORT_PATH_PLAN, got {m25_status}"
        if not planned:
            return 0, "FALSE_PASS", "Valid path test missing planned_output_path"
        if not Path(planned).suffix.lower() == ".xlsx":
            return 0, "FALSE_PASS", "Valid path test must plan .xlsx"
        return 1, m25_status, "Valid sandbox path planned"

    if test_id == "02":
        if m25_status not in TEST_02_OK:
            return 0, "FALSE_PASS", f"Expected FAIL_TARGET_ALREADY_EXISTS, got {m25_status}"
        if checks.get("no_overwrite", "").startswith("pass"):
            return 0, "FALSE_PASS", "Existing file should fail no_overwrite check"
        return 1, m25_status, "Existing target correctly rejected"

    if test_id == "03":
        if m25_status not in TEST_03_OK:
            return 0, "FALSE_PASS", f"Expected FAIL_PATH_OUTSIDE_SANDBOX, got {m25_status}"
        return 1, m25_status, "Outside sandbox path rejected"

    if test_id == "04":
        if m25_status not in TEST_04_OK:
            return 0, "FALSE_PASS", f"Expected FAIL_EXTENSION_NOT_ALLOWED, got {m25_status}"
        return 1, m25_status, "Wrong extension rejected"

    if test_id == "05":
        if m25_status not in TEST_05_OK:
            return 0, "FALSE_PASS", f"Expected FAIL_FILENAME_UNSAFE, got {m25_status}"
        return 1, m25_status, "Unsafe filename rejected"

    if test_id == "06":
        if m25_status not in TEST_06_OK:
            return 0, "FALSE_PASS", f"Expected FAIL_PATH_OUTSIDE_SANDBOX, got {m25_status}"
        if ".." not in str(m25_result.get("steps", [])):
            pass  # traversal may appear in output_path arg, not steps
        return 1, m25_status, "Path traversal rejected"

    return 0, "UNKNOWN_TEST", f"Unhandled test id {test_id}"


def finish_hard_test(
    test_folder: Path,
    test_def: Dict[str, Any],
    m25_result: Dict[str, Any],
    setup_notes: List[str],
) -> Dict[str, Any]:
    m25_status = m25_result.get("status", "ERROR")
    score, status, score_reason = score_result(test_def["id"], m25_status, m25_result)

    result = {
        "test_id": test_def["id"],
        "test_name": test_def["name"],
        "slug": test_def["slug"],
        "m25_run_id": m25_result.get("run_id", ""),
        "m25_status": m25_status,
        "m25_reason": m25_result.get("reason", ""),
        "planned_output_path": m25_result.get("planned_output_path", ""),
        "safety_checks": m25_result.get("safety_checks", {}),
        "score": score,
        "status": status,
        "score_reason": score_reason,
        "test_folder": str(test_folder),
        "setup_notes": setup_notes,
    }
    write_json(test_folder / "test_summary.json", result)
    lines = [
        f"# M25 Hard Test {test_def['id']} — {test_def['name']}",
        "",
        f"- M25 run ID: {m25_result.get('run_id', '')}",
        f"- M25 status: {m25_status}",
        f"- Hard test score: {score}",
        f"- Score reason: {score_reason}",
        f"- Planned output path: {result['planned_output_path']}",
        "",
        "## Setup notes",
    ]
    for note in setup_notes:
        lines.append(f"- {note}")
    lines.extend(["", "## M25 reason", m25_result.get("reason", "")])
    (test_folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def ensure_sandbox() -> Path:
    DEFAULT_SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    return DEFAULT_SANDBOX_ROOT


def run_test_01(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Plan valid timestamped .xlsx path inside sandbox"]
    ensure_sandbox()
    m25_result = run_m25(ctx["project"], run_id=f"{ctx['matrix_run_id']}_t01")
    return finish_hard_test(test_folder, ctx["test_def"], m25_result, notes)


def run_test_02(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Create existing .xlsx in sandbox; validate same path"]
    sandbox = ensure_sandbox()
    existing_name = build_timestamped_filename(ctx["project"], timestamp="existing_file_test")
    existing_path = sandbox / existing_name
    existing_path.write_bytes(b"existing")
    notes.append(f"Created existing file: {existing_path}")
    m25_result = run_m25(
        ctx["project"],
        output_path=str(existing_path),
        run_id=f"{ctx['matrix_run_id']}_t02",
    )
    return finish_hard_test(test_folder, ctx["test_def"], m25_result, notes)


def run_test_03(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Validate path outside sandbox root"]
    ensure_sandbox()
    outside = ROOT / "06_output" / "exports" / "outside_sandbox_test.xlsx"
    notes.append(f"Outside path: {outside}")
    m25_result = run_m25(
        ctx["project"],
        output_path=str(outside),
        run_id=f"{ctx['matrix_run_id']}_t03",
    )
    return finish_hard_test(test_folder, ctx["test_def"], m25_result, notes)


def run_test_04(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Validate .csv extension inside sandbox"]
    sandbox = ensure_sandbox()
    bad_ext = sandbox / "wrong_extension.csv"
    notes.append(f"Wrong extension path: {bad_ext}")
    m25_result = run_m25(
        ctx["project"],
        output_path=str(bad_ext),
        run_id=f"{ctx['matrix_run_id']}_t04",
    )
    return finish_hard_test(test_folder, ctx["test_def"], m25_result, notes)


def run_test_05(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Validate filename with unsafe characters"]
    sandbox = ensure_sandbox()
    unsafe = sandbox / "bad<name>.xlsx"
    notes.append(f"Unsafe filename path: {unsafe}")
    m25_result = run_m25(
        ctx["project"],
        output_path=str(unsafe),
        run_id=f"{ctx['matrix_run_id']}_t05",
    )
    return finish_hard_test(test_folder, ctx["test_def"], m25_result, notes)


def run_test_06(ctx: Dict[str, Any], test_folder: Path) -> Dict[str, Any]:
    notes = ["Validate path traversal attempt"]
    sandbox = ensure_sandbox()
    traversal = sandbox / ".." / ".." / "outside_traversal.xlsx"
    notes.append(f"Traversal path: {traversal}")
    m25_result = run_m25(
        ctx["project"],
        output_path=str(traversal),
        run_id=f"{ctx['matrix_run_id']}_t06",
    )
    return finish_hard_test(test_folder, ctx["test_def"], m25_result, notes)


HARD_TESTS: List[Dict[str, Any]] = [
    {"id": "01", "slug": "valid_sandbox_path", "name": "Valid sandbox path", "runner": run_test_01},
    {
        "id": "02",
        "slug": "existing_file_rejected",
        "name": "Existing file rejected",
        "runner": run_test_02,
    },
    {
        "id": "03",
        "slug": "outside_sandbox",
        "name": "Outside sandbox rejected",
        "runner": run_test_03,
    },
    {
        "id": "04",
        "slug": "wrong_extension",
        "name": "Wrong extension rejected",
        "runner": run_test_04,
    },
    {
        "id": "05",
        "slug": "unsafe_filename",
        "name": "Unsafe filename rejected",
        "runner": run_test_05,
    },
    {
        "id": "06",
        "slug": "path_traversal",
        "name": "Path traversal rejected",
        "runner": run_test_06,
    },
]


def run_matrix(project: str) -> Dict[str, Any]:
    matrix_run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / matrix_run_id
    (run_root / "m25_hard_test_6").mkdir(parents=True, exist_ok=True)

    print("M25 Hard Testing — 6-test matrix")
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
                "m25_run_id": "",
                "m25_status": "CRASH",
                "m25_reason": str(exc),
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
            f"m25={result.get('m25_status')}"
        )

    summary = write_hard_summary(matrix_run_id, run_root, results, project)
    print("=" * 60)
    print(f"Final score: {summary['final_score']}/{summary['max_score']}")
    print(f"Decision: {summary['decision']}")
    print(f"Summary: {run_root / 'm25_hard_test_6_summary.json'}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="M25 Hard Testing 6-test matrix")
    parser.add_argument("--project", default="Talison 1275", help="Project name for path planning")
    args = parser.parse_args()
    summary = run_matrix(args.project.strip())
    if summary.get("decision") == "M25 STABLE":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
