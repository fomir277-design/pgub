import time, asyncio, logging
from telethon import TelegramClient
from telethon.sessions import StringSession
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, API_ID, API_HASH
from storage import Storage
from handlers import router
from scheduler import JobManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    storage = Storage()

    # Пул клиентов (user_id -> TelegramClient)
    clients = {}
    for uid_str in storage.all_users():
        uid = int(uid_str)
        us = storage.get_user(uid)
        if us["connected"] and us["session_string"]:
            try:
                client = TelegramClient(StringSession(us["session_string"]), API_ID, API_HASH)
                await client.start()
                clients[uid] = client
                logger.info(f"Восстановлена сессия {uid}")
            except Exception as e:
                logger.error(f"Ошибка восстановления сессии {uid}: {e}")

    job = JobManager(clients, storage)
    await job.restore_all()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.storage = storage
    bot.scheduler = job
    bot.clients = clients
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

    await bot.set_my_commands([BotCommand(command="start", description="Начало работы")])
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())