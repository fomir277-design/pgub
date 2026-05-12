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

    # Загружаем все сессии
    sessions_data = storage.data.get("sessions", [])
    clients = []
    # Главная сессия (обязательна)
    if not SESSION_STRING:
        raise RuntimeError("SESSION_STRING не задан")
    main_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await main_client.start()
    clients.append((main_client, {"owner": 0, "session": SESSION_STRING, "game_id": "main"}))
    me = await main_client.get_me()
    self_username = f"@{me.username}" if me.username else None

    # Дополнительные сессии из хранилища
    for idx, s in enumerate(sessions_data):
        try:
            cl = TelegramClient(StringSession(s["session"]), API_ID, API_HASH)
            await cl.start()
            clients.append((cl, s))
            logger.info(f"Доп. сессия {idx} запущена")
        except Exception as e:
            logger.error(f"Ошибка запуска доп. сессии: {e}")

    # Глобальные настройки (GA задаёт через команды)
    global_target = storage.get_user(list(__import__("config").GA_IDS)[0]).get("target")
    global_amount = storage.get_user(list(__import__("config").GA_IDS)[0]).get("amount", 0)

    job_manager = JobManager(clients, storage, global_target, global_amount)

    # Aiogram Bot
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.storage = storage
    bot.scheduler = job_manager
    bot.start_time = time.time()
    bot.clients = clients  # для использования в handlers

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # business_connection
    @dp.business_connection()
    async def on_business_connection(conn):
        user_id = conn.user.id
        if conn.is_enabled:
            storage.register_if_absent(user_id)
            storage.set_connection(user_id, conn.id)
            logger.info(f"Business connection enabled for user {user_id}")
        else:
            storage.remove_connection(user_id)
            logger.info(f"Business connection disabled for user {user_id}")

    await bot.set_my_commands([BotCommand(command="start", description="Начало работы")])
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())