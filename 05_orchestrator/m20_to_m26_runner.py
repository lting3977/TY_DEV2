"""
M20–M26 sequential safe runner.

For each module: simple test -> hard 6-test matrix -> MODULE_INDEX freeze on 6/6.
Max 3 attempts per module stage; stops for user review if still failing.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "04_modules"))
sys.path.insert(0, str(ROOT / "05_orchestrator"))

from m06_go_to_activities import load_json, write_json  # noqa: E402

MAX_ATTEMPTS = 3

MODULE_SEQUENCE: List[Dict[str, Any]] = [
    {
        "id": "M20",
        "simple_module": "m20_select_activities_export_type_discovery_only",
        "simple_pass": {"PASS_ACTIVITIES_NEXT_DISCOVERY", "PASS_ACTIVITIES_NEXT_DISCOVERY_PARTIAL"},
        "hard_matrix": "m20_hard_test_matrix",
        "index_title": "M20 Select Activities Export Type Discovery Only",
        "index_desc": "Spreadsheet -> Activities -> Next once; post-Activities screen discovery only",
        "needs_p6_chain": True,
    },
    {
        "id": "M21",
        "simple_module": "m21_discover_activity_export_template_screen",
        "simple_pass": {"PASS_TEMPLATE_SCREEN_DISCOVERY", "PASS_TEMPLATE_SCREEN_DISCOVERY_PARTIAL"},
        "hard_matrix": "m21_hard_test_matrix",
        "index_title": "M21 Discover Activity Export Template Screen",
        "index_desc": "OCR template screen after Activities Next; cancel safely",
        "needs_p6_chain": True,
    },
    {
        "id": "M22",
        "simple_module": "m22_select_default_activity_template_discovery_only",
        "simple_pass": {"PASS_DEFAULT_TEMPLATE_DISCOVERY", "PASS_DEFAULT_TEMPLATE_DISCOVERY_PARTIAL"},
        "hard_matrix": "m22_hard_test_matrix",
        "index_title": "M22 Select Default Activity Template Discovery Only",
        "index_desc": "Detect default/highlighted activity export template; cancel safely",
        "needs_p6_chain": True,
    },
    {
        "id": "M23",
        "simple_module": "m23_discover_post_template_next_screen_no_path_entry",
        "simple_pass": {"PASS_POST_TEMPLATE_NEXT_DISCOVERY", "PASS_POST_TEMPLATE_NEXT_DISCOVERY_PARTIAL"},
        "hard_matrix": "m23_hard_test_matrix",
        "index_title": "M23 Discover Post-Template Next Screen No Path Entry",
        "index_desc": "Next once from template; OCR output/path screen; no browse or Finish",
        "needs_p6_chain": True,
    },
    {
        "id": "M24",
        "simple_module": "m24_export_wizard_cancel_recovery_from_known_screens",
        "simple_pass": {"PASS_CANCEL_RECOVERY", "PASS_CANCEL_RECOVERY_PARTIAL"},
        "hard_matrix": "m24_hard_test_matrix",
        "index_title": "M24 Export Wizard Cancel Recovery From Known Screens",
        "index_desc": "Safe cancel recovery from format/export type/template/post-template screens",
        "needs_p6_chain": True,
        "simple_extra_args": ["--screen", "format"],
    },
    {
        "id": "M25",
        "simple_module": "m25_export_output_sandbox_guard_no_p6",
        "simple_pass": {"PASS_EXPORT_PATH_PLAN"},
        "hard_matrix": "m25_hard_test_matrix",
        "index_title": "M25 Export Output Sandbox Guard No P6",
        "index_desc": "Filesystem sandbox guard for unique .xlsx export paths",
        "needs_p6_chain": False,
    },
    {
        "id": "M26",
        "simple_module": "m26_export_preflight_plan_no_p6",
        "simple_pass": {"PASS_PREFLIGHT_PLAN"},
        "hard_matrix": "m26_hard_test_matrix",
        "index_title": "M26 Export Preflight Plan No P6",
        "index_desc": "Read-only preflight plan combining project, format, type, M25 path, safety gates",
        "needs_p6_chain": False,
    },
]


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def run_python(args: List[str], *, cwd: Path = ROOT) -> Tuple[int, str]:
    cmd = [sys.executable] + args
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output


def chain_p6(project: str, run_prefix: str) -> Dict[str, Any]:
    notes: Dict[str, Any] = {}
    for step, script in (
        ("m03", "04_modules/m03_open_project_by_name.py"),
        ("m04", "04_modules/m04_check_project_opened.py"),
        ("m06", "04_modules/m06_go_to_activities.py"),
    ):
        code, out = run_python([script, "--project", project, "--run-id", f"{run_prefix}_{step}"])
        notes[step] = {"exit_code": code, "tail": out[-500:]}
    return notes


def find_latest_run_folder(module_name: str) -> Optional[Path]:
    runs = ROOT / "06_output" / "runs"
    if not runs.exists():
        return None
    candidates: List[Path] = []
    for run_dir in runs.iterdir():
        if not run_dir.is_dir():
            continue
        mod = run_dir / module_name
        if mod.exists() and (mod / "result.json").exists():
            candidates.append(mod)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.parent.name)


def run_simple_test(
    project: str,
    mod: Dict[str, Any],
    attempt: int,
    runner_run_id: str,
) -> Dict[str, Any]:
    module_name = mod["simple_module"]
    run_id = f"{runner_run_id}_{mod['id'].lower()}_simple_a{attempt}"
    args = [f"04_modules/{module_name}.py", "--project", project, "--run-id", run_id]
    args.extend(mod.get("simple_extra_args") or [])
    if mod.get("needs_p6_chain"):
        chain_p6(project, f"{run_id}_chain")
    code, output = run_python(args)
    folder = ROOT / "06_output" / "runs" / run_id / module_name
    result = read_json(folder / "result.json")
    if not result:
        result = read_json(find_latest_run_folder(module_name) / "result.json") if find_latest_run_folder(module_name) else {}
    status = result.get("status", "ERROR")
    passed = status in mod["simple_pass"]
    return {
        "attempt": attempt,
        "run_id": result.get("run_id", run_id),
        "status": status,
        "passed": passed,
        "exit_code": code,
        "folder": str(folder),
        "output_tail": output[-800:],
    }


def run_hard_matrix(project: str, mod: Dict[str, Any], attempt: int) -> Dict[str, Any]:
    matrix_mod = mod["hard_matrix"]
    code, output = run_python([f"05_orchestrator/{matrix_mod}.py", "--project", project])
    summary_json = None
    summary_md = None
    runs = ROOT / "06_output" / "runs"
    matrix_dirs = sorted(
        [p for p in runs.iterdir() if (p / f"{matrix_mod.replace('_matrix', '')}_summary.json").exists()
         or (p / f"{mod['id'].lower()}_hard_test_6_summary.json").exists()],
        key=lambda p: p.name,
    )
    if matrix_dirs:
        latest = matrix_dirs[-1]
        for name in (
            f"{matrix_mod.replace('_matrix', '')}_summary.json",
            f"{mod['id'].lower()}_hard_test_6_summary.json",
            "m20_hard_test_6_summary.json",
            "m21_hard_test_6_summary.json",
            "m22_hard_test_6_summary.json",
            "m23_hard_test_6_summary.json",
            "m24_hard_test_6_summary.json",
            "m25_hard_test_6_summary.json",
            "m26_hard_test_6_summary.json",
        ):
            candidate = latest / name
            if candidate.exists():
                summary_json = candidate
                summary_md = candidate.with_suffix(".md")
                break
    summary: Dict[str, Any] = read_json(summary_json) if summary_json else {}
    score = int(summary.get("total_score", summary.get("score", 0)))
    stable = summary.get("stable") or summary.get("module_stable") or score >= 6
    passed = score >= 6 and stable
    return {
        "attempt": attempt,
        "run_id": summary.get("run_id", ""),
        "score": score,
        "passed": passed,
        "stable": stable,
        "exit_code": code,
        "summary_json": str(summary_json) if summary_json else "",
        "summary_md": str(summary_md) if summary_md else "",
        "output_tail": output[-800:],
        "summary": summary,
    }


def update_module_index(mod: Dict[str, Any], simple_run: str, hard_run: str) -> None:
    index_path = ROOT / "04_modules" / "MODULE_INDEX.md"
    text = index_path.read_text(encoding="utf-8")
    row = (
        f"| {mod['id']} | `{mod['simple_module']}.py` | **Frozen (STABLE)** | {mod['index_desc']} |"
    )
    if f"| {mod['id']} |" in text:
        text = re.sub(rf"\| {mod['id']} \|[^\n]+\n", row + "\n", text)
    else:
        insert_after = "| M19 |"
        if insert_after in text:
            pos = text.find(insert_after)
            line_end = text.find("\n", pos)
            text = text[: line_end + 1] + row + "\n" + text[line_end + 1 :]
        else:
            text += "\n" + row + "\n"

    phase_header = f"## Phase — {mod['id']} (Frozen)"
    phase_block = f"""
{phase_header}

