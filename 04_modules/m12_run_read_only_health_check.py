"""
M12 — Run Read-Only Health Check (Phase 11).

Master orchestrator: runs M03 -> M04 -> M06 -> M07 -> M08 -> M09 -> M10 -> M11
in one read-only planning health check workflow.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "04_modules"))

MODULE_NAME = "m12_run_read_only_health_check"

M03_MODULE = "m03_open_project_by_name"
M04_MODULE = "m04_check_project_opened"
M06_MODULE = "m06_go_to_activities"
M07_MODULE = "m07_read_activity_table_snapshot"
M08_MODULE = "m08_read_activity_table_structured"
M09_MODULE = "m09_read_project_data_date"
M10_MODULE = "m10_compare_data_date_to_activity_dates"
M11_MODULE = "m11_generate_planning_health_report"

STEPS_TOTAL = 8

ALLOWED_STATUSES: Dict[str, frozenset[str]] = {
    M03_MODULE: frozenset({"PASS", "PASS_ALREADY_OPEN"}),
    M04_MODULE: frozenset({"PASS"}),
    M06_MODULE: frozenset({"PASS", "PASS_ALREADY_IN_ACTIVITIES"}),
    M07_MODULE: frozenset({"PASS", "PASS_PARTIAL_SNAPSHOT"}),
    M08_MODULE: frozenset({"PASS", "PASS_WITH_LOW_CONFIDENCE_ROWS"}),
    M09_MODULE: frozenset({"PASS", "PASS_WITH_DATE_CANDIDATES"}),
    M10_MODULE: frozenset({"PASS", "PASS_WITH_WARNINGS"}),
    M11_MODULE: frozenset({"PASS", "PASS_WITH_WARNINGS"}),
}

WARNING_PARTIAL_STATUSES = frozenset(
    {
        "PASS_PARTIAL_SNAPSHOT",
        "PASS_WITH_LOW_CONFIDENCE_ROWS",
        "PASS_WITH_DATE_CANDIDATES",
        "PASS_WITH_WARNINGS",
    }
)

M10_CRITICAL_FAILURES = frozenset(
    {
        "FAIL_M08_SOURCE_NOT_FOUND",
        "FAIL_M09_SOURCE_NOT_FOUND",
        "FAIL_DATA_DATE_MISSING",
        "FAIL_NO_ACTIVITY_ROWS",
    }
)


@dataclass
class RunEvidence:
    run_id: str
    folder: Path
    steps: List[str] = field(default_factory=list)


@dataclass
class StepDef:
    step_number: int
    module_key: str
    module_name: str
    label: str
    runner: Callable[..., Dict[str, Any]]
    build_kwargs: Callable[[Dict[str, Any]], Dict[str, Any]]


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_evidence(run_id: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / run_id / MODULE_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return RunEvidence(run_id=run_id, folder=folder)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def module_folder(module_run_id: str, module_name: str) -> Path:
    return ROOT / "06_output" / "runs" / module_run_id / module_name


def read_module_result(module_run_id: str, module_name: str) -> Tuple[Dict[str, Any], Path, Path, Path]:
    folder = module_folder(module_run_id, module_name)
    result_path = folder / "result.json"
    report_path = folder / "report.md"
    if not result_path.exists():
        return (
            {
                "run_id": module_run_id,
                "module": module_name,
                "status": "ERROR",
                "reason": f"result.json not found at {result_path}",
            },
            result_path,
            report_path,
            folder,
        )
    return load_json(result_path), result_path, report_path, folder


def is_allowed(module_name: str, status: str) -> bool:
    return status in ALLOWED_STATUSES.get(module_name, frozenset())


def is_warning_partial(status: str) -> bool:
    return status in WARNING_PARTIAL_STATUSES


def step_run_id(master_run_id: str, step_number: int) -> str:
    return f"{master_run_id}_s{step_number:02d}"


def record_step(
    step_number: int,
    module_name: str,
    module_run_id: str,
    started_at: datetime,
    finished_at: datetime,
) -> Dict[str, Any]:
    result, result_path, report_path, output_folder = read_module_result(module_run_id, module_name)
    duration = (finished_at - started_at).total_seconds()
    return {
        "step_number": step_number,
        "module": module_name,
        "run_id": result.get("run_id", module_run_id),
        "status": result.get("status", "ERROR"),
        "reason": result.get("reason", ""),
        "result_json": str(result_path),
        "report_md": str(report_path) if report_path.exists() else "",
        "output_folder": str(output_folder),
        "start_time": started_at.isoformat(timespec="seconds"),
        "finish_time": finished_at.isoformat(timespec="seconds"),
        "duration_seconds": round(duration, 3),
    }


def decide_master_status(
    step_results: List[Dict[str, Any]],
    *,
    stopped_early: bool,
) -> Tuple[str, str]:
    if stopped_early:
        failed = next((s for s in step_results if not is_allowed(s["module"], s["status"])), None)
        if failed:
            return (
                "FAIL_STEP_FAILED",
                f"Stopped at {failed['module']}: {failed['status']} — {failed['reason']}",
            )
        return "FAIL_STEP_FAILED", "Workflow stopped before all steps completed"

    if len(step_results) < STEPS_TOTAL:
        return "FAIL_STEP_FAILED", "Not all workflow steps completed"

    m11 = step_results[-1]
    if m11["status"] not in ALLOWED_STATUSES[M11_MODULE]:
        return "FAIL_STEP_FAILED", f"M11 ended with {m11['status']}: {m11['reason']}"

    if any(is_warning_partial(s["status"]) for s in step_results):
        return (
            "PASS_WITH_WARNINGS",
            "Read-only health check completed with warning or partial step status(es)",
        )
    if m11["status"] == "PASS":
        return "PASS", "Read-only health check completed with no warnings"

    return (
        "PASS_WITH_WARNINGS",
        "Read-only health check completed; M11 reported warnings",
    )


def build_summary_json(
    project_name: str,
    master_run_id: str,
    final_status: str,
    final_reason: str,
    step_results: List[Dict[str, Any]],
    m11_result: Optional[Dict[str, Any]],
    m12_folder: Path,
) -> Dict[str, Any]:
    modules_run = [s["module"] for s in step_results]
    source_folders: Dict[str, str] = {}
    if m11_result:
        source_folders = {
            "m08": m11_result.get("source_m08_folder", ""),
            "m09": m11_result.get("source_m09_folder", ""),
            "m10": m11_result.get("source_m10_folder", ""),
        }

    warning_summary: Dict[str, Any] = {}
    if m11_result:
        warning_summary = {
            "data_date": m11_result.get("data_date", ""),
            "activity_rows_checked": m11_result.get("activity_rows_checked", 0),
            "warning_count": m11_result.get("warning_count", 0),
            "high_severity_count": m11_result.get("high_severity_count", 0),
            "medium_severity_count": m11_result.get("medium_severity_count", 0),
            "low_severity_count": m11_result.get("low_severity_count", 0),
            "final_m11_report_path": "",
            "final_warning_register_path": "",
        }
        report_files = m11_result.get("report_files") or []
        for path in report_files:
            if str(path).endswith("planning_health_report.md"):
                warning_summary["final_m11_report_path"] = path
            if str(path).endswith("warning_register.csv"):
                warning_summary["final_warning_register_path"] = path

    return {
        "project_name": project_name,
        "master_run_id": master_run_id,
        "final_status": final_status,
        "final_reason": final_reason,
        "modules_run": modules_run,
        "source_folders": source_folders,
        "output_folders": {s["module"]: s["output_folder"] for s in step_results},
        "warning_summary": warning_summary,
        "final_report_path": warning_summary.get("final_m11_report_path", ""),
        "step_results": step_results,
        "m12_summary_files": {
            "result_json": str(m12_folder / "result.json"),
            "report_md": str(m12_folder / "report.md"),
            "summary_json": str(m12_folder / "read_only_health_check_summary.json"),
            "summary_csv": str(m12_folder / "read_only_health_check_summary.csv"),
            "master_report_md": str(m12_folder / "read_only_health_check_master_report.md"),
        },
    }


def write_summary_csv(path: Path, step_results: List[Dict[str, Any]]) -> None:
    fields = [
        "step_number",
        "module",
        "run_id",
        "status",
        "reason",
        "duration_seconds",
        "result_json",
        "report_md",
        "output_folder",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for step in step_results:
            writer.writerow({k: step.get(k, "") for k in fields})


def write_master_report(
    path: Path,
    *,
    project_name: str,
    master_run_id: str,
    final_status: str,
    final_reason: str,
    run_datetime: str,
    step_results: List[Dict[str, Any]],
    m11_result: Optional[Dict[str, Any]],
    stopped_early: bool,
    summary_paths: Dict[str, str],
) -> None:
    lines = [
        "# TY Read-Only Planning Health Check",
        "",
        f"Project: {project_name}",
        f"Master run ID: {master_run_id}",
        f"Final status: {final_status}",
        f"Final reason: {final_reason}",
        f"Run date/time: {run_datetime}",
        "",
        "## Workflow Summary",
        "",
        "| Step | Module | Status | Reason | Output folder |",
        "|------|--------|--------|--------|---------------|",
    ]
    for step in step_results:
        reason = (step.get("reason") or "").replace("|", "/")
        if len(reason) > 80:
            reason = reason[:77] + "..."
        lines.append(
            f"| {step['step_number']} | {step['module']} | {step['status']} | "
            f"{reason} | {step['output_folder']} |"
        )

    lines.extend(["", "## Key Result", ""])
    if m11_result and final_status in ("PASS", "PASS_WITH_WARNINGS"):
        report_files = m11_result.get("report_files") or []
        m11_report = next((p for p in report_files if str(p).endswith("planning_health_report.md")), "")
        warn_reg = next((p for p in report_files if str(p).endswith("warning_register.csv")), "")
        lines.extend(
            [
                f"- Data Date: {m11_result.get('data_date', '')}",
                f"- Activity rows checked: {m11_result.get('activity_rows_checked', 0)}",
                f"- Warning count: {m11_result.get('warning_count', 0)}",
                f"- High severity count: {m11_result.get('high_severity_count', 0)}",
                f"- Medium severity count: {m11_result.get('medium_severity_count', 0)}",
                f"- Low severity count: {m11_result.get('low_severity_count', 0)}",
                f"- Final planning health report path: {m11_report}",
                f"- Warning register path: {warn_reg}",
            ]
        )
    else:
        lines.append("- M11 report not available (workflow did not complete successfully).")

    lines.extend(["", "## Stop / Failure Information", ""])
    if stopped_early:
        failed = next((s for s in step_results if not is_allowed(s["module"], s["status"])), None)
        if failed:
            lines.extend(
                [
                    f"- Failed module: {failed['module']}",
                    f"- Status: {failed['status']}",
                    f"- Reason: {failed['reason']}",
                    f"- result.json path: {failed['result_json']}",
                ]
            )
        else:
            lines.append("- Workflow stopped before completion.")
    else:
        lines.append("- All workflow steps completed.")

    lines.extend(["", "## Evidence", ""])
    for step in step_results:
        lines.append(f"- {step['module']} result.json: {step['result_json']}")
        if step.get("report_md"):
            lines.append(f"- {step['module']} report.md: {step['report_md']}")
    if m11_result:
        for path_str in m11_result.get("report_files") or []:
            if "planning_health_report" in str(path_str):
                lines.append(f"- Final M11 planning report: {path_str}")
    lines.append(f"- M12 summary JSON: {summary_paths.get('summary_json', '')}")
    lines.append(f"- M12 summary CSV: {summary_paths.get('summary_csv', '')}")
    lines.append(f"- M12 master report: {summary_paths.get('master_report_md', '')}")

    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- This is a read-only check.",
            "- It is based on visible activity table OCR only.",
            "- TY has not exported or scrolled the full schedule.",
            "- OCR errors may exist.",
            "- Raw evidence is preserved in each module output folder.",
            "- Report is for planner review, not automatic approval.",
            "",
            "## Next Recommendation",
        ]
    )
    if final_status in ("PASS", "PASS_WITH_WARNINGS"):
        lines.append("- Review M11 planning report and warning register.")
        lines.append("- Next module can be M13_export_activity_table_csv.")
    else:
        lines.append("- Fix the failed module only and rerun M12.")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_m12_report(evidence: RunEvidence, result: Dict[str, Any]) -> None:
    lines = [
        "# M12 Run Read-Only Health Check",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project name: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Steps total: {result.get('steps_total', STEPS_TOTAL)}",
        f"- Steps completed: {result.get('steps_completed', 0)}",
        f"- Steps failed: {result.get('steps_failed', 0)}",
        f"- Warning/partial steps: {result.get('warning_or_partial_steps', 0)}",
        f"- Final M11 report path: {result.get('final_m11_report_path', '')}",
        "",
        "## Step results",
    ]
    for step in result.get("step_results", []):
        lines.append(
            f"- Step {step['step_number']} {step['module']}: {step['status']} — {step['reason']}"
        )
    lines.extend(["", "## Final decision", result["status"]])
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_m12(project_name: str, *, run_id: Optional[str] = None) -> Dict[str, Any]:
    evidence = build_evidence(run_id or new_run_id())
    project_name = (project_name or "").strip()
    run_datetime = datetime.now().isoformat(timespec="seconds")

    if not project_name:
        result = {
            "run_id": evidence.run_id,
            "module": MODULE_NAME,
            "project_name": "",
            "status": "FAIL_PROJECT_NAME_EMPTY",
            "reason": "project_name is empty",
            "steps_total": STEPS_TOTAL,
            "steps_completed": 0,
            "steps_failed": 0,
            "warning_or_partial_steps": 0,
            "final_m11_report_path": "",
            "final_m11_warning_register_path": "",
            "step_results": [],
            "manual_review_required": False,
            "error": None,
        }
        write_json(evidence.folder / "result.json", result)
        write_m12_report(evidence, result)
        return result

    from m03_open_project_by_name import run_m03  # noqa: WPS433
    from m04_check_project_opened import run_m04  # noqa: WPS433
    from m06_go_to_activities import run_m06  # noqa: WPS433
    from m07_read_activity_table_snapshot import run_m07  # noqa: WPS433
    from m08_read_activity_table_structured import run_m08  # noqa: WPS433
    from m09_read_project_data_date import run_m09  # noqa: WPS433
    from m10_compare_data_date_to_activity_dates import run_m10  # noqa: WPS433
    from m11_generate_planning_health_report import run_m11  # noqa: WPS433

    ctx: Dict[str, Any] = {"project": project_name, "master_run_id": evidence.run_id}
    step_results: List[Dict[str, Any]] = []
    stopped_early = False
    m11_result: Optional[Dict[str, Any]] = None

    steps: List[Tuple[int, str, str, Callable[..., Dict[str, Any]], Callable[[Dict[str, Any]], Dict[str, Any]]]] = [
        (1, M03_MODULE, "M03", run_m03, lambda c: {"project_name": c["project"], "run_id": step_run_id(c["master_run_id"], 1)}),
        (2, M04_MODULE, "M04", run_m04, lambda c: {"project_name": c["project"], "run_id": step_run_id(c["master_run_id"], 2)}),
        (3, M06_MODULE, "M06", run_m06, lambda c: {"project_name": c["project"], "run_id": step_run_id(c["master_run_id"], 3)}),
        (4, M07_MODULE, "M07", run_m07, lambda c: {"project_name": c["project"], "run_id": step_run_id(c["master_run_id"], 4)}),
        (
            5,
            M08_MODULE,
            "M08",
            run_m08,
            lambda c: {
                "project_name": c["project"],
                "m07_folder": c.get("m07_folder"),
                "run_id": step_run_id(c["master_run_id"], 5),
            },
        ),
        (6, M09_MODULE, "M09", run_m09, lambda c: {"project_name": c["project"], "run_id": step_run_id(c["master_run_id"], 6)}),
        (
            7,
            M10_MODULE,
            "M10",
            run_m10,
            lambda c: {
                "project_name": c["project"],
                "m08_folder": c.get("m08_folder"),
                "m09_folder": c.get("m09_folder"),
                "run_id": step_run_id(c["master_run_id"], 7),
            },
        ),
        (
            8,
            M11_MODULE,
            "M11",
            run_m11,
            lambda c: {
                "project_name": c["project"],
                "m08_folder": c.get("m08_folder"),
                "m09_folder": c.get("m09_folder"),
                "m10_folder": c.get("m10_folder"),
                "run_id": step_run_id(c["master_run_id"], 8),
            },
        ),
    ]

    try:
        for step_number, module_name, _label, runner, kwargs_fn in steps:
            started = datetime.now()
            module_run_id = step_run_id(evidence.run_id, step_number)
            kwargs = kwargs_fn(ctx)

            if module_name == M03_MODULE:
                runner(kwargs["project_name"], run_id=kwargs["run_id"])
            elif module_name == M04_MODULE:
                runner(kwargs["project_name"], run_id=kwargs["run_id"])
            elif module_name == M06_MODULE:
                runner(kwargs["project_name"], run_id=kwargs["run_id"])
            elif module_name == M07_MODULE:
                runner(kwargs["project_name"], run_id=kwargs["run_id"])
            elif module_name == M08_MODULE:
                runner(
                    kwargs["project_name"],
                    m07_folder=kwargs.get("m07_folder"),
                    run_id=kwargs["run_id"],
                )
            elif module_name == M09_MODULE:
                runner(kwargs["project_name"], run_id=kwargs["run_id"])
            elif module_name == M10_MODULE:
                runner(
                    kwargs["project_name"],
                    m08_folder=kwargs.get("m08_folder"),
                    m09_folder=kwargs.get("m09_folder"),
                    run_id=kwargs["run_id"],
                )
            elif module_name == M11_MODULE:
                runner(
                    kwargs["project_name"],
                    m08_folder=kwargs.get("m08_folder"),
                    m09_folder=kwargs.get("m09_folder"),
                    m10_folder=kwargs.get("m10_folder"),
                    run_id=kwargs["run_id"],
                )

            finished = datetime.now()
            step = record_step(step_number, module_name, module_run_id, started, finished)
            step_results.append(step)
            evidence.steps.append(f"Step {step_number} {module_name}: {step['status']}")

            folder = step["output_folder"]
            if module_name == M07_MODULE:
                ctx["m07_folder"] = folder
            elif module_name == M08_MODULE:
                ctx["m08_folder"] = folder
            elif module_name == M09_MODULE:
                ctx["m09_folder"] = folder
            elif module_name == M10_MODULE:
                ctx["m10_folder"] = folder
            elif module_name == M11_MODULE:
                m11_result, _, _, _ = read_module_result(module_run_id, module_name)

            status = step["status"]
            if not is_allowed(module_name, status):
                stopped_early = True
                break

            if module_name == M10_MODULE and status in M10_CRITICAL_FAILURES:
                stopped_early = True
                break

        final_status, final_reason = decide_master_status(step_results, stopped_early=stopped_early)

        warning_partial_count = sum(1 for s in step_results if is_warning_partial(s["status"]))
        steps_failed = sum(1 for s in step_results if not is_allowed(s["module"], s["status"]))

        final_m11_report = ""
        final_warn_reg = ""
        if m11_result:
            for path in m11_result.get("report_files") or []:
                if str(path).endswith("planning_health_report.md"):
                    final_m11_report = path
                if str(path).endswith("warning_register.csv"):
                    final_warn_reg = path

        result = {
            "run_id": evidence.run_id,
            "module": MODULE_NAME,
            "project_name": project_name,
            "status": final_status,
            "reason": final_reason,
            "steps_total": STEPS_TOTAL,
            "steps_completed": len(step_results),
            "steps_failed": steps_failed,
            "warning_or_partial_steps": warning_partial_count,
            "final_m11_report_path": final_m11_report,
            "final_m11_warning_register_path": final_warn_reg,
            "step_results": step_results,
            "manual_review_required": any(
                s["status"].startswith("MANUAL_REVIEW") for s in step_results
            ),
            "error": None,
            "run_datetime": run_datetime,
        }

        summary_json = build_summary_json(
            project_name,
            evidence.run_id,
            final_status,
            final_reason,
            step_results,
            m11_result,
            evidence.folder,
        )
        write_json(evidence.folder / "read_only_health_check_summary.json", summary_json)
        write_summary_csv(evidence.folder / "read_only_health_check_summary.csv", step_results)
        write_master_report(
            evidence.folder / "read_only_health_check_master_report.md",
            project_name=project_name,
            master_run_id=evidence.run_id,
            final_status=final_status,
            final_reason=final_reason,
            run_datetime=run_datetime,
            step_results=step_results,
            m11_result=m11_result,
            stopped_early=stopped_early,
            summary_paths=summary_json["m12_summary_files"],
        )
        write_json(evidence.folder / "result.json", result)
        write_m12_report(evidence, result)
        return result

    except Exception as exc:  # noqa: BLE001
        evidence.steps.append(f"exception: {exc}")
        evidence.steps.append(traceback.format_exc())
        result = {
            "run_id": evidence.run_id,
            "module": MODULE_NAME,
            "project_name": project_name,
            "status": "ERROR",
            "reason": str(exc),
            "steps_total": STEPS_TOTAL,
            "steps_completed": len(step_results),
            "steps_failed": 1,
            "warning_or_partial_steps": sum(1 for s in step_results if is_warning_partial(s["status"])),
            "final_m11_report_path": "",
            "final_m11_warning_register_path": "",
            "step_results": step_results,
            "manual_review_required": False,
            "error": traceback.format_exc(),
        }
        write_json(evidence.folder / "result.json", result)
        write_m12_report(evidence, result)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="M12 Run Read-Only Health Check")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    args = parser.parse_args()

    result = run_m12(args.project.strip())
    print(f"M12 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Steps completed: {result.get('steps_completed', 0)} / {result.get('steps_total', STEPS_TOTAL)}")
    print(f"Warning/partial steps: {result.get('warning_or_partial_steps', 0)}")
    print(f"Final M11 report: {result.get('final_m11_report_path', '')}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")
    if result["status"] in ("PASS", "PASS_WITH_WARNINGS"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
