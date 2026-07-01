"""
X02 — Read-only inspection of a P6 (or other) SQLite database schema.

Safety: opens DB with mode=ro only; never writes to the inspected database.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_ROOT = ROOT / "06_output" / "x_modules" / "xer_sqlite_research" / "schema_reports"
MODULE_NAME = "x02_inspect_sqlite_schema"

PASS_STATUS = "PASS_X02_SCHEMA_INSPECTION"

LIKELY_P6_TABLES = (
    "PROJECT",
    "TASK",
    "PROJWBS",
    "TASKPRED",
    "CALENDAR",
    "RSRC",
    "TASKRSRC",
    "ACTVCODE",
    "ACTVTYPE",
    "UDFVALUE",
    "CURRTYPE",
    "OBS",
    "USERS",
    "PREFER",
    "SETTING",
)

LIVE_P6_PATH_MARKERS = (
    "primavera",
    "p6 professional",
    "\\ppm\\",
    "\\p6\\",
    "ppm.db",
    "pm.db",
)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def is_likely_live_p6_path(db_path: Path) -> bool:
    norm = str(db_path.resolve()).lower().replace("/", "\\")
    return any(marker in norm for marker in LIVE_P6_PATH_MARKERS)


def open_readonly(db_path: Path) -> sqlite3.Connection:
    uri = db_path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def pragma_single(conn: sqlite3.Connection, name: str) -> Any:
    try:
        row = conn.execute(f"PRAGMA {name}").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def pragma_all(conn: sqlite3.Connection, name: str) -> List[Tuple]:
    try:
        return conn.execute(f"PRAGMA {name}").fetchall()
    except sqlite3.Error:
        return []


def fetch_master_objects(conn: sqlite3.Connection, obj_type: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%' ORDER BY name",
        (obj_type,),
    ).fetchall()
    return [{"name": r[0], "sql": r[1] or ""} for r in rows]


def table_row_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        row = conn.execute(f'SELECT COUNT(*) FROM "{table.replace(chr(34), chr(34)+chr(34))}"').fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return -1


def sample_rows(conn: sqlite3.Connection, table: str, limit: int = 5) -> List[Dict[str, Any]]:
    try:
        cur = conn.execute(f'SELECT * FROM "{table.replace(chr(34), chr(34)+chr(34))}" LIMIT ?', (limit,))
        cols = [d[0] for d in cur.description] if cur.description else []
        out: List[Dict[str, Any]] = []
        for row in cur.fetchall():
            out.append({cols[i]: row[i] for i in range(len(cols))})
        return out
    except sqlite3.Error:
        return []


def run_foreign_key_check(conn: sqlite3.Connection) -> Tuple[int, List[Dict[str, Any]]]:
    issues: List[Dict[str, Any]] = []
    try:
        rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        for row in rows:
            issues.append(
                {
                    "table": row[0] if len(row) > 0 else "",
                    "rowid": row[1] if len(row) > 1 else None,
                    "parent": row[2] if len(row) > 2 else "",
                    "fk_index": row[3] if len(row) > 3 else None,
                }
            )
    except sqlite3.Error:
        pass
    return len(issues), issues


def build_schema_sql(conn: sqlite3.Connection) -> str:
    lines: List[str] = []
    for obj_type in ("table", "index", "view", "trigger"):
        for obj in fetch_master_objects(conn, obj_type):
            sql = (obj.get("sql") or "").strip()
            if sql:
                lines.append(f"{sql};")
                lines.append("")
    return "\n".join(lines)


def write_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_report_md(
    path: Path,
    summary: Dict[str, Any],
    warnings: List[str],
    largest: List[Dict[str, Any]],
) -> None:
    lines = [
        "# X02 P6 SQLite Schema Inspection",
        "",
        f"- **Run ID:** {summary.get('run_id', '')}",
        f"- **Status:** {summary.get('status', '')}",
        f"- **Reason:** {summary.get('reason', '')}",
        f"- **Database:** `{summary.get('db_path', '')}`",
        f"- **File size:** {summary.get('db_file_size_bytes', 0)} bytes",
        f"- **Opened read-only:** {summary.get('sqlite_open_readonly', False)}",
        f"- **SQLite version:** {summary.get('sqlite_version', '')}",
        "",
    ]
    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")
    lines.extend(
        [
            "## Database",
            "",
            f"- Integrity check: {summary.get('integrity_check', '')}",
            f"- Foreign key issues: {summary.get('foreign_key_issue_count', 0)}",
            f"- Encoding: {summary.get('encoding', '')}",
            f"- Page size: {summary.get('page_size', '')}",
            f"- Page count: {summary.get('page_count', '')}",
            f"- User version: {summary.get('user_version', '')}",
            f"- Application ID: {summary.get('application_id', '')}",
            "",
            "## Object counts",
            "",
            f"- Tables: {summary.get('table_count', 0)}",
            f"- Views: {summary.get('view_count', 0)}",
            f"- Indexes: {summary.get('index_count', 0)}",
            f"- Triggers: {summary.get('trigger_count', 0)}",
            f"- Total rows: {summary.get('total_rows', 0)}",
            "",
            "## Likely P6 tables found",
            "",
        ]
    )
    for name in summary.get("likely_p6_tables_found", []):
        rows = summary.get("likely_p6_row_counts", {}).get(name, 0)
        lines.append(f"- **{name}:** {rows} rows")
    lines.extend(["", "## Largest tables", ""])
    for entry in largest[:20]:
        lines.append(f"- `{entry.get('table_name', '')}`: {entry.get('row_count', 0)} rows")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def inspect_schema(db_path: Path, out_root: Path) -> Dict[str, Any]:
    run_id = new_run_id()
    out_dir = out_root / run_id / MODULE_NAME
    warnings: List[str] = []

    if not db_path.exists():
        return {
            "run_id": run_id,
            "db_path": str(db_path),
            "status": "FAIL_DB_FILE_NOT_FOUND",
            "reason": f"Database file not found: {db_path}",
        }

    if not db_path.is_file():
        return {
            "run_id": run_id,
            "db_path": str(db_path),
            "status": "FAIL_DB_FILE_NOT_FOUND",
            "reason": f"Path is not a file: {db_path}",
        }

    if is_likely_live_p6_path(db_path):
        warnings.append(
            "Database path appears to be under a live P6 installation folder. "
            "Prefer inspecting a copied database file. Inspection is read-only only."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    file_size = db_path.stat().st_size

    try:
        conn = open_readonly(db_path)
    except sqlite3.Error as exc:
        return {
            "run_id": run_id,
            "db_path": str(db_path.resolve()),
            "db_file_size_bytes": file_size,
            "sqlite_open_readonly": False,
            "status": "FAIL_SQLITE_OPEN_READONLY",
            "reason": f"Could not open database read-only: {exc}",
            "warnings": warnings,
        }

    try:
        sqlite_version = sqlite3.sqlite_version
        user_version = pragma_single(conn, "user_version")
        application_id = pragma_single(conn, "application_id")
        encoding = pragma_single(conn, "encoding")
        page_size = pragma_single(conn, "page_size")
        page_count = pragma_single(conn, "page_count")

        database_list = [
            {"seq": r[0], "name": r[1], "file": r[2]}
            for r in pragma_all(conn, "database_list")
        ]

        integrity_rows = pragma_all(conn, "integrity_check")
        integrity_check = integrity_rows[0][0] if integrity_rows else "unknown"
        if integrity_check != "ok":
            warnings.append(f"PRAGMA integrity_check returned: {integrity_check}")

        fk_issue_count, fk_issues = run_foreign_key_check(conn)

        tables = fetch_master_objects(conn, "table")
        views = fetch_master_objects(conn, "view")
        indexes = fetch_master_objects(conn, "index")
        triggers = fetch_master_objects(conn, "trigger")

        table_names = {t["name"] for t in tables}
        table_names_upper = {n.upper(): n for n in table_names}

        columns_rows: List[Dict[str, Any]] = []
        fk_rows: List[Dict[str, Any]] = []
        counts_rows: List[Dict[str, Any]] = []
        table_samples: Dict[str, List[Dict[str, Any]]] = {}
        total_rows = 0

        for table in tables:
            tname = table["name"]
            for col in pragma_all(conn, f'table_info("{tname}")'):
                columns_rows.append(
                    {
                        "table_name": tname,
                        "cid": col[0],
                        "column_name": col[1],
                        "column_type": col[2],
                        "notnull": col[3],
                        "default_value": col[4],
                        "pk": col[5],
                    }
                )
            for fk in pragma_all(conn, f'foreign_key_list("{tname}")'):
                fk_rows.append(
                    {
                        "table_name": tname,
                        "id": fk[0],
                        "seq": fk[1],
                        "referenced_table": fk[2],
                        "from_column": fk[3],
                        "to_column": fk[4],
                        "on_update": fk[5],
                        "on_delete": fk[6],
                        "match": fk[7],
                    }
                )
            rc = table_row_count(conn, tname)
            if rc >= 0:
                total_rows += rc
            counts_rows.append({"table_name": tname, "row_count": rc})
            table_samples[tname] = sample_rows(conn, tname, 5)

        counts_rows.sort(key=lambda x: (-x["row_count"], x["table_name"]))
        largest_tables = [
            {"table_name": r["table_name"], "row_count": r["row_count"]}
            for r in counts_rows[:15]
            if r["row_count"] >= 0
        ]

        likely_found: List[str] = []
        likely_row_counts: Dict[str, int] = {}
        for candidate in LIKELY_P6_TABLES:
            actual = table_names_upper.get(candidate.upper())
            if actual:
                likely_found.append(candidate)
                match = next((c for c in counts_rows if c["table_name"] == actual), None)
                likely_row_counts[candidate] = match["row_count"] if match else 0

        schema_sql = build_schema_sql(conn)

        integrity_text_lines = [
            f"integrity_check: {integrity_check}",
            f"foreign_key_issue_count: {fk_issue_count}",
            "",
        ]
        if fk_issues:
            integrity_text_lines.append("foreign_key_check issues:")
            for issue in fk_issues[:200]:
                integrity_text_lines.append(json.dumps(issue))
        integrity_report = "\n".join(integrity_text_lines)

        summary: Dict[str, Any] = {
            "run_id": run_id,
            "db_path": str(db_path.resolve()),
            "db_file_size_bytes": file_size,
            "sqlite_open_readonly": True,
            "sqlite_version": sqlite_version,
            "integrity_check": integrity_check,
            "foreign_key_issue_count": fk_issue_count,
            "table_count": len(tables),
            "view_count": len(views),
            "index_count": len(indexes),
            "trigger_count": len(triggers),
            "total_rows": total_rows,
            "likely_p6_tables_found": likely_found,
            "likely_p6_row_counts": likely_row_counts,
            "largest_tables": largest_tables,
            "encoding": encoding,
            "page_size": page_size,
            "page_count": page_count,
            "user_version": user_version,
            "application_id": application_id,
            "database_list": database_list,
            "warnings": warnings,
            "table_samples": table_samples,
            "status": PASS_STATUS,
            "reason": "Schema inspection completed (read-only)",
            "output_dir": str(out_dir.resolve()),
        }

        (out_dir / "schema_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        (out_dir / "schema.sql").write_text(schema_sql, encoding="utf-8")
        (out_dir / "integrity_report.txt").write_text(integrity_report, encoding="utf-8")

        write_csv(out_dir / "table_counts.csv", ["table_name", "row_count"], counts_rows)
        write_csv(
            out_dir / "columns.csv",
            ["table_name", "cid", "column_name", "column_type", "notnull", "default_value", "pk"],
            columns_rows,
        )
        write_csv(out_dir / "indexes.csv", ["name", "sql"], indexes)
        write_csv(out_dir / "triggers.csv", ["name", "sql"], triggers)
        write_csv(out_dir / "views.csv", ["name", "sql"], views)
        write_csv(
            out_dir / "foreign_keys.csv",
            [
                "table_name",
                "id",
                "seq",
                "referenced_table",
                "from_column",
                "to_column",
                "on_update",
                "on_delete",
                "match",
            ],
            fk_rows,
        )
        write_report_md(out_dir / "report.md", summary, warnings, largest_tables)

        return summary

    except sqlite3.Error as exc:
        return {
            "run_id": run_id,
            "db_path": str(db_path.resolve()),
            "db_file_size_bytes": file_size,
            "sqlite_open_readonly": True,
            "status": "FAIL_SCHEMA_INSPECTION_FAILED",
            "reason": str(exc),
            "warnings": warnings,
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="X02: Read-only P6 SQLite schema inspection")
    parser.add_argument("--db", required=True, help="Path to SQLite database file (prefer a copy)")
    parser.add_argument(
        "--out-root",
        default=str(DEFAULT_OUT_ROOT),
        help="Root folder for schema reports",
    )
    args = parser.parse_args()

    db_path = Path(args.db.strip().strip('"'))
    if not db_path.is_absolute():
        db_path = (ROOT / db_path).resolve()

    out_root = Path(args.out_root)
    if not out_root.is_absolute():
        out_root = (ROOT / out_root).resolve()

    if not str(out_root).startswith(str((ROOT / "06_output").resolve())):
        print("ERROR: --out-root must be under 06_output", file=sys.stderr)
        return 1

    summary = inspect_schema(db_path, out_root)
    print(f"X02 status: {summary.get('status', 'ERROR')}")
    print(f"Reason: {summary.get('reason', '')}")
    if summary.get("output_dir"):
        print(f"Evidence: {summary['output_dir']}")
    for w in summary.get("warnings", []):
        print(f"WARNING: {w}")
    return 0 if summary.get("status") == PASS_STATUS else 1


if __name__ == "__main__":
    raise SystemExit(main())