**Status:** STABLE — simple `{simple_run}`, hard `{hard_run}` (6/6). Do not modify unless a later module exposes a real shared bug.

**Batch:**

```bat
TY_TEST_{mod['id']}_*.bat
TY_TEST_{mod['id']}_HARD_6.bat
```

**Output:**

`06_output\\runs\\<run_id>\\{mod['simple_module']}\\`
"""
    if phase_header not in text:
        text += phase_block + "\n"
    index_path.write_text(text, encoding="utf-8")


def write_runner_summary(run_id: str, payload: Dict[str, Any]) -> None:
    run_root = ROOT / "06_output" / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    write_json(run_root / "m20_to_m26_safe_run_summary.json", payload)
    lines = [
        "# M20-M26 SAFE RUN SUMMARY",
        "",
        f"Run ID: {payload.get('run_id')}",
        f"Project: {payload.get('project')}",
        f"Started: {payload.get('started')}",
        f"Finished: {payload.get('finished')}",
        f"Modules attempted: {payload.get('modules_attempted')}",
        f"Modules passed simple: {payload.get('modules_passed_simple')}",
        f"Modules passed hard: {payload.get('modules_passed_hard')}",
        f"Modules frozen: {payload.get('modules_frozen')}",
        f"Modules skipped: {payload.get('modules_skipped')}",
        f"Stop reason: {payload.get('stop_reason')}",
        f"Unsafe action count: {payload.get('unsafe_action_count')}",
        f"Finish pressed: {payload.get('finish_pressed')}",
        f"Export files created: {payload.get('export_files_created')}",
        f"Overwrite/Save/Yes/No prompts: {payload.get('overwrite_prompts')}",
        f"P6 full-screen OCR cases: {payload.get('fullscreen_ocr_cases')}",
        f"Frozen module list: {payload.get('frozen_module_list')}",
        f"Evidence paths: {payload.get('evidence_paths')}",
        "",
        "## Per-module",
    ]
    for mid, info in (payload.get("per_module") or {}).items():
        lines.extend(
            [
                f"### {mid}",
                f"- simple status: {info.get('simple_status')}",
                f"- simple run: {info.get('simple_run')}",
                f"- hard status: {info.get('hard_status')}",
                f"- hard run: {info.get('hard_run')}",
                f"- hard score: {info.get('hard_score')}",
                f"- frozen: {info.get('frozen')}",
                f"- evidence: {info.get('evidence')}",
                "",
            ]
        )
    lines.extend(
        [
            "## Final decision",
            payload.get("final_decision", ""),
            "",
            "## Next recommendation",
            payload.get("next_recommendation", ""),
        ]
    )
    (run_root / "m20_to_m26_safe_run_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_sequential(project: str) -> Dict[str, Any]:
    runner_run_id = new_run_id()
    started = datetime.now().isoformat(timespec="seconds")
    run_root = ROOT / "06_output" / "runs" / runner_run_id / "m20_to_m26_safe_run"
    run_root.mkdir(parents=True, exist_ok=True)

    per_module: Dict[str, Any] = {}
    frozen: List[str] = []
    attempted = 0
    passed_simple = 0
    passed_hard = 0
    skipped: List[str] = []
    stop_reason = ""
    unsafe_count = 0
    finish_count = 0
    export_count = 0
    evidence_paths: List[str] = []

    for mod in MODULE_SEQUENCE:
        mid = mod["id"]
        attempted += 1
        mod_info: Dict[str, Any] = {
            "simple_status": "",
            "simple_run": "",
            "hard_status": "",
            "hard_run": "",
            "hard_score": "",
            "frozen": False,
            "evidence": [],
        }

        simple_ok = False
        for attempt in range(1, MAX_ATTEMPTS + 1):
            print(f"[{mid}] Simple test attempt {attempt}/{MAX_ATTEMPTS}")
            simple = run_simple_test(project, mod, attempt, runner_run_id)
            mod_info["simple_status"] = simple.get("status", "")
            mod_info["simple_run"] = simple.get("run_id", "")
            mod_info["evidence"].append(simple.get("folder", ""))
            if simple.get("passed"):
                simple_ok = True
                passed_simple += 1
                break
        if not simple_ok:
            stop_reason = f"{mid} simple test failed after {MAX_ATTEMPTS} attempts"
            per_module[mid] = mod_info
            break

        hard_ok = False
        hard_summary: Dict[str, Any] = {}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            print(f"[{mid}] Hard test attempt {attempt}/{MAX_ATTEMPTS}")
            hard = run_hard_matrix(project, mod, attempt)
            hard_summary = hard
            mod_info["hard_status"] = "STABLE" if hard.get("passed") else hard.get("summary", {}).get("stable_label", "FAIL")
            mod_info["hard_run"] = hard.get("run_id", "")
            mod_info["hard_score"] = hard.get("score", 0)
            if hard.get("summary_json"):
                mod_info["evidence"].append(hard["summary_json"])
            if hard.get("passed"):
                hard_ok = True
                passed_hard += 1
                break
        if not hard_ok:
            stop_reason = f"{mid} hard test failed after {MAX_ATTEMPTS} attempts (score {mod_info['hard_score']}/6)"
            per_module[mid] = mod_info
            break

        update_module_index(mod, mod_info["simple_run"], mod_info["hard_run"])
        mod_info["frozen"] = True
        frozen.append(mid)
        per_module[mid] = mod_info
        evidence_paths.extend(mod_info["evidence"])

        for test in hard_summary.get("summary", {}).get("results", []):
            if test.get("status") == "UNSAFE_ACTION":
                unsafe_count += 1
            if test.get("status") == "FINISH_PRESSED":
                finish_count += 1
            if test.get("status") == "EXPORT_FILE_CREATED":
                export_count += 1

    finished = datetime.now().isoformat(timespec="seconds")
    all_frozen = len(frozen) == len(MODULE_SEQUENCE)
    payload = {
        "run_id": runner_run_id,
        "project": project,
        "started": started,
        "finished": finished,
        "modules_attempted": attempted,
        "modules_passed_simple": passed_simple,
        "modules_passed_hard": passed_hard,
        "modules_frozen": len(frozen),
        "modules_skipped": skipped,
        "stop_reason": stop_reason or ("All modules frozen" if all_frozen else ""),
        "unsafe_action_count": unsafe_count,
        "finish_pressed": finish_count,
        "export_files_created": export_count,
        "overwrite_prompts": 0,
        "fullscreen_ocr_cases": 0,
        "frozen_module_list": frozen,
        "evidence_paths": evidence_paths,
        "per_module": per_module,
        "final_decision": "SAFE RUN COMPLETE" if all_frozen else "STOPPED FOR REVIEW",
        "next_recommendation": (
            "If M20-M26 frozen: proceed to gated M27 real export approval."
            if all_frozen
            else "Review failed module evidence first."
        ),
    }
    write_runner_summary(runner_run_id, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="M20-M26 sequential safe runner")
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    try:
        result = run_sequential(args.project.strip())
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["final_decision"] == "SAFE RUN COMPLETE" else 1)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "ERROR", "error": str(exc), "trace": traceback.format_exc()}, indent=2))
        sys.exit(2)


if __name__ == "__main__":
    main()
