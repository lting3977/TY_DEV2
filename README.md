# TY_DEV2 — Primavera P6 Automation Assistant

TY is a Primavera P6 automation assistant for read-only health checks, workspace navigation, and controlled export-wizard discovery.

## Module status

- **M03–M19:** Frozen stable — do not modify unless a later module exposes a shared bug.
- **M20:** Currently under fix/testing (Activities export-type discovery).
- **M21+:** Gated — real export modules must not run without manual approval.

## Layout

| Path | Purpose |
|------|---------|
| `01_config/` | TY configuration |
| `02_accessibility/` | Brain, hand, and accessibility helpers |
| `02_eye/` | Screenshot and OCR |
| `02_hand/` | P6 window preparation |
| `03_screen_library/` | Screen rules and templates |
| `04_modules/` | M03+ automation modules |
| `05_orchestrator/` | Test matrices and runners |
| `06_output/` | Run evidence (gitignored) |

## Safety

- P6-window crop OCR only — never full desktop.
- No Finish, save, delete, or unapproved export file creation.
- Run outputs, screenshots, OCR, and exports stay local under `06_output/` (not committed).

## Quick start

```bat
cd C:\TY_DEV2
TY_RUN_READ_ONLY_HEALTH_CHECK.bat
```

See `04_modules\MODULE_INDEX.md` for module commands and batch tests.
