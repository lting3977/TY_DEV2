"""Shared helpers for Phase 1 eye + hand stability tests — P6-only OCR."""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from accessibility.brain.classifier import (
    classify_p6_presence,
    classify_popup_buttons,
    classify_unknown_screen,
    classify_workspace,
)
from eye.ocr import (
    check_ocr_pollution,
    collect_text_blob,
    detect_pollution,
    find_keywords,
    is_easyocr_available,
    ocr_to_entries,
    run_easyocr,
    save_ocr_results,
)
from eye.screenshot import (
    P6Rect,
    capture_p6_window_only,
    crop_center_percent_of_image,
    rect_from_window_state,
    validate_p6_rect,
)
from hand.p6_prepare import get_fresh_p6_rect, prepare_p6_for_test
from accessibility.hand import window_tools


PROJECT_ROOT = Path(r"C:\TY_DEV2")
CONFIG_PATH = PROJECT_ROOT / "01_config" / "ty_config.json"
SCREEN_RULE_PATH = PROJECT_ROOT / "03_screen_library" / "p6_open_project" / "screen_rule.json"

FAIL_P6_WINDOW_NOT_READY = "FAIL_P6_WINDOW_NOT_READY"
FAIL_P6_REASON = "P6 window rectangle unavailable or invalid; OCR skipped to avoid desktop pollution"

ALLOWED_SCORE_STATUSES = {
    "PASS",
    "MANUAL_REVIEW_EXPECTED",
    "CONTROLLED_UNKNOWN",
    "FAIL",
    "FALSE_MANUAL_REVIEW",
    "OCR_POLLUTION",
    "CRASH",
    FAIL_P6_WINDOW_NOT_READY,
}


@dataclass
class TestContext:
    run_id: str
    run_root: Path
    config: Dict[str, Any]
    screen_rule: Dict[str, Any]
    p6_keyword: str
    min_confidence: float


@dataclass
class TestArtifacts:
    test_id: str
    slug: str
    name: str
    folder: Path
    screenshots_dir: Path
    ocr_dir: Path
    classification_dir: Path
    popup_dir: Path
    notes: List[str] = field(default_factory=list)
    hand_actions: List[str] = field(default_factory=list)


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_test_folder(run_root: Path, test_id: str, slug: str) -> TestArtifacts:
    folder = run_root / f"test_{test_id}_{slug}"
    screenshots_dir = folder / "screenshots"
    ocr_dir = folder / "ocr"
    classification_dir = folder / "classification"
    popup_dir = folder / "popup"
    for path in (screenshots_dir, ocr_dir, classification_dir, popup_dir):
        path.mkdir(parents=True, exist_ok=True)
    return TestArtifacts(
        test_id=test_id,
        slug=slug,
        name=slug.replace("_", " "),
        folder=folder,
        screenshots_dir=screenshots_dir,
        ocr_dir=ocr_dir,
        classification_dir=classification_dir,
        popup_dir=popup_dir,
    )


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def _build_not_ready_analysis(
    reason: str,
    window_state: Optional[Dict[str, Any]] = None,
    prep: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "ocr_skipped": True,
        "status": FAIL_P6_WINDOW_NOT_READY,
        "reason": reason,
        "window_state": window_state,
        "prep": prep,
        "classification": {
            "p6_presence": {"level": "none", "hits": [], "blob_excerpt": ""},
            "workspace": "unknown",
            "popup_buttons": {},
            "open_project_hits": {},
            "open_project_visible": False,
            "unknown_screen": True,
            "pollution_hits": [],
            "desktop_pollution_hits": [],
            "ocr_blob_excerpt": "",
        },
        "entries": [],
        "pollution_check": {"polluted": False, "pollution_words": [], "status": "OK"},
    }


def capture_and_analyze(
    ctx: TestContext,
    artifacts: TestArtifacts,
    label: str = "capture",
    *,
    prepare: bool = True,
    p6_rect: Optional[P6Rect] = None,
    use_popup_crop: bool = False,
    save_debug_fullscreen: bool = False,
    require_p6_foreground: bool = False,
) -> Dict[str, Any]:
    """
    Capture P6-window-only screenshot and run OCR. Never OCRs full desktop.

    If prepare=True, calls prepare_p6_for_test() for a fresh validated rectangle.
    If rectangle is invalid, returns FAIL_P6_WINDOW_NOT_READY without OCR.
    """
    prep = None
    window_state = window_tools.get_window_state(ctx.p6_keyword)

    if prepare:
        artifacts.hand_actions.append("prepare_p6_for_test")
        prep = prepare_p6_for_test(ctx.p6_keyword)
        artifacts.notes.append(f"prepare_p6: {prep.get('message')}")
        if prep.get("success") and prep.get("rect"):
            p6_rect = prep["rect"]
            window_state = prep.get("window_state", window_state)
        else:
            return _build_not_ready_analysis(
                prep.get("message", FAIL_P6_REASON),
                window_state=window_state,
                prep=prep,
            )
    elif p6_rect is None:
        fresh = get_fresh_p6_rect(ctx.p6_keyword)
        if fresh.get("success") and fresh.get("rect"):
            p6_rect = fresh["rect"]
            window_state = fresh.get("window_state", window_state)
        else:
            return _build_not_ready_analysis(
                fresh.get("message", FAIL_P6_REASON),
                window_state=window_state,
            )

    valid, reason = validate_p6_rect(p6_rect, is_minimized=window_state.get("is_minimized"))
    if not valid:
        return _build_not_ready_analysis(reason, window_state=window_state, prep=prep)

    if require_p6_foreground:
        active = window_tools.get_active_window_title() or ""
        if ctx.p6_keyword.lower() not in active.lower():
            return _build_not_ready_analysis(
                f"P6 not foreground (active: {active}); OCR skipped",
                window_state=window_state,
                prep=prep,
            )

    metadata_path = artifacts.ocr_dir / f"{label}_capture_metadata.json"
    capture = capture_p6_window_only(
        artifacts.screenshots_dir,
        f"{label}_p6_crop.png",
        p6_rect,
        metadata_path=metadata_path,
        save_debug_fullscreen_label=label if save_debug_fullscreen else None,
    )

    if not capture["success"]:
        return _build_not_ready_analysis(
            capture.get("error", FAIL_P6_REASON),
            window_state=window_state,
            prep=prep,
        )

    ocr_image = capture["image_path"]
    popup_crop_path = None
    if use_popup_crop:
        popup_crop_path = str(artifacts.screenshots_dir / f"{label}_popup_crop.png")
        crop_center_percent_of_image(
            ocr_image,
            popup_crop_path,
            ctx.screen_rule["crop_region_percent"],
        )
        ocr_image = popup_crop_path
        popup_meta = dict(capture["metadata"])
        popup_meta["source"] = "p6_popup_crop"
        popup_meta["parent_p6_crop"] = capture["image_path"]
        write_json(artifacts.ocr_dir / f"{label}_popup_capture_metadata.json", popup_meta)

    raw_ocr = run_easyocr(ocr_image)
    ocr_json_path = artifacts.ocr_dir / f"{label}_ocr.json"
    save_ocr_results(raw_ocr, str(ocr_json_path), metadata=capture["metadata"])
    entries = ocr_to_entries(raw_ocr)

    pollution_keywords = ctx.config.get("pollution_keywords", [])
    pollution_check = check_ocr_pollution(entries, pollution_keywords, ctx.min_confidence)
    pollution_hits = pollution_check["pollution_words"]
    desktop_hits = detect_pollution(
        entries, ctx.config.get("desktop_pollution_keywords", []), ctx.min_confidence
    )

    p6_presence = classify_p6_presence(entries, ctx.config["p6_recognition_keywords"], ctx.min_confidence)
    workspace = classify_workspace(entries, ctx.min_confidence)
    popup_buttons = classify_popup_buttons(entries, ctx.config["popup_button_keywords"], ctx.min_confidence)
    open_project_hits = find_keywords(
        entries,
        ctx.screen_rule.get("recognition_text", []),
        ctx.min_confidence,
    )
    open_project_visible = sum(1 for v in open_project_hits.values() if v) >= 3

    classification = {
        "p6_presence": p6_presence,
        "workspace": workspace,
        "popup_buttons": popup_buttons,
        "open_project_hits": open_project_hits,
        "open_project_visible": open_project_visible,
        "unknown_screen": classify_unknown_screen(p6_presence, popup_buttons, open_project_visible),
        "pollution_hits": pollution_hits,
        "desktop_pollution_hits": desktop_hits,
        "ocr_blob_excerpt": collect_text_blob(entries, ctx.min_confidence)[:1000],
    }
    write_json(artifacts.classification_dir / f"{label}_classification.json", classification)
    write_json(artifacts.popup_dir / f"{label}_popup.json", popup_buttons)

    return {
        "ocr_skipped": False,
        "status": "OK",
        "p6_crop": capture["image_path"],
        "popup_crop": popup_crop_path,
        "ocr_json": str(ocr_json_path),
        "capture_metadata": capture["metadata"],
        "entries": entries,
        "classification": classification,
        "pollution_check": pollution_check,
        "window_state": window_state,
        "prep": prep,
    }


def analysis_not_ready(analysis: Dict[str, Any]) -> bool:
    return analysis.get("ocr_skipped") or analysis.get("status") == FAIL_P6_WINDOW_NOT_READY


