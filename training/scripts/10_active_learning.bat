@echo off
setlocal
cd /d "%~dp0.."

echo ==================================================
echo [10] Active learning: surface frames worth labelling next
echo ==================================================
echo.
echo Requires a trained student model. Runs it (and optionally
echo a larger teacher) across raw_frames\ and copies the
echo low-confidence / disagreement frames into
echo   outputs\review\uncertain\
echo Label THOSE by hand, drop into dataset/train/, retrain.
echo.

if not exist ".venv\Scripts\python.exe" (
  call scripts\01_setup_environment.bat
  if errorlevel 1 exit /b 1
)
call .venv\Scripts\activate.bat

set STUDENT=outputs\runs\detect\train\weights\best.pt
if not exist %STUDENT% (
  echo ERROR: student weights not found at %STUDENT%.
  echo Train the model first via scripts\04_train_adaptive.bat
  exit /b 2
)

python src\active_learning.py ^
    --student %STUDENT% ^
    --teacher yolov8x.pt ^
    --frames raw_frames ^
    --out outputs\review ^
    --student-imgsz 256 ^
    --teacher-imgsz 1280 ^
    --max-review 300 ^
    %*

set RC=%ERRORLEVEL%
if %RC% EQU 0 (
  echo.
  echo Review frames -> training\outputs\review\uncertain
) else (
  echo Active learning failed with code %RC%.
)
exit /b %RC%
