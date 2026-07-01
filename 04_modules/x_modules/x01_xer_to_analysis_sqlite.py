"""
X01 — Parse Primavera P6 XER export into a new analysis SQLite database.

Safety: never reads or writes P6 application databases. Output only under 06_output.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_ROOT = ROOT / "06_output" / "x_modules" / "xer_sqlite_research" / "runs"
MODULE_NAME = "x01_xer_to_analysis_sqlite"

MAJOR_TABLES = (
    "PROJECT",
    "TASK",
    "PROJWBS",
    "TASKPRED",
    "RSRC",
    "CALENDAR",
    "ACTVCODE",
    "ACTVTYPE",
    "UDFVALUE",
    "RESOURCECURVE",
    "TASKRSRC",
)

PASS_STATUS = "PASS_X01_XER_TO_SQLITE"


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def sanitise_sqlite_identifier(name: str, *, prefix: str = "t") -> str:
    """Make a safe SQLite table/column identifier; preserve empties as fallback."""
    raw = (name or "").strip()
    if not raw:
        return f"{prefix}_unnamed"
    safe = re.sub(r"[^0-9A-Za-z_]", "_", raw)
    safe = re.sub(r"_+", "_", safe).strip("_")
    if not safe:
        safe = f"{prefix}_unnamed"
    if safe[0].isdigit():
        safe = f"{prefix}_{safe}"
    return safe[:128]


def make_unique_names(names: List[str], *, prefix: str = "col") -> Tuple[List[str], List[str]]:
    """Return (sanitised_unique_names, original_names)."""
    seen: Dict[str, int] = {}
    out: List[str] = []
    for original in names:
        base = sanitise_sqlite_identifier(original, prefix=prefix)
        key = base.lower()
        count = seen.get(key, 0)
        if count:
            candidate = f"{base}_{count + 1}"
            while candidate.lower() in seen:
                count += 1
                candidate = f"{base}_{count + 1}"
            base = candidate
        seen[base.lower()] = seen.get(base.lower(), 0) + 1
        out.append(base)
    return out, names


def split_xer_fields(line: str) -> List[str]:
    return line.split("\t")


@dataclass
class XerTable:
    original_name: str
    sqlite_name: str
    columns_original: List[str]
    columns_sqlite: List[str]
    rows: List[List[str]]


def parse_xer_text(text: str) -> Tuple[List[XerTable], List[Dict[str, Any]]]:
    tables: List[XerTable] = []
    errors: List[Dict[str, Any]] = []
    current: Optional[XerTable] = None
    line_no = 0

    for raw_line in text.splitlines():
        line_no += 1
        line = raw_line.rstrip("\n\r")
        if not line.strip():
            continue
        parts = split_xer_fields(line)
        tag = parts[0] if parts else ""

        if tag == "%T":
            if len(parts) < 2:
                errors.append({"line": line_no, "error": "missing_table_name", "detail": line[:200]})
                current = None
                continue
            original_name = parts[1].strip()
            sqlite_name = sanitise_sqlite_identifier(original_name, prefix="xer")
            current = XerTable(
                original_name=original_name,
                sqlite_name=sqlite_name,
                columns_original=[],
                columns_sqlite=[],
                rows=[],
            )
            tables.append(current)
        elif tag == "%F":
            if current is None:
                errors.append({"line": line_no, "error": "fields_without_table", "detail": line[:200]})
                continue
            cols = [c.strip() for c in parts[1:]]
            sqlite_cols, _ = make_unique_names(cols, prefix="col")
            current.columns_original = cols
            current.columns_sqlite = sqlite_cols
        elif tag == "%R":
            if current is None:
                errors.append({"line": line_no, "error": "row_without_table", "detail": line[:200]})
                continue
            values = parts[1:]
            current.rows.append(values)
        elif tag == "%E":
            current = None
        elif tag == "ERMHDR":
            continue
        else:
            errors.append({"line": line_no, "error": "unknown_tag", "detail": tag})

    return tables, errors


def align_row(values: List[str], col_count: int, table_name: str, row_index: int) -> Tuple[List[Optional[str]], Optional[Dict[str, Any]]]:
    """Pad or extend row to match column count; log mismatch."""
    issue: Optional[Dict[str, Any]] = None
    if len(values) == col_count:
        return values, issue
    if len(values) > col_count:
        issue = {
            "table": table_name,
            "row_index": row_index,
            "error": "row_extra_values",
            "expected_columns": col_count,
            "actual_values": len(values),
        }
        extra_cols = len(values) - col_count
        extended = list(values)
        return extended, issue
    issue = {
        "table": table_name,
        "row_index": row_index,
        "error": "row_missing_values",
        "expected_columns": col_count,
        "actual_values": len(values),
    }
    padded = values + [None] * (col_count - len(values))
    return padded, issue


def create_metadata_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS _xer_import_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS _xer_tables (
            sqlite_table_name TEXT PRIMARY KEY,
            original_table_name TEXT NOT NULL,
            column_count INTEGER NOT NULL,
            row_count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS _xer_parse_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            line INTEGER,
            table_name TEXT,
            row_index INTEGER,
            error TEXT,
            detail TEXT
        );
        """
    )


