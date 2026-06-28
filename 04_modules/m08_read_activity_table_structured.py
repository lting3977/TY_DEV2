"""
M08 — Read Activity Table Structured (Phase 7).

Read-only parser: converts M07 visible activity table snapshot into structured rows.
Does not touch P6 unless running the full test chain.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "04_modules"))

MODULE_NAME = "m08_read_activity_table_structured"
M07_MODULE_NAME = "m07_read_activity_table_snapshot"
HIGH_CONFIDENCE_THRESHOLD = 0.75

ACTIVITY_ID_PATTERN = re.compile(r"^[Aa]\d{3,5}[A-Za-z0-9]?$")
DATE_PATTERN = re.compile(
    r"\d{1,2}[-/\s][A-Za-z]{3}[-/\s]?\d{0,4}|\d{1,2}[-/][A-Za-z]{3}[-/]\d{2,4}",
    re.I,
)
MONTH_NAMES = (
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
)
OCR_ID_FIXES = {"z": "2", "o": "0", "l": "1", "i": "1", "s": "5"}


@dataclass
class RunEvidence:
    run_id: str
    folder: Path
    structured_dir: Path
    steps: List[str] = field(default_factory=list)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_evidence(run_id: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / run_id / MODULE_NAME
    structured = folder / "structured"
    structured.mkdir(parents=True, exist_ok=True)
    return RunEvidence(run_id=run_id, folder=folder, structured_dir=structured)


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def looks_like_date(raw: str) -> bool:
    if not raw or not DATE_PATTERN.search(raw):
        return False
    lower = raw.lower()
    return any(m in lower for m in MONTH_NAMES) or re.search(r"\d{1,2}[-/]\w{3}", raw)


def normalize_activity_id_candidate(raw: str) -> Optional[str]:
    if not raw:
        return None
    cleaned = raw.strip()
    if ACTIVITY_ID_PATTERN.match(cleaned):
        return cleaned.upper()
    norm = normalize_text(cleaned).replace(" ", "")
    if re.match(r"^a[a-z0-9]{3,6}$", norm):
        fixed = "a" + "".join(OCR_ID_FIXES.get(c, c) for c in norm[1:])
        if re.match(r"^a\d{4,5}$", fixed):
            return fixed.upper()
    if re.match(r"^a\d{3,5}[a-z0-9]?$", norm):
        fixed = "a" + "".join(OCR_ID_FIXES.get(c, c) for c in norm[1:])
        if re.match(r"^a\d{4,5}$", fixed):
            return fixed.upper()
    return None


def normalize_date_candidate(raw: str) -> Optional[str]:
    if not looks_like_date(raw):
        return None
    lower = raw.lower().strip().rstrip(",")
    if not any(m in lower for m in MONTH_NAMES):
        return None
    if re.search(r"[a-z]{2}$", lower.split("-")[-1]) and len(lower.split("-")[-1]) == 2:
        if "za" in lower or "zo" in lower:
            return None
    m = re.search(
        r"(\d{1,2})[-/\s]?([A-Za-z]{3})[-/\s]?(\d{2,4})?",
        raw.strip().rstrip(","),
    )
    if not m:
        return None
    day, mon, year = m.group(1), m.group(2).title()[:3], m.group(3) or ""
    if year and len(year) == 2:
        return f"{day}-{mon}-{year}"
    if year:
        return f"{day}-{mon}-{year}"
    return f"{day}-{mon}"


def score_row(
    activity_id_raw: Optional[str],
    activity_name_raw: Optional[str],
    start_raw: Optional[str],
    finish_raw: Optional[str],
) -> float:
    score = 0.0
    if activity_id_raw and normalize_activity_id_candidate(activity_id_raw):
        score += 0.25
    if activity_name_raw and len(activity_name_raw.strip()) >= 2:
        score += 0.25
    if start_raw and looks_like_date(start_raw):
        score += 0.25
    if finish_raw and looks_like_date(finish_raw):
        score += 0.25
    return min(score, 1.0)


def parse_activity_row(row: Dict[str, Any], row_index: int) -> Dict[str, Any]:
    cells = row.get("cells") or []
    norm_cells = row.get("normalized_cells") or []
    row_text_raw = row.get("raw_line", "")

    activity_id_raw: Optional[str] = None
    activity_id_normalized_candidate: Optional[str] = None
    activity_name_raw: Optional[str] = None
    start_raw: Optional[str] = None
    finish_raw: Optional[str] = None
    start_normalized_candidate: Optional[str] = None
    finish_normalized_candidate: Optional[str] = None

    id_idx: Optional[int] = None
    date_indices: List[int] = []

    for idx, cell in enumerate(cells):
        candidate = normalize_activity_id_candidate(cell)
        if candidate and activity_id_raw is None:
            activity_id_raw = cell
            activity_id_normalized_candidate = candidate
            if idx < len(norm_cells) and norm_cells[idx].get("normalized_candidate"):
                activity_id_normalized_candidate = norm_cells[idx]["normalized_candidate"]
            id_idx = idx
        elif looks_like_date(cell):
            date_indices.append(idx)

    if activity_id_raw is None:
        for idx, cell in enumerate(cells):
            if re.match(r"^\d{3}$", cell.strip()):
                continue
            if not looks_like_date(cell) and "new activity" in normalize_text(cell):
                if activity_name_raw is None:
                    activity_name_raw = cell
            elif not looks_like_date(cell) and activity_name_raw is None and len(cell) > 3:
                activity_name_raw = cell

    if id_idx is not None:
        between = [
            c
            for i, c in enumerate(cells)
            if i > id_idx and i not in date_indices and not looks_like_date(c)
        ]
        if between and not activity_name_raw:
            activity_name_raw = between[0]

    if date_indices:
        start_raw = cells[date_indices[0]]
        start_normalized_candidate = normalize_date_candidate(start_raw)
        if len(date_indices) > 1:
            finish_raw = cells[date_indices[1]]
            finish_normalized_candidate = normalize_date_candidate(finish_raw)

    used = {id_idx} if id_idx is not None else set()
    used.update(date_indices)
    if activity_name_raw:
        for i, c in enumerate(cells):
            if c == activity_name_raw:
                used.add(i)
    remaining = [c for i, c in enumerate(cells) if i not in used]

    confidence = score_row(activity_id_raw, activity_name_raw, start_raw, finish_raw)

    return {
        "row_index": row_index,
        "activity_id_raw": activity_id_raw,
        "activity_id_normalized_candidate": activity_id_normalized_candidate,
        "activity_name_raw": activity_name_raw,
        "start_raw": start_raw,
        "start_normalized_candidate": start_normalized_candidate,
        "finish_raw": finish_raw,
        "finish_normalized_candidate": finish_normalized_candidate,
        "remaining_text": " | ".join(remaining) if remaining else "",
        "row_text_raw": row_text_raw,
        "confidence": round(confidence, 2),
    }


def parse_m07_rows(m07_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    activity_rows = [
        r for r in m07_data.get("raw_rows", []) if r.get("row_type") == "activity"
    ]
    if not activity_rows:
        activity_rows = [
            r for r in m07_data.get("normalized_rows", []) if r.get("row_type") == "activity"
        ]
    structured: List[Dict[str, Any]] = []
    for idx, row in enumerate(activity_rows, start=1):
        structured.append(parse_activity_row(row, idx))
    return structured


def find_latest_m07_folder() -> Optional[Path]:
    runs_root = ROOT / "06_output" / "runs"
    if not runs_root.exists():
        return None
    candidates: List[Tuple[str, Path]] = []
    for run_dir in runs_root.iterdir():
        if not run_dir.is_dir():
            continue
        m07 = run_dir / M07_MODULE_NAME
        extracted = m07 / "extracted" / "activity_table_rows.json"
        if extracted.exists():
            candidates.append((run_dir.name, m07))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def resolve_m07_folder(m07_folder: Optional[str]) -> Tuple[Optional[Path], List[str]]:
    notes: List[str] = []
    if m07_folder:
        path = Path(m07_folder)
        if path.name == "extracted":
            path = path.parent
        notes.append(f"Using provided M07 folder: {path}")
        return path, notes
    latest = find_latest_m07_folder()
    if latest:
        notes.append(f"Using latest M07 folder: {latest}")
    return latest, notes


def run_m07_chain(project_name: str, evidence: RunEvidence) -> Tuple[Optional[Path], List[str]]:
    notes: List[str] = ["Running M03 -> M04 -> M06 -> M07 chain"]
    from m03_open_project_by_name import run_m03  # noqa: WPS433
    from m04_check_project_opened import run_m04  # noqa: WPS433
    from m06_go_to_activities import run_m06  # noqa: WPS433
    from m07_read_activity_table_snapshot import run_m07  # noqa: WPS433

    m03 = run_m03(project_name, run_id=f"{new_run_id()}_chain_m03")
    notes.append(f"M03 status: {m03.get('status')}")
    m04 = run_m04(project_name, run_id=f"{new_run_id()}_chain_m04")
    notes.append(f"M04 status: {m04.get('status')}")
    m06 = run_m06(project_name, run_id=f"{new_run_id()}_chain_m06")
    notes.append(f"M06 status: {m06.get('status')}")
    m07 = run_m07(project_name, run_id=f"{new_run_id()}_chain_m07")
    notes.append(f"M07 status: {m07.get('status')}")
    if m07.get("status") not in ("PASS", "PASS_PARTIAL_SNAPSHOT"):
        return None, notes
    folder = ROOT / "06_output" / "runs" / m07["run_id"] / M07_MODULE_NAME
    return folder, notes


def load_m07_sources(m07_folder: Path) -> Tuple[Dict[str, Any], List[str], List[str]]:
    extracted = m07_folder / "extracted"
    required = [
        "activity_table_raw_lines.json",
        "activity_table_rows.json",
        "activity_table_snapshot.csv",
    ]
    source_files: List[str] = []
    for name in required:
        path = extracted / name
        if not path.exists():
            raise FileNotFoundError(f"Missing M07 file: {path}")
        source_files.append(str(path))
    rows_data = load_json(extracted / "activity_table_rows.json")
    return rows_data, source_files, required


def save_structured_outputs(
    evidence: RunEvidence, structured_rows: List[Dict[str, Any]]
) -> Tuple[List[str], int, int]:
    high = [r for r in structured_rows if r["confidence"] >= HIGH_CONFIDENCE_THRESHOLD]
    low = [r for r in structured_rows if r["confidence"] < HIGH_CONFIDENCE_THRESHOLD]

    json_path = evidence.structured_dir / "activity_table_structured.json"
    write_json(
        json_path,
        {
            "row_count": len(structured_rows),
            "high_confidence_count": len(high),
            "low_confidence_count": len(low),
            "rows": structured_rows,
        },
    )

    low_path = evidence.structured_dir / "activity_table_low_confidence_rows.json"
    write_json(
        low_path,
        {
            "low_confidence_count": len(low),
            "rows": low,
        },
    )

    csv_path = evidence.structured_dir / "activity_table_structured.csv"
    fieldnames = [
        "row_index",
        "activity_id_raw",
        "activity_id_normalized_candidate",
        "activity_name_raw",
        "start_raw",
        "start_normalized_candidate",
        "finish_raw",
        "finish_normalized_candidate",
        "remaining_text",
        "row_text_raw",
        "confidence",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in structured_rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    return [str(json_path), str(csv_path), str(low_path)], len(high), len(low)


def decide_status(structured_rows: List[Dict[str, Any]]) -> Tuple[str, str]:
    if not structured_rows:
        return "FAIL_NO_ACTIVITY_ROWS", "No activity-like rows could be parsed from M07 source"
    high = sum(1 for r in structured_rows if r["confidence"] >= HIGH_CONFIDENCE_THRESHOLD)
    majority = high > len(structured_rows) / 2
    if majority:
        return (
            "PASS",
            f"Parsed {len(structured_rows)} structured row(s); {high} high-confidence",
        )
    return (
        "PASS_WITH_LOW_CONFIDENCE_ROWS",
        f"Parsed {len(structured_rows)} row(s); {len(structured_rows) - high} low-confidence",
    )


def finish_result(
    evidence: RunEvidence,
    project_name: str,
    status: str,
    reason: str,
    *,
    source_m07_folder: str = "",
    source_files: Optional[List[str]] = None,
    structured_files: Optional[List[str]] = None,
    row_count: int = 0,
    high_confidence_count: int = 0,
    low_confidence_count: int = 0,
    sample_rows: Optional[List[Any]] = None,
    low_confidence_examples: Optional[List[Any]] = None,
    manual_review_required: bool = False,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    result = {
        "run_id": evidence.run_id,
        "module": MODULE_NAME,
        "project_name": project_name,
        "status": status,
        "reason": reason,
        "source_m07_folder": source_m07_folder,
        "structured_files": structured_files or [],
        "source_files": source_files or [],
        "row_count": row_count,
        "high_confidence_count": high_confidence_count,
        "low_confidence_count": low_confidence_count,
        "sample_rows": sample_rows or [],
        "low_confidence_examples": low_confidence_examples or [],
        "manual_review_required": manual_review_required,
        "error": error,
        "steps": evidence.steps,
    }
    write_json(evidence.folder / "result.json", result)
    write_report(evidence, result)
    return result


def write_report(evidence: RunEvidence, result: Dict[str, Any]) -> None:
    lines = [
        "# M08 Read Activity Table Structured Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project name: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Source M07 folder: {result.get('source_m07_folder', '')}",
        f"- Source files: {result.get('source_files', [])}",
        f"- Row count: {result.get('row_count', 0)}",
        f"- High confidence count: {result.get('high_confidence_count', 0)}",
        f"- Low confidence count: {result.get('low_confidence_count', 0)}",
        f"- Sample structured rows: {result.get('sample_rows', [])}",
        f"- Low confidence examples: {result.get('low_confidence_examples', [])}",
        "",
        "## Final decision",
        result["status"],
        "",
        "## Next recommendation",
    ]
    if result["status"] in ("PASS", "PASS_WITH_LOW_CONFIDENCE_ROWS"):
        lines.append("Ready for M08 hard testing.")
    else:
        lines.append("Fix the reported issue and re-run TY_TEST_M08_READ_ACTIVITY_STRUCTURED.bat")
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_m08(
    project_name: str,
    *,
    m07_folder: Optional[str] = None,
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
        m07_path: Optional[Path]
        setup_notes: List[str]

        if m07_folder:
            m07_path, setup_notes = resolve_m07_folder(m07_folder)
            evidence.steps.extend(setup_notes)
        elif run_chain:
            m07_path, setup_notes = run_m07_chain(project_name, evidence)
            evidence.steps.extend(setup_notes)
        else:
            m07_path, setup_notes = resolve_m07_folder(None)
            evidence.steps.extend(setup_notes)
            if not m07_path:
                evidence.steps.append("No latest M07 folder — running chain")
                m07_path, chain_notes = run_m07_chain(project_name, evidence)
                evidence.steps.extend(chain_notes)

        if not m07_path or not m07_path.exists():
            return finish_result(
                evidence,
                project_name,
                "FAIL_M07_SOURCE_NOT_FOUND",
                "M07 source folder not found",
            )

        evidence.steps.append("load M07 extracted files")
        rows_data, source_files, _ = load_m07_sources(m07_path)

        evidence.steps.append("parse structured activity rows")
        structured_rows = parse_m07_rows(rows_data)

        if not structured_rows:
            return finish_result(
                evidence,
                project_name,
                "FAIL_NO_ACTIVITY_ROWS",
                "M07 source present but no activity rows parsed",
                source_m07_folder=str(m07_path),
                source_files=source_files,
            )

        structured_files, high_count, low_count = save_structured_outputs(
            evidence, structured_rows
        )
        status, reason = decide_status(structured_rows)
        low_examples = [r for r in structured_rows if r["confidence"] < HIGH_CONFIDENCE_THRESHOLD][:3]

        return finish_result(
            evidence,
            project_name,
            status,
            reason,
            source_m07_folder=str(m07_path),
            source_files=source_files,
            structured_files=structured_files,
            row_count=len(structured_rows),
            high_confidence_count=high_count,
            low_confidence_count=low_count,
            sample_rows=structured_rows[:5],
            low_confidence_examples=low_examples,
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
    parser = argparse.ArgumentParser(description="M08 Read Activity Table Structured")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    parser.add_argument(
        "--m07-folder",
        default=None,
        help="Path to existing M07 run folder (skips P6 chain)",
    )
    parser.add_argument(
        "--run-chain",
        action="store_true",
        help="Force M03->M07 chain before parsing",
    )
    args = parser.parse_args()

    result = run_m08(
        args.project.strip(),
        m07_folder=args.m07_folder,
        run_chain=bool(args.run_chain),
    )
    print(f"M08 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Source M07 folder: {result.get('source_m07_folder', '')}")
    print(f"Row count: {result.get('row_count', 0)}")
    print(f"High confidence: {result.get('high_confidence_count', 0)}")
    print(f"Low confidence: {result.get('low_confidence_count', 0)}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")
    if result["status"] in ("PASS", "PASS_WITH_LOW_CONFIDENCE_ROWS"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
