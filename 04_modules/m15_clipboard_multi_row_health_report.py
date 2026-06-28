"""
M15 — Clipboard Multi Row Health Report (Phase 14).

Read-only report module: reads M14 clipboard multi-row output and M09 Data Date,
compares clipboard-derived activity rows against the project Data Date, and
generates planner-readable health reports. No P6 interaction when source folders
are provided.
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

MODULE_NAME = "m15_clipboard_multi_row_health_report"
M14_MODULE_NAME = "m14_copy_visible_activity_rows_multi_select"
M09_MODULE_NAME = "m09_read_project_data_date"

ACTIVITY_ID_CLIP = re.compile(r"\bA\d{3,5}[A-Za-z0-9]?\b", re.I)
HEADER_HINTS = ("activity", "activity name", "start", "finish", "wbs", "activity id", "resources")

M14_REQUIRED = (
    "clipboard/clipboard_raw.txt",
    "clipboard/clipboard_table.csv",
    "clipboard/clipboard_table.json",
    "clipboard/clipboard_validation.json",
    "clipboard/row_selection_targets.json",
)
M09_REQUIRED = (
    "extracted/data_date_result.json",
    "extracted/data_date_candidates.json",
)

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

SEVERITY_MAP = {
    "missing_activity_id": "LOW",
    "missing_start": "LOW",
    "missing_finish": "LOW",
    "missing_resources": "LOW",
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
    m14_folder: Path
    m09_folder: Path
    clipboard_raw: str
    clipboard_table: Dict[str, Any]
    clipboard_validation: Dict[str, Any]
    row_selection_targets: Dict[str, Any]
    m09_data_date: Dict[str, Any]
    m09_candidates: Dict[str, Any]
    m14_result: Dict[str, Any]


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


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def resolve_source_folder(folder: Optional[str]) -> Optional[Path]:
    if not folder:
        return None
    path = Path(folder)
    if path.name in ("clipboard", "extracted", "report"):
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


def find_latest_m14_folder() -> Optional[Path]:
    return find_latest_module_folder(M14_MODULE_NAME, ("clipboard", "clipboard_table.json"))


def find_latest_m09_folder() -> Optional[Path]:
    return find_latest_module_folder(M09_MODULE_NAME, ("extracted", "data_date_result.json"))


def validate_required_files(folder: Path, required: Tuple[str, ...]) -> List[str]:
    missing: List[str] = []
    for rel in required:
        if not (folder / rel).exists():
            missing.append(str(folder / rel))
    return missing


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


def parse_activity_date(raw: Optional[str]) -> Tuple[Optional[date], bool]:
    if not raw or not str(raw).strip():
        return None, False
    candidate = str(raw).strip()
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
    if re.search(r"\d", candidate):
        lower = candidate.lower()
        if any(m in lower for m in MONTH_MAP) or "/" in candidate:
            return None, True
    return None, False


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


def is_header_row(row: List[str]) -> bool:
    blob = normalize_text(" ".join(row))
    strong_headers = ("activity id", "activity name", "start", "finish", "resources", "wbs")
    matches = sum(1 for h in strong_headers if h in blob)
    return matches >= 2


def is_summary_span_row(row: List[str], mapping: Dict[str, int]) -> bool:
    """P6 multi-select may emit a span summary row (e.g. 001 with empty name)."""
    aid = cell_at(row, mapping, "activity_id")
    name = cell_at(row, mapping, "activity_name")
    if ACTIVITY_ID_CLIP.search(aid):
        return False
    if aid.isdigit() and not name.strip():
        return True
    return False


def is_activity_like_row(row: List[str], mapping: Dict[str, int]) -> bool:
    if not row or is_summary_span_row(row, mapping):
        return False
    blob = " ".join(row)
    if ACTIVITY_ID_CLIP.search(blob):
        return True
    start = cell_at(row, mapping, "start")
    finish = cell_at(row, mapping, "finish")
    name = cell_at(row, mapping, "activity_name")
    if start and finish and name.strip():
        return True
    return False


def map_column_indices(header_row: List[str]) -> Dict[str, int]:
    mapping: Dict[str, int] = {}
    for idx, cell in enumerate(header_row):
        norm = normalize_text(cell)
        if "activity id" in norm or norm == "id":
            mapping["activity_id"] = idx
        elif "activity name" in norm or norm == "name":
            mapping["activity_name"] = idx
        elif "start" in norm and "finish" not in norm:
            mapping["start"] = idx
        elif "finish" in norm:
            mapping["finish"] = idx
        elif "resource" in norm:
            mapping["resources"] = idx
    return mapping


def default_column_indices(num_cols: int) -> Dict[str, int]:
    mapping: Dict[str, int] = {"activity_id": 0}
    if num_cols > 1:
        mapping["activity_name"] = 1
    if num_cols > 2:
        mapping["start"] = 2
    if num_cols > 3:
        mapping["finish"] = 3
    if num_cols > 4:
        mapping["resources"] = 4
    return mapping


def cell_at(row: List[str], mapping: Dict[str, int], key: str) -> str:
    idx = mapping.get(key)
    if idx is None or idx >= len(row):
        return ""
    return row[idx].strip()


def extract_activity_id(row: List[str], mapping: Dict[str, int]) -> str:
    explicit = cell_at(row, mapping, "activity_id")
    if explicit and ACTIVITY_ID_CLIP.search(explicit):
        m = ACTIVITY_ID_CLIP.search(explicit)
        return m.group(0).upper() if m else explicit
    for cell in row:
        m = ACTIVITY_ID_CLIP.search(cell)
        if m:
            return m.group(0).upper()
    return explicit


def parse_clipboard_activity_rows(clipboard_table: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_rows: List[List[str]] = clipboard_table.get("rows") or []
    if not raw_rows:
        return []

    header_mapping: Dict[str, int] = {}
    data_rows = raw_rows
    if raw_rows and is_header_row(raw_rows[0]):
        header_mapping = map_column_indices(raw_rows[0])
        data_rows = raw_rows[1:]
    elif raw_rows:
        header_mapping = default_column_indices(len(raw_rows[0]))

    parsed: List[Dict[str, Any]] = []
    row_index = 0
    for row in data_rows:
        row = [c.strip() for c in row]
        if not row or not any(c.strip() for c in row):
            continue
        if is_header_row(row):
            continue
        mapping = header_mapping or default_column_indices(len(row))
        if not is_activity_like_row(row, mapping):
            continue
        row_index += 1
        parsed.append(
            {
                "row_index": row_index,
                "activity_id": extract_activity_id(row, mapping),
                "activity_name": cell_at(row, mapping, "activity_name"),
                "start_raw": cell_at(row, mapping, "start"),
                "finish_raw": cell_at(row, mapping, "finish"),
                "resources_raw": cell_at(row, mapping, "resources"),
                "row_text_raw": "\t".join(row),
            }
        )
    return parsed


def warning_message(code: str, row: Dict[str, Any], data_date: Optional[date]) -> str:
    aid = row.get("activity_id") or row.get("activity_name") or f"row {row.get('row_index')}"
    if code == "missing_activity_id":
        return f"{aid}: missing Activity ID on clipboard row (warning only)"
    if code == "missing_start":
        return f"{aid}: missing Start date on clipboard row (warning only)"
    if code == "missing_finish":
        return f"{aid}: missing Finish date on clipboard row (warning only)"
    if code == "start_parse_issue":
        return f"{aid}: start date parse issue — raw={row.get('start_raw')!r}"
    if code == "finish_parse_issue":
        return f"{aid}: finish date parse issue — raw={row.get('finish_raw')!r}"
    if code == "start_before_data_date" and data_date:
        return f"{aid}: start before Data Date {data_date.isoformat()} (warning only)"
    if code == "finish_before_data_date" and data_date:
        return f"{aid}: finish before Data Date {data_date.isoformat()} (warning only)"
    return f"{aid}: {code}"


def compare_clipboard_rows(
    rows: List[Dict[str, Any]],
    data_date: Optional[date],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    comparisons: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    counts = {
        "start_before_data_date_count": 0,
        "finish_before_data_date_count": 0,
        "date_parse_issue_count": 0,
    }

    for row in rows:
        row_warnings: List[str] = []
        activity_id = (row.get("activity_id") or "").strip()
        start_raw = row.get("start_raw") or ""
        finish_raw = row.get("finish_raw") or ""

        if not activity_id:
            row_warnings.append("missing_activity_id")
        if not start_raw.strip():
            row_warnings.append("missing_start")
        if not finish_raw.strip():
            row_warnings.append("missing_finish")

        start_parsed, start_issue = parse_activity_date(start_raw)
        finish_parsed, finish_issue = parse_activity_date(finish_raw)
        if start_issue:
            row_warnings.append("start_parse_issue")
            counts["date_parse_issue_count"] += 1
        if finish_issue:
            row_warnings.append("finish_parse_issue")
            counts["date_parse_issue_count"] += 1

        start_before = bool(data_date and start_parsed and start_parsed < data_date)
        finish_before = bool(data_date and finish_parsed and finish_parsed < data_date)
        if start_before:
            row_warnings.append("start_before_data_date")
            counts["start_before_data_date_count"] += 1
        if finish_before:
            row_warnings.append("finish_before_data_date")
            counts["finish_before_data_date_count"] += 1

        comparison = {
            **row,
            "start_parsed": start_parsed.isoformat() if start_parsed else None,
            "finish_parsed": finish_parsed.isoformat() if finish_parsed else None,
            "start_before_data_date": start_before,
            "finish_before_data_date": finish_before,
            "start_parse_issue": start_issue,
            "finish_parse_issue": finish_issue,
            "warnings": row_warnings,
        }
        comparisons.append(comparison)

        for code in row_warnings:
            warnings.append(
                {
                    "row_index": row.get("row_index"),
                    "activity_id": row.get("activity_id"),
                    "activity_name": row.get("activity_name"),
                    "warning_type": code,
                    "raw_value": row.get("start_raw") if "start" in code else row.get("finish_raw")
                    if "finish" in code
                    else row.get("activity_id") or row.get("row_text_raw"),
                    "message": warning_message(code, row, data_date),
                    "severity": SEVERITY_MAP.get(code, "MEDIUM"),
                }
            )

    return comparisons, warnings, counts


def count_severities(register: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for w in register:
        sev = w.get("severity", "MEDIUM")
        if sev in counts:
            counts[sev] += 1
    return counts


def load_sources(m14_folder: Path, m09_folder: Path) -> SourceBundle:
    for name, folder, required in (
        ("M14", m14_folder, M14_REQUIRED),
        ("M09", m09_folder, M09_REQUIRED),
    ):
        missing = validate_required_files(folder, required)
        if missing:
            raise FileNotFoundError(f"{name} missing required files: {missing}")

    m14_result_path = m14_folder / "result.json"
    m14_result = load_json(m14_result_path) if m14_result_path.exists() else {}

    return SourceBundle(
        m14_folder=m14_folder,
        m09_folder=m09_folder,
        clipboard_raw=(m14_folder / "clipboard" / "clipboard_raw.txt").read_text(
            encoding="utf-8", errors="replace"
        ),
        clipboard_table=load_json(m14_folder / "clipboard" / "clipboard_table.json"),
        clipboard_validation=load_json(m14_folder / "clipboard" / "clipboard_validation.json"),
        row_selection_targets=load_json(m14_folder / "clipboard" / "row_selection_targets.json"),
        m09_data_date=load_json(m09_folder / "extracted" / "data_date_result.json"),
        m09_candidates=load_json(m09_folder / "extracted" / "data_date_candidates.json"),
        m14_result=m14_result,
    )


def build_executive_summary(
    project_name: str,
    data_date_display: str,
    rows_checked: int,
    warning_count: int,
    counts: Dict[str, int],
) -> List[str]:
    return [
        "TY used P6 clipboard table data copied from visible activity rows.",
        "The report is based on visible selected rows only.",
        "Data Date was read from M09.",
        "Clipboard rows were compared against the Data Date.",
        "Warnings are for planner review only.",
        f"Project: {project_name}.",
        f"Data Date: {data_date_display or '(not confirmed)'}.",
        f"{rows_checked} clipboard activity row(s) were checked.",
        f"{warning_count} warning(s) found"
        + (
            f" ({counts.get('start_before_data_date_count', 0)} start before Data Date, "
            f"{counts.get('finish_before_data_date_count', 0)} finish before Data Date, "
            f"{counts.get('date_parse_issue_count', 0)} date parse issue(s))."
            if warning_count
            else "."
        ),
    ]


def generate_reports(
    evidence: RunEvidence,
    project_name: str,
    sources: SourceBundle,
    comparisons: List[Dict[str, Any]],
    warning_register: List[Dict[str, Any]],
    data_date_raw: str,
    data_date_normalized: str,
    data_date_parsed: str,
    counts: Dict[str, int],
) -> Tuple[List[str], Dict[str, Any]]:
    rows_checked = len(comparisons)
    warning_count = len(warning_register)
    sev = count_severities(warning_register)
    overall = "PASS_WITH_WARNINGS" if warning_count else "PASS"
    data_date_display = data_date_normalized or data_date_raw or data_date_parsed
    exec_bullets = build_executive_summary(
        project_name, data_date_display, rows_checked, warning_count, counts
    )

    report_payload = {
        "project": project_name,
        "report_run_id": evidence.run_id,
        "source_m14_folder": str(sources.m14_folder),
        "source_m09_folder": str(sources.m09_folder),
        "data_date_raw": data_date_raw,
        "data_date_normalized": data_date_normalized,
        "data_date_parsed": data_date_parsed,
        "data_date_confidence": sources.m09_data_date.get("confidence", 0),
        "candidate_count": sources.m09_candidates.get("candidate_count", 0),
        "clipboard_line_count": sources.clipboard_validation.get("line_count", 0),
        "activity_like_row_count": sources.clipboard_validation.get("activity_like_row_count", 0),
        "headers_detected": sources.clipboard_validation.get("headers_detected", []),
        "clipboard_rows_checked": rows_checked,
        "warnings_found": warning_count,
        "start_before_data_date": counts.get("start_before_data_date_count", 0),
        "finish_before_data_date": counts.get("finish_before_data_date_count", 0),
        "date_parse_issues": counts.get("date_parse_issue_count", 0),
        "overall_status": overall,
        "executive_summary": exec_bullets,
        "sample_clipboard_rows": comparisons[:5],
        "m14_validation_summary": sources.clipboard_validation,
        "warning_register": warning_register,
        "severity_counts": sev,
    }

    md_lines = [
        "# Clipboard Multi-Row Planning Health Report",
        "",
        f"Project: {project_name}",
        f"Run ID: {evidence.run_id}",
        f"Source M14 folder: {sources.m14_folder}",
        f"Source M09 folder: {sources.m09_folder}",
        f"Data Date: {data_date_display}",
        f"Clipboard activity rows checked: {rows_checked}",
        f"Warnings found: {warning_count}",
        f"Start before Data Date: {counts.get('start_before_data_date_count', 0)}",
        f"Finish before Data Date: {counts.get('finish_before_data_date_count', 0)}",
        f"Date parse issues: {counts.get('date_parse_issue_count', 0)}",
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
            f"- Raw data date: {data_date_raw or '(none)'}",
            f"- Normalized data date: {data_date_normalized or '(none)'}",
            f"- Parsed data date: {data_date_parsed or '(none)'}",
            f"- Confidence: {sources.m09_data_date.get('confidence', 0)}",
            f"- Candidate count: {sources.m09_candidates.get('candidate_count', 0)}",
            "",
            "## Clipboard Table Evidence",
            f"- Clipboard line count: {sources.clipboard_validation.get('line_count', 0)}",
            f"- Activity-like row count: {sources.clipboard_validation.get('activity_like_row_count', 0)}",
            f"- Detected headers: {sources.clipboard_validation.get('headers_detected', [])}",
            "",
            "### Sample clipboard rows",
        ]
    )
    for row in comparisons[:5]:
        md_lines.append(
            f"- {row.get('activity_id', '?')}: {row.get('activity_name', '')} | "
            f"Start {row.get('start_raw', '')} | Finish {row.get('finish_raw', '')}"
        )
    md_lines.extend(
        [
            "",
            "### Source M14 validation summary",
            json.dumps(sources.clipboard_validation, indent=2),
            "",
            "## Warning Register",
        ]
    )
    if warning_register:
        for w in warning_register:
            md_lines.append(
                f"- Row {w.get('row_index')} | {w.get('activity_id')} | "
                f"{w.get('warning_type')} | {w.get('severity')} | {w.get('message')}"
            )
    else:
        md_lines.append("- No warnings on visible clipboard rows.")

    md_lines.extend(
        [
            "",
            "## Limitations",
            "- This report is based on selected visible clipboard rows only.",
            "- TY has not exported the full activity list.",
            "- TY has not scrolled the full schedule.",
            "- Clipboard data is cleaner than OCR but may still depend on visible layout.",
            "- This report is for planner review, not automatic schedule approval.",
            "",
            "## Next Recommendation",
        ]
    )
    if warning_count:
        md_lines.append("- Review warning register.")
        md_lines.append("- Consider exporting the full activity table after export module is stable.")
    else:
        md_lines.append("- Proceed to full-table export module.")

    report_files: List[str] = []

    md_path = evidence.report_dir / "clipboard_health_report.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    report_files.append(str(md_path))

    json_path = evidence.report_dir / "clipboard_health_report.json"
    write_json(json_path, report_payload)
    report_files.append(str(json_path))

    rows_path = evidence.report_dir / "clipboard_activity_rows.csv"
    row_fields = [
        "row_index",
        "activity_id",
        "activity_name",
        "start_raw",
        "finish_raw",
        "resources_raw",
        "row_text_raw",
        "start_parsed",
        "finish_parsed",
        "start_before_data_date",
        "finish_before_data_date",
        "start_parse_issue",
        "finish_parse_issue",
    ]
    with rows_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=row_fields, extrasaction="ignore")
        writer.writeheader()
        for row in comparisons:
            writer.writerow({k: row.get(k, "") for k in row_fields})
    report_files.append(str(rows_path))

    warn_path = evidence.report_dir / "clipboard_warning_register.csv"
    warn_fields = [
        "row_index",
        "activity_id",
        "activity_name",
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


def run_upstream_chain(
    project_name: str,
    evidence: RunEvidence,
    *,
    max_rows: int = 3,
) -> Tuple[Optional[Path], Optional[Path], List[str]]:
    notes: List[str] = ["Running M03 -> M04 -> M06 -> M09 -> M14 read-only chain"]
    from m03_open_project_by_name import run_m03  # noqa: WPS433
    from m04_check_project_opened import run_m04  # noqa: WPS433
    from m06_go_to_activities import run_m06  # noqa: WPS433
    from m09_read_project_data_date import run_m09  # noqa: WPS433
    from m14_copy_visible_activity_rows_multi_select import run_m14  # noqa: WPS433

    prefix = f"{evidence.run_id}_chain"
    m03 = run_m03(project_name, run_id=f"{prefix}_m03")
    notes.append(f"M03 status: {m03.get('status')}")
    m04 = run_m04(project_name, run_id=f"{prefix}_m04")
    notes.append(f"M04 status: {m04.get('status')}")
    m06 = run_m06(project_name, run_id=f"{prefix}_m06")
    notes.append(f"M06 status: {m06.get('status')}")
    m09 = run_m09(project_name, run_id=f"{prefix}_m09")
    notes.append(f"M09 status: {m09.get('status')}")
    if m09.get("status") not in ("PASS", "PASS_WITH_DATE_CANDIDATES"):
        return None, None, notes

    m09_folder = ROOT / "06_output" / "runs" / m09["run_id"] / M09_MODULE_NAME
    m14 = run_m14(project_name, max_rows=max_rows, run_id=f"{prefix}_m14")
    notes.append(f"M14 status: {m14.get('status')}")
    if m14.get("status") not in ("PASS", "PASS_PARTIAL_CLIPBOARD"):
        return None, None, notes

    m14_folder = ROOT / "06_output" / "runs" / m14["run_id"] / M14_MODULE_NAME
    return m14_folder, m09_folder, notes


def decide_status(
    rows_checked: int,
    warning_count: int,
    data_date_parsed: str,
) -> Tuple[str, str]:
    if not data_date_parsed:
        return "FAIL_DATA_DATE_MISSING", "No usable Data Date could be parsed from M09 source"
    if rows_checked < 1:
        return "FAIL_NO_CLIPBOARD_ROWS", "M14 source present but no activity-like clipboard rows parsed"
    if warning_count > 0:
        return (
            "PASS_WITH_WARNINGS",
            f"Compared {rows_checked} clipboard row(s); {warning_count} warning(s) for planner review",
        )
    return (
        "PASS",
        f"Compared {rows_checked} clipboard row(s); no warnings on visible clipboard findings",
    )


def finish_result(
    evidence: RunEvidence,
    project_name: str,
    status: str,
    reason: str,
    *,
    source_m14_folder: str = "",
    source_m09_folder: str = "",
    data_date_raw: str = "",
    data_date_normalized_candidate: str = "",
    data_date_parsed: str = "",
    clipboard_rows_checked: int = 0,
    start_before_data_date_count: int = 0,
    finish_before_data_date_count: int = 0,
    date_parse_issue_count: int = 0,
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
        "source_m14_folder": source_m14_folder,
        "source_m09_folder": source_m09_folder,
        "data_date_raw": data_date_raw,
        "data_date_normalized_candidate": data_date_normalized_candidate,
        "data_date_parsed": data_date_parsed,
        "clipboard_rows_checked": clipboard_rows_checked,
        "start_before_data_date_count": start_before_data_date_count,
        "finish_before_data_date_count": finish_before_data_date_count,
        "date_parse_issue_count": date_parse_issue_count,
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
        "# M15 Clipboard Multi Row Health Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project name: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Source M14 folder: {result.get('source_m14_folder', '')}",
        f"- Source M09 folder: {result.get('source_m09_folder', '')}",
        f"- Data Date raw: {result.get('data_date_raw', '')}",
        f"- Data Date normalized: {result.get('data_date_normalized_candidate', '')}",
        f"- Data Date parsed: {result.get('data_date_parsed', '')}",
        f"- Clipboard rows checked: {result.get('clipboard_rows_checked', 0)}",
        f"- Start before Data Date count: {result.get('start_before_data_date_count', 0)}",
        f"- Finish before Data Date count: {result.get('finish_before_data_date_count', 0)}",
        f"- Date parse issue count: {result.get('date_parse_issue_count', 0)}",
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
        lines.append("Ready for M15 hard testing.")
    else:
        lines.append("Fix the reported issue and re-run TY_TEST_M15_CLIPBOARD_HEALTH_REPORT.bat")
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_m15(
    project_name: str,
    *,
    m14_folder: Optional[str] = None,
    m09_folder: Optional[str] = None,
    max_rows: int = 3,
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
        m14_path: Optional[Path]
        m09_path: Optional[Path]

        provided = sum(1 for f in (m14_folder, m09_folder) if f)
        if provided == 2:
            m14_path = resolve_source_folder(m14_folder)
            m09_path = resolve_source_folder(m09_folder)
            evidence.steps.append(f"Using provided M14 folder: {m14_path}")
            evidence.steps.append(f"Using provided M09 folder: {m09_path}")
        elif provided > 0:
            return finish_result(
                evidence,
                project_name,
                "FAIL_REPORT_SOURCE_INVALID",
                "Both --m14-folder and --m09-folder required together",
            )
        elif run_chain:
            m14_path, m09_path, notes = run_upstream_chain(
                project_name, evidence, max_rows=max_rows
            )
            evidence.steps.extend(notes)
        else:
            m14_path = find_latest_m14_folder()
            m09_path = find_latest_m09_folder()
            if m14_path:
                evidence.steps.append(f"Using latest M14 folder: {m14_path}")
            if m09_path:
                evidence.steps.append(f"Using latest M09 folder: {m09_path}")
            if not m14_path or not m09_path:
                evidence.steps.append("Latest M14/M09 not all found — running upstream chain")
                m14_path, m09_path, chain_notes = run_upstream_chain(
                    project_name, evidence, max_rows=max_rows
                )
                evidence.steps.extend(chain_notes)

        if not m14_path or not m14_path.exists():
            return finish_result(
                evidence,
                project_name,
                "FAIL_M14_SOURCE_NOT_FOUND",
                "M14 source folder not found",
            )
        if not m09_path or not m09_path.exists():
            return finish_result(
                evidence,
                project_name,
                "FAIL_M09_SOURCE_NOT_FOUND",
                "M09 source folder not found",
            )

        evidence.steps.append("load M14/M09 source files")
        try:
            sources = load_sources(m14_path, m09_path)
        except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
            return finish_result(
                evidence,
                project_name,
                "FAIL_REPORT_SOURCE_INVALID",
                f"Source files could not be parsed: {exc}",
                source_m14_folder=str(m14_path),
                source_m09_folder=str(m09_path),
            )

        evidence.steps.append("parse Data Date from M09")
        data_date, data_date_raw, data_date_normalized, data_date_parsed = parse_data_date(
            sources.m09_data_date
        )

        evidence.steps.append("parse clipboard activity rows from M14")
        activity_rows = parse_clipboard_activity_rows(sources.clipboard_table)

        if not data_date_parsed:
            return finish_result(
                evidence,
                project_name,
                "FAIL_DATA_DATE_MISSING",
                "No usable Data Date could be parsed from M09 source",
                source_m14_folder=str(m14_path),
                source_m09_folder=str(m09_path),
                data_date_raw=data_date_raw,
                data_date_normalized_candidate=data_date_normalized,
            )

        if not activity_rows:
            return finish_result(
                evidence,
                project_name,
                "FAIL_NO_CLIPBOARD_ROWS",
                "M14 source present but no activity-like clipboard rows could be parsed",
                source_m14_folder=str(m14_path),
                source_m09_folder=str(m09_path),
                data_date_raw=data_date_raw,
                data_date_normalized_candidate=data_date_normalized,
                data_date_parsed=data_date_parsed,
            )

        evidence.steps.append("compare clipboard rows against Data Date")
        comparisons, warnings, counts = compare_clipboard_rows(activity_rows, data_date)
        sev = count_severities(warnings)

        evidence.steps.append("generate clipboard health report")
        report_files, _payload = generate_reports(
            evidence,
            project_name,
            sources,
            comparisons,
            warnings,
            data_date_raw,
            data_date_normalized,
            data_date_parsed,
            counts,
        )
        status, reason = decide_status(len(comparisons), len(warnings), data_date_parsed)

        return finish_result(
            evidence,
            project_name,
            status,
            reason,
            source_m14_folder=str(m14_path),
            source_m09_folder=str(m09_path),
            data_date_raw=data_date_raw,
            data_date_normalized_candidate=data_date_normalized,
            data_date_parsed=data_date_parsed,
            clipboard_rows_checked=len(comparisons),
            start_before_data_date_count=counts.get("start_before_data_date_count", 0),
            finish_before_data_date_count=counts.get("finish_before_data_date_count", 0),
            date_parse_issue_count=counts.get("date_parse_issue_count", 0),
            warning_count=len(warnings),
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
    parser = argparse.ArgumentParser(description="M15 Clipboard Multi Row Health Report")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    parser.add_argument(
        "--m14-folder",
        default=None,
        help="Path to existing M14 run folder (skips P6 chain when used with --m09-folder)",
    )
    parser.add_argument(
        "--m09-folder",
        default=None,
        help="Path to existing M09 run folder (skips P6 chain when used with --m14-folder)",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=3,
        help="Max rows for M14 when upstream chain is required",
    )
    parser.add_argument(
        "--run-chain",
        action="store_true",
        help="Force M03->M09->M14 chain before report generation",
    )
    args = parser.parse_args()

    result = run_m15(
        args.project.strip(),
        m14_folder=args.m14_folder,
        m09_folder=args.m09_folder,
        max_rows=args.max_rows,
        run_chain=bool(args.run_chain),
    )
    print(f"M15 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Source M14 folder: {result.get('source_m14_folder', '')}")
    print(f"Source M09 folder: {result.get('source_m09_folder', '')}")
    print(f"Data Date parsed: {result.get('data_date_parsed', '')}")
    print(f"Clipboard rows checked: {result.get('clipboard_rows_checked', 0)}")
    print(f"Warning count: {result.get('warning_count', 0)}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")
    if result["status"] in ("PASS", "PASS_WITH_WARNINGS"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