def finish_test(
    artifacts: TestArtifacts,
    status: str,
    message: str,
    expected_status: Optional[str] = None,
    analysis: Optional[Dict[str, Any]] = None,
    score: Optional[int] = None,
) -> Dict[str, Any]:
    normalized = status if status in ALLOWED_SCORE_STATUSES else "FAIL"
    if score is None:
        score = 1 if normalized in {
            "PASS",
            "MANUAL_REVIEW_EXPECTED",
            "CONTROLLED_UNKNOWN",
            FAIL_P6_WINDOW_NOT_READY,
        } else 0

    result = {
        "test_id": artifacts.test_id,
        "test_slug": artifacts.slug,
        "test_name": artifacts.name,
        "status": normalized,
        "expected_status": expected_status,
        "score": score,
        "message": message,
        "hand_actions": artifacts.hand_actions,
        "notes": artifacts.notes,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "analysis_summary": {
            "window_state": (analysis or {}).get("window_state"),
            "classification": (analysis or {}).get("classification"),
            "ocr_skipped": (analysis or {}).get("ocr_skipped", False),
            "pollution_words": (analysis or {}).get("pollution_check", {}).get("pollution_words", []),
        },
    }
    write_json(artifacts.folder / "result.json", result)

    lines = [
        f"# Test {artifacts.test_id} — {artifacts.name}",
        "",
        f"**Status:** {normalized}",
        f"**Score:** {score}",
        f"**Message:** {message}",
        "",
        "## Hand actions",
    ]
    if artifacts.hand_actions:
        lines.extend(f"- {action}" for action in artifacts.hand_actions)
    else:
        lines.append("- none")

    lines.extend(["", "## Notes"])
    if artifacts.notes:
        lines.extend(f"- {note}" for note in artifacts.notes)
    else:
        lines.append("- none")

    if analysis:
        if analysis.get("ocr_skipped"):
            lines.extend(["", "## OCR", f"- Skipped: {analysis.get('reason', FAIL_P6_REASON)}"])
        if analysis.get("capture_metadata"):
            meta = analysis["capture_metadata"]
            lines.extend(
                [
                    "",
                    "## Capture metadata",
                    f"- source: {meta.get('source')}",
                    f"- p6_rect: {meta.get('p6_rect')}",
                    f"- used_for_ocr: {meta.get('used_for_ocr')}",
                ]
            )
        if analysis.get("classification"):
            cls = analysis["classification"]
            lines.extend(
                [
                    "",
                    "## Classification",
                    f"- P6 presence: {cls.get('p6_presence', {}).get('level')}",
                    f"- Workspace: {cls.get('workspace')}",
                    f"- Open project visible: {cls.get('open_project_visible')}",
                    f"- Pollution hits: {cls.get('pollution_hits')}",
                ]
            )

    (artifacts.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def run_test_case(
    ctx: TestContext,
    test_id: str,
    slug: str,
    name: str,
    runner: Callable[[TestContext, TestArtifacts], Dict[str, Any]],
) -> Dict[str, Any]:
    artifacts = build_test_folder(ctx.run_root, test_id, slug)
    artifacts.name = name
    try:
        if not is_easyocr_available():
            return finish_test(
                artifacts,
                "FAIL",
                "EasyOCR not available — install easyocr opencv-python",
                score=0,
            )
        return runner(ctx, artifacts)
    except Exception as exc:  # noqa: BLE001
        artifacts.notes.append(traceback.format_exc())
        return finish_test(artifacts, "CRASH", f"Unhandled exception: {exc}", score=0)


def score_from_expectation(actual: str, expected: str, explanation: str) -> Dict[str, Any]:
    if actual == "OCR_POLLUTION":
        return {"status": actual, "score": 0, "message": explanation}
    if actual in {"PASS", "MANUAL_REVIEW_EXPECTED", "CONTROLLED_UNKNOWN", FAIL_P6_WINDOW_NOT_READY}:
        return {"status": actual, "score": 1, "message": explanation}
    if actual == "FALSE_MANUAL_REVIEW":
        return {"status": actual, "score": 0, "message": explanation}
    return {"status": actual if actual in ALLOWED_SCORE_STATUSES else "FAIL", "score": 0, "message": explanation}


def finish_from_not_ready(
    artifacts: TestArtifacts,
    analysis: Dict[str, Any],
    expected: str,
    message: str,
) -> Dict[str, Any]:
    scored = score_from_expectation(FAIL_P6_WINDOW_NOT_READY, expected, message)
    return finish_test(
        artifacts,
        scored["status"],
        scored["message"],
        expected,
        analysis,
        scored["score"],
    )


def check_pollution(analysis: Dict[str, Any], artifacts: TestArtifacts) -> Optional[str]:
    if analysis_not_ready(analysis):
        return None
    pollution = analysis.get("pollution_check", {}).get("pollution_words") or []
    if not pollution:
        pollution = analysis["classification"].get("pollution_hits") or []
    if pollution:
        artifacts.notes.append(f"OCR pollution detected: {pollution}")
        return "OCR_POLLUTION"
    return None
