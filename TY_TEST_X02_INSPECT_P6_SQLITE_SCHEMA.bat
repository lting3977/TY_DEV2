@echo off
cd /d C:\TY_DEV2
set /p P6_DB_PATH=Enter full copied P6 SQLite DB path:
python 04_modules\x_modules\x02_inspect_sqlite_schema.py --db "%P6_DB_PATH%"
pause
