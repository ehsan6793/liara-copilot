"""
دستیار همکار فنی لیارا.

دو حالت:
  1) قاعده‌محور (پیش‌فرض، همیشه کار می‌کند؛ کلید LLM لازم ندارد).
  2) LLM tool-calling (اختیاری) — اگر LLM_API_KEY تنظیم شده باشد، جمله‌های پیچیده‌تر و
     چندشرطی را هم می‌فهمد؛ در غیر این صورت یا در صورت خطا، به حالت قاعده‌محور برمی‌گردد
     (دقیقاً همان الگویی که در پروژه‌ی ترب‌گرد هم استفاده شد).

خروجی هر پاسخ شامل:
  - reply: متن قابل‌فهم برای کاربر
  - cards: داده‌های ساختاریافته برای نمایش در UI (جدول لاگ، نمودار متریک، لیست علل)
  - pending_action: اگر اقدام پیشنهادی «حساس» باشد، این اقدام اجرا نمی‌شود و منتظر
    تأیید صریح کاربر (دکمه‌ی تأیید در UI) می‌ماند.
"""
from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from .liara_client import LiaraClient, LiaraAPIError
from .tools import build_tools, diagnose, RISK_RISKY

PENDING: dict[str, dict] = {}  # action_id -> {tool, args, description}


@dataclass
class AgentResponse:
    reply: str
    cards: list[dict] = field(default_factory=list)
    pending_action: dict | None = None


