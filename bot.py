import time, asyncio, logging
from telethon import TelegramClient
from telethon.sessions import StringSession
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, API_ID, API_HASH, SESSION_STRING
from storage import Storage
from handlers import router
from scheduler import JobManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    storage = Storage()

    # Основной Telethon-клиент
    if not SESSION_STRING:
        raise RuntimeError("SESSION_STRING не задан")
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.start()

    # Менеджер задач
    job = JobManager(client)
    # Загружаем сохранённые задачи для всех пользователей
    for uid in storage.all_users():
        us = storage.get_user(int(uid))
        if us["tcard_enabled"]:
            await job.set_tcard(int(uid), True, us["tcard_interval"])
        if us["daily_enabled"]:
            await job.set_daily(int(uid), True)
    # Глобальная ферма (если задана цель)
    ga_id = list(__import__("config").GA_IDS)[0]
    ga_settings = storage.get_user(ga_id)
    if ga_settings["target"] and ga_settings["amount"] > 0:
        await job.set_farm(ga_settings["target"], ga_settings["amount"])

    # Aiogram
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.storage = storage
    bot.scheduler = job
    bot.start_time = time.time()

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    @dp.business_connection()
    async def on_bc(conn):
        uid = conn.user.id
        if conn.is_enabled:
            storage.register_if_absent(uid)
            storage.set_connection(uid, conn.id)
            logger.info(f"BC enabled {uid}")
        else:
            storage.remove_connection(uid)
            logger.info(f"BC disabled {uid}")

    await bot.set_my_commands([BotCommand(command="start", description="Начало")])
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())