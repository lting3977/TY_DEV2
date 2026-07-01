"""
X04 — Dry-run sandbox import plan: XER analysis SQLite → copied P6 SQLite.

Safety: read-only only. No inserts, updates, deletes, or schema changes on any DB.
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
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_ROOT = ROOT / "06_output" / "x_modules" / "xer_sqlite_research" / "mapping_reports"
MODULE_NAME = "x04_plan_xer_to_p6_sqlite_sandbox_import"

PASS_STATUS = "PASS_X04_DRY_RUN_PLAN_CREATED"

CANDIDATE_TABLES = ("PROJECT", "PROJWBS", "TASK", "TASKPRED", "CALENDAR", "CURRTYPE")
MISSING_DEPENDENCY_TABLES = ("RSRC", "TASKRSRC", "ACTVCODE", "ACTVTYPE", "UDFVALUE")
EXTRA_REPORT_TABLES = ("OBS", "FINTMPL", "SCHEDOPTIONS")

INSERT_ORDER_TEMPLATE = (
    ("1", "CALENDAR", "calendar_mapping_plan.csv", "none"),
    ("2", "PROJECT", "proposed_id_remap_project.csv", "CALENDAR"),
    ("3", "PROJWBS", "proposed_id_remap_wbs.csv", "PROJECT"),
    ("4", "TASK", "proposed_id_remap_task.csv", "PROJWBS,CALENDAR"),
    ("5", "TASKPRED", "proposed_id_remap_taskpred.csv", "TASK"),
    ("6", "TASKRSRC", "missing_dependency_report.csv", "TASK,RSRC"),
    ("7", "ACTVCODE/ACTVTYPE", "missing_dependency_report.csv", "TASK"),
    ("8", "UDFVALUE", "missing_dependency_report.csv", "PROJECT,TASK"),
)

ID_OFFSET_BASE = 100000


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def open_readonly(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)


def resolve_table(tables: List[str], name: str) -> Optional[str]:
    upper = {t.upper(): t for t in tables}
    return upper.get(name.upper())


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def fetch_rows(conn: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    cur = conn.execute(f"SELECT * FROM {quote_ident(table)}")
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def row_get(row: Dict[str, Any], key: str) -> Any:
    key_upper = key.upper()
    for k, v in row.items():
        if k.upper() == key_upper:
            return v
    return None


def max_numeric_id(conn: sqlite3.Connection, table: str, column: str) -> int:
    t = resolve_table(
        [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()],
        table,
    )
    if not t:
        return 0
    col = resolve_column(conn, t, column)
    if not col:
        return 0
    try:
        val = conn.execute(
            f"SELECT MAX(CAST({quote_ident(col)} AS INTEGER)) FROM {quote_ident(t)}"
        ).fetchone()[0]
        return int(val) if val is not None else 0
    except sqlite3.Error:
        return 0


def resolve_column(conn: sqlite3.Connection, table: str, column: str) -> Optional[str]:
    rows = conn.execute(f"PRAGMA table_info({quote_ident(table)})").fetchall()
    upper = column.upper()
    for r in rows:
        if r[1].upper() == upper:
            return r[1]
    return None


def table_triggers(conn: sqlite3.Connection, table: str) -> List[Dict[str, str]]:
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=? ORDER BY name",
        (table,),
    ).fetchall()
    return [{"name": r[0], "sql": (r[1] or "")[:800]} for r in rows]


def classify_trigger_risk(triggers: List[Dict[str, str]]) -> str:
    if not triggers:
        return "LOW"
    high_markers = ("DELETE", "SUM", "AUDIT", "CHANGE", "UPDATE", "INSERT")
    high_count = 0
    for tr in triggers:
        sql = (tr.get("sql") or "").upper()
        if any(m in sql for m in high_markers):
            high_count += 1
    if len(triggers) >= 3 or high_count >= 2:
        return "HIGH"
    if triggers:
        return "MEDIUM"
    return "LOW"


def write_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_project_remap(
    xer_rows: List[Dict[str, Any]],
    p6_conn: sqlite3.Connection,
    p6_table: str,
    calendar_remap: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    p6_max = max_numeric_id(p6_conn, p6_table, "proj_id")
    proj_remap: Dict[str, str] = {}
    plan: List[Dict[str, Any]] = []
    offset = ID_OFFSET_BASE
    for idx, row in enumerate(xer_rows):
        old_id = row_get(row, "proj_id")
        if old_id is None or str(old_id).strip() == "":
            continue
        old_key = str(old_id)
        if old_key in proj_remap:
            continue
        new_id = p6_max + offset
        offset += 1
        proj_remap[old_key] = str(new_id)
        old_clndr = row_get(row, "clndr_id")
        new_clndr = calendar_remap.get(str(old_clndr), str(old_clndr) if old_clndr else "")
        plan.append(
            {
                "old_proj_id": old_key,
                "proposed_new_proj_id": new_id,
                "old_clndr_id": old_clndr,
                "proposed_clndr_id": new_clndr,
                "proj_short_name": row_get(row, "proj_short_name"),
                "guid": row_get(row, "guid"),
                "p6_max_proj_id_before": p6_max,
                "uniqueness_check": "proposed_id_not_in_p6" if new_id > p6_max else "review",
            }
        )
    return plan, proj_remap


def build_wbs_remap(
    xer_rows: List[Dict[str, Any]],
    p6_conn: sqlite3.Connection,
    p6_table: str,
    proj_remap: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    p6_max = max_numeric_id(p6_conn, p6_table, "wbs_id")
    wbs_remap: Dict[str, str] = {}
    plan: List[Dict[str, Any]] = []
    offset = ID_OFFSET_BASE
    for row in xer_rows:
        old_id = row_get(row, "wbs_id")
        if old_id is None or str(old_id).strip() == "":
            continue
        old_key = str(old_id)
        if old_key in wbs_remap:
            continue
        new_id = p6_max + offset
        offset += 1
        wbs_remap[old_key] = str(new_id)
        old_parent = row_get(row, "parent_wbs_id")
        old_proj = row_get(row, "proj_id")
        plan.append(
            {
                "old_wbs_id": old_key,
                "proposed_new_wbs_id": new_id,
                "old_parent_wbs_id": old_parent,
                "proposed_parent_wbs_id": wbs_remap.get(str(old_parent), str(old_parent) if old_parent else ""),
                "old_proj_id": old_proj,
                "proposed_proj_id": proj_remap.get(str(old_proj), str(old_proj) if old_proj else ""),
                "wbs_short_name": row_get(row, "wbs_short_name"),
                "wbs_name": row_get(row, "wbs_name"),
            }
        )
    # second pass for parent remap
    for entry in plan:
        op = entry.get("old_parent_wbs_id")
        if op is not None and str(op) in wbs_remap:
            entry["proposed_parent_wbs_id"] = wbs_remap[str(op)]
    return plan, wbs_remap


def build_task_remap(
    xer_rows: List[Dict[str, Any]],
    p6_conn: sqlite3.Connection,
    p6_table: str,
    proj_remap: Dict[str, str],
    wbs_remap: Dict[str, str],
    calendar_remap: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    p6_max = max_numeric_id(p6_conn, p6_table, "task_id")
    task_remap: Dict[str, str] = {}
    plan: List[Dict[str, Any]] = []
    offset = ID_OFFSET_BASE
    for row in xer_rows:
        old_id = row_get(row, "task_id")
        if old_id is None or str(old_id).strip() == "":
            continue
        old_key = str(old_id)
        if old_key in task_remap:
            continue
        new_id = p6_max + offset
        offset += 1
        task_remap[old_key] = str(new_id)
        old_wbs = row_get(row, "wbs_id")
        old_proj = row_get(row, "proj_id")
        old_clndr = row_get(row, "clndr_id")
        plan.append(
            {
                "old_task_id": old_key,
                "proposed_new_task_id": new_id,
                "old_wbs_id": old_wbs,
                "proposed_wbs_id": wbs_remap.get(str(old_wbs), str(old_wbs) if old_wbs else ""),
                "old_proj_id": old_proj,
                "proposed_proj_id": proj_remap.get(str(old_proj), str(old_proj) if old_proj else ""),
                "old_clndr_id": old_clndr,
                "proposed_clndr_id": calendar_remap.get(str(old_clndr), str(old_clndr) if old_clndr else ""),
                "task_code": row_get(row, "task_code"),
                "task_name": row_get(row, "task_name"),
            }
        )
    return plan, task_remap


def build_taskpred_remap(
    xer_rows: List[Dict[str, Any]],
    proj_remap: Dict[str, str],
    task_remap: Dict[str, str],
) -> List[Dict[str, Any]]:
    plan: List[Dict[str, Any]] = []
    offset = ID_OFFSET_BASE
    for row in xer_rows:
        old_pred_id = row_get(row, "task_pred_id")
        old_task = row_get(row, "task_id")
        old_pred_task = row_get(row, "pred_task_id")
        old_proj = row_get(row, "proj_id")
        proposed_pred_id = (ID_OFFSET_BASE + offset) if old_pred_id is not None else ""
        if old_pred_id is not None:
            offset += 1
        plan.append(
            {
                "old_task_pred_id": old_pred_id,
                "proposed_new_task_pred_id": proposed_pred_id,
                "old_task_id": old_task,
                "proposed_task_id": task_remap.get(str(old_task), str(old_task) if old_task else ""),
                "old_pred_task_id": old_pred_task,
                "proposed_pred_task_id": task_remap.get(str(old_pred_task), str(old_pred_task) if old_pred_task else ""),
                "old_proj_id": old_proj,
                "proposed_proj_id": proj_remap.get(str(old_proj), str(old_proj) if old_proj else ""),
                "pred_type": row_get(row, "pred_type"),
                "lag_hr_cnt": row_get(row, "lag_hr_cnt"),
            }
        )
    return plan


def build_calendar_mapping(
    xer_rows: List[Dict[str, Any]],
    p6_conn: sqlite3.Connection,
    p6_table: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    p6_rows = fetch_rows(p6_conn, p6_table)
    p6_by_name: Dict[str, Dict[str, Any]] = {}
    for row in p6_rows:
        name = row_get(row, "clndr_name")
        if name:
            p6_by_name[str(name).strip().lower()] = row

    calendar_remap: Dict[str, str] = {}
    plan: List[Dict[str, Any]] = []
    p6_max = max_numeric_id(p6_conn, p6_table, "clndr_id")
    next_new = p6_max + ID_OFFSET_BASE

    for row in xer_rows:
        old_id = row_get(row, "clndr_id")
        name = row_get(row, "clndr_name")
        name_key = str(name).strip().lower() if name else ""
        strategy = "B_create_new_id_later"
        proposed_id = next_new
        match_p6_id = ""
        notes = "No name match in P6; would allocate new clndr_id on write"

        if name_key and name_key in p6_by_name:
            p6_row = p6_by_name[name_key]
            match_p6_id = str(row_get(p6_row, "clndr_id"))
            strategy = "A_map_to_existing_p6_calendar"
            proposed_id = match_p6_id
            notes = f"Exact name match: {name}"
        else:
            next_new += 1

        if old_id is not None:
            calendar_remap[str(old_id)] = str(proposed_id)

        plan.append(
            {
                "old_clndr_id": old_id,
                "clndr_name": name,
                "strategy": strategy,
                "matched_p6_clndr_id": match_p6_id,
                "proposed_clndr_id": proposed_id,
                "notes": notes,
            }
        )
    return plan, calendar_remap


def compare_currtype(xer_rows: List[Dict[str, Any]], p6_conn: sqlite3.Connection, p6_table: str) -> Dict[str, Any]:
    p6_rows = fetch_rows(p6_conn, p6_table)
    xer_keys = {str(row_get(r, "curr_id")): r for r in xer_rows if row_get(r, "curr_id") is not None}
    p6_keys = {str(row_get(r, "curr_id")): r for r in p6_rows if row_get(r, "curr_id") is not None}
    identical = 0
    different = 0
    for cid, xrow in xer_keys.items():
        if cid not in p6_keys:
            different += 1
        elif str(row_get(xrow, "curr_short_name")) == str(row_get(p6_keys[cid], "curr_short_name")):
            identical += 1
        else:
            different += 1
    recommendation = "no_insert_recommended" if different == 0 and len(xer_keys) <= len(p6_keys) else "flag_risk_review"
    return {
        "xer_row_count": len(xer_rows),
        "p6_row_count": len(p6_rows),
        "matching_ids": identical,
        "different_or_new": different,
        "recommendation": recommendation,
    }


def check_project_conflict(
    xer_project_rows: List[Dict[str, Any]],
    p6_conn: sqlite3.Connection,
    p6_table: str,
) -> Dict[str, Any]:
    p6_rows = fetch_rows(p6_conn, p6_table)
    result: Dict[str, Any] = {
        "conflict_found": False,
        "conflict_type": "",
        "recommended_action": "proceed_with_new_proj_id_remap",
        "details": [],
    }
    for xrow in xer_project_rows:
        short = row_get(xrow, "proj_short_name")
        guid = row_get(xrow, "guid")
        old_id = row_get(xrow, "proj_id")
        for prow in p6_rows:
            if short and str(row_get(prow, "proj_short_name")) == str(short):
                result["conflict_found"] = True
                result["conflict_type"] = "proj_short_name_match"
                result["recommended_action"] = "use_new_proj_id_and_review_duplicate_short_name"
                result["details"].append(
                    {"xer_proj_id": old_id, "p6_proj_id": row_get(prow, "proj_id"), "proj_short_name": short}
                )
            if guid and row_get(prow, "guid") and str(row_get(prow, "guid")) == str(guid):
                result["conflict_found"] = True
                if not result["conflict_type"]:
                    result["conflict_type"] = "guid_match"
                result["recommended_action"] = "abort_or_import_as_copy_with_new_guid"
                result["details"].append({"xer_proj_id": old_id, "p6_proj_id": row_get(prow, "proj_id"), "guid": guid})
            if old_id is not None and str(row_get(prow, "proj_id")) == str(old_id):
                result["conflict_found"] = True
                if not result["conflict_type"]:
                    result["conflict_type"] = "proj_id_collision"
                result["recommended_action"] = "must_remap_proj_id_before_write"
                result["details"].append({"xer_proj_id": old_id, "p6_proj_id": row_get(prow, "proj_id")})
    return result


def missing_dependency_analysis(xer_tables: List[str], p6_tables: List[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    xer_upper = {t.upper() for t in xer_tables}
    p6_upper = {t.upper() for t in p6_tables}

    specs = [
        ("RSRC", "Resource master data", "TASKRSRC/resource assignments cannot be imported from this XER"),
        ("TASKRSRC", "Task-resource assignments", "Resource assignments cannot be imported"),
        ("ACTVCODE", "Activity codes", "Activity codes cannot be imported"),
        ("ACTVTYPE", "Activity code types", "Activity code types cannot be imported"),
        ("UDFVALUE", "User-defined field values", "UDFs cannot be imported"),
        ("SCHEDOPTIONS", "Schedule options (XER only)", "Table exists in XER but not in P6 SQLite schema"),
        ("OBS", "OBS hierarchy", "Present in XER; verify OBS mapping if needed"),
        ("FINTMPL", "Financial template", "Present in both; low priority for schedule import"),
    ]
    for table, purpose, impact in specs:
        in_xer = table.upper() in xer_upper
        in_p6 = table.upper() in p6_upper
        blocked = table in MISSING_DEPENDENCY_TABLES and not in_xer
        rows.append(
            {
                "table": table,
                "in_xer": in_xer,
                "in_p6": in_p6,
                "blocked_for_import": blocked,
                "purpose": purpose,
                "impact_if_missing": impact if blocked or (table == "SCHEDOPTIONS" and in_xer and not in_p6) else "",
                "status": "BLOCKED" if blocked else ("XER_ONLY" if in_xer and not in_p6 else "OK"),
            }
        )
    return rows


def decide_readiness(
    missing_blocked: List[str],
    trigger_risk: str,
    conflict: Dict[str, Any],
    candidate_tables: List[str],
) -> Tuple[str, str, str]:
    blockers: List[str] = []
    if missing_blocked:
        blockers.append(f"Missing XER tables: {', '.join(missing_blocked)}")
    if trigger_risk == "HIGH":
        blockers.append("P6 triggers on candidate tables (HIGH risk)")
    if conflict.get("conflict_found"):
        blockers.append(f"Project conflict: {conflict.get('conflict_type')}")

    if missing_blocked and trigger_risk == "HIGH":
        readiness = "DRY_RUN_BLOCKED_MISSING_DEPENDENCIES"
        step = "Obtain fuller XER with RSRC/TASKRSRC/ACTV/UDF tables; design trigger-safe write path"
    elif missing_blocked:
        readiness = "DRY_RUN_BLOCKED_MISSING_DEPENDENCIES"
        step = "Use fuller XER export or accept partial schedule-only import scope"
    elif trigger_risk == "HIGH":
        readiness = "DRY_RUN_BLOCKED_TRIGGER_ID_RISK"
        step = "Design write mode with trigger bypass strategy or staged sandbox DB creation"
    elif blockers:
        readiness = "DRY_RUN_BLOCKED_SCHEMA_MISMATCH"
        step = "Resolve schema/conflict issues before write design"
    else:
        readiness = "DRY_RUN_READY_FOR_SANDBOX_WRITE_REVIEW"
        step = "Proceed to X05 write design on sandbox copy only"

    return readiness, "; ".join(blockers), step


def write_readiness_checklist(path: Path, summary: Dict[str, Any], blockers: List[str]) -> None:
    lines = [
        "# Write Readiness Checklist (X04 Dry-Run)",
        "",
        f"- **Overall readiness:** {summary.get('overall_readiness', '')}",
        f"- **Can attempt sandbox write now:** {'NO' if 'BLOCKED' in summary.get('overall_readiness', '') else 'REVIEW'}",
        "",
        "## Prerequisites before any write mode",
        "",
        "- [ ] Work only on a **copy** of P6 SQLite (never live P6 DB)",
        "- [ ] X05 write mode not yet implemented",
        "- [ ] Full dependency tables present in XER (RSRC, TASKRSRC, ACTVCODE, ACTVTYPE, UDFVALUE)",
        "- [ ] ID remap plan reviewed and approved",
        "- [ ] Trigger impact understood per table",
        "- [ ] Project conflict resolved",
        "- [ ] Calendar mapping strategy confirmed",
        "- [ ] Rollback / backup of sandbox copy confirmed",
        "",
        "## Major blockers",
        "",
    ]
    for b in blockers:
        lines.append(f"- {b}")
    lines.extend(["", f"## Recommended next step", "", summary.get("recommended_next_step", ""), ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def write_dry_run_report(path: Path, summary: Dict[str, Any], blockers: List[str]) -> None:
    lines = [
        "# X04 Dry-Run Sandbox Import Plan",
        "",
        f"- **Run ID:** {summary.get('run_id')}",
        f"- **Status:** {summary.get('status')}",
        f"- **Mode:** {summary.get('mode')}",
        f"- **Overall readiness:** {summary.get('overall_readiness')}",
        "",
        "## XER row counts",
        "",
        f"- PROJECT: {summary.get('xer_project_count')}",
        f"- PROJWBS: {summary.get('xer_wbs_count')}",
        f"- TASK: {summary.get('xer_task_count')}",
        f"- TASKPRED: {summary.get('xer_taskpred_count')}",
        f"- CALENDAR: {summary.get('xer_calendar_count')}",
        "",
        "## Insert order (planned, not executed)",
        "",
        "1. CALENDAR mapping",
        "2. PROJECT",
        "3. PROJWBS",
        "4. TASK",
        "5. TASKPRED",
        "6. TASKRSRC (blocked — no RSRC in XER)",
        "7. ACTVCODE/ACTVTYPE (blocked)",
        "8. UDFVALUE (blocked)",
        "",
        "## Blockers",
        "",
    ]
    for b in blockers:
        lines.append(f"- {b}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_dry_run(xer_db: Path, p6_db: Path, out_root: Path) -> Dict[str, Any]:
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
        return {"run_id": run_id, "status": "FAIL_OPEN_READONLY", "reason": str(exc)}

    try:
        xer_tables = [
            r[0]
            for r in xer_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '_xer_%'"
            ).fetchall()
        ]
        p6_tables = [
            r[0] for r in p6_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]

        def xer_table(name: str) -> Optional[str]:
            return resolve_table(xer_tables, name)

        def p6_table(name: str) -> Optional[str]:
            return resolve_table(p6_tables, name)

        xer_counts = {t: len(fetch_rows(xer_conn, xer_table(t))) if xer_table(t) else 0 for t in CANDIDATE_TABLES}

        calendar_plan, calendar_remap = [], {}
        if xer_table("CALENDAR") and p6_table("CALENDAR"):
            calendar_plan, calendar_remap = build_calendar_mapping(
                fetch_rows(xer_conn, xer_table("CALENDAR")), p6_conn, p6_table("CALENDAR")
            )

        project_plan, proj_remap = [], {}
        if xer_table("PROJECT") and p6_table("PROJECT"):
            project_plan, proj_remap = build_project_remap(
                fetch_rows(xer_conn, xer_table("PROJECT")), p6_conn, p6_table("PROJECT"), calendar_remap
            )

        wbs_plan, wbs_remap = [], {}
        if xer_table("PROJWBS") and p6_table("PROJWBS"):
            wbs_plan, wbs_remap = build_wbs_remap(
                fetch_rows(xer_conn, xer_table("PROJWBS")), p6_conn, p6_table("PROJWBS"), proj_remap
            )

        task_plan, task_remap = [], {}
        if xer_table("TASK") and p6_table("TASK"):
            task_plan, task_remap = build_task_remap(
                fetch_rows(xer_conn, xer_table("TASK")),
                p6_conn,
                p6_table("TASK"),
                proj_remap,
                wbs_remap,
                calendar_remap,
            )

        taskpred_plan: List[Dict[str, Any]] = []
        if xer_table("TASKPRED") and p6_table("TASKPRED"):
            taskpred_plan = build_taskpred_remap(
                fetch_rows(xer_conn, xer_table("TASKPRED")), proj_remap, task_remap
            )

        currtype_analysis = {}
        if xer_table("CURRTYPE") and p6_table("CURRTYPE"):
            currtype_analysis = compare_currtype(
                fetch_rows(xer_conn, xer_table("CURRTYPE")), p6_conn, p6_table("CURRTYPE")
            )

        conflict = check_project_conflict(
            fetch_rows(xer_conn, xer_table("PROJECT")) if xer_table("PROJECT") else [],
            p6_conn,
            p6_table("PROJECT") or "PROJECT",
        )

        missing_dep = missing_dependency_analysis(xer_tables, p6_tables)
        missing_blocked = [r["table"] for r in missing_dep if r.get("blocked_for_import")]

        trigger_rows: List[Dict[str, Any]] = []
        trigger_levels: List[str] = []
        for t in CANDIDATE_TABLES:
            pt = p6_table(t)
            if not pt:
                continue
            triggers = table_triggers(p6_conn, pt)
            risk = classify_trigger_risk(triggers)
            trigger_levels.append(risk)
            for tr in triggers:
                trigger_rows.append(
                    {
                        "table": t,
                        "trigger_name": tr["name"],
                        "trigger_risk": risk,
                        "fires_on_insert": "INSERT" in (tr.get("sql") or "").upper() or risk != "LOW",
                        "sql_excerpt": tr.get("sql", "")[:200],
                        "notes": "Triggers fire on write; may update audit/summary tables",
                    }
                )
            if not triggers:
                trigger_rows.append(
                    {"table": t, "trigger_name": "", "trigger_risk": "LOW", "fires_on_insert": False, "sql_excerpt": "", "notes": "No triggers"}
                )

        overall_trigger = "HIGH" if "HIGH" in trigger_levels else ("MEDIUM" if "MEDIUM" in trigger_levels else "LOW")

        candidate_insert = [t for t in CANDIDATE_TABLES if xer_table(t) and p6_table(t)]
        blocked_tables = list(MISSING_DEPENDENCY_TABLES) + ["SCHEDOPTIONS"]

        readiness, blocker_text, next_step = decide_readiness(
            missing_blocked, overall_trigger, conflict, candidate_insert
        )
        blockers = [b for b in blocker_text.split("; ") if b]

        insert_order_rows = []
        for step, table, artifact, deps in INSERT_ORDER_TEMPLATE:
            status = "PLANNED"
            if table in MISSING_DEPENDENCY_TABLES or table.startswith("ACTVCODE"):
                status = "BLOCKED_MISSING_XER"
            elif table == "TASKRSRC":
                status = "BLOCKED_MISSING_XER"
            insert_order_rows.append(
                {
                    "step": step,
                    "table": table,
                    "depends_on": deps,
                    "status": status,
                    "artifact": artifact,
                    "xer_rows": xer_counts.get(table.split("/")[0], 0) if table in xer_counts else 0,
                }
            )

        summary: Dict[str, Any] = {
            "run_id": run_id,
            "mode": "dry-run",
            "xer_db": str(xer_db.resolve()),
            "p6_db": str(p6_db.resolve()),
            "opened_readonly": True,
            "write_attempted": False,
            "write_performed": False,
            "xer_project_count": xer_counts.get("PROJECT", 0),
            "xer_wbs_count": xer_counts.get("PROJWBS", 0),
            "xer_task_count": xer_counts.get("TASK", 0),
            "xer_taskpred_count": xer_counts.get("TASKPRED", 0),
            "xer_calendar_count": xer_counts.get("CALENDAR", 0),
            "missing_dependency_tables": missing_blocked,
            "candidate_insert_tables": candidate_insert,
            "blocked_tables": blocked_tables,
            "project_conflict_found": conflict.get("conflict_found", False),
            "project_conflict": conflict,
            "id_remap_plan_created": bool(project_plan or wbs_plan or task_plan),
            "insert_order_plan_created": True,
            "trigger_risk_level": overall_trigger,
            "currtype_analysis": currtype_analysis,
            "overall_readiness": readiness,
            "recommended_next_step": next_step,
            "major_blockers": blockers,
            "can_attempt_sandbox_write_now": False,
            "required_before_write": [
                "Fuller XER with RSRC/TASKRSRC/ACTVCODE/ACTVTYPE/UDFVALUE" if missing_blocked else None,
                "Trigger-safe write strategy",
                "Sandbox DB copy backup",
                "X05 write mode implementation",
            ],
            "status": PASS_STATUS,
            "reason": "Dry-run import plan created without any database writes",
            "output_dir": str(out_dir.resolve()),
        }
        summary["required_before_write"] = [x for x in summary["required_before_write"] if x]

        (out_dir / "x04_dry_run_summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8"
        )

        write_csv(
            out_dir / "proposed_id_remap_project.csv",
            [
                "old_proj_id",
                "proposed_new_proj_id",
                "old_clndr_id",
                "proposed_clndr_id",
                "proj_short_name",
                "guid",
                "p6_max_proj_id_before",
                "uniqueness_check",
            ],
            project_plan,
        )
        write_csv(
            out_dir / "proposed_id_remap_wbs.csv",
            [
                "old_wbs_id",
                "proposed_new_wbs_id",
                "old_parent_wbs_id",
                "proposed_parent_wbs_id",
                "old_proj_id",
                "proposed_proj_id",
                "wbs_short_name",
                "wbs_name",
            ],
            wbs_plan,
        )
        write_csv(
            out_dir / "proposed_id_remap_task.csv",
            [
                "old_task_id",
                "proposed_new_task_id",
                "old_wbs_id",
                "proposed_wbs_id",
                "old_proj_id",
                "proposed_proj_id",
                "old_clndr_id",
                "proposed_clndr_id",
                "task_code",
                "task_name",
            ],
            task_plan,
        )
        write_csv(
            out_dir / "proposed_id_remap_taskpred.csv",
            [
                "old_task_pred_id",
                "proposed_new_task_pred_id",
                "old_task_id",
                "proposed_task_id",
                "old_pred_task_id",
                "proposed_pred_task_id",
                "old_proj_id",
                "proposed_proj_id",
                "pred_type",
                "lag_hr_cnt",
            ],
            taskpred_plan,
        )
        write_csv(
            out_dir / "calendar_mapping_plan.csv",
            ["old_clndr_id", "clndr_name", "strategy", "matched_p6_clndr_id", "proposed_clndr_id", "notes"],
            calendar_plan,
        )
        write_csv(
            out_dir / "insert_order_plan.csv",
            ["step", "table", "depends_on", "status", "artifact", "xer_rows"],
            insert_order_rows,
        )
        write_csv(
            out_dir / "missing_dependency_report.csv",
            ["table", "in_xer", "in_p6", "blocked_for_import", "purpose", "impact_if_missing", "status"],
            missing_dep,
        )
        write_csv(
            out_dir / "trigger_risk_report.csv",
            ["table", "trigger_name", "trigger_risk", "fires_on_insert", "sql_excerpt", "notes"],
            trigger_rows,
        )
        write_csv(
            out_dir / "conflict_check_report.csv",
            ["conflict_found", "conflict_type", "recommended_action"],
            [
                {
                    "conflict_found": conflict.get("conflict_found"),
                    "conflict_type": conflict.get("conflict_type"),
                    "recommended_action": conflict.get("recommended_action"),
                }
            ],
        )
        if conflict.get("details"):
            write_csv(
                out_dir / "conflict_check_details.csv",
                ["xer_proj_id", "p6_proj_id", "proj_short_name", "guid"],
                conflict["details"],
            )

        write_dry_run_report(out_dir / "x04_dry_run_report.md", summary, blockers)
        write_readiness_checklist(out_dir / "write_readiness_checklist.md", summary, blockers)

        return summary

    except sqlite3.Error as exc:
        return {
            "run_id": run_id,
            "status": "FAIL_DRY_RUN_PLAN_FAILED",
            "reason": str(exc),
        }
    finally:
        xer_conn.close()
        p6_conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="X04: Dry-run XER to P6 sandbox import plan (read-only)")
    parser.add_argument("--xer-db", required=True)
    parser.add_argument("--p6-db", required=True)
    parser.add_argument("--mode", default="dry-run", choices=["dry-run", "write"])
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    args = parser.parse_args()

    if args.mode == "write":
        print("X04 status: FAIL_WRITE_MODE_DISABLED")
        print("Reason: Write mode is not implemented; use --mode dry-run only")
        return 1

    xer_db = Path(args.xer_db.strip().strip('"'))
    p6_db = Path(args.p6_db.strip().strip('"'))
    if not xer_db.is_absolute():
        xer_db = (ROOT / xer_db).resolve()
    if not p6_db.is_absolute():
        p6_db = (ROOT / p6_db).resolve()

    out_root = Path(args.out_root)
    if not out_root.is_absolute():
        out_root = (ROOT / out_root).resolve()

    summary = run_dry_run(xer_db, p6_db, out_root)
    print(f"X04 status: {summary.get('status', 'ERROR')}")
    print(f"Reason: {summary.get('reason', '')}")
    print(f"Overall readiness: {summary.get('overall_readiness', '')}")
    if summary.get("output_dir"):
        print(f"Evidence: {summary['output_dir']}")
    return 0 if summary.get("status") == PASS_STATUS else 1


if __name__ == "__main__":
    raise SystemExit(main())
