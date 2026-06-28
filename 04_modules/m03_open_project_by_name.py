"""
M03 — Open Project By Name (Phase 2).

Opens a named Primavera P6 project via the Open Project dialog.
Uses P6-window-only OCR from Phase 1 foundation. No full-desktop OCR.
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
from accessibility.hand import keyboard_tools  # noqa: E402
from accessibility.hand import window_tools  # noqa: E402
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
from eye.screenshot import (  # noqa: E402
    P6Rect,
    capture_p6_window_only,
    crop_center_percent_of_image,
)
from hand.p6_prepare import get_fresh_p6_rect, prepare_p6_for_test  # noqa: E402

MODULE_NAME = "m03_open_project_by_name"
CONFIG_PATH = ROOT / "01_config" / "ty_config.json"
SCREEN_RULE_PATH = ROOT / "03_screen_library" / "p6_open_project" / "screen_rule.json"
STABILITY_WAIT = 2.5

UNSAFE_POPUP_WORDS = {"yes", "no", "warning", "delete", "save", "overwrite", "remove", "confirm"}
SAFE_DIALOG_WORDS = {"open project", "cancel", "open", "project name", "project id", "help"}


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


def title_indicates_project_open(title: str, project_name: str) -> bool:
    if not title:
        return False
    norm_title = normalize_text(title)
    if "no current project" in norm_title:
        return False
    norm_project = normalize_text(project_name)
    if norm_project in norm_title:
        return True
    tokens = project_tokens(project_name)
    return len(tokens) >= 2 and all(normalize_text(t) in norm_title for t in tokens)


def classify_screen_state(
    entries: List[Dict[str, Any]],
    screen_rule: Dict[str, Any],
    config: Dict[str, Any],
    min_confidence: float,
) -> Dict[str, Any]:
    open_hits = find_keywords(
        entries, screen_rule.get("recognition_text", []), min_confidence
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

    if open_project_dialog:
        state = "open_project_dialog"
    elif "activities" in blob and p6_presence["level"] != "none":
        state = "activities_workspace"
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
        "p6_presence": p6_presence,
        "ocr_blob_excerpt": blob[:1000],
    }


def exact_popup_button_labels(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> set[str]:
    """Match standalone button labels only — not substrings like 'no' in 'november'."""
    labels: set[str] = set()
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        text = entry["normalized"].strip()
        if text in UNSAFE_POPUP_WORDS or text in {"ok", "cancel", "open"}:
            labels.add(text)
    return labels


UNSAFE_CONFIRM_PHRASES = (
    "close this project",
    "want to save",
    "do you want to",
    "save changes",
    "unsaved changes",
    "want to close",
)


def detect_unsafe_popup(
    classification: Dict[str, Any],
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Tuple[bool, str]:
    exact = exact_popup_button_labels(entries, min_confidence)
    blob = (classification.get("ocr_blob_excerpt") or "").lower()

    if classification.get("open_project_dialog"):
        if "yes" in exact and "no" in exact:
            return True, "Yes/No buttons on Open Project dialog variant"
        return False, ""

    for phrase in UNSAFE_CONFIRM_PHRASES:
        if phrase in blob:
            return True, f"Unsafe confirmation phrase detected: {phrase}"

    if "yes" in exact and "no" in exact:
        return True, "Yes/No confirmation popup detected"
    if "warning" in exact and ("yes" in exact or "no" in exact):
        return True, "Warning popup with Yes/No detected"
    if exact.intersection({"delete", "overwrite", "remove", "save"}):
        hit = exact.intersection({"delete", "overwrite", "remove", "save"})
        return True, f"Unsafe action popup: {sorted(hit)}"
    return False, ""


def popup_crop_offsets(screen_rule: Dict[str, Any], p6_width: int, p6_height: int) -> Tuple[int, int]:
    crop = screen_rule["crop_region_percent"]
    left = int(p6_width * float(crop["left"]))
    top = int(p6_height * float(crop["top"]))
    return left, top


def bbox_center(bbox: List[List[float]]) -> Tuple[float, float]:
    xs = [p[0] for p in bbox]
    ys = [p[1] for p in bbox]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def image_point_to_screen(
    p6_rect: P6Rect,
    image_x: float,
    image_y: float,
    crop_origin_x: int = 0,
    crop_origin_y: int = 0,
) -> Tuple[int, int]:
    return (
        int(p6_rect.left + crop_origin_x + image_x),
        int(p6_rect.top + crop_origin_y + image_y),
    )


def capture_p6_ocr_step(
    evidence: RunEvidence,
    label: str,
    p6_rect: P6Rect,
    config: Dict[str, Any],
    screen_rule: Dict[str, Any],
    *,
    use_popup_crop: bool = False,
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
        return {"ok": False, "error": capture.get("error"), "entries": [], "classification": {}}

    evidence.screenshots.append(capture["image_path"])
    ocr_image = capture["image_path"]
    crop_ox, crop_oy = 0, 0

    if use_popup_crop:
        popup_path = str(evidence.screenshots_dir / f"{label}_popup_crop.png")
        crop_center_percent_of_image(ocr_image, popup_path, screen_rule["crop_region_percent"])
        evidence.screenshots.append(popup_path)
        crop_ox, crop_oy = popup_crop_offsets(screen_rule, p6_rect.width, p6_rect.height)
        ocr_image = popup_path

    if not is_easyocr_available():
        return {"ok": False, "error": "EasyOCR not available", "entries": [], "classification": {}}

    raw = run_easyocr(ocr_image)
    ocr_path = str(evidence.ocr_dir / f"{label}_ocr.json")
    save_ocr_results(raw, ocr_path, metadata=capture["metadata"])
    evidence.ocr_files.append(ocr_path)

    entries = ocr_to_entries(raw)
    pollution = check_ocr_pollution(entries, config.get("pollution_keywords"), min_confidence)
    if pollution["polluted"]:
        return {
            "ok": False,
            "error": f"OCR pollution: {pollution['pollution_words']}",
            "entries": entries,
            "classification": {},
            "pollution": pollution,
        }

    classification = classify_screen_state(entries, screen_rule, config, min_confidence)
    cls_path = evidence.classification_dir / f"{label}_classification.json"
    write_json(cls_path, classification)
    evidence.classification_files.append(str(cls_path))

    popup_path = evidence.popup_dir / f"{label}_popup.json"
    write_json(
        popup_path,
        {
            "popup_buttons": classification.get("popup_buttons"),
            "unsafe_check": detect_unsafe_popup(classification, entries, min_confidence),
        },
    )
    evidence.popup_files.append(str(popup_path))

    return {
        "ok": True,
        "entries": entries,
        "classification": classification,
        "ocr_path": ocr_path,
        "capture": capture,
        "crop_origin": (crop_ox, crop_oy),
        "ocr_image": ocr_image,
        "pollution": pollution,
    }


def find_project_matches(
    entries: List[Dict[str, Any]],
    project_name: str,
    min_confidence: float,
) -> List[Dict[str, Any]]:
    norm_target = normalize_text(project_name)
    generic_tokens = {"project", "name", "does", "not", "exist", "portfolio", "select"}
    tokens = [
        normalize_text(t)
        for t in project_tokens(project_name)
        if len(t) >= 3 and normalize_text(t) not in generic_tokens
    ]
    min_reverse_len = max(8, len(norm_target) // 2)
    matches: List[Dict[str, Any]] = []

    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        text = entry["normalized"]
        if norm_target in text:
            matches.append(entry)
            continue
        if len(text) >= min_reverse_len and text in norm_target:
            matches.append(entry)
            continue
        if len(tokens) >= 2 and all(t in text for t in tokens):
            matches.append(entry)
    return matches


def confirm_project_open(
    entries: List[Dict[str, Any]],
    project_name: str,
    window_title: Optional[str],
    min_confidence: float,
) -> Tuple[bool, List[str]]:
    words: List[str] = []
    norm_project = normalize_text(project_name)
    tokens = project_tokens(project_name)
    blob = collect_text_blob(entries, min_confidence)

    if title_indicates_project_open(window_title or "", project_name):
        words.append(f"window_title:{window_title}")

    if norm_project in blob:
        words.append(f"ocr_full:{norm_project}")
    for token in tokens:
        if normalize_text(token) in blob:
            words.append(f"token:{token}")

    workspace_hits = [w for w in ("activities", "eps", "wbs") if w in blob]
    words.extend(workspace_hits)

    if "no current project" in blob:
        return False, words

    has_name = norm_project in blob or (
        len(tokens) >= 2 and all(normalize_text(t) in blob for t in tokens)
    )
    has_context = bool(workspace_hits) or title_indicates_project_open(window_title or "", project_name)
    confirmed = has_name and (has_context or title_indicates_project_open(window_title or "", project_name))
    return confirmed, words


def open_project_dialog() -> str:
    try:
        keyboard_tools.open_dialog_ctrl_o()
        return "ctrl+o"
    except Exception:  # noqa: BLE001
        pass
    keyboard_tools.hotkey("alt", "f")
    time.sleep(0.4)
    keyboard_tools.press_key("o")
    time.sleep(1.0)
    return "alt+f,o"


def click_entry_on_screen(
    entry: Dict[str, Any],
    p6_rect: P6Rect,
    crop_origin: Tuple[int, int],
) -> Tuple[int, int]:
    import pyautogui

    cx, cy = bbox_center(entry["bbox"])
    sx, sy = image_point_to_screen(p6_rect, cx, cy, crop_origin[0], crop_origin[1])
    pyautogui.click(sx, sy)
    return sx, sy


def type_filter_project(project_name: str) -> None:
    import pyautogui

    pyautogui.write(project_name, interval=0.05)
    time.sleep(0.6)


def confirm_open_with_alt_o() -> None:
    keyboard_tools.hotkey("alt", "o")
    time.sleep(0.5)


def finish_result(
    evidence: RunEvidence,
    project_name: str,
    status: str,
    reason: str,
    *,
    final_screen_state: str = "",
    confirmation_words: Optional[List[str]] = None,
    manual_review_required: bool = False,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    result = {
        "run_id": evidence.run_id,
        "module": MODULE_NAME,
        "project_name": project_name,
        "status": status,
        "reason": reason,
        "screenshots": evidence.screenshots,
        "ocr_files": evidence.ocr_files,
        "classification_files": evidence.classification_files,
        "popup_files": evidence.popup_files,
        "final_screen_state": final_screen_state,
        "confirmation_words": confirmation_words or [],
        "manual_review_required": manual_review_required,
        "error": error,
        "steps": evidence.steps,
    }
    write_json(evidence.folder / "result.json", result)
    write_report(evidence, result)
    return result


def write_report(evidence: RunEvidence, result: Dict[str, Any]) -> None:
    lines = [
        "# M03 Open Project By Name Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project name: {result['project_name']}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        "",
        "## Step list",
    ]
    for step in result.get("steps", []):
        lines.append(f"- {step}")

    lines.extend(["", "## Screenshot list"])
    for path in result.get("screenshots", []):
        lines.append(f"- {path}")

    lines.extend(["", "## OCR files"])
    for path in result.get("ocr_files", []):
        lines.append(f"- {path}")

    lines.extend(["", "## Screen classification summary"])
    for path in result.get("classification_files", []):
        lines.append(f"- {path}")

    lines.extend(["", "## Popup detection summary"])
    for path in result.get("popup_files", []):
        lines.append(f"- {path}")

    lines.extend(
        [
            "",
            f"- Final screen state: {result.get('final_screen_state', '')}",
            f"- Confirmation words: {result.get('confirmation_words', [])}",
            f"- Manual review required: {result.get('manual_review_required', False)}",
            "",
            "## Final decision",
            result["status"],
            "",
            "## Next recommendation",
        ]
    )
    if result["status"] in ("PASS", "PASS_ALREADY_OPEN"):
        lines.append("M03 first simple test ready for hard testing.")
    elif result["status"].startswith("MANUAL_REVIEW"):
        lines.append("Review screenshots and popup evidence before retrying.")
    else:
        lines.append("Fix the reported issue and re-run TY_TEST_M03_OPEN_PROJECT.bat")

    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_m03(
    project_name: str,
    *,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
    before_prepare_hook: Optional[Any] = None,
    after_prepare_hook: Optional[Any] = None,
    skip_prepare: bool = False,
    allow_already_open_shortcut: bool = True,
) -> Dict[str, Any]:
    evidence = evidence or build_evidence(run_id or new_run_id())
    config = load_json(CONFIG_PATH)
    screen_rule = load_json(SCREEN_RULE_PATH)
    p6_keyword = config["p6_window_title_keyword"]
    min_confidence = float(config.get("min_ocr_confidence", 0.5))

    if not project_name or not project_name.strip():
        return finish_result(
            evidence,
            project_name,
            "FAIL_PROJECT_NAME_EMPTY",
            "project_name is empty",
        )

    project_name = project_name.strip()
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
        if before_prepare_hook:
            evidence.steps.append("before_prepare_hook")
            before_prepare_hook()

        if skip_prepare:
            evidence.steps.append("skip_prepare: using current P6 window state")
            fresh = get_fresh_p6_rect(p6_keyword)
            if not fresh.get("success") or not fresh.get("rect"):
                return finish_result(
                    evidence,
                    project_name,
                    "FAIL_P6_WINDOW_NOT_READY",
                    fresh.get("message", "P6 window not ready"),
                )
            prep = fresh
            p6_rect: P6Rect = prep["rect"]
        else:
            evidence.steps.append("prepare_p6_for_test")
            prep = prepare_p6_for_test(p6_keyword)
            if not prep.get("success") or not prep.get("rect"):
                return finish_result(
                    evidence,
                    project_name,
                    "FAIL_P6_WINDOW_NOT_READY",
                    prep.get("message", "P6 window not ready"),
                )
            p6_rect = prep["rect"]

        if after_prepare_hook:
            evidence.steps.append("after_prepare_hook")
            after_prepare_hook()

        window_state = prep.get("window_state") or window_tools.get_window_state(p6_keyword)
        title = window_state.get("title") or ""
        if allow_already_open_shortcut and title_indicates_project_open(title, project_name):
            evidence.steps.append("already_open detected from window title (pre-OCR)")
            words = [f"window_title:{title}"]
            return finish_result(
                evidence,
                project_name,
                "PASS_ALREADY_OPEN",
                f"Project already open: {title}",
                final_screen_state="p6_main_project_open",
                confirmation_words=words,
            )

        evidence.steps.append("capture before_action")
        before = capture_p6_ocr_step(evidence, "01_before", p6_rect, config, screen_rule)
        if not before.get("ok"):
            err = before.get("error", "before capture failed")
            if "OCR pollution" in err:
                return finish_result(
                    evidence,
                    project_name,
                    "OCR_POLLUTION",
                    err,
                )
            return finish_result(
                evidence,
                project_name,
                "FAIL_P6_WINDOW_NOT_READY",
                err,
            )

        window_state = prep.get("window_state") or window_tools.get_window_state(p6_keyword)
        title = window_state.get("title") or ""
        unsafe, unsafe_reason = detect_unsafe_popup(
            before["classification"], before["entries"], min_confidence
        )
        if unsafe:
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_UNSAFE_POPUP",
                unsafe_reason,
                final_screen_state=before["classification"].get("screen_state", ""),
                manual_review_required=True,
            )

        if allow_already_open_shortcut and title_indicates_project_open(title, project_name):
            evidence.steps.append("already_open detected from window title")
            confirmed, words = confirm_project_open(
                before["entries"], project_name, title, min_confidence
            )
            if confirmed:
                return finish_result(
                    evidence,
                    project_name,
                    "PASS_ALREADY_OPEN",
                    f"Project already open: {title}",
                    final_screen_state=before["classification"].get("screen_state", ""),
                    confirmation_words=words,
                )

        evidence.steps.append("open Open Project dialog (ctrl+o)")
        method = open_project_dialog()
        evidence.steps.append(f"dialog open method: {method}")
        time.sleep(1.2)

        fresh = get_fresh_p6_rect(p6_keyword)
        if fresh.get("success") and fresh.get("rect"):
            p6_rect = fresh["rect"]

        evidence.steps.append("capture dialog state")
        dialog = capture_p6_ocr_step(
            evidence,
            "02_dialog",
            p6_rect,
            config,
            screen_rule,
            use_popup_crop=True,
        )
        if not dialog.get("ok"):
            err = dialog.get("error", "dialog capture failed")
            if "OCR pollution" in err:
                return finish_result(
                    evidence,
                    project_name,
                    "OCR_POLLUTION",
                    err,
                )
            return finish_result(
                evidence,
                project_name,
                "FAIL_P6_WINDOW_NOT_READY",
                err,
            )

        unsafe, unsafe_reason = detect_unsafe_popup(
            dialog["classification"], dialog["entries"], min_confidence
        )
        if unsafe:
            keyboard_tools.press_escape()
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_UNSAFE_POPUP",
                unsafe_reason,
                final_screen_state=dialog["classification"].get("screen_state", ""),
                manual_review_required=True,
            )

        if not dialog["classification"].get("open_project_dialog"):
            keyboard_tools.press_escape()
            return finish_result(
                evidence,
                project_name,
                "FAIL_OPEN_DIALOG_NOT_FOUND",
                "Open Project dialog not recognised in P6 popup OCR",
                final_screen_state=dialog["classification"].get("screen_state", "unknown"),
            )

        evidence.steps.append("search/select project in dialog")
        matches = find_project_matches(dialog["entries"], project_name, min_confidence)

        if not matches:
            evidence.steps.append("type project name to filter list")
            crop = screen_rule["crop_region_percent"]
            list_x = p6_rect.width * (float(crop["left"]) + float(crop["right"])) / 2
            list_y = p6_rect.height * (float(crop["top"]) + float(crop["bottom"])) / 2
            import pyautogui

            sx, sy = image_point_to_screen(p6_rect, list_x, list_y)
            pyautogui.click(sx, sy)
            time.sleep(0.3)
            type_filter_project(project_name)
            time.sleep(0.8)

            dialog2 = capture_p6_ocr_step(
                evidence,
                "03_after_filter",
                p6_rect,
                config,
                screen_rule,
                use_popup_crop=True,
            )
            if dialog2.get("ok"):
                matches = find_project_matches(
                    dialog2["entries"], project_name, min_confidence
                )
                dialog = dialog2

        if not matches:
            keyboard_tools.press_escape()
            return finish_result(
                evidence,
                project_name,
                "FAIL_PROJECT_NOT_FOUND",
                f"Project '{project_name}' not found in Open Project dialog OCR",
                final_screen_state="open_project_dialog",
            )

        high_conf = [m for m in matches if m["confidence"] >= 0.75]
        if len(high_conf) != 1:
            keyboard_tools.press_escape()
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM",
                f"Ambiguous project matches ({len(matches)} found, {len(high_conf)} high-confidence)",
                final_screen_state="open_project_dialog",
                manual_review_required=True,
            )

        selected = high_conf[0]
        evidence.steps.append(f"click project row: {selected['text']}")
        sx, sy = click_entry_on_screen(selected, p6_rect, dialog["crop_origin"])
        evidence.steps.append(f"clicked at ({sx},{sy})")
        time.sleep(0.5)

        evidence.steps.append("confirm open with Alt+O")
        confirm_open_with_alt_o()
        time.sleep(STABILITY_WAIT)

        fresh = get_fresh_p6_rect(p6_keyword)
        if fresh.get("success") and fresh.get("rect"):
            p6_rect = fresh["rect"]
        window_state = window_tools.get_window_state(p6_keyword)
        title = window_state.get("title") or ""

        evidence.steps.append("capture final state")
        final = capture_p6_ocr_step(evidence, "04_final", p6_rect, config, screen_rule)
        if not final.get("ok"):
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM",
                final.get("error", "final capture failed"),
                manual_review_required=True,
            )

        unsafe, unsafe_reason = detect_unsafe_popup(
            final["classification"], final["entries"], min_confidence
        )
        if unsafe:
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_UNSAFE_POPUP",
                unsafe_reason,
                final_screen_state=final["classification"].get("screen_state", ""),
                manual_review_required=True,
            )

        confirmed, words = confirm_project_open(
            final["entries"], project_name, title, min_confidence
        )
        final_state = final["classification"].get("screen_state", "unknown")

        if confirmed:
            evidence.steps.append("project open confirmed")
            return finish_result(
                evidence,
                project_name,
                "PASS",
                f"Project '{project_name}' opened and confirmed",
                final_screen_state=final_state,
                confirmation_words=words,
            )

        return finish_result(
            evidence,
            project_name,
            "MANUAL_REVIEW_CANNOT_CONFIRM",
            "Open action completed but final OCR/title could not confirm project",
            final_screen_state=final_state,
            confirmation_words=words,
            manual_review_required=True,
        )

    except Exception as exc:  # noqa: BLE001
        evidence.steps.append(f"exception: {exc}")
        evidence.steps.append(traceback.format_exc())
        try:
            keyboard_tools.press_escape()
        except Exception:  # noqa: BLE001
            pass
        return finish_result(
            evidence,
            project_name,
            "ERROR",
            str(exc),
            error=traceback.format_exc(),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="M03 Open Project By Name")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    args = parser.parse_args()

    result = run_m03(args.project)
    print(f"M03 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")
    if result["status"] in ("PASS", "PASS_ALREADY_OPEN"):
        return 0
    if result["status"].startswith("MANUAL_REVIEW"):
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
