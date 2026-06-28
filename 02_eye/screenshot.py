"""P6-window-only screenshot capture. Never OCR full desktop."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    import pyautogui
except ImportError:
    pyautogui = None  # type: ignore[assignment]

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[assignment,misc]

MIN_P6_WIDTH = 200
MIN_P6_HEIGHT = 150
MINIMIZED_COORD_THRESHOLD = -10000


@dataclass
class P6Rect:
    left: int
    top: int
    width: int
    height: int

    def to_dict(self) -> Dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
            "right": self.left + self.width,
            "bottom": self.top + self.height,
        }


def _require_pyautogui() -> None:
    if pyautogui is None:
        raise ImportError("pyautogui is not installed. Install with: pip install pyautogui")
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.5


def _require_pillow() -> None:
    if Image is None:
        raise ImportError("Pillow is not installed. Install with: pip install pillow")


def rect_from_window_state(state: Dict[str, Any]) -> Optional[P6Rect]:
    if not state.get("found"):
        return None
    left = state.get("left")
    top = state.get("top")
    width = state.get("width")
    height = state.get("height")
    if left is None or top is None or width is None or height is None:
        return None
    return P6Rect(int(left), int(top), int(width), int(height))


def validate_p6_rect(rect: Optional[P6Rect], is_minimized: Optional[bool] = None) -> Tuple[bool, str]:
    if rect is None:
        return False, "P6 window rectangle unavailable"
    if is_minimized:
        return False, "P6 window is minimised"
    if rect.left <= MINIMIZED_COORD_THRESHOLD or rect.top <= MINIMIZED_COORD_THRESHOLD:
        return False, "P6 window coordinates indicate minimised or off-screen state"
    if rect.width <= 0 or rect.height <= 0:
        return False, "P6 window has zero size"
    if rect.width < MIN_P6_WIDTH or rect.height < MIN_P6_HEIGHT:
        return False, f"P6 window too small ({rect.width}x{rect.height})"
    if rect.width > 10000 or rect.height > 10000:
        return False, f"P6 window dimensions implausible ({rect.width}x{rect.height})"
    return True, "ok"


def crop_center_percent_of_image(
    image_path: str,
    output_path: str,
    crop_region: dict,
) -> str:
    """Crop a centre region from an existing image (popup dialog within P6 window)."""
    _require_pillow()
    left = float(crop_region["left"])
    top = float(crop_region["top"])
    right = float(crop_region["right"])
    bottom = float(crop_region["bottom"])

    with Image.open(image_path) as img:
        width, height = img.size
        box = (
            int(width * left),
            int(height * top),
            int(width * right),
            int(height * bottom),
        )
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        img.crop(box).save(output_path)
    return output_path


def save_debug_fullscreen(output_folder: Path, label: str) -> Optional[str]:
    """Save full-screen image for debugging only — never used for OCR."""
    _require_pyautogui()
    _require_pillow()
    output_folder.mkdir(parents=True, exist_ok=True)
    path = output_folder / f"debug_fullscreen_{label}.png"
    pyautogui.screenshot().save(str(path))
    return str(path)


def _crop_box_for_rect(p6_rect: P6Rect, img_width: int, img_height: int) -> Tuple[int, int, int, int]:
    """Clamp P6 rect crop box to image bounds (handles negative maximized-window coords)."""
    left = max(0, p6_rect.left)
    top = max(0, p6_rect.top)
    right = min(img_width, p6_rect.left + p6_rect.width)
    bottom = min(img_height, p6_rect.top + p6_rect.height)
    if right <= left or bottom <= top:
        left, top = 0, 0
        right, bottom = min(p6_rect.width, img_width), min(p6_rect.height, img_height)
    return left, top, right, bottom


def _capture_pyautogui_region(p6_rect: P6Rect, image_path: Path) -> Tuple[bool, str]:
    try:
        _require_pyautogui()
        region = (p6_rect.left, p6_rect.top, p6_rect.width, p6_rect.height)
        image = pyautogui.screenshot(region=region)
        image.save(str(image_path))
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _capture_mss_region(p6_rect: P6Rect, image_path: Path) -> Tuple[bool, str]:
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
            image_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(image_path))
        return True, ""
    except ImportError:
        return False, "mss not installed"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _capture_imagegrab_full_crop(
    p6_rect: P6Rect,
    image_path: Path,
    debug_fullscreen_path: Optional[Path] = None,
) -> Tuple[bool, str]:
    try:
        _require_pillow()
        from PIL import ImageGrab  # noqa: WPS433

        full = ImageGrab.grab(all_screens=True)
        if debug_fullscreen_path:
            debug_fullscreen_path.parent.mkdir(parents=True, exist_ok=True)
            full.save(str(debug_fullscreen_path))

        box = _crop_box_for_rect(p6_rect, full.width, full.height)
        crop = full.crop(box)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        crop.save(str(image_path))
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def capture_p6_window_only(
    output_folder: Path,
    filename: str,
    p6_rect: P6Rect,
    metadata_path: Optional[Path] = None,
    save_debug_fullscreen_label: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Capture only the P6 window region. Never falls back to full-screen OCR input.

    Tries pyautogui region first, then mss region, then ImageGrab full-screen
    crop to P6 rect. Only the P6 crop is saved for OCR.
    """
    valid, reason = validate_p6_rect(p6_rect)
    if not valid:
        return {
            "success": False,
            "image_path": None,
            "metadata": None,
            "error": reason,
        }

    _require_pillow()
    output_folder.mkdir(parents=True, exist_ok=True)
    image_path = output_folder / filename

    debug_path = None
    if save_debug_fullscreen_label:
        debug_path = save_debug_fullscreen(output_folder, save_debug_fullscreen_label)

    imagegrab_debug_path = (
        output_folder / f"debug_fullscreen_imagegrab_{Path(filename).stem}.png"
    )

    errors: Dict[str, str] = {}
    capture_method = ""
    full_screen_used_for_crop_only = False

    ok, err = _capture_pyautogui_region(p6_rect, image_path)
    if ok:
        capture_method = "pyautogui_region"
    else:
        errors["pyautogui_region"] = err
        ok, err = _capture_mss_region(p6_rect, image_path)
        if ok:
            capture_method = "mss_region"
        else:
            errors["mss_region"] = err
            ok, err = _capture_imagegrab_full_crop(
                p6_rect, image_path, imagegrab_debug_path
            )
            if ok:
                capture_method = "imagegrab_full_crop_fallback"
                full_screen_used_for_crop_only = True
            else:
                errors["imagegrab_full_crop"] = err

    if not ok:
        detail = "; ".join(f"{k}: {v}" for k, v in errors.items())
        return {
            "success": False,
            "image_path": None,
            "metadata": None,
            "error": f"P6 region capture failed: {detail}",
        }

    metadata = {
        "image_path": str(image_path),
        "source": "p6_crop_only",
        "capture_method": capture_method,
        "p6_rect": p6_rect.to_dict(),
        "width": p6_rect.width,
        "height": p6_rect.height,
        "used_for_ocr": True,
        "full_screen_used_for_crop_only": full_screen_used_for_crop_only,
        "full_screen_ocr_allowed": False,
        "debug_fullscreen_path": debug_path,
        "debug_imagegrab_fullscreen_path": (
            str(imagegrab_debug_path) if full_screen_used_for_crop_only else None
        ),
        "capture_errors": errors,
    }

    if metadata_path:
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        with metadata_path.open("w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2, ensure_ascii=False)

    return {
        "success": True,
        "image_path": str(image_path),
        "metadata": metadata,
        "error": None,
    }
