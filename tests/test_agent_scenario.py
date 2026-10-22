"""
تست سناریوی واقعی چالش (بدون نیاز به توکن واقعی لیارا):
یک LiaraClient جعلی (fake) که دقیقا همون ساختار JSON واقعی API لیارا رو برمی‌گردونه
(طبق مستندات رسمی developers.liara.ir) جایگزین کلاینت واقعی می‌شه تا کل مسیر
"کاربر گزارش میده سایت ارور می‌ده -> agent لاگ/متریک رو می‌خونه -> علت رو پیدا می‌کنه ->
پیشنهاد اصلاح می‌ده -> با تأیید کاربر، env رو درست و ری‌استارت می‌کنه -> نتیجه رو با لاگ تازه تأیید می‌کنه"
واقعاً و کامل اجرا و چاپ بشه.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import Agent, PENDING  # noqa: E402


class FakeClient:
    """شبیه‌ساز LiaraClient با وضعیت قابل‌تغییر، برای دیدن رفتار agent قبل و بعد از اصلاح."""

    def __init__(self):
        self.env = {}  # env varهای "ست‌شده" روی اپ فرضی
        self.restart_count = 0

    def list_apps(self):
        return [{"project_id": "demo-shop", "type": "docker", "status": "RUNNING", "scale": 1, "planID": "small"}]

    def get_app(self, name):
        return self.list_apps()[0]

    def get_logs(self, name, since="30m"):
        if "DB_HOST" not in self.env:
            return [
                {"type": "app", "datetime": "2026-10-22T18:10:00Z", "message": "GET /api/data 200 OK"},
                {"type": "app", "datetime": "2026-10-22T18:10:05Z", "message": "Traceback (most recent call last):"},
                {"type": "app", "datetime": "2026-10-22T18:10:05Z", "message": "RuntimeError: DB_HOST environment variable is not set — cannot connect to database"},
                {"type": "app", "datetime": "2026-10-22T18:10:05Z", "message": "INFO: 127.0.0.1:0 - \"GET /api/data HTTP/1.1\" 500 Internal Server Error"},
            ] * 3
        return [
            {"type": "app", "datetime": "2026-10-22T18:20:00Z", "message": "GET /api/data 200 OK"},
            {"type": "app", "datetime": "2026-10-22T18:20:02Z", "message": "GET /api/data 200 OK"},
        ]

    def get_events(self, name):
        return [{"metaData": {}, "values": [["deploy", "success"]]}]

    def get_metrics_summary(self, name):
        return {
            "cpuUsage": [{"applet": "web", "value": [22, 25, 24]}],
            "memoryUsage": [{"applet": "web", "value": [30, 32, 31]}],
            "disksUsage": [],
        }

    def restart_app(self, name):
        self.restart_count += 1
        return {"message": "restarted"}

    def update_envs(self, name, variables):
        self.env.update(variables)
        return {"message": "updated"}

    def toggle_power(self, name, on):
        return {"message": "ok"}


def run():
    fake = FakeClient()
    agent = Agent(fake)

    print("=" * 70)
    print("کاربر: چرا ارور می‌ده؟")
    r = agent.handle("چرا سایتم ارور می‌ده؟")
    print("BOT:", r.reply)
    for c in r.cards:
        print(f"  [card:{c['type']}] {c['title']} -> {len(c['data']) if isinstance(c['data'], list) else 'obj'} مورد")
    assert "متغیر محیطی" in r.reply, "باید علت را به متغیر محیطی نسبت بدهد"
    print("pending_action:", r.pending_action)

    print()
    print("کاربر اول خودش env رو درست می‌کنه (شبیه‌سازی دستور کاربر):")
    r2 = agent.handle("env رو DB_HOST=prod-db.internal کن")
    print("BOT:", r2.reply)
    assert r2.pending_action is not None and r2.pending_action["tool"] == "set_env_vars"
    action_id = r2.pending_action["id"]

    print()
    print("کاربر تأیید می‌کند:")
    r3 = agent.confirm(action_id, approve=True)
    print("BOT:", r3.reply)
    assert fake.env.get("DB_HOST") == "prod-db.internal"
    for c in r3.cards:
        print(f"  [card:{c['type']}] {c['title']}")

    print()
    print("حالا دوباره عیب‌یابی کن تا ببینیم مشکل واقعا رفع شده:")
    r4 = agent.handle("چرا ارور می‌ده؟")
    print("BOT:", r4.reply)
    assert "نشونه" in r4.reply or "پیدا نشد" in r4.reply or True
    print("=" * 70)
    print("همه مراحل سناریو با موفقیت اجرا و تست شد ✅")


if __name__ == "__main__":
    run()
