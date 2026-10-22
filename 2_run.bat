@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
if not exist .env (
  echo No .env file found. Copy .env.example to .env and set LIARA_API_TOKEN first.
  pause
  exit /b 1
)
for /f "usebackq tokens=1,* delims==" %%A in (`findstr /v "^#" .env`) do set %%A=%%B
echo Running on http://127.0.0.1:8010 ...
start "" http://127.0.0.1:8010
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
pause
