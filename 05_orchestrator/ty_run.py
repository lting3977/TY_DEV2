"""TY orchestrator entry point — Phase 1 observation and stability tests."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCH = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ORCH))
sys.path.insert(0, str(ROOT / "02_accessibility"))
sys.path.insert(0, str(ROOT / "02_eye"))
sys.path.insert(0, str(ROOT / "02_hand"))

import importlib.util


def _load_subpackage(name: str, folder: Path) -> None:
    init = folder / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        name,
        init,
        submodule_search_locations=[str(folder)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load package {name} from {folder}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)


def bootstrap_packages() -> None:
    acc = ROOT / "02_accessibility"
    _load_subpackage("accessibility", acc)
    for sub in ("eye", "hand", "brain"):
        sub_path = acc / sub
        _load_subpackage(f"accessibility.{sub}", sub_path)

    eye = ROOT / "02_eye"
    _load_subpackage("eye", eye)
    hand = ROOT / "02_hand"
    _load_subpackage("hand", hand)


def run_test_eye_hand_20() -> int:
    bootstrap_packages()

    from eye_hand_matrix import EYE_HAND_TEST_MATRIX
    from summary_builder import build_summary, write_summary_files
    from test_helpers import TestContext, load_json, new_run_id, run_test_case

    config = load_json(ROOT / "01_config" / "ty_config.json")
    screen_rule = load_json(ROOT / "03_screen_library" / "p6_open_project" / "screen_rule.json")
    run_id = new_run_id()
    run_root = ROOT / "06_output" / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    ctx = TestContext(
        run_id=run_id,
        run_root=run_root,
        config=config,
        screen_rule=screen_rule,
        p6_keyword=config["p6_window_title_keyword"],
        min_confidence=float(config.get("min_ocr_confidence", 0.5)),
    )

    print("TY Phase 1 — Eye + Hand Stability (20 tests) — Fix Round 1")
    print("OCR policy: P6-window crop ONLY — no full-desktop OCR")
    print(f"Run ID: {run_id}")
    print(f"Output: {run_root}")
    print("Safety: observation only — no Enter/Yes/No/Save/Delete")
    print("=" * 60)

    results = []
    for index, test_def in enumerate(EYE_HAND_TEST_MATRIX, start=1):
        print(f"[{index}/20] {test_def['id']} {test_def['name']}")
        result = run_test_case(
            ctx,
            test_def["id"],
            test_def["slug"],
            test_def["name"],
            test_def["runner"],
        )
        results.append(result)
        print(f"  -> {result['status']} (score {result['score']})")

    previous = config.get("previous_run", {})
    summary = build_summary(run_id, run_root, results, previous=previous)
    write_summary_files(run_root, summary)

    cmp_ = summary["comparison"]
    print("=" * 60)
    print(f"Final score: {summary['final_score']} / 20 ({summary['percentage']}%)")
    print(f"OCR pollution: {summary['ocr_pollution_cases']}")
    print(f"Previous: {cmp_['previous_score']}/20, pollution {cmp_['previous_ocr_pollution']}")
    print(f"Decision: {summary['decision']}")
    print(f"Summary: {run_root / 'phase1_eye_hand_20_summary.md'}")
    return 0 if summary["ocr_pollution_cases"] == 0 and summary["final_score"] >= 16 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="TY orchestrator")
    parser.add_argument(
        "--mode",
        required=True,
        choices=["test_eye_hand_20"],
        help="Orchestrator mode",
    )
    args = parser.parse_args()

    if args.mode == "test_eye_hand_20":
        return run_test_eye_hand_20()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
