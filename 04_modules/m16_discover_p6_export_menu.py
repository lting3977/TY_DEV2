"""
M16 — Discover P6 Export Menu (Phase 15).

Export-path discovery only: opens File > Export, captures evidence, then safely
cancels without completing any export. Does not save files or choose formats.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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
    STABILITY_WAIT,
    capture_and_ocr_step,
    confirm_project_open,
    confirms_activities_workspace,
    detect_unsafe_popup,
    load_json,
    navigate_to_activities,
    title_indicates_project_open,
    write_json,
)

MODULE_NAME = "m16_discover_p6_export_menu"

EXPORT_PHRASES = (
    "export format",
    "export type",
    "primavera pm",
    "microsoft excel",
)
EXPORT_TOKENS = (
    "export",
    "xer",
    "xml",
    "spreadsheet",
    "next",
    "back",
    "cancel",
    "finish",
)
EXPORT_EXTENSIONS = (".xer", ".xml", ".xlsx", ".xls", ".csv", ".plf")
UNSAFE_CONFIRM_PHRASES = (
    "do you want to",
    "want to save",
    "save changes",
    "unsaved changes",
    "overwrite",
    "delete",
    "remove",
)
BLOCKING_BUTTON_LABELS = {"yes", "no", "save", "delete", "overwrite", "remove"}


@dataclass
class RunEvidence:
    run_id: str
    folder: Path
    screenshots_dir: Path
    ocr_dir: Path
    classification_dir: Path
    popup_dir: Path
    discovery_dir: Path
    steps: List[str] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    ocr_files: List[str] = field(default_factory=list)
    classification_files: List[str] = field(default_factory=list)
    popup_files: List[str] = field(default_factory=list)
    discovery_files: List[str] = field(default_factory=list)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_evidence(run_id: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / run_id / MODULE_NAME
    for sub in ("screenshots", "ocr", "classification", "popup", "discovery"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    return RunEvidence(
        run_id=run_id,
        folder=folder,
        screenshots_dir=folder / "screenshots",
        ocr_dir=folder / "ocr",
        classification_dir=folder / "classification",
        popup_dir=folder / "popup",
        discovery_dir=folder / "discovery",
    )


def refresh_p6_rect(p6_keyword: str, fallback: P6Rect) -> P6Rect:
    fresh = get_fresh_p6_rect(p6_keyword)
    rect = fresh.get("rect")
    if fresh.get("success") and rect is not None:
        return rect
    return fallback


def bbox_center(entry: Dict[str, Any]) -> Tuple[float, float]:
    xs = [p[0] for p in entry["bbox"]]
    ys = [p[1] for p in entry["bbox"]]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def exact_button_labels(entries: List[Dict[str, Any]], min_confidence: float) -> Set[str]:
    labels: Set[str] = set()
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        text = entry.get("normalized", "").strip()
        if text in BLOCKING_BUTTON_LABELS or text in {"cancel", "next", "back", "finish", "ok"}:
            labels.add(text)
    return labels


def find_export_evidence_words(blob: str) -> List[str]:
    norm = normalize_text(blob)
    found: List[str] = []
    for phrase in EXPORT_PHRASES:
        if phrase in norm:
            found.append(phrase)
    for token in EXPORT_TOKENS:
        if token in norm.split() or token in norm:
            if token not in found:
                found.append(token)
    return sorted(set(found))


def export_dialog_detected(evidence_words: List[str]) -> bool:
    if not evidence_words:
        return False
    joined = " ".join(evidence_words)
    if "export format" in joined or "export type" in joined:
        return True
    if "export" in evidence_words and len(evidence_words) >= 2:
        return True
    if any(w in evidence_words for w in ("xer", "xml", "spreadsheet", "primavera pm", "microsoft excel")):
        return True
    return False


def partial_export_discovery(evidence_words: List[str]) -> bool:
    return bool(evidence_words) and not export_dialog_detected(evidence_words)


def detect_m16_blocking_popup(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Tuple[bool, str]:
    exact = exact_button_labels(entries, min_confidence)
    blob = collect_text_blob(entries, min_confidence).lower()

    for phrase in UNSAFE_CONFIRM_PHRASES:
        if phrase in blob:
            return True, f"Unsafe confirmation phrase: {phrase}"

    if "yes" in exact and "no" in exact:
        return True, "Yes/No confirmation popup detected"

    unsafe_hits = exact.intersection({"delete", "overwrite", "remove", "save"})
    if unsafe_hits and ("yes" in exact or "no" in exact or "ok" in exact):
        return True, f"Unsafe action popup: {sorted(unsafe_hits)}"

    if "warning" in exact and ("yes" in exact or "no" in exact):
        return True, "Warning popup with Yes/No detected"

    return False, ""


def snapshot_export_files() -> Dict[str, float]:
    snapshots: Dict[str, float] = {}
    candidates: List[Path] = []
    downloads = Path.home() / "Downloads"
    desktop = Path.home() / "Desktop"
    for folder in (downloads, desktop, ROOT / "06_output"):
        if not folder.exists():
            continue
        for ext in EXPORT_EXTENSIONS:
            candidates.extend(folder.glob(f"*{ext}"))
    for path in candidates:
        try:
            snapshots[str(path.resolve())] = path.stat().st_mtime
        except OSError:
            continue
    return snapshots


def export_file_created(before: Dict[str, float], after: Dict[str, float]) -> bool:
    for path, mtime in after.items():
        if path not in before:
            return True
        if mtime > before[path] + 0.01:
            return True
    return False


def open_export_menu(evidence: RunEvidence) -> None:
    evidence.steps.append("open export path: Alt+F, E (File > Export)")
    keyboard_tools.press_escape()
    time.sleep(0.3)
    keyboard_tools.hotkey("alt", "f")
    time.sleep(0.6)
    keyboard_tools.press_key("e")
    time.sleep(STABILITY_WAIT)


def find_cancel_entry(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None
    best_conf = 0.0
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        norm = entry.get("normalized", "").strip()
        if norm == "cancel" and entry["confidence"] >= best_conf:
            best = entry
            best_conf = entry["confidence"]
    return best


def click_ocr_entry(p6_rect: P6Rect, entry: Dict[str, Any]) -> None:
    import pyautogui  # noqa: WPS433

    cx, cy = bbox_center(entry)
    sx = int(p6_rect.left + cx)
    sy = int(p6_rect.top + cy)
    pyautogui.click(sx, sy)
    time.sleep(0.8)


def close_export_dialog(
    evidence: RunEvidence,
    p6_keyword: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    entries: List[Dict[str, Any]],
    evidence_words: List[str],
) -> Tuple[bool, str, P6Rect]:
    min_confidence = float(config.get("min_ocr_confidence", 0.5))
    check: Dict[str, Any] = {"ok": False}

    if not export_dialog_detected(evidence_words) and not partial_export_discovery(evidence_words):
        return True, "none_dialog_not_open", p6_rect

    evidence.steps.append("close export dialog: Esc")
    keyboard_tools.press_escape()
    time.sleep(1.0)

    fresh = refresh_p6_rect(p6_keyword, p6_rect)
    check = capture_and_ocr_step(evidence, "04_after_esc", fresh, config, screen_rule)
    if check.get("ok"):
        words_after = find_export_evidence_words(collect_text_blob(check["entries"], min_confidence))
        if not export_dialog_detected(words_after):
            return True, "esc", fresh

    cancel_entry = find_cancel_entry(entries, min_confidence)
    if cancel_entry:
        evidence.steps.append("close export dialog: OCR-confirmed Cancel click")
        click_ocr_entry(fresh, cancel_entry)
        time.sleep(1.0)
        fresh = refresh_p6_rect(p6_keyword, fresh)
        check = capture_and_ocr_step(evidence, "05_after_cancel", fresh, config, screen_rule)
        if check.get("ok"):
            words_after = find_export_evidence_words(collect_text_blob(check["entries"], min_confidence))
            if not export_dialog_detected(words_after):
                return True, "cancel_click", fresh

    if export_dialog_detected(evidence_words) or partial_export_discovery(evidence_words):
        evidence.steps.append("close export dialog: Alt+F4 (export-related foreground)")
        keyboard_tools.hotkey("alt", "f4")
        time.sleep(1.0)
        fresh = refresh_p6_rect(p6_keyword, fresh)
        check = capture_and_ocr_step(evidence, "06_after_alt_f4", fresh, config, screen_rule)
        if check.get("ok"):
            blocking, reason = detect_m16_blocking_popup(check["entries"], min_confidence)
            if blocking:
                return False, f"alt_f4_blocked:{reason}", fresh
            words_after = find_export_evidence_words(collect_text_blob(check["entries"], min_confidence))
            if not export_dialog_detected(words_after):
                return True, "alt_f4", fresh

    final_words = evidence_words
    if check.get("ok"):
        final_words = find_export_evidence_words(collect_text_blob(check["entries"], min_confidence))
    if export_dialog_detected(final_words):
        return False, "dialog_still_open", fresh
    return True, "esc_or_partial_close", fresh


def save_discovery_evidence(
    evidence: RunEvidence,
    payload: Dict[str, Any],
) -> str:
    path = evidence.discovery_dir / "export_discovery_evidence.json"
    write_json(path, payload)
    evidence.discovery_files.append(str(path))
    return str(path)


def finish_result(
    evidence: RunEvidence,
    project_name: str,
    status: str,
    reason: str,
    *,
    window_title_before: str = "",
    window_title_after: str = "",
    screen_state_before: str = "",
    screen_state_after: str = "",
    export_dialog_detected_flag: bool = False,
    export_evidence_words: Optional[List[str]] = None,
    export_dialog_closed: bool = False,
    close_method_used: str = "",
    export_file_created_flag: bool = False,
    manual_review_required: bool = False,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    result = {
        "run_id": evidence.run_id,
        "module": MODULE_NAME,
        "project_name": project_name,
        "status": status,
        "reason": reason,
        "window_title_before": window_title_before,
        "window_title_after": window_title_after,
        "screen_state_before": screen_state_before,
        "screen_state_after": screen_state_after,
        "export_dialog_detected": export_dialog_detected_flag,
        "export_evidence_words": export_evidence_words or [],
        "export_dialog_closed": export_dialog_closed,
        "close_method_used": close_method_used,
        "export_file_created": export_file_created_flag,
        "screenshots": evidence.screenshots,
        "ocr_files": evidence.ocr_files,
        "classification_files": evidence.classification_files,
        "popup_files": evidence.popup_files,
        "discovery_files": evidence.discovery_files,
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
            texts = [e.get("text", "") for e in data.get("entries", [])[:12]]
            ocr_summary.append(f"{path}: {', '.join(texts)}")
        except Exception:  # noqa: BLE001
            ocr_summary.append(path)

    discovery_summary = ""
    for path in result.get("discovery_files", []):
        try:
            discovery_summary = json.dumps(load_json(Path(path)), indent=2)
        except Exception:  # noqa: BLE001
            discovery_summary = path

    lines = [
        "# M16 Discover P6 Export Menu Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project name: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Window title before: {result.get('window_title_before', '')}",
        f"- Window title after: {result.get('window_title_after', '')}",
        f"- Screen state before: {result.get('screen_state_before', '')}",
        f"- Screen state after: {result.get('screen_state_after', '')}",
        f"- Export dialog detected: {result.get('export_dialog_detected')}",
        f"- Export evidence words: {result.get('export_evidence_words', [])}",
        f"- Export dialog closed: {result.get('export_dialog_closed')}",
        f"- Close method used: {result.get('close_method_used', '')}",
        f"- Export file created: {result.get('export_file_created')}",
        "",
        "## Screenshot list",
    ]
    for path in result.get("screenshots", []):
        lines.append(f"- {path}")

    lines.extend(["", "## OCR summary"])
    for item in ocr_summary or ["(none)"]:
        lines.append(f"- {item}")

    lines.extend(["", "## Popup detection summary"])
    for path in result.get("popup_files", []):
        lines.append(f"- {path}")

    lines.extend(["", "## Discovery evidence summary", discovery_summary or "(none)", "", "## Final decision"])
    lines.append(result["status"])
    lines.extend(["", "## Next recommendation"])
    if result["status"] in ("PASS_EXPORT_DISCOVERY", "PASS_DISCOVERY_PARTIAL"):
        lines.append("Ready for M16 hard testing.")
    elif result["status"].startswith("MANUAL_REVIEW"):
        lines.append("Review screenshots and popup evidence before retrying.")
    else:
        lines.append("Fix the reported issue and re-run TY_TEST_M16_DISCOVER_EXPORT_MENU.bat")
    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def decide_status(
    export_detected: bool,
    partial: bool,
    dialog_closed: bool,
    file_created: bool,
    blocking_after_close: bool,
) -> Tuple[str, str]:
    if file_created:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Export file may have been created — manual review required"
    if blocking_after_close:
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Blocking confirmation popup after close attempt"
    if export_detected and dialog_closed:
        return "PASS_EXPORT_DISCOVERY", "Export menu/dialog discovered and safely closed"
    if partial and dialog_closed:
        return "PASS_DISCOVERY_PARTIAL", "Partial export discovery evidence; dialog safely closed"
    if partial:
        return "PASS_DISCOVERY_PARTIAL", "Partial export-related evidence captured"
    return "FAIL_EXPORT_MENU_NOT_FOUND", "File > Export path did not open export-related UI"


def run_m16(
    project_name: str,
    *,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
    block_activities_navigation: bool = False,
    force_skip_export_open: bool = False,
) -> Dict[str, Any]:
    evidence = evidence or build_evidence(run_id or new_run_id())
    config = load_json(CONFIG_PATH)
    screen_rule = load_json(SCREEN_RULE_PATH)
    p6_keyword = config["p6_window_title_keyword"]
    min_confidence = float(config.get("min_ocr_confidence", 0.5))

    project_name = (project_name or "").strip()
    if not project_name:
        return finish_result(
            evidence,
            "",
            "FAIL_PROJECT_NAME_EMPTY",
            "project_name is empty",
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

    export_snap_before = snapshot_export_files()
    check_after_close: Dict[str, Any] = {"ok": False}

    try:
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
        window_title_before = window_tools.get_window_state(p6_keyword).get("title") or ""

        evidence.steps.append("capture before_action")
        before = capture_and_ocr_step(evidence, "01_before", p6_rect, config, screen_rule)
        if not before.get("ok"):
            polluted = before.get("polluted")
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_P6_WINDOW_NOT_READY",
                before.get("error", "before capture failed"),
                window_title_before=window_title_before,
                screen_state_before="unknown",
                manual_review_required=bool(polluted),
            )

        screen_state_before = before["screen_state"]
        if before.get("unsafe"):
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_UNSAFE_POPUP",
                before.get("unsafe_reason", "unsafe popup before action"),
                window_title_before=window_title_before,
                screen_state_before=screen_state_before,
                manual_review_required=True,
            )

        open_ok, open_reason, _ = confirm_project_open(
            before["entries"], project_name, window_title_before, min_confidence
        )
        if not open_ok:
            return finish_result(
                evidence,
                project_name,
                "FAIL_PROJECT_NOT_OPEN",
                open_reason,
                window_title_before=window_title_before,
                screen_state_before=screen_state_before,
            )

        in_activities, _ = confirms_activities_workspace(before["entries"], min_confidence)
        if not in_activities:
            if block_activities_navigation:
                evidence.steps.append("block_activities_navigation: skip M06-style navigation")
                return finish_result(
                    evidence,
                    project_name,
                    "FAIL_ACTIVITIES_NOT_FOUND",
                    "Activities workspace not confirmed; navigation blocked for hard test",
                    window_title_before=window_title_before,
                    screen_state_before=screen_state_before,
                )
            evidence.steps.append("not in Activities — navigate via M06-style Alt+P, A")
            navigate_to_activities(evidence)
            fresh = refresh_p6_rect(p6_keyword, p6_rect)
            nav_cap = capture_and_ocr_step(evidence, "02_after_nav", fresh, config, screen_rule)
            if not nav_cap.get("ok"):
                return finish_result(
                    evidence,
                    project_name,
                    "FAIL_ACTIVITIES_NOT_FOUND",
                    nav_cap.get("error", "Activities not confirmed after navigation"),
                    window_title_before=window_title_before,
                    screen_state_before=screen_state_before,
                )
            in_activities, _ = confirms_activities_workspace(nav_cap["entries"], min_confidence)
            p6_rect = fresh
            screen_state_before = nav_cap["screen_state"]
            if not in_activities:
                return finish_result(
                    evidence,
                    project_name,
                    "FAIL_ACTIVITIES_NOT_FOUND",
                    "Activities workspace not confirmed after M06-style navigation",
                    window_title_before=window_title_before,
                    screen_state_before=screen_state_before,
                )
            if nav_cap.get("unsafe"):
                return finish_result(
                    evidence,
                    project_name,
                    "MANUAL_REVIEW_UNSAFE_POPUP",
                    nav_cap.get("unsafe_reason", "unsafe popup after navigation"),
                    window_title_before=window_title_before,
                    screen_state_before=screen_state_before,
                    manual_review_required=True,
                )

        if force_skip_export_open:
            evidence.steps.append("force_skip_export_open: hard test mode — export path not opened")
            return finish_result(
                evidence,
                project_name,
                "FAIL_EXPORT_MENU_NOT_FOUND",
                "Export path blocked for hard test; File > Export not opened",
                window_title_before=window_title_before,
                window_title_after=window_tools.get_window_state(p6_keyword).get("title") or "",
                screen_state_before=screen_state_before,
                screen_state_after=screen_state_before,
                export_dialog_detected_flag=False,
                export_evidence_words=[],
                export_dialog_closed=True,
                close_method_used="none_blocked",
                export_file_created_flag=export_file_created(
                    export_snap_before, snapshot_export_files()
                ),
            )

        open_export_menu(evidence)
        p6_rect = refresh_p6_rect(p6_keyword, p6_rect)

        evidence.steps.append("capture after_export_open")
        after_export = capture_and_ocr_step(evidence, "03_after_export", p6_rect, config, screen_rule)
        if not after_export.get("ok"):
            polluted = after_export.get("polluted")
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_P6_WINDOW_NOT_READY",
                after_export.get("error", "after export capture failed"),
                window_title_before=window_title_before,
                screen_state_before=screen_state_before,
                manual_review_required=bool(polluted),
            )

        blocking, blocking_reason = detect_m16_blocking_popup(after_export["entries"], min_confidence)
        if blocking:
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_UNSAFE_POPUP",
                blocking_reason,
                window_title_before=window_title_before,
                screen_state_before=screen_state_before,
                manual_review_required=True,
            )

        blob = collect_text_blob(after_export["entries"], min_confidence)
        evidence_words = find_export_evidence_words(blob)
        export_detected = export_dialog_detected(evidence_words)
        partial = partial_export_discovery(evidence_words)

        discovery_payload = {
            "export_dialog_detected": export_detected,
            "partial_discovery": partial,
            "export_evidence_words": evidence_words,
            "ocr_blob_excerpt": blob[:2000],
            "screen_state": after_export.get("screen_state", ""),
            "classification": after_export.get("classification", {}),
        }
        save_discovery_evidence(evidence, discovery_payload)

        if not export_detected and not partial:
            closed, close_method, p6_rect = close_export_dialog(
                evidence,
                p6_keyword,
                p6_rect,
                config,
                screen_rule,
                after_export["entries"],
                evidence_words,
            )
            return finish_result(
                evidence,
                project_name,
                "FAIL_EXPORT_MENU_NOT_FOUND",
                "File > Export path did not open export-related UI",
                window_title_before=window_title_before,
                window_title_after=window_tools.get_window_state(p6_keyword).get("title") or "",
                screen_state_before=screen_state_before,
                screen_state_after=after_export.get("screen_state", ""),
                export_dialog_detected_flag=False,
                export_evidence_words=evidence_words,
                export_dialog_closed=closed,
                close_method_used=close_method,
                export_file_created_flag=export_file_created(
                    export_snap_before, snapshot_export_files()
                ),
            )

        closed, close_method, p6_rect = close_export_dialog(
            evidence,
            p6_keyword,
            p6_rect,
            config,
            screen_rule,
            after_export["entries"],
            evidence_words,
        )

        evidence.steps.append("capture final_after_close")
        final_cap = capture_and_ocr_step(evidence, "07_final", p6_rect, config, screen_rule)
        screen_state_after = final_cap.get("screen_state", "unknown") if final_cap.get("ok") else "unknown"
        window_title_after = window_tools.get_window_state(p6_keyword).get("title") or ""

        blocking_after = False
        if final_cap.get("ok"):
            blocking_after, blocking_reason = detect_m16_blocking_popup(
                final_cap["entries"], min_confidence
            )
            if blocking_after:
                return finish_result(
                    evidence,
                    project_name,
                    "MANUAL_REVIEW_UNSAFE_POPUP",
                    blocking_reason,
                    window_title_before=window_title_before,
                    window_title_after=window_title_after,
                    screen_state_before=screen_state_before,
                    screen_state_after=screen_state_after,
                    export_dialog_detected_flag=export_detected,
                    export_evidence_words=evidence_words,
                    export_dialog_closed=closed,
                    close_method_used=close_method,
                    export_file_created_flag=export_file_created(
                        export_snap_before, snapshot_export_files()
                    ),
                    manual_review_required=True,
                )

        file_created = export_file_created(export_snap_before, snapshot_export_files())
        status, reason = decide_status(
            export_detected,
            partial,
            closed,
            file_created,
            blocking_after,
        )

        if file_created:
            status = "MANUAL_REVIEW_UNSAFE_POPUP"
            reason = "Export file may have been created — manual review required"
            manual = True
        else:
            manual = status.startswith("MANUAL_REVIEW")

        return finish_result(
            evidence,
            project_name,
            status,
            reason,
            window_title_before=window_title_before,
            window_title_after=window_title_after,
            screen_state_before=screen_state_before,
            screen_state_after=screen_state_after,
            export_dialog_detected_flag=export_detected,
            export_evidence_words=evidence_words,
            export_dialog_closed=closed,
            close_method_used=close_method,
            export_file_created_flag=file_created,
            manual_review_required=manual,
        )

    except Exception as exc:  # noqa: BLE001
        evidence.steps.append(f"exception: {exc}")
        evidence.steps.append(traceback.format_exc())
        return finish_result(
            evidence,
            project_name,
            "ERROR",
            str(exc),
            export_file_created_flag=export_file_created(
                export_snap_before, snapshot_export_files()
            ),
            error=traceback.format_exc(),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="M16 Discover P6 Export Menu")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    args = parser.parse_args()
    result = run_m16(args.project.strip())
    print(f"M16 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Export dialog detected: {result.get('export_dialog_detected')}")
    print(f"Export evidence words: {result.get('export_evidence_words', [])}")
    print(f"Export dialog closed: {result.get('export_dialog_closed')}")
    print(f"Close method: {result.get('close_method_used', '')}")
    print(f"Export file created: {result.get('export_file_created')}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")
    if result["status"] in ("PASS_EXPORT_DISCOVERY", "PASS_DISCOVERY_PARTIAL"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