class Agent:
    def __init__(self, client: LiaraClient):
        self.client = client
        self.tools = build_tools(client)
        self.last_app: str | None = None

    # ------------------------------------------------------------------
    def handle(self, message: str, app_hint: str | None = None) -> AgentResponse:
        message = message.strip()
        app = app_hint or self._extract_app_name(message) or self.last_app

        try:
            apps = self.tools["list_apps"].fn().data or []
        except Exception:
            apps = []
        app_names = [a.get("project_id") for a in apps if a.get("project_id")]

        if not app and app_names:
            if len(app_names) == 1:
                app = app_names[0]
            elif self._wants_list(message):
                return self._reply_app_list(apps)

        if app:
            self.last_app = app

        # ---- مسیرهای نیت (intent) ----
        if self._wants_list(message) and not self._wants_logs(message):
            return self._reply_app_list(apps)

        if not app:
            return AgentResponse(
                "اول باید بدونم روی کدوم اپ کار کنیم. اسم اپ رو بگو، یا بپرس «لیست اپ‌هام چیه»."
            )

        if self._wants_restart(message):
            return self._propose_action(
                tool="restart_app",
                args={"name": app},
                description=f"ری‌استارت اپ «{app}»",
                reply=f"می‌خوای اپ «{app}» رو ری‌استارت کنم؟ این یه عملیات حساسه و قبل از اجرا تأیید لازم داره.",
            )

        if self._wants_env_set(message):
            kv = self._extract_env_kv(message)
            if not kv:
                return AgentResponse(
                    "فرمت متغیر محیطی رو نفهمیدم. این‌جوری بگو: «env رو KEY=VALUE کن» (مثلاً: env رو MODE=normal کن)."
                )
            return self._propose_action(
                tool="set_env_vars",
                args={"name": app, "variables": kv},
                description=f"تنظیم متغیر(های) محیطی {kv} روی اپ «{app}»",
                reply=f"می‌خوام روی اپ «{app}» این متغیر(ها) رو تنظیم کنم: {kv}. این تغییر روی سرویس واقعیه — تأیید می‌کنی؟",
            )

        if self._wants_status(message) and not self._wants_diagnose(message):
            return self._reply_status(app)

        if self._wants_metrics(message) and not self._wants_diagnose(message):
            return self._reply_metrics(app)

        if self._wants_logs(message) and not self._wants_diagnose(message):
            return self._reply_logs(app)

        if self._wants_diagnose(message):
            return self._reply_diagnose(app)

        # پیش‌فرض: یک خلاصهٔ وضعیت + راهنما
        return AgentResponse(
            f"متوجه نشدم دقیقاً چی می‌خوای از اپ «{app}». می‌تونی بپرسی: «وضعیت چطوره»، «چرا ارور می‌ده»، "
            f"«چرا کند شده»، «لاگ‌ها رو نشون بده»، «منابع/متریک چطوره»، «ری‌استارت کن» یا «env رو KEY=VALUE کن»."
        )

    # ------------------------------------------------------------------
    def confirm(self, action_id: str, approve: bool) -> AgentResponse:
        pending = PENDING.pop(action_id, None)
        if not pending:
            return AgentResponse("این درخواست دیگه معتبر نیست (شاید قبلاً اجرا یا لغو شده).")
        if not approve:
            return AgentResponse("باشه، لغو شد. هیچ تغییری روی سرویس اعمال نشد.")

        tool = self.tools[pending["tool"]]
        result = tool.fn(**pending["args"])
        if not result.ok:
            return AgentResponse(f"اجرا نشد: {result.error}")

        reply = f"انجام شد: {pending['description']}."
        cards = [{"type": "result", "title": "نتیجهٔ عملیات", "data": result.data}]

        if pending["tool"] in ("restart_app", "set_env_vars"):
            reply += " چند ثانیه صبر کن تا لاگ تازه رو بگیرم و مطمئن شم مشکل برطرف شده..."
            try:
                logs_after = self.client.get_logs(pending["args"]["name"], since="2m")
                cards.append({"type": "logs", "title": "لاگ بعد از اقدام", "data": logs_after[-10:]})
                still_erroring = any(re.search(r"(?i)error|exception|500", str(l.get("message", ""))) for l in logs_after)
                reply += " ❌ هنوز نشونه‌ی خطا در لاگ هست، احتمالاً نیاز به بررسی بیشتر." if still_erroring else " ✅ در لاگ تازه نشونه‌ای از خطا دیده نمی‌شه."
            except LiaraAPIError:
                pass

        return AgentResponse(reply, cards=cards)

    # ------------------------------------------------------------------ helpers
    def _propose_action(self, tool: str, args: dict, description: str, reply: str) -> AgentResponse:
        action_id = uuid.uuid4().hex[:10]
        PENDING[action_id] = {"tool": tool, "args": args, "description": description}
        return AgentResponse(
            reply,
            pending_action={"id": action_id, "tool": tool, "description": description, "risk": RISK_RISKY},
        )

    def _reply_app_list(self, apps: list[dict]) -> AgentResponse:
        if not apps:
            return AgentResponse("هیچ اپی روی حساب لیارای تنظیم‌شده پیدا نکردم؛ توکن API رو چک کن.")
        lines = "\n".join(f"- {a.get('project_id')} ({a.get('type')}, وضعیت: {a.get('status')})" for a in apps)
        return AgentResponse(f"اپ‌های حساب شما:\n{lines}", cards=[{"type": "apps", "title": "اپ‌ها", "data": apps}])

    def _reply_status(self, app: str) -> AgentResponse:
        r = self.tools["get_app_status"].fn(name=app)
        if not r.ok:
            return AgentResponse(r.error or "خطا در دریافت وضعیت.")
        d = r.data
        return AgentResponse(
            f"اپ «{app}»: وضعیت {d.get('status')}، پلن {d.get('planID')}، نوع {d.get('type')}.",
            cards=[{"type": "status", "title": "وضعیت اپ", "data": d}],
        )

    def _reply_metrics(self, app: str) -> AgentResponse:
        r = self.tools["get_metrics"].fn(name=app)
        if not r.ok:
            return AgentResponse(r.error or "خطا در دریافت متریک.")
        return AgentResponse(
            f"متریک مصرف منابع اپ «{app}» رو آوردم؛ توی نمودار پایین ببین.",
            cards=[{"type": "metrics", "title": "CPU / RAM", "data": r.data}],
        )

    def _reply_logs(self, app: str) -> AgentResponse:
        r = self.tools["get_logs"].fn(name=app, since="30m")
        if not r.ok:
            return AgentResponse(r.error or "خطا در دریافت لاگ.")
        logs = r.data or []
        return AgentResponse(
            f"{len(logs)} خط لاگ از ۳۰ دقیقهٔ اخیر اپ «{app}» رو آوردم.",
            cards=[{"type": "logs", "title": "لاگ‌های اخیر", "data": logs[-50:]}],
        )

    def _reply_diagnose(self, app: str) -> AgentResponse:
        logs_r = self.tools["get_logs"].fn(name=app, since="1h")
        events_r = self.tools["get_events"].fn(name=app)
        metrics_r = self.tools["get_metrics"].fn(name=app)

        if not logs_r.ok:
            return AgentResponse(f"برای عیب‌یابی به لاگ نیاز دارم ولی نگرفتمش: {logs_r.error}")

        logs = logs_r.data or []
        events = events_r.data if events_r.ok else []
        metrics = metrics_r.data if metrics_r.ok else None

        diag = diagnose(logs, events, metrics)
        cards = [
            {"type": "logs", "title": "لاگ بررسی‌شده (۱ ساعت اخیر)", "data": logs[-30:]},
            {"type": "causes", "title": "علت‌های احتمالی (اولویت‌بندی‌شده)", "data": diag.probable_causes},
        ]
        if metrics:
            cards.append({"type": "metrics", "title": "CPU / RAM", "data": metrics})

        reply = diag.summary
        if diag.probable_causes:
            reply += "\n\nعلت‌های محتمل به ترتیب اولویت:\n" + "\n".join(
                f"{i+1}. {c['cause']} (اطمینان ~{c['score']}٪)" for i, c in enumerate(diag.probable_causes)
            )

        if diag.recommended_tool == "restart_app":
            action_id = uuid.uuid4().hex[:10]
            PENDING[action_id] = {"tool": "restart_app", "args": {"name": app}, "description": f"ری‌استارت اپ «{app}»"}
            reply += f"\n\nپیشنهاد من: {diag.recommended_action}. برای اجرا، تأیید کن."
            return AgentResponse(reply, cards=cards, pending_action={"id": action_id, "tool": "restart_app", "description": f"ری‌استارت اپ «{app}»", "risk": RISK_RISKY})

        if diag.recommended_action:
            reply += f"\n\nپیشنهاد من: {diag.recommended_action}."
            if diag.recommended_tool == "set_env_vars":
                reply += " بگو کدوم متغیر رو با چه مقدار تنظیم کنم (مثلاً «env رو MODE=normal کن»)."

        return AgentResponse(reply, cards=cards)

    # ------------------------------------------------------------------ NLU قاعده‌محور
    @staticmethod
    def _extract_app_name(message: str) -> str | None:
        m = re.search(r"اپ\s+([a-zA-Z0-9_\-]{2,40})", message)
        return m.group(1) if m else None

    @staticmethod
    def _extract_env_kv(message: str) -> dict[str, str]:
        pairs = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^\s,،]+)", message)
        return {k: v for k, v in pairs}

    @staticmethod
    def _wants_list(m: str) -> bool:
        return any(w in m for w in ["لیست اپ", "چه اپی", "اپ‌هام", "اپ هام", "اپلیکیشن‌ها", "کدوم اپ"])

    @staticmethod
    def _wants_status(m: str) -> bool:
        return any(w in m for w in ["وضعیت", "حال", "اپ چطوره", "بالاست", "رو هست"])

    @staticmethod
    def _wants_logs(m: str) -> bool:
        return "لاگ" in m or "log" in m.lower()

    @staticmethod
    def _wants_metrics(m: str) -> bool:
        return any(w in m for w in ["متریک", "منابع", "cpu", "رم", "حافظه", "ram"])

    @staticmethod
    def _wants_diagnose(m: str) -> bool:
        return any(w in m for w in ["ارور", "خطا", "500", "کند شده", "کند شد", "قطع", "کار نمی‌کنه", "مشکل", "بالا نمیاد", "داون"])

    @staticmethod
    def _wants_restart(m: str) -> bool:
        return "ری‌استارت" in m or "ریستارت" in m or "restart" in m.lower()

    @staticmethod
    def _wants_env_set(m: str) -> bool:
        return ("env" in m.lower() or "متغیر محیط" in m) and "=" in m


def agent_from_env() -> Agent:
    from .liara_client import client_from_env

    return Agent(client_from_env())
