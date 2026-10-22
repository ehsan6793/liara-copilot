@echo off
cd /d "%~dp0"
echo Setting up Liara Copilot environment...
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Done. Now:
echo 1. Copy .env.example to .env and put your LIARA_API_TOKEN in it.
echo 2. Run 2_run.bat
pause
