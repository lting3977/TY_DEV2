"""OCR utilities with optional EasyOCR support."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Union


def is_easyocr_available() -> bool:
    try:
        import easyocr  # noqa: F401
        return True
    except ImportError:
        return False


def normalize_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip()).lower()


_OCR_READER = None


def run_easyocr(
    image_path: str,
    languages: List[str] | None = None,
) -> Union[List[Any], Dict[str, str]]:
    global _OCR_READER
    if languages is None:
        languages = ["en"]

    if not is_easyocr_available():
        return {"error": "EasyOCR is not installed. Install with: pip install easyocr"}

    import easyocr

    if _OCR_READER is None:
        _OCR_READER = easyocr.Reader(languages, gpu=False, verbose=False)
    return _OCR_READER.readtext(image_path)


def ocr_to_entries(results: Union[List[Any], Dict[str, str]]) -> List[Dict[str, Any]]:
    if isinstance(results, dict) and "error" in results:
        return []

    entries: List[Dict[str, Any]] = []
    for entry in results:
        if len(entry) >= 3:
            bbox, text, confidence = entry[0], entry[1], entry[2]
            bbox_serializable = [
                [float(point[0]), float(point[1])] for point in bbox
            ]
            entries.append(
                {
                    "bbox": bbox_serializable,
                    "text": str(text),
                    "confidence": float(confidence),
                    "normalized": normalize_text(str(text)),
                }
            )
    return entries


def save_ocr_results(results: Union[List[Any], Dict[str, str]], output_path: str) -> str:
    folder = os.path.dirname(output_path)
    if folder:
        os.makedirs(folder, exist_ok=True)

    if isinstance(results, dict) and "error" in results:
        serializable: Any = results
    else:
        serializable = ocr_to_entries(results)

    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(serializable, handle, indent=2, ensure_ascii=False)
    return output_path


def collect_text_blob(entries: List[Dict[str, Any]], min_confidence: float = 0.0) -> str:
    texts = [
        entry["normalized"]
        for entry in entries
        if entry.get("confidence", 0.0) >= min_confidence
    ]
    return " ".join(texts)


def find_keywords(
    entries: List[Dict[str, Any]],
    keywords: List[str],
    min_confidence: float = 0.5,
) -> Dict[str, bool]:
    blob = collect_text_blob(entries, min_confidence)
    found: Dict[str, bool] = {}
    for keyword in keywords:
        norm = normalize_text(keyword)
        found[keyword] = norm in blob or any(norm in e["normalized"] for e in entries if e["confidence"] >= min_confidence)
    return found


def detect_pollution(
    entries: List[Dict[str, Any]],
    pollution_keywords: List[str],
    min_confidence: float = 0.5,
) -> List[str]:
    hits: List[str] = []
    for keyword in pollution_keywords:
        norm = normalize_text(keyword)
        for entry in entries:
            if entry["confidence"] < min_confidence:
                continue
            if norm in entry["normalized"]:
                hits.append(keyword)
                break
    return hits
