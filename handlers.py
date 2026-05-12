import time, random, asyncio, logging
from datetime import datetime, timezone, timedelta
from aiogram import Bot, types, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from telethon import TelegramClient
from telethon.sessions import StringSession

from config import GA_IDS, API_ID, API_HASH
from storage import Storage

router = Router()
_self_username = None

# FSM для авторизации нового аккаунта
class AuthStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()
    waiting_2fa = State()

def role_check(role: str, required: str) -> bool:
    hierarchy = {"ga": 4, "admin": 3, "player": 2, "banned": 0}
    return hierarchy.get(role, 0) >= hierarchy.get(required, 0)

async def _reply(message: types.Message, text: str):
    await message.answer(text)

# -------------------------------
# Общие команды (доступны игроку)
# -------------------------------
@router.message(lambda msg: msg.text and msg.text.lower() in [".помощь", ".help"])
async def cmd_help(message: types.Message):
    await _reply(message, (
        "🤖 PGUB Bot — Список команд\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Игрок:\n"
        ".ткарточка вкл/выкл [мин] — авто-карточка\n"
        ".ежедн вкл/выкл — ежедневный бонус\n"
        ".настройки — ваши настройки\n"
        ".помощь — эта справка\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Админ:\n"
        ".дебаг — системная информация\n"
        ".роль IDTG 2/3 — сменить роль (2-админ, 3-игрок)\n"
        ".бан IDTG — заблокировать\n"
        ".разбан IDTG — разблокировать\n"
        ".айди — ваш Telegram ID\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Главный администратор (ГА):\n"
        ".цель @user — цель перевода\n"
        ".количество <сумма> — сумма перевода\n"
        ".авторизация — добавить новый аккаунт\n"
        ".привязать IDTG сессия — привязать игру к аккаунту\n"
        ".сессии — список всех сессий\n"
        ".удалитьсессию IDTG — удалить сессию\n"
        ".роль IDTG 10 — назначить ГА"
    ))

@router.message(lambda msg: msg.text and msg.text.lower().startswith((".ткарточка", ".tcard")))
async def toggle_tcard(message: types.Message):
    parts = message.text.split()
    if len(parts) < 2:
        await _reply(message, "❌ Формат: .ткарточка вкл/выкл [мин]")
        return
    action = parts[1].lower()
    user_id = message.from_user.id
    storage: Storage = message.bot.storage
    scheduler = message.bot.scheduler

    if action in ("вкл", "on"):
        interval = 120
        if len(parts) >= 3:
            try:
                interval = int(parts[2])
                if interval < 1: raise ValueError
            except ValueError:
                await _reply(message, "❌ Интервал должен быть числом минут >0")
                return
        storage.set_user(user_id, "tcard_enabled", True)
        storage.set_user(user_id, "tcard_interval", interval)
        # Для простоты задачи ткарточки персональные для каждого пользователя больше не нужны (мы работаем через глобальные сессии),
        # но оставим заглушку совместимости: можно запланировать на глобальном клиенте? Нет, теперь управление сессиями централизовано.
        # Вместо этого просто сохраним настройки, они будут подхвачены при следующем создании задач.
        await _reply(message, f"🃏 Ткарточка включена (каждые {interval} мин).")
    elif action in ("выкл", "off"):
        storage.set_user(user_id, "tcard_enabled", False)
        await _reply(message, "🃏 Ткарточка выключена.")
    else:
        await _reply(message, "❌ Укажите вкл или выкл.")

@router.message(lambda msg: msg.text and msg.text.lower().startswith((".ежедн", ".everyday")))
async def toggle_daily(message: types.Message):
    parts = message.text.split()
    if len(parts) < 2:
        await _reply(message, "❌ Формат: .ежедн вкл/выкл")
        return
    action = parts[1].lower()
    user_id = message.from_user.id
    storage: Storage = message.bot.storage

    if action in ("вкл", "on"):
        storage.set_user(user_id, "daily_enabled", True)
        await _reply(message, "🎁 Ежедневная награда включена.")
    elif action in ("выкл", "off"):
        storage.set_user(user_id, "daily_enabled", False)
        await _reply(message, "🎁 Ежедневная награда выключена.")
    else:
        await _reply(message, "❌ Укажите вкл или выкл.")

