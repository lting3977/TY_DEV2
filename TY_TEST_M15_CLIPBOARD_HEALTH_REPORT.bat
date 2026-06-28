@echo off
cd /d C:\TY_DEV2
python 04_modules\m03_open_project_by_name.py --project "Talison 1275"
python 04_modules\m04_check_project_opened.py --project "Talison 1275"
python 04_modules\m06_go_to_activities.py --project "Talison 1275"
python 04_modules\m09_read_project_data_date.py --project "Talison 1275"
python 04_modules\m14_copy_visible_activity_rows_multi_select.py --project "Talison 1275" --max-rows 3
python 04_modules\m15_clipboard_multi_row_health_report.py --project "Talison 1275"
pause
