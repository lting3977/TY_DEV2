"""
M06 — Go To Activities (Phase 5).

Safe navigation to Activities workspace when the requested project is open.
Uses Alt+P, A (Project menu -> Activities). No schedule edit, save, or close.
P6-window-only OCR.
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
from typing import Any, Dict, List, Optional, Tuple

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
from eye.screenshot import P6Rect, capture_p6_window_only  # noqa: E402
from hand.p6_prepare import get_fresh_p6_rect, prepare_p6_for_test  # noqa: E402

MODULE_NAME = "m06_go_to_activities"
CONFIG_PATH = ROOT / "01_config" / "ty_config.json"
SCREEN_RULE_PATH = ROOT / "03_screen_library" / "p6_project_workspace" / "screen_rule.json"
STABILITY_WAIT = 2.5

GENERIC_PROJECT_TOKENS = {"project", "name", "portfolio", "select", "current"}
UNSAFE_BLOB_WORDS = (
    "delete",
    "remove",
    "overwrite",
    "import",
    "export",
    "commit changes",
    "save changes",
    "unsaved changes",
)
UNSAFE_CONFIRM_PHRASES = (
    "close this project",
    "want to save",
    "do you want to",
    "want to close",
)
ACTIVITIES_INDICATORS = (
    "activities",
    "wbs",
    "activity id",
    "activity name",
    "start",
    "finish",
    "layout",
)
BLOCKING_BUTTON_LABELS = {"yes", "no", "ok", "cancel", "open", "save", "warning", "delete", "overwrite", "remove"}


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


def exact_button_labels(entries: List[Dict[str, Any]], min_confidence: float) -> set[str]:
    labels: set[str] = set()
    for entry in entries:
        if entry["confidence"] < min_confidence:
            continue
        text = entry["normalized"].strip()
        if text in BLOCKING_BUTTON_LABELS:
            labels.add(text)
    return labels


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

    if open_project_dialog:
        state = "open_project_dialog"
    elif is_wbs_primary_workspace(blob):
        state = "wbs_workspace"
    elif is_projects_primary_workspace(blob):
        state = "projects_workspace"
    elif confirms_activities_workspace(entries, min_confidence)[0]:
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
        "p6_presence": p6_presence,
        "ocr_blob_excerpt": blob[:1000],
    }


def detect_unsafe_popup(
    classification: Dict[str, Any],
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Tuple[bool, str]:
    exact = exact_button_labels(entries, min_confidence)
    blob = (classification.get("ocr_blob_excerpt") or "").lower()

    if classification.get("open_project_dialog"):
        return True, "Open Project dialog blocks navigation"

    for phrase in UNSAFE_CONFIRM_PHRASES:
        if phrase in blob:
            return True, f"Unsafe confirmation phrase: {phrase}"

    for word in UNSAFE_BLOB_WORDS:
        if word in blob and ("yes" in exact or "no" in exact or "save" in exact):
            return True, f"Unsafe popup word with buttons: {word}"

    if "yes" in exact and "no" in exact:
        return True, "Yes/No confirmation popup detected"
    if exact.intersection({"delete", "overwrite", "remove", "save"}):
        hit = exact.intersection({"delete", "overwrite", "remove", "save"})
        return True, f"Unsafe action popup: {sorted(hit)}"
    if "warning" in exact and ("yes" in exact or "no" in exact):
        return True, "Warning popup with Yes/No detected"
    return False, ""


def activities_indicator_hits(blob: str) -> List[str]:
    return [ind for ind in ACTIVITIES_INDICATORS if ind in blob]


def is_wbs_primary_workspace(blob: str) -> bool:
    if "layout:wbs" in blob or "layout: wbs" in blob:
        return True
    if "wbs code" in blob and "wbs name" in blob:
        if "activity name" not in blob and "activity id" not in blob:
            return True
    return False


def is_projects_primary_workspace(blob: str) -> bool:
    if "layout:project" in blob or "layout:projects" in blob:
        return True
    if "project id" in blob and "project name" in blob:
        if "activity name" not in blob and "wbs code" not in blob:
            return True
    return False


def confirms_activities_workspace(
    entries: List[Dict[str, Any]],
    min_confidence: float,
) -> Tuple[bool, List[str]]:
    blob = collect_text_blob(entries, min_confidence)
    if is_wbs_primary_workspace(blob) or is_projects_primary_workspace(blob):
        return False, activities_indicator_hits(blob)

    hits = activities_indicator_hits(blob)
    activity_grid = sum(
        1 for h in ("activity name", "activity id", "layout:activ") if h in blob
    )
    if activity_grid >= 1 and ("start" in blob or "finish" in blob):
        return len(hits) >= 2, hits
    if "activity name" in blob and "start" in blob and "finish" in blob:
        return True, hits
    return False, hits


def confirm_project_open(
    entries: List[Dict[str, Any]],
    project_name: str,
    window_title: str,
    min_confidence: float,
) -> Tuple[bool, str, List[str]]:
    blob = collect_text_blob(entries, min_confidence)
    words: List[str] = []

    if "no current project" in normalize_text(window_title) or "no current project" in blob:
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
    if title_match or project_name_in_ocr(blob, project_name):
        return True, f"Project '{project_name}' likely open", words

    return False, f"Project '{project_name}' not found in title or OCR", words


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
    if not is_easyocr_available():
        return {"ok": False, "error": "EasyOCR not available"}

    raw = run_easyocr(capture["image_path"])
    ocr_path = str(evidence.ocr_dir / f"{label}_ocr.json")
    save_ocr_results(raw, ocr_path, metadata=capture.get("metadata"))
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

    unsafe, unsafe_reason = detect_unsafe_popup(classification, entries, min_confidence)
    popup_path = evidence.popup_dir / f"{label}_popup.json"
    write_json(
        popup_path,
        {
            "popup_buttons": classification.get("popup_buttons"),
            "unsafe_check": [unsafe, unsafe_reason],
        },
    )
    evidence.popup_files.append(str(popup_path))

    return {
        "ok": True,
        "entries": entries,
        "classification": classification,
        "screen_state": classification.get("screen_state", "unknown"),
        "unsafe": unsafe,
        "unsafe_reason": unsafe_reason,
    }


def navigate_to_activities(evidence: RunEvidence) -> None:
    evidence.steps.append("navigate: Alt+P, A (Project -> Activities)")
    keyboard_tools.hotkey("alt", "p")
    time.sleep(0.5)
    keyboard_tools.press_key("a")
    time.sleep(STABILITY_WAIT)


def finish_result(
    evidence: RunEvidence,
    project_name: str,
    status: str,
    reason: str,
    *,
    window_title_before: str = "",
    window_title_after: str = "",
    before_screen_state: str = "",
    after_screen_state: str = "",
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
        "window_title_before": window_title_before,
        "window_title_after": window_title_after,
        "screenshots": evidence.screenshots,
        "ocr_files": evidence.ocr_files,
        "classification_files": evidence.classification_files,
        "popup_files": evidence.popup_files,
        "before_screen_state": before_screen_state,
        "after_screen_state": after_screen_state,
        "confirmation_words": confirmation_words or [],
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
        "# M06 Go To Activities Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project name: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Window title before: {result.get('window_title_before', '')}",
        f"- Window title after: {result.get('window_title_after', '')}",
        f"- Before screen state: {result.get('before_screen_state', '')}",
        f"- After screen state: {result.get('after_screen_state', '')}",
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

    lines.extend(
        [
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
    if result["status"] in ("PASS", "PASS_ALREADY_IN_ACTIVITIES"):
        lines.append("Ready for M06 hard testing.")
    elif result["status"].startswith("MANUAL_REVIEW"):
        lines.append("Review screenshots and popup evidence before retrying.")
    elif result["status"] == "FAIL_PROJECT_NOT_OPEN":
        lines.append("Open the project with M03, then re-run TY_TEST_M06_GO_TO_ACTIVITIES.bat")
    else:
        lines.append("Fix the reported issue and re-run TY_TEST_M06_GO_TO_ACTIVITIES.bat")

    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_m06(
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
                before_screen_state="unknown",
                manual_review_required=bool(polluted),
            )

        before_state = before["screen_state"]
        if before.get("unsafe"):
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_UNSAFE_POPUP",
                before.get("unsafe_reason", "unsafe popup"),
                window_title_before=window_title_before,
                before_screen_state=before_state,
                manual_review_required=True,
            )

        open_ok, open_reason, open_words = confirm_project_open(
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
                confirmation_words=open_words,
            )

        in_activities, activity_hits = confirms_activities_workspace(
            before["entries"], min_confidence
        )
        if before_state == "activities_workspace" and in_activities:
            words = list(open_words) + activity_hits
            return finish_result(
                evidence,
                project_name,
                "PASS_ALREADY_IN_ACTIVITIES",
                f"Already in Activities workspace for '{project_name}'",
                window_title_before=window_title_before,
                window_title_after=window_title_before,
                before_screen_state=before_state,
                after_screen_state=before_state,
                confirmation_words=words,
            )

        navigate_to_activities(evidence)

        fresh = get_fresh_p6_rect(p6_keyword)
        if fresh.get("success") and fresh.get("rect"):
            p6_rect = fresh["rect"]
        window_title_after = window_tools.get_window_state(p6_keyword).get("title") or ""

        evidence.steps.append("capture after_action")
        after = capture_and_ocr_step(evidence, "02_after", p6_rect, config, screen_rule)
        if not after.get("ok"):
            polluted = after.get("polluted")
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_CANNOT_CONFIRM" if polluted else "FAIL_P6_WINDOW_NOT_READY",
                after.get("error", "after capture failed"),
                window_title_before=window_title_before,
                window_title_after=window_title_after,
                before_screen_state=before_state,
                manual_review_required=bool(polluted),
            )

        if after.get("unsafe"):
            return finish_result(
                evidence,
                project_name,
                "MANUAL_REVIEW_UNSAFE_POPUP",
                after.get("unsafe_reason", "unsafe popup after navigation"),
                window_title_before=window_title_before,
                window_title_after=window_title_after,
                before_screen_state=before_state,
                after_screen_state=after["screen_state"],
                manual_review_required=True,
            )

        in_activities_after, activity_hits_after = confirms_activities_workspace(
            after["entries"], min_confidence
        )
        after_state = after["screen_state"]
        words = list(open_words) + activity_hits_after

        if in_activities_after or after_state == "activities_workspace":
            return finish_result(
                evidence,
                project_name,
                "PASS",
                f"Navigated to Activities workspace for '{project_name}'",
                window_title_before=window_title_before,
                window_title_after=window_title_after,
                before_screen_state=before_state,
                after_screen_state=after_state,
                confirmation_words=words,
            )

        return finish_result(
            evidence,
            project_name,
            "FAIL_ACTIVITIES_NOT_FOUND",
            "Activities workspace indicators not found after Alt+P, A navigation",
            window_title_before=window_title_before,
            window_title_after=window_title_after,
            before_screen_state=before_state,
            after_screen_state=after_state,
            confirmation_words=words,
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
    parser = argparse.ArgumentParser(description="M06 Go To Activities")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    args = parser.parse_args()

    result = run_m06(args.project.strip())
    print(f"M06 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Window title before: {result.get('window_title_before', '')}")
    print(f"Window title after: {result.get('window_title_after', '')}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")
    if result["status"] in ("PASS", "PASS_ALREADY_IN_ACTIVITIES"):
        return 0
    if result["status"].startswith("MANUAL_REVIEW"):
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