def import_tables_to_sqlite(
    conn: sqlite3.Connection,
    tables: List[XerTable],
    row_errors: List[Dict[str, Any]],
) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for table in tables:
        if not table.columns_sqlite:
            row_errors.append(
                {
                    "table": table.original_name,
                    "row_index": None,
                    "error": "table_without_fields",
                    "detail": "no %F section before rows",
                }
            )
            continue

        col_defs = ", ".join(f'"{c}" TEXT' for c in table.columns_sqlite)
        max_extra = 0
        normalised_rows: List[List[Optional[str]]] = []
        for idx, raw_row in enumerate(table.rows):
            aligned, issue = align_row(raw_row, len(table.columns_sqlite), table.original_name, idx + 1)
            if issue:
                row_errors.append(issue)
            if len(aligned) > len(table.columns_sqlite):
                max_extra = max(max_extra, len(aligned) - len(table.columns_sqlite))
            normalised_rows.append(aligned)

        extra_col_names: List[str] = []
        if max_extra:
            for i in range(max_extra):
                extra_col_names.append(f"extra_{i + 1:03d}")
            col_defs += ", " + ", ".join(f'"{c}" TEXT' for c in extra_col_names)

        conn.execute(f'DROP TABLE IF EXISTS "{table.sqlite_name}"')
        conn.execute(f'CREATE TABLE "{table.sqlite_name}" ({col_defs})')

        insert_cols = table.columns_sqlite + extra_col_names
        placeholders = ", ".join("?" for _ in insert_cols)
        col_list = ", ".join(f'"{c}"' for c in insert_cols)
        insert_sql = f'INSERT INTO "{table.sqlite_name}" ({col_list}) VALUES ({placeholders})'

        batch: List[Tuple[Any, ...]] = []
        for row in normalised_rows:
            values: List[Optional[str]] = list(row[: len(table.columns_sqlite)])
            if len(values) < len(table.columns_sqlite):
                values.extend([None] * (len(table.columns_sqlite) - len(values)))
            if extra_col_names:
                extras = row[len(table.columns_sqlite) :]
                extras = extras + [None] * (len(extra_col_names) - len(extras))
                values.extend(extras[: len(extra_col_names)])
            batch.append(tuple(values))
        if batch:
            conn.executemany(insert_sql, batch)

        counts[table.sqlite_name] = len(batch)
        conn.execute(
            "INSERT INTO _xer_tables (sqlite_table_name, original_table_name, column_count, row_count) VALUES (?, ?, ?, ?)",
            (table.sqlite_name, table.original_name, len(insert_cols), len(batch)),
        )
    return counts


def sqlite_open_test(db_path: Path) -> bool:
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        conn.close()
        return True
    except sqlite3.Error:
        return False


def write_table_counts_csv(path: Path, counts: Dict[str, int], tables_meta: List[Dict[str, Any]]) -> None:
    meta_by_sqlite = {m["sqlite_table_name"]: m for m in tables_meta}
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["sqlite_table_name", "original_table_name", "row_count"])
        for sqlite_name, row_count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
            original = meta_by_sqlite.get(sqlite_name, {}).get("original_table_name", sqlite_name)
            writer.writerow([sqlite_name, original, row_count])


