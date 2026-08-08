@echo off
setlocal
cd /d "%~dp0.."

echo ==================================================
echo [09] Add labelled negative samples to dataset
echo ==================================================
echo.
echo Put no-enemy frames (menus, vehicles, friendly NPCs,
echo scoreboards, kill cams of allies, etc.) in
echo   raw_frames\negatives\
echo before running this. They will be copied with EMPTY
echo labels into dataset\train\ so the model learns what
echo is NOT a target.
echo.

if not exist ".venv\Scripts\python.exe" (
  call scripts\01_setup_environment.bat
  if errorlevel 1 exit /b 1
)
call .venv\Scripts\activate.bat

if not exist raw_frames\negatives (
  echo ERROR: raw_frames\negatives does not exist.
  echo Create it and drop no-enemy frames in, then re-run.
  exit /b 2
)

python src\mine_negatives.py --in raw_frames\negatives --out dataset\train %*
set RC=%ERRORLEVEL%

if %RC% EQU 0 (
  echo Done. Aim for 10-25%% negatives in your training pool.
) else (
  echo Negative-mining failed with code %RC%.
)
exit /b %RC%
