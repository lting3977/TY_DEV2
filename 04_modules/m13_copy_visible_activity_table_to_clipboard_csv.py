"""
M13 — Copy Visible Activity Table To Clipboard CSV (Phase 12).

Read-only: copies visible P6 Activities table via Ctrl+C and saves clipboard
as CSV/text for comparison with M07/M08 OCR output.
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
from eye.ocr import collect_text_blob, is_easyocr_available, normalize_text  # noqa: E402
from eye.screenshot import P6Rect  # noqa: E402
from hand.p6_prepare import prepare_p6_for_test  # noqa: E402
from m06_go_to_activities import (  # noqa: E402
    CONFIG_PATH,
    SCREEN_RULE_PATH,
    capture_and_ocr_step,
    confirm_project_open,
    confirms_activities_workspace,
    load_json,
    navigate_to_activities,
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

MODULE_NAME = "m13_copy_visible_activity_table_to_clipboard_csv"
M08_MODULE_NAME = "m08_read_activity_table_structured"
ACTIVITY_ID_CLIP = re.compile(r"\bA\d{3,5}[A-Za-z0-9]?\b", re.I)
HEADER_HINTS = ("activity", "activity name", "start", "finish", "wbs", "activity id")

CLIPBOARD_POLLUTION_WORDS = (
    "chatgpt",
    "cursor",
    "composer",
    "copy paste",
    "m13 delivered",
    "p6 capture diagnostic summary",
    "evidence path",
    "ty_dev2",
    "sandbox",
    "user message",
    "openai",
    "copilot",
    "claude",
    "do not modify",
    "grid focus fix",
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


@dataclass
class GridClickTarget:
    x: float
    y: float
    method: str
    evidence: str
    activity_id: str = ""
    row_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "method": self.method,
            "evidence": self.evidence,
            "activity_id": self.activity_id,
            "row_text": self.row_text,
        }


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


def image_point_to_screen(p6_rect: P6Rect, x: float, y: float) -> Tuple[int, int]:
    return int(p6_rect.left + x), int(p6_rect.top + y)


def get_foreground_window_title() -> str:
    try:
        import pygetwindow as gw  # noqa: WPS433

        active = gw.getActiveWindow()
        return (active.title or "") if active else ""
    except Exception:  # noqa: BLE001
        pass
    try:
        import win32gui  # noqa: WPS433

        hwnd = win32gui.GetForegroundWindow()
        return win32gui.GetWindowText(hwnd) or ""
    except Exception:  # noqa: BLE001
        pass
    return window_tools.get_active_window_title() or ""


def is_p6_foreground(p6_keyword: str, project_name: str, title: Optional[str] = None) -> bool:
    fg = (title or get_foreground_window_title()).lower()
    if p6_keyword.lower() not in fg and "primavera" not in fg:
        return False
    if project_name and not title_indicates_project_open(title or fg, project_name):
        if normalize_text(project_name) not in fg:
            return False
    return True


def ensure_p6_foreground(
    p6_keyword: str,
    project_name: str,
    evidence: RunEvidence,
) -> Tuple[bool, str, bool]:
    fg = get_foreground_window_title()
    if is_p6_foreground(p6_keyword, project_name, fg):
        return True, fg, True

    evidence.steps.append(f"foreground not P6 ({fg!r}); activating P6")
    activate = window_tools.activate_window_by_title(p6_keyword)
    time.sleep(0.7)
    fg = get_foreground_window_title()
    if is_p6_foreground(p6_keyword, project_name, fg):
        return True, fg, False

    evidence.steps.append("foreground still not P6 after activate; prepare_p6_for_test")
    prep = prepare_p6_for_test(p6_keyword)
    time.sleep(0.5)
    fg = get_foreground_window_title()
    ok = is_p6_foreground(p6_keyword, project_name, fg) and prep.get("success", False)
    return ok, fg, False


def get_p6_hwnd(p6_keyword: str) -> Optional[int]:
    try:
        import win32gui  # noqa: WPS433

        matches: List[Tuple[int, str]] = []

        def _enum(hwnd: int, _ctx: Any) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd) or ""
            if p6_keyword.lower() in title.lower() or "primavera" in title.lower():
                matches.append((hwnd, title))
            return True

        win32gui.EnumWindows(_enum, None)
        if not matches:
            return None
        return matches[0][0]
    except Exception:  # noqa: BLE001
        return None


def force_foreground_hwnd(hwnd: int) -> bool:
    try:
        import win32con  # noqa: WPS433
        import win32gui  # noqa: WPS433
        import win32process  # noqa: WPS433

        if not hwnd:
            return False
        foreground = win32gui.GetForegroundWindow()
        fg_thread = win32process.GetWindowThreadProcessId(foreground)[0]
        target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
        attached = False
        if fg_thread != target_thread:
            win32process.AttachThreadInput(fg_thread, target_thread, True)
            attached = True
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)
        finally:
            if attached:
                win32process.AttachThreadInput(fg_thread, target_thread, False)
        time.sleep(0.35)
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:  # noqa: BLE001
        return False


def send_hotkey_win32(*keys: str) -> None:
    try:
        import win32api  # noqa: WPS433
        import win32con  # noqa: WPS433

        vk_map = {
            "ctrl": win32con.VK_CONTROL,
            "control": win32con.VK_CONTROL,
            "a": ord("A"),
            "c": ord("C"),
        }
        vks = [vk_map[k.lower()] for k in keys]
        for vk in vks:
            win32api.keybd_event(vk, 0, 0, 0)
            time.sleep(0.05)
        for vk in reversed(vks):
            win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.05)
        return
    except Exception:  # noqa: BLE001
        pass
    keyboard_tools.hotkey(*keys)


def read_clipboard_text() -> Tuple[bool, str, str]:
    formats: List[str] = []
    try:
        import win32clipboard  # noqa: WPS433

        win32clipboard.OpenClipboard()
        try:
            fmt = 0
            while True:
                fmt = win32clipboard.EnumClipboardFormats(fmt)
                if fmt == 0:
                    break
                try:
                    formats.append(win32clipboard.GetClipboardFormatName(fmt) or str(fmt))
                except Exception:  # noqa: BLE001
                    formats.append(str(fmt))
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                return True, data or "", ""
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_TEXT):
                data = win32clipboard.GetClipboardData(win32clipboard.CF_TEXT)
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")
                return True, data or "", ""
            if formats:
                return True, "", f"clipboard has formats {formats} but no text"
            return True, "", "no unicode text on clipboard"
        finally:
            win32clipboard.CloseClipboard()
    except Exception:  # noqa: BLE001
        pass
    try:
        import tkinter as tk  # noqa: WPS433

        root = tk.Tk()
        root.withdraw()
        try:
            text = root.clipboard_get()
            root.destroy()
            return True, text or "", ""
        except tk.TclError as exc:
            root.destroy()
            return True, "", str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, "", str(exc)


def write_clipboard_text(text: str) -> Tuple[bool, str]:
    try:
        import win32clipboard  # noqa: WPS433

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
            return True, ""
        finally:
            win32clipboard.CloseClipboard()
    except Exception:  # noqa: BLE001
        pass
    try:
        import tkinter as tk  # noqa: WPS433

        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        root.destroy()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def normalize_activity_id_text(text: str) -> Optional[str]:
    norm = normalize_text(text).replace(" ", "")
    if ACTIVITY_ID_CLIP.match(text.strip()):
        return text.strip().upper()
    if re.match(r"^a\d{3,5}[a-z0-9]?$", norm):
        return norm.upper()
    return None


def find_activity_row_click_target(
    entries: List[Dict[str, Any]],
    min_confidence: float,
    p6_rect: P6Rect,
) -> Optional[GridClickTarget]:
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

    best: Optional[GridClickTarget] = None
    id_row_candidates: List[GridClickTarget] = []
    for row in rows:
        if is_footer_or_status_row(row, p6_height):
            continue
        if not looks_like_activity_row(row):
            continue

        id_entry = None
        for entry in row:
            aid = normalize_activity_id_text(entry.get("text", ""))
            if aid:
                id_entry = entry
                activity_id = aid
                break
        else:
            activity_id = ""
            for entry in row:
                if ACTIVITY_ID_CLIP.search(entry.get("text", "")):
                    activity_id = ACTIVITY_ID_CLIP.search(entry.get("text", "")).group(0).upper()
                    id_entry = entry
                    break

        if id_entry:
            cx, cy = bbox_center(id_entry)
            click_x = min(cx + 8, table_max_x)
            if cy < body_min_y or cy > footer_y or cx > table_max_x:
                continue
            id_row_candidates.append(
                GridClickTarget(
                    x=click_x,
                    y=cy,
                    method="activity_id_row_bbox",
                    evidence=f"activity_id={activity_id} row={row[0].get('text', '')[:40]}",
                    activity_id=activity_id,
                    row_text=" | ".join(e.get("text", "") for e in row),
                )
            )
            continue

        xs = [bbox_center(e)[0] for e in row]
        ys = [bbox_center(e)[1] for e in row]
        cy = sum(ys) / len(ys)
        cx = sum(xs) / len(xs)
        if cy < body_min_y or cy > footer_y or cx > table_max_x:
            continue
        candidate = GridClickTarget(
            x=cx,
            y=cy,
            method="activity_row_bbox",
            evidence=f"row={row[0].get('text', '')[:60]}",
            row_text=" | ".join(e.get("text", "") for e in row),
        )
        if best is None:
            best = candidate

    if id_row_candidates:
        id_row_candidates.sort(key=lambda t: t.y)
        return id_row_candidates[0]

    if best:
        return best

    header_idx: Optional[int] = None
    for idx, row in enumerate(rows):
        is_hdr, _ = is_header_row(row)
        if is_hdr:
            header_idx = idx
            break

    body_rows = []
    for idx, row in enumerate(rows):
        if header_idx is not None and idx <= header_idx:
            continue
        if is_footer_or_status_row(row, p6_height):
            continue
        if looks_like_activity_row(row):
            body_rows.append(row)

    if header_idx is not None and body_rows:
        row = body_rows[0]
        xs = [bbox_center(e)[0] for e in row]
        ys = [bbox_center(e)[1] for e in row]
        cy = sum(ys) / len(ys)
        if TABLE_MIN_Y <= cy <= footer_y:
            return GridClickTarget(
                x=sum(xs) / len(xs),
                y=cy,
                method="header_plus_row_body",
                evidence="derived from header row and first body row",
                row_text=" | ".join(e.get("text", "") for e in row),
            )

    return None


def click_grid_point(p6_rect: P6Rect, target: GridClickTarget) -> None:
    import pyautogui  # noqa: WPS433

    sx, sy = image_point_to_screen(p6_rect, target.x, target.y)
    pyautogui.click(sx, sy)
    time.sleep(0.5)


def detect_clipboard_pollution(text: str) -> Tuple[bool, List[str]]:
    blob = normalize_text(text)
    hits = [w for w in CLIPBOARD_POLLUTION_WORDS if w in blob]
    if "we are working in" in blob and "ty_dev2" in blob:
        hits.append("cursor_chat_context")
    if blob.count("#") >= 3 and "summary" in blob:
        hits.append("markdown_summary")
    if len(blob) > 500 and not ("\t" in text or ACTIVITY_ID_CLIP.search(text)):
        hits.append("long_prose_without_table")
    return bool(hits), sorted(set(hits))


def is_prose_markdown(text: str) -> bool:
    blob = normalize_text(text)
    if text.count("\n") > 20 and not ("\t" in text):
        if any(marker in blob for marker in ("## ", "**", "do not modify", "required fix")):
            return True
    return False


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
        if DATE_PATTERN.search(blob):
            count += 1
            continue
        if "new activity" in norm:
            count += 1
    return count


def guess_column_count(rows: List[List[str]]) -> int:
    return max((len(r) for r in rows), default=0)


def validate_clipboard_table(text: str) -> Dict[str, Any]:
    rows = parse_clipboard_lines(text)
    line_count = len([ln for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()])
    headers = detect_headers(rows)
    activity_rows = activity_like_row_count(rows)
    col_guess = guess_column_count(rows)
    blob = normalize_text(text)

    has_tabs = "\t" in text
    has_columns = col_guess >= 2
    has_keywords = any(h in blob for h in HEADER_HINTS)
    has_activity_id = bool(ACTIVITY_ID_CLIP.search(text))
    has_dates = bool(DATE_PATTERN.search(text))
    polluted, pollution_words = detect_clipboard_pollution(text)
    prose = is_prose_markdown(text)

    table_like = (
        (has_tabs or has_columns)
        and (has_keywords or has_activity_id or has_dates)
        and activity_rows >= 1
        and not polluted
        and not prose
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
        "prose_markdown_detected": prose,
        "rows": rows,
    }


def evaluate_clipboard_copy(
    text: str,
    sentinel: str,
) -> Tuple[str, str, Dict[str, Any]]:
    if text.strip() == sentinel.strip():
        return "FAIL_CLIPBOARD_EMPTY", "Ctrl+C did not place new table data on clipboard", {}

    validation = validate_clipboard_table(text)
    if validation.get("clipboard_pollution_detected"):
        words = validation.get("clipboard_pollution_words", [])
        return (
            "FAIL_CLIPBOARD_NOT_TABLE",
            f"Clipboard content appears to come from non-P6 window: {words}",
            validation,
        )
    if validation.get("prose_markdown_detected"):
        return (
            "FAIL_CLIPBOARD_NOT_TABLE",
            "Clipboard contains long prose/Markdown, not activity table data",
            validation,
        )
    if not text.strip():
        return "FAIL_CLIPBOARD_EMPTY", "Clipboard read succeeded but copied content is empty", validation
    if not validation.get("table_like"):
        return (
            "FAIL_CLIPBOARD_NOT_TABLE",
            "Copied clipboard text does not look like activity table data",
            validation,
        )
    if validation.get("activity_like_row_count", 0) < 1:
        return (
            "FAIL_CLIPBOARD_NOT_TABLE",
            "No activity-like rows detected in clipboard content",
            validation,
        )
    return "OK", "", validation


def decide_pass_status(table_detected: bool, validation: Dict[str, Any]) -> Tuple[str, str]:
    activity_rows = int(validation.get("activity_like_row_count", 0))
    headers = validation.get("headers_detected", [])
    line_count = int(validation.get("line_count", 0))
    partial = not headers or line_count <= 1 or activity_rows == 1 or not table_detected
    if partial:
        return (
            "PASS_PARTIAL_CLIPBOARD",
            f"Clipboard table captured with {activity_rows} activity-like row(s); headers or row count partial",
        )
    return (
        "PASS",
        f"Clipboard table captured with {activity_rows} activity-like row(s) and detected headers",
    )


def refocus_p6_grid_for_copy(
    p6_keyword: str,
    project_name: str,
    p6_rect: P6Rect,
    target: GridClickTarget,
    evidence: RunEvidence,
) -> Tuple[bool, str, P6Rect]:
    hwnd = get_p6_hwnd(p6_keyword)
    if hwnd:
        force_foreground_hwnd(hwnd)
    else:
        window_tools.activate_window_by_title(p6_keyword)
    time.sleep(0.35)
    fg = get_foreground_window_title()
    if not is_p6_foreground(p6_keyword, project_name, fg):
        prep = prepare_p6_for_test(p6_keyword)
        time.sleep(0.4)
        hwnd = get_p6_hwnd(p6_keyword)
        if hwnd:
            force_foreground_hwnd(hwnd)
        fg = get_foreground_window_title()
        if not is_p6_foreground(p6_keyword, project_name, fg):
            return False, fg, p6_rect
        if prep.get("rect"):
            p6_rect = prep["rect"]
    click_grid_point(p6_rect, target)
    if hwnd:
        force_foreground_hwnd(hwnd)
    evidence.steps.append(f"refocus grid click at ({target.x:.0f},{target.y:.0f}) fg={fg[:50]}")
    return True, fg, p6_rect


def send_wm_copy_to_focus(p6_keyword: str) -> bool:
    try:
        import win32con  # noqa: WPS433
        import win32gui  # noqa: WPS433

        hwnd = get_p6_hwnd(p6_keyword)
        if hwnd:
            force_foreground_hwnd(hwnd)
        focus = win32gui.GetFocus()
        targets = [h for h in (focus, hwnd) if h]
        for target in targets:
            try:
                win32gui.SendMessage(target, win32con.WM_COPY, 0, 0)
                return True
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return False


def trigger_copy_shortcuts(p6_keyword: str, use_select_all: bool) -> None:
    hwnd = get_p6_hwnd(p6_keyword)
    if hwnd:
        force_foreground_hwnd(hwnd)
    time.sleep(0.35)
    if use_select_all:
        send_hotkey_win32("ctrl", "a")
        time.sleep(0.35)
        keyboard_tools.hotkey("ctrl", "a")
        time.sleep(0.35)
    send_hotkey_win32("ctrl", "c")
    time.sleep(0.35)
    keyboard_tools.hotkey("ctrl", "c")
    time.sleep(0.35)
    send_wm_copy_to_focus(p6_keyword)


def copy_with_sentinel(
    evidence: RunEvidence,
    p6_keyword: str,
    project_name: str,
    p6_rect: P6Rect,
    target: GridClickTarget,
    sentinel: str,
    use_select_all: bool,
    method_name: str,
) -> Tuple[str, str, str, bool, Dict[str, Any]]:
    write_clipboard_text(sentinel)
    time.sleep(0.25)
    ok, _, p6_rect = refocus_p6_grid_for_copy(p6_keyword, project_name, p6_rect, target, evidence)
    if not ok:
        return sentinel, "FAIL_CLIPBOARD_EMPTY", "P6 lost focus before copy", False, {}
    hwnd = get_p6_hwnd(p6_keyword)
    if hwnd:
        force_foreground_hwnd(hwnd)
    trigger_copy_shortcuts(p6_keyword, use_select_all=use_select_all)
    time.sleep(0.8)
    _, copied, _ = read_clipboard_text()
    changed = copied.strip() != sentinel.strip()
    evidence.steps.append(
        f"{method_name}: sentinel_changed={changed} len={len(copied.strip())}"
    )
    status, reason, validation = evaluate_clipboard_copy(copied, sentinel)
    return copied, status, reason, changed, validation


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


def compare_to_m08(validation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    m08_folder = find_latest_m08_folder()
    if not m08_folder:
        return None
    structured_path = m08_folder / "structured" / "activity_table_structured.json"
    if not structured_path.exists():
        return None
    m08_rows = load_json(structured_path).get("rows", [])
    m08_ids = {
        (r.get("activity_id_normalized_candidate") or r.get("activity_id_raw") or "").upper()
        for r in m08_rows
    }
    m08_ids = {i for i in m08_ids if i}

    clip_ids: set[str] = set()
    for row in validation.get("rows", []):
        for match in ACTIVITY_ID_CLIP.findall(" ".join(row)):
            clip_ids.add(match.upper())

    id_matches = sorted(clip_ids & m08_ids)
    return {
        "m08_source_folder": str(m08_folder),
        "m08_row_count": len(m08_rows),
        "clipboard_activity_ids_found": sorted(clip_ids),
        "matching_activity_ids": id_matches,
        "id_match_count": len(id_matches),
    }


def capture_with_retry(
    evidence: RunEvidence,
    label: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    p6_keyword: str,
) -> Tuple[Dict[str, Any], P6Rect]:
    capture = capture_and_ocr_step(evidence, label, p6_rect, config, screen_rule)
    if capture.get("ok"):
        return capture, p6_rect
    evidence.steps.append("capture failed; retry after prepare_p6_for_test")
    prep = prepare_p6_for_test(p6_keyword)
    if prep.get("success") and prep.get("rect"):
        p6_rect = prep["rect"]
    time.sleep(1.0)
    capture = capture_and_ocr_step(evidence, f"{label}_retry", p6_rect, config, screen_rule)
    return capture, p6_rect


def restore_saved_clipboard(saved_clipboard: str, had_text: bool) -> Tuple[bool, str]:
    if saved_clipboard:
        ok, err = write_clipboard_text(saved_clipboard)
        return (True, "restored original clipboard text") if ok else (False, err)
    if had_text:
        return False, "could not restore prior clipboard content"
    return True, "prior clipboard was empty; nothing to restore"


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
    prep = prepare_p6_for_test(p6_keyword)
    if prep.get("success") and prep.get("rect"):
        p6_rect = prep["rect"]

    after, p6_rect = capture_with_retry(
        evidence, "01b_after_activities_nav", p6_rect, config, screen_rule, p6_keyword
    )
    if not after.get("ok"):
        return after, state, p6_rect, capture
    if after.get("unsafe"):
        return after, state, p6_rect, capture

    in_after, _ = confirms_activities_workspace(after["entries"], min_confidence)
    if in_after or after["screen_state"] == "activities_workspace":
        return None, after["screen_state"], p6_rect, after
    return after, state, p6_rect, capture


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
        "clipboard_files": evidence.clipboard_files,
        "screenshots": evidence.screenshots,
        "ocr_files": evidence.ocr_files,
        "classification_files": evidence.classification_files,
        "popup_files": evidence.popup_files,
        "steps": evidence.steps,
        "foreground_before_copy": "",
        "foreground_after_grid_click": "",
        "p6_foreground_confirmed_before_copy": False,
        "grid_click_method": "",
        "grid_click_target": {},
        "grid_click_evidence": "",
        "clipboard_sentinel_used": False,
        "clipboard_changed_from_sentinel": False,
        "copy_method_used": "",
        "clipboard_pollution_detected": False,
        "clipboard_pollution_words": [],
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

    lines = [
        "# M13 Copy Visible Activity Table To Clipboard CSV Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project name: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Window title: {result.get('window_title', '')}",
        f"- Screen state: {result.get('screen_state', '')}",
        f"- Table detected: {result.get('table_detected')}",
        f"- Foreground window before copy: {result.get('foreground_before_copy', '')}",
        f"- Foreground window after grid click: {result.get('foreground_after_grid_click', '')}",
        f"- P6 foreground confirmed before copy: {result.get('p6_foreground_confirmed_before_copy')}",
        f"- Grid click method: {result.get('grid_click_method', '')}",
        f"- Grid click target: {result.get('grid_click_target', {})}",
        f"- Grid click evidence: {result.get('grid_click_evidence', '')}",
        f"- Copy method used: {result.get('copy_method_used', '')}",
        f"- Clipboard sentinel used: {result.get('clipboard_sentinel_used')}",
        f"- Clipboard changed from sentinel: {result.get('clipboard_changed_from_sentinel')}",
        f"- Clipboard pollution detected: {result.get('clipboard_pollution_detected')}",
        f"- Clipboard pollution words: {result.get('clipboard_pollution_words', [])}",
        f"- Clipboard copied: {result.get('clipboard_copied')}",
        f"- Clipboard restored: {result.get('clipboard_restored')}",
        f"- Clipboard restore reason: {result.get('clipboard_restore_reason', '')}",
        f"- Clipboard line count: {result.get('clipboard_line_count', 0)}",
        f"- Clipboard column guess: {result.get('clipboard_column_guess', 0)}",
        f"- Activity-like row count: {result.get('activity_like_row_count', 0)}",
        f"- Headers detected: {result.get('headers_detected', [])}",
        f"- Clipboard files: {result.get('clipboard_files', [])}",
        "",
        "## Screenshot list",
    ]
    for path in result.get("screenshots", []):
        lines.append(f"- {path}")
    lines.extend(["", "## Validation summary", validation_summary or "(none)"])
    if result.get("comparison_to_m08"):
        lines.extend(["", "## Comparison to M08", json.dumps(result["comparison_to_m08"], indent=2)])
    lines.extend(["", "## Final decision", result["status"], "", "## Next recommendation"])
    if result["status"] in ("PASS", "PASS_PARTIAL_CLIPBOARD"):
        lines.append("Ready for M13 hard testing.")
    elif result["status"].startswith("MANUAL_REVIEW"):
        lines.append("Review screenshots and popup evidence before retrying.")
    else:
        lines.append("Fix the reported issue and re-run TY_TEST_M13_COPY_VISIBLE_TABLE.bat")
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _base_kwargs(**kwargs: Any) -> Dict[str, Any]:
    defaults = {
        "window_title": "",
        "screen_state": "",
        "table_detected": False,
        "clipboard_readable_before": False,
        "clipboard_had_text_before": False,
        "clipboard_copied": False,
        "clipboard_restored": False,
        "clipboard_restore_reason": "",
        "clipboard_line_count": 0,
        "clipboard_column_guess": 0,
        "activity_like_row_count": 0,
        "headers_detected": [],
        "comparison_to_m08": None,
        "manual_review_required": False,
        "error": None,
    }
    defaults.update(kwargs)
    return defaults


def run_m13(
    project_name: str,
    *,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
    block_activities_navigation: bool = False,
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

    evidence.steps.append("validate project_name")
    if not is_easyocr_available():
        return finish_result(
            evidence,
            project_name,
            "ERROR",
            "EasyOCR not installed",
            error="pip install easyocr",
        )

    saved_clipboard = ""
    clipboard_readable_before = False
    clipboard_had_text_before = False

    try:
        for _ in range(2):
            try:
                keyboard_tools.press_escape()
            except Exception:  # noqa: BLE001
                pass

        readable, before_text, _ = read_clipboard_text()
        clipboard_readable_before = readable
        clipboard_had_text_before = bool(before_text.strip())
        saved_clipboard = before_text

        evidence.steps.append("prepare_p6_for_test (initial)")
        prep = prepare_p6_for_test(p6_keyword)
        if not prep.get("success") or not prep.get("rect"):
            return finish_result(
                evidence,
                project_name,
                "FAIL_P6_WINDOW_NOT_READY",
                prep.get("message", "P6 window not ready"),
                **_base_kwargs(
                    clipboard_readable_before=clipboard_readable_before,
                    clipboard_had_text_before=clipboard_had_text_before,
                ),
            )

        p6_rect: P6Rect = prep["rect"]
        window_title = window_tools.get_window_state(p6_keyword).get("title") or ""

        capture, p6_rect = capture_with_retry(
            evidence, "01_before_copy", p6_rect, config, screen_rule, p6_keyword
        )
        if not capture.get("ok"):
            restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM" if capture.get("polluted") else "FAIL_P6_WINDOW_NOT_READY",
                capture.get("error", "capture failed"),
                **_base_kwargs(
                    window_title=window_title,
                    clipboard_readable_before=clipboard_readable_before,
                    clipboard_had_text_before=clipboard_had_text_before,
                    clipboard_restored=restored,
                    clipboard_restore_reason=restore_reason,
                    manual_review_required=bool(capture.get("polluted")),
                ),
            )

        screen_state = capture["screen_state"]
        if capture.get("unsafe"):
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_UNSAFE_POPUP",
                capture.get("unsafe_reason", "unsafe popup"),
                **_base_kwargs(
                    window_title=window_title,
                    screen_state=screen_state,
                    clipboard_readable_before=clipboard_readable_before,
                    clipboard_had_text_before=clipboard_had_text_before,
                    manual_review_required=True,
                ),
            )

        if not title_indicates_project_open(window_title, project_name):
            open_ok, open_reason, _ = confirm_project_open(
                capture["entries"], project_name, window_title, min_confidence
            )
            if not open_ok:
                restored, restore_reason = restore_saved_clipboard(
                    saved_clipboard, clipboard_had_text_before
                )
                return finish_result(
                    evidence,
                    project_name,
                    "FAIL_PROJECT_NOT_OPEN",
                    open_reason,
                    **_base_kwargs(
                        window_title=window_title,
                        screen_state=screen_state,
                        clipboard_readable_before=clipboard_readable_before,
                        clipboard_had_text_before=clipboard_had_text_before,
                        clipboard_restored=restored,
                        clipboard_restore_reason=restore_reason,
                    ),
                )

        if block_activities_navigation:
            evidence.steps.append("block_activities_navigation: skip M06-style navigation")
            in_blocked, _ = confirms_activities_workspace(capture["entries"], min_confidence)
            screen_state = capture["screen_state"]
            if not in_blocked and screen_state != "activities_workspace":
                restored, restore_reason = restore_saved_clipboard(
                    saved_clipboard, clipboard_had_text_before
                )
                return finish_result(
                    evidence,
                    project_name,
                    "FAIL_ACTIVITIES_NOT_FOUND",
                    "Activities workspace not confirmed; navigation blocked for hard test",
                    **_base_kwargs(
                        window_title=window_title,
                        screen_state=screen_state,
                        clipboard_readable_before=clipboard_readable_before,
                        clipboard_had_text_before=clipboard_had_text_before,
                        clipboard_restored=restored,
                        clipboard_restore_reason=restore_reason,
                    ),
                )
            working = capture
        else:
            nav_issue, screen_state, p6_rect, working = ensure_activities_workspace(
                evidence, p6_rect, config, screen_rule, p6_keyword, min_confidence, capture
            )
            if nav_issue is not None:
                status = "MANUAL_REVIEW_UNSAFE_POPUP" if nav_issue.get("unsafe") else "FAIL_ACTIVITIES_NOT_FOUND"
                return finish_result(
                    evidence,
                    project_name,
                    status,
                    nav_issue.get("unsafe_reason") or nav_issue.get("error", "Activities not confirmed"),
                    **_base_kwargs(
                        window_title=window_title,
                        screen_state=screen_state,
                        clipboard_readable_before=clipboard_readable_before,
                        clipboard_had_text_before=clipboard_had_text_before,
                        manual_review_required=nav_issue.get("unsafe", False),
                    ),
                )

        extraction = detect_table_evidence(working["entries"], min_confidence)
        table_detected = bool(extraction.get("table_detected"))
        if not table_detected:
            return finish_result(
                evidence,
                project_name,
                "FAIL_TABLE_NOT_DETECTED",
                "Activities workspace confirmed but visible activity table evidence not found",
                **_base_kwargs(
                    window_title=window_title,
                    screen_state=screen_state,
                    clipboard_readable_before=clipboard_readable_before,
                    clipboard_had_text_before=clipboard_had_text_before,
                ),
            )

        evidence.steps.append("prepare_p6_for_test (immediately before grid focus)")
        prep2 = prepare_p6_for_test(p6_keyword)
        if not prep2.get("success") or not prep2.get("rect"):
            restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
            return finish_result(
                evidence,
                project_name,
                "FAIL_P6_WINDOW_NOT_READY",
                prep2.get("message", "P6 not ready before grid focus"),
                **_base_kwargs(
                    window_title=window_title,
                    screen_state=screen_state,
                    table_detected=table_detected,
                    clipboard_readable_before=clipboard_readable_before,
                    clipboard_had_text_before=clipboard_had_text_before,
                    clipboard_restored=restored,
                    clipboard_restore_reason=restore_reason,
                ),
            )
        p6_rect = prep2["rect"]
        window_title = window_tools.get_window_state(p6_keyword).get("title") or ""

        pre_copy_capture, p6_rect = capture_with_retry(
            evidence, "02_pre_grid_focus", p6_rect, config, screen_rule, p6_keyword
        )
        if not pre_copy_capture.get("ok"):
            restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
            return finish_result(
                evidence,
                project_name,
                "FAIL_P6_WINDOW_NOT_READY",
                pre_copy_capture.get("error", "Pre-grid capture failed"),
                **_base_kwargs(
                    window_title=window_title,
                    screen_state=screen_state,
                    table_detected=table_detected,
                    clipboard_readable_before=clipboard_readable_before,
                    clipboard_had_text_before=clipboard_had_text_before,
                    clipboard_restored=restored,
                    clipboard_restore_reason=restore_reason,
                ),
            )

        working = pre_copy_capture
        in_act, _ = confirms_activities_workspace(working["entries"], min_confidence)
        if not in_act and working.get("screen_state") != "activities_workspace":
            restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
            return finish_result(
                evidence,
                project_name,
                "FAIL_ACTIVITIES_NOT_FOUND",
                "Activities workspace not confirmed before grid focus",
                **_base_kwargs(
                    window_title=window_title,
                    screen_state=working.get("screen_state", screen_state),
                    table_detected=table_detected,
                    clipboard_readable_before=clipboard_readable_before,
                    clipboard_had_text_before=clipboard_had_text_before,
                    clipboard_restored=restored,
                    clipboard_restore_reason=restore_reason,
                ),
            )

        extraction = detect_table_evidence(working["entries"], min_confidence)
        table_detected = bool(extraction.get("table_detected"))
        if not table_detected:
            restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
            return finish_result(
                evidence,
                project_name,
                "FAIL_TABLE_NOT_DETECTED",
                "Table evidence lost before grid focus",
                **_base_kwargs(
                    window_title=window_title,
                    screen_state=working.get("screen_state", screen_state),
                    clipboard_readable_before=clipboard_readable_before,
                    clipboard_had_text_before=clipboard_had_text_before,
                    clipboard_restored=restored,
                    clipboard_restore_reason=restore_reason,
                ),
            )

        target = find_activity_row_click_target(working["entries"], min_confidence, p6_rect)
        if target is None:
            restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM",
                "Cannot safely identify activity grid click target",
                **_base_kwargs(
                    window_title=window_title,
                    screen_state=working.get("screen_state", screen_state),
                    table_detected=table_detected,
                    clipboard_readable_before=clipboard_readable_before,
                    clipboard_had_text_before=clipboard_had_text_before,
                    clipboard_restored=restored,
                    clipboard_restore_reason=restore_reason,
                    manual_review_required=True,
                ),
            )

        fg_ok, fg_before, _ = ensure_p6_foreground(p6_keyword, project_name, evidence)
        if not fg_ok:
            restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM",
                "Cannot confirm P6 is foreground before clipboard copy",
                **_base_kwargs(
                    window_title=window_title,
                    screen_state=working.get("screen_state", screen_state),
                    table_detected=table_detected,
                    foreground_before_copy=fg_before,
                    p6_foreground_confirmed_before_copy=False,
                    grid_click_method=target.method,
                    grid_click_target=target.to_dict(),
                    grid_click_evidence=target.evidence,
                    clipboard_readable_before=clipboard_readable_before,
                    clipboard_had_text_before=clipboard_had_text_before,
                    clipboard_restored=restored,
                    clipboard_restore_reason=restore_reason,
                    manual_review_required=True,
                ),
            )

        evidence.steps.append(f"grid click {target.method} at ({target.x:.0f},{target.y:.0f})")
        click_grid_point(p6_rect, target)
        fg_after = get_foreground_window_title()
        if not is_p6_foreground(p6_keyword, project_name, fg_after):
            restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM",
                "P6 not foreground after grid click",
                **_base_kwargs(
                    window_title=window_title,
                    screen_state=working.get("screen_state", screen_state),
                    table_detected=table_detected,
                    foreground_before_copy=fg_before,
                    foreground_after_grid_click=fg_after,
                    p6_foreground_confirmed_before_copy=True,
                    grid_click_method=target.method,
                    grid_click_target=target.to_dict(),
                    grid_click_evidence=target.evidence,
                    clipboard_readable_before=clipboard_readable_before,
                    clipboard_had_text_before=clipboard_had_text_before,
                    clipboard_restored=restored,
                    clipboard_restore_reason=restore_reason,
                    manual_review_required=True,
                ),
            )

        post_click_capture, p6_rect = capture_with_retry(
            evidence, "03_after_grid_click", p6_rect, config, screen_rule, p6_keyword
        )
        if post_click_capture.get("ok"):
            working = post_click_capture

        evidence.steps.append("re-focus P6 grid after post-click OCR (OCR may steal focus)")
        refocus_ok, fg_before, p6_rect = refocus_p6_grid_for_copy(
            p6_keyword, project_name, p6_rect, target, evidence
        )
        if not refocus_ok:
            restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM",
                "Cannot confirm P6 is foreground before clipboard copy after OCR",
                **_base_kwargs(
                    window_title=window_title,
                    screen_state=working.get("screen_state", screen_state),
                    table_detected=table_detected,
                    foreground_before_copy=fg_before,
                    foreground_after_grid_click=fg_after,
                    p6_foreground_confirmed_before_copy=False,
                    grid_click_method=target.method,
                    grid_click_target=target.to_dict(),
                    grid_click_evidence=target.evidence,
                    clipboard_readable_before=clipboard_readable_before,
                    clipboard_had_text_before=clipboard_had_text_before,
                    clipboard_restored=restored,
                    clipboard_restore_reason=restore_reason,
                    manual_review_required=True,
                ),
            )

        copied_text = ""
        copy_status = "FAIL_CLIPBOARD_EMPTY"
        copy_reason = ""
        validation: Dict[str, Any] = {}
        changed_from_sentinel = False
        copy_method_used = ""

        copy_attempts: List[Tuple[str, bool]] = [
            ("method_a_ctrl_c", False),
            ("method_a_ctrl_c_retry", False),
            ("method_b_ctrl_a_c", True),
        ]
        for method_name, use_select_all in copy_attempts:
            if use_select_all and not is_p6_foreground(p6_keyword, project_name):
                continue
            copied_text, eval_status, eval_reason, changed, validation = copy_with_sentinel(
                evidence,
                p6_keyword,
                project_name,
                p6_rect,
                target,
                sentinel,
                use_select_all=use_select_all,
                method_name=method_name,
            )
            changed_from_sentinel = changed
            copy_method_used = method_name
            if eval_status == "OK":
                copy_status = "OK"
                break
            copy_status = eval_status
            copy_reason = eval_reason
            if eval_status == "FAIL_CLIPBOARD_NOT_TABLE":
                break

        if copy_status == "OK":
            save_clipboard_outputs(evidence, copied_text, validation)
            comparison = compare_to_m08(validation)
            restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
            final_status, final_reason = decide_pass_status(table_detected, validation)
            return finish_result(
                evidence,
                project_name,
                final_status,
                final_reason,
                **_base_kwargs(
                    window_title=window_title,
                    screen_state=working.get("screen_state", screen_state),
                    table_detected=table_detected,
                    foreground_before_copy=fg_before,
                    foreground_after_grid_click=fg_after,
                    p6_foreground_confirmed_before_copy=True,
                    grid_click_method=target.method,
                    grid_click_target=target.to_dict(),
                    grid_click_evidence=target.evidence,
                    clipboard_sentinel_used=True,
                    clipboard_changed_from_sentinel=changed_from_sentinel,
                    copy_method_used=copy_method_used,
                    clipboard_pollution_detected=validation.get("clipboard_pollution_detected", False),
                    clipboard_pollution_words=validation.get("clipboard_pollution_words", []),
                    clipboard_readable_before=clipboard_readable_before,
                    clipboard_had_text_before=clipboard_had_text_before,
                    clipboard_copied=True,
                    clipboard_restored=restored,
                    clipboard_restore_reason=restore_reason,
                    clipboard_line_count=int(validation.get("line_count", 0)),
                    clipboard_column_guess=int(validation.get("column_guess", 0)),
                    activity_like_row_count=int(validation.get("activity_like_row_count", 0)),
                    headers_detected=validation.get("headers_detected", []),
                    comparison_to_m08=comparison,
                ),
            )

        if copied_text.strip():
            save_clipboard_outputs(evidence, copied_text, validation or validate_clipboard_table(copied_text))
        restored, restore_reason = restore_saved_clipboard(saved_clipboard, clipboard_had_text_before)
        final_status = copy_status if copy_status != "OK" else "FAIL_CLIPBOARD_NOT_TABLE"
        final_reason = copy_reason or "Clipboard copy did not yield activity table data"

        return finish_result(
            evidence,
            project_name,
            final_status,
            final_reason,
            **_base_kwargs(
                window_title=window_title,
                screen_state=working.get("screen_state", screen_state),
                table_detected=table_detected,
                foreground_before_copy=fg_before,
                foreground_after_grid_click=fg_after,
                p6_foreground_confirmed_before_copy=True,
                grid_click_method=target.method,
                grid_click_target=target.to_dict(),
                grid_click_evidence=target.evidence,
                clipboard_sentinel_used=True,
                clipboard_changed_from_sentinel=changed_from_sentinel,
                copy_method_used=copy_method_used,
                clipboard_pollution_detected=(validation or {}).get("clipboard_pollution_detected", False),
                clipboard_pollution_words=(validation or {}).get("clipboard_pollution_words", []),
                clipboard_readable_before=clipboard_readable_before,
                clipboard_had_text_before=clipboard_had_text_before,
                clipboard_copied=bool(copied_text.strip()),
                clipboard_restored=restored,
                clipboard_restore_reason=restore_reason,
                clipboard_line_count=int((validation or {}).get("line_count", 0)),
                clipboard_column_guess=int((validation or {}).get("column_guess", 0)),
                activity_like_row_count=int((validation or {}).get("activity_like_row_count", 0)),
                headers_detected=(validation or {}).get("headers_detected", []),
            ),
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
            **_base_kwargs(
                clipboard_readable_before=clipboard_readable_before,
                clipboard_had_text_before=clipboard_had_text_before,
                error=traceback.format_exc(),
            ),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="M13 Copy Visible Activity Table To Clipboard CSV")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    args = parser.parse_args()
    result = run_m13(args.project.strip())
    print(f"M13 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"P6 foreground confirmed: {result.get('p6_foreground_confirmed_before_copy')}")
    print(f"Copy method: {result.get('copy_method_used', '')}")
    print(f"Activity-like rows: {result.get('activity_like_row_count', 0)}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")
    if result["status"] in ("PASS", "PASS_PARTIAL_CLIPBOARD"):
        return 0
    if result["status"].startswith("MANUAL_REVIEW"):
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
