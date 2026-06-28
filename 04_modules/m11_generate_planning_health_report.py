"""
M11 — Generate Planning Health Report (Phase 10).

No-P6 report module: reads M08, M09, and M10 outputs and generates
planner-readable health reports.
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
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "04_modules"))

MODULE_NAME = "m11_generate_planning_health_report"
M08_MODULE_NAME = "m08_read_activity_table_structured"
M09_MODULE_NAME = "m09_read_project_data_date"
M10_MODULE_NAME = "m10_compare_data_date_to_activity_dates"
LOW_CONFIDENCE_THRESHOLD = 0.75

M08_REQUIRED = (
    "structured/activity_table_structured.json",
    "structured/activity_table_structured.csv",
    "structured/activity_table_low_confidence_rows.json",
)
M09_REQUIRED = (
    "extracted/data_date_result.json",
    "extracted/data_date_candidates.json",
)
M10_REQUIRED = (
    "analysis/data_date_activity_comparison.json",
    "analysis/data_date_activity_comparison.csv",
    "analysis/warnings.json",
)

SEVERITY_MAP = {
    "low_confidence_row": "LOW",
    "start_parse_issue": "MEDIUM",
    "finish_parse_issue": "MEDIUM",
    "start_before_data_date": "MEDIUM",
    "finish_before_data_date": "HIGH",
    "missing_data_date": "HIGH",
    "no_activity_rows": "HIGH",
}


@dataclass
class RunEvidence:
    run_id: str
    folder: Path
    report_dir: Path
    steps: List[str] = field(default_factory=list)


@dataclass
class SourceBundle:
    m08_folder: Path
    m09_folder: Path
    m10_folder: Path
    m08_structured: Dict[str, Any]
    m08_low_confidence: Dict[str, Any]
    m09_data_date: Dict[str, Any]
    m09_candidates: Dict[str, Any]
    m10_comparison: Dict[str, Any]
    m10_warnings: Dict[str, Any]
    m10_result: Dict[str, Any]


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_evidence(run_id: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / run_id / MODULE_NAME
    report_dir = folder / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    return RunEvidence(run_id=run_id, folder=folder, report_dir=report_dir)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_source_folder(folder: Optional[str]) -> Optional[Path]:
    if not folder:
        return None
    path = Path(folder)
    if path.name in ("structured", "extracted", "analysis", "report"):
        path = path.parent
    return path


def find_latest_module_folder(module_name: str, marker_parts: Tuple[str, ...]) -> Optional[Path]:
    runs_root = ROOT / "06_output" / "runs"
    if not runs_root.exists():
        return None
    candidates: List[Tuple[str, Path]] = []
    for run_dir in runs_root.iterdir():
        module_dir = run_dir / module_name
        marker = module_dir.joinpath(*marker_parts)
        if marker.exists():
            candidates.append((run_dir.name, module_dir))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def find_latest_m08_folder() -> Optional[Path]:
    return find_latest_module_folder(M08_MODULE_NAME, ("structured", "activity_table_structured.json"))


def find_latest_m09_folder() -> Optional[Path]:
    return find_latest_module_folder(M09_MODULE_NAME, ("extracted", "data_date_result.json"))


def find_latest_m10_folder() -> Optional[Path]:
    return find_latest_module_folder(
        M10_MODULE_NAME, ("analysis", "data_date_activity_comparison.json")
    )


def validate_required_files(folder: Path, required: Tuple[str, ...]) -> List[str]:
    missing: List[str] = []
    for rel in required:
        if not (folder / rel).exists():
            missing.append(str(folder / rel))
    return missing


def load_sources(
    m08_folder: Path,
    m09_folder: Path,
    m10_folder: Path,
) -> SourceBundle:
    for name, folder, required in (
        ("M08", m08_folder, M08_REQUIRED),
        ("M09", m09_folder, M09_REQUIRED),
        ("M10", m10_folder, M10_REQUIRED),
    ):
        missing = validate_required_files(folder, required)
        if missing:
            raise FileNotFoundError(f"{name} missing required files: {missing}")

    m10_result_path = m10_folder / "result.json"
    m10_result = load_json(m10_result_path) if m10_result_path.exists() else {}

    return SourceBundle(
        m08_folder=m08_folder,
        m09_folder=m09_folder,
        m10_folder=m10_folder,
        m08_structured=load_json(m08_folder / "structured" / "activity_table_structured.json"),
        m08_low_confidence=load_json(
            m08_folder / "structured" / "activity_table_low_confidence_rows.json"
        ),
        m09_data_date=load_json(m09_folder / "extracted" / "data_date_result.json"),
        m09_candidates=load_json(m09_folder / "extracted" / "data_date_candidates.json"),
        m10_comparison=load_json(
            m10_folder / "analysis" / "data_date_activity_comparison.json"
        ),
        m10_warnings=load_json(m10_folder / "analysis" / "warnings.json"),
        m10_result=m10_result,
    )


def warning_raw_value(warning: Dict[str, Any]) -> str:
    code = warning.get("warning_code", "")
    msg = warning.get("message", "")
    if "raw=" in msg:
        part = msg.split("raw=", 1)[-1].strip().strip("'\"")
        return part
    if code == "low_confidence_row":
        return warning.get("row_text_raw", "")
    return ""


def build_warning_register(sources: SourceBundle) -> List[Dict[str, Any]]:
    register: List[Dict[str, Any]] = []
    for w in sources.m10_warnings.get("warnings", []):
        code = w.get("warning_code", "")
        register.append(
            {
                "row_index": w.get("row_index"),
                "activity_id_raw": w.get("activity_id_raw"),
                "activity_name_raw": w.get("activity_name_raw"),
                "warning_type": code,
                "raw_value": warning_raw_value(w),
                "message": w.get("message", ""),
                "severity": SEVERITY_MAP.get(code, "MEDIUM"),
            }
        )

    data_date_raw = sources.m09_data_date.get("data_date_raw") or ""
    if not data_date_raw and not sources.m09_data_date.get("data_date_normalized_candidate"):
        register.append(
            {
                "row_index": None,
                "activity_id_raw": None,
                "activity_name_raw": None,
                "warning_type": "missing_data_date",
                "raw_value": "",
                "message": "No usable Data Date found in M09 source",
                "severity": "HIGH",
            }
        )

    row_count = int(sources.m08_structured.get("row_count", 0))
    if row_count < 1:
        register.append(
            {
                "row_index": None,
                "activity_id_raw": None,
                "activity_name_raw": None,
                "warning_type": "no_activity_rows",
                "raw_value": "",
                "message": "No activity rows available in M08 structured output",
                "severity": "HIGH",
            }
        )
    return register


def count_severities(register: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for w in register:
        sev = w.get("severity", "MEDIUM")
        if sev in counts:
            counts[sev] += 1
    return counts


def data_date_display(sources: SourceBundle) -> str:
    raw = sources.m09_data_date.get("data_date_raw") or ""
    norm = sources.m09_data_date.get("data_date_normalized_candidate") or ""
    parsed = sources.m10_comparison.get("data_date_parsed") or sources.m10_result.get(
        "data_date_parsed", ""
    )
    if norm:
        return norm
    if raw:
        return raw
    return parsed


def build_executive_summary(
    project_name: str,
    sources: SourceBundle,
    warning_count: int,
    rows_checked: int,
) -> List[str]:
    data_date = data_date_display(sources)
    start_before = int(sources.m10_result.get("start_before_data_date_count", 0))
    finish_before = int(sources.m10_result.get("finish_before_data_date_count", 0))
    parse_issues = int(sources.m10_result.get("date_parse_issue_count", 0))

    bullets = [
        "TY reviewed the visible activity table only.",
        f"The project Data Date was read as {data_date or '(not confirmed)'}."
        if data_date
        else "The project Data Date could not be confirmed from visible-screen evidence.",
        f"{rows_checked} visible activity row(s) were checked.",
    ]
    if warning_count:
        bullets.append(
            f"{warning_count} warning(s) were found"
            + (f", including {parse_issues} date parse issue(s)." if parse_issues else ".")
        )
    else:
        bullets.append("No warnings were found on visible-screen findings.")
    if start_before or finish_before:
        bullets.append(
            f"{start_before} start and {finish_before} finish date(s) appeared before the Data Date on screen."
        )
    else:
        bullets.append("No visible Start/Finish dates were confirmed before the Data Date.")
    return bullets


def overall_health_status(warning_count: int) -> str:
    return "PASS_WITH_WARNINGS" if warning_count > 0 else "PASS"


def generate_reports(
    evidence: RunEvidence,
    project_name: str,
    sources: SourceBundle,
    warning_register: List[Dict[str, Any]],
) -> Tuple[List[str], Dict[str, Any]]:
    rows = sources.m08_structured.get("rows", [])
    rows_checked = int(sources.m10_comparison.get("activity_rows_checked", len(rows)))
    high_conf = int(sources.m08_structured.get("high_confidence_count", 0))
    low_conf = int(sources.m08_structured.get("low_confidence_count", 0))
    warning_count = len(warning_register)
    sev = count_severities(warning_register)
    data_date = data_date_display(sources)
    exec_bullets = build_executive_summary(project_name, sources, warning_count, rows_checked)
    overall = overall_health_status(warning_count)

    report_payload = {
        "project": project_name,
        "report_run_id": evidence.run_id,
        "source_m08_folder": str(sources.m08_folder),
        "source_m09_folder": str(sources.m09_folder),
        "source_m10_folder": str(sources.m10_folder),
        "data_date": data_date,
        "data_date_raw": sources.m09_data_date.get("data_date_raw", ""),
        "data_date_normalized": sources.m09_data_date.get("data_date_normalized_candidate", ""),
        "data_date_parsed": sources.m10_comparison.get("data_date_parsed", ""),
        "data_date_confidence": sources.m09_data_date.get("confidence", 0),
        "candidate_count": sources.m09_candidates.get("candidate_count", 0),
        "visible_activities_checked": rows_checked,
        "high_confidence_rows": high_conf,
        "low_confidence_rows": low_conf,
        "warnings_found": warning_count,
        "start_before_data_date": int(sources.m10_result.get("start_before_data_date_count", 0)),
        "finish_before_data_date": int(sources.m10_result.get("finish_before_data_date_count", 0)),
        "date_parse_issues": int(sources.m10_result.get("date_parse_issue_count", 0)),
        "overall_status": overall,
        "executive_summary": exec_bullets,
        "sample_activity_rows": rows[:5],
        "low_confidence_rows": sources.m08_low_confidence.get("rows", [])[:5],
        "warning_register": warning_register,
        "severity_counts": sev,
    }

    md_lines = [
        "# Planning Health Report",
        "",
        f"Project: {project_name}",
        f"Report run ID: {evidence.run_id}",
        f"Source M08 folder: {sources.m08_folder}",
        f"Source M09 folder: {sources.m09_folder}",
        f"Source M10 folder: {sources.m10_folder}",
        f"Data Date: {data_date}",
        f"Visible activities checked: {rows_checked}",
        f"High confidence rows: {high_conf}",
        f"Low confidence rows: {low_conf}",
        f"Warnings found: {warning_count}",
        f"Start before Data Date: {report_payload['start_before_data_date']}",
        f"Finish before Data Date: {report_payload['finish_before_data_date']}",
        f"Date parse issues: {report_payload['date_parse_issues']}",
        f"Overall status: {overall}",
        "",
        "## Executive Summary",
    ]
    for bullet in exec_bullets:
        md_lines.append(f"- {bullet}")

    md_lines.extend(
        [
            "",
            "## Data Date Evidence",
            "",
            f"- Raw data date: {report_payload['data_date_raw']}",
            f"- Normalized data date: {report_payload['data_date_normalized']}",
            f"- Parsed data date: {report_payload['data_date_parsed']}",
            f"- Confidence: {report_payload['data_date_confidence']}",
            f"- Candidate count: {report_payload['candidate_count']}",
            "",
            "## Visible Activity Table Evidence",
            "",
            f"- Row count: {sources.m08_structured.get('row_count', 0)}",
            f"- Sample activity rows: {rows[:3]}",
            f"- Low confidence rows: {sources.m08_low_confidence.get('rows', [])[:3]}",
            "",
            "## Warning Register",
            "",
            "| Row | Activity ID | Activity Name | Warning | Raw Value | Severity | Message |",
            "|-----|-------------|---------------|---------|-----------|----------|---------|",
        ]
    )
    for w in warning_register[:20]:
        md_lines.append(
            f"| {w.get('row_index', '')} | {w.get('activity_id_raw') or ''} | "
            f"{w.get('activity_name_raw') or ''} | {w.get('warning_type', '')} | "
            f"{w.get('raw_value', '')} | {w.get('severity', '')} | {w.get('message', '')} |"
        )

    md_lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- This report is based on the visible Activities table only.",
            "- TY has not scrolled or exported the full activity list yet.",
            "- OCR errors may exist.",
            "- Raw OCR evidence is preserved in source files.",
            "- This report is for planner review, not automatic schedule approval.",
            "",
            "## Next Recommendation",
        ]
    )
    if warning_count:
        md_lines.append("- Review low-confidence and date parse warning rows.")
        md_lines.append(
            "- Consider standardising P6 layout or exporting activity table for cleaner data."
        )
    else:
        md_lines.append("- Proceed to export/full-table module.")

    report_files: List[str] = []

    md_path = evidence.report_dir / "planning_health_report.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    report_files.append(str(md_path))

    json_path = evidence.report_dir / "planning_health_report.json"
    write_json(json_path, report_payload)
    report_files.append(str(json_path))

    summary_path = evidence.report_dir / "planning_health_summary.csv"
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "project",
                "report_run_id",
                "data_date",
                "visible_activities_checked",
                "high_confidence_rows",
                "low_confidence_rows",
                "warning_count",
                "start_before_data_date",
                "finish_before_data_date",
                "date_parse_issues",
                "overall_status",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "project": project_name,
                "report_run_id": evidence.run_id,
                "data_date": data_date,
                "visible_activities_checked": rows_checked,
                "high_confidence_rows": high_conf,
                "low_confidence_rows": low_conf,
                "warning_count": warning_count,
                "start_before_data_date": report_payload["start_before_data_date"],
                "finish_before_data_date": report_payload["finish_before_data_date"],
                "date_parse_issues": report_payload["date_parse_issues"],
                "overall_status": overall,
            }
        )
    report_files.append(str(summary_path))

    warn_path = evidence.report_dir / "warning_register.csv"
    warn_fields = [
        "row_index",
        "activity_id_raw",
        "activity_name_raw",
        "warning_type",
        "raw_value",
        "message",
        "severity",
    ]
    with warn_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=warn_fields)
        writer.writeheader()
        for w in warning_register:
            writer.writerow({k: w.get(k, "") for k in warn_fields})
    report_files.append(str(warn_path))

    return report_files, report_payload


def run_upstream_chain(project_name: str, evidence: RunEvidence) -> Tuple[Optional[Path], Optional[Path], Optional[Path], List[str]]:
    notes: List[str] = ["Running M03 -> M04 -> M06 -> M07 -> M08 -> M09 -> M10 read-only chain"]
    from m03_open_project_by_name import run_m03  # noqa: WPS433
    from m04_check_project_opened import run_m04  # noqa: WPS433
    from m06_go_to_activities import run_m06  # noqa: WPS433
    from m07_read_activity_table_snapshot import run_m07  # noqa: WPS433
    from m08_read_activity_table_structured import run_m08  # noqa: WPS433
    from m09_read_project_data_date import run_m09  # noqa: WPS433
    from m10_compare_data_date_to_activity_dates import run_m10  # noqa: WPS433

    m03 = run_m03(project_name, run_id=f"{new_run_id()}_chain_m03")
    notes.append(f"M03 status: {m03.get('status')}")
    m04 = run_m04(project_name, run_id=f"{new_run_id()}_chain_m04")
    notes.append(f"M04 status: {m04.get('status')}")
    m06 = run_m06(project_name, run_id=f"{new_run_id()}_chain_m06")
    notes.append(f"M06 status: {m06.get('status')}")
    m07 = run_m07(project_name, run_id=f"{new_run_id()}_chain_m07")
    notes.append(f"M07 status: {m07.get('status')}")
    if m07.get("status") not in ("PASS", "PASS_PARTIAL_SNAPSHOT"):
        return None, None, None, notes

    m07_folder = ROOT / "06_output" / "runs" / m07["run_id"] / "m07_read_activity_table_snapshot"
    m08 = run_m08(project_name, m07_folder=str(m07_folder), run_id=f"{new_run_id()}_chain_m08")
    notes.append(f"M08 status: {m08.get('status')}")
    if m08.get("status") not in ("PASS", "PASS_WITH_LOW_CONFIDENCE_ROWS"):
        return None, None, None, notes

    m08_folder = ROOT / "06_output" / "runs" / m08["run_id"] / M08_MODULE_NAME
    m09 = run_m09(project_name, run_id=f"{new_run_id()}_chain_m09")
    notes.append(f"M09 status: {m09.get('status')}")
    if m09.get("status") not in ("PASS", "PASS_WITH_DATE_CANDIDATES"):
        return None, None, None, notes

    m09_folder = ROOT / "06_output" / "runs" / m09["run_id"] / M09_MODULE_NAME
    m10 = run_m10(
        project_name,
        m08_folder=str(m08_folder),
        m09_folder=str(m09_folder),
        run_id=f"{new_run_id()}_chain_m10",
    )
    notes.append(f"M10 status: {m10.get('status')}")
    if m10.get("status") not in ("PASS", "PASS_WITH_WARNINGS"):
        return None, None, None, notes

    m10_folder = ROOT / "06_output" / "runs" / m10["run_id"] / M10_MODULE_NAME
    return m08_folder, m09_folder, m10_folder, notes


def finish_result(
    evidence: RunEvidence,
    project_name: str,
    status: str,
    reason: str,
    *,
    source_m08_folder: str = "",
    source_m09_folder: str = "",
    source_m10_folder: str = "",
    data_date: str = "",
    activity_rows_checked: int = 0,
    warning_count: int = 0,
    high_severity_count: int = 0,
    medium_severity_count: int = 0,
    low_severity_count: int = 0,
    report_files: Optional[List[str]] = None,
    manual_review_required: bool = False,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    result = {
        "run_id": evidence.run_id,
        "module": MODULE_NAME,
        "project_name": project_name,
        "status": status,
        "reason": reason,
        "source_m08_folder": source_m08_folder,
        "source_m09_folder": source_m09_folder,
        "source_m10_folder": source_m10_folder,
        "data_date": data_date,
        "activity_rows_checked": activity_rows_checked,
        "warning_count": warning_count,
        "high_severity_count": high_severity_count,
        "medium_severity_count": medium_severity_count,
        "low_severity_count": low_severity_count,
        "report_files": report_files or [],
        "manual_review_required": manual_review_required,
        "error": error,
        "steps": evidence.steps,
    }
    write_json(evidence.folder / "result.json", result)
    write_module_report(evidence, result)
    return result


def write_module_report(evidence: RunEvidence, result: Dict[str, Any]) -> None:
    lines = [
        "# M11 Generate Planning Health Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project name: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Source M08 folder: {result.get('source_m08_folder', '')}",
        f"- Source M09 folder: {result.get('source_m09_folder', '')}",
        f"- Source M10 folder: {result.get('source_m10_folder', '')}",
        f"- Data Date: {result.get('data_date', '')}",
        f"- Activity rows checked: {result.get('activity_rows_checked', 0)}",
        f"- Warning count: {result.get('warning_count', 0)}",
        f"- High severity count: {result.get('high_severity_count', 0)}",
        f"- Medium severity count: {result.get('medium_severity_count', 0)}",
        f"- Low severity count: {result.get('low_severity_count', 0)}",
        f"- Report files: {result.get('report_files', [])}",
        "",
        "## Final decision",
        result["status"],
        "",
        "## Next recommendation",
    ]
    if result["status"] in ("PASS", "PASS_WITH_WARNINGS"):
        lines.append("Ready for M11 hard testing.")
    else:
        lines.append("Fix the reported issue and re-run TY_TEST_M11_HEALTH_REPORT.bat")
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def decide_status(warning_count: int) -> Tuple[str, str]:
    if warning_count > 0:
        return (
            "PASS_WITH_WARNINGS",
            f"Planning health report created with {warning_count} warning(s)",
        )
    return "PASS", "Planning health report created with no warnings"


def run_m11(
    project_name: str,
    *,
    m08_folder: Optional[str] = None,
    m09_folder: Optional[str] = None,
    m10_folder: Optional[str] = None,
    run_chain: bool = False,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    evidence = evidence or build_evidence(run_id or new_run_id())
    project_name = (project_name or "").strip()

    if not project_name:
        return finish_result(evidence, "", "FAIL_PROJECT_NAME_EMPTY", "project_name is empty")

    evidence.steps.append("validate project_name")

    try:
        m08_path: Optional[Path]
        m09_path: Optional[Path]
        m10_path: Optional[Path]

        provided = sum(1 for f in (m08_folder, m09_folder, m10_folder) if f)
        if provided == 3:
            m08_path = resolve_source_folder(m08_folder)
            m09_path = resolve_source_folder(m09_folder)
            m10_path = resolve_source_folder(m10_folder)
            evidence.steps.append(f"Using provided M08 folder: {m08_path}")
            evidence.steps.append(f"Using provided M09 folder: {m09_path}")
            evidence.steps.append(f"Using provided M10 folder: {m10_path}")
        elif provided > 0:
            return finish_result(
                evidence,
                project_name,
                "FAIL_REPORT_SOURCE_INVALID",
                "All three source folders (--m08-folder, --m09-folder, --m10-folder) required together",
            )
        elif run_chain:
            m08_path, m09_path, m10_path, notes = run_upstream_chain(project_name, evidence)
            evidence.steps.extend(notes)
        else:
            m08_path = find_latest_m08_folder()
            m09_path = find_latest_m09_folder()
            m10_path = find_latest_m10_folder()
            if m08_path:
                evidence.steps.append(f"Using latest M08 folder: {m08_path}")
            if m09_path:
                evidence.steps.append(f"Using latest M09 folder: {m09_path}")
            if m10_path:
                evidence.steps.append(f"Using latest M10 folder: {m10_path}")
            if not m08_path or not m09_path or not m10_path:
                evidence.steps.append("Latest M08/M09/M10 not all found — running upstream chain")
                m08_path, m09_path, m10_path, chain_notes = run_upstream_chain(
                    project_name, evidence
                )
                evidence.steps.extend(chain_notes)

        if not m08_path or not m08_path.exists():
            return finish_result(
                evidence, project_name, "FAIL_M08_SOURCE_NOT_FOUND", "M08 source folder not found"
            )
        if not m09_path or not m09_path.exists():
            return finish_result(
                evidence, project_name, "FAIL_M09_SOURCE_NOT_FOUND", "M09 source folder not found"
            )
        if not m10_path or not m10_path.exists():
            return finish_result(
                evidence, project_name, "FAIL_M10_SOURCE_NOT_FOUND", "M10 source folder not found"
            )

        evidence.steps.append("load M08/M09/M10 source files")
        try:
            sources = load_sources(m08_path, m09_path, m10_path)
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as exc:
            return finish_result(
                evidence,
                project_name,
                "FAIL_REPORT_SOURCE_INVALID",
                f"Source files could not be parsed: {exc}",
                source_m08_folder=str(m08_path),
                source_m09_folder=str(m09_path),
                source_m10_folder=str(m10_path),
            )

        evidence.steps.append("build warning register")
        warning_register = build_warning_register(sources)
        sev = count_severities(warning_register)

        evidence.steps.append("generate planning health report")
        report_files, payload = generate_reports(
            evidence, project_name, sources, warning_register
        )
        status, reason = decide_status(len(warning_register))

        return finish_result(
            evidence,
            project_name,
            status,
            reason,
            source_m08_folder=str(m08_path),
            source_m09_folder=str(m09_path),
            source_m10_folder=str(m10_path),
            data_date=data_date_display(sources),
            activity_rows_checked=int(payload.get("visible_activities_checked", 0)),
            warning_count=len(warning_register),
            high_severity_count=sev["HIGH"],
            medium_severity_count=sev["MEDIUM"],
            low_severity_count=sev["LOW"],
            report_files=report_files,
        )

    except Exception as exc:  # noqa: BLE001
        evidence.steps.append(f"exception: {exc}")
        evidence.steps.append(traceback.format_exc())
        return finish_result(
            evidence,
            project_name,
            "ERROR",
            str(exc),
            error=traceback.format_exc(),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="M11 Generate Planning Health Report")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    parser.add_argument("--m08-folder", default=None, help="Existing M08 run folder")
    parser.add_argument("--m09-folder", default=None, help="Existing M09 run folder")
    parser.add_argument("--m10-folder", default=None, help="Existing M10 run folder")
    parser.add_argument(
        "--run-chain",
        action="store_true",
        help="Force M03->M10 chain before report generation",
    )
    args = parser.parse_args()

    result = run_m11(
        args.project.strip(),
        m08_folder=args.m08_folder,
        m09_folder=args.m09_folder,
        m10_folder=args.m10_folder,
        run_chain=bool(args.run_chain),
    )
    print(f"M11 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Source M08 folder: {result.get('source_m08_folder', '')}")
    print(f"Source M09 folder: {result.get('source_m09_folder', '')}")
    print(f"Source M10 folder: {result.get('source_m10_folder', '')}")
    print(f"Data Date: {result.get('data_date', '')}")
    print(f"Activity rows checked: {result.get('activity_rows_checked', 0)}")
    print(f"Warning count: {result.get('warning_count', 0)}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")
    if result["status"] in ("PASS", "PASS_WITH_WARNINGS"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
