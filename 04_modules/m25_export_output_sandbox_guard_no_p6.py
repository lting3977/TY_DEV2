"""
M25 — Export Output Sandbox Guard (No P6).

Pure filesystem guard for planned Spreadsheet/XLSX export paths under a fixed
sandbox root. Validates extension, filename safety, sandbox containment, and
rejects overwrite targets. Does not touch P6 or create export files.
"""

from __future__ import annotations

import argparse
import re
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "04_modules"))

from m06_go_to_activities import new_run_id, write_json  # noqa: E402

MODULE_NAME = "m25_export_output_sandbox_guard_no_p6"
DEFAULT_SANDBOX_ROOT = ROOT / "06_output" / "exports" / "sandbox"
ALLOWED_EXTENSION = ".xlsx"
UNSAFE_FILENAME_PATTERN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_FILENAME_LEN = 200


@dataclass
class RunEvidence:
    run_id: str
    folder: Path
    discovery_dir: Path
    steps: List[str] = field(default_factory=list)
    discovery_files: List[str] = field(default_factory=list)


def build_evidence(run_id: str) -> RunEvidence:
    folder = ROOT / "06_output" / "runs" / run_id / MODULE_NAME
    discovery_dir = folder / "discovery"
    discovery_dir.mkdir(parents=True, exist_ok=True)
    return RunEvidence(run_id=run_id, folder=folder, discovery_dir=discovery_dir)


def resolve_sandbox_root(sandbox_root: Optional[Path] = None) -> Tuple[Optional[Path], str]:
    root = (sandbox_root or DEFAULT_SANDBOX_ROOT).expanduser()
    try:
        resolved = root.resolve()
    except (OSError, RuntimeError) as exc:
        return None, f"sandbox root could not be resolved: {exc}"
    if not resolved.exists():
        return None, f"sandbox root does not exist: {resolved}"
    if not resolved.is_dir():
        return None, f"sandbox root is not a directory: {resolved}"
    return resolved, "ok"


