import random, logging
from datetime import timedelta, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))

class JobManager:
    def __init__(self, client):
        self.client = client                     # основной Telethon-клиент
        self.scheduler = AsyncIOScheduler(timezone=MSK)
        self.scheduler.start()
        self.farm_target = None
        self.farm_amount = 0

    # ---------- управление задачами ----------
    def _safe_remove(self, job_id: str):
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            pass

    async def set_tcard(self, user_id: int, enabled: bool, interval: int = 120):
        jid = f"tcard_{user_id}"
        self._safe_remove(jid)
        if enabled:
            self.scheduler.add_job(
                self._send_tcard, "interval", minutes=interval,
                id=jid, replace_existing=True
            )

    async def set_daily(self, user_id: int, enabled: bool):
        jid = f"daily_{user_id}"
        self._safe_remove(jid)
        if enabled:
            self.scheduler.add_job(
                self._daily_present, "cron", hour=7, minute=0,
                id=jid, replace_existing=True
            )

    async def set_farm(self, target: str, amount: int):
        self.farm_target = target
        self.farm_amount = amount
        self._safe_remove("farm_main")
        if target and amount > 0:
            h = random.randint(1, 4)
            m = random.randint(0, 59)
            utc_h = (h - 3) % 24
            self.scheduler.add_job(
                self._collect_farm, "cron", hour=utc_h, minute=m,
                id="farm_main", replace_existing=True
            )

    # ---------- действия ----------
    async def _send_tcard(self):
        try:
            await self.client.send_message(
                __import__("config").GAME_BOT_USERNAME, "ткарточка"
            )
        except Exception as e:
            logger.error(f"tcard error: {e}")

    async def _daily_present(self):
        try:
            bot_username = __import__("config").GAME_BOT_USERNAME
            await self.client.send_message(bot_username, "Ежедневная награда")
            async for msg in self.client.iter_messages(bot_username, limit=1):
                if msg.reply_markup:
                    for row in msg.reply_markup.rows:
                        for btn in row.buttons:
                            if "Забрать" in btn.text:
                                await btn.click()
                                return
        except Exception as e:
            logger.error(f"daily error: {e}")

    async def _collect_farm(self):
        if not self.farm_target or self.farm_amount <= 0:
            return
        try:
            bot_username = __import__("config").GAME_BOT_USERNAME
            await self.client.send_message(bot_username, "/tfarm")
            async for msg in self.client.iter_messages(bot_username, limit=3):
                if msg.reply_markup:
                    for row in msg.reply_markup.rows:
                        for btn in row.buttons:
                            if "Снять деньги с фермы" in btn.text:
                                await btn.click()
                                break
            pay_cmd = f"/pay {self.farm_target} {self.farm_amount}"
            await self.client.send_message(bot_username, pay_cmd)
        except Exception as e:
            logger.error(f"farm error: {e}")

    def shutdown(self):
        self.scheduler.shutdown(wait=False)