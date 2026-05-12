import time
from datetime import datetime, timezone, timedelta
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telethon import TelegramClient
from telethon.sessions import StringSession

from config import GA_IDS, GAME_BOT_USERNAME, API_ID, API_HASH
from storage import Storage

router = Router()

# ------- FSM для привязки номера -------
class BindStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()
    waiting_2fa = State()

# Временное хранилище Telethon клиентов во время привязки
temp_clients = {}

def role_hierarchy(user_role: str, required: str) -> bool:
    order = {"ga": 4, "admin": 3, "player": 2, "banned": 0}
    return order.get(user_role, 0) >= order.get(required, 0)

async def reply(msg: types.Message, text: str):
    await msg.answer(text, parse_mode=None)

# ------- Главное меню /start -------
@router.message(F.text.lower().in_(["/start", ".start", ".старт"]))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    storage: Storage = message.bot.storage
    storage.register_if_absent(user_id)
    user = storage.get_user(user_id)
    role = user["role"]
    conn = "✅ привязан" if user["connected"] else "❌ не привязан"
    text = (
        f"🤖 Добро пожаловать в PGUB!\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Ваш ID: {user_id}\n"
        f"👤 Роль: {role}\n"
        f"📱 Статус привязки: {conn}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
    )
    if not user["connected"]:
        text += (
            "⚠️ Ваш аккаунт не привязан. Некоторые функции недоступны.\n"
            "Для привязки используйте команду .привязать\n"
        )
    text += "📋 Справка по командам: .помощь"
    await reply(message, text)

@router.message(F.text.lower().in_([".помощь", ".help", ".хелп"]))
async def help_cmd(message: types.Message):
    await reply(message, (
        "🤖 PGUB Bot — Список команд\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Игрок (без привязки):\n"
        ".ткарточка вкл/выкл [мин] — авто-карточка\n"
        ".ежедн вкл/выкл — ежедневный бонус\n"
        ".настройки — ваши настройки\n"
        ".помощь — эта справка\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Игрок (после привязки):\n"
        ".автоферма — автовывод фермы\n"
        ".цель @user — цель перевода\n"
        ".количество <сумма> — сумма перевода\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Админ:\n"
        ".дебаг — системная информация\n"
        ".роль IDTG 1 — назначить игрока\n"
        ".бан IDTG — заблокировать\n"
        ".разбан IDTG — разблокировать\n"
        ".айди — ваш Telegram ID\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Главный администратор (ГА):\n"
        ".роль IDTG 10/2/1 — сменить роль\n"
        ".бан IDTG — заблокировать\n"
        ".разбан IDTG — разблокировать\n"
        ".сессии — список привязанных аккаунтов\n"
        ".удалитьсессию IDTG — удалить сессию\n"
    ))

# ------- Игрок (уровень 1) -------
@router.message(F.text.lower().startswith((".ткарточка", ".tcard")))
async def tcard_cmd(message: types.Message):
    storage: Storage = message.bot.storage
    user_id = message.from_user.id
    if storage.is_banned(user_id):
        return await reply(message, "🚫 Вы заблокированы.")
    parts = message.text.split()
    if len(parts) < 2:
        return await reply(message, "❌ Формат: .ткарточка вкл/выкл [мин]")
    act = parts[1].lower()
    job = message.bot.scheduler
    if act in ("вкл", "on"):
        interval = 120
        if len(parts) >= 3:
            try:
                interval = int(parts[2])
                if interval < 1: raise ValueError
            except ValueError:
                return await reply(message, "❌ Интервал должен быть целым числом минут >0")
        storage.set_user(user_id, "tcard_enabled", True)
        storage.set_user(user_id, "tcard_interval", interval)
        if storage.get_user(user_id)["connected"]:
            job.add_tcard(user_id, interval)
            await reply(message, f"🃏 Ткарточка включена (каждые {interval} мин).")
        else:
            await reply(message, "🃏 Настройка сохранена, но аккаунт не привязан. Команда не будет выполняться.")
    elif act in ("выкл", "off"):
        storage.set_user(user_id, "tcard_enabled", False)
        job.remove_tcard(user_id)
        await reply(message, "🃏 Ткарточка выключена.")
    else:
        await reply(message, "❌ Укажите вкл или выкл.")

