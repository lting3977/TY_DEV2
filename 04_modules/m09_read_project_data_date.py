"""
M09 — Read Project Data Date (Phase 8).

Read-only capture of the visible P6 Data Date from Activities workspace / status area.
P6-window-only OCR. No schedule edit, data date change, or data modification.
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
    load_json,
    write_json,
)
from m07_read_activity_table_snapshot import ensure_activities_workspace  # noqa: E402

MODULE_NAME = "m09_read_project_data_date"
TEST_OCR_P6_HEIGHT = 900
FOOTER_Y_RATIO = 0.78
ROW_Y_TOLERANCE = 16
LABEL_Y_TOLERANCE = 18

DATA_DATE_LABELS = (
    "data date",
    "current data date",
    "project data date",
    "status date",
)
STRONG_LABELS = ("data date", "current data date", "project data date")
FOOTER_CONTEXT_WORDS = ("access mode", "baseline", "user", "filter", "db:")

DATE_REGEXES = (
    re.compile(r"\d{1,2}[-/\s][A-Za-z]{3}[-/\s]?\d{2,4}", re.I),
    re.compile(r"\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}", re.I),
    re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}"),
)
INLINE_LABEL_DATE = re.compile(
    r"(data\s*date|current\s*data\s*date|project\s*data\s*date|status\s*date)"
    r"\s*[:\-]?\s*"
    r"(\d{1,2}[-/\s][A-Za-z]{3}[-/\s]?\d{2,4}|\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}|\d{1,2}/\d{1,2}/\d{2,4})",
    re.I,
)
MONTH_NAMES = (
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
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


def looks_like_date_text(raw: str) -> bool:
    text = (raw or "").strip().rstrip(",")
    if not text or len(text) < 6:
        return False
    if not any(rx.search(text) for rx in DATE_REGEXES):
        return False
    lower = text.lower()
    return any(m in lower for m in MONTH_NAMES) or "/" in text


def normalize_date_candidate(raw: str) -> Optional[str]:
    text = (raw or "").strip().rstrip(",")
    if not looks_like_date_text(text):
        return None
    m = re.search(
        r"(\d{1,2})[-/\s]?([A-Za-z]{3})[-/\s]?(\d{2,4})?",
        text,
        re.I,
    )
    if m:
        day, mon, year = m.group(1), m.group(2).title()[:3], m.group(3) or ""
        if year:
            return f"{day}-{mon}-{year}"
        return f"{day}-{mon}"
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", text)
    if m:
        return f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
    return None


def entry_contains_label(text: str) -> Optional[str]:
    norm = normalize_text(text)
    for label in DATA_DATE_LABELS:
        if label in norm:
            return label
    if re.search(r"\bdd\b", norm) and (":" in text or "date" in norm):
        return "dd"
    return None


def footer_entry(entry: Dict[str, Any], p6_height: int) -> bool:
    _x, y = bbox_center(entry)
    return y >= p6_height * FOOTER_Y_RATIO


def find_dates_in_text(text: str) -> List[str]:
    found: List[str] = []
    for rx in DATE_REGEXES:
        for match in rx.finditer(text):
            candidate = match.group(0).strip().rstrip(",")
            if looks_like_date_text(candidate) and candidate not in found:
                found.append(candidate)
    return found


def score_candidate(
    *,
    date_raw: str,
    label: str,
    source: str,
    in_footer: bool,
    adjacent_to_label: bool,
    inline_with_label: bool,
) -> float:
    if inline_with_label and label in STRONG_LABELS:
        return 1.0
    if adjacent_to_label and label in STRONG_LABELS:
        return 1.0
    if inline_with_label:
        return 0.9
    if adjacent_to_label:
        return 0.9
    if in_footer and label in STRONG_LABELS:
        return 0.85
    if in_footer and any(w in source.lower() for w in FOOTER_CONTEXT_WORDS):
        return 0.75
    if label in STRONG_LABELS:
        return 0.6
    if in_footer and looks_like_date_text(date_raw):
        return 0.75
    if looks_like_date_text(date_raw):
        return 0.5
    return 0.3


def extract_data_date_candidates(
    entries: List[Dict[str, Any]],
    min_confidence: float,
    p6_height: int,
) -> Dict[str, Any]:
    usable = [e for e in entries if e.get("confidence", 0) >= min_confidence and e.get("text")]
    blob = collect_text_blob(usable, min_confidence)
    candidates: List[Dict[str, Any]] = []
    label_visible = any(lbl in blob for lbl in STRONG_LABELS) or "status date" in blob

    for match in INLINE_LABEL_DATE.finditer(blob):
        label = normalize_text(match.group(1))
        date_raw = match.group(2).strip().rstrip(",")
        candidates.append(
            {
                "date_raw": date_raw,
                "date_normalized_candidate": normalize_date_candidate(date_raw),
                "label": label,
                "source": "inline_blob",
                "context": match.group(0),
                "in_footer": True,
                "adjacent_to_label": True,
                "inline_with_label": True,
                "confidence": score_candidate(
                    date_raw=date_raw,
                    label=label,
                    source=match.group(0),
                    in_footer=True,
                    adjacent_to_label=True,
                    inline_with_label=True,
                ),
            }
        )

    for entry in usable:
        text = entry.get("text", "").strip()
        label = entry_contains_label(text)
        if not label:
            continue
        _x, y = bbox_center(entry)
        in_footer = footer_entry(entry, p6_height)
        inline_dates = find_dates_in_text(text)
        if inline_dates:
            for date_raw in inline_dates:
                candidates.append(
                    {
                        "date_raw": date_raw,
                        "date_normalized_candidate": normalize_date_candidate(date_raw),
                        "label": label,
                        "source": "label_entry_inline",
                        "context": text,
                        "in_footer": in_footer,
                        "adjacent_to_label": True,
                        "inline_with_label": True,
                        "confidence": score_candidate(
                            date_raw=date_raw,
                            label=label,
                            source=text,
                            in_footer=in_footer,
                            adjacent_to_label=True,
                            inline_with_label=True,
                        ),
                    }
                )
            continue

        nearest: Optional[Dict[str, Any]] = None
        nearest_dist = 9999.0
        for other in usable:
            other_text = other.get("text", "").strip()
            if other is entry or not looks_like_date_text(other_text):
                continue
            ox, oy = bbox_center(other)
            if abs(oy - y) > LABEL_Y_TOLERANCE:
                continue
            dist = abs(ox - _x)
            if dist < nearest_dist:
                nearest_dist = dist
                nearest = other

        if nearest is not None:
            date_raw = nearest.get("text", "").strip().rstrip(",")
            candidates.append(
                {
                    "date_raw": date_raw,
                    "date_normalized_candidate": normalize_date_candidate(date_raw),
                    "label": label,
                    "source": "adjacent_entry",
                    "context": f"{text} | {date_raw}",
                    "in_footer": in_footer or footer_entry(nearest, p6_height),
                    "adjacent_to_label": True,
                    "inline_with_label": False,
                    "confidence": score_candidate(
                        date_raw=date_raw,
                        label=label,
                        source=text,
                        in_footer=in_footer,
                        adjacent_to_label=True,
                        inline_with_label=False,
                    ),
                }
            )

    for entry in usable:
        text = entry.get("text", "").strip()
        if entry_contains_label(text):
            continue
        if not footer_entry(entry, p6_height):
            continue
        for date_raw in find_dates_in_text(text):
            if any(c["date_raw"] == date_raw for c in candidates):
                continue
            lower = text.lower()
            label = ""
            if "baseline" in lower:
                label = "baseline_context"
            elif "access mode" in lower:
                label = "access_mode_context"
            candidates.append(
                {
                    "date_raw": date_raw,
                    "date_normalized_candidate": normalize_date_candidate(date_raw),
                    "label": label,
                    "source": "footer_entry",
                    "context": text,
                    "in_footer": True,
                    "adjacent_to_label": False,
                    "inline_with_label": False,
                    "confidence": score_candidate(
                        date_raw=date_raw,
                        label=label,
                        source=text,
                        in_footer=True,
                        adjacent_to_label=False,
                        inline_with_label=False,
                    ),
                }
            )

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for cand in sorted(candidates, key=lambda c: c["confidence"], reverse=True):
        key = (cand.get("date_raw"), cand.get("label"), cand.get("source"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cand)

    best = deduped[0] if deduped else None
    return {
        "label_visible": label_visible,
        "candidate_count": len(deduped),
        "candidates": deduped,
        "best": best,
        "ocr_blob_excerpt": blob[:1500],
    }


def decide_status(extraction: Dict[str, Any]) -> Tuple[str, str, bool, str, str, float]:
    best = extraction.get("best")
    label_visible = extraction.get("label_visible", False)
    candidates = extraction.get("candidates", [])

    if not candidates:
        if label_visible:
            return (
                "MANUAL_REVIEW_CANNOT_CONFIRM",
                "Data Date label visible in OCR but no clear date candidate found",
                False,
                "",
                "",
                0.0,
            )
        return (
            "FAIL_DATA_DATE_NOT_FOUND",
            "No Data Date label or date candidate found in Activities workspace OCR",
            False,
            "",
            "",
            0.0,
        )

    confidence = float(best.get("confidence", 0.0))
    date_raw = best.get("date_raw", "")
    normalized = best.get("date_normalized_candidate") or ""
    label = best.get("label", "")
    strong_label = label in STRONG_LABELS or best.get("inline_with_label") or best.get("adjacent_to_label")

    if confidence >= 0.75 and strong_label and date_raw:
        return (
            "PASS",
            f"Data Date found: {date_raw} (confidence {confidence:.2f})",
            True,
            date_raw,
            normalized,
            confidence,
        )

    if confidence >= 0.5 or len(candidates) >= 1:
        return (
            "PASS_WITH_DATE_CANDIDATES",
            f"Data Date candidate(s) found; best={date_raw} confidence={confidence:.2f}",
            bool(date_raw),
            date_raw,
            normalized,
            confidence,
        )

    if label_visible:
        return (
            "MANUAL_REVIEW_CANNOT_CONFIRM",
            "Data Date label visible but confidence too low to confirm",
            False,
            date_raw,
            normalized,
            confidence,
        )

    return (
        "FAIL_DATA_DATE_NOT_FOUND",
        "No reliable Data Date candidate found",
        False,
        "",
        "",
        confidence,
    )


def save_empty_extractions(evidence: RunEvidence, note: str = "") -> None:
    save_extractions(
        evidence,
        {
            "candidate_count": 0,
            "label_visible": False,
            "candidates": [],
            "ocr_blob_excerpt": note,
        },
        {
            "data_date_found": False,
            "data_date_raw": "",
            "data_date_normalized_candidate": "",
            "confidence": 0.0,
            "candidate_count": 0,
            "best_candidate": None,
            "label_visible": False,
        },
    )


def save_extractions(evidence: RunEvidence, extraction: Dict[str, Any], result_payload: Dict[str, Any]) -> None:
    candidates_path = evidence.extracted_dir / "data_date_candidates.json"
    write_json(
        candidates_path,
        {
            "candidate_count": extraction.get("candidate_count", 0),
            "label_visible": extraction.get("label_visible", False),
            "candidates": extraction.get("candidates", []),
            "ocr_blob_excerpt": extraction.get("ocr_blob_excerpt", ""),
        },
    )
    evidence.extracted_files.append(str(candidates_path))

    result_path = evidence.extracted_dir / "data_date_result.json"
    write_json(result_path, result_payload)
    evidence.extracted_files.append(str(result_path))


def finish_result(
    evidence: RunEvidence,
    project_name: str,
    status: str,
    reason: str,
    *,
    window_title: str = "",
    screen_state: str = "",
    data_date_found: bool = False,
    data_date_raw: str = "",
    data_date_normalized_candidate: str = "",
    confidence: float = 0.0,
    candidate_count: int = 0,
    candidates: Optional[List[Any]] = None,
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
        "data_date_found": data_date_found,
        "data_date_raw": data_date_raw,
        "data_date_normalized_candidate": data_date_normalized_candidate,
        "confidence": confidence,
        "candidate_count": candidate_count,
        "candidates": candidates or [],
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
        "# M09 Read Project Data Date Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project name: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Window title: {result.get('window_title', '')}",
        f"- Screen state: {result.get('screen_state', '')}",
        f"- Data date found: {result.get('data_date_found')}",
        f"- Data date raw: {result.get('data_date_raw', '')}",
        f"- Data date normalized candidate: {result.get('data_date_normalized_candidate', '')}",
        f"- Confidence: {result.get('confidence', 0.0)}",
        f"- Candidate list: {result.get('candidates', [])}",
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
    if result["status"] in ("PASS", "PASS_WITH_DATE_CANDIDATES"):
        lines.append("Ready for M09 hard testing.")
    elif result["status"].startswith("MANUAL_REVIEW"):
        lines.append("Review screenshots and popup evidence before retrying.")
    else:
        lines.append("Fix the reported issue and re-run TY_TEST_M09_READ_DATA_DATE.bat")

    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_synthetic_ocr_entries(
    lines: List[Tuple[str, Optional[int]]],
    *,
    p6_height: int = TEST_OCR_P6_HEIGHT,
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    y = 150
    for text, y_override in lines:
        cy = y_override if y_override is not None else y
        entries.append(
            {
                "bbox": [[10.0, float(cy)], [500.0, float(cy)], [500.0, float(cy + 18)], [10.0, float(cy + 18)]],
                "text": text,
                "confidence": 0.92,
                "normalized": normalize_text(text),
            }
        )
        if y_override is None:
            y += 36
    return entries


def write_test_ocr_fixture(
    evidence: RunEvidence,
    entries: List[Dict[str, Any]],
    label: str = "test_fixture",
) -> str:
    ocr_path = evidence.ocr_dir / f"{label}_ocr.json"
    payload = {
        "entries": entries,
        "capture_metadata": {
            "source": "test_fixture",
            "used_for_ocr": True,
            "p6_height": TEST_OCR_P6_HEIGHT,
        },
    }
    write_json(ocr_path, payload)
    evidence.ocr_files.append(str(ocr_path))
    return str(ocr_path)


def load_test_ocr_entries(
    ocr_json: Optional[str] = None,
    ocr_source_folder: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int, str]:
    path: Optional[Path] = None
    if ocr_json:
        path = Path(ocr_json)
    elif ocr_source_folder:
        folder = Path(ocr_source_folder)
        for name in ("test_ocr.json", "01_data_date_ocr.json"):
            candidate = folder / name
            if candidate.exists():
                path = candidate
                break
        if path is None:
            for candidate in sorted(folder.glob("*_ocr.json")):
                path = candidate
                break
    if path is None or not path.exists():
        raise FileNotFoundError("Test OCR source not found")

    data = load_json(path)
    entries = data.get("entries") or []
    meta = data.get("capture_metadata") or {}
    p6_height = int(meta.get("p6_height") or TEST_OCR_P6_HEIGHT)
    return entries, p6_height, str(path)


def process_data_date_from_entries(
    evidence: RunEvidence,
    project_name: str,
    entries: List[Dict[str, Any]],
    min_confidence: float,
    p6_height: int,
    *,
    window_title: str = "",
    screen_state: str = "test_ocr_source",
) -> Dict[str, Any]:
    evidence.steps.append("extract data date candidates")
    extraction = extract_data_date_candidates(entries, min_confidence, p6_height)
    status, reason, found, date_raw, normalized, confidence = decide_status(extraction)

    result_payload = {
        "data_date_found": found,
        "data_date_raw": date_raw,
        "data_date_normalized_candidate": normalized,
        "confidence": confidence,
        "candidate_count": extraction.get("candidate_count", 0),
        "best_candidate": extraction.get("best"),
        "label_visible": extraction.get("label_visible", False),
    }
    save_extractions(evidence, extraction, result_payload)

    manual = status.startswith("MANUAL_REVIEW")
    return finish_result(
        evidence,
        project_name,
        status,
        reason,
        window_title=window_title,
        screen_state=screen_state,
        data_date_found=found,
        data_date_raw=date_raw,
        data_date_normalized_candidate=normalized,
        confidence=confidence,
        candidate_count=extraction.get("candidate_count", 0),
        candidates=extraction.get("candidates", []),
        manual_review_required=manual,
    )


def run_m09(
    project_name: str,
    *,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
    ocr_json: Optional[str] = None,
    ocr_source_folder: Optional[str] = None,
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

    if ocr_json or ocr_source_folder:
        try:
            evidence.steps.append("test OCR source mode (skip P6)")
            entries, p6_height, source_path = load_test_ocr_entries(ocr_json, ocr_source_folder)
            evidence.steps.append(f"loaded test OCR: {source_path}")
            if not evidence.ocr_files:
                evidence.ocr_files.append(source_path)
            return process_data_date_from_entries(
                evidence,
                project_name,
                entries,
                min_confidence,
                p6_height,
                screen_state="test_ocr_source",
            )
        except Exception as exc:  # noqa: BLE001
            evidence.steps.append(f"test OCR load failed: {exc}")
            save_empty_extractions(evidence, str(exc))
            return finish_result(
                evidence,
                project_name,
                "ERROR",
                str(exc),
                screen_state="test_ocr_source",
                error=traceback.format_exc(),
            )

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
            save_empty_extractions(evidence, prep.get("message", ""))
            return finish_result(
                evidence,
                project_name,
                "FAIL_P6_WINDOW_NOT_READY",
                prep.get("message", "P6 window not ready"),
            )

        p6_rect: P6Rect = prep["rect"]
        p6_height = p6_rect.height
        window_title = window_tools.get_window_state(p6_keyword).get("title") or ""

        evidence.steps.append("capture data_date_screen")
        capture = capture_and_ocr_step(evidence, "01_data_date", p6_rect, config, screen_rule)
        if not capture.get("ok"):
            polluted = capture.get("polluted")
            save_empty_extractions(evidence, capture.get("error", ""))
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
            save_empty_extractions(evidence, open_reason)
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

        if working is not capture:
            p6_height = p6_rect.height

        return process_data_date_from_entries(
            evidence,
            project_name,
            working["entries"],
            min_confidence,
            p6_height,
            window_title=window_title,
            screen_state=screen_state,
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
    parser = argparse.ArgumentParser(description="M09 Read Project Data Date")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    parser.add_argument(
        "--ocr-json",
        default=None,
        help="Path to test OCR JSON fixture (skips P6; hard-test only)",
    )
    parser.add_argument(
        "--ocr-source-folder",
        default=None,
        help="Folder containing test OCR JSON (skips P6; hard-test only)",
    )
    args = parser.parse_args()

    result = run_m09(
        args.project.strip(),
        ocr_json=args.ocr_json,
        ocr_source_folder=args.ocr_source_folder,
    )
    print(f"M09 status: {result['status']}")
    print(f"Reason: {result['reason']}")
    print(f"Window title: {result.get('window_title', '')}")
    print(f"Screen state: {result.get('screen_state', '')}")
    print(f"Data date found: {result.get('data_date_found')}")
    print(f"Data date raw: {result.get('data_date_raw', '')}")
    print(f"Data date normalized: {result.get('data_date_normalized_candidate', '')}")
    print(f"Confidence: {result.get('confidence', 0.0)}")
    print(f"Candidate count: {result.get('candidate_count', 0)}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / MODULE_NAME}")
    if result["status"] in ("PASS", "PASS_WITH_DATE_CANDIDATES"):
        return 0
    if result["status"].startswith("MANUAL_REVIEW"):
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
