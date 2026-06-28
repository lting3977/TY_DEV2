"""
M14 — Copy Visible Activity Rows Multi Select (Phase 13).

Read-only: shift-selects multiple visible P6 Activities rows and copies via Ctrl+C.
Builds on M13 single-row clipboard patterns.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
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
from eye.ocr import is_easyocr_available, normalize_text  # noqa: E402
from eye.screenshot import P6Rect  # noqa: E402
from hand.p6_prepare import prepare_p6_for_test  # noqa: E402
from m06_go_to_activities import (  # noqa: E402
    CONFIG_PATH,
    SCREEN_RULE_PATH,
    capture_and_ocr_step,
    confirm_project_open,
    confirms_activities_workspace,
    load_json,
    title_indicates_project_open,
    write_json,
)
from m07_read_activity_table_snapshot import (  # noqa: E402
    ACTIVITY_ID_PATTERN,
    DATE_PATTERN,
    FOOTER_Y_RATIO,
    TABLE_MIN_Y,
    bbox_center,
    detect_table_evidence,
    group_into_rows,
    is_footer_or_status_row,
    is_header_row,
    looks_like_activity_row,
    table_entries,
)
from m13_copy_visible_activity_table_to_clipboard_csv import (  # noqa: E402
    GridClickTarget,
    capture_with_retry,
    ensure_activities_workspace,
    force_foreground_hwnd,
    get_foreground_window_title,
    get_p6_hwnd,
    image_point_to_screen,
    is_p6_foreground,
    read_clipboard_text,
    restore_saved_clipboard,
    send_hotkey_win32,
    send_wm_copy_to_focus,
    write_clipboard_text,
)

MODULE_NAME = "m14_copy_visible_activity_rows_multi_select"
ACTIVITY_ID_CLIP = re.compile(r"\bA\d{3,5}[A-Za-z0-9]?\b", re.I)
HEADER_HINTS = ("activity", "activity name", "start", "finish", "wbs", "activity id")
MAX_ROWS_DEFAULT = 3
MAX_ROWS_MIN = 2
MAX_ROWS_MAX = 10

CLIPBOARD_POLLUTION_WORDS = (
    "chatgpt",
    "cursor",
    "composer",
    "copy paste",
    "m13",
    "m14",
    "evidence path",
    "ty_dev2",
    "sandbox",
    "user message",
    "hard testing summary",
    "openai",
    "copilot",
    "claude",
    "do not modify",
    "manual_review",
)


@dataclass
class RunEvidence:
    run_id: str
    folder: Path
    screenshots_dir: Path
    ocr_dir: Path
    classification_dir: Path
    popup_dir: Path
    clipboard_dir: Path
    steps: List[str] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    ocr_files: List[str] = field(default_factory=list)
    classification_files: List[str] = field(default_factory=list)
    popup_files: List[str] = field(default_factory=list)
    clipboard_files: List[str] = field(default_factory=list)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_evidence(run_id: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / run_id / MODULE_NAME
    for sub in ("screenshots", "ocr", "classification", "popup", "clipboard"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    return RunEvidence(
        run_id=run_id,
        folder=folder,
        screenshots_dir=folder / "screenshots",
        ocr_dir=folder / "ocr",
        classification_dir=folder / "classification",
        popup_dir=folder / "popup",
        clipboard_dir=folder / "clipboard",
    )


def clamp_max_rows(value: int) -> Tuple[int, bool]:
    original = value
    clamped = max(MAX_ROWS_MIN, min(MAX_ROWS_MAX, value))
    return clamped, clamped != original


def normalize_activity_id_text(text: str) -> Optional[str]:
    norm = normalize_text(text).replace(" ", "")
    if ACTIVITY_ID_CLIP.match(text.strip()):
        return text.strip().upper()
    if re.match(r"^a\d{3,5}[a-z0-9]?$", norm):
        return norm.upper()
    return None


def find_visible_activity_row_targets(
    entries: List[Dict[str, Any]],
    min_confidence: float,
    p6_rect: P6Rect,
) -> List[GridClickTarget]:
    p6_height = max(p6_rect.height, 800)
    footer_y = p6_height * FOOTER_Y_RATIO
    table_max_x = p6_rect.width * 0.55
    table_ents = table_entries(entries, min_confidence, p6_height)
    rows = group_into_rows(table_ents)

    header_max_y = float(TABLE_MIN_Y)
    for row in rows:
        is_hdr, _ = is_header_row(row)
        if is_hdr:
            header_max_y = max(header_max_y, max(bbox_center(e)[1] for e in row))
    body_min_y = header_max_y + 10

    candidates: List[GridClickTarget] = []
    seen_ids: set[str] = set()

    for row in rows:
        if is_footer_or_status_row(row, p6_height):
            continue
        if not looks_like_activity_row(row):
            continue

        id_entry = None
        activity_id = ""
        for entry in row:
            aid = normalize_activity_id_text(entry.get("text", ""))
            if aid:
                id_entry = entry
                activity_id = aid
                break
        if not id_entry:
            for entry in row:
                match = ACTIVITY_ID_CLIP.search(entry.get("text", ""))
                if match:
                    activity_id = match.group(0).upper()
                    id_entry = entry
                    break
        if not id_entry or not activity_id:
            continue
        if activity_id in seen_ids:
            continue

        cx, cy = bbox_center(id_entry)
        if cy < body_min_y or cy > footer_y or cx > table_max_x:
            continue

        click_x = min(cx + 8, table_max_x)
        seen_ids.add(activity_id)
        candidates.append(
            GridClickTarget(
                x=click_x,
                y=cy,
                method="activity_id_row_bbox",
                evidence=f"activity_id={activity_id}",
                activity_id=activity_id,
                row_text=" | ".join(e.get("text", "") for e in row),
            )
        )

    candidates.sort(key=lambda t: t.y)
    return candidates


def click_target(p6_rect: P6Rect, target: GridClickTarget) -> None:
    import pyautogui  # noqa: WPS433

    sx, sy = image_point_to_screen(p6_rect, target.x, target.y)
    pyautogui.click(sx, sy)
    time.sleep(0.45)


def shift_click_select(
    p6_rect: P6Rect,
    first: GridClickTarget,
    last: GridClickTarget,
    evidence: RunEvidence,
) -> None:
    import pyautogui  # noqa: WPS433

    sx1, sy1 = image_point_to_screen(p6_rect, first.x, first.y)
    sx2, sy2 = image_point_to_screen(p6_rect, last.x, last.y)
    pyautogui.click(sx1, sy1)
    time.sleep(0.4)
    pyautogui.keyDown("shift")
    time.sleep(0.15)
    pyautogui.click(sx2, sy2)
    pyautogui.keyUp("shift")
    time.sleep(0.5)
    evidence.steps.append(
        f"shift-select ({first.activity_id} at {first.x:.0f},{first.y:.0f}) "
        f"to ({last.activity_id} at {last.x:.0f},{last.y:.0f})"
    )


def refocus_p6_before_selection(
    p6_keyword: str,
    project_name: str,
    p6_rect: P6Rect,
    evidence: RunEvidence,
) -> Tuple[bool, str, P6Rect]:
    prep = prepare_p6_for_test(p6_keyword)
    if prep.get("success") and prep.get("rect"):
        p6_rect = prep["rect"]
    hwnd = get_p6_hwnd(p6_keyword)
    if hwnd:
        force_foreground_hwnd(hwnd)
    else:
        window_tools.activate_window_by_title(p6_keyword)
    time.sleep(0.4)
    fg = get_foreground_window_title()
    if is_p6_foreground(p6_keyword, project_name, fg):
        return True, fg, p6_rect
    window_tools.activate_window_by_title(p6_keyword)
    time.sleep(0.5)
    fg = get_foreground_window_title()
    if is_p6_foreground(p6_keyword, project_name, fg):
        return True, fg, p6_rect
    return False, fg, p6_rect


def trigger_ctrl_c(p6_keyword: str) -> None:
    hwnd = get_p6_hwnd(p6_keyword)
    if hwnd:
        force_foreground_hwnd(hwnd)
    time.sleep(0.35)
    send_hotkey_win32("ctrl", "c")
    time.sleep(0.35)
    keyboard_tools.hotkey("ctrl", "c")
    time.sleep(0.35)
    send_wm_copy_to_focus(p6_keyword)


def detect_clipboard_pollution(text: str) -> Tuple[bool, List[str]]:
    blob = normalize_text(text)
    hits = [w for w in CLIPBOARD_POLLUTION_WORDS if w in blob]
    if "we are working in" in blob and "ty_dev2" in blob:
        hits.append("cursor_chat_context")
    return bool(hits), sorted(set(hits))


def parse_clipboard_lines(text: str) -> List[List[str]]:
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
    rows: List[List[str]] = []
    for line in lines:
        if "\t" in line:
            rows.append([c.strip() for c in line.split("\t")])
        elif "," in line and line.count(",") >= 2:
            rows.append([c.strip() for c in line.split(",")])
        else:
            parts = [p.strip() for p in re.split(r"\s{2,}", line) if p.strip()]
            rows.append(parts if len(parts) > 1 else [line.strip()])
    return rows


def detect_headers(rows: List[List[str]]) -> List[str]:
    if not rows:
        return []
    first_blob = normalize_text(" ".join(rows[0]))
    return sorted({h for h in HEADER_HINTS if h in first_blob})


def activity_like_row_count(rows: List[List[str]]) -> int:
    count = 0
    for row in rows:
        blob = " ".join(row)
        norm = normalize_text(blob)
        if row == rows[0] and any(h in norm for h in HEADER_HINTS):
            continue
        if ACTIVITY_ID_CLIP.search(blob) or ACTIVITY_ID_PATTERN.match(norm.replace(" ", "")):
            count += 1
            continue
        if DATE_PATTERN.search(blob) or "new activity" in norm:
            count += 1
    return count


def validate_clipboard_table(text: str) -> Dict[str, Any]:
    rows = parse_clipboard_lines(text)
    line_count = len([ln for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()])
    headers = detect_headers(rows)
    activity_rows = activity_like_row_count(rows)
    col_guess = max((len(r) for r in rows), default=0)
    blob = normalize_text(text)
    has_tabs = "\t" in text
    has_columns = col_guess >= 2
    has_keywords = any(h in blob for h in HEADER_HINTS)
    has_activity_id = bool(ACTIVITY_ID_CLIP.search(text))
    polluted, pollution_words = detect_clipboard_pollution(text)

    table_like = (
        (has_tabs or has_columns)
        and (has_keywords or has_activity_id or DATE_PATTERN.search(text))
        and activity_rows >= 1
        and not polluted
    )

    return {
        "line_count": line_count,
        "parsed_row_count": len(rows),
        "column_guess": col_guess,
        "headers_detected": headers,
        "activity_like_row_count": activity_rows,
        "has_tabs": has_tabs,
        "table_like": table_like,
        "clipboard_pollution_detected": polluted,
        "clipboard_pollution_words": pollution_words,
        "rows": rows,
    }


def decide_m14_status(validation: Dict[str, Any], text: str, sentinel: str) -> Tuple[str, str]:
    if text.strip() == sentinel.strip() or not text.strip():
        return "FAIL_CLIPBOARD_EMPTY", "Ctrl+C did not place new table data on clipboard"
    if validation.get("clipboard_pollution_detected"):
        words = validation.get("clipboard_pollution_words", [])
        return "FAIL_CLIPBOARD_NOT_TABLE", f"Clipboard pollution detected: {words}"
    if not validation.get("table_like"):
        return "FAIL_CLIPBOARD_NOT_TABLE", "Copied clipboard text does not look like activity table data"

    activity_rows = int(validation.get("activity_like_row_count", 0))
    if activity_rows >= 2:
        return (
            "PASS",
            f"Multi-row clipboard captured with {activity_rows} activity-like row(s)",
        )
    if activity_rows == 1:
        return (
            "PASS_PARTIAL_CLIPBOARD",
            f"Clipboard table captured with 1 activity-like row(s); multi-select partial",
        )
    return "FAIL_CLIPBOARD_NOT_TABLE", "No activity-like rows detected in clipboard content"


def save_row_selection_targets(
    evidence: RunEvidence,
    all_targets: List[GridClickTarget],
    first: GridClickTarget,
    last: GridClickTarget,
    max_rows_used: int,
) -> str:
    path = evidence.clipboard_dir / "row_selection_targets.json"
    payload = {
        "visible_targets_count": len(all_targets),
        "max_rows_used": max_rows_used,
        "all_targets": [t.to_dict() for t in all_targets],
        "selected_first_target": first.to_dict(),
        "selected_last_target": last.to_dict(),
    }
    write_json(path, payload)
    evidence.clipboard_files.append(str(path))
    return str(path)


def save_clipboard_outputs(evidence: RunEvidence, text: str, validation: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    raw_path = evidence.clipboard_dir / "clipboard_raw.txt"
    raw_path.write_text(text, encoding="utf-8")
    paths.append(str(raw_path))

    json_path = evidence.clipboard_dir / "clipboard_table.json"
    write_json(
        json_path,
        {
            "line_count": validation.get("line_count", 0),
            "column_guess": validation.get("column_guess", 0),
            "headers_detected": validation.get("headers_detected", []),
            "activity_like_row_count": validation.get("activity_like_row_count", 0),
            "rows": validation.get("rows", []),
        },
    )
    paths.append(str(json_path))

    csv_path = evidence.clipboard_dir / "clipboard_table.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for row in validation.get("rows", []):
            writer.writerow(row)
    paths.append(str(csv_path))

    val_path = evidence.clipboard_dir / "clipboard_validation.json"
    val_payload = {k: v for k, v in validation.items() if k != "rows"}
    write_json(val_path, val_payload)
    paths.append(str(val_path))

    evidence.clipboard_files.extend(paths)
    return paths


def finish_result(
    evidence: RunEvidence,
    project_name: str,
    status: str,
    reason: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "run_id": evidence.run_id,
        "module": MODULE_NAME,
        "project_name": project_name,
        "status": status,
        "reason": reason,
        "max_rows_requested": MAX_ROWS_DEFAULT,
        "max_rows_used": MAX_ROWS_DEFAULT,
        "table_detected": False,
        "visible_activity_targets_count": 0,
        "selected_first_target": {},
        "selected_last_target": {},
        "selection_method_used": "",
        "copy_method_used": "",
        "foreground_before_selection": "",
        "foreground_after_selection": "",
        "p6_foreground_confirmed_before_copy": False,
        "clipboard_sentinel_used": False,
        "clipboard_changed_from_sentinel": False,
        "clipboard_pollution_detected": False,
        "clipboard_pollution_words": [],
        "clipboard_restored": False,
        "clipboard_restore_reason": "",
        "clipboard_line_count": 0,
        "clipboard_column_guess": 0,
        "activity_like_row_count": 0,
        "headers_detected": [],
        "clipboard_files": evidence.clipboard_files,
        "screenshots": evidence.screenshots,
        "ocr_files": evidence.ocr_files,
        "classification_files": evidence.classification_files,
        "popup_files": evidence.popup_files,
        "manual_review_required": False,
        "error": None,
        "steps": evidence.steps,
    }
    result.update(kwargs)
    write_json(evidence.folder / "result.json", result)
    write_report(evidence, result)
    return result


def write_report(evidence: RunEvidence, result: Dict[str, Any]) -> None:
    val_path = evidence.clipboard_dir / "clipboard_validation.json"
    validation_summary = ""
    if val_path.exists():
        validation_summary = json.dumps(load_json(val_path), indent=2)

    targets_path = evidence.clipboard_dir / "row_selection_targets.json"
    targets_summary = ""
    if targets_path.exists():
        targets_summary = json.dumps(load_json(targets_path), indent=2)

    ocr_summary: List[str] = []
    for path in result.get("ocr_files", []):
        try:
            data = load_json(Path(path))
            texts = [e.get("text", "") for e in data.get("entries", [])[:12]]
            ocr_summary.append(f"{path}: {', '.join(texts)}")
        except Exception:  # noqa: BLE001
            ocr_summary.append(path)

    lines = [
        "# M14 Copy Visible Activity Rows Multi Select Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project name: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Window title: {result.get('window_title', '')}",
        f"- Screen state: {result.get('screen_state', '')}",
        f"- Max rows requested: {result.get('max_rows_requested', 3)}",
        f"- Max rows used: {result.get('max_rows_used', 3)}",
        f"- Table detected: {result.get('table_detected')}",
        f"- Visible activity target count: {result.get('visible_activity_targets_count', 0)}",
        f"- Selected first target: {result.get('selected_first_target', {})}",
        f"- Selected last target: {result.get('selected_last_target', {})}",
        f"- Selection method used: {result.get('selection_method_used', '')}",
        f"- Copy method used: {result.get('copy_method_used', '')}",
        f"- Foreground before selection: {result.get('foreground_before_selection', '')}",
        f"- Foreground after selection: {result.get('foreground_after_selection', '')}",
        f"- P6 foreground confirmed before copy: {result.get('p6_foreground_confirmed_before_copy')}",
        f"- Clipboard sentinel used: {result.get('clipboard_sentinel_used')}",
        f"- Clipboard changed from sentinel: {result.get('clipboard_changed_from_sentinel')}",
        f"- Clipboard pollution detected: {result.get('clipboard_pollution_detected')}",
        f"- Clipboard pollution words: {result.get('clipboard_pollution_words', [])}",
        f"- Clipboard restored: {result.get('clipboard_restored')}",
        f"- Clipboard restore reason: {result.get('clipboard_restore_reason', '')}",
        f"- Clipboard line count: {result.get('clipboard_line_count', 0)}",
        f"- Clipboard column guess: {result.get('clipboard_column_guess', 0)}",
        f"- Activity-like row count: {result.get('activity_like_row_count', 0)}",
        f"- Headers detected: {result.get('headers_detected', [])}",
        f"- Clipboard files: {result.get('clipboard_files', [])}",
        "",
        "## Row selection target summary",
        targets_summary or "(none)",
        "",
        "## Screenshot list",
    ]
    for path in result.get("screenshots", []):
        lines.append(f"- {path}")
    lines.extend(["", "## OCR summary"])
    for item in ocr_summary or ["(none)"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Validation summary", validation_summary or "(none)"])
    lines.extend(["", "## Final decision", result["status"], "", "## Next recommendation"])
    if result["status"] in ("PASS", "PASS_PARTIAL_CLIPBOARD"):
        lines.append("Ready for M14 hard testing.")
    elif result["status"].startswith("MANUAL_REVIEW"):
        lines.append("Review screenshots and popup evidence before retrying.")
    else:
        lines.append("Fix the reported issue and re-run TY_TEST_M14_COPY_MULTI_ROWS.bat")
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _base_kwargs(**kwargs: Any) -> Dict[str, Any]:
    defaults = {
        "window_title": "",
        "screen_state": "",
        "table_detected": False,
        "clipboard_readable_before": False,
        "clipboard_had_text_before": False,
        "clipboard_copied": False,
    }
    defaults.update(kwargs)
    return defaults


def run_m14(
    project_name: str,
    *,
    max_rows: int = MAX_ROWS_DEFAULT,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
    block_activities_navigation: bool = False,
    force_insufficient_row_targets: bool = False,
) -> Dict[str, Any]:
    evidence = evidence or build_evidence(run_id or new_run_id())
    config = load_json(CONFIG_PATH)
    screen_rule = load_json(SCREEN_RULE_PATH)
    p6_keyword = config["p6_window_title_keyword"]
    min_confidence = float(config.get("min_ocr_confidence", 0.5))
    sentinel = f"TY_CLIPBOARD_SENTINEL_{evidence.run_id}"

    project_name = (project_name or "").strip()
    if not project_name:
        return finish_result(evidence, "", "FAIL_PROJECT_NAME_EMPTY", "project_name is empty")

    max_rows_requested = max_rows
    max_rows_used, was_clamped = clamp_max_rows(max_rows)
    if was_clamped:
        evidence.steps.append(f"max_rows clamped from {max_rows_requested} to {max_rows_used}")

    if not is_easyocr_available():
        return finish_result(
            evidence,
            project_name,
            "ERROR",
            "EasyOCR not installed",
            max_rows_requested=max_rows_requested,
            max_rows_used=max_rows_used,
            error="pip install easyocr",
        )

    saved_clipboard = ""
    clipboard_had_text_before = False

    try:
        for _ in range(2):
            try:
                keyboard_tools.press_escape()
            except Exception:  # noqa: BLE001
                pass

        _, before_text, _ = read_clipboard_text()
        clipboard_had_text_before = bool(before_text.strip())
        saved_clipboard = before_text

        evidence.steps.append("prepare_p6_for_test (initial)")
        prep = prepare_p6_for_test(p6_keyword)
        if not prep.get("success") or not prep.get("rect"):
            restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
            return finish_result(
                evidence,
                project_name,
                "FAIL_P6_WINDOW_NOT_READY",
                prep.get("message", "P6 window not ready"),
                max_rows_requested=max_rows_requested,
                max_rows_used=max_rows_used,
                clipboard_restored=restored,
                clipboard_restore_reason=restore_reason,
            )

        p6_rect: P6Rect = prep["rect"]
        window_title = window_tools.get_window_state(p6_keyword).get("title") or ""

        capture, p6_rect = capture_with_retry(
            evidence, "01_before_select", p6_rect, config, screen_rule, p6_keyword
        )
        if not capture.get("ok"):
            restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM" if capture.get("polluted") else "FAIL_P6_WINDOW_NOT_READY",
                capture.get("error", "capture failed"),
                max_rows_requested=max_rows_requested,
                max_rows_used=max_rows_used,
                window_title=window_title,
                clipboard_restored=restored,
                clipboard_restore_reason=restore_reason,
                manual_review_required=bool(capture.get("polluted")),
            )

        screen_state = capture["screen_state"]
        if capture.get("unsafe"):
            restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_UNSAFE_POPUP",
                capture.get("unsafe_reason", "unsafe popup"),
                max_rows_requested=max_rows_requested,
                max_rows_used=max_rows_used,
                window_title=window_title,
                screen_state=screen_state,
                clipboard_restored=restored,
                clipboard_restore_reason=restore_reason,
                manual_review_required=True,
            )

        if not title_indicates_project_open(window_title, project_name):
            open_ok, open_reason, _ = confirm_project_open(
                capture["entries"], project_name, window_title, min_confidence
            )
            if not open_ok:
                restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
                return finish_result(
                    evidence,
                    project_name,
                    "FAIL_PROJECT_NOT_OPEN",
                    open_reason,
                    max_rows_requested=max_rows_requested,
                    max_rows_used=max_rows_used,
                    window_title=window_title,
                    screen_state=screen_state,
                    clipboard_restored=restored,
                    clipboard_restore_reason=restore_reason,
                )

        if block_activities_navigation:
            evidence.steps.append("block_activities_navigation: skip M06-style navigation")
            in_blocked, _ = confirms_activities_workspace(capture["entries"], min_confidence)
            screen_state = capture["screen_state"]
            if not in_blocked and screen_state != "activities_workspace":
                restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
                return finish_result(
                    evidence,
                    project_name,
                    "FAIL_ACTIVITIES_NOT_FOUND",
                    "Activities workspace not confirmed; navigation blocked for hard test",
                    max_rows_requested=max_rows_requested,
                    max_rows_used=max_rows_used,
                    window_title=window_title,
                    screen_state=screen_state,
                    clipboard_restored=restored,
                    clipboard_restore_reason=restore_reason,
                )
            working = capture
        else:
            nav_issue, screen_state, p6_rect, working = ensure_activities_workspace(
                evidence, p6_rect, config, screen_rule, p6_keyword, min_confidence, capture
            )
            if nav_issue is not None:
                status = "MANUAL_REVIEW_UNSAFE_POPUP" if nav_issue.get("unsafe") else "FAIL_ACTIVITIES_NOT_FOUND"
                restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
                return finish_result(
                    evidence,
                    project_name,
                    status,
                    nav_issue.get("unsafe_reason") or nav_issue.get("error", "Activities not confirmed"),
                    max_rows_requested=max_rows_requested,
                    max_rows_used=max_rows_used,
                    window_title=window_title,
                    screen_state=screen_state,
                    clipboard_restored=restored,
                    clipboard_restore_reason=restore_reason,
                    manual_review_required=nav_issue.get("unsafe", False),
                )

        extraction = detect_table_evidence(working["entries"], min_confidence)
        table_detected = bool(extraction.get("table_detected"))
        if not table_detected:
            restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
            return finish_result(
                evidence,
                project_name,
                "FAIL_TABLE_NOT_DETECTED",
                "Activities workspace confirmed but visible activity table evidence not found",
                max_rows_requested=max_rows_requested,
                max_rows_used=max_rows_used,
                window_title=window_title,
                screen_state=screen_state,
                table_detected=False,
                clipboard_restored=restored,
                clipboard_restore_reason=restore_reason,
            )

        all_targets = find_visible_activity_row_targets(working["entries"], min_confidence, p6_rect)
        if force_insufficient_row_targets:
            evidence.steps.append("force_insufficient_row_targets: hard test mode")
            all_targets = all_targets[:1]
        if len(all_targets) < 2:
            restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
            return finish_result(
                evidence,
                project_name,
                "FAIL_NOT_ENOUGH_VISIBLE_ROWS",
                f"Only {len(all_targets)} visible activity row target(s) identified; need at least 2",
                max_rows_requested=max_rows_requested,
                max_rows_used=max_rows_used,
                window_title=window_title,
                screen_state=screen_state,
                table_detected=table_detected,
                visible_activity_targets_count=len(all_targets),
                clipboard_restored=restored,
                clipboard_restore_reason=restore_reason,
            )

        last_index = min(max_rows_used, len(all_targets)) - 1
        first_target = all_targets[0]
        last_target = all_targets[last_index]
        rows_to_span = last_index + 1
        evidence.steps.append(
            f"selection span: {rows_to_span} rows ({first_target.activity_id} .. {last_target.activity_id})"
        )
        save_row_selection_targets(evidence, all_targets, first_target, last_target, rows_to_span)

        fg_ok, fg_before, p6_rect = refocus_p6_before_selection(
            p6_keyword, project_name, p6_rect, evidence
        )
        if not fg_ok:
            restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM",
                "Cannot confirm P6 is foreground before multi-row selection",
                max_rows_requested=max_rows_requested,
                max_rows_used=rows_to_span,
                window_title=window_title,
                screen_state=screen_state,
                table_detected=table_detected,
                visible_activity_targets_count=len(all_targets),
                selected_first_target=first_target.to_dict(),
                selected_last_target=last_target.to_dict(),
                foreground_before_selection=fg_before,
                p6_foreground_confirmed_before_copy=False,
                clipboard_restored=restored,
                clipboard_restore_reason=restore_reason,
                manual_review_required=True,
            )

        write_clipboard_text(sentinel)
        time.sleep(0.25)

        selection_method = "method_a_shift_click"
        copy_method = ""
        copied_text = ""
        changed_from_sentinel = False
        validation: Dict[str, Any] = {}
        final_status = "FAIL_CLIPBOARD_EMPTY"
        final_reason = ""

        hwnd = get_p6_hwnd(p6_keyword)
        if hwnd:
            force_foreground_hwnd(hwnd)

        shift_click_select(p6_rect, first_target, last_target, evidence)
        fg_after = get_foreground_window_title()
        if not is_p6_foreground(p6_keyword, project_name, fg_after):
            restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM",
                "P6 not foreground after shift-select",
                max_rows_requested=max_rows_requested,
                max_rows_used=rows_to_span,
                window_title=window_title,
                screen_state=screen_state,
                table_detected=table_detected,
                visible_activity_targets_count=len(all_targets),
                selected_first_target=first_target.to_dict(),
                selected_last_target=last_target.to_dict(),
                selection_method_used=selection_method,
                foreground_before_selection=fg_before,
                foreground_after_selection=fg_after,
                p6_foreground_confirmed_before_copy=True,
                clipboard_sentinel_used=True,
                clipboard_restored=restored,
                clipboard_restore_reason=restore_reason,
                manual_review_required=True,
            )

        trigger_ctrl_c(p6_keyword)
        time.sleep(0.9)
        _, copied_text, _ = read_clipboard_text()
        changed_from_sentinel = copied_text.strip() != sentinel.strip()
        copy_method = "method_a_shift_click_ctrl_c"
        evidence.steps.append(
            f"{copy_method}: sentinel_changed={changed_from_sentinel} len={len(copied_text.strip())}"
        )
        validation = validate_clipboard_table(copied_text)
        final_status, final_reason = decide_m14_status(validation, copied_text, sentinel)

        if final_status in ("FAIL_CLIPBOARD_EMPTY", "FAIL_CLIPBOARD_NOT_TABLE") and not validation.get(
            "table_like"
        ):
            evidence.steps.append("method_b fallback: single-row click + Ctrl+C")
            write_clipboard_text(sentinel)
            time.sleep(0.2)
            if hwnd:
                force_foreground_hwnd(hwnd)
            click_target(p6_rect, first_target)
            time.sleep(0.4)
            trigger_ctrl_c(p6_keyword)
            time.sleep(0.9)
            _, copied_text, _ = read_clipboard_text()
            changed_from_sentinel = copied_text.strip() != sentinel.strip()
            copy_method = "method_b_single_row_ctrl_c"
            selection_method = "method_b_single_row"
            validation = validate_clipboard_table(copied_text)
            final_status, final_reason = decide_m14_status(validation, copied_text, sentinel)
            evidence.steps.append(
                f"{copy_method}: sentinel_changed={changed_from_sentinel} len={len(copied_text.strip())}"
            )

        if copied_text.strip() and copied_text.strip() != sentinel.strip():
            save_clipboard_outputs(evidence, copied_text, validation)

        restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)

        return finish_result(
            evidence,
            project_name,
            final_status,
            final_reason,
            window_title=window_title,
            screen_state=screen_state,
            max_rows_requested=max_rows_requested,
            max_rows_used=rows_to_span,
            table_detected=table_detected,
            visible_activity_targets_count=len(all_targets),
            selected_first_target=first_target.to_dict(),
            selected_last_target=last_target.to_dict(),
            selection_method_used=selection_method,
            copy_method_used=copy_method,
            foreground_before_selection=fg_before,
            foreground_after_selection=fg_after,
            p6_foreground_confirmed_before_copy=True,
            clipboard_sentinel_used=True,
            clipboard_changed_from_sentinel=changed_from_sentinel,
            clipboard_pollution_detected=validation.get("clipboard_pollution_detected", False),
            clipboard_pollution_words=validation.get("clipboard_pollution_words", []),
            clipboard_restored=restored,
            clipboard_restore_reason=restore_reason,
            clipboard_line_count=int(validation.get("line_count", 0)),
            clipboard_column_guess=int(validation.get("column_guess", 0)),
            activity_like_row_count=int(validation.get("activity_like_row_count", 0)),
            headers_detected=validation.get("headers_detected", []),
            manual_review_required=final_status.startswith("MANUAL_REVIEW"),
        )

    except Exception as exc:  # noqa: BLE001
        evidence.steps.append(traceback.format_exc())
        if saved_clipboard:
            write_clipboard_text(saved_clipboard)
        return finish_result(
            evidence,
            project_name,
            "ERROR",
            str(exc),
            max_rows_requested=max_rows_requested,
            max_rows_used=max_rows_used,
            error=traceback.format_exc(),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="M14 Copy Visible Activity Rows Multi Select")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    parser.add_argument(
        "--max-rows",
        type=int,
        default=MAX_ROWS_DEFAULT,
        help="Maximum visible rows to span (2-10, default 3)",
    )
    args = parser.parse_args()
    result = run_m14(args.project.strip(), max_rows=args.max_rows)
    print(f"M14 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Visible targets: {result.get('visible_activity_targets_count', 0)}")
    print(f"Activity-like rows: {result.get('activity_like_row_count', 0)}")
    print(f"Selection: {result.get('selection_method_used', '')}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")
    if result["status"] in ("PASS", "PASS_PARTIAL_CLIPBOARD"):
        return 0
    if result["status"].startswith("MANUAL_REVIEW"):
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