@router.message(F.text.lower().startswith((".ежедн", ".everyday")))
async def daily_cmd(message: types.Message):
    storage: Storage = message.bot.storage
    user_id = message.from_user.id
    if storage.is_banned(user_id):
        return await reply(message, "🚫 Вы заблокированы.")
    parts = message.text.split()
    if len(parts) < 2:
        return await reply(message, "❌ Формат: .ежедн вкл/выкл")
    act = parts[1].lower()
    job = message.bot.scheduler
    if act in ("вкл", "on"):
        storage.set_user(user_id, "daily_enabled", True)
        if storage.get_user(user_id)["connected"]:
            job.add_daily(user_id)
            await reply(message, "🎁 Ежедневная награда включена.")
        else:
            await reply(message, "🎁 Настройка сохранена, но аккаунт не привязан.")
    elif act in ("выкл", "off"):
        storage.set_user(user_id, "daily_enabled", False)
        job.remove_daily(user_id)
        await reply(message, "🎁 Ежедневная награда выключена.")
    else:
        await reply(message, "❌ Укажите вкл или выкл.")

@router.message(F.text.lower().in_([".настройки", ".settings"]))
async def settings_cmd(message: types.Message):
    storage: Storage = message.bot.storage
    user_id = message.from_user.id
    if storage.is_banned(user_id):
        return await reply(message, "🚫 Вы заблокированы.")
    s = storage.get_user(user_id)
    tcard = f"✅ каждые {s['tcard_interval']} мин" if s['tcard_enabled'] else "❌ выкл"
    daily = "✅ вкл" if s['daily_enabled'] else "❌ выкл"
    autof = "✅ вкл" if s['autofarm_enabled'] else "❌ выкл"
    conn = "✅" if s['connected'] else "❌"
    await reply(message, (
        f"⚙️ Ваши настройки:\n"
        f"📱 Привязка: {conn}\n"
        f"🎯 Цель: {s['target'] or 'не задана'}\n"
        f"💰 Сумма: {s['amount']:,} точек\n"
        f"🃏 Ткарточка: {tcard}\n"
        f"🎁 Ежедн. награда: {daily}\n"
        f"🚜 Автоферма: {autof}"
    ))

# ------- Игрок (уровень 2) -------
@router.message(F.text.lower().startswith(".автоферма"))
async def autofarm_cmd(message: types.Message):
    storage: Storage = message.bot.storage
    user_id = message.from_user.id
    if storage.is_banned(user_id):
        return await reply(message, "🚫 Вы заблокированы.")
    if not storage.get_user(user_id)["connected"]:
        return await reply(message, "❌ Сначала привяжите аккаунт (.привязать).")
    parts = message.text.split()
    if len(parts) < 2:
        # переключение
        current = storage.get_user(user_id)["autofarm_enabled"]
        new_val = not current
        storage.set_user(user_id, "autofarm_enabled", new_val)
        if new_val:
            message.bot.scheduler.add_autofarm(user_id)
            await reply(message, "🚜 Автоферма включена (ежедневно в 03:00 МСК).")
        else:
            message.bot.scheduler.remove_autofarm(user_id)
            await reply(message, "🚜 Автоферма выключена.")
    else:
        await reply(message, "❌ Используйте .автоферма для включения/выключения.")

@router.message(F.text.lower().startswith(".цель"))
@router.message(F.text.lower().startswith(".target"))
async def target_cmd(message: types.Message):
    storage: Storage = message.bot.storage
    user_id = message.from_user.id
    if not storage.get_user(user_id)["connected"]:
        return await reply(message, "❌ Сначала привяжите аккаунт.")
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await reply(message, "❌ Укажите @user")
    target = parts[1].strip()
    if not target.startswith("@"):
        return await reply(message, "❌ Юзернейм должен начинаться с @")
    storage.set_user(user_id, "target", target)
    await reply(message, f"🎯 Цель перевода: {target}")

@router.message(F.text.lower().startswith(".количество"))
@router.message(F.text.lower().startswith(".amount"))
async def amount_cmd(message: types.Message):
    storage: Storage = message.bot.storage
    user_id = message.from_user.id
    if not storage.get_user(user_id)["connected"]:
        return await reply(message, "❌ Сначала привяжите аккаунт.")
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await reply(message, "❌ Укажите сумму")
    try:
        amt = int(parts[1].strip())
        if amt < 1: raise ValueError
    except:
        return await reply(message, "❌ Сумма должна быть целым положительным числом.")
    storage.set_user(user_id, "amount", amt)
    await reply(message, f"💰 Сумма перевода: {amt:,} точек")

# ------- Привязка номера -------
@router.message(F.text.lower() == ".привязать")
async def bind_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    storage: Storage = message.bot.storage
    if storage.get_user(user_id)["connected"]:
        return await reply(message, "❌ Ваш аккаунт уже привязан.")
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )
    await message.answer(
        "Для привязки аккаунта нажмите кнопку ниже.\n"
        "Бот авторизуется в ваш аккаунт Telegram для выполнения команд от вашего имени.",
        reply_markup=kb
    )
    await state.set_state(BindStates.waiting_phone)

