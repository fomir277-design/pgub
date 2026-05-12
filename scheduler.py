import logging
from datetime import timedelta, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telethon import TelegramClient

logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))

class JobManager:
    def __init__(self, main_client: TelegramClient, clients: dict, storage):
        """
        main_client: основной аккаунт бота (SESSION_STRING) для задач без привязки
        clients: словарь {user_id: TelegramClient} для привязанных пользователей
        """
        self.main_client = main_client
        self.clients = clients
        self.storage = storage
        self.scheduler = AsyncIOScheduler(timezone=MSK)
        self.scheduler.start()

    async def restore_all(self):
        """Восстанавливает задачи для всех пользователей."""
        for uid_str in self.storage.all_users():
            uid = int(uid_str)
            us = self.storage.get_user(uid)
            if us.get("tcard_enabled"):
                self.add_tcard(uid, us["tcard_interval"])
            if us.get("daily_enabled"):
                self.add_daily(uid)
            if us.get("autofarm_enabled") and us.get("connected"):
                self.add_autofarm(uid)

    def _safe_remove(self, job_id: str):
        try:
            self.scheduler.remove_job(job_id)
        except:
            pass

    # ---------- tcard ----------
    def add_tcard(self, user_id: int, interval: int):
        jid = f"tcard_{user_id}"
        self._safe_remove(jid)
        self.scheduler.add_job(
            self._send_tcard, "interval", minutes=interval,
            id=jid, args=[user_id], replace_existing=True
        )

    def remove_tcard(self, user_id: int):
        self._safe_remove(f"tcard_{user_id}")

    async def _send_tcard(self, user_id: int):
        # Всегда используем main_client (общий)
        try:
            await self.main_client.send_message(
                __import__("config").GAME_BOT_USERNAME, "ткарточка"
            )
            logger.info(f"tcard sent for user {user_id}")
        except Exception as e:
            logger.error(f"tcard error for {user_id}: {e}")

    # ---------- daily ----------
    def add_daily(self, user_id: int):
        jid = f"daily_{user_id}"
        self._safe_remove(jid)
        self.scheduler.add_job(
            self._daily_present, "cron", hour=7, minute=0,
            id=jid, args=[user_id], replace_existing=True
        )

    def remove_daily(self, user_id: int):
        self._safe_remove(f"daily_{user_id}")

    async def _daily_present(self, user_id: int):
        try:
            bot = __import__("config").GAME_BOT_USERNAME
            await self.main_client.send_message(bot, "Ежедневная награда")
            async for msg in self.main_client.iter_messages(bot, limit=1):
                if msg.reply_markup:
                    for row in msg.reply_markup.rows:
                        for btn in row.buttons:
                            if "Забрать" in btn.text:
                                await btn.click()
                                return
            logger.info(f"daily present for user {user_id}")
        except Exception as e:
            logger.error(f"daily error for {user_id}: {e}")

    # ---------- autofarm (привязанные) ----------
    def add_autofarm(self, user_id: int):
        jid = f"autofarm_{user_id}"
        self._safe_remove(jid)
        # 3:00 MSK = 0:00 UTC
        self.scheduler.add_job(
            self._do_autofarm, "cron", hour=0, minute=0,
            id=jid, args=[user_id], replace_existing=True
        )

    def remove_autofarm(self, user_id: int):
        self._safe_remove(f"autofarm_{user_id}")

    async def _do_autofarm(self, user_id: int):
        client = self.clients.get(user_id)
        if not client:
            return
        us = self.storage.get_user(user_id)
        target = us.get("target")
        amount = us.get("amount", 0) or 0
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
            logger.info(f"autofarm for user {user_id} completed")
        except Exception as e:
            logger.error(f"autofarm error for {user_id}: {e}")

    def shutdown(self):
        self.scheduler.shutdown(wait=False)