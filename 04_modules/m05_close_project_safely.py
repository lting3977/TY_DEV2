"""
M05 — Close Project Safely (Phase 4).

Closes the current P6 project via Ctrl+W when safe.
Auto-confirms normal close-project dialogs when OCR confirms phrase + Yes button.
Stops on save-only, unknown, or unsafe popups. P6-window-only OCR.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "02_eye"))
sys.path.insert(0, str(ROOT / "02_hand"))
sys.path.insert(0, str(ROOT / "02_accessibility"))

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

from accessibility.brain.classifier import (  # noqa: E402
    classify_p6_presence,
    classify_popup_buttons,
    classify_workspace,
)
from accessibility.hand import keyboard_tools, window_tools  # noqa: E402
from eye.ocr import (  # noqa: E402
    check_ocr_pollution,
    collect_text_blob,
    find_keywords,
    is_easyocr_available,
    normalize_text,
    ocr_to_entries,
    run_easyocr,
    save_ocr_results,
)
from eye.screenshot import P6Rect, capture_p6_window_only, crop_center_percent_of_image  # noqa: E402
from hand.p6_prepare import get_fresh_p6_rect, prepare_p6_for_test  # noqa: E402

MODULE_NAME = "m05_close_project_safely"
CONFIG_PATH = ROOT / "01_config" / "ty_config.json"
SCREEN_RULE_PATH = ROOT / "03_screen_library" / "p6_project_workspace" / "screen_rule.json"
STABILITY_WAIT = 2.5

GENERIC_PROJECT_TOKENS = {"project", "name", "portfolio", "select", "current"}
CLOSE_CONFIRM_PHRASES = (
    "close this project",
    "do you want to close this project",
    "close project",
    "want to close",
)
SAVE_ONLY_PHRASES = (
    "save changes",
    "want to save",
    "unsaved changes",
)
UNSAFE_BLOB_WORDS = (
    "delete",
    "remove",
    "overwrite",
    "import",
    "export",
    "schedule",
    "commit changes",
    "error",
    "database",
    "cannot",
    "failed",
)
CLOSE_DIALOG_CROP = {"left": 0.28, "top": 0.32, "right": 0.72, "bottom": 0.68}
YES_LABEL_VARIANTS = {"yes", "ves", "yas", "ye5"}
BLOCKING_BUTTON_LABELS = {"yes", "no", "ok", "cancel", "open", "save", "warning"}


@dataclass
class RunEvidence:
    run_id: str
    folder: Path
    screenshots_dir: Path
    ocr_dir: Path
    classification_dir: Path
    popup_dir: Path
    steps: List[str] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    ocr_files: List[str] = field(default_factory=list)
    classification_files: List[str] = field(default_factory=list)
    popup_files: List[str] = field(default_factory=list)


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_evidence(run_id: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / run_id / MODULE_NAME
    for sub in ("screenshots", "ocr", "classification", "popup"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    return RunEvidence(
        run_id=run_id,
        folder=folder,
        screenshots_dir=folder / "screenshots",
        ocr_dir=folder / "ocr",
        classification_dir=folder / "classification",
        popup_dir=folder / "popup",
    )


def project_tokens(project_name: str) -> List[str]:
    return [t for t in re.split(r"[\s\-_]+", project_name.strip()) if t]


def meaningful_project_tokens(project_name: str) -> List[str]:
    return [
        t
        for t in project_tokens(project_name)
        if normalize_text(t) not in GENERIC_PROJECT_TOKENS and len(normalize_text(t)) >= 2
    ]


def title_indicates_project_open(title: str, project_name: str) -> bool:
    if not title:
        return False
    norm_title = normalize_text(title)
    if "no current project" in norm_title:
        return False
    norm_project = normalize_text(project_name)
    if norm_project in norm_title:
        return True
    tokens = meaningful_project_tokens(project_name)
    return len(tokens) >= 2 and all(normalize_text(t) in norm_title for t in tokens)


def project_name_in_ocr(blob: str, project_name: str) -> bool:
    norm_project = normalize_text(project_name)
    if norm_project in blob:
        return True
    tokens = meaningful_project_tokens(project_name)
    return len(tokens) >= 2 and all(normalize_text(t) in blob for t in tokens)


def title_indicates_no_project(title: str) -> bool:
    return "no current project" in normalize_text(title or "")


def blob_indicates_no_project(blob: str) -> bool:
    return "no current project" in blob


def any_project_workspace_open(blob: str, title: str) -> bool:
    if title_indicates_no_project(title):
        return False
    if blob_indicates_no_project(blob):
        return False
    if "activities" in blob or "wbs" in blob:
        return True
    norm_title = normalize_text(title or "")
    return "(" in (title or "") and "primavera" in norm_title


def bbox_center(bbox: List[List[float]]) -> Tuple[float, float]:
    xs = [p[0] for p in bbox]
    ys = [p[1] for p in bbox]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def exact_button_labels(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> set[str]:
    labels: set[str] = set()
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        text = entry["normalized"].strip()
        if text in BLOCKING_BUTTON_LABELS:
            labels.add(text)
    return labels


def is_close_confirmation_phrase(blob: str) -> Tuple[bool, str]:
    norm = blob.lower()
    for phrase in CLOSE_CONFIRM_PHRASES:
        if phrase in norm:
            return True, phrase
    return False, ""


def is_save_only_phrase(blob: str) -> Tuple[bool, str]:
    norm = blob.lower()
    close_hit, _ = is_close_confirmation_phrase(norm)
    if close_hit:
        return False, ""
    for phrase in SAVE_ONLY_PHRASES:
        if phrase in norm:
            return True, phrase
    if "save" in norm and "close" not in norm:
        return True, "save"
    return False, ""


def has_unsafe_blob_words(blob: str) -> Tuple[bool, List[str]]:
    norm = blob.lower()
    hits = [w for w in UNSAFE_BLOB_WORDS if w in norm]
    if "warning" in norm and not is_close_confirmation_phrase(norm)[0]:
        hits.append("warning")
    return bool(hits), hits


def classify_screen_state(
    entries: List[Dict[str, Any]],
    screen_rule: Dict[str, Any],
    config: Dict[str, Any],
    min_confidence: float,
) -> Dict[str, Any]:
    open_hits = find_keywords(
        entries, ["Open Project", "Project ID", "Project Name", "Open", "Cancel"], min_confidence
    )
    open_project_dialog = sum(1 for v in open_hits.values() if v) >= 3
    popup_buttons = classify_popup_buttons(
        entries, config.get("popup_button_keywords", []), min_confidence
    )
    workspace = classify_workspace(entries, min_confidence)
    p6_presence = classify_p6_presence(
        entries, config.get("p6_recognition_keywords", []), min_confidence
    )
    blob = collect_text_blob(entries, min_confidence)
    workspace_keywords = screen_rule.get("workspace_keywords", ["activities", "wbs", "eps"])
    workspace_hits = {kw: kw in blob for kw in workspace_keywords}

    if open_project_dialog:
        state = "open_project_dialog"
    elif "activities" in blob and p6_presence["level"] != "none":
        state = "activities_workspace"
    elif blob_indicates_no_project(blob):
        state = "no_current_project"
    elif p6_presence["level"] != "none":
        state = "p6_main"
    else:
        state = "unknown"

    return {
        "screen_state": state,
        "open_project_dialog": open_project_dialog,
        "open_project_hits": open_hits,
        "popup_buttons": popup_buttons,
        "workspace": workspace,
        "workspace_hits": workspace_hits,
        "p6_presence": p6_presence,
        "ocr_blob_excerpt": blob[:1000],
    }


def analyze_popup(
    classification: Dict[str, Any],
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Dict[str, Any]:
    blob = (classification.get("ocr_blob_excerpt") or "").lower()
    exact = exact_button_labels(entries, min_confidence)
    close_hit, close_phrase = is_close_confirmation_phrase(blob)
    save_hit, save_phrase = is_save_only_phrase(blob)
    unsafe_hit, unsafe_words = has_unsafe_blob_words(blob)
    yes_entries = find_yes_button_entries(entries, min_confidence)

    popup_kind = "none"
    if classification.get("open_project_dialog"):
        popup_kind = "blocking_open_dialog"
    elif save_hit:
        popup_kind = "save_only"
    elif unsafe_hit:
        popup_kind = "unsafe"
    elif close_hit:
        popup_kind = "close_confirm"
    elif exact.intersection({"yes", "no"}):
        popup_kind = "unknown_confirm"

    return {
        "popup_kind": popup_kind,
        "close_confirmation_detected": close_hit,
        "close_phrase": close_phrase,
        "save_only_detected": save_hit,
        "save_phrase": save_phrase,
        "unsafe_words": unsafe_words,
        "exact_buttons": sorted(exact),
        "yes_button_count": len(yes_entries),
        "yes_entries": yes_entries,
    }


def find_yes_button_entries(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    for entry in entries:
        if entry["confidence"] < min_confidence * 0.45:
            continue
        text = entry["normalized"].strip()
        if text in YES_LABEL_VARIANTS or text.startswith("yes"):
            hits.append(entry)
    return hits


def dialog_crop_origin(p6_width: int, p6_height: int) -> Tuple[int, int]:
    left = int(p6_width * float(CLOSE_DIALOG_CROP["left"]))
    top = int(p6_height * float(CLOSE_DIALOG_CROP["top"]))
    return left, top


def ocr_dialog_crop_for_buttons(
    evidence: RunEvidence,
    screenshot_path: str,
    label: str,
) -> List[Dict[str, Any]]:
    crop_path = str(evidence.screenshots_dir / f"{label}_dialog_crop.png")
    crop_center_percent_of_image(screenshot_path, crop_path, CLOSE_DIALOG_CROP)
    evidence.screenshots.append(crop_path)
    raw = run_easyocr(crop_path)
    ocr_path = str(evidence.ocr_dir / f"{label}_dialog_crop_ocr.json")
    save_ocr_results(raw, ocr_path)
    evidence.ocr_files.append(ocr_path)
    return ocr_to_entries(raw)


def find_button_entries(
    entries: List[Dict[str, Any]],
    label: str,
    min_confidence: float,
) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        if entry["normalized"].strip() == label:
            hits.append(entry)
    return hits


def click_ocr_entry(
    entry: Dict[str, Any],
    p6_rect: P6Rect,
    crop_origin: Tuple[int, int] = (0, 0),
) -> Tuple[int, int]:
    import pyautogui

    cx, cy = bbox_center(entry["bbox"])
    sx, sy = int(p6_rect.left + crop_origin[0] + cx), int(p6_rect.top + crop_origin[1] + cy)
    pyautogui.click(sx, sy)
    return sx, sy


def confirm_close_with_alt_y(evidence: RunEvidence) -> str:
    import pyautogui

    pyautogui.hotkey("alt", "y")
    evidence.steps.append("confirm_close: alt+y_close_dialog_accelerator")
    time.sleep(STABILITY_WAIT)
    return "alt+y_close_dialog_accelerator"


def try_confirm_close_popup(
    evidence: RunEvidence,
    popup: Dict[str, Any],
    p6_rect: P6Rect,
    screenshot_path: str,
) -> Tuple[bool, str]:
    if popup["popup_kind"] != "close_confirm":
        return False, "Not a close confirmation popup"
    if popup["save_only_detected"]:
        return False, "Save-only prompt — cannot auto-confirm"
    if popup["unsafe_words"]:
        return False, f"Unsafe words in popup: {popup['unsafe_words']}"
    if not popup["close_confirmation_detected"]:
        return False, "Close confirmation phrase not detected"

    min_confidence = 0.5
    yes_entries = list(popup.get("yes_entries") or [])
    if not yes_entries and screenshot_path:
        crop_entries = ocr_dialog_crop_for_buttons(evidence, screenshot_path, "close_dialog")
        yes_entries = find_yes_button_entries(crop_entries, min_confidence)
        crop_ox, crop_oy = dialog_crop_origin(p6_rect.width, p6_rect.height)
    else:
        crop_ox, crop_oy = 0, 0

    high = [e for e in yes_entries if e["confidence"] >= 0.45]
    if len(high) == 1:
        sx, sy = click_ocr_entry(high[0], p6_rect, (crop_ox, crop_oy))
        evidence.steps.append(f"confirm_close: click_yes at ({sx},{sy})")
        time.sleep(STABILITY_WAIT)
        return True, f"Clicked Yes at ({sx},{sy})"

    if len(high) == 0 and popup["close_confirmation_detected"]:
        action = confirm_close_with_alt_y(evidence)
        return True, f"Close phrase confirmed; used {action} (Yes button not OCR-visible)"

    return False, f"Ambiguous Yes button ({len(yes_entries)} detected, {len(high)} usable)"


def capture_and_ocr_step(
    evidence: RunEvidence,
    label: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
) -> Dict[str, Any]:
    min_confidence = float(config.get("min_ocr_confidence", 0.5))
    meta_path = evidence.ocr_dir / f"{label}_capture_metadata.json"
    capture = capture_p6_window_only(
        evidence.screenshots_dir,
        f"{label}_p6_crop.png",
        p6_rect,
        metadata_path=meta_path,
    )
    if not capture["success"]:
        return {"ok": False, "error": capture.get("error", "capture failed")}

    evidence.screenshots.append(capture["image_path"])
    metadata = capture.get("metadata") or {}
    full_screen = bool(metadata.get("full_screen_fallback", False))
    if full_screen:
        return {"ok": False, "error": "Full-screen OCR fallback detected", "full_screen": True}

    if not is_easyocr_available():
        return {"ok": False, "error": "EasyOCR not available"}

    raw = run_easyocr(capture["image_path"])
    ocr_path = str(evidence.ocr_dir / f"{label}_ocr.json")
    save_ocr_results(raw, ocr_path, metadata=metadata)
    evidence.ocr_files.append(ocr_path)

    entries = ocr_to_entries(raw)
    pollution = check_ocr_pollution(entries, config.get("pollution_keywords"), min_confidence)
    if pollution["polluted"]:
        return {
            "ok": False,
            "error": f"OCR pollution: {pollution['pollution_words']}",
            "polluted": True,
        }

    classification = classify_screen_state(entries, screen_rule, config, min_confidence)
    cls_path = evidence.classification_dir / f"{label}_classification.json"
    write_json(cls_path, classification)
    evidence.classification_files.append(str(cls_path))

    popup = analyze_popup(classification, entries, min_confidence)
    popup_path = evidence.popup_dir / f"{label}_popup.json"
    write_json(popup_path, popup)
    evidence.popup_files.append(str(popup_path))

    return {
        "ok": True,
        "entries": entries,
        "classification": classification,
        "popup": popup,
        "screen_state": classification.get("screen_state", "unknown"),
        "full_screen_ocr": False,
        "screenshot_path": capture["image_path"],
    }


def popup_stop_status(popup: Dict[str, Any]) -> Tuple[Optional[str], str]:
    if popup["popup_kind"] == "blocking_open_dialog":
        return "MANUAL_REVIEW_UNSAFE_POPUP", "Blocking Open Project dialog before close"
    if popup["popup_kind"] == "save_only":
        return "MANUAL_REVIEW_SAVE_PROMPT", f"Save prompt detected: {popup['save_phrase']}"
    if popup["popup_kind"] == "unsafe":
        return "MANUAL_REVIEW_UNSAFE_POPUP", f"Unsafe popup words: {popup['unsafe_words']}"
    if popup["popup_kind"] == "unknown_confirm":
        return "MANUAL_REVIEW_UNKNOWN_POPUP", "Unknown Yes/No confirmation popup"
    return None, ""


def confirm_target_project_open(
    entries: List[Dict[str, Any]],
    project_name: str,
    window_title: str,
    min_confidence: float,
) -> Tuple[bool, str, List[str]]:
    blob = collect_text_blob(entries, min_confidence)
    words: List[str] = []

    if title_indicates_no_project(window_title) or blob_indicates_no_project(blob):
        return False, f"Project '{project_name}' is not open", words

    title_match = title_indicates_project_open(window_title, project_name)
    if title_match:
        words.append(f"window_title:{window_title}")
    if project_name_in_ocr(blob, project_name):
        words.append(f"ocr_full:{normalize_text(project_name)}")
    for token in meaningful_project_tokens(project_name):
        if normalize_text(token) in blob:
            words.append(f"token:{token}")

    workspace_hits = [w for w in ("activities", "eps", "wbs") if w in blob]
    words.extend(workspace_hits)

    if title_match or (project_name_in_ocr(blob, project_name) and workspace_hits):
        return True, f"Project '{project_name}' confirmed open", words

    if project_name_in_ocr(blob, project_name) or title_match:
        return True, f"Project '{project_name}' likely open (partial confirmation)", words

    return False, f"Project '{project_name}' not found in title or OCR", words


def confirm_project_closed(
    project_name: Optional[str],
    window_title: str,
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Tuple[bool, str, List[str]]:
    blob = collect_text_blob(entries, min_confidence)
    words: List[str] = []
    screen_state = classify_screen_state(
        entries,
        load_json(SCREEN_RULE_PATH),
        load_json(CONFIG_PATH),
        min_confidence,
    ).get("screen_state", "unknown")

    if title_indicates_no_project(window_title) or blob_indicates_no_project(blob):
        words.append("no_current_project")
        return True, "P6 reports no current project", words

    if screen_state == "no_current_project":
        words.append("screen:no_current_project")
        return True, "Screen classified as no current project", words

    if project_name:
        if title_indicates_project_open(window_title, project_name):
            return False, f"Window title still shows '{project_name}'", words
        if project_name_in_ocr(blob, project_name) and ("activities" in blob or "wbs" in blob):
            return False, f"OCR still shows '{project_name}' in activities workspace", words
        if not title_indicates_project_open(window_title, project_name):
            words.append("title_cleared")

    if not any_project_workspace_open(blob, window_title):
        words.append("workspace_cleared")
        return True, "Project workspace no longer detected", words

    if "projects" in blob and "activities" not in blob:
        words.append("projects_workspace")
        return True, "Projects workspace without active project activities", words

    return False, "Project may still be open — confirmation insufficient", words


def finish_result(
    evidence: RunEvidence,
    project_name: Optional[str],
    status: str,
    reason: str,
    *,
    window_title_before: str = "",
    window_title_after: str = "",
    before_screen_state: str = "",
    after_screen_state: str = "",
    close_confirmation_detected: bool = False,
    confirmation_action_taken: str = "",
    final_project_closed_confirmation: Optional[List[str]] = None,
    manual_review_required: bool = False,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    result = {
        "run_id": evidence.run_id,
        "module": MODULE_NAME,
        "project_name": project_name or "",
        "status": status,
        "reason": reason,
        "window_title_before": window_title_before,
        "window_title_after": window_title_after,
        "screenshots": evidence.screenshots,
        "ocr_files": evidence.ocr_files,
        "classification_files": evidence.classification_files,
        "popup_files": evidence.popup_files,
        "before_screen_state": before_screen_state,
        "after_screen_state": after_screen_state,
        "close_confirmation_detected": close_confirmation_detected,
        "confirmation_action_taken": confirmation_action_taken,
        "final_project_closed_confirmation": final_project_closed_confirmation or [],
        "unsafe_button_presses": 0,
        "full_screen_ocr": False,
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

    lines = [
        "# M05 Close Project Safely Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project name: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Window title before: {result.get('window_title_before', '')}",
        f"- Window title after: {result.get('window_title_after', '')}",
        f"- Before screen state: {result.get('before_screen_state', '')}",
        f"- After screen state: {result.get('after_screen_state', '')}",
        f"- Close confirmation detected: {result.get('close_confirmation_detected', False)}",
        f"- Confirmation action taken: {result.get('confirmation_action_taken', '')}",
        f"- Final project closed confirmation: {result.get('final_project_closed_confirmation', [])}",
        f"- Unsafe button presses: {result.get('unsafe_button_presses', 0)}",
        f"- Full-screen OCR: {result.get('full_screen_ocr', False)}",
        "",
        "## Screenshot list",
    ]
    for path in result.get("screenshots", []):
        lines.append(f"- {path}")

    lines.extend(["", "## OCR summary"])
    for item in ocr_summary or ["(none)"]:
        lines.append(f"- {item}")

    lines.extend(["", "## Screen classification"])
    for path in result.get("classification_files", []):
        lines.append(f"- {path}")

    lines.extend(["", "## Popup detection summary"])
    for path in result.get("popup_files", []):
        lines.append(f"- {path}")

    lines.extend(["", "## Final decision", result["status"], "", "## Next recommendation"])
    if result["status"] == "PASS_CLOSED":
        lines.append("Project closed safely. Ready for M05 hard testing.")
    elif result["status"] == "PASS_ALREADY_NO_PROJECT":
        lines.append("No project was open. Ready for M05 hard testing.")
    elif result["status"] == "MANUAL_REVIEW_SAVE_PROMPT":
        lines.append("Save-only prompt — human must choose.")
    elif result["status"].startswith("MANUAL_REVIEW"):
        lines.append("Review screenshots and popup evidence before retrying.")
    else:
        lines.append("Fix the reported issue and re-run TY_TEST_M05_CLOSE_PROJECT.bat")

    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def finalize_closed_state(
    evidence: RunEvidence,
    project_name: Optional[str],
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    p6_keyword: str,
    *,
    window_title_before: str,
    before_screen_state: str,
    close_confirmation_detected: bool,
    confirmation_action_taken: str,
) -> Dict[str, Any]:
    fresh = get_fresh_p6_rect(p6_keyword)
    if fresh.get("success") and fresh.get("rect"):
        p6_rect = fresh["rect"]
    window_title_after = window_tools.get_window_state(p6_keyword).get("title") or ""

    evidence.steps.append("capture final state")
    final = capture_and_ocr_step(evidence, "03_final", p6_rect, config, screen_rule)
    if not final.get("ok"):
        polluted = final.get("polluted")
        return finish_result(
            evidence,
            project_name,
            "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_P6_WINDOW_NOT_READY",
            final.get("error", "final capture failed"),
            window_title_before=window_title_before,
            window_title_after=window_title_after,
            before_screen_state=before_screen_state,
            close_confirmation_detected=close_confirmation_detected,
            confirmation_action_taken=confirmation_action_taken,
            manual_review_required=bool(polluted),
        )

    final_state = final["screen_state"]
    closed, close_reason, closed_words = confirm_project_closed(
        project_name,
        window_title_after,
        final["entries"],
        float(config.get("min_ocr_confidence", 0.5)),
    )
    if closed:
        return finish_result(
            evidence,
            project_name,
            "PASS_CLOSED",
            close_reason,
            window_title_before=window_title_before,
            window_title_after=window_title_after,
            before_screen_state=before_screen_state,
            after_screen_state=final_state,
            close_confirmation_detected=close_confirmation_detected,
            confirmation_action_taken=confirmation_action_taken,
            final_project_closed_confirmation=closed_words,
        )

    return finish_result(
        evidence,
        project_name,
        "MANUAL_REVIEW_CANNOT_CONFIRM",
        close_reason,
        window_title_before=window_title_before,
        window_title_after=window_title_after,
        before_screen_state=before_screen_state,
        after_screen_state=final_state,
        close_confirmation_detected=close_confirmation_detected,
        confirmation_action_taken=confirmation_action_taken,
        final_project_closed_confirmation=closed_words,
        manual_review_required=True,
    )


def handle_popup_after_capture(
    evidence: RunEvidence,
    step: Dict[str, Any],
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    p6_keyword: str,
    *,
    project_name: Optional[str],
    window_title_before: str,
    before_screen_state: str,
    allow_close_confirm: bool,
) -> Optional[Dict[str, Any]]:
    popup = step["popup"]
    stop_status, stop_reason = popup_stop_status(popup)
    if stop_status:
        return finish_result(
            evidence,
            project_name,
            stop_status,
            stop_reason,
            window_title_before=window_title_before,
            window_title_after=window_tools.get_window_state(p6_keyword).get("title") or "",
            before_screen_state=before_screen_state,
            after_screen_state=step["screen_state"],
            close_confirmation_detected=popup["close_confirmation_detected"],
            manual_review_required=True,
        )

    if allow_close_confirm and popup["popup_kind"] == "close_confirm":
        confirmed, action = try_confirm_close_popup(
            evidence,
            popup,
            p6_rect,
            step.get("screenshot_path", ""),
        )
        if confirmed:
            return finalize_closed_state(
                evidence,
                project_name,
                p6_rect,
                config,
                screen_rule,
                p6_keyword,
                window_title_before=window_title_before,
                before_screen_state=before_screen_state,
                close_confirmation_detected=True,
                confirmation_action_taken=action,
            )
        return finish_result(
            evidence,
            project_name,
            "MANUAL_REVIEW_CANNOT_CONFIRM",
            action,
            window_title_before=window_title_before,
            before_screen_state=before_screen_state,
            after_screen_state=step["screen_state"],
            close_confirmation_detected=True,
            manual_review_required=True,
        )
    return None


def run_m05(
    project_name: Optional[str] = None,
    *,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
    before_prepare_hook: Optional[Callable[[], None]] = None,
    after_prepare_hook: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    evidence = evidence or build_evidence(run_id or new_run_id())
    config = load_json(CONFIG_PATH)
    screen_rule = load_json(SCREEN_RULE_PATH)
    p6_keyword = config["p6_window_title_keyword"]
    min_confidence = float(config.get("min_ocr_confidence", 0.5))
    project_name = project_name.strip() if project_name and str(project_name).strip() else None

    if not is_easyocr_available():
        return finish_result(
            evidence,
            project_name,
            "ERROR",
            "EasyOCR not installed",
            error="pip install easyocr",
        )

    try:
        if before_prepare_hook:
            evidence.steps.append("before_prepare_hook")
            before_prepare_hook()

        evidence.steps.append("prepare_p6_for_test")
        prep = prepare_p6_for_test(p6_keyword)
        if not prep.get("success") or not prep.get("rect"):
            return finish_result(
                evidence,
                project_name,
                "FAIL_P6_WINDOW_NOT_READY",
                prep.get("message", "P6 window not ready"),
            )

        if after_prepare_hook:
            evidence.steps.append("after_prepare_hook")
            after_prepare_hook()

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
                before_screen_state="unknown",
                manual_review_required=bool(polluted),
            )

        before_state = before["screen_state"]
        before_popup = before["popup"]

        pre_stop = handle_popup_after_capture(
            evidence,
            before,
            p6_rect,
            config,
            screen_rule,
            p6_keyword,
            project_name=project_name,
            window_title_before=window_title_before,
            before_screen_state=before_state,
            allow_close_confirm=True,
        )
        if pre_stop:
            return pre_stop

        before_blob = before["classification"].get("ocr_blob_excerpt", "")

        evidence.steps.append("check initial project state")
        if title_indicates_no_project(window_title_before) or blob_indicates_no_project(
            before_blob
        ):
            return finish_result(
                evidence,
                project_name,
                "PASS_ALREADY_NO_PROJECT",
                "No project is currently open",
                window_title_before=window_title_before,
                window_title_after=window_title_before,
                before_screen_state=before_state,
                after_screen_state=before_state,
            )

        if not any_project_workspace_open(before_blob, window_title_before):
            return finish_result(
                evidence,
                project_name,
                "PASS_ALREADY_NO_PROJECT",
                "No active project workspace detected",
                window_title_before=window_title_before,
                window_title_after=window_title_before,
                before_screen_state=before_state,
                after_screen_state=before_state,
            )

        if project_name:
            open_ok, open_reason, _words = confirm_target_project_open(
                before["entries"], project_name, window_title_before, min_confidence
            )
            if not open_ok:
                return finish_result(
                    evidence,
                    project_name,
                    "FAIL_PROJECT_NOT_OPEN",
                    open_reason,
                    window_title_before=window_title_before,
                    before_screen_state=before_state,
                )

        evidence.steps.append("issue Ctrl+W close project command")
        keyboard_tools.hotkey("ctrl", "w")
        time.sleep(STABILITY_WAIT)

        fresh = get_fresh_p6_rect(p6_keyword)
        if fresh.get("success") and fresh.get("rect"):
            p6_rect = fresh["rect"]

        evidence.steps.append("capture after_ctrl_w")
        after = capture_and_ocr_step(evidence, "02_after", p6_rect, config, screen_rule)
        if not after.get("ok"):
            polluted = after.get("polluted")
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_P6_WINDOW_NOT_READY",
                after.get("error", "after capture failed"),
                window_title_before=window_title_before,
                window_title_after=window_tools.get_window_state(p6_keyword).get("title") or "",
                before_screen_state=before_state,
                manual_review_required=bool(polluted),
            )

        post_stop = handle_popup_after_capture(
            evidence,
            after,
            p6_rect,
            config,
            screen_rule,
            p6_keyword,
            project_name=project_name,
            window_title_before=window_title_before,
            before_screen_state=before_state,
            allow_close_confirm=True,
        )
        if post_stop:
            return post_stop

        return finalize_closed_state(
            evidence,
            project_name,
            p6_rect,
            config,
            screen_rule,
            p6_keyword,
            window_title_before=window_title_before,
            before_screen_state=before_state,
            close_confirmation_detected=False,
            confirmation_action_taken="none_required",
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


def ensure_project_open_for_test(project: str) -> None:
    sys.path.insert(0, str(ROOT / "04_modules"))
    from m04_check_project_opened import run_m04  # noqa: WPS433

    check = run_m04(project, run_id=f"{new_run_id()}_precheck")
    if check.get("status") == "PASS":
        print(f"Pre-check: project '{project}' is open")
        return
    from m03_open_project_by_name import run_m03  # noqa: WPS433

    print(f"Pre-check: opening '{project}' via M03")
    opened = run_m03(project, run_id=f"{new_run_id()}_preopen")
    print(f"M03 pre-open status: {opened.get('status')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="M05 Close Project Safely")
    parser.add_argument("--project", default=None, help='Optional project name e.g. "Talison 1275"')
    parser.add_argument("--skip-preopen", action="store_true", help="Skip M03 pre-open step")
    args = parser.parse_args()

    if args.project and not args.skip_preopen:
        ensure_project_open_for_test(args.project.strip())

    result = run_m05(args.project)
    print(f"M05 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Close confirmation detected: {result.get('close_confirmation_detected')}")
    print(f"Confirmation action: {result.get('confirmation_action_taken')}")
    print(f"Window title before: {result.get('window_title_before', '')}")
    print(f"Window title after: {result.get('window_title_after', '')}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")

    if result["status"] in ("PASS_CLOSED", "PASS_ALREADY_NO_PROJECT"):
        return 0
    if result["status"].startswith("MANUAL_REVIEW"):
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
