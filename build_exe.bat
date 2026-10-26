@echo off
setlocal
cd /d "%~dp0"
echo === CreatorScout: build CreatorScout.exe ===

rem --- pick a Python that the ML libraries support (3.10-3.13), newest first ---
set "PY="
set "PYV="
for %%V in (3.13 3.12 3.11 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && (
      set "PY=py -%%V"
      set "PYV=%%V"
    )
  )
)
if not defined PY (
  where py >nul 2>nul && (
    set "PY=py -3"
    set "PYV=other"
  )
)
if not defined PY (
  where python >nul 2>nul && (
    set "PY=python"
    set "PYV=other"
  )
)
if not defined PY (
  echo [ERROR] Python is not installed. Install Python 3.12 from https://www.python.org/downloads/
  echo         and tick "Add python.exe to PATH" during setup. Then run this file again.
  pause & exit /b 1
)
echo Using Python %PYV%  (%PY%)

set "VENV=.venv-%PYV%"
if not exist "%VENV%\Scripts\python.exe" (
  %PY% -m venv "%VENV%" || (echo [ERROR] Could not create the virtual environment. & pause & exit /b 1)
)
call "%VENV%\Scripts\activate.bat"
python -m pip install --upgrade pip >nul
python -m pip install -r requirements-core.txt pyinstaller || (echo [ERROR] pip install failed. & pause & exit /b 1)

set "ML=1"
set "EXTRA=--collect-binaries onnxruntime --collect-binaries tokenizers"
python -m pip install -r requirements-ml.txt || (
  set "ML=0"
  set "EXTRA="
)

if exist CreatorScout.exe del /q CreatorScout.exe >nul 2>nul
if exist CreatorScout.exe (
  echo [ERROR] CreatorScout.exe is still open. Close the app and run this file again.
  pause & exit /b 1
)

python -m PyInstaller --noconfirm --clean --onefile --windowed --name CreatorScout --icon assets\icon.ico --add-data "assets;assets" --collect-data selenium %EXTRA% app.py || (echo [ERROR] Build failed. & pause & exit /b 1)

copy /y dist\CreatorScout.exe CreatorScout.exe >nul
echo.
echo === DONE: CreatorScout.exe is in this folder. ===
if "%ML%"=="0" (
  echo.
  echo [NOTE] The semantic model is OFF in this build: its library does not support Python %PYV% yet.
  echo        The app works fine with keyword matching. To turn the semantic model on:
  echo        install Python 3.12 from https://www.python.org/downloads/ and run this file again.
)
pause