@router.message(lambda msg: msg.text and msg.text.lower() in [".настройки", ".settings"])
async def show_settings(message: types.Message):
    user_id = message.from_user.id
    storage: Storage = message.bot.storage
    s = storage.get_user(user_id)
    tcard = f"✅ каждые {s['tcard_interval']} мин" if s['tcard_enabled'] else "❌ выкл"
    daily = "✅ вкл" if s['daily_enabled'] else "❌ выкл"
    await _reply(message, (
        f"⚙️ Ваши настройки:\n"
        f"🎯 Цель: {s['target'] or 'не задана'}\n"
        f"💰 Сумма: {s['amount']:,} точек\n"
        f"🃏 Ткарточка: {tcard}\n"
        f"🎁 Ежедневная награда: {daily}"
    ))

# -------------------------------
# Команды администратора
# -------------------------------
@router.message(lambda msg: msg.text and msg.text.lower() in [".дебаг", ".debug"])
async def debug(message: types.Message):
    user_id = message.from_user.id
    storage: Storage = message.bot.storage
    role = storage.get_role(user_id)
    if not role_check(role, "admin"):
        return await _reply(message, "⛔ Только для администраторов.")
    uptime = time.time() - message.bot.start_time
    h, rem = divmod(int(uptime), 3600)
    m, s = divmod(rem, 60)
    cnt = storage.count_by_roles()
    now = datetime.now(timezone.utc) + timedelta(hours=3)
    await _reply(message, (
        f"🛠 Отладка\n"
        f"⏱ Аптайм: {h:02}:{m:02}:{s:02}\n"
        f"📅 Дата/время (МСК): {now.strftime('%d.%m.%Y %H:%M')}\n"
        f"📊 Пользователи: ГА {cnt['ga']}, Админ {cnt['admin']}, Игроков {cnt['player']}, Забанено {cnt['banned']}\n"
        f"📚 Библиотеки: aiogram ✅, telethon ✅, apscheduler ✅"
    ))

@router.message(lambda msg: msg.text and msg.text.lower().startswith((".бан ", ".ban ")))
async def ban_cmd(message: types.Message):
    user_id = message.from_user.id
    storage: Storage = message.bot.storage
    role = storage.get_role(user_id)
    if not role_check(role, "admin"):
        return await _reply(message, "⛔ Нет прав.")
    parts = message.text.split()
    if len(parts) < 2:
        return await _reply(message, "❌ Укажите ID: .бан 123456")
    try:
        target = int(parts[1])
    except ValueError:
        return await _reply(message, "❌ ID должен быть числом.")
    if storage.get_role(target) in ("ga", "admin"):
        return await _reply(message, "❌ Нельзя забанить ГА или админа.")
    storage.set_role(target, "banned")
    await _reply(message, f"🚫 Пользователь {target} забанен.")

@router.message(lambda msg: msg.text and msg.text.lower().startswith((".разбан ", ".unban ")))
async def unban_cmd(message: types.Message):
    user_id = message.from_user.id
    storage: Storage = message.bot.storage
    role = storage.get_role(user_id)
    if not role_check(role, "admin"):
        return await _reply(message, "⛔ Нет прав.")
    parts = message.text.split()
    if len(parts) < 2:
        return await _reply(message, "❌ Укажите ID: .разбан 123456")
    try:
        target = int(parts[1])
    except ValueError:
        return await _reply(message, "❌ ID должен быть числом.")
    if storage.get_role(target) != "banned":
        return await _reply(message, "❌ Пользователь не забанен.")
    storage.set_role(target, "player")
    await _reply(message, f"✅ Пользователь {target} разбанен.")

@router.message(lambda msg: msg.text and msg.text.lower() in [".айди", ".id"])
async def my_id(message: types.Message):
    await _reply(message, f"🆔 Ваш Telegram ID: {message.from_user.id}")

