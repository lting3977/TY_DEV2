"""
M26 — Export Preflight Plan (No P6).

Builds a read-only export preflight plan by calling M25 for a validated sandbox
path. Requires manual approval; never allows real export, Finish, or overwrite.
Does not touch P6 or create export files.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "04_modules"))

from m06_go_to_activities import new_run_id, write_json  # noqa: E402
from m25_export_output_sandbox_guard_no_p6 import (  # noqa: E402
    DEFAULT_SANDBOX_ROOT,
    MODULE_NAME as M25_MODULE_NAME,
    run_m25,
)

MODULE_NAME = "m26_export_preflight_plan_no_p6"

NEXT_REQUIRED_USER_ACTION = (
    "Review export_preflight_plan.json and export_preflight_plan.md, then explicitly "
    "approve export in a future module. Do not press Finish or allow real export until approved."
)


@dataclass
class RunEvidence:
    run_id: str
    folder: Path
    preflight_dir: Path
    steps: List[str] = field(default_factory=list)
    preflight_files: List[str] = field(default_factory=list)


def build_evidence(run_id: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / run_id / MODULE_NAME
    preflight_dir = folder / "preflight"
    preflight_dir.mkdir(parents=True, exist_ok=True)
    return RunEvidence(run_id=run_id, folder=folder, preflight_dir=preflight_dir)


def build_safety_checks(m25_result: Dict[str, Any]) -> Dict[str, Any]:
    m25_checks = m25_result.get("safety_checks") or {}
    return {
        "sandbox_root_valid": m25_result.get("status") != "FAIL_SANDBOX_ROOT_INVALID",
        "path_in_sandbox": m25_checks.get("path_in_sandbox", "unknown"),
        "extension_allowed": m25_checks.get("extension_allowed", "unknown"),
        "filename_safe": m25_checks.get("filename_safe", "unknown"),
        "no_overwrite": m25_checks.get("no_overwrite", "unknown"),
        "manual_approval_required": True,
        "allow_real_export": False,
        "finish_allowed": False,
        "overwrite_allowed": False,
        "p6_interaction": False,
    }


def build_preflight_plan(
    project_name: str,
    m25_result: Dict[str, Any],
    *,
    force_manual_approval_off: bool = False,
) -> Dict[str, Any]:
    safety_checks = build_safety_checks(m25_result)
    if force_manual_approval_off:
        safety_checks["manual_approval_required"] = False

    return {
        "project_name": project_name,
        "export_format": "Spreadsheet/XLSX",
        "export_type": "Activities",
        "planned_output_path": m25_result.get("planned_output_path", ""),
        "planned_filename": m25_result.get("planned_filename", ""),
        "sandbox_root": m25_result.get("sandbox_root", str(DEFAULT_SANDBOX_ROOT)),
        "manual_approval_required": safety_checks["manual_approval_required"],
        "allow_real_export": safety_checks["allow_real_export"],
        "finish_allowed": safety_checks["finish_allowed"],
        "overwrite_allowed": safety_checks["overwrite_allowed"],
        "safety_checks": safety_checks,
        "next_required_user_action": NEXT_REQUIRED_USER_ACTION,
        "m25_run_id": m25_result.get("run_id", ""),
        "m25_status": m25_result.get("status", ""),
        "m25_export_path_plan_file": m25_result.get("export_path_plan_file", ""),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def save_preflight_outputs(evidence: RunEvidence, plan: Dict[str, Any]) -> None:
    json_path = evidence.preflight_dir / "export_preflight_plan.json"
    md_path = evidence.preflight_dir / "export_preflight_plan.md"
    write_json(json_path, plan)
    evidence.preflight_files.extend([str(json_path), str(md_path)])

    lines = [
        "# Export Preflight Plan",
        "",
        f"- Project: {plan.get('project_name', '')}",
        f"- Export format: {plan.get('export_format', '')}",
        f"- Export type: {plan.get('export_type', '')}",
        f"- Planned output path: {plan.get('planned_output_path', '')}",
        f"- Sandbox root: {plan.get('sandbox_root', '')}",
        f"- Manual approval required: {plan.get('manual_approval_required')}",
        f"- Allow real export: {plan.get('allow_real_export')}",
        f"- Finish allowed: {plan.get('finish_allowed')}",
        f"- Overwrite allowed: {plan.get('overwrite_allowed')}",
        "",
        "## Safety checks",
    ]
    for key, value in (plan.get("safety_checks") or {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Next required user action",
            plan.get("next_required_user_action", ""),
            "",
            "## M25 source",
            f"- M25 run ID: {plan.get('m25_run_id', '')}",
            f"- M25 status: {plan.get('m25_status', '')}",
            f"- M25 export path plan: {plan.get('m25_export_path_plan_file', '')}",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def finish_result(
    evidence: RunEvidence,
    project_name: str,
    status: str,
    reason: str,
    *,
    preflight_plan: Optional[Dict[str, Any]] = None,
    m25_result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    result = {
        "run_id": evidence.run_id,
        "module": MODULE_NAME,
        "project_name": project_name,
        "status": status,
        "reason": reason,
        "preflight_files": evidence.preflight_files,
        "preflight_plan": preflight_plan or {},
        "m25_run_id": (m25_result or {}).get("run_id", ""),
        "m25_status": (m25_result or {}).get("status", ""),
        "planned_output_path": (preflight_plan or {}).get("planned_output_path", ""),
        "error": error,
        "steps": evidence.steps,
    }
    write_json(evidence.folder / "result.json", result)
    write_report(evidence, result)
    return result


def write_report(evidence: RunEvidence, result: Dict[str, Any]) -> None:
    plan = result.get("preflight_plan") or {}
    lines = [
        "# M26 Export Preflight Plan Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project name: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- M25 run ID: {result.get('m25_run_id', '')}",
        f"- M25 status: {result.get('m25_status', '')}",
        f"- Planned output path: {result.get('planned_output_path', '')}",
        "",
        "## Preflight files",
    ]
    for path in result.get("preflight_files", []):
        lines.append(f"- {path}")

    if plan:
        lines.extend(["", "## Preflight summary"])
        for key in (
            "export_format",
            "export_type",
            "manual_approval_required",
            "allow_real_export",
            "finish_allowed",
            "overwrite_allowed",
            "next_required_user_action",
        ):
            lines.append(f"- {key}: {plan.get(key)}")

    lines.extend(["", "## Steps"])
    for step in result.get("steps", []):
        lines.append(f"- {step}")

    lines.extend(
        [
            "",
            "## Final decision",
            result["status"],
            "",
            "## Next recommendation",
        ]
    )
    if result["status"] == "PASS_PREFLIGHT_PLAN":
        lines.append("Human review required before any real export module runs.")
    else:
        lines.append("Fix the reported issue and re-run TY_TEST_M26_EXPORT_PREFLIGHT_PLAN.bat")

    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_m26(
    project_name: str,
    *,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
    output_path: Optional[str] = None,
    sandbox_root: Optional[Path] = None,
    force_manual_approval_off: bool = False,
    force_m25_error: bool = False,
) -> Dict[str, Any]:
    evidence = evidence or build_evidence(run_id or new_run_id())
    project_name = str(project_name or "").strip()

    if not project_name:
        return finish_result(
            evidence,
            project_name,
            "FAIL_PROJECT_NAME_EMPTY",
            "project_name is empty",
        )

    evidence.steps.append("validate project_name")

    try:
        evidence.steps.append("call M25 for export path plan")
        m25_run_id = f"{evidence.run_id}_m25"
        if force_m25_error:
            m25_result = {
                "run_id": m25_run_id,
                "module": M25_MODULE_NAME,
                "status": "ERROR",
                "reason": "forced M25 error for hard test",
                "planned_output_path": "",
                "planned_filename": "",
                "sandbox_root": str(sandbox_root or DEFAULT_SANDBOX_ROOT),
                "export_path_plan_file": "",
                "safety_checks": {},
            }
            evidence.steps.append("force_m25_error: hard test hook")
        else:
            m25_result = run_m25(
                project_name,
                output_path=output_path,
                sandbox_root=sandbox_root,
                run_id=m25_run_id,
            )

        m25_status = m25_result.get("status", "")
        if m25_status != "PASS_EXPORT_PATH_PLAN":
            if m25_status == "FAIL_SANDBOX_ROOT_INVALID":
                return finish_result(
                    evidence,
                    project_name,
                    "ERROR",
                    m25_result.get("reason", "M25 sandbox root invalid"),
                    m25_result=m25_result,
                    error=m25_result.get("error") or m25_result.get("reason"),
                )
            if not m25_result.get("planned_output_path"):
                return finish_result(
                    evidence,
                    project_name,
                    "FAIL_M25_PLAN_MISSING",
                    f"M25 did not produce export path plan: {m25_status}",
                    m25_result=m25_result,
                )
            return finish_result(
                evidence,
                project_name,
                "FAIL_OUTPUT_PATH_INVALID",
                m25_result.get("reason", "M25 output path invalid"),
                m25_result=m25_result,
            )

        evidence.steps.append("build export preflight plan")
        preflight_plan = build_preflight_plan(
            project_name,
            m25_result,
            force_manual_approval_off=force_manual_approval_off,
        )

        if not preflight_plan.get("manual_approval_required"):
            return finish_result(
                evidence,
                project_name,
                "FAIL_MANUAL_APPROVAL_NOT_REQUIRED",
                "Preflight plan must require manual approval",
                preflight_plan=preflight_plan,
                m25_result=m25_result,
            )

        for flag in ("allow_real_export", "finish_allowed", "overwrite_allowed"):
            if preflight_plan.get(flag):
                return finish_result(
                    evidence,
                    project_name,
                    "FAIL_MANUAL_APPROVAL_NOT_REQUIRED",
                    f"Preflight safety flag {flag} must remain false",
                    preflight_plan=preflight_plan,
                    m25_result=m25_result,
                )

        if not preflight_plan.get("planned_output_path"):
            return finish_result(
                evidence,
                project_name,
                "FAIL_OUTPUT_PATH_INVALID",
                "Planned output path missing from preflight plan",
                preflight_plan=preflight_plan,
                m25_result=m25_result,
            )

        evidence.steps.append("save preflight outputs")
        save_preflight_outputs(evidence, preflight_plan)

        return finish_result(
            evidence,
            project_name,
            "PASS_PREFLIGHT_PLAN",
            "Export preflight plan created; manual approval required before real export",
            preflight_plan=preflight_plan,
            m25_result=m25_result,
        )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return finish_result(
            evidence,
            project_name,
            "ERROR",
            str(exc),
            error=traceback.format_exc(),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="M26 export preflight plan (no P6)")
    parser.add_argument("--project", required=True, help='Project name e.g. "Talison 1275"')
    parser.add_argument("--output-path", default="", help="Optional explicit output path for M25")
    parser.add_argument("--run-id", default="", help="Optional run id")
    args = parser.parse_args()

    result = run_m26(
        args.project.strip(),
        output_path=args.output_path.strip() or None,
        run_id=args.run_id.strip() or None,
    )
    print(f"Status: {result['status']}")
    print(f"Reason: {result['reason']}")
    if result.get("planned_output_path"):
        print(f"Planned path: {result['planned_output_path']}")
    return 0 if result["status"] == "PASS_PREFLIGHT_PLAN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
