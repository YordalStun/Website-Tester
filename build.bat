@echo off
rem Builds WebsiteTester.exe on a Windows PC (needs Python 3.12 from python.org).
cd /d "%~dp0"
if not exist .venv (
    python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt || goto :error
python -m pytest -q || goto :error
python build.py || goto :error
echo.
echo Done! Your app is here: %~dp0dist\WebsiteTester.exe
pause
exit /b 0
:error
echo.
echo Something went wrong - see the messages above.
pause
exit /b 1