def write_parse_errors_csv(path: Path, errors: List[Dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["line", "table", "table_name", "row_index", "error", "detail", "expected_columns", "actual_values"],
            extrasaction="ignore",
        )
        writer.writeheader()
        for err in errors:
            writer.writerow(err)


def write_report_md(path: Path, summary: Dict[str, Any], counts: Dict[str, int]) -> None:
    lines = [
        "# X01 XER to Analysis SQLite",
        "",
        f"- **Run ID:** {summary.get('run_id', '')}",
        f"- **Status:** {summary.get('status', '')}",
        f"- **Reason:** {summary.get('reason', '')}",
        f"- **Source XER:** `{summary.get('source_xer_path', '')}`",
        f"- **Output SQLite:** `{summary.get('output_sqlite', '')}`",
        "",
        "## Counts",
        "",
        f"- Tables created: {summary.get('tables_created', 0)}",
        f"- Total rows: {summary.get('total_rows', 0)}",
        f"- Parse errors: {summary.get('parse_errors', 0)}",
        f"- SQLite open test: {summary.get('sqlite_open_test', False)}",
        "",
        "## Major tables",
        "",
    ]
    for name in MAJOR_TABLES:
        key = name.lower() + "_rows"
        rows = summary.get(key, 0)
        found = name in summary.get("major_tables_found", []) or rows > 0
        lines.append(f"- **{name}:** {'found' if found else 'not found'} — {rows} rows")
    lines.extend(["", "## Largest tables", ""])
    for sqlite_name, row_count in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:15]:
        lines.append(f"- `{sqlite_name}`: {row_count}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def count_major_rows(counts: Dict[str, int], tables: List[XerTable]) -> Dict[str, int]:
    original_to_sqlite = {t.original_name.upper(): t.sqlite_name for t in tables}
    result: Dict[str, int] = {}
    for major in MAJOR_TABLES:
        sqlite_name = original_to_sqlite.get(major.upper())
        result[major.lower() + "_rows"] = counts.get(sqlite_name, 0) if sqlite_name else 0
    return result


def run_x01(xer_path: Path, out_root: Path) -> Dict[str, Any]:
    if not xer_path.exists():
        return {
            "status": "FAIL_XER_FILE_NOT_FOUND",
            "reason": f"XER file not found: {xer_path}",
            "source_xer_path": str(xer_path),
        }

    run_id = new_run_id()
    out_dir = out_root / run_id / MODULE_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "xer_analysis.sqlite"

    try:
        text = xer_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "run_id": run_id,
            "status": "ERROR",
            "reason": f"Cannot read XER file: {exc}",
            "source_xer_path": str(xer_path),
        }

    tables, parse_errors = parse_xer_text(text)
    row_errors: List[Dict[str, Any]] = []

    if not tables:
        summary = {
            "run_id": run_id,
            "source_xer_path": str(xer_path.resolve()),
            "output_sqlite": str(db_path.resolve()),
            "tables_created": 0,
            "total_rows": 0,
            "major_tables_found": [],
            "task_rows": 0,
            "project_rows": 0,
            "projwbs_rows": 0,
            "taskpred_rows": 0,
            "rsrc_rows": 0,
            "calendar_rows": 0,
            "parse_errors": len(parse_errors),
            "sqlite_open_test": False,
            "status": "FAIL_XER_PARSE_NO_TABLES",
            "reason": "No %T tables found in XER file",
        }
        write_parse_errors_csv(out_dir / "parse_errors.csv", parse_errors)
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        write_report_md(out_dir / "report.md", summary, {})
        write_table_counts_csv(out_dir / "table_counts.csv", {}, [])
        return summary

    try:
        conn = sqlite3.connect(str(db_path))
        create_metadata_tables(conn)
        conn.execute(
            "INSERT INTO _xer_import_meta (key, value) VALUES (?, ?), (?, ?), (?, ?)",
            (
                "run_id",
                run_id,
                "source_xer_path",
                str(xer_path.resolve()),
                "imported_at",
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        counts = import_tables_to_sqlite(conn, tables, row_errors)
        all_errors = parse_errors + row_errors
        for err in all_errors:
            conn.execute(
                "INSERT INTO _xer_parse_errors (line, table_name, row_index, error, detail) VALUES (?, ?, ?, ?, ?)",
                (
                    err.get("line"),
                    err.get("table") or err.get("table_name"),
                    err.get("row_index"),
                    err.get("error", ""),
                    str(err.get("detail", err.get("expected_columns", "")))[:500],
                ),
            )
        conn.commit()
        conn.close()
    except sqlite3.Error as exc:
        return {
            "run_id": run_id,
            "source_xer_path": str(xer_path.resolve()),
            "output_sqlite": str(db_path.resolve()),
            "status": "FAIL_SQLITE_CREATE_FAILED",
            "reason": str(exc),
        }

    open_ok = sqlite_open_test(db_path)
    total_rows = sum(counts.values())
    major_found = [t.original_name for t in tables if t.original_name.upper() in MAJOR_TABLES]
    major_counts = count_major_rows(counts, tables)

    tables_with_rows = sum(1 for c in counts.values() if c > 0)
    status = PASS_STATUS
    reason = "XER parsed into analysis SQLite successfully"
    if not open_ok:
        status = "FAIL_SQLITE_CREATE_FAILED"
        reason = "SQLite open test failed after import"
    elif tables_with_rows == 0:
        status = "FAIL_XER_PARSE_NO_TABLES"
        reason = "Tables created but no rows imported"

    summary: Dict[str, Any] = {
        "run_id": run_id,
        "source_xer_path": str(xer_path.resolve()),
        "output_sqlite": str(db_path.resolve()),
        "tables_created": len(counts),
        "total_rows": total_rows,
        "major_tables_found": major_found,
        "task_rows": major_counts.get("task_rows", 0),
        "project_rows": major_counts.get("project_rows", 0),
        "projwbs_rows": major_counts.get("projwbs_rows", 0),
        "taskpred_rows": major_counts.get("taskpred_rows", 0),
        "rsrc_rows": major_counts.get("rsrc_rows", 0),
        "calendar_rows": major_counts.get("calendar_rows", 0),
        "parse_errors": len(all_errors),
        "sqlite_open_test": open_ok,
        "status": status,
        "reason": reason,
        "output_dir": str(out_dir.resolve()),
    }

    tables_meta = [
        {
            "sqlite_table_name": t.sqlite_name,
            "original_table_name": t.original_name,
            "row_count": counts.get(t.sqlite_name, 0),
        }
        for t in tables
    ]

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_report_md(out_dir / "report.md", summary, counts)
    write_table_counts_csv(out_dir / "table_counts.csv", counts, tables_meta)
    write_parse_errors_csv(out_dir / "parse_errors.csv", all_errors)

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="X01: XER to analysis SQLite (no P6 DB access)")
    parser.add_argument("--xer", required=True, help="Path to source .xer file")
    parser.add_argument(
        "--out-root",
        default=str(DEFAULT_OUT_ROOT),
        help="Root folder for run output (default: 06_output/x_modules/xer_sqlite_research/runs)",
    )
    args = parser.parse_args()

    xer_path = Path(args.xer)
    if not xer_path.is_absolute():
        xer_path = (ROOT / xer_path).resolve()
    out_root = Path(args.out_root)
    if not out_root.is_absolute():
        out_root = (ROOT / out_root).resolve()

    if not str(out_root).startswith(str((ROOT / "06_output").resolve())):
        print("ERROR: --out-root must be under 06_output", file=sys.stderr)
        return 1

    summary = run_x01(xer_path, out_root)
    print(f"X01 status: {summary.get('status', 'ERROR')}")
    print(f"Reason: {summary.get('reason', '')}")
    print(f"Evidence: {summary.get('output_dir', summary.get('output_sqlite', ''))}")
    return 0 if summary.get("status") == PASS_STATUS else 1


if __name__ == "__main__":
    raise SystemExit(main())
