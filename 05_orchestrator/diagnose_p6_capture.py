"""
Diagnose P6 window crop capture reliability.

Tests pyautogui region, ImageGrab full+crop, and mss region capture.
Never marks full-screen images as safe for OCR.
"""

from __future__ import annotations

import json
import sys
import traceback
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
        ("accessibility.hand", acc / "hand"),
        ("hand", ROOT / "02_hand"),
        ("eye", ROOT / "02_eye"),
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

from accessibility.hand import window_tools  # noqa: E402
from eye.screenshot import P6Rect, validate_p6_rect  # noqa: E402
from hand.p6_prepare import prepare_p6_for_test  # noqa: E402

CONFIG_PATH = ROOT / "01_config" / "ty_config.json"


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def load_config() -> Dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def get_monitor_info() -> List[Dict[str, Any]]:
    monitors: List[Dict[str, Any]] = []
    try:
        import mss  # noqa: WPS433

        with mss.mss() as sct:
            for idx, mon in enumerate(sct.monitors):
                monitors.append({"index": idx, **mon})
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        monitors.append({"error": str(exc)})

    try:
        import pyautogui  # noqa: WPS433

        size = pyautogui.size()
        monitors.append({"source": "pyautogui.size", "width": size.width, "height": size.height})
    except Exception as exc:  # noqa: BLE001
        monitors.append({"pyautogui_size_error": str(exc)})

    return monitors


def try_pyautogui_region(p6_rect: P6Rect, out_path: Path) -> Tuple[bool, str]:
    try:
        import pyautogui  # noqa: WPS433

        pyautogui.FAILSAFE = True
        region = (p6_rect.left, p6_rect.top, p6_rect.width, p6_rect.height)
        image = pyautogui.screenshot(region=region)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(out_path))
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def try_imagegrab_full_crop(
    p6_rect: P6Rect,
    crop_path: Path,
    debug_full_path: Optional[Path],
) -> Tuple[bool, str]:
    try:
        from PIL import ImageGrab  # noqa: WPS433

        full = ImageGrab.grab(all_screens=True)
        if debug_full_path:
            debug_full_path.parent.mkdir(parents=True, exist_ok=True)
            full.save(str(debug_full_path))

        box = (
            p6_rect.left,
            p6_rect.top,
            p6_rect.left + p6_rect.width,
            p6_rect.top + p6_rect.height,
        )
        crop = full.crop(box)
        crop_path.parent.mkdir(parents=True, exist_ok=True)
        crop.save(str(crop_path))
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def try_mss_region(p6_rect: P6Rect, out_path: Path) -> Tuple[bool, str]:
    try:
        import mss  # noqa: WPS433
        from PIL import Image  # noqa: WPS433

        monitor = {
            "left": p6_rect.left,
            "top": p6_rect.top,
            "width": p6_rect.width,
            "height": p6_rect.height,
        }
        with mss.mss() as sct:
            shot = sct.grab(monitor)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(out_path))
        return True, ""
    except ImportError:
        return False, "mss not installed"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def choose_recommended(
    pyautogui_ok: bool,
    imagegrab_ok: bool,
    mss_ok: bool,
) -> str:
    if pyautogui_ok:
        return "pyautogui_region"
    if mss_ok:
        return "mss_region"
    if imagegrab_ok:
        return "imagegrab_full_crop_fallback"
    return "none"