@router.message(lambda msg: msg.text and msg.text.lower().startswith((".роль ", ".role ")))
async def set_role_cmd(message: types.Message):
    user_id = message.from_user.id
    storage: Storage = message.bot.storage
    role = storage.get_role(user_id)
    parts = message.text.split()
    if len(parts) < 3:
        return await _reply(message, "❌ Формат: .роль ID 2/3/10")
    try:
        target = int(parts[1])
        code = int(parts[2])
    except ValueError:
        return await _reply(message, "❌ Цифры.")
    # GA может всё
    if role == "ga":
        if code == 10:   # назначить GA
            if storage.get_role(target) == "ga":
                return await _reply(message, "❌ Уже ГА.")
            storage.set_role(target, "ga")
            return await _reply(message, f"✅ {target} теперь ГА.")
        elif code in (2, 3):
            new_role = "admin" if code == 2 else "player"
            if storage.get_role(target) in ("ga", "admin") and role != "ga":
                return await _reply(message, "❌ Недостаточно прав.")
            storage.set_role(target, new_role)
            return await _reply(message, f"✅ Роль изменена на {new_role}.")
        else:
            return await _reply(message, "❌ Неверный код роли (2-админ, 3-игрок, 10-ГА).")
    elif role == "admin":
        if code in (2, 3):
            target_role = storage.get_role(target)
            if target_role in ("ga", "admin"):
                return await _reply(message, "❌ Нельзя изменить роль ГА или другого админа.")
            new_role = "admin" if code == 2 else "player"
            storage.set_role(target, new_role)
            return await _reply(message, f"✅ Роль изменена на {new_role}.")
        else:
            return await _reply(message, "❌ Админ может назначать только 2 (админ) или 3 (игрок).")
    else:
        return await _reply(message, "⛔ Нет доступа.")

# -------------------------------
# Команды ГА
# -------------------------------
@router.message(lambda msg: msg.text and msg.text.lower().startswith(".цель "))
async def ga_target(message: types.Message):
    user_id = message.from_user.id
    storage: Storage = message.bot.storage
    if storage.get_role(user_id) != "ga":
        return await _reply(message, "⛔ Только для ГА.")
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await _reply(message, "❌ Укажите @user")
    target = parts[1].strip()
    if not target.startswith("@"):
        return await _reply(message, "❌ Юзернейм должен начинаться с @")
    # Сохраняем глобальную цель и обновляем планировщик
    storage.set_user(user_id, "target", target)
    message.bot.scheduler.update_global(target, storage.get_user(user_id)["amount"])
    await _reply(message, f"🎯 Глобальная цель: {target}")

@router.message(lambda msg: msg.text and msg.text.lower().startswith(".количество "))
async def ga_amount(message: types.Message):
    user_id = message.from_user.id
    storage: Storage = message.bot.storage
    if storage.get_role(user_id) != "ga":
        return await _reply(message, "⛔ Только для ГА.")
    parts = message.text.split(maxsplit=1)
    try:
        amt = int(parts[1].strip())
        if amt < 1: raise ValueError
    except:
        return await _reply(message, "❌ Сумма должна быть целым положительным числом.")
    storage.set_user(user_id, "amount", amt)
    message.bot.scheduler.update_global(storage.get_user(user_id)["target"], amt)
    await _reply(message, f"💰 Глобальная сумма перевода: {amt:,} точек")

# Авторизация нового аккаунта
class AuthSession:
    """Простая обёртка для временного хранения данных FSM."""
    def __init__(self):
        self.client = None
        self.phone = None

auth_sessions = {}

@router.message(lambda msg: msg.text and msg.text.lower() == ".авторизация")
async def start_auth(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    storage: Storage = message.bot.storage
    if storage.get_role(user_id) != "ga":
        return await _reply(message, "⛔ Только для ГА.")
    auth = AuthSession()
    auth.client = TelegramClient(StringSession(), API_ID, API_HASH)
    auth_sessions[user_id] = auth
    await state.set_state(AuthStates.waiting_phone)
    await _reply(message, "📱 Введите номер телефона в международном формате (с +)")

@router.message(AuthStates.waiting_phone)
async def auth_phone(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    auth = auth_sessions[user_id]
    try:
        await auth.client.connect()
        sent = await auth.client.send_code_request(message.text.strip())
        auth.phone = message.text.strip()
        auth.sent = sent
        await state.set_state(AuthStates.waiting_code)
        await _reply(message, "📲 Отправлен код подтверждения. Введите код:")
    except Exception as e:
        await _reply(message, f"❌ Ошибка: {e}")
        await state.clear()

@router.message(AuthStates.waiting_code)
async def auth_code(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    auth = auth_sessions[user_id]
    try:
        await auth.client.sign_in(phone=auth.phone, code=message.text.strip())
    except Exception as e:
        # возможно, нужна 2FA
        if "password" in str(e).lower():
            await state.set_state(AuthStates.waiting_2fa)
            return await _reply(message, "🔐 Введите пароль двухфакторной аутентификации:")
        await _reply(message, f"❌ Ошибка входа: {e}")
        await state.clear()
        return
    # успешно
    session_str = auth.client.session.save()
    storage: Storage = message.bot.storage
    storage.add_session(owner_id=user_id, session_string=session_str)
    # запускаем клиента и добавляем в глобальный список
    new_client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    await new_client.start()
    message.bot.clients.append((new_client, {"owner": user_id, "session": session_str, "game_id": ""}))
    # добавим задачи для нового клиента
    await message.bot.scheduler.add_jobs_for_new_client(len(message.bot.clients)-1, {"settings": {}})
    await _reply(message, "✅ Новый аккаунт добавлен и готов к работе.")
    await state.clear()

@router.message(AuthStates.waiting_2fa)
async def auth_2fa(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    auth = auth_sessions[user_id]
    try:
        await auth.client.sign_in(password=message.text.strip())
    except Exception as e:
        await _reply(message, f"❌ Ошибка: {e}")
        await state.clear()
        return
    session_str = auth.client.session.save()
    storage: Storage = message.bot.storage
    storage.add_session(owner_id=user_id, session_string=session_str)
    new_client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    await new_client.start()
    message.bot.clients.append((new_client, {"owner": user_id, "session": session_str, "game_id": ""}))
    await message.bot.scheduler.add_jobs_for_new_client(len(message.bot.clients)-1, {"settings": {}})
    await _reply(message, "✅ Аккаунт с 2FA успешно добавлен.")
    await state.clear()

# Привязать IDTG к сессии
@router.message(lambda msg: msg.text and msg.text.lower().startswith(".привязать "))
async def bind_session(message: types.Message):
    user_id = message.from_user.id
    storage: Storage = message.bot.storage
    if storage.get_role(user_id) != "ga":
        return await _reply(message, "⛔ Только для ГА.")
    parts = message.text.split()
    if len(parts) < 3:
        return await _reply(message, "❌ Формат: .привязать IDTG сессия")
    game_id = parts[1]
    try:
        session_index = int(parts[2])
    except:
        return await _reply(message, "❌ Сессия должна быть числом (индекс из .сессии)")
    sessions = storage.get_sessions(owner_id=user_id)
    if session_index < 0 or session_index >= len(sessions):
        return await _reply(message, "❌ Неверный индекс сессии.")
    sessions[session_index]["game_id"] = game_id
    storage._save()
    await _reply(message, f"🔗 Сессия {session_index} привязана к IDTG {game_id}.")

# Список сессий
@router.message(lambda msg: msg.text and msg.text.lower() == ".сессии")
async def list_sessions(message: types.Message):
    user_id = message.from_user.id
    storage: Storage = message.bot.storage
    if storage.get_role(user_id) != "ga":
        return await _reply(message, "⛔ Только для ГА.")
    sessions = storage.get_sessions(owner_id=user_id)
    if not sessions:
        return await _reply(message, "📭 Нет дополнительных сессий.")
    text = "📋 Сессии:\n"
    for i, s in enumerate(sessions):
        text += f"{i}: игра={s.get('game_id', 'нет')}\n"
    text += f"Всего: {len(sessions)}"
    await _reply(message, text)

# Удалить сессию
@router.message(lambda msg: msg.text and msg.text.lower().startswith(".удалитьсессию "))
async def delete_session(message: types.Message):
    user_id = message.from_user.id
    storage: Storage = message.bot.storage
    if storage.get_role(user_id) != "ga":
        return await _reply(message, "⛔ Только для ГА.")
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await _reply(message, "❌ Укажите IDTG или индекс")
    ident = parts[1]
    # ищем сессию по game_id или по индексу
    sessions = storage.get_sessions(owner_id=user_id)
    deleted = False
    if ident.isdigit():
        idx = int(ident)
        if 0 <= idx < len(sessions):
            game_id = sessions[idx].get("game_id")
            storage.delete_session(game_id)
            # останавливаем клиент и удаляем задачи
            for i, (cli, sdata) in enumerate(message.bot.clients):
                if sdata.get("game_id") == game_id and cli is not None:
                    await cli.disconnect()
                    del message.bot.clients[i]
                    await message.bot.scheduler.remove_jobs_for_client(i)
                    break
            await _reply(message, f"🗑 Сессия {idx} удалена.")
            deleted = True
    else:
        # удаляем по game_id
        storage.delete_session(ident)
        for i, (cli, sdata) in enumerate(message.bot.clients):
            if sdata.get("game_id") == ident:
                await cli.disconnect()
                del message.bot.clients[i]
                await message.bot.scheduler.remove_jobs_for_client(i)
                break
        await _reply(message, f"🗑 Сессия с IDTG {ident} удалена.")
        deleted = True
    if not deleted:
        await _reply(message, "❌ Сессия не найдена.")