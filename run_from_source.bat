@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py -3 -m pip install -q -r requirements.txt & py -3 app.py & exit /b)
python -m pip install -q -r requirements.txt
python app.py
