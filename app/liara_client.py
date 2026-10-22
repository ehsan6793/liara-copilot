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
        resp = self._request(
            "GET", f"{LOGS_BASE}/v1/projects/{name}/logs", params={"since": since}
        )
        data = self._safe_json(resp)
        return data if isinstance(data, list) else data.get("logs", [])

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
    return LiaraClient(token=token)