@router.message(BindStates.waiting_phone, F.contact)
async def got_phone(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    phone = message.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone
    # Создаём временный Telethon клиент
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    temp_clients[user_id] = client
    try:
        await client.connect()
        await client.send_code_request(phone)
        await state.update_data(phone=phone)
        await state.set_state(BindStates.waiting_code)
        await message.answer("📲 Код подтверждения отправлен. Введите его:", reply_markup=ReplyKeyboardRemove())
    except Exception as e:
        await reply(message, f"❌ Ошибка: {e}")
        await state.clear()

@router.message(BindStates.waiting_code)
async def code_entered(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    client = temp_clients.get(user_id)
    data = await state.get_data()
    phone = data["phone"]
    try:
        await client.sign_in(phone=phone, code=message.text.strip())
    except Exception as e:
        if "password" in str(e).lower():
            await state.set_state(BindStates.waiting_2fa)
            return await message.answer("🔐 Введите пароль 2FA:")
        await reply(message, f"❌ Ошибка входа: {e}")
        await state.clear()
        return
    # Успех
    session_str = client.session.save()
    storage: Storage = message.bot.storage
    storage.set_user(user_id, "connected", True)
    storage.set_user(user_id, "session_string", session_str)
    # Запускаем постоянный клиент и добавляем в пул
    new_client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    await new_client.start()
    message.bot.clients[user_id] = new_client
    # Восстанавливаем задачи, если были включены
    us = storage.get_user(user_id)
    if us["tcard_enabled"]:
        message.bot.scheduler.add_tcard(user_id, us["tcard_interval"])
    if us["daily_enabled"]:
        message.bot.scheduler.add_daily(user_id)
    if us["autofarm_enabled"]:
        message.bot.scheduler.add_autofarm(user_id)
    await reply(message, "✅ Аккаунт успешно привязан! Теперь вам доступны все функции.")
    await state.clear()

@router.message(BindStates.waiting_2fa)
async def twofa_entered(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    client = temp_clients.get(user_id)
    try:
        await client.sign_in(password=message.text.strip())
    except Exception as e:
        await reply(message, f"❌ Ошибка: {e}")
        await state.clear()
        return
    session_str = client.session.save()
    storage: Storage = message.bot.storage
    storage.set_user(user_id, "connected", True)
    storage.set_user(user_id, "session_string", session_str)
    new_client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    await new_client.start()
    message.bot.clients[user_id] = new_client
    us = storage.get_user(user_id)
    if us["tcard_enabled"]:
        message.bot.scheduler.add_tcard(user_id, us["tcard_interval"])
    if us["daily_enabled"]:
        message.bot.scheduler.add_daily(user_id)
    if us["autofarm_enabled"]:
        message.bot.scheduler.add_autofarm(user_id)
    await reply(message, "✅ Аккаунт с 2FA привязан!")
    await state.clear()

# ------- Админ -------
@router.message(F.text.lower().in_([".дебаг", ".debug"]))
async def debug_cmd(message: types.Message):
    storage: Storage = message.bot.storage
    role = storage.get_role(message.from_user.id)
    if not role_hierarchy(role, "admin"):
        return await reply(message, "⛔ Нет прав.")
    uptime = time.time() - message.bot.start_time
    h, rem = divmod(int(uptime), 3600)
    m, s = divmod(rem, 60)
    cnt = storage.count_by_roles()
    now = datetime.now(timezone.utc) + timedelta(hours=3)
    sessions = sum(1 for uid in storage.all_users() if storage.get_user(int(uid))["connected"])
    await reply(message, (
        f"🛠 Отладка\n"
        f"⏱ Аптайм: {h:02}:{m:02}:{s:02}\n"
        f"📅 МСК: {now.strftime('%d.%m.%Y %H:%M')}\n"
        f"👥 Пользователи: ГА {cnt['ga']}, Админ {cnt['admin']}, Игроков {cnt['player']}, Забанено {cnt['banned']}\n"
        f"📱 Активных сессий: {sessions}"
    ))

@router.message(F.text.lower().in_([".айди", ".id"]))
async def id_cmd(message: types.Message):
    await reply(message, f"🆔 Ваш ID: {message.from_user.id}")

@router.message(F.text.lower().startswith((".бан ", ".ban ")))
async def ban_cmd(message: types.Message):
    storage: Storage = message.bot.storage
    user_id = message.from_user.id
    role = storage.get_role(user_id)
    if not role_hierarchy(role, "admin"):
        return await reply(message, "⛔ Нет прав.")
    parts = message.text.split()
    if len(parts) < 2:
        return await reply(message, "❌ Укажите ID")
    try:
        target = int(parts[1])
    except ValueError:
        return await reply(message, "❌ ID должен быть числом.")
    target_role = storage.get_role(target)
    if role == "admin":
        if target_role in ("ga", "admin"):
            return await reply(message, "❌ Администратор не может заблокировать ГА или другого администратора.")
    elif role == "ga":
        # ГА может всех (кроме разве что самого себя? разрешим)
        pass
    storage.set_role(target, "banned")
    await reply(message, f"🚫 Пользователь {target} заблокирован.")

@router.message(F.text.lower().startswith((".разбан ", ".unban ")))
async def unban_cmd(message: types.Message):
    storage: Storage = message.bot.storage
    user_id = message.from_user.id
    role = storage.get_role(user_id)
    if not role_hierarchy(role, "admin"):
        return await reply(message, "⛔ Нет прав.")
    parts = message.text.split()
    if len(parts) < 2:
        return await reply(message, "❌ Укажите ID")
    try:
        target = int(parts[1])
    except ValueError:
        return await reply(message, "❌ ID должен быть числом.")
    if storage.get_role(target) != "banned":
        return await reply(message, "❌ Пользователь не забанен.")
    storage.set_role(target, "player")
    await reply(message, f"✅ Пользователь {target} разблокирован.")

@router.message(F.text.lower().startswith((".роль ", ".role ")))
async def role_cmd(message: types.Message):
    storage: Storage = message.bot.storage
    user_id = message.from_user.id
    role = storage.get_role(user_id)
    if not role_hierarchy(role, "admin"):
        return await reply(message, "⛔ Нет прав.")
    parts = message.text.split()
    if len(parts) < 3:
        return await reply(message, "❌ Формат: .роль ID 10/2/1")
    try:
        target = int(parts[1])
        code = int(parts[2])
    except ValueError:
        return await reply(message, "❌ ID и роль должны быть числами.")
    if role == "admin":
        # админ может назначать только роль "player" (1)
        if code != 1 or target in GA_IDS or storage.get_role(target) in ("ga", "admin"):
            return await reply(message, "❌ Администратор может только разжаловать игроков до player.")
        storage.set_role(target, "player")
        return await reply(message, f"✅ Пользователь {target} теперь player.")
    elif role == "ga":
        # ГА может всё: 10 - ga, 2 - admin, 1 - player
        role_map = {10: "ga", 2: "admin", 1: "player"}
        if code not in role_map:
            return await reply(message, "❌ Неверный код (10-ГА, 2-админ, 1-игрок).")
        new_role = role_map[code]
        # Не даём снять ga с фиксированных
        if target in GA_IDS and new_role != "ga":
            return await reply(message, "❌ Нельзя изменить роль фиксированного ГА.")
        storage.set_role(target, new_role)
        await reply(message, f"✅ Пользователь {target} теперь {new_role}.")
    else:
        await reply(message, "⛔ Нет доступа.")

# ------- ГА -------
@router.message(F.text.lower() == ".сессии")
async def sessions_cmd(message: types.Message):
    storage: Storage = message.bot.storage
    if storage.get_role(message.from_user.id) != "ga":
        return await reply(message, "⛔ Только для ГА.")
    sessions = storage.get_all_sessions()
    if not sessions:
        return await reply(message, "📭 Нет активных сессий.")
    text = "📋 Активные сессии:\n"
    for uid, _ in sessions:
        text += f"• {uid}\n"
    await reply(message, text)

@router.message(F.text.lower().startswith(".удалитьсессию"))
async def delsession_cmd(message: types.Message):
    storage: Storage = message.bot.storage
    if storage.get_role(message.from_user.id) != "ga":
        return await reply(message, "⛔ Только для ГА.")
    parts = message.text.split()
    if len(parts) < 2:
        return await reply(message, "❌ Укажите ID пользователя.")
    try:
        target = int(parts[1])
    except ValueError:
        return await reply(message, "❌ ID должен быть числом.")
    if not storage.get_user(target)["connected"]:
        return await reply(message, "❌ У этого пользователя нет активной сессии.")
    # Останавливаем клиент и удаляем из пула
    client = message.bot.clients.pop(target, None)
    if client:
        await client.disconnect()
    storage.remove_session(target)
    # Удаляем задачи
    message.bot.scheduler.remove_tcard(target)
    message.bot.scheduler.remove_daily(target)
    message.bot.scheduler.remove_autofarm(target)
    await reply(message, f"🗑 Сессия пользователя {target} удалена.")

# Блокировка забаненных
@router.message()
async def catch_banned(message: types.Message):
    if message.bot.storage.is_banned(message.from_user.id):
        await reply(message, "🚫 Вы заблокированы и не можете использовать бота.")