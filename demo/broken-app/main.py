"""
اپ نمونه برای سناریوی دمو: یک اپ ساده که وقتی متغیر محیطی DB_HOST تنظیم نشده
باشد (یا BROKEN=1 باشد)، روی هر درخواست خطای 500 می‌دهد — دقیقا شبیه یک باگ واقعی
"یادم رفت env رو روی لیارا ست کنم بعد از دیپلوی".
"""
import os
import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()
START = time.time()


@app.get("/")
def home():
    return {"app": "demo-shop", "uptime_s": round(time.time() - START, 1)}


@app.get("/api/data")
def data():
    db_host = os.environ.get("DB_HOST")
    if not db_host or os.environ.get("BROKEN") == "1":
        # این دقیقا همون چیزیه که در لاگ واقعی یک اپ کرش‌کرده می‌بینیم
        raise RuntimeError("DB_HOST environment variable is not set — cannot connect to database")
    return JSONResponse({"ok": True, "db_host": db_host, "items": [1, 2, 3]})


@app.get("/api/health")
def health():
    return {"status": "ok"}