def run_diagnostic() -> Dict[str, Any]:
    run_id = new_run_id()
    out_root = ROOT / "06_output" / "runs" / run_id / "diagnose_p6_capture"
    shots = out_root / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)

    config = load_config()
    p6_keyword = config.get("p6_window_title_keyword", "Primavera")

    result: Dict[str, Any] = {
        "run_id": run_id,
        "p6_window_found": False,
        "p6_title": "",
        "p6_rect": {},
        "monitor_info": [],
        "pyautogui_region_success": False,
        "pyautogui_region_error": "",
        "imagegrab_full_crop_success": False,
        "imagegrab_full_crop_error": "",
        "mss_region_success": False,
        "mss_region_error": "",
        "recommended_capture_method": "",
        "safe_for_ocr": False,
        "error": None,
        "screenshots": {},
    }

    try:
        prep = prepare_p6_for_test(p6_keyword)
        state = prep.get("window_state") or window_tools.get_window_state(p6_keyword)
        result["p6_window_found"] = bool(state.get("found"))
        result["p6_title"] = state.get("title") or ""
        result["monitor_info"] = get_monitor_info()
        result["prepare_message"] = prep.get("message", "")

        p6_rect: Optional[P6Rect] = prep.get("rect")
        if p6_rect:
            result["p6_rect"] = p6_rect.to_dict()

        valid, reason = validate_p6_rect(p6_rect, is_minimized=state.get("is_minimized"))
        if not valid or not p6_rect:
            result["error"] = reason or prep.get("message", "P6 rect invalid")
            _write_outputs(out_root, result)
            return result

        py_ok, py_err = try_pyautogui_region(p6_rect, shots / "p6_crop_pyautogui.png")
        result["pyautogui_region_success"] = py_ok
        result["pyautogui_region_error"] = py_err
        if py_ok:
            result["screenshots"]["p6_crop_pyautogui"] = str(shots / "p6_crop_pyautogui.png")

        ig_ok, ig_err = try_imagegrab_full_crop(
            p6_rect,
            shots / "p6_crop_imagegrab.png",
            shots / "debug_fullscreen_imagegrab.png",
        )
        result["imagegrab_full_crop_success"] = ig_ok
        result["imagegrab_full_crop_error"] = ig_err
        if ig_ok:
            result["screenshots"]["p6_crop_imagegrab"] = str(shots / "p6_crop_imagegrab.png")
        if (shots / "debug_fullscreen_imagegrab.png").exists():
            result["screenshots"]["debug_fullscreen_imagegrab"] = str(
                shots / "debug_fullscreen_imagegrab.png"
            )

        mss_ok, mss_err = try_mss_region(p6_rect, shots / "p6_crop_mss.png")
        result["mss_region_success"] = mss_ok
        result["mss_region_error"] = mss_err
        if mss_ok:
            result["screenshots"]["p6_crop_mss"] = str(shots / "p6_crop_mss.png")

        recommended = choose_recommended(py_ok, ig_ok, mss_ok)
        result["recommended_capture_method"] = recommended
        result["safe_for_ocr"] = recommended != "none"

    except Exception as exc:  # noqa: BLE001
        result["error"] = traceback.format_exc()

    _write_outputs(out_root, result)
    return result


def _write_outputs(out_root: Path, result: Dict[str, Any]) -> None:
    with (out_root / "result.json").open("w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)

    lines = [
        "# P6 Capture Diagnostic Report",
        "",
        f"- Run ID: {result.get('run_id', '')}",
        f"- P6 window found: {result.get('p6_window_found')}",
        f"- P6 title: {result.get('p6_title', '')}",
        f"- P6 rect: {result.get('p6_rect', {})}",
        f"- Prepare message: {result.get('prepare_message', '')}",
        "",
        "## Capture methods",
        f"- pyautogui region success: {result.get('pyautogui_region_success')}",
        f"- pyautogui error: {result.get('pyautogui_region_error', '')}",
        f"- ImageGrab full+crop success: {result.get('imagegrab_full_crop_success')}",
        f"- ImageGrab error: {result.get('imagegrab_full_crop_error', '')}",
        f"- mss region success: {result.get('mss_region_success')}",
        f"- mss error: {result.get('mss_region_error', '')}",
        "",
        f"- Recommended capture method: {result.get('recommended_capture_method', '')}",
        f"- Safe for OCR: {result.get('safe_for_ocr')}",
        "",
        "## Monitor info",
        json.dumps(result.get("monitor_info", []), indent=2),
        "",
        "## Screenshots",
    ]
    for name, path in (result.get("screenshots") or {}).items():
        lines.append(f"- {name}: {path}")
    if result.get("error"):
        lines.extend(["", "## Error", str(result["error"])])

    (out_root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    result = run_diagnostic()
    print(f"Run ID: {result['run_id']}")
    print(f"P6 window found: {result.get('p6_window_found')}")
    print(f"P6 title: {result.get('p6_title', '')}")
    print(f"pyautogui region: {result.get('pyautogui_region_success')}")
    print(f"ImageGrab crop: {result.get('imagegrab_full_crop_success')}")
    print(f"mss region: {result.get('mss_region_success')}")
    print(f"Recommended: {result.get('recommended_capture_method', '')}")
    print(f"Safe for OCR: {result.get('safe_for_ocr')}")
    print(f"Evidence: {ROOT / '06_output' / 'runs' / result['run_id'] / 'diagnose_p6_capture'}")
    return 0 if result.get("safe_for_ocr") else 1


if __name__ == "__main__":
    raise SystemExit(main())
