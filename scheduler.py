import random, logging
from datetime import timedelta, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telethon import TelegramClient
from config import GAME_BOT_USERNAME

logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))

class JobManager:
    def __init__(self, clients: list, storage, global_target, global_amount):
        """
        clients – список объектов (client, session_info) для каждого аккаунта
        storage – хранилище
        global_target / global_amount – общие настройки для всех аккаунтов
        """
        self.clients = clients   # [(TelegramClient, dict session_data), ...]
        self.storage = storage
        self.target = global_target
        self.amount = global_amount
        self.scheduler = AsyncIOScheduler(timezone=MSK)
        self.scheduler.start()
        self._restore_all()

    def update_global(self, target: str, amount: int):
        self.target = target
        self.amount = amount

    def _safe_remove_job(self, job_id: str):
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            pass

    def _jobs_for_client(self, client_idx: int, session_data: dict):
        """Создаёт персональные задачи для одного клиента."""
        uid = f"client_{client_idx}"
        us = session_data.get("settings", {})
        if us.get("tcard_enabled"):
            self.scheduler.add_job(
                self._send_tcard, 'interval', minutes=us['tcard_interval'],
                id=f"tcard_{uid}", args=[client_idx], replace_existing=True
            )
        if us.get("daily_enabled"):
            # 10:00 MSK = 7:00 UTC
            self.scheduler.add_job(
                self._daily_present, 'cron', hour=7, minute=0,
                id=f"daily_{uid}", args=[client_idx], replace_existing=True
            )
        if us.get("farm_task_added") and us['farm_hour'] is not None:
            utc_h = (us['farm_hour'] - 3) % 24
            self.scheduler.add_job(
                self._collect_farm, 'cron', hour=utc_h, minute=us['farm_minute'],
                id=f"farm_{uid}", args=[client_idx], replace_existing=True
            )

    def _restore_all(self):
        for i, (cli, sdata) in enumerate(self.clients):
            # Восстанавливаем настройки из session_data, если они сохранены
            if "settings" in sdata:
                self._jobs_for_client(i, sdata)

    async def add_jobs_for_new_client(self, client_idx: int, session_data: dict):
        self._jobs_for_client(client_idx, session_data)

    async def remove_jobs_for_client(self, client_idx: int):
        uid = f"client_{client_idx}"
        self._safe_remove_job(f"tcard_{uid}")
        self._safe_remove_job(f"daily_{uid}")
        self._safe_remove_job(f"farm_{uid}")

    # ---------- действия ----------
    async def _send_tcard(self, client_idx: int):
        try:
            client, _ = self.clients[client_idx]
            await client.send_message(GAME_BOT_USERNAME, "ткарточка")
        except Exception as e:
            logger.error(f"tcard client {client_idx}: {e}")

    async def _daily_present(self, client_idx: int):
        try:
            client, _ = self.clients[client_idx]
            await client.send_message(GAME_BOT_USERNAME, "Ежедневная награда")
            async for msg in client.iter_messages(GAME_BOT_USERNAME, limit=1):
                if msg.reply_markup:
                    for row in msg.reply_markup.rows:
                        for btn in row.buttons:
                            if "Забрать" in btn.text:
                                await btn.click()
                                return
        except Exception as e:
            logger.error(f"daily client {client_idx}: {e}")

    async def _collect_farm(self, client_idx: int):
        if not self.target or self.amount <= 0:
            return
        try:
            client, _ = self.clients[client_idx]
            await client.send_message(GAME_BOT_USERNAME, "/tfarm")
            async for msg in client.iter_messages(GAME_BOT_USERNAME, limit=3):
                if msg.reply_markup:
                    for row in msg.reply_markup.rows:
                        for btn in row.buttons:
                            if "Снять деньги с фермы" in btn.text:
                                await btn.click()
                                break
            await client.send_message(GAME_BOT_USERNAME, f"/pay {self.target} {self.amount}")
        except Exception as e:
            logger.error(f"farm client {client_idx}: {e}")

    def shutdown(self):
        self.scheduler.shutdown(wait=False)