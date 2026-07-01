"""
X03 — Read-only schema comparison: X01 XER analysis SQLite vs copied P6 SQLite.

Safety: opens both databases mode=ro only; writes reports under 06_output only.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_ROOT = ROOT / "06_output" / "x_modules" / "xer_sqlite_research" / "mapping_reports"
MODULE_NAME = "x03_compare_xer_to_p6_sqlite"

PASS_STATUS = "PASS_X03_SCHEMA_COMPARISON"

KEY_TABLES = (
    "PROJECT",
    "PROJWBS",
    "TASK",
    "TASKPRED",
    "CALENDAR",
    "RSRC",
    "TASKRSRC",
    "ACTVCODE",
    "ACTVTYPE",
    "UDFVALUE",
    "CURRTYPE",
)

ID_COLUMN_MARKERS = (
    "proj_id",
    "task_id",
    "wbs_id",
    "clndr_id",
    "rsrc_id",
    "task_pred_id",
    "taskrsrc_id",
    "actv_code_id",
    "actv_code_type_id",
    "udf_type_id",
    "curr_id",
)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def open_readonly(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)


def list_tables(conn: sqlite3.Connection, *, exclude_xer_meta: bool = False) -> List[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    names = [r[0] for r in rows]
    if exclude_xer_meta:
        names = [n for n in names if not n.startswith("_xer_")]
    return names


def table_info(conn: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    rows = conn.execute(f'PRAGMA table_info("{table.replace(chr(34), chr(34)+chr(34))}")').fetchall()
    return [
        {
            "name": r[1],
            "name_upper": r[1].upper(),
            "type": r[2],
            "notnull": bool(r[3]),
            "default_value": r[4],
            "pk": bool(r[5]),
        }
        for r in rows
    ]


def row_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(
            conn.execute(
                f'SELECT COUNT(*) FROM "{table.replace(chr(34), chr(34)+chr(34))}"'
            ).fetchone()[0]
        )
    except sqlite3.Error:
        return -1


def table_triggers(conn: sqlite3.Connection, table: str) -> List[Dict[str, str]]:
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=? ORDER BY name",
        (table,),
    ).fetchall()
    return [{"name": r[0], "sql": (r[1] or "")[:500]} for r in rows]


def table_foreign_keys(conn: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    rows = conn.execute(f'PRAGMA foreign_key_list("{table.replace(chr(34), chr(34)+chr(34))}")').fetchall()
    return [
        {
            "from_column": r[3],
            "to_table": r[2],
            "to_column": r[4],
            "on_update": r[5],
            "on_delete": r[6],
        }
        for r in rows
    ]


def table_indexes(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f'PRAGMA index_list("{table.replace(chr(34), chr(34)+chr(34))}")').fetchall()
    return [r[1] for r in rows]


def resolve_table_name(tables: List[str], wanted: str) -> Optional[str]:
    upper_map = {t.upper(): t for t in tables}
    return upper_map.get(wanted.upper())


def likely_pk_columns(cols: List[Dict[str, Any]]) -> List[str]:
    pks = [c["name"] for c in cols if c["pk"]]
    if pks:
        return pks
    for c in cols:
        nu = c["name"].lower()
        if nu.endswith("_id") and "INTEGER" in (c["type"] or "").upper():
            return [c["name"]]
    return []


def likely_fk_columns(fks: List[Dict[str, Any]]) -> List[str]:
    return [fk["from_column"] for fk in fks]


def detect_id_columns(cols: List[Dict[str, Any]]) -> List[str]:
    found: List[str] = []
    col_names = {c["name"].lower(): c["name"] for c in cols}
    for marker in ID_COLUMN_MARKERS:
        if marker in col_names:
            found.append(col_names[marker])
    return found


def has_autoincrement(create_sql: str) -> bool:
    return bool(create_sql and re.search(r"AUTOINCREMENT", create_sql, re.I))


def get_create_sql(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return (row[0] or "") if row else ""


def mapping_confidence_and_risk(
    *,
    xer_exists: bool,
    p6_exists: bool,
    exact_col_matches: int,
    xer_col_count: int,
    p6_col_count: int,
    missing_required_in_xer: List[str],
    trigger_count: int,
    fk_count: int,
    id_columns: List[str],
) -> Tuple[str, str, List[str]]:
    notes: List[str] = []
    if not xer_exists or not p6_exists:
        return "BLOCKED", "BLOCKED", ["Table missing in XER or P6 database"]

    if xer_col_count == 0 or p6_col_count == 0:
        return "BLOCKED", "BLOCKED", ["No columns found on one side"]

    match_ratio = exact_col_matches / max(p6_col_count, 1)
    notes.append(f"Column match ratio vs P6: {match_ratio:.0%} ({exact_col_matches}/{p6_col_count})")

    risk = "LOW"
    confidence = "high"

    if missing_required_in_xer:
        notes.append(f"P6 NOT NULL columns missing from XER: {', '.join(missing_required_in_xer)}")
        risk = "MEDIUM"
        confidence = "medium"

    if trigger_count > 0:
        notes.append(f"P6 has {trigger_count} trigger(s) on insert/update/delete paths")
        risk = "HIGH" if risk != "BLOCKED" else risk
        confidence = "low" if confidence == "high" else confidence

    if fk_count > 0:
        notes.append(f"P6 has {fk_count} foreign key constraint(s)")

    if id_columns:
        notes.append(f"Likely ID columns: {', '.join(id_columns)}")
        if trigger_count > 0 or missing_required_in_xer:
            risk = "HIGH"
            confidence = "low"

    if match_ratio < 0.5:
        confidence = "low"
        risk = "HIGH" if risk == "LOW" else risk
        notes.append("Less than 50% of P6 columns present in XER")

    if match_ratio >= 0.85 and not missing_required_in_xer and trigger_count == 0:
        risk = "LOW"
        confidence = "high"

    return confidence, risk, notes


def compare_tables(
    xer_conn: sqlite3.Connection,
    p6_conn: sqlite3.Connection,
) -> Dict[str, Any]:
    xer_tables = list_tables(xer_conn, exclude_xer_meta=True)
    p6_tables = list_tables(p6_conn, exclude_xer_meta=False)

    xer_upper = {t.upper(): t for t in xer_tables}
    p6_upper = {t.upper(): t for t in p6_tables}

    exact_matches = sorted(set(xer_tables) & set(p6_tables))
    case_insensitive_matches = sorted(
        u for u in set(xer_upper) & set(p6_upper) if xer_upper[u] != p6_upper.get(u, "")
    )
    xer_in_p6 = sorted(u for u in xer_upper if u in p6_upper)
    xer_missing_in_p6 = sorted(u for u in xer_upper if u not in p6_upper)
    p6_not_in_xer = sorted(u for u in p6_upper if u not in xer_upper)

    return {
        "xer_tables": xer_tables,
        "p6_tables": p6_tables,
        "exact_table_matches": exact_matches,
        "case_insensitive_matches": case_insensitive_matches,
        "xer_tables_in_p6": [xer_upper[u] for u in xer_in_p6],
        "xer_tables_missing_in_p6": [xer_upper[u] for u in xer_missing_in_p6],
        "p6_tables_not_in_xer": [p6_upper[u] for u in p6_not_in_xer],
    }


def analyse_key_table(
    key: str,
    xer_conn: sqlite3.Connection,
    p6_conn: sqlite3.Connection,
    xer_tables: List[str],
    p6_tables: List[str],
) -> Dict[str, Any]:
    xer_name = resolve_table_name(xer_tables, key)
    p6_name = resolve_table_name(p6_tables, key)
    xer_exists = xer_name is not None
    p6_exists = p6_name is not None

    xer_cols: List[Dict[str, Any]] = table_info(xer_conn, xer_name) if xer_exists else []
    p6_cols: List[Dict[str, Any]] = table_info(p6_conn, p6_name) if p6_exists else []

    xer_col_names = [c["name"] for c in xer_cols]
    p6_col_names = [c["name"] for c in p6_cols]
    xer_col_upper = {c["name_upper"] for c in xer_cols}
    p6_col_upper_map = {c["name_upper"]: c for c in p6_cols}

    exact_matches = sorted(c["name"] for c in p6_cols if c["name_upper"] in xer_col_upper)
    missing_in_p6 = sorted(
        c["name"] for c in xer_cols if c["name_upper"] not in p6_col_upper_map
    )
    extra_in_p6 = sorted(c["name"] for c in p6_cols if c["name_upper"] not in xer_col_upper)

    required_p6 = [
        c["name"]
        for c in p6_cols
        if c["notnull"] and c["default_value"] is None and not c["pk"]
    ]
    missing_required_in_xer = [c for c in required_p6 if c.upper() not in xer_col_upper]

    triggers = table_triggers(p6_conn, p6_name) if p6_exists else []
    fks = table_foreign_keys(p6_conn, p6_name) if p6_exists else []
    indexes = table_indexes(p6_conn, p6_name) if p6_exists else []
    create_sql = get_create_sql(p6_conn, p6_name) if p6_exists else ""

    confidence, risk, notes = mapping_confidence_and_risk(
        xer_exists=xer_exists,
        p6_exists=p6_exists,
        exact_col_matches=len(exact_matches),
        xer_col_count=len(xer_cols),
        p6_col_count=len(p6_cols),
        missing_required_in_xer=missing_required_in_xer,
        trigger_count=len(triggers),
        fk_count=len(fks),
        id_columns=detect_id_columns(p6_cols),
    )

    return {
        "table": key,
        "xer_table_name": xer_name,
        "p6_table_name": p6_name,
        "xer_table_exists": xer_exists,
        "p6_table_exists": p6_exists,
        "xer_row_count": row_count(xer_conn, xer_name) if xer_exists else 0,
        "p6_row_count": row_count(p6_conn, p6_name) if p6_exists else 0,
        "xer_columns": xer_col_names,
        "p6_columns": p6_col_names,
        "exact_column_match_count": len(exact_matches),
        "exact_column_matches": exact_matches,
        "xer_columns_missing_in_p6": missing_in_p6,
        "p6_required_or_nonnullable_columns": required_p6,
        "p6_columns_not_in_xer": extra_in_p6,
        "likely_primary_key_columns": likely_pk_columns(p6_cols),
        "likely_foreign_key_columns": likely_fk_columns(fks),
        "likely_id_columns": detect_id_columns(p6_cols),
        "p6_trigger_count": len(triggers),
        "p6_triggers": triggers,
        "p6_foreign_keys": fks,
        "p6_index_count": len(indexes),
        "p6_has_autoincrement": has_autoincrement(create_sql),
        "mapping_confidence": confidence,
        "insert_risk": risk,
        "notes": notes,
    }


def overall_insert_risk(key_analyses: List[Dict[str, Any]]) -> str:
    risks = [a.get("insert_risk", "BLOCKED") for a in key_analyses if a.get("p6_table_exists")]
    if any(r == "BLOCKED" for r in risks):
        return "BLOCKED"
    if any(r == "HIGH" for r in risks):
        return "HIGH"
    if any(r == "MEDIUM" for r in risks):
        return "MEDIUM"
    return "LOW"


def write_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_comparison_report_md(
    path: Path,
    summary: Dict[str, Any],
    key_analyses: List[Dict[str, Any]],
) -> None:
    lines = [
        "# X03 XER vs P6 SQLite Schema Comparison",
        "",
        f"- **Run ID:** {summary.get('run_id', '')}",
        f"- **Status:** {summary.get('status', '')}",
        f"- **XER DB:** `{summary.get('xer_db', '')}`",
        f"- **P6 DB:** `{summary.get('p6_db', '')}`",
        f"- **Opened read-only:** {summary.get('opened_readonly', False)}",
        "",
        "## Table counts",
        "",
        f"- XER tables: {summary.get('xer_table_count', 0)}",
        f"- P6 tables: {summary.get('p6_table_count', 0)}",
        f"- Exact table matches: {len(summary.get('exact_table_matches', []))}",
        "",
        "## Overall",
        "",
        f"- Overall insert risk: **{summary.get('overall_insert_risk', '')}**",
        f"- Recommended next step: {summary.get('recommended_next_step', '')}",
        "",
        "## Key table mapping",
        "",
        "| Table | XER rows | P6 rows | Col matches | Confidence | Risk |",
        "|-------|----------|---------|-------------|------------|------|",
    ]
    for a in key_analyses:
        lines.append(
            f"| {a.get('table', '')} | {a.get('xer_row_count', 0)} | {a.get('p6_row_count', 0)} | "
            f"{a.get('exact_column_match_count', 0)} | {a.get('mapping_confidence', '')} | {a.get('insert_risk', '')} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_insert_risk_report_md(path: Path, key_analyses: List[Dict[str, Any]]) -> None:
    lines = ["# P6 Insert Risk Report (X03)", ""]
    for a in key_analyses:
        lines.extend(
            [
                f"## {a.get('table', '')}",
                "",
                f"- XER exists: {a.get('xer_table_exists')}",
                f"- P6 exists: {a.get('p6_table_exists')}",
                f"- Mapping confidence: {a.get('mapping_confidence')}",
                f"- Insert risk: **{a.get('insert_risk')}**",
                f"- P6 triggers: {a.get('p6_trigger_count', 0)}",
                f"- P6 foreign keys: {len(a.get('p6_foreign_keys', []))}",
                f"- Likely ID columns: {', '.join(a.get('likely_id_columns', [])) or 'none'}",
            ]
        )
        missing_req = [
            c
            for c in a.get("p6_required_or_nonnullable_columns", [])
            if c.upper() not in {x.upper() for x in a.get("exact_column_matches", [])}
        ]
        lines.append(f"- Required P6 columns not in XER: {', '.join(missing_req) or 'none'}")
        for note in a.get("notes", []):
            lines.append(f"- {note}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_comparison(xer_db: Path, p6_db: Path, out_root: Path) -> Dict[str, Any]:
    run_id = new_run_id()
    out_dir = out_root / run_id / MODULE_NAME
    out_dir.mkdir(parents=True, exist_ok=True)

    if not xer_db.exists():
        return {"run_id": run_id, "status": "FAIL_XER_DB_NOT_FOUND", "reason": str(xer_db)}
    if not p6_db.exists():
        return {"run_id": run_id, "status": "FAIL_P6_DB_NOT_FOUND", "reason": str(p6_db)}

    try:
        xer_conn = open_readonly(xer_db)
        p6_conn = open_readonly(p6_db)
    except sqlite3.Error as exc:
        return {
            "run_id": run_id,
            "status": "FAIL_OPEN_READONLY",
            "reason": str(exc),
            "xer_db": str(xer_db),
            "p6_db": str(p6_db),
        }

    try:
        table_cmp = compare_tables(xer_conn, p6_conn)
        key_analyses = [
            analyse_key_table(kt, xer_conn, p6_conn, table_cmp["xer_tables"], table_cmp["p6_tables"])
            for kt in KEY_TABLES
        ]

        high = [a["table"] for a in key_analyses if a.get("mapping_confidence") == "high"]
        medium = [a["table"] for a in key_analyses if a.get("mapping_confidence") == "medium"]
        low = [a["table"] for a in key_analyses if a.get("mapping_confidence") == "low"]
        blocked = [a["table"] for a in key_analyses if a.get("mapping_confidence") == "BLOCKED"]

        overall_risk = overall_insert_risk(key_analyses)
        if overall_risk == "BLOCKED":
            next_step = "Resolve missing key tables before any sandbox import planning"
        elif overall_risk == "HIGH":
            next_step = "X04 dry-run plan must address triggers, IDs, and required P6 columns"
        elif overall_risk == "MEDIUM":
            next_step = "X04 dry-run plan should map defaults for required P6-only columns"
        else:
            next_step = "Proceed to X04 dry-run sandbox import plan for matched key tables"

        direct_mappings = {
            a["table"]: {
                "xer_table": a.get("xer_table_name"),
                "p6_table": a.get("p6_table_name"),
                "column_map": {c: c for c in a.get("exact_column_matches", [])},
                "mapping_confidence": a.get("mapping_confidence"),
                "insert_risk": a.get("insert_risk"),
            }
            for a in key_analyses
            if a.get("xer_table_exists") and a.get("p6_table_exists")
        }

        summary: Dict[str, Any] = {
            "run_id": run_id,
            "xer_db": str(xer_db.resolve()),
            "p6_db": str(p6_db.resolve()),
            "opened_readonly": True,
            "xer_table_count": len(table_cmp["xer_tables"]),
            "p6_table_count": len(table_cmp["p6_tables"]),
            "exact_table_matches": table_cmp["exact_table_matches"],
            "case_insensitive_table_matches": table_cmp["case_insensitive_matches"],
            "xer_tables_missing_in_p6": table_cmp["xer_tables_missing_in_p6"],
            "p6_tables_not_in_xer_count": len(table_cmp["p6_tables_not_in_xer"]),
            "key_tables_compared": list(KEY_TABLES),
            "high_confidence_mappings": high,
            "medium_confidence_mappings": medium,
            "low_confidence_mappings": low,
            "blocked_mappings": blocked,
            "overall_insert_risk": overall_risk,
            "recommended_next_step": next_step,
            "key_table_details": key_analyses,
            "status": PASS_STATUS,
            "reason": "Schema comparison and mapping risk report completed (read-only)",
            "output_dir": str(out_dir.resolve()),
        }

        (out_dir / "comparison_summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8"
        )
        (out_dir / "direct_table_mappings.json").write_text(
            json.dumps(direct_mappings, indent=2), encoding="utf-8"
        )

        table_rows = []
        for name in sorted(set(table_cmp["xer_tables"]) | set(table_cmp["p6_tables"])):
            xn = resolve_table_name(table_cmp["xer_tables"], name) or ""
            pn = resolve_table_name(table_cmp["p6_tables"], name) or ""
            table_rows.append(
                {
                    "table_upper": name.upper(),
                    "xer_table": xn,
                    "p6_table": pn,
                    "in_xer": bool(xn),
                    "in_p6": bool(pn),
                    "exact_name_match": xn == pn and bool(xn),
                    "case_insensitive_match": xn.upper() == pn.upper() if xn and pn else False,
                }
            )
        write_csv(
            out_dir / "table_name_comparison.csv",
            ["table_upper", "xer_table", "p6_table", "in_xer", "in_p6", "exact_name_match", "case_insensitive_match"],
            table_rows,
        )

        key_summary_rows = [
            {
                "table": a["table"],
                "xer_table_exists": a["xer_table_exists"],
                "p6_table_exists": a["p6_table_exists"],
                "xer_row_count": a["xer_row_count"],
                "p6_row_count": a["p6_row_count"],
                "exact_column_match_count": a["exact_column_match_count"],
                "mapping_confidence": a["mapping_confidence"],
                "insert_risk": a["insert_risk"],
                "p6_trigger_count": a["p6_trigger_count"],
                "notes": "; ".join(a.get("notes", [])),
            }
            for a in key_analyses
        ]
        write_csv(
            out_dir / "key_table_mapping_summary.csv",
            [
                "table",
                "xer_table_exists",
                "p6_table_exists",
                "xer_row_count",
                "p6_row_count",
                "exact_column_match_count",
                "mapping_confidence",
                "insert_risk",
                "p6_trigger_count",
                "notes",
            ],
            key_summary_rows,
        )

        required_rows: List[Dict[str, Any]] = []
        col_rows: List[Dict[str, Any]] = []
        for a in key_analyses:
            if not a.get("p6_table_exists"):
                continue
            p6_name = a["p6_table_name"]
            for col in table_info(p6_conn, p6_name):
                in_xer = col["name_upper"] in {c.upper() for c in a.get("xer_columns", [])}
                required_rows.append(
                    {
                        "table": a["table"],
                        "column_name": col["name"],
                        "column_type": col["type"],
                        "notnull": col["notnull"],
                        "default_value": col["default_value"],
                        "pk": col["pk"],
                        "present_in_xer": in_xer,
                    }
                )
                col_rows.append(
                    {
                        "table": a["table"],
                        "column_name": col["name"],
                        "column_type": col["type"],
                        "notnull": col["notnull"],
                        "default_value": col["default_value"],
                        "pk": col["pk"],
                        "in_xer": in_xer,
                        "in_p6": True,
                    }
                )
            for xc in a.get("xer_columns", []):
                if xc.upper() not in {c.upper() for c in a.get("p6_columns", [])}:
                    col_rows.append(
                        {
                            "table": a["table"],
                            "column_name": xc,
                            "column_type": "",
                            "notnull": "",
                            "default_value": "",
                            "pk": "",
                            "in_xer": True,
                            "in_p6": False,
                        }
                    )

        write_csv(
            out_dir / "p6_required_columns.csv",
            ["table", "column_name", "column_type", "notnull", "default_value", "pk", "present_in_xer"],
            required_rows,
        )
        write_csv(
            out_dir / "p6_key_table_columns.csv",
            ["table", "column_name", "column_type", "notnull", "default_value", "pk", "in_xer", "in_p6"],
            col_rows,
        )

        write_comparison_report_md(out_dir / "comparison_report.md", summary, key_analyses)
        write_insert_risk_report_md(out_dir / "p6_insert_risk_report.md", key_analyses)

        return summary

    except sqlite3.Error as exc:
        return {
            "run_id": run_id,
            "status": "FAIL_COMPARISON_FAILED",
            "reason": str(exc),
            "xer_db": str(xer_db),
            "p6_db": str(p6_db),
        }
    finally:
        xer_conn.close()
        p6_conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="X03: Compare XER analysis SQLite vs P6 SQLite (read-only)")
    parser.add_argument("--xer-db", required=True, help="Path to X01 xer_analysis.sqlite")
    parser.add_argument("--p6-db", required=True, help="Path to copied P6 SQLite database")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT), help="Output root under 06_output")
    args = parser.parse_args()

    xer_db = Path(args.xer_db.strip().strip('"'))
    p6_db = Path(args.p6_db.strip().strip('"'))
    if not xer_db.is_absolute():
        xer_db = (ROOT / xer_db).resolve()
    if not p6_db.is_absolute():
        p6_db = (ROOT / p6_db).resolve()

    out_root = Path(args.out_root)
    if not out_root.is_absolute():
        out_root = (ROOT / out_root).resolve()
    if not str(out_root).startswith(str((ROOT / "06_output").resolve())):
        print("ERROR: --out-root must be under 06_output", file=sys.stderr)
        return 1

    summary = run_comparison(xer_db, p6_db, out_root)
    print(f"X03 status: {summary.get('status', 'ERROR')}")
    print(f"Reason: {summary.get('reason', '')}")
    if summary.get("output_dir"):
        print(f"Evidence: {summary['output_dir']}")
    return 0 if summary.get("status") == PASS_STATUS else 1


if __name__ == "__main__":
    raise SystemExit(main())
