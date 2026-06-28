"""Screenshot capture utilities using pyautogui and Pillow."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Tuple, Union

try:
    import pyautogui
except ImportError:
    pyautogui = None  # type: ignore[assignment]

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[assignment,misc]


def _require_pyautogui() -> None:
    if pyautogui is None:
        raise ImportError("pyautogui is not installed. Install with: pip install pyautogui")
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.5


def _require_pillow() -> None:
    if Image is None:
        raise ImportError("Pillow is not installed. Install with: pip install pillow")


def get_screen_size() -> Tuple[int, int]:
    """Return (width, height) of the primary screen."""
    _require_pyautogui()
    size = pyautogui.size()
    return int(size.width), int(size.height)


def take_screenshot(output_folder: str, filename: str) -> str:
    """Capture the full screen and save to output_folder/filename."""
    _require_pyautogui()
    _require_pillow()
    os.makedirs(output_folder, exist_ok=True)
    output_path = os.path.join(output_folder, filename)
    image = pyautogui.screenshot()
    image.save(output_path)
    return output_path


def take_screenshot_with_timestamp(output_folder: str, label: str) -> str:
    """Capture the screen with a timestamped filename based on label."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
    filename = f"{safe_label}_{timestamp}.png"
    return take_screenshot(output_folder, filename)


def crop_center_percent(image_path: str, output_path: str, crop_region: dict) -> str:
    """Crop image using fractional crop_region_percent dict."""
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
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        img.crop(box).save(output_path)
    return output_path


def crop_window_region(image_path: str, output_path: str, region: dict) -> str:
    """Crop using pixel region {left, top, right, bottom}."""
    _require_pillow()
    with Image.open(image_path) as img:
        box = (
            int(region["left"]),
            int(region["top"]),
            int(region["right"]),
            int(region["bottom"]),
        )
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        img.crop(box).save(output_path)
    return output_path
