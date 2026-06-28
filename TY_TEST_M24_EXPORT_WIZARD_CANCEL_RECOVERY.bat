@echo off

cd /d C:\TY_DEV2

python 04_modules\m03_open_project_by_name.py --project "Talison 1275"

python 04_modules\m04_check_project_opened.py --project "Talison 1275"

python 04_modules\m06_go_to_activities.py --project "Talison 1275"

python 04_modules\m24_export_wizard_cancel_recovery_from_known_screens.py --project "Talison 1275" --screen format

pause

