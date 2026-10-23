"""
کلاینت رسمی APIهای لیارا (PaaS).
مرجع: https://developers.liara.ir/  (OpenAPI: spec/paas.yaml)

نکته‌های مهم که از مستندات رسمی استخراج شده:
- Base URL عملیات اصلی: https://api.liara.ir
- لاگ‌ها روی سرور جداگانه است: https://api.iran.liara.ir
- احراز هویت: هدر Authorization با مقدار Bearer <API_TOKEN>
  (توکن از کنسول لیارا: console.liara.ir -> حساب کاربری -> API Token ساخته می‌شود)
- تمام متدها در صورت خطا یک HTTPStatusError پرتاب می‌کنند که توسط لایه‌ی بالاتر
  (agent/tools) به پیام قابل‌فهم برای کاربر تبدیل می‌شود.

این کلاینت عمداً فقط چیزهایی را پیاده‌سازی کرده که در صورت‌مسئله‌ی چالش لیارا آمده:
راه‌اندازی/دیپلوی، مدیریت سرویس (ری‌استارت، env)، پایش (لاگ/رویداد/متریک CPU و RAM).
هر قابلیتی که واقعاً در API لیارا موجود نبود (مثلاً دسترسی مستقیم به دیسک I/O یا
متریک per-process) در خروجی صریحاً به‌عنوان «محدودیت» اعلام می‌شود، نه شبیه‌سازی.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

API_BASE = "https://api.liara.ir"
LOGS_BASE = "https://api.iran.liara.ir"


class LiaraAPIError(Exception):
    """خطای قابل‌فهم از سمت API لیارا (برای نمایش مستقیم به کاربر)."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class LiaraClient:
    token: str
    team_id: str = ""
    timeout: float = 20.0

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise LiaraAPIError(
                "توکن API لیارا تنظیم نشده. از کنسول لیارا (console.liara.ir) یک API Token بساز و در "
                "متغیر محیطی LIARA_API_TOKEN قرار بده.",
                status_code=401,
            )
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        # لیارا خودِ CLI رسمی‌اش هم روی تمام درخواست‌ها یک query param به اسم
        # teamID می‌فرسته (خالی یعنی حساب شخصی). بدون این، اپ‌های ساخته‌شده
        # زیر یک تیم از دید این کلاینت دیده نمی‌شن.
        params = dict(kwargs.pop("params", None) or {})
        params.setdefault("teamID", self.team_id or "")
        kwargs["params"] = params
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.request(method, url, headers=self._headers(), **kwargs)
        except httpx.RequestError as exc:
            raise LiaraAPIError(f"عدم دسترسی به سرور لیارا: {exc}") from exc

        if resp.status_code == 401:
            raise LiaraAPIError("توکن API لیارا نامعتبر یا منقضی شده است.", status_code=401)
        if resp.status_code == 404:
            raise LiaraAPIError("اپلیکیشن یا مسیر مورد نظر در لیارا پیدا نشد.", status_code=404)
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = str(resp.json())
            except Exception:
                detail = resp.text[:300]
            raise LiaraAPIError(f"خطای لیارا (کد {resp.status_code}): {detail}", status_code=resp.status_code)
        return resp

    # ---------- Apps ----------
    def list_apps(self) -> list[dict]:
        resp = self._request("GET", f"{API_BASE}/v1/projects")
        return resp.json().get("projects", [])

    def get_app(self, name: str) -> dict:
        apps = self.list_apps()
        for a in apps:
            if a.get("project_id") == name:
                return a
        raise LiaraAPIError(f"اپلیکیشنی با نام «{name}» در حساب شما پیدا نشد.", status_code=404)

    # ---------- Deploy / lifecycle ----------
    def restart_app(self, name: str) -> dict:
        resp = self._request("POST", f"{API_BASE}/v1/projects/{name}/actions/restart")
        return {"ok": True, "raw": self._safe_json(resp)}

    def toggle_power(self, name: str, on: bool) -> dict:
        action = "on" if on else "off"
        resp = self._request("POST", f"{API_BASE}/v1/projects/{name}/actions/{action}")
        return {"ok": True, "raw": self._safe_json(resp)}

    # ---------- Env ----------
    def update_envs(self, name: str, variables: dict[str, str]) -> dict:
        payload = {
            "project": name,
            "variables": [{"key": k, "value": v} for k, v in variables.items()],
        }
        resp = self._request("POST", f"{API_BASE}/v1/projects/update-envs", json=payload)
        return {"ok": True, "raw": self._safe_json(resp)}

    # ---------- Observability ----------
    def get_logs(self, name: str, since: str = "30m") -> list[dict]:
        # نکته‌ی مهمی که فقط از سورس واقعی CLI رسمی لیارا (@liara/cli) به‌دست اومد،
        # نه از مستندات عمومی (که این جزئیات رو نداره):
        #   - endpoint واقعی v2 هست، نه v1: /v2/projects/{app}/logs
        #   - روی همون API_BASE (api.liara.ir) است، نه یک ساب‌دامین جدا برای لاگ
        #   - پارامتر اسمش start هست نه since، و باید یک عدد (unix timestamp
        #     به ثانیه) باشه، نه رشته‌ای مثل "30m" — دقیقاً همون چیزی که با خطای
        #     واقعی «The since parameter must be a number» از خود API گرفتیم.
        #   - فرمت پاسخ هم شبیه Loki است: {"data": [{"metaData": {...},
        #     "values": [[timestamp_ns, "متن لاگ"], ...]}, ...]}
        start_ts = int(time.time()) - self._parse_duration_seconds(since)
        resp = self._request(
            "GET",
            f"{API_BASE}/v2/projects/{name}/logs",
            params={"start": start_ts, "direction": "forward", "last_lines": "false"},
        )
        data = self._safe_json(resp)
        groups = data.get("data", []) if isinstance(data, dict) else (data or [])
        lines: list[dict] = []
        for group in groups:
            for value in group.get("values", []) or []:
                try:
                    ts_raw, text = value[0], value[1]
                    ts = int(ts_raw) // 1_000_000_000  # نانوثانیه -> ثانیه
                except Exception:
                    ts, text = 0, str(value)
                lines.append({"timestamp": ts, "message": text})
        lines.sort(key=lambda entry: entry["timestamp"])
        return lines

    @staticmethod
    def _parse_duration_seconds(since: str) -> int:
        since = (since or "30m").strip()
        if since.isdigit():
            return int(since)
        unit = since[-1]
        try:
            qty = int(since[:-1])
        except ValueError:
            return 1800
        return qty * {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 60)

    def get_events(self, name: str) -> list[dict]:
        resp = self._request("GET", f"{API_BASE}/v1/projects/{name}/events")
        return self._safe_json(resp).get("data", [])

    def get_metrics_summary(self, name: str) -> dict:
        resp = self._request("GET", f"{API_BASE}/v1/projects/{name}/metrics/summary")
        return self._safe_json(resp)

    @staticmethod
    def _safe_json(resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except Exception:
            return {}


def client_from_env() -> LiaraClient:
    token = os.environ.get("LIARA_API_TOKEN", "")
    team_id = os.environ.get("LIARA_TEAM_ID", "")
    return LiaraClient(token=token, team_id=team_id)
