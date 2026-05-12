import logging
from datetime import timedelta, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telethon import TelegramClient

logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))

class JobManager:
    def __init__(self, clients: dict, storage):
        """
        clients: словарь user_id -> TelegramClient (только активные авторизованные)
        storage: экземпляр Storage
        """
        self.clients = clients
        self.storage = storage
        self.scheduler = AsyncIOScheduler(timezone=MSK)
        self.scheduler.start()

    async def restore_all(self):
        """Восстанавливает задачи для всех клиентов на основе их настроек."""
        for user_id, client in self.clients.items():
            us = self.storage.get_user(user_id)
            if us["tcard_enabled"]:
                self.add_tcard(user_id, us["tcard_interval"])
            if us["daily_enabled"]:
                self.add_daily(user_id)
            if us["autofarm_enabled"]:
                self.add_autofarm(user_id)

    # ---------- управление задачами ----------
    def _safe_remove(self, job_id: str):
        try:
            self.scheduler.remove_job(job_id)
        except:
            pass

    def add_tcard(self, user_id: int, interval: int):
        jid = f"tcard_{user_id}"
        self._safe_remove(jid)
        client = self.clients.get(user_id)
        if client:
            self.scheduler.add_job(
                self._send_tcard, "interval", minutes=interval,
                id=jid, args=[user_id], replace_existing=True
            )

    def remove_tcard(self, user_id: int):
        self._safe_remove(f"tcard_{user_id}")

    def add_daily(self, user_id: int):
        jid = f"daily_{user_id}"
        self._safe_remove(jid)
        client = self.clients.get(user_id)
        if client:
            # 10:00 MSK = 7:00 UTC
            self.scheduler.add_job(
                self._daily_present, "cron", hour=7, minute=0,
                id=jid, args=[user_id], replace_existing=True
            )

    def remove_daily(self, user_id: int):
        self._safe_remove(f"daily_{user_id}")

    def add_autofarm(self, user_id: int):
        jid = f"autofarm_{user_id}"
        self._safe_remove(jid)
        client = self.clients.get(user_id)
        if client:
            # 3:00 MSK = 0:00 UTC
            self.scheduler.add_job(
                self._do_autofarm, "cron", hour=0, minute=0,
                id=jid, args=[user_id], replace_existing=True
            )

    def remove_autofarm(self, user_id: int):
        self._safe_remove(f"autofarm_{user_id}")

    # ---------- колбэки ----------
    async def _send_tcard(self, user_id: int):
        client = self.clients.get(user_id)
        if not client:
            return
        try:
            await client.send_message(
                __import__("config").GAME_BOT_USERNAME, "ткарточка"
            )
        except Exception as e:
            logger.error(f"tcard error for {user_id}: {e}")

    async def _daily_present(self, user_id: int):
        client = self.clients.get(user_id)
        if not client:
            return
        try:
            bot = __import__("config").GAME_BOT_USERNAME
            await client.send_message(bot, "Ежедневная награда")
            async for msg in client.iter_messages(bot, limit=1):
                if msg.reply_markup:
                    for row in msg.reply_markup.rows:
                        for btn in row.buttons:
                            if "Забрать" in btn.text:
                                await btn.click()
                                return
        except Exception as e:
            logger.error(f"daily error for {user_id}: {e}")

    async def _do_autofarm(self, user_id: int):
        client = self.clients.get(user_id)
        if not client:
            return
        us = self.storage.get_user(user_id)
        target = us.get("target")
        amount = us.get("amount", 0)
        try:
            bot = __import__("config").GAME_BOT_USERNAME
            await client.send_message(bot, "/tfarm")
            async for msg in client.iter_messages(bot, limit=3):
                if msg.reply_markup:
                    for row in msg.reply_markup.rows:
                        for btn in row.buttons:
                            if "Забрать деньги с фермы" in btn.text:
                                await btn.click()
                                break
            if target and amount >= 1:
                await client.send_message(bot, f"/pay {target} {amount}")
        except Exception as e:
            logger.error(f"autofarm error for {user_id}: {e}")

    def shutdown(self):
        self.scheduler.shutdown(wait=False)