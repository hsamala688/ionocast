@echo off
cd /d "%~dp0\..\.."
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
echo [%DATE% %TIME%] starting generate >> "predicted_data\predicted\generate_run.log"
".venv\Scripts\python.exe" -u "predicted_data\predicted\generate.py" --years 2023,2024,2025 --batch-size 8 >> "predicted_data\predicted\generate_run.log" 2>> "predicted_data\predicted\generate_run.err"
echo [%DATE% %TIME%] exit=%ERRORLEVEL% >> "predicted_data\predicted\generate_run.log"
