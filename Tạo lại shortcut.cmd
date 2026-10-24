@echo off
chcp 65001 >nul
rem Tao lai shortcut Screen Translator (Desktop + Start Menu, va Startup neu dang bat) cho thu muc nay.
rem Bam dup de chay, vi du sau khi chuyen thu muc app sang cho khac.
cd /d "%~dp0"
rem SCREEN_TRANSLATOR_PYTHON (nhu run.ps1) chon Python khac venv mac dinh.
set "PY=%USERPROFILE%\.venvs\screen-translator\Scripts\python.exe"
if defined SCREEN_TRANSLATOR_PYTHON set "PY=%SCREEN_TRANSLATOR_PYTHON%"
if not exist "%PY%" (
  echo Không tìm thấy Python của app: %PY%
  echo Cài lại môi trường theo README rồi chạy lại tệp này.
  pause
  exit /b 1
)
"%PY%" -m tools.install_shortcut %*
echo.
pause
