@echo off
cd /d C:\TY_DEV2
python 04_modules\m03_open_project_by_name.py --project "Talison 1275"
python 04_modules\m04_check_project_opened.py --project "Talison 1275"
python 04_modules\m06_go_to_activities.py --project "Talison 1275"
python 04_modules\m07_read_activity_table_snapshot.py --project "Talison 1275"
python 04_modules\m08_read_activity_table_structured.py --project "Talison 1275"
python 04_modules\m09_read_project_data_date.py --project "Talison 1275"
python 04_modules\m10_compare_data_date_to_activity_dates.py --project "Talison 1275"
python 04_modules\m11_generate_planning_health_report.py --project "Talison 1275"
pause
