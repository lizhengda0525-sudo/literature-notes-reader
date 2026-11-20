@echo off
setlocal
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
pushd "%~dp0"
if exist "%~dp0.venv\.workspace-ready" (
  "%~dp0.venv\Scripts\python.exe" -B "%~dp090_system\scripts\literature.py" %*
) else (
  python -B "%~dp090_system\scripts\literature.py" %*
)
set "lit_result=%errorlevel%"
popd
exit /b %lit_result%
