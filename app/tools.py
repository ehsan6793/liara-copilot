"""
ابزارهای دستیار (tool layer) — هر ابزار یک قابلیت از صورت‌مسئله را پیاده می‌کند.
هر ابزار یک سطح ریسک دارد:
  - "safe"  : فقط خواندن اطلاعات؛ بدون تأیید کاربر اجرا می‌شود.
  - "risky" : تغییر در سرویس واقعی (ری‌استارت، env، روشن/خاموش)؛ نیاز به تأیید صریح کاربر دارد.

تابع‌های تشخیص خطا (diagnose_*) روی داده‌های واقعی لاگ/رویداد/متریک کار می‌کنند؛
این‌ها "قاعده‌محور" هستند و در نبود کلید LLM هم کار می‌کنند (مطابق الزام
«در صورت نیاز، به منطق قاعده‌محور برمی‌گردد» که در پروژه‌ی ترب‌گرد هم رعایت شده بود).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .liara_client import LiaraClient, LiaraAPIError

RISK_SAFE = "safe"
RISK_RISKY = "risky"


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str | None = None


@dataclass
class ToolSpec:
    name: str
    risk: str
    description: str
    fn: Callable[..., ToolResult]


def build_tools(client: LiaraClient) -> dict[str, ToolSpec]:
    def list_apps(**_) -> ToolResult:
        try:
            return ToolResult(True, client.list_apps())
        except LiaraAPIError as e:
            return ToolResult(False, error=e.message)

    def get_app_status(name: str, **_) -> ToolResult:
        try:
            return ToolResult(True, client.get_app(name))
        except LiaraAPIError as e:
            return ToolResult(False, error=e.message)

    def get_logs(name: str, since: str = "30m", **_) -> ToolResult:
        try:
            return ToolResult(True, client.get_logs(name, since))
        except LiaraAPIError as e:
            return ToolResult(False, error=e.message)

    def get_events(name: str, **_) -> ToolResult:
        try:
            return ToolResult(True, client.get_events(name))
        except LiaraAPIError as e:
            return ToolResult(False, error=e.message)

    def get_metrics(name: str, **_) -> ToolResult:
        try:
            return ToolResult(True, client.get_metrics_summary(name))
        except LiaraAPIError as e:
            return ToolResult(False, error=e.message)

    def restart_app(name: str, **_) -> ToolResult:
        try:
            return ToolResult(True, client.restart_app(name))
        except LiaraAPIError as e:
            return ToolResult(False, error=e.message)

    def set_env_vars(name: str, variables: dict[str, str], **_) -> ToolResult:
        try:
            return ToolResult(True, client.update_envs(name, variables))
        except LiaraAPIError as e:
            return ToolResult(False, error=e.message)

    def toggle_power(name: str, on: bool, **_) -> ToolResult:
        try:
            return ToolResult(True, client.toggle_power(name, on))
        except LiaraAPIError as e:
            return ToolResult(False, error=e.message)

    return {
        "list_apps": ToolSpec("list_apps", RISK_SAFE, "لیست اپلیکیشن‌های حساب کاربر", list_apps),
        "get_app_status": ToolSpec("get_app_status", RISK_SAFE, "وضعیت فعلی یک اپ", get_app_status),
        "get_logs": ToolSpec("get_logs", RISK_SAFE, "لاگ‌های اخیر یک اپ", get_logs),
        "get_events": ToolSpec("get_events", RISK_SAFE, "رویدادهای اخیر یک اپ", get_events),
        "get_metrics": ToolSpec("get_metrics", RISK_SAFE, "متریک CPU/RAM/دیسک یک اپ", get_metrics),
        "restart_app": ToolSpec("restart_app", RISK_RISKY, "ری‌استارت اپ", restart_app),
        "set_env_vars": ToolSpec("set_env_vars", RISK_RISKY, "تغییر متغیرهای محیطی اپ", set_env_vars),
        "toggle_power": ToolSpec("toggle_power", RISK_RISKY, "روشن/خاموش کردن اپ", toggle_power),
    }


# ---------------------------------------------------------------------------
# تشخیص هوشمند قاعده‌محور (بخش «عیب‌یابی هوشمند» صورت‌مسئله)
# ---------------------------------------------------------------------------

ERROR_PATTERNS: list[tuple[str, str, int]] = [
    # (regex, عنوان علت احتمالی, وزن اولویت - عدد بزرگ‌تر = مهم‌تر)
    (r"(?i)out of memory|oom[-_ ]?killed|cannot allocate memory", "کمبود حافظه (RAM) و Kill شدن پردازه توسط سیستم", 95),
    (r"(?i)econnrefused|connection refused.*(5432|3306|27017|6379)", "عدم دسترسی به دیتابیس/سرویس وابسته (رد اتصال)", 90),
    (r"(?i)address already in use|eaddrinuse", "تعارض پورت یا چند نمونه هم‌زمان اجرا شدن اپ", 70),
    (r"(?i)modulenotfounderror|cannot find module|no such file or directory.*node_modules", "وابستگی/پکیج نصب‌نشده در دیپلوی", 80),
    (r"(?i)permission denied", "عدم دسترسی کافی به فایل یا پورت", 60),
    (r"(?i)\b500\b.*internal server error|internal server error", "خطای داخلی اپلیکیشن (کد ۵۰۰)", 85),
    (r"(?i)env.*(undefined|not set|missing)|KeyError|is not defined.*process\.env", "متغیر محیطی لازم تنظیم نشده", 88),
    (r"(?i)timeout|timed out|etimedout", "تایم‌اوت در پاسخ‌دهی یا اتصال به سرویس بیرونی", 55),
    (r"(?i)segmentation fault|panic:|unhandled exception|traceback \(most recent call last\)", "کرش برنامه (استثنای مدیریت‌نشده)", 75),
]


@dataclass
class Diagnosis:
    probable_causes: list[dict] = field(default_factory=list)  # [{cause, score, evidence}]
    cpu_high: bool = False
    mem_high: bool = False
    recommended_action: str | None = None
    recommended_tool: str | None = None
    recommended_args: dict | None = None
    summary: str = ""


def diagnose(logs: list[dict], events: list[dict], metrics: dict | None) -> Diagnosis:
    """تحلیل قاعده‌محور لاگ/رویداد/متریک برای استخراج علت‌های احتمالی خطا، اولویت‌بندی‌شده."""
    found: dict[str, dict] = {}
    for entry in logs or []:
        msg = str(entry.get("message", ""))
        for pattern, cause, score in ERROR_PATTERNS:
            if re.search(pattern, msg):
                rec = found.setdefault(cause, {"cause": cause, "score": score, "evidence": [], "count": 0})
                rec["count"] += 1
                if len(rec["evidence"]) < 2:
                    rec["evidence"].append(msg[:200])

    diag = Diagnosis()
    # تشدید امتیاز بر اساس تکرار
    for rec in found.values():
        rec["score"] = min(99, rec["score"] + min(rec["count"], 5))
    diag.probable_causes = sorted(found.values(), key=lambda r: -r["score"])

    cpu_vals: list[float] = []
    mem_vals: list[float] = []
    if metrics:
        try:
            for item in metrics.get("cpuUsage", []):
                cpu_vals += [float(v) for v in item.get("value", []) if _is_num(v)]
            for item in metrics.get("memoryUsage", []):
                mem_vals += [float(v) for v in item.get("value", []) if _is_num(v)]
        except Exception:
            pass
    if cpu_vals and max(cpu_vals) > 85:
        diag.cpu_high = True
        diag.probable_causes.insert(0, {"cause": "مصرف CPU بالا (نزدیک به سقف منابع پلن فعلی)", "score": 92, "evidence": [f"حداکثر مصرف مشاهده‌شده: {max(cpu_vals):.0f}%"], "count": 1})
    if mem_vals and max(mem_vals) > 85:
        diag.mem_high = True
        diag.probable_causes.insert(0, {"cause": "مصرف RAM بالا (نزدیک به سقف منابع پلن فعلی)", "score": 94, "evidence": [f"حداکثر مصرف مشاهده‌شده: {max(mem_vals):.0f}%"], "count": 1})

    diag.probable_causes = sorted(diag.probable_causes, key=lambda r: -r["score"])[:5]

    if not diag.probable_causes:
        diag.summary = "در لاگ‌ها و متریک‌های اخیر، نشانهٔ مشخصی از خطا یا فشار منابع پیدا نشد."
        return diag

    top = diag.probable_causes[0]
    diag.summary = f"علت محتمل‌ترین: «{top['cause']}» (اطمینان تقریبی {top['score']}٪)."

    # پیشنهاد اقدام اصلاحی بر اساس علت محتمل‌ترین
    cause = top["cause"]
    if "متغیر محیطی" in cause:
        diag.recommended_action = "تنظیم/اصلاح متغیر محیطی مربوطه و سپس ری‌استارت اپ"
        diag.recommended_tool = "set_env_vars"
    elif "حافظه" in cause or "RAM" in cause:
        diag.recommended_action = "ری‌استارت فوری اپ برای آزادسازی حافظه و سپس بررسی ارتقای پلن"
        diag.recommended_tool = "restart_app"
    elif "دیتابیس" in cause:
        diag.recommended_action = "بررسی دسترس‌پذیری سرویس دیتابیس و ری‌استارت اپ پس از رفع اتصال"
        diag.recommended_tool = "restart_app"
    elif "کرش" in cause or "CPU" in cause or "تایم‌اوت" in cause or "۵۰۰" in cause or "500" in cause:
        diag.recommended_action = "ری‌استارت اپ برای بازگشت به حالت پایدار و پایش مجدد لاگ‌ها"
        diag.recommended_tool = "restart_app"
    else:
        diag.recommended_action = "بررسی دستی لاگ‌های بالا توسط کاربر"

    return diag


def _is_num(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False
