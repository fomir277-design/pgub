import time, asyncio, logging
from datetime import datetime, timezone, timedelta
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from telethon import TelegramClient
from telethon.sessions import StringSession

from config import GA_IDS, API_ID, API_HASH
from storage import Storage

router = Router()
_self_username = None

def role_check(user_role: str, required: str) -> bool:
    order = {"ga": 4, "admin": 3, "player": 2, "banned": 0}
    return order.get(user_role, 0) >= order.get(required, 0)

async def reply(msg: types.Message, text: str):
    await msg.answer(text, parse_mode=None)

# ---------- Общие ----------
@router.message(F.text.lower().in_([".помощь", ".help"]))
async def help_cmd(message: types.Message):
    await reply(message, (
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
        ".роль IDTG 2/3 — сменить роль (2-админ,3-игрок)\n"
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

# ---------- Игрок ----------
@router.message(F.text.lower().startswith((".ткарточка", ".tcard")))
async def tcard(message: types.Message):
    storage: Storage = message.bot.storage
    user_id = message.from_user.id
    role = storage.get_role(user_id)
    if role == "banned":
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
        await job.set_tcard(user_id, True, interval)
        await reply(message, f"🃏 Ткарточка включена (каждые {interval} мин).")
    elif act in ("выкл", "off"):
        storage.set_user(user_id, "tcard_enabled", False)
        await job.set_tcard(user_id, False)
        await reply(message, "🃏 Ткарточка выключена.")
    else:
        await reply(message, "❌ Укажите вкл или выкл.")

@router.message(F.text.lower().startswith((".ежедн", ".everyday")))
async def daily(message: types.Message):
    storage: Storage = message.bot.storage
    user_id = message.from_user.id
    role = storage.get_role(user_id)
    if role == "banned":
        return await reply(message, "🚫 Вы заблокированы.")
    parts = message.text.split()
    if len(parts) < 2:
        return await reply(message, "❌ Формат: .ежедн вкл/выкл")
    act = parts[1].lower()
    job = message.bot.scheduler
    if act in ("вкл", "on"):
        storage.set_user(user_id, "daily_enabled", True)
        await job.set_daily(user_id, True)
        await reply(message, "🎁 Ежедневная награда включена.")
    elif act in ("выкл", "off"):
        storage.set_user(user_id, "daily_enabled", False)
        await job.set_daily(user_id, False)
        await reply(message, "🎁 Ежедневная награда выключена.")
    else:
        await reply(message, "❌ Укажите вкл или выкл.")

@router.message(F.text.lower().in_([".настройки", ".settings"]))
async def settings(message: types.Message):
    storage: Storage = message.bot.storage
    user_id = message.from_user.id
    if storage.is_banned(user_id):
        return await reply(message, "🚫 Вы заблокированы.")
    s = storage.get_user(user_id)
    tcard = f"✅ каждые {s['tcard_interval']} мин" if s['tcard_enabled'] else "❌ выкл"
    daily = "✅ вкл" if s['daily_enabled'] else "❌ выкл"
    await reply(message, (
        f"⚙️ Ваши настройки:\n"
        f"🎯 Цель: {s['target'] or 'не задана'}\n"
        f"💰 Сумма: {s['amount']:,} точек\n"
        f"🃏 Ткарточка: {tcard}\n"
        f"🎁 Ежедневная награда: {daily}"
    ))

# ---------- Админ ----------
@router.message(F.text.lower().in_([".дебаг", ".debug"]))
async def debug(message: types.Message):
    storage: Storage = message.bot.storage
    role = storage.get_role(message.from_user.id)
    if not role_check(role, "admin"):
        return await reply(message, "⛔ Только для администраторов.")
    now = datetime.now(timezone.utc) + timedelta(hours=3)
    uptime = time.time() - message.bot.start_time
    h, rem = divmod(int(uptime), 3600)
    m, s = divmod(rem, 60)
    cnt = storage.count_by_roles()
    await reply(message, (
        f"🛠 Отладка\n"
        f"⏱ Аптайм: {h:02}:{m:02}:{s:02}\n"
        f"📅 МСК: {now.strftime('%d.%m.%Y %H:%M')}\n"
        f"👥 Пользователи: ГА {cnt['ga']}, Админ {cnt['admin']}, Игроков {cnt['player']}, Забанено {cnt['banned']}"
    ))

@router.message(F.text.lower().startswith((".бан ", ".ban ")))
async def ban_cmd(message: types.Message):
    storage: Storage = message.bot.storage
    user_id = message.from_user.id
    if not role_check(storage.get_role(user_id), "admin"):
        return await reply(message, "⛔ Нет прав.")
    parts = message.text.split()
    if len(parts) < 2:
        return await reply(message, "❌ Укажите ID: .бан 123")
    try:
        target = int(parts[1])
    except ValueError:
        return await reply(message, "❌ ID должен быть числом.")
    if storage.get_role(target) in ("ga", "admin"):
        return await reply(message, "❌ Нельзя забанить ГА или админа.")
    storage.set_role(target, "banned")
    await reply(message, f"🚫 Пользователь {target} забанен.")

@router.message(F.text.lower().startswith((".разбан ", ".unban ")))
async def unban_cmd(message: types.Message):
    storage: Storage = message.bot.storage
    user_id = message.from_user.id
    if not role_check(storage.get_role(user_id), "admin"):
        return await reply(message, "⛔ Нет прав.")
    parts = message.text.split()
    if len(parts) < 2:
        return await reply(message, "❌ Укажите ID: .разбан 123")
    try:
        target = int(parts[1])
    except ValueError:
        return await reply(message, "❌ ID должен быть числом.")
    if storage.get_role(target) != "banned":
        return await reply(message, "❌ Пользователь не забанен.")
    storage.set_role(target, "player")
    await reply(message, f"✅ Пользователь {target} разбанен.")

@router.message(F.text.lower().in_([".айди", ".id"]))
async def my_id(message: types.Message):
    await reply(message, f"🆔 Ваш ID: {message.from_user.id}")

@router.message(F.text.lower().startswith((".роль ", ".role ")))
async def set_role(message: types.Message):
    storage: Storage = message.bot.storage
    user_id = message.from_user.id
    role = storage.get_role(user_id)
    parts = message.text.split()
    if len(parts) < 3:
        return await reply(message, "❌ Формат: .роль ID 2/3/10")
    try:
        target = int(parts[1])
        code = int(parts[2])
    except ValueError:
        return await reply(message, "❌ Цифры.")
    if role == "ga":
        if code == 10:
            if storage.get_role(target) == "ga":
                return await reply(message, "❌ Уже ГА.")
            storage.set_role(target, "ga")
            return await reply(message, f"✅ {target} теперь ГА.")
        elif code in (2, 3):
            new = "admin" if code == 2 else "player"
            storage.set_role(target, new)
            return await reply(message, f"✅ Роль изменена на {new}.")
        else:
            return await reply(message, "❌ Код роли: 2-админ, 3-игрок, 10-ГА.")
    elif role == "admin":
        if code in (2, 3):
            target_role = storage.get_role(target)
            if target_role in ("ga", "admin"):
                return await reply(message, "❌ Нельзя изменить роль ГА или админа.")
            new = "admin" if code == 2 else "player"
            storage.set_role(target, new)
            return await reply(message, f"✅ Роль изменена на {new}.")
        else:
            return await reply(message, "❌ Админ может назначать только 2 (админ) или 3 (игрок).")
    else:
        return await reply(message, "⛔ Нет доступа.")

# ---------- ГА ----------
@router.message(F.text.lower().startswith(".цель "))
async def ga_target(message: types.Message):
    storage: Storage = message.bot.storage
    if storage.get_role(message.from_user.id) != "ga":
        return await reply(message, "⛔ Только для ГА.")
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await reply(message, "❌ Укажите @user")
    target = parts[1].strip()
    if not target.startswith("@"):
        return await reply(message, "❌ Юзернейм должен начинаться с @")
    storage.set_user(message.from_user.id, "target", target)
    # обновляем ферму
    amt = storage.get_user(message.from_user.id)["amount"]
    await message.bot.scheduler.set_farm(target, amt)
    await reply(message, f"🎯 Глобальная цель: {target}")

@router.message(F.text.lower().startswith(".количество "))
async def ga_amount(message: types.Message):
    storage: Storage = message.bot.storage
    if storage.get_role(message.from_user.id) != "ga":
        return await reply(message, "⛔ Только для ГА.")
    parts = message.text.split(maxsplit=1)
    try:
        amt = int(parts[1].strip())
        if amt < 1: raise ValueError
    except:
        return await reply(message, "❌ Сумма должна быть целым положительным числом.")
    storage.set_user(message.from_user.id, "amount", amt)
    target = storage.get_user(message.from_user.id)["target"]
    await message.bot.scheduler.set_farm(target, amt)
    await reply(message, f"💰 Глобальная сумма: {amt:,} точек")

# ---------- Авторизация доп. сессии (ГА) ----------
class AuthSession:
    def __init__(self):
        self.client = None
        self.phone = None

auth_sessions = {}

class AuthStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()
    waiting_2fa = State()

@router.message(F.text.lower() == ".авторизация")
async def start_auth(message: types.Message, state: FSMContext):
    if message.bot.storage.get_role(message.from_user.id) != "ga":
        return await reply(message, "⛔ Только для ГА.")
    a = AuthSession()
    a.client = TelegramClient(StringSession(), API_ID, API_HASH)
    auth_sessions[message.from_user.id] = a
    await state.set_state(AuthStates.waiting_phone)
    await reply(message, "📱 Введите номер телефона (с +)")

@router.message(AuthStates.waiting_phone)
async def phone_in(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    a = auth_sessions[uid]
    try:
        await a.client.connect()
        await a.client.send_code_request(message.text.strip())
        a.phone = message.text.strip()
        await state.set_state(AuthStates.waiting_code)
        await reply(message, "📲 Код отправлен. Введите код:")
    except Exception as e:
        await reply(message, f"❌ Ошибка: {e}")
        await state.clear()

@router.message(AuthStates.waiting_code)
async def code_in(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    a = auth_sessions[uid]
    try:
        await a.client.sign_in(phone=a.phone, code=message.text.strip())
    except Exception as e:
        if "password" in str(e).lower():
            await state.set_state(AuthStates.waiting_2fa)
            return await reply(message, "🔐 Введите пароль 2FA:")
        await reply(message, f"❌ Ошибка входа: {e}")
        await state.clear()
        return
    session_str = a.client.session.save()
    message.bot.storage.add_session(owner_id=uid, session_string=session_str)
    # Запускаем клиент и добавляем в пул (если нужно) — здесь просто сохраняем
    await reply(message, "✅ Новый аккаунт сохранён.")
    await state.clear()

@router.message(AuthStates.waiting_2fa)
async def twofa_in(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    a = auth_sessions[uid]
    try:
        await a.client.sign_in(password=message.text.strip())
    except Exception as e:
        await reply(message, f"❌ Ошибка: {e}")
        await state.clear()
        return
    session_str = a.client.session.save()
    message.bot.storage.add_session(owner_id=uid, session_string=session_str)
    await reply(message, "✅ Аккаунт с 2FA сохранён.")
    await state.clear()

@router.message(F.text.lower().startswith(".привязать "))
async def bind_sess(message: types.Message):
    storage: Storage = message.bot.storage
    if storage.get_role(message.from_user.id) != "ga":
        return await reply(message, "⛔ Только для ГА.")
    parts = message.text.split()
    if len(parts) < 3:
        return await reply(message, "❌ Формат: .привязать IDTG индекс")
    try:
        idx = int(parts[2])
    except ValueError:
        return await reply(message, "❌ Индекс должен быть числом.")
    sessions = storage.get_sessions(owner_id=message.from_user.id)
    if idx < 0 or idx >= len(sessions):
        return await reply(message, "❌ Неверный индекс.")
    # привязка IDTG к сессии: доп. поле game_id
    sessions[idx]["game_id"] = parts[1]
    storage._save()
    await reply(message, f"🔗 Сессия {idx} привязана к {parts[1]}.")

@router.message(F.text.lower() == ".сессии")
async def list_sessions(message: types.Message):
    storage: Storage = message.bot.storage
    if storage.get_role(message.from_user.id) != "ga":
        return await reply(message, "⛔ Только для ГА.")
    sessions = storage.get_sessions(owner_id=message.from_user.id)
    if not sessions:
        return await reply(message, "📭 Нет сессий.")
    text = "📋 Сессии:\n" + "\n".join(
        f"{i}: игра={s.get('game_id','нет')}" for i, s in enumerate(sessions)
    )
    await reply(message, text)

@router.message(F.text.lower().startswith(".удалитьсессию "))
async def del_session(message: types.Message):
    storage: Storage = message.bot.storage
    if storage.get_role(message.from_user.id) != "ga":
        return await reply(message, "⛔ Только для ГА.")
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await reply(message, "❌ Укажите IDTG или индекс")
    ident = parts[1]
    sessions = storage.get_sessions(owner_id=message.from_user.id)
    if ident.isdigit():
        idx = int(ident)
        if 0 <= idx < len(sessions):
            storage.delete_session(idx)
            return await reply(message, f"🗑 Сессия {idx} удалена.")
    else:
        # удаление по game_id
        for i, s in enumerate(sessions):
            if s.get("game_id") == ident:
                storage.delete_session(i)
                return await reply(message, f"🗑 Сессия с {ident} удалена.")
    await reply(message, "❌ Не найдена.")

# Блокировка забаненных
@router.message()
async def block_banned(message: types.Message):
    if message.bot.storage.is_banned(message.from_user.id):
        await reply(message, "🚫 Вы заблокированы.")