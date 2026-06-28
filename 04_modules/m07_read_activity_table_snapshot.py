"""
M07 — Read Activity Table Snapshot (Phase 6).

Read-only capture of the visible Activities table from P6.
P6-window-only OCR. No layout change, schedule edit, or data modification.
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
sys.path.insert(0, str(ROOT / "02_eye"))
sys.path.insert(0, str(ROOT / "02_hand"))
sys.path.insert(0, str(ROOT / "02_accessibility"))
sys.path.insert(0, str(ROOT / "04_modules"))

import importlib.util


def _bootstrap() -> None:
    acc = ROOT / "02_accessibility"
    for name, folder in [
        ("accessibility", acc),
        ("accessibility.eye", acc / "eye"),
        ("accessibility.hand", acc / "hand"),
        ("accessibility.brain", acc / "brain"),
        ("eye", ROOT / "02_eye"),
        ("hand", ROOT / "02_hand"),
    ]:
        init = folder / "__init__.py"
        spec = importlib.util.spec_from_file_location(
            name, init, submodule_search_locations=[str(folder)]
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load {name}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)


_bootstrap()

from accessibility.hand import keyboard_tools, window_tools  # noqa: E402
from eye.ocr import collect_text_blob, is_easyocr_available, normalize_text  # noqa: E402
from eye.screenshot import P6Rect  # noqa: E402
from hand.p6_prepare import get_fresh_p6_rect, prepare_p6_for_test  # noqa: E402
from m06_go_to_activities import (  # noqa: E402
    CONFIG_PATH,
    SCREEN_RULE_PATH,
    capture_and_ocr_step,
    confirm_project_open,
    confirms_activities_workspace,
    load_json,
    navigate_to_activities,
    write_json,
)

MODULE_NAME = "m07_read_activity_table_snapshot"
ROW_Y_TOLERANCE = 14
TABLE_MIN_Y = 130
FOOTER_Y_RATIO = 0.82

HEADER_KEYWORDS = (
    "activity id",
    "activity",
    "activity name",
    "start",
    "finish",
    "original duration",
    "remaining duration",
    "total float",
    "wbs",
    "layout",
)
ACTIVITY_ID_PATTERN = re.compile(r"^[a-z]?\d{3,5}[a-z]?$|^[a-z]\d{3,4}$", re.I)
DATE_PATTERN = re.compile(r"\d{1,2}[-/][a-z]{3}[-/]\d{2,4}", re.I)
FOOTER_PHRASES = (
    "access mode",
    "data date",
    "baseline:",
    "baseline",
    "user;",
    "user:",
    "db:",
    "main (professional)",
    "portfolio:",
    "filter: all",
)


@dataclass
class RunEvidence:
    run_id: str
    folder: Path
    screenshots_dir: Path
    ocr_dir: Path
    classification_dir: Path
    popup_dir: Path
    extracted_dir: Path
    steps: List[str] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    ocr_files: List[str] = field(default_factory=list)
    classification_files: List[str] = field(default_factory=list)
    popup_files: List[str] = field(default_factory=list)
    extracted_files: List[str] = field(default_factory=list)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_evidence(run_id: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / run_id / MODULE_NAME
    for sub in ("screenshots", "ocr", "classification", "popup", "extracted"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    return RunEvidence(
        run_id=run_id,
        folder=folder,
        screenshots_dir=folder / "screenshots",
        ocr_dir=folder / "ocr",
        classification_dir=folder / "classification",
        popup_dir=folder / "popup",
        extracted_dir=folder / "extracted",
    )


def bbox_center(entry: Dict[str, Any]) -> Tuple[float, float]:
    xs = [p[0] for p in entry["bbox"]]
    ys = [p[1] for p in entry["bbox"]]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def table_entries(
    entries: List[Dict[str, Any]], min_confidence: float, p6_height: int = 1500
) -> List[Dict[str, Any]]:
    footer_y = p6_height * FOOTER_Y_RATIO
    out: List[Dict[str, Any]] = []
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        _cx, cy = bbox_center(entry)
        if cy < TABLE_MIN_Y or cy > footer_y:
            continue
        text = entry.get("text", "").strip()
        if not text or len(text) < 2:
            continue
        norm = entry.get("normalized", "")
        if norm == "activities" and cy < 160:
            continue
        out.append(entry)
    return out


def group_into_rows(
    entries: List[Dict[str, Any]], y_tolerance: float = ROW_Y_TOLERANCE
) -> List[List[Dict[str, Any]]]:
    if not entries:
        return []
    sorted_entries = sorted(entries, key=lambda e: bbox_center(e)[1])
    rows: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_y: Optional[float] = None

    for entry in sorted_entries:
        _x, y = bbox_center(entry)
        if current_y is None or abs(y - current_y) <= y_tolerance:
            current.append(entry)
            current_y = y if current_y is None else (current_y + y) / 2
        else:
            if current:
                rows.append(sorted(current, key=lambda e: bbox_center(e)[0]))
            current = [entry]
            current_y = y
    if current:
        rows.append(sorted(current, key=lambda e: bbox_center(e)[0]))
    return rows


def row_text(row: List[Dict[str, Any]]) -> str:
    return " | ".join(e.get("text", "").strip() for e in row if e.get("text"))


def row_normalized_blob(row: List[Dict[str, Any]]) -> str:
    return " ".join(e.get("normalized", "") for e in row)


def is_header_row(row: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    blob = row_normalized_blob(row)
    cells = [e.get("normalized", "").strip() for e in row]

    has_activity_name = "activity name" in blob
    has_start = "start" in blob
    has_finish = "finish" in blob
    has_activity_id = "activity id" in blob
    has_activity_col = any(c == "activity" for c in cells) or has_activity_id

    if "activities" in blob and not has_activity_name:
        return False, []

    detected: List[str] = []
    if has_activity_id:
        detected.append("activity id")
    elif has_activity_col:
        detected.append("activity")
    if has_activity_name:
        detected.append("activity name")
    if has_start:
        detected.append("start")
    if has_finish:
        detected.append("finish")

    for h in HEADER_KEYWORDS:
        if h in blob and h not in detected and h not in ("activity",):
            if h not in detected:
                detected.append(h)

    ok = has_activity_col and has_activity_name and has_start and has_finish
    return ok, sorted(set(detected))


def is_footer_or_status_row(row: List[Dict[str, Any]], p6_height: int = 1500) -> bool:
    blob = row_normalized_blob(row)
    if any(phrase in blob for phrase in FOOTER_PHRASES):
        return True
    if bbox_center(row[0])[1] > p6_height * FOOTER_Y_RATIO:
        return True
    if "professional)" in blob and "user" in blob:
        return True
    return False


def normalize_activity_id_candidate(raw: str) -> Optional[str]:
    norm = normalize_text(raw).replace(" ", "")
    if not norm:
        return None
    candidate = norm
    ocr_fixes = {"z": "2", "o": "0", "l": "1", "i": "1", "s": "5"}
    if re.match(r"^a\d{3,4}[a-z]?$", candidate):
        fixed = "a" + "".join(ocr_fixes.get(c, c) for c in candidate[1:])
        if fixed != candidate and re.match(r"^a\d{4}$", fixed):
            return fixed.upper()
    if re.match(r"^a\d{3,4}$", candidate):
        return candidate.upper()
    return None


def normalize_cell(raw: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"raw": raw}
    norm = normalize_text(raw)
    out["normalized"] = norm
    id_candidate = normalize_activity_id_candidate(raw)
    if id_candidate:
        out["normalized_candidate"] = id_candidate
    date_match = DATE_PATTERN.search(raw)
    if date_match:
        out["date_candidate"] = date_match.group(0)
    return out


def looks_like_activity_row(row: List[Dict[str, Any]]) -> bool:
    if is_footer_or_status_row(row):
        return False
    texts = [e.get("text", "").strip() for e in row]
    norm_texts = [e.get("normalized", "") for e in row]
    for t, n in zip(texts, norm_texts):
        if normalize_activity_id_candidate(t):
            return True
        if ACTIVITY_ID_PATTERN.match(n.replace(" ", "")):
            return True
        if DATE_PATTERN.search(t):
            return True
        if n.startswith("a") and any(c.isdigit() for c in n) and len(n) >= 4:
            return True
    joined = " ".join(norm_texts)
    if "new activity" in joined:
        return True
    return False


def build_row_record(row: List[Dict[str, Any]], row_type: str) -> Dict[str, Any]:
    cells_raw = [e.get("text", "").strip() for e in row]
    cells_norm = [normalize_cell(t) for t in cells_raw]
    return {
        "row_type": row_type,
        "raw_line": row_text(row),
        "cells": cells_raw,
        "normalized_cells": cells_norm,
        "y_center": round(bbox_center(row[0])[1], 1),
    }


def detect_table_evidence(
    entries: List[Dict[str, Any]], min_confidence: float
) -> Dict[str, Any]:
    p6_height = 1500
    if entries:
        max_y = max(bbox_center(e)[1] for e in entries if e.get("bbox"))
        p6_height = max(int(max_y) + 50, 800)

    table_ents = table_entries(entries, min_confidence, p6_height)
    rows = group_into_rows(table_ents)
    raw_lines = [row_text(r) for r in rows if row_text(r)]

    header_row_idx: Optional[int] = None
    detected_headers: List[str] = []
    header_record: Optional[Dict[str, Any]] = None

    for idx, row in enumerate(rows):
        is_hdr, hits = is_header_row(row)
        if is_hdr:
            header_row_idx = idx
            detected_headers = hits
            header_record = build_row_record(row, "header")
            break

    if header_row_idx is None:
        blob = collect_text_blob(table_ents, min_confidence)
        detected_headers = [h for h in HEADER_KEYWORDS if h in blob and h != "activities"]

    raw_rows: List[Dict[str, Any]] = []
    normalized_rows: List[Dict[str, Any]] = []
    filtered_footer_rows: List[Dict[str, Any]] = []
    start_idx = (header_row_idx + 1) if header_row_idx is not None else 0

    for idx, row in enumerate(rows):
        rec = build_row_record(row, "unknown")
        if header_row_idx is not None and idx == header_row_idx:
            raw_rows.append(header_record or rec)
            normalized_rows.append(header_record or rec)
            continue
        if is_footer_or_status_row(row, p6_height):
            rec["row_type"] = "filtered_footer"
            filtered_footer_rows.append(rec)
            continue
        if idx < start_idx:
            continue
        if looks_like_activity_row(row):
            rec["row_type"] = "activity"
            raw_rows.append(rec)
            norm_rec = dict(rec)
            norm_cells = []
            for cell in rec.get("normalized_cells", []):
                nc = dict(cell)
                if "normalized_candidate" in nc:
                    nc["display"] = nc["normalized_candidate"]
                else:
                    nc["display"] = nc.get("raw", "")
                norm_cells.append(nc)
            norm_rec["normalized_cells"] = norm_cells
            normalized_rows.append(norm_rec)

    activity_rows = [r for r in raw_rows if r.get("row_type") == "activity"]
    header_detected = header_row_idx is not None and bool(detected_headers)
    grid_score = sum(
        1 for h in ("activity", "activity id", "activity name", "start", "finish")
        if h in detected_headers
    )
    table_detected = header_detected or (grid_score >= 2 and bool(activity_rows)) or bool(
        activity_rows
    )

    return {
        "table_detected": table_detected,
        "header_detected": header_detected,
        "detected_headers": sorted(set(detected_headers)),
        "raw_lines": raw_lines,
        "raw_rows": raw_rows,
        "normalized_rows": normalized_rows,
        "activity_rows": activity_rows,
        "visible_row_count": len(activity_rows),
        "footer_filtered_count": len(filtered_footer_rows),
        "filtered_footer_rows": filtered_footer_rows,
        "ocr_row_count": len(rows),
        "header_row": header_record,
    }


def save_extractions(
    evidence: RunEvidence, extraction: Dict[str, Any]
) -> Dict[str, str]:
    paths: Dict[str, str] = {}

    raw_path = evidence.extracted_dir / "activity_table_raw_lines.json"
    write_json(
        raw_path,
        {
            "line_count": len(extraction.get("raw_lines", [])),
            "lines": extraction.get("raw_lines", []),
        },
    )
    paths["raw_lines"] = str(raw_path)
    evidence.extracted_files.append(str(raw_path))

    rows_path = evidence.extracted_dir / "activity_table_rows.json"
    rows_payload = {
        "visible_row_count": extraction.get("visible_row_count", 0),
        "footer_filtered_count": extraction.get("footer_filtered_count", 0),
        "detected_headers": extraction.get("detected_headers", []),
        "header_detected": extraction.get("header_detected", False),
        "raw_rows": extraction.get("raw_rows", []),
        "normalized_rows": extraction.get("normalized_rows", []),
        "filtered_footer_rows": extraction.get("filtered_footer_rows", []),
    }
    write_json(rows_path, rows_payload)
    paths["rows"] = str(rows_path)
    evidence.extracted_files.append(str(rows_path))

    csv_path = evidence.extracted_dir / "activity_table_snapshot.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "row_index",
                "row_type",
                "raw_line",
                "cells",
                "normalized_candidates",
            ]
        )
        for idx, row in enumerate(extraction.get("raw_rows", []), start=1):
            candidates = []
            for cell in row.get("normalized_cells", []):
                if "normalized_candidate" in cell:
                    candidates.append(f"{cell.get('raw')}->{cell['normalized_candidate']}")
            writer.writerow(
                [
                    idx,
                    row.get("row_type", ""),
                    row.get("raw_line", ""),
                    " | ".join(row.get("cells", [])),
                    "; ".join(candidates),
                ]
            )
    paths["csv"] = str(csv_path)
    evidence.extracted_files.append(str(csv_path))
    return paths


def decide_status(extraction: Dict[str, Any], activities_ok: bool) -> Tuple[str, str]:
    if not activities_ok:
        return "FAIL_ACTIVITIES_NOT_FOUND", "Activities workspace not confirmed"

    row_count = extraction.get("visible_row_count", 0)
    has_lines = bool(extraction.get("raw_lines"))
    header_ok = extraction.get("header_detected", False)

    if not extraction.get("table_detected"):
        if has_lines or row_count >= 1:
            return (
                "PASS_PARTIAL_SNAPSHOT",
                "Activities confirmed; table-like OCR lines captured but headers incomplete",
            )
        return (
            "FAIL_TABLE_NOT_DETECTED",
            "Activities workspace confirmed but no table/header/row evidence found",
        )

    if header_ok and row_count >= 1:
        return (
            "PASS",
            f"Activity table snapshot captured with {row_count} visible row(s) and header detected",
        )

    if row_count >= 1 or has_lines:
        return (
            "PASS_PARTIAL_SNAPSHOT",
            "Activities confirmed; partial table snapshot (incomplete headers or rows)",
        )

    return (
        "FAIL_TABLE_NOT_DETECTED",
        "Table evidence weak — no extractable rows or lines",
    )


def finish_result(
    evidence: RunEvidence,
    project_name: str,
    status: str,
    reason: str,
    *,
    window_title: str = "",
    screen_state: str = "",
    table_detected: bool = False,
    header_detected: bool = False,
    visible_row_count: int = 0,
    detected_headers: Optional[List[str]] = None,
    footer_filtered_count: int = 0,
    sample_rows: Optional[List[Any]] = None,
    manual_review_required: bool = False,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    result = {
        "run_id": evidence.run_id,
        "module": MODULE_NAME,
        "project_name": project_name,
        "status": status,
        "reason": reason,
        "window_title": window_title,
        "screenshots": evidence.screenshots,
        "ocr_files": evidence.ocr_files,
        "classification_files": evidence.classification_files,
        "popup_files": evidence.popup_files,
        "extracted_files": evidence.extracted_files,
        "screen_state": screen_state,
        "table_detected": table_detected,
        "header_detected": header_detected,
        "visible_row_count": visible_row_count,
        "detected_headers": detected_headers or [],
        "footer_filtered_count": footer_filtered_count,
        "sample_rows": sample_rows or [],
        "manual_review_required": manual_review_required,
        "error": error,
        "steps": evidence.steps,
    }
    write_json(evidence.folder / "result.json", result)
    write_report(evidence, result)
    return result


def write_report(evidence: RunEvidence, result: Dict[str, Any]) -> None:
    ocr_summary: List[str] = []
    for path in result.get("ocr_files", []):
        try:
            data = load_json(Path(path))
            texts = [e.get("text", "") for e in data.get("entries", [])[:15]]
            ocr_summary.append(f"{path}: {', '.join(texts)}")
        except Exception:  # noqa: BLE001
            ocr_summary.append(path)

    lines = [
        "# M07 Read Activity Table Snapshot Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project name: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Window title: {result.get('window_title', '')}",
        f"- Screen state: {result.get('screen_state', '')}",
        f"- Table detected: {result.get('table_detected')}",
        f"- Header detected: {result.get('header_detected')}",
        f"- Detected headers: {result.get('detected_headers', [])}",
        f"- Visible row count: {result.get('visible_row_count', 0)}",
        f"- Footer rows filtered: {result.get('footer_filtered_count', 0)}",
        f"- Sample rows: {result.get('sample_rows', [])}",
        "",
        "## Screenshot list",
    ]
    for path in result.get("screenshots", []):
        lines.append(f"- {path}")

    lines.extend(["", "## OCR summary"])
    for item in ocr_summary or ["(none)"]:
        lines.append(f"- {item}")

    lines.extend(["", "## Extraction summary"])
    for path in result.get("extracted_files", []):
        lines.append(f"- {path}")

    lines.extend(
        [
            "",
            "## Final decision",
            result["status"],
            "",
            "## Next recommendation",
        ]
    )
    if result["status"] in ("PASS", "PASS_PARTIAL_SNAPSHOT"):
        lines.append("Ready for M07 hard testing.")
    elif result["status"].startswith("MANUAL_REVIEW"):
        lines.append("Review screenshots and popup evidence before retrying.")
    else:
        lines.append("Fix the reported issue and re-run TY_TEST_M07_READ_ACTIVITY_TABLE.bat")

    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_activities_workspace(
    evidence: RunEvidence,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    p6_keyword: str,
    min_confidence: float,
    capture: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], str, P6Rect, Dict[str, Any]]:
    in_activities, _ = confirms_activities_workspace(capture["entries"], min_confidence)
    state = capture["screen_state"]
    if state == "activities_workspace" and in_activities:
        return None, state, p6_rect, capture

    navigate_to_activities(evidence)
    fresh = get_fresh_p6_rect(p6_keyword)
    if fresh.get("success") and fresh.get("rect"):
        p6_rect = fresh["rect"]

    after = capture_and_ocr_step(evidence, "01b_after_activities_nav", p6_rect, config, screen_rule)
    if not after.get("ok"):
        return after, state, p6_rect, capture
    if after.get("unsafe"):
        return after, state, p6_rect, capture

    in_after, _ = confirms_activities_workspace(after["entries"], min_confidence)
    if in_after or after["screen_state"] == "activities_workspace":
        return None, after["screen_state"], p6_rect, after
    return after, state, p6_rect, capture


def run_m07(
    project_name: str,
    *,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    evidence = evidence or build_evidence(run_id or new_run_id())
    config = load_json(CONFIG_PATH)
    screen_rule = load_json(SCREEN_RULE_PATH)
    p6_keyword = config["p6_window_title_keyword"]
    min_confidence = float(config.get("min_ocr_confidence", 0.5))

    project_name = (project_name or "").strip()
    if not project_name:
        return finish_result(
            evidence, "", "FAIL_PROJECT_NAME_EMPTY", "project_name is empty"
        )

    evidence.steps.append("validate project_name")

    if not is_easyocr_available():
        return finish_result(
            evidence,
            project_name,
            "ERROR",
            "EasyOCR not installed",
            error="pip install easyocr",
        )

    try:
        for _ in range(2):
            try:
                keyboard_tools.press_escape()
            except Exception:  # noqa: BLE001
                pass

        evidence.steps.append("prepare_p6_for_test")
        prep = prepare_p6_for_test(p6_keyword)
        if not prep.get("success") or not prep.get("rect"):
            return finish_result(
                evidence,
                project_name,
                "FAIL_P6_WINDOW_NOT_READY",
                prep.get("message", "P6 window not ready"),
            )

        p6_rect: P6Rect = prep["rect"]
        window_title = window_tools.get_window_state(p6_keyword).get("title") or ""

        evidence.steps.append("capture activities_table")
        capture = capture_and_ocr_step(evidence, "01_snapshot", p6_rect, config, screen_rule)
        if not capture.get("ok"):
            polluted = capture.get("polluted")
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_P6_WINDOW_NOT_READY",
                capture.get("error", "capture failed"),
                window_title=window_title,
                screen_state="unknown",
                manual_review_required=bool(polluted),
            )

        screen_state = capture["screen_state"]
        if capture.get("unsafe"):
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_UNSAFE_POPUP",
                capture.get("unsafe_reason", "unsafe popup"),
                window_title=window_title,
                screen_state=screen_state,
                manual_review_required=True,
            )

        open_ok, open_reason, _ = confirm_project_open(
            capture["entries"], project_name, window_title, min_confidence
        )
        if not open_ok:
            return finish_result(
                evidence,
                project_name,
                "FAIL_PROJECT_NOT_OPEN",
                open_reason,
                window_title=window_title,
                screen_state=screen_state,
            )

        nav_issue, screen_state, p6_rect, working = ensure_activities_workspace(
            evidence, p6_rect, config, screen_rule, p6_keyword, min_confidence, capture
        )
        if nav_issue is not None:
            if nav_issue.get("unsafe"):
                return finish_result(
                    evidence,
                    project_name,
                    "MANUAL_REVIEW_UNSAFE_POPUP",
                    nav_issue.get("unsafe_reason", "unsafe popup during navigation"),
                    window_title=window_title,
                    screen_state=screen_state,
                    manual_review_required=True,
                )
            if not nav_issue.get("ok"):
                polluted = nav_issue.get("polluted")
                return finish_result(
                    evidence,
                    project_name,
                    "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_ACTIVITIES_NOT_FOUND",
                    nav_issue.get("error", "Activities navigation failed"),
                    window_title=window_title,
                    screen_state=screen_state,
                    manual_review_required=bool(polluted),
                )
            return finish_result(
                evidence,
                project_name,
                "FAIL_ACTIVITIES_NOT_FOUND",
                "Activities workspace could not be confirmed",
                window_title=window_title,
                screen_state=screen_state,
            )

        evidence.steps.append("extract activity table snapshot")
        extraction = detect_table_evidence(working["entries"], min_confidence)
        save_extractions(evidence, extraction)

        status, reason = decide_status(extraction, activities_ok=True)
        sample = extraction.get("activity_rows", [])[:5]

        return finish_result(
            evidence,
            project_name,
            status,
            reason,
            window_title=window_title,
            screen_state=screen_state,
            table_detected=extraction.get("table_detected", False),
            header_detected=extraction.get("header_detected", False),
            visible_row_count=extraction.get("visible_row_count", 0),
            detected_headers=extraction.get("detected_headers", []),
            footer_filtered_count=extraction.get("footer_filtered_count", 0),
            sample_rows=sample,
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
    parser = argparse.ArgumentParser(description="M07 Read Activity Table Snapshot")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    args = parser.parse_args()

    result = run_m07(args.project.strip())
    print(f"M07 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Window title: {result.get('window_title', '')}")
    print(f"Screen state: {result.get('screen_state', '')}")
    print(f"Table detected: {result.get('table_detected')}")
    print(f"Header detected: {result.get('header_detected')}")
    print(f"Visible row count: {result.get('visible_row_count', 0)}")
    print(f"Footer filtered: {result.get('footer_filtered_count', 0)}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")
    if result["status"] in ("PASS", "PASS_PARTIAL_SNAPSHOT"):
        return 0
    if result["status"].startswith("MANUAL_REVIEW"):
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
