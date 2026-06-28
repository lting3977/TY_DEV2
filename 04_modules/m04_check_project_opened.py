"""
M04 — Check Project Opened (Phase 3).

Read-only observation: confirms whether a named Primavera P6 project is open.
Uses P6-window-only OCR. Does not open, close, or modify anything.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
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
from eye.screenshot import P6Rect, capture_p6_window_only  # noqa: E402
from hand.p6_prepare import prepare_p6_for_test  # noqa: E402

MODULE_NAME = "m04_check_project_opened"
CONFIG_PATH = ROOT / "01_config" / "ty_config.json"
SCREEN_RULE_PATH = ROOT / "03_screen_library" / "p6_project_workspace" / "screen_rule.json"

UNSAFE_POPUP_WORDS = {"yes", "no", "warning", "delete", "save", "overwrite", "remove", "confirm"}
GENERIC_PROJECT_TOKENS = {"project", "name", "portfolio", "select", "current"}
UNSAFE_CONFIRM_PHRASES = (
    "close this project",
    "want to save",
    "do you want to",
    "save changes",
    "unsaved changes",
    "want to close",
)


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
    tokens = [
        t for t in project_tokens(project_name) if normalize_text(t) not in GENERIC_PROJECT_TOKENS
    ]
    return len(tokens) >= 2 and all(normalize_text(t) in norm_title for t in tokens)


def exact_popup_button_labels(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> set[str]:
    labels: set[str] = set()
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        text = entry["normalized"].strip()
        if text in UNSAFE_POPUP_WORDS or text in {"ok", "cancel", "open"}:
            labels.add(text)
    return labels


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
    elif "no current project" in blob:
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


def meaningful_project_tokens(project_name: str) -> List[str]:
    return [
        t
        for t in project_tokens(project_name)
        if normalize_text(t) not in GENERIC_PROJECT_TOKENS and len(normalize_text(t)) >= 2
    ]


def project_name_in_ocr(blob: str, project_name: str) -> bool:
    norm_project = normalize_text(project_name)
    if norm_project in blob:
        return True
    tokens = meaningful_project_tokens(project_name)
    return len(tokens) >= 2 and all(normalize_text(t) in blob for t in tokens)


def evaluate_project_open(
    entries: List[Dict[str, Any]],
    project_name: str,
    window_title: str,
    min_confidence: float,
) -> Tuple[str, str, List[str]]:
    words: List[str] = []
    blob = collect_text_blob(entries, min_confidence)
    norm_title = normalize_text(window_title or "")

    if "no current project" in norm_title or "no current project" in blob:
        return (
            "FAIL_PROJECT_NOT_OPEN",
            "P6 reports no current project open",
            words,
        )

    title_match = title_indicates_project_open(window_title or "", project_name)
    if title_match:
        words.append(f"window_title:{window_title}")

    norm_project = normalize_text(project_name)
    if norm_project in blob:
        words.append(f"ocr_full:{norm_project}")

    for token in meaningful_project_tokens(project_name):
        if normalize_text(token) in blob:
            words.append(f"token:{token}")

    workspace_hits = [w for w in ("activities", "eps", "wbs") if w in blob]
    words.extend(workspace_hits)

    has_name = project_name_in_ocr(blob, project_name) or title_match
    has_context = bool(workspace_hits) or title_match

    if title_match and (project_name_in_ocr(blob, project_name) or workspace_hits):
        return (
            "PASS",
            f"Project '{project_name}' is open (title + OCR/workspace confirmed)",
            words,
        )

    if title_match:
        return (
            "PASS",
            f"Project '{project_name}' is open (confirmed via window title)",
            words,
        )

    if project_name_in_ocr(blob, project_name) and workspace_hits:
        return (
            "PASS",
            f"Project '{project_name}' is open (OCR name + workspace indicators)",
            words,
        )

    if not has_name:
        return (
            "FAIL_PROJECT_NOT_OPEN",
            f"Project '{project_name}' not found in window title or OCR",
            words,
        )

    if has_name and not has_context:
        return (
            "MANUAL_REVIEW_CANNOT_CONFIRM",
            "Project name signals found but workspace/title context is insufficient",
            words,
        )

    return (
        "MANUAL_REVIEW_CANNOT_CONFIRM",
        "Ambiguous project open state — manual review required",
        words,
    )


def finish_result(
    evidence: RunEvidence,
    project_name: str,
    status: str,
    reason: str,
    *,
    window_title: str = "",
    screen_state: str = "",
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
        "window_title": window_title,
        "screenshots": evidence.screenshots,
        "ocr_files": evidence.ocr_files,
        "classification_files": evidence.classification_files,
        "popup_files": evidence.popup_files,
        "screen_state": screen_state,
        "confirmation_words": confirmation_words or [],
        "manual_review_required": manual_review_required,
        "error": error,
        "steps": evidence.steps,
    }
    write_json(evidence.folder / "result.json", result)
    write_report(evidence, result)
    return result


def write_report(evidence: RunEvidence, result: Dict[str, Any]) -> None:
    ocr_summary = []
    for path in result.get("ocr_files", []):
        try:
            data = load_json(Path(path))
            texts = [e.get("text", "") for e in data.get("entries", [])[:12]]
            ocr_summary.append(f"{path}: {', '.join(texts)}")
        except Exception:  # noqa: BLE001
            ocr_summary.append(path)

    lines = [
        "# M04 Check Project Opened Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project name: {result['project_name']}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Window title: {result.get('window_title', '')}",
        "",
        "## Screenshot list",
    ]
    for path in result.get("screenshots", []):
        lines.append(f"- {path}")

    lines.extend(["", "## OCR summary"])
    if ocr_summary:
        for item in ocr_summary:
            lines.append(f"- {item}")
    else:
        lines.append("- (none)")

    lines.extend(["", "## Screen classification"])
    for path in result.get("classification_files", []):
        lines.append(f"- {path}")

    lines.extend(["", "## Popup detection summary"])
    for path in result.get("popup_files", []):
        lines.append(f"- {path}")

    lines.extend(
        [
            "",
            f"- Screen state: {result.get('screen_state', '')}",
            "",
            "## Confirmation words",
            str(result.get("confirmation_words", [])),
            "",
            "## Final decision",
            result["status"],
            "",
            "## Next recommendation",
        ]
    )
    if result["status"] == "PASS":
        lines.append("Ready for M04 hard testing.")
    elif result["status"].startswith("MANUAL_REVIEW"):
        lines.append("Review screenshots and popup evidence before retrying.")
    elif result["status"] == "FAIL_PROJECT_NOT_OPEN":
        lines.append("Open the requested project with M03, then re-run TY_TEST_M04_CHECK_PROJECT.bat")
    else:
        lines.append("Fix the reported issue and re-run TY_TEST_M04_CHECK_PROJECT.bat")

    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_m04(
    project_name: str,
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

    if not project_name or not str(project_name).strip():
        return finish_result(
            evidence,
            project_name or "",
            "FAIL_PROJECT_NAME_EMPTY",
            "project_name is empty",
        )

    project_name = str(project_name).strip()
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
        window_state = prep.get("window_state") or window_tools.get_window_state(p6_keyword)
        window_title = window_state.get("title") or ""

        evidence.steps.append("capture P6-only screenshot and OCR")
        meta_path = evidence.ocr_dir / "01_observe_capture_metadata.json"
        capture = capture_p6_window_only(
            evidence.screenshots_dir,
            "01_observe_p6_crop.png",
            p6_rect,
            metadata_path=meta_path,
        )
        if not capture["success"]:
            return finish_result(
                evidence,
                project_name,
                "FAIL_P6_WINDOW_NOT_READY",
                capture.get("error", "P6 capture failed"),
                window_title=window_title,
            )

        evidence.screenshots.append(capture["image_path"])
        raw = run_easyocr(capture["image_path"])
        ocr_path = str(evidence.ocr_dir / "01_observe_ocr.json")
        save_ocr_results(raw, ocr_path, metadata=capture["metadata"])
        evidence.ocr_files.append(ocr_path)

        entries = ocr_to_entries(raw)
        pollution = check_ocr_pollution(entries, config.get("pollution_keywords"), min_confidence)
        if pollution["polluted"]:
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM",
                f"OCR pollution detected: {pollution['pollution_words']}",
                window_title=window_title,
                manual_review_required=True,
            )

        evidence.steps.append("classify current screen")
        classification = classify_screen_state(entries, screen_rule, config, min_confidence)
        cls_path = evidence.classification_dir / "01_observe_classification.json"
        write_json(cls_path, classification)
        evidence.classification_files.append(str(cls_path))
        screen_state = classification.get("screen_state", "unknown")

        popup_path = evidence.popup_dir / "01_observe_popup.json"
        unsafe, unsafe_reason = detect_unsafe_popup(classification, entries, min_confidence)
        write_json(
            popup_path,
            {
                "popup_buttons": classification.get("popup_buttons"),
                "unsafe_check": [unsafe, unsafe_reason],
            },
        )
        evidence.popup_files.append(str(popup_path))

        evidence.steps.append("detect unsafe popup")
        if unsafe:
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_UNSAFE_POPUP",
                unsafe_reason,
                window_title=window_title,
                screen_state=screen_state,
                manual_review_required=True,
            )

        evidence.steps.append("evaluate project open state")
        status, reason, confirmation_words = evaluate_project_open(
            entries, project_name, window_title, min_confidence
        )
        manual_review = status.startswith("MANUAL_REVIEW")
        return finish_result(
            evidence,
            project_name,
            status,
            reason,
            window_title=window_title,
            screen_state=screen_state,
            confirmation_words=confirmation_words,
            manual_review_required=manual_review,
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
    parser = argparse.ArgumentParser(description="M04 Check Project Opened")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    args = parser.parse_args()

    result = run_m04(args.project)
    print(f"M04 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Window title: {result.get('window_title', '')}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")
    if result["status"] == "PASS":
        return 0
    if result["status"].startswith("MANUAL_REVIEW"):
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
