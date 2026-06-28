"""
M10 — Compare Data Date To Activity Dates (Phase 9).

Read-only planning health-check: compares M09 Data Date against M08 activity dates.
Does not touch P6 unless running the full upstream chain.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "04_modules"))

MODULE_NAME = "m10_compare_data_date_to_activity_dates"
M08_MODULE_NAME = "m08_read_activity_table_structured"
M09_MODULE_NAME = "m09_read_project_data_date"
LOW_CONFIDENCE_THRESHOLD = 0.75

MONTH_MAP = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
TEXT_MONTH_RE = re.compile(
    r"(\d{1,2})[-/\s]+([A-Za-z]{3})[-/\s]*(\d{2,4})?",
    re.I,
)
SLASH_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")


@dataclass
class RunEvidence:
    run_id: str
    folder: Path
    analysis_dir: Path
    steps: List[str] = field(default_factory=list)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_evidence(run_id: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / run_id / MODULE_NAME
    analysis = folder / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    return RunEvidence(run_id=run_id, folder=folder, analysis_dir=analysis)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def expand_year(year_str: str) -> Optional[int]:
    if not year_str:
        return None
    year = int(year_str)
    if year < 100:
        return 2000 + year if year < 70 else 1900 + year
    return year


def parse_text_month_date(raw: str) -> Tuple[Optional[date], bool]:
    text = (raw or "").strip().rstrip(",")
    if not text:
        return None, False
    m = TEXT_MONTH_RE.search(text)
    if not m:
        return None, False
    day = int(m.group(1))
    mon_key = m.group(2).lower()[:3]
    if mon_key not in MONTH_MAP:
        return None, True
    month = MONTH_MAP[mon_key]
    year = expand_year(m.group(3)) if m.group(3) else None
    if year is None:
        return None, True
    try:
        return date(year, month, day), False
    except ValueError:
        return None, True


def parse_slash_date(raw: str) -> Tuple[Optional[date], bool]:
    text = (raw or "").strip().rstrip(",")
    m = SLASH_DATE_RE.search(text)
    if not m:
        return None, False
    a, b, y = int(m.group(1)), int(m.group(2)), expand_year(m.group(3))
    if a > 12 and b <= 12:
        day, month = a, b
    elif b > 12 and a <= 12:
        month, day = a, b
    elif a <= 12 and b <= 12:
        day, month = a, b
    else:
        return None, True
    try:
        return date(y, month, day), False
    except ValueError:
        return None, True


def parse_activity_date(
    raw: Optional[str],
    normalized: Optional[str] = None,
) -> Tuple[Optional[date], bool]:
    for candidate in (normalized, raw):
        if not candidate:
            continue
        parsed, issue = parse_text_month_date(candidate)
        if parsed:
            return parsed, False
        if issue:
            return None, True
        parsed, issue = parse_slash_date(candidate)
        if parsed:
            return parsed, False
        if issue:
            return None, True
    if raw and re.search(r"\d", raw):
        lower = raw.lower()
        if any(m in lower for m in MONTH_MAP) or "/" in raw:
            return None, True
    return None, False


def find_latest_m08_folder() -> Optional[Path]:
    runs_root = ROOT / "06_output" / "runs"
    if not runs_root.exists():
        return None
    candidates: List[Tuple[str, Path]] = []
    for run_dir in runs_root.iterdir():
        module_dir = run_dir / M08_MODULE_NAME
        marker = module_dir / "structured" / "activity_table_structured.json"
        if marker.exists():
            candidates.append((run_dir.name, module_dir))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def find_latest_m09_folder() -> Optional[Path]:
    runs_root = ROOT / "06_output" / "runs"
    if not runs_root.exists():
        return None
    candidates: List[Tuple[str, Path]] = []
    for run_dir in runs_root.iterdir():
        module_dir = run_dir / M09_MODULE_NAME
        marker = module_dir / "extracted" / "data_date_result.json"
        if marker.exists():
            candidates.append((run_dir.name, module_dir))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def resolve_source_folder(folder: Optional[str], module_name: str) -> Optional[Path]:
    if not folder:
        return None
    path = Path(folder)
    if path.name in ("structured", "extracted", "analysis"):
        path = path.parent
    return path


def load_m08_rows(m08_folder: Path) -> List[Dict[str, Any]]:
    path = m08_folder / "structured" / "activity_table_structured.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing M08 structured file: {path}")
    data = load_json(path)
    return data.get("rows") or []


def load_m09_data_date(m09_folder: Path) -> Dict[str, Any]:
    path = m09_folder / "extracted" / "data_date_result.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing M09 data date file: {path}")
    return load_json(path)


def parse_data_date(m09_data: Dict[str, Any]) -> Tuple[Optional[date], str, str, str]:
    raw = (m09_data.get("data_date_raw") or "").strip()
    normalized = (m09_data.get("data_date_normalized_candidate") or "").strip()
    for candidate in (normalized, raw):
        if not candidate:
            continue
        parsed, issue = parse_text_month_date(candidate)
        if parsed:
            return parsed, raw, normalized, parsed.isoformat()
        if not issue:
            parsed, issue = parse_slash_date(candidate)
            if parsed:
                return parsed, raw, normalized, parsed.isoformat()
    return None, raw, normalized, ""


def compare_rows(
    rows: List[Dict[str, Any]],
    data_date: date,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    comparisons: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    counts = {
        "start_before_data_date_count": 0,
        "finish_before_data_date_count": 0,
        "date_parse_issue_count": 0,
        "low_confidence_count": 0,
    }

    for row in rows:
        confidence = float(row.get("confidence") or 0.0)
        start_raw = row.get("start_raw")
        finish_raw = row.get("finish_raw")
        start_norm = row.get("start_normalized_candidate")
        finish_norm = row.get("finish_normalized_candidate")

        start_parsed, start_issue = parse_activity_date(start_raw, start_norm)
        finish_parsed, finish_issue = parse_activity_date(finish_raw, finish_norm)

        row_warnings: List[str] = []
        if confidence < LOW_CONFIDENCE_THRESHOLD:
            row_warnings.append("low_confidence_row")
            counts["low_confidence_count"] += 1
        if start_issue:
            row_warnings.append("start_parse_issue")
            counts["date_parse_issue_count"] += 1
        if finish_issue:
            row_warnings.append("finish_parse_issue")
            counts["date_parse_issue_count"] += 1

        start_before = bool(start_parsed and start_parsed < data_date)
        finish_before = bool(finish_parsed and finish_parsed < data_date)
        if start_before:
            row_warnings.append("start_before_data_date")
            counts["start_before_data_date_count"] += 1
        if finish_before:
            row_warnings.append("finish_before_data_date")
            counts["finish_before_data_date_count"] += 1

        comparison = {
            "row_index": row.get("row_index"),
            "activity_id_raw": row.get("activity_id_raw"),
            "activity_id_normalized_candidate": row.get("activity_id_normalized_candidate"),
            "activity_name_raw": row.get("activity_name_raw"),
            "start_raw": start_raw,
            "finish_raw": finish_raw,
            "confidence": confidence,
            "row_text_raw": row.get("row_text_raw"),
            "start_parsed": start_parsed.isoformat() if start_parsed else None,
            "finish_parsed": finish_parsed.isoformat() if finish_parsed else None,
            "start_before_data_date": start_before,
            "finish_before_data_date": finish_before,
            "start_parse_issue": start_issue,
            "finish_parse_issue": finish_issue,
            "low_confidence_row": confidence < LOW_CONFIDENCE_THRESHOLD,
            "warnings": row_warnings,
        }
        comparisons.append(comparison)

        for code in row_warnings:
            warnings.append(
                {
                    "row_index": row.get("row_index"),
                    "warning_code": code,
                    "activity_id_raw": row.get("activity_id_raw"),
                    "activity_name_raw": row.get("activity_name_raw"),
                    "row_text_raw": row.get("row_text_raw"),
                    "message": warning_message(code, row, data_date),
                }
            )

    return comparisons, warnings, counts


def warning_message(code: str, row: Dict[str, Any], data_date: date) -> str:
    aid = row.get("activity_id_raw") or row.get("activity_name_raw") or f"row {row.get('row_index')}"
    if code == "low_confidence_row":
        return f"{aid}: low-confidence OCR row (visible-screen finding only)"
    if code == "start_parse_issue":
        return f"{aid}: start date parse issue — raw={row.get('start_raw')!r}"
    if code == "finish_parse_issue":
        return f"{aid}: finish date parse issue — raw={row.get('finish_raw')!r}"
    if code == "start_before_data_date":
        return f"{aid}: start before Data Date {data_date.isoformat()} (warning only)"
    if code == "finish_before_data_date":
        return f"{aid}: finish before Data Date {data_date.isoformat()} (warning only)"
    return f"{aid}: {code}"


def decide_status(
    rows_checked: int,
    warning_count: int,
    data_date_parsed: str,
) -> Tuple[str, str]:
    if not data_date_parsed:
        return "FAIL_DATA_DATE_MISSING", "No usable Data Date could be parsed from M09 source"
    if rows_checked < 1:
        return "FAIL_NO_ACTIVITY_ROWS", "M08 source present but no activity rows to check"
    if warning_count > 0:
        return (
            "PASS_WITH_WARNINGS",
            f"Compared {rows_checked} row(s); {warning_count} warning(s) on visible-screen findings",
        )
    return (
        "PASS",
        f"Compared {rows_checked} row(s); no warnings on visible-screen findings",
    )


def save_analysis(
    evidence: RunEvidence,
    comparisons: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
    meta: Dict[str, Any],
) -> List[str]:
    json_path = evidence.analysis_dir / "data_date_activity_comparison.json"
    write_json(
        json_path,
        {
            "data_date_parsed": meta.get("data_date_parsed"),
            "data_date_raw": meta.get("data_date_raw"),
            "data_date_normalized_candidate": meta.get("data_date_normalized_candidate"),
            "activity_rows_checked": len(comparisons),
            "comparisons": comparisons,
        },
    )

    warnings_path = evidence.analysis_dir / "warnings.json"
    write_json(
        warnings_path,
        {
            "warning_count": len(warnings),
            "warnings": warnings,
        },
    )

    csv_path = evidence.analysis_dir / "data_date_activity_comparison.csv"
    fieldnames = [
        "row_index",
        "activity_id_raw",
        "activity_name_raw",
        "start_raw",
        "finish_raw",
        "start_parsed",
        "finish_parsed",
        "confidence",
        "start_before_data_date",
        "finish_before_data_date",
        "start_parse_issue",
        "finish_parse_issue",
        "low_confidence_row",
        "warnings",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in comparisons:
            writer.writerow(
                {
                    **{k: row.get(k, "") for k in fieldnames if k != "warnings"},
                    "warnings": "; ".join(row.get("warnings") or []),
                }
            )

    return [str(json_path), str(csv_path), str(warnings_path)]


def run_upstream_chain(project_name: str, evidence: RunEvidence) -> Tuple[Optional[Path], Optional[Path], List[str]]:
    notes: List[str] = ["Running M03 -> M04 -> M06 -> M07 -> M08 -> M09 chain"]
    from m03_open_project_by_name import run_m03  # noqa: WPS433
    from m04_check_project_opened import run_m04  # noqa: WPS433
    from m06_go_to_activities import run_m06  # noqa: WPS433
    from m07_read_activity_table_snapshot import run_m07  # noqa: WPS433
    from m08_read_activity_table_structured import run_m08  # noqa: WPS433
    from m09_read_project_data_date import run_m09  # noqa: WPS433

    m03 = run_m03(project_name, run_id=f"{new_run_id()}_chain_m03")
    notes.append(f"M03 status: {m03.get('status')}")
    m04 = run_m04(project_name, run_id=f"{new_run_id()}_chain_m04")
    notes.append(f"M04 status: {m04.get('status')}")
    m06 = run_m06(project_name, run_id=f"{new_run_id()}_chain_m06")
    notes.append(f"M06 status: {m06.get('status')}")
    m07 = run_m07(project_name, run_id=f"{new_run_id()}_chain_m07")
    notes.append(f"M07 status: {m07.get('status')}")
    if m07.get("status") not in ("PASS", "PASS_PARTIAL_SNAPSHOT"):
        return None, None, notes

    m07_folder = ROOT / "06_output" / "runs" / m07["run_id"] / "m07_read_activity_table_snapshot"
    m08 = run_m08(project_name, m07_folder=str(m07_folder), run_id=f"{new_run_id()}_chain_m08")
    notes.append(f"M08 status: {m08.get('status')}")
    if m08.get("status") not in ("PASS", "PASS_WITH_LOW_CONFIDENCE_ROWS"):
        return None, None, notes

    m09 = run_m09(project_name, run_id=f"{new_run_id()}_chain_m09")
    notes.append(f"M09 status: {m09.get('status')}")
    if m09.get("status") not in ("PASS", "PASS_WITH_DATE_CANDIDATES"):
        return None, None, notes

    m08_folder = ROOT / "06_output" / "runs" / m08["run_id"] / M08_MODULE_NAME
    m09_folder = ROOT / "06_output" / "runs" / m09["run_id"] / M09_MODULE_NAME
    return m08_folder, m09_folder, notes


def finish_result(
    evidence: RunEvidence,
    project_name: str,
    status: str,
    reason: str,
    *,
    source_m08_folder: str = "",
    source_m09_folder: str = "",
    data_date_raw: str = "",
    data_date_normalized_candidate: str = "",
    data_date_parsed: str = "",
    activity_rows_checked: int = 0,
    start_before_data_date_count: int = 0,
    finish_before_data_date_count: int = 0,
    date_parse_issue_count: int = 0,
    low_confidence_count: int = 0,
    warning_count: int = 0,
    analysis_files: Optional[List[str]] = None,
    sample_warnings: Optional[List[Any]] = None,
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
        "data_date_raw": data_date_raw,
        "data_date_normalized_candidate": data_date_normalized_candidate,
        "data_date_parsed": data_date_parsed,
        "activity_rows_checked": activity_rows_checked,
        "start_before_data_date_count": start_before_data_date_count,
        "finish_before_data_date_count": finish_before_data_date_count,
        "date_parse_issue_count": date_parse_issue_count,
        "low_confidence_count": low_confidence_count,
        "warning_count": warning_count,
        "analysis_files": analysis_files or [],
        "sample_warnings": sample_warnings or [],
        "manual_review_required": manual_review_required,
        "error": error,
        "steps": evidence.steps,
    }
    write_json(evidence.folder / "result.json", result)
    write_report(evidence, result)
    return result


def write_report(evidence: RunEvidence, result: Dict[str, Any]) -> None:
    lines = [
        "# M10 Compare Data Date To Activity Dates Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project name: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Source M08 folder: {result.get('source_m08_folder', '')}",
        f"- Source M09 folder: {result.get('source_m09_folder', '')}",
        f"- Data Date raw: {result.get('data_date_raw', '')}",
        f"- Data Date normalized: {result.get('data_date_normalized_candidate', '')}",
        f"- Data Date parsed: {result.get('data_date_parsed', '')}",
        f"- Activity rows checked: {result.get('activity_rows_checked', 0)}",
        f"- Start before Data Date count: {result.get('start_before_data_date_count', 0)}",
        f"- Finish before Data Date count: {result.get('finish_before_data_date_count', 0)}",
        f"- Date parse issue count: {result.get('date_parse_issue_count', 0)}",
        f"- Low confidence count: {result.get('low_confidence_count', 0)}",
        f"- Warning count: {result.get('warning_count', 0)}",
        f"- Sample warnings: {result.get('sample_warnings', [])}",
        "",
        "## Final decision",
        result["status"],
        "",
        "## Next recommendation",
    ]
    if result["status"] in ("PASS", "PASS_WITH_WARNINGS"):
        lines.append("Ready for M10 hard testing.")
    else:
        lines.append("Fix the reported issue and re-run TY_TEST_M10_COMPARE_DATA_DATE.bat")
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_m10(
    project_name: str,
    *,
    m08_folder: Optional[str] = None,
    m09_folder: Optional[str] = None,
    run_chain: bool = False,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    evidence = evidence or build_evidence(run_id or new_run_id())
    project_name = (project_name or "").strip()

    if not project_name:
        return finish_result(
            evidence, "", "FAIL_PROJECT_NAME_EMPTY", "project_name is empty"
        )

    evidence.steps.append("validate project_name")

    try:
        m08_path: Optional[Path]
        m09_path: Optional[Path]
        setup_notes: List[str] = []

        if m08_folder and m09_folder:
            m08_path = resolve_source_folder(m08_folder, M08_MODULE_NAME)
            m09_path = resolve_source_folder(m09_folder, M09_MODULE_NAME)
            evidence.steps.append(f"Using provided M08 folder: {m08_path}")
            evidence.steps.append(f"Using provided M09 folder: {m09_path}")
        elif m08_folder or m09_folder:
            missing = "M09" if m08_folder else "M08"
            return finish_result(
                evidence,
                project_name,
                f"FAIL_{missing}_SOURCE_NOT_FOUND",
                f"Both --m08-folder and --m09-folder required when providing sources; missing {missing}",
            )
        elif run_chain:
            m08_path, m09_path, setup_notes = run_upstream_chain(project_name, evidence)
            evidence.steps.extend(setup_notes)
        else:
            m08_path = find_latest_m08_folder()
            m09_path = find_latest_m09_folder()
            if m08_path:
                evidence.steps.append(f"Using latest M08 folder: {m08_path}")
            if m09_path:
                evidence.steps.append(f"Using latest M09 folder: {m09_path}")
            if not m08_path or not m09_path:
                evidence.steps.append("Latest M08/M09 not found — running upstream chain")
                m08_path, m09_path, chain_notes = run_upstream_chain(project_name, evidence)
                evidence.steps.extend(chain_notes)

        if not m08_path or not m08_path.exists():
            return finish_result(
                evidence,
                project_name,
                "FAIL_M08_SOURCE_NOT_FOUND",
                "M08 source folder not found",
            )
        if not m09_path or not m09_path.exists():
            return finish_result(
                evidence,
                project_name,
                "FAIL_M09_SOURCE_NOT_FOUND",
                "M09 source folder not found",
            )

        evidence.steps.append("load M08 structured rows")
        rows = load_m08_rows(m08_path)
        if not rows:
            return finish_result(
                evidence,
                project_name,
                "FAIL_NO_ACTIVITY_ROWS",
                "No activity rows in M08 structured output",
                source_m08_folder=str(m08_path),
                source_m09_folder=str(m09_path),
            )

        evidence.steps.append("load M09 data date")
        m09_data = load_m09_data_date(m09_path)
        data_date, data_raw, data_norm, data_parsed = parse_data_date(m09_data)
        if not data_date:
            return finish_result(
                evidence,
                project_name,
                "FAIL_DATA_DATE_MISSING",
                "M09 source present but Data Date could not be parsed",
                source_m08_folder=str(m08_path),
                source_m09_folder=str(m09_path),
                data_date_raw=data_raw,
                data_date_normalized_candidate=data_norm,
            )

        evidence.steps.append("compare activity dates to Data Date")
        comparisons, warnings, counts = compare_rows(rows, data_date)
        analysis_files = save_analysis(
            evidence,
            comparisons,
            warnings,
            {
                "data_date_parsed": data_parsed,
                "data_date_raw": data_raw,
                "data_date_normalized_candidate": data_norm,
            },
        )

        status, reason = decide_status(len(comparisons), len(warnings), data_parsed)
        return finish_result(
            evidence,
            project_name,
            status,
            reason,
            source_m08_folder=str(m08_path),
            source_m09_folder=str(m09_path),
            data_date_raw=data_raw,
            data_date_normalized_candidate=data_norm,
            data_date_parsed=data_parsed,
            activity_rows_checked=len(comparisons),
            start_before_data_date_count=counts["start_before_data_date_count"],
            finish_before_data_date_count=counts["finish_before_data_date_count"],
            date_parse_issue_count=counts["date_parse_issue_count"],
            low_confidence_count=counts["low_confidence_count"],
            warning_count=len(warnings),
            analysis_files=analysis_files,
            sample_warnings=warnings[:8],
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
    parser = argparse.ArgumentParser(description="M10 Compare Data Date To Activity Dates")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    parser.add_argument(
        "--m08-folder",
        default=None,
        help="Path to existing M08 run folder (skips P6 chain)",
    )
    parser.add_argument(
        "--m09-folder",
        default=None,
        help="Path to existing M09 run folder (skips P6 chain)",
    )
    parser.add_argument(
        "--run-chain",
        action="store_true",
        help="Force M03->M09 chain before comparison",
    )
    args = parser.parse_args()

    result = run_m10(
        args.project.strip(),
        m08_folder=args.m08_folder,
        m09_folder=args.m09_folder,
        run_chain=bool(args.run_chain),
    )
    print(f"M10 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Source M08 folder: {result.get('source_m08_folder', '')}")
    print(f"Source M09 folder: {result.get('source_m09_folder', '')}")
    print(f"Data Date parsed: {result.get('data_date_parsed', '')}")
    print(f"Activity rows checked: {result.get('activity_rows_checked', 0)}")
    print(f"Warning count: {result.get('warning_count', 0)}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")
    if result["status"] in ("PASS", "PASS_WITH_WARNINGS"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