def sanitize_project_slug(project_name: str) -> str:
    cleaned = project_name.strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = UNSAFE_FILENAME_PATTERN.sub("_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    if not cleaned:
        cleaned = "export"
    return cleaned[:80]


def build_timestamped_filename(project_name: str, timestamp: Optional[str] = None) -> str:
    slug = sanitize_project_slug(project_name)
    stamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{slug}_{stamp}{ALLOWED_EXTENSION}"


def check_extension(filename: str) -> Tuple[bool, str]:
    suffix = Path(filename).suffix.lower()
    if suffix != ALLOWED_EXTENSION:
        return False, f"extension {suffix!r} not allowed; only {ALLOWED_EXTENSION!r}"
    return True, "ok"


def check_filename_safe(filename: str) -> Tuple[bool, str]:
    name = Path(filename).name
    if not name or name in (".", ".."):
        return False, "filename is empty or reserved"
    if len(name) > MAX_FILENAME_LEN:
        return False, f"filename exceeds {MAX_FILENAME_LEN} characters"
    if UNSAFE_FILENAME_PATTERN.search(name):
        return False, "filename contains unsafe characters"
    if ".." in name:
        return False, "filename contains path traversal marker"
    return True, "ok"


def validate_path_in_sandbox(candidate: Path, sandbox_root: Path) -> Tuple[bool, str]:
    raw = str(candidate)
    if ".." in Path(raw).parts:
        return False, "path contains traversal segments"

    try:
        sandbox_resolved = sandbox_root.resolve()
        candidate_resolved = candidate.expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        return False, f"path resolution failed: {exc}"

    try:
        candidate_resolved.relative_to(sandbox_resolved)
    except ValueError:
        return False, f"path outside sandbox root {sandbox_resolved}"
    return True, "ok"


def check_no_overwrite(path: Path) -> Tuple[bool, str]:
    if path.exists():
        return False, f"target already exists: {path}"
    return True, "ok"


def save_export_path_plan(evidence: RunEvidence, payload: Dict[str, Any]) -> str:
    path = evidence.discovery_dir / "export_path_plan.json"
    write_json(path, payload)
    evidence.discovery_files.append(str(path))
    return str(path)


def finish_result(
    evidence: RunEvidence,
    project_name: str,
    status: str,
    reason: str,
    *,
    sandbox_root: str = "",
    planned_output_path: str = "",
    planned_filename: str = "",
    export_path_plan: Optional[Dict[str, Any]] = None,
    safety_checks: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    result = {
        "run_id": evidence.run_id,
        "module": MODULE_NAME,
        "project_name": project_name,
        "status": status,
        "reason": reason,
        "sandbox_root": sandbox_root,
        "planned_output_path": planned_output_path,
        "planned_filename": planned_filename,
        "export_path_plan_file": str(evidence.discovery_dir / "export_path_plan.json"),
        "discovery_files": evidence.discovery_files,
        "safety_checks": safety_checks or {},
        "error": error,
        "steps": evidence.steps,
    }
    write_json(evidence.folder / "result.json", result)
    write_report(evidence, result, export_path_plan or {})
    return result


def write_report(
    evidence: RunEvidence,
    result: Dict[str, Any],
    export_path_plan: Dict[str, Any],
) -> None:
    checks = result.get("safety_checks") or {}
    lines = [
        "# M25 Export Output Sandbox Guard Report",
        "",
        f"- Run ID: {result['run_id']}",
        f"- Project name: {result.get('project_name', '')}",
        f"- Status: {result['status']}",
        f"- Reason: {result['reason']}",
        f"- Sandbox root: {result.get('sandbox_root', '')}",
        f"- Planned output path: {result.get('planned_output_path', '')}",
        f"- Planned filename: {result.get('planned_filename', '')}",
        "",
        "## Safety checks",
    ]
    for key, value in checks.items():
        lines.append(f"- {key}: {value}")

    lines.extend(["", "## Export path plan"])
    if export_path_plan:
        for key, value in export_path_plan.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- (none)")

    lines.extend(["", "## Discovery files"])
    for path in result.get("discovery_files", []):
        lines.append(f"- {path}")

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
    if result["status"] == "PASS_EXPORT_PATH_PLAN":
        lines.append("Proceed to M26 export preflight plan; no file created.")
    else:
        lines.append("Fix the reported sandbox/path issue and re-run TY_TEST_M25_EXPORT_SANDBOX_GUARD.bat")

    (evidence.folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plan_export_path(
    project_name: str,
    sandbox_root: Path,
    *,
    output_path: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> Tuple[Optional[Path], Dict[str, Any], Dict[str, str]]:
    checks: Dict[str, str] = {}

    if output_path:
        candidate = Path(output_path)
        checks["mode"] = "explicit_path"
    else:
        filename = build_timestamped_filename(project_name, timestamp=timestamp)
        candidate = sandbox_root / filename
        checks["mode"] = "auto_timestamp_filename"

    ext_ok, ext_reason = check_extension(candidate.name)
    checks["extension_allowed"] = "pass" if ext_ok else f"fail: {ext_reason}"
    if not ext_ok:
        return None, {}, checks

    safe_ok, safe_reason = check_filename_safe(candidate.name)
    checks["filename_safe"] = "pass" if safe_ok else f"fail: {safe_reason}"
    if not safe_ok:
        return None, {}, checks

    in_sandbox_ok, in_sandbox_reason = validate_path_in_sandbox(candidate, sandbox_root)
    checks["path_in_sandbox"] = "pass" if in_sandbox_ok else f"fail: {in_sandbox_reason}"
    if not in_sandbox_ok:
        return None, {}, checks

    overwrite_ok, overwrite_reason = check_no_overwrite(candidate)
    checks["no_overwrite"] = "pass" if overwrite_ok else f"fail: {overwrite_reason}"
    if not overwrite_ok:
        return None, {}, checks

    plan = {
        "project_name": project_name,
        "sandbox_root": str(sandbox_root),
        "planned_output_path": str(candidate),
        "planned_filename": candidate.name,
        "export_format": "Spreadsheet/XLSX",
        "export_type": "Activities",
        "overwrite_allowed": False,
        "file_will_be_created": False,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    return candidate, plan, checks


def run_m25(
    project_name: str = "",
    *,
    output_path: Optional[str] = None,
    sandbox_root: Optional[Path] = None,
    evidence: Optional[RunEvidence] = None,
    run_id: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    evidence = evidence or build_evidence(run_id or new_run_id())
    project_name = str(project_name or "").strip()

    if not project_name and not output_path:
        return finish_result(
            evidence,
            project_name,
            "ERROR",
            "project_name and output_path are both empty",
            error="provide --project or --output-path",
        )

    evidence.steps.append("resolve sandbox root")
    resolved_root, root_reason = resolve_sandbox_root(sandbox_root)
    if resolved_root is None:
        return finish_result(
            evidence,
            project_name,
            "FAIL_SANDBOX_ROOT_INVALID",
            root_reason,
            sandbox_root=str(sandbox_root or DEFAULT_SANDBOX_ROOT),
            error=root_reason,
        )

    resolved_root.mkdir(parents=True, exist_ok=True)
    evidence.steps.append(f"sandbox root: {resolved_root}")

    try:
        evidence.steps.append("plan export path")
        candidate, plan, checks = plan_export_path(
            project_name or "export",
            resolved_root,
            output_path=output_path,
            timestamp=timestamp,
        )

        if candidate is None:
            status = "ERROR"
            reason = "export path planning failed"
            if checks.get("path_in_sandbox", "").startswith("fail"):
                status = "FAIL_PATH_OUTSIDE_SANDBOX"
                reason = checks["path_in_sandbox"].split("fail: ", 1)[-1]
            elif checks.get("extension_allowed", "").startswith("fail"):
                status = "FAIL_EXTENSION_NOT_ALLOWED"
                reason = checks["extension_allowed"].split("fail: ", 1)[-1]
            elif checks.get("filename_safe", "").startswith("fail"):
                status = "FAIL_FILENAME_UNSAFE"
                reason = checks["filename_safe"].split("fail: ", 1)[-1]
            elif checks.get("no_overwrite", "").startswith("fail"):
                status = "FAIL_TARGET_ALREADY_EXISTS"
                reason = checks["no_overwrite"].split("fail: ", 1)[-1]
            return finish_result(
                evidence,
                project_name,
                status,
                reason,
                sandbox_root=str(resolved_root),
                planned_output_path=output_path or "",
                safety_checks=checks,
            )

        evidence.steps.append("save export_path_plan.json")
        save_export_path_plan(evidence, plan)

        return finish_result(
            evidence,
            project_name,
            "PASS_EXPORT_PATH_PLAN",
            "Export path plan validated inside sandbox; target does not exist",
            sandbox_root=str(resolved_root),
            planned_output_path=str(candidate),
            planned_filename=candidate.name,
            export_path_plan=plan,
            safety_checks=checks,
        )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return finish_result(
            evidence,
            project_name,
            "ERROR",
            str(exc),
            sandbox_root=str(resolved_root),
            error=traceback.format_exc(),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="M25 export output sandbox guard (no P6)")
    parser.add_argument("--project", default="", help='Project name e.g. "Talison 1275"')
    parser.add_argument("--output-path", default="", help="Explicit output path to validate")
    parser.add_argument(
        "--sandbox-root",
        default="",
        help="Override sandbox root (default: 06_output/exports/sandbox)",
    )
    parser.add_argument("--run-id", default="", help="Optional run id")
    args = parser.parse_args()

    sandbox = Path(args.sandbox_root) if args.sandbox_root.strip() else None
    result = run_m25(
        args.project.strip(),
        output_path=args.output_path.strip() or None,
        sandbox_root=sandbox,
        run_id=args.run_id.strip() or None,
    )
    print(f"Status: {result['status']}")
    print(f"Reason: {result['reason']}")
    if result.get("planned_output_path"):
        print(f"Planned path: {result['planned_output_path']}")
    return 0 if result["status"] == "PASS_EXPORT_PATH_PLAN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
