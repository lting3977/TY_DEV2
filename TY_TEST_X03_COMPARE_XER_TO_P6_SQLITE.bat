@echo off
cd /d C:\TY_DEV2
python 04_modules\x_modules\x03_compare_xer_to_p6_sqlite.py --xer-db "C:\TY_DEV2\06_output\x_modules\xer_sqlite_research\runs\20260701_154557\x01_xer_to_analysis_sqlite\xer_analysis.sqlite" --p6-db "C:\TY_DEV2\test_data\p6_sqlite_copies\P6_TEST_COPY_001.db"
pause
