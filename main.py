import os
import sys
import json
import asyncio
import random
import time
import logging
# Заглушка вместо оригинального psutil для работы на Termux
class PsutilMock:
    def cpu_percent(self, *args, **kwargs): 
        return 0.0
    
    def virtual_memory(self):
        class VM:
            percent = 0.0
            total = 4 * 1024 * 1024 * 1024  # Имитация 4 ГБ
            used = 0
            available = total
        return VM()

    def Process(self, *args, **kwargs):
        class Proc:
            def memory_info(self):
                class Mem: rss = 0
                return Mem()
        return Proc()

psutil = PsutilMock()
import sqlite3
import subprocess
import builtins
from datetime import datetime, timedelta
import pytz
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, 
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, BotCommand,
    MenuButtonWebApp, WebAppInfo
)
from pyrogram.errors import (
    SessionPasswordNeeded, FloodWait, PhoneCodeInvalid, PhoneCodeExpired, 
    AuthKeyUnregistered, SessionRevoked, UserDeactivated
)
from pyrogram.enums import ChatAction
from dotenv import load_dotenv

# Переопределяем стандартный print
def print(*args, **kwargs):
    import builtins
    try:
        now = datetime.now(pytz.timezone("Europe/Moscow")).strftime("%d.%m.%Y %H:%M:%S")
        msg = " ".join(map(str, args))
        
        if msg.startswith("[🎁"):
            if "]" in msg:
                parts = msg.split("]", 1)
                msg = f"{parts[0]}] -{parts[1]}"
                
        if msg.startswith("["):
            builtins.print(f"[{now}] - {msg}", **kwargs)
        else:
            builtins.print(f"[{now}] - [🟢 SYSTEM] - {msg}", **kwargs)
    except Exception:
        pass

load_dotenv()

# Отключаем логгеру asyncio мусорный вывод об обрыве сокетов
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

# --- Фильтр для глушения мусорных ошибок Pyrogram ---
class PyrogramJunkFilter(logging.Filter):
    def filter(self, record):
        if record.exc_info:
            _, exc_value, _ = record.exc_info
            exc_str = str(exc_value)
            if "Peer id invalid" in exc_str or "ID not found" in exc_str:
                return False
        if "Retrying" in record.getMessage() and "Connection lost" in record.getMessage():
            return False
        return True

logging.getLogger("pyrogram.dispatcher").addFilter(PyrogramJunkFilter())
logging.getLogger("pyrogram.session.session").addFilter(PyrogramJunkFilter())

# --- Список ID Администраторов ---
ADMIN_IDS = [6118149728, 8209965013]

# --- Глушитель мусорных ошибок ---
def custom_exception_handler(loop, context):
    exc = context.get('exception')
    msg = context.get('message', '')
    if exc:
        exc_str = str(exc)
        if isinstance(exc, ValueError) and "Peer id invalid" in exc_str: return
        if isinstance(exc, KeyError) and "ID not found" in exc_str: return
    if "Peer id invalid" in msg or "ID not found" in msg: return
    if "socket.send()" in msg or "socket.send() raised exception" in msg: return
        
    loop.default_exception_handler(context)

sys.stdout.reconfigure(line_buffering=True)
START_TIME = time.time()

# ──────────────────────────────────────────────
# Файловая система и Константы
# ──────────────────────────────────────────────
DATA_DIR = "data"
SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")
CONFIGS_DIR  = os.path.join(DATA_DIR, "configs")
STATS_DIR    = os.path.join(DATA_DIR, "stats")
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(CONFIGS_DIR, exist_ok=True)
os.makedirs(STATS_DIR, exist_ok=True)

GAME_BOT = "phonegetcardsbot"
API_ID   = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

user_states    = {}
active_clients = {}
master_bot_instance = None

async def send_alert(text: str):
    """Отправка критических системных логов всем администраторам"""
    global master_bot_instance
    if master_bot_instance:
        for admin_id in ADMIN_IDS:
            try: await master_bot_instance.send_message(admin_id, text)
            except: pass

async def send_critical_alert(text: str, kb: list):
    global master_bot_instance
    if master_bot_instance:
        for admin_id in ADMIN_IDS:
            try: await master_bot_instance.send_message(admin_id, text, reply_markup=InlineKeyboardMarkup(kb))
            except: pass

scheduler = AsyncIOScheduler(timezone=pytz.timezone("Europe/Moscow"))

RARITIES = [
    ("Ширпотрёб",     "Ширпотреб",     "S"),
    ("Необычный",     "Необычный",     "N"),
    ("Редкий",        "Редкий",        "R"),
    ("Мистический",   "Мистический",   "M"),
    ("Хроматический", "Хроматический", "C"),
    ("Аркана",        "Аркана",        "A"),
    ("Платиновый",    "Платиновый",    "P"),
]

TCARD_INTERVALS = [185, 175, 165, 155, 145, 135, 125, 65]

DEVICES = [
    {"device_model": "iPhone 14 Pro Max", "system_version": "16.6.1", "app_version": "10.14.5"},
    {"device_model": "iPhone 13", "system_version": "15.7", "app_version": "10.14.5"},
    {"device_model": "Samsung Galaxy S23 Ultra", "system_version": "13.0", "app_version": "10.14.5"},
    {"device_model": "Xiaomi 13 Pro", "system_version": "13.0", "app_version": "10.14.5"},
    {"device_model": "Google Pixel 7 Pro", "system_version": "13.0", "app_version": "10.14.5"},
    {"device_model": "Redmi Note 12", "system_version": "12.0", "app_version": "10.14.5"}
]

# ──────────────────────────────────────────────
# Утилиты Терминала
# ──────────────────────────────────────────────
def make_bar(percent, length=15):
    percent = min(max(int(percent), 0), 100)
    filled = int(round((percent / 100) * length))
    return "█" * filled + "░" * (length - filled)

# ──────────────────────────────────────────────
# Ядро Базы Данных с защитой от блокировки
# ──────────────────────────────────────────────
DB_PATH = os.path.join(DATA_DIR, "bot_data.db")

def init_db():
    """Инициализация таблиц и автоматический перенос старых JSON данных"""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)  # <-- Добавлен таймаут
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS configs (
            session_name TEXT PRIMARY KEY,
            config_json TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            session_name TEXT,
            date TEXT,
            farm INTEGER DEFAULT 0,
            repair INTEGER DEFAULT 0,
            buy INTEGER DEFAULT 0,
            PRIMARY KEY (session_name, date)
        )
    """)
    conn.commit()

    if os.path.exists(CONFIGS_DIR):
        for f in os.listdir(CONFIGS_DIR):
            if f.endswith(".json"):
                s_name = f.replace(".json", "")
                try:
                    with open(os.path.join(CONFIGS_DIR, f), "r", encoding="utf-8") as file:
                        cursor.execute("INSERT OR IGNORE INTO configs (session_name, config_json) VALUES (?, ?)", (s_name, json.dumps(json.load(file), ensure_ascii=False)))
                except: pass
    if os.path.exists(STATS_DIR):
        for f in os.listdir(STATS_DIR):
            if f.endswith("_stats.json"):
                s_name = f.replace("_stats.json", "")
                try:
                    with open(os.path.join(STATS_DIR, f), "r", encoding="utf-8") as file:
                        for date_str, data in json.load(file).items():
                            cursor.execute("INSERT OR IGNORE INTO stats (session_name, date, farm, repair, buy) VALUES (?, ?, ?, ?, ?)", 
                                           (s_name, date_str, data.get("farm", 0), data.get("repair", 0), data.get("buy", 0)))
                except: pass
    conn.commit()
    conn.close()

def load_config(session_name):
    defaults = {
        "owner_id":         None,
        "enabled":          True,
        "workshop_enabled": True,
        "target_user":      None,
        "target_amount":    0,
        "tcard_enabled":    False,
        "tcard_interval":   185,
        "last_tcard_time":  0,
        "eday_enabled":     False,
        "buy_enabled":      False,
        "buy_rarity":       None,
        "buy_count":        1,
        "proxy":            None,
        "device":           random.choice(DEVICES),
        "last_mining_date": ""
    }
    conn = sqlite3.connect(DB_PATH, timeout=30.0)  # <-- Добавлен таймаут
    cursor = conn.cursor()
    cursor.execute("SELECT config_json FROM configs WHERE session_name = ?", (session_name,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        saved = json.loads(row[0])
        if "device" not in saved: saved["device"] = defaults["device"]
        defaults.update(saved)
    return defaults

def save_config(session_name, config):
    conn = sqlite3.connect(DB_PATH, timeout=30.0)  # <-- Добавлен таймаут
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO configs (session_name, config_json) VALUES (?, ?)
        ON CONFLICT(session_name) DO UPDATE SET config_json = excluded.config_json
    """, (session_name, json.dumps(config, ensure_ascii=False, indent=4)))
    conn.commit()
    conn.close()

def add_stat(session_name, stat_type, amount=1):
    today = datetime.now(pytz.timezone("Europe/Moscow")).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH, timeout=30.0)  # <-- Добавлен таймаут
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO stats (session_name, date) VALUES (?, ?)", (session_name, today))
    if stat_type in ["farm", "repair", "buy"]:
        cursor.execute(f"UPDATE stats SET {stat_type} = {stat_type} + ? WHERE session_name = ? AND date = ?", (amount, session_name, today))
    conn.commit()
    conn.close()

def get_user_all_time_stats(session_name):
    conn = sqlite3.connect(DB_PATH, timeout=30.0)  # <-- Добавлен таймаут
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(farm), SUM(repair), SUM(buy) FROM stats WHERE session_name = ?", (session_name,))
    row = cursor.fetchone()
    conn.close()
    if row and row[0] is not None:
        return {"farm": row[0], "repair": row[1], "buy": row[2]}
    return {"farm": 0, "repair": 0, "buy": 0}

def get_admin_dashboard_stats():
    now = datetime.now(pytz.timezone("Europe/Moscow"))
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    month_ago = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    leaderboard = {}
    
    conn = sqlite3.connect(DB_PATH, timeout=30.0)  # <-- Добавлен таймаут
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT session_name FROM stats")
    sessions = [r[0] for r in cursor.fetchall()]
    
    for s_name in sessions:
        cursor.execute("SELECT SUM(farm), SUM(repair), SUM(buy) FROM stats WHERE session_name = ? AND date >= ?", (s_name, week_ago))
        w = cursor.fetchone()
        cursor.execute("SELECT SUM(farm), SUM(repair), SUM(buy) FROM stats WHERE session_name = ? AND date >= ?", (s_name, month_ago))
        m = cursor.fetchone()
        
        leaderboard[s_name] = {
            "w_farm": w[0] or 0, "w_rep": w[1] or 0, "w_buy": w[2] or 0,
            "m_farm": m[0] or 0, "m_rep": m[1] or 0, "m_buy": m[2] or 0
        }
    conn.close()
    return leaderboard

def get_owned_sessions(chat_id):
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cursor = conn.cursor()
    cursor.execute("SELECT session_name, config_json FROM configs")
    rows = cursor.fetchall()
    conn.close()
    
    owned = []
    for r in rows:
        s_name = r[0]
        try:
            cfg = json.loads(r[1])
            # Железная привязка к owner_id возвращена на место
            if cfg.get("owner_id") == chat_id:
                owned.append(s_name)
        except Exception: 
            pass
    return owned

# === Сбор абсолютно всех сессий из базы строго для Админ-Менеджера ===
def get_all_sessions():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cursor = conn.cursor()
    cursor.execute("SELECT session_name FROM configs")
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]
# ===========================================================================

# ──────────────────────────────────────────────
# APScheduler Задачи
# ──────────────────────────────────────────────
async def daily_summary_job(bot: Client):
    today_str = datetime.now(pytz.timezone("Europe/Moscow")).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(DISTINCT session_name), SUM(farm), SUM(repair), SUM(buy) FROM stats WHERE date = ?", (today_str,))
    row = cursor.fetchone()
    conn.close()

    active_count = row[0] or 0
    total_farm = row[1] or 0
    total_repair = row[2] or 0
    total_buy = row[3] or 0

    if total_farm == 0 and total_repair == 0 and total_buy == 0:
        text = f"📊 **ЕЖЕДНЕВНАЯ СВОДКА ({today_str})**\n\nЗа сегодня активность в базе отсутствует."
    else:
        text = (
            f"📊 **ЕЖЕДНЕВНАЯ СВОДКА ({today_str})**\n"
            f"───────────────────\n"
            f"🤖 Активных ферм: {active_count}\n\n"
            f"⛏ Собрано ферм: **{total_farm}** раз\n"
            f"🛠 Принято ремонтов: **{total_repair}** шт.\n"
            f"🛍 Куплено телефонов: **{total_buy}** шт.\n"
            f"───────────────────"
        )
    for admin_id in ADMIN_IDS:
        try: await bot.send_message(admin_id, text)
        except: pass

async def scheduled_tcard(client: Client, session_name: str):
    try:
        await asyncio.sleep(random.uniform(1.0, 15.0)) # Stagger Module
        await client.send_chat_action(GAME_BOT, ChatAction.TYPING) # <--- ИМИТАЦИЯ
        await asyncio.sleep(random.uniform(1.5, 3.0))
        await client.send_message(GAME_BOT, "ткарточка")
        
        # обновляем время в конфиге
        config = load_config(session_name)
        config["last_tcard_time"] = int(time.time())
        save_config(session_name, config)
        
        print(f"[🃏 {session_name}] ТКарточка отправлена")
    except Exception as e:
        print(f"[🔴 ERROR] Ошибка при плановой отправке ТКарточки для {session_name}: {e}")

async def scheduled_mining(client: Client, session_name: str):
    try:
        cfg = load_config(session_name)
        
        # === БЛОКИРОВКА: ПРОВЕРЯЕМ ДАТУ ===
        today_str = datetime.now(pytz.timezone("Europe/Moscow")).strftime("%Y-%m-%d")
        if cfg.get("last_mining_date") == today_str:
            # Убрали вывод спама в терминал
            return
        # ==================================
        
        if cfg.get("eday_enabled"):
            await client.send_chat_action(GAME_BOT, ChatAction.TYPING) # <--- ИМИТАЦИЯ
            await asyncio.sleep(random.uniform(1.0, 2.5))
            await client.send_message(GAME_BOT, "Ежедневная награда")
            await asyncio.sleep(random.uniform(5.0, 10.0))
        
        await client.send_chat_action(GAME_BOT, ChatAction.TYPING) # <--- ИМИТАЦИЯ
        await asyncio.sleep(random.uniform(1.5, 3.0))
        await client.send_message(GAME_BOT, "тмайнинг")
        
        print(f"[⛏ {session_name}] Запрос фермы отправлен")
    except Exception as e:
        # Вывод ошибку в терминале, если она произойдет
        print(f"[🔴 ERROR] Сбой в scheduled_mining для {session_name}: {e}")

def update_scheduler_jobs(client: Client, session_name: str):
    cfg = load_config(session_name)
    j_tcard = f"{session_name}_tcard"
    j_mining = f"{session_name}_mining"
    
    if scheduler.get_job(j_tcard): scheduler.remove_job(j_tcard)
    if scheduler.get_job(j_mining): scheduler.remove_job(j_mining)
    
    if cfg.get("enabled"):
        if cfg.get("tcard_enabled") and cfg.get("tcard_interval", 0) > 0:
            scheduler.add_job(scheduled_tcard, 'interval', minutes=cfg["tcard_interval"], args=[client, session_name], id=j_tcard)
        # Запуск каждый день в 1 час ночи по МСК.
        # случайный сдвиг в 0-60 минут, чтобы аккаунты не стучались одновременно.
        scheduler.add_job(
            scheduled_mining, 
            'cron', 
            hour=1, 
            minute=random.randint(0, 59), 
            args=[client, session_name], 
            id=j_mining
        )

    run_minute = random.randint(0, 59)
    print(f"⏰ [{session_name}] Майнинг запланирован на 01:{run_minute:02d}")
    
# ──────────────────────────────────────────────
# Воркеры Юзербота (Переводы и Закупка)
# ──────────────────────────────────────────────
async def delayed_payment(client: Client, session_name: str):
    await asyncio.sleep(random.randint(60, 180))
    config = load_config(session_name)
    if config.get("enabled") and config.get("target_user") and config.get("target_amount"):
        user   = config["target_user"]
        amount = config["target_amount"]
        await asyncio.sleep(random.uniform(1.5, 4.0)) # Human Imitation
        await client.send_message(GAME_BOT, f"/pay {user} {amount} Майнинг ферма")
        print(f"[📡 {session_name}] Отправлен перевод {amount} для {user}")

async def execute_auto_buy(client: Client, session_name: str, rarity: str, count: int):
    # === БЛОКИРОВКА: ПРОВЕРЯЕМ ДАТУ ПОКУПКИ ===
    cfg = load_config(session_name)
    today_str = datetime.now(pytz.timezone("Europe/Moscow")).strftime("%Y-%m-%d")
    if cfg.get("last_buy_date") == today_str:
        print(f"🛑 [{session_name}] Лимит закупки на сегодня исчерпан. Ждем завтра.")
        return
    # ==========================================

    chat_id = GAME_BOT
    await asyncio.sleep(random.uniform(2.5, 5.0)) 
    try:
        await client.send_message(chat_id, "Магазин телефонов")
    except Exception as e:
        print(f"[❌ {session_name}] Сбой старта магазина: {e}")
        return

    steps = [
        f"shop_rarity_{rarity}", f"shop_phone_{rarity}_1", f"shop_propose_bulk_{rarity}_1",
        f"shop_bulk_select_{rarity}_1_{count}", f"shop_confirm_bulk_{rarity}_1_{count}"
    ]
    
    for step_cb in steps:
        await asyncio.sleep(random.uniform(1.5, 3.5)) # Human Imitation Delay
        clicked = False
        target_norm = step_cb.lower().replace("ё", "е")
        
        for _ in range(6):
            try:
                async for m in client.get_chat_history(chat_id, limit=5):
                    if not m.reply_markup or not m.reply_markup.inline_keyboard: continue
                    for row in m.reply_markup.inline_keyboard:
                        for btn in row:
                            if not btn.callback_data: continue
                            btn_cb = btn.callback_data.decode('utf-8') if isinstance(btn.callback_data, bytes) else str(btn.callback_data)
                            if btn_cb.lower().replace("ё", "е") == target_norm:
                                await client.request_callback_answer(chat_id, m.id, btn.callback_data)
                                clicked = True
                                break
                        if clicked: break
                    if clicked: break
            except Exception: pass
            if clicked: break
            await asyncio.sleep(1)
            
        if not clicked:
            print(f"[⚠️ {session_name}] Зависание закупки на шаге '{step_cb}'.")
            return
            
    print(f"[✅ {session_name}] Закупка ({count}x {rarity}) выполнена!")
    add_stat(session_name, "buy", count)
    
    # === СОХРАНЯЕМ ДАТУ ПОКУПКИ ===
    cfg["last_buy_date"] = today_str
    save_config(session_name, cfg)
    # ==============================

# ──────────────────────────────────────────────
# Обработчик сообщений игрового бота
# ──────────────────────────────────────────────
async def handle_bot_message(client: Client, message: Message):
    if not message.chat or message.chat.username != GAME_BOT: return
    session_name = client.name
    config = load_config(session_name)
    if not config.get("enabled"): return

    def cb_str(button):
        if not button or button.callback_data is None: return ""
        raw = button.callback_data
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    # 1. Проверка на запрос из мастерской
    if config.get("workshop_enabled") and message.text and "Вам пришел запрос на ремонт телефона" in message.text:
        if message.reply_markup and message.reply_markup.inline_keyboard:
            for row in message.reply_markup.inline_keyboard:
                for btn in row:
                    cbs = cb_str(btn)
                    if cbs.startswith("ws_ord_acc_"):
                        try:
                            await asyncio.sleep(random.uniform(1.5, 3.8)) # Human Imitation
                            await client.request_callback_answer(message.chat.id, message.id, btn.callback_data)
                            add_stat(session_name, "repair", 1)
                            return
                        except Exception: return

    if config.get("tcard_enabled"):
        current_time = int(time.time())
        last_tcard = config.get("last_tcard_time", 0)
        cooldown_seconds = config.get("tcard_interval", 185) * 60 
        
        if current_time - last_tcard >= cooldown_seconds:
            try:
                print(f"[🃏 {session_name}] Интервал прошел. Отправляю команду 'ТКарточка'...")
                # Сразу фиксируем время ДО отправки, чтобы избежать race condition при задержках
                config["last_tcard_time"] = current_time
                save_config(session_name, config)
                
                await client.send_message(GAME_BOT, "ТКарточка")
            except Exception as e:
                print(f"[🔴 ERROR] Ошибка при обработке ТКарточки для {session_name}: {e}")
        else:
            # сколько минут осталось до следующего сброса
            remains = int((cooldown_seconds - (current_time - last_tcard)) / 60)
            # print(f"[⚙️ DEBUG] [{session_name}] До следующей ТКарточки осталось {remains} мин.")
            pass
    # 2. Проверка на Ежедневную награду по алгоритму
    msg_text = message.text or message.caption or ""
    if config.get("eday_enabled") and "Ежедневные награды" in msg_text:
        if message.reply_markup and message.reply_markup.inline_keyboard:
            for row in message.reply_markup.inline_keyboard:
                for btn in row:
                    cbs = cb_str(btn)
                    if cbs.startswith("confirm_daily_claim_"):
                        try:
                            await asyncio.sleep(random.uniform(1.5, 3.5)) # Имитация человека
                            await client.request_callback_answer(message.chat.id, message.id, btn.callback_data)
                            print(f"[🎁 {session_name}] Ежедневная награда успешно забрана!")
                            return
                        except Exception as e:
                            print(f"[🔴 ERROR] Ошибка клика ежедневки для {session_name}: {e}")
                            return
                        except Exception: return

    if not message.reply_markup or not message.reply_markup.inline_keyboard: return

    # 3. Обработка кнопок (ферма, оплата)
    for row in message.reply_markup.inline_keyboard:
        for btn in row:
            cbs = cb_str(btn)
            if "farm_claim" in cbs:
                try:
                    # === Защита от двойных срабатываний при обновлении сообщения ===
                    today_str = datetime.now(pytz.timezone("Europe/Moscow")).strftime("%Y-%m-%d")
                    if config.get("last_mining_date") == today_str:
                        continue # Если сегодня уже кликали, игнорируем дубль
                    # =======================================================================

                    await asyncio.sleep(random.uniform(1.2, 3.5)) # Human Imitation
                    await client.request_callback_answer(message.chat.id, message.id, btn.callback_data)
                    add_stat(session_name, "farm", 1)
                    
                    config["last_mining_date"] = today_str
                    save_config(session_name, config)
                    
                    asyncio.create_task(delayed_payment(client, session_name))
                    if config.get("buy_enabled") and config.get("buy_rarity") and config.get("buy_count"):
                        asyncio.create_task(execute_auto_buy(client, session_name, config["buy_rarity"], config["buy_count"]))
                except Exception: pass
            elif cbs.startswith("pay_confirm_"):
                try:
                    parts = cbs.split("_")
                    if len(parts) >= 5:
                        btn_target_id, btn_amount, btn_sender_id = int(parts[2]), int(parts[3]), int(parts[4])
                        if btn_sender_id == client.me.id and btn_amount == config.get("target_amount", 0):
                            target_match = False
                            if "target_user_id" in config: target_match = (btn_target_id == config["target_user_id"])
                            else:
                                conf_target = config.get("target_user")
                                if conf_target:
                                    try:
                                        t_user = await client.get_users(conf_target)
                                        config["target_user_id"] = t_user.id
                                        save_config(session_name, config)
                                        target_match = (btn_target_id == t_user.id)
                                    except Exception: pass
                            if target_match:
                                await asyncio.sleep(random.uniform(0.5, 2.0))
                                await client.request_callback_answer(message.chat.id, message.id, btn.callback_data)
                except Exception as e:
                    print(f"[🔴 ERROR] Сбой автоматического подтверждения перевода: {e}")

# ──────────────────────────────────────────────
# Инициализация Инстансов
# ──────────────────────────────────────────────
async def launch_userbot_instance(session_name):
    if session_name in active_clients: return
    try:
        cfg = load_config(session_name)
        dev = cfg["device"]
        proxy_dict = cfg.get("proxy") # Ожидается словарь, например: {"scheme": "socks5", "hostname": "ip", "port": 1080, "username": "log", "password": "pas"}
        
        client = Client(
            name=session_name, 
            workdir=SESSIONS_DIR, 
            api_id=int(API_ID), 
            api_hash=API_HASH, 
            plugins=None,
            proxy=proxy_dict,
            device_model=dev["device_model"],
            system_version=dev["system_version"],
            app_version=dev["app_version"]
        )
        
        @client.on_message(filters.chat(GAME_BOT))
        @client.on_edited_message(filters.chat(GAME_BOT))
        async def b_handler(c, m): await handle_bot_message(c, m)
        
        await client.start()
        try: 
            async for _ in client.get_dialogs(limit=20): pass
        except Exception: pass

        active_clients[session_name] = client
        update_scheduler_jobs(client, session_name)
        print(f"[🟢 SYSTEM] Юзербот '{session_name}' запущен ({dev['device_model']}).")
        
        if cfg.get("enabled"):
            today_str = datetime.now(pytz.timezone("Europe/Moscow")).strftime("%Y-%m-%d")
            last_date = cfg.get("last_mining_date", "")
            if last_date != today_str:
                print(f"[⏱ {session_name}] Наступил новый день (или ферма еще не собиралась). Запускаю сбор...")
                asyncio.create_task(scheduled_mining(client, session_name))
                
    except (AuthKeyUnregistered, SessionRevoked, UserDeactivated) as e:
        print(f"[🔴 FATAL] Сессия {session_name} умерла (Слет авторизации): {e}")
        kb = [[InlineKeyboardButton("🔄 Переавторизовать", callback_data=f"cfg_reauth_{session_name}")]]
        asyncio.create_task(send_critical_alert(
            f"⚠️ **КРИТИЧЕСКИЙ СБОЙ АВТОРИЗАЦИИ**\n\nАккаунт `{session_name}` принудительно отключен сервером Telegram (Сессия недействительна).\n\nЛог: `{e}`", 
            kb
        ))
    except Exception as e: 
        print(f"[🔴 ERROR] Сбой старта {session_name}: {e}")
        asyncio.create_task(send_alert(f"⚠️ **Ошибка юзербота!**\nСессия: `{session_name}`\nОшибка: `{e}`"))

async def init_existing_sessions():
    await asyncio.sleep(2)
    files = [f for f in os.listdir(SESSIONS_DIR) if f.endswith(".session")]
    for f in files:
        s_name = f.replace(".session", "")
        if "master_bot" in s_name or s_name == "auth_manager_bot":
            continue
        asyncio.create_task(launch_userbot_instance(s_name))

# ──────────────────────────────────────────────
# Мастер Терминал (Панель Управления)
# ──────────────────────────────────────────────
def get_pin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1", callback_data="pin_1"), InlineKeyboardButton("2", callback_data="pin_2"), InlineKeyboardButton("3", callback_data="pin_3")],
        [InlineKeyboardButton("4", callback_data="pin_4"), InlineKeyboardButton("5", callback_data="pin_5"), InlineKeyboardButton("6", callback_data="pin_6")],
        [InlineKeyboardButton("7", callback_data="pin_7"), InlineKeyboardButton("8", callback_data="pin_8"), InlineKeyboardButton("9", callback_data="pin_9")],
        [InlineKeyboardButton("⬅️", callback_data="pin_del"), InlineKeyboardButton("0", callback_data="pin_0"), InlineKeyboardButton("🗑", callback_data="pin_clear")],
        [InlineKeyboardButton("❌ Отмена", callback_data="pin_cancel")],
    ])

def format_code_display(code: str):
    display = " ".join(list(code))
    if len(code) < 5:
        display += " " if len(code) > 0 else ""
        display += " ".join(["⚪️"] * (5 - len(code)))
    return display

def get_main_keyboard(sess, chat_id):
    cfg = load_config(sess)
    status_text = "🔴 Стоп" if cfg.get("enabled") else "🟢 Старт"
    ws_text = "🛠 Мастерская: Вкл" if cfg.get("workshop_enabled") else "🛠 Мастерская: Выкл"
    eday_text = "🎁 Ежедневка: Вкл" if cfg.get("eday_enabled") else "🎁 Ежедневка: Выкл"
    
    # основа в 2 столбца
    kb = [
        [InlineKeyboardButton(status_text, callback_data="cfg_toggle"), InlineKeyboardButton("🛠 Дебаг", callback_data="cfg_debug")],
        [InlineKeyboardButton(ws_text, callback_data="cfg_wstoggle"), InlineKeyboardButton(eday_text, callback_data="cfg_edaytoggle")],
        [InlineKeyboardButton("🛍 Магазин", callback_data="cfg_buymenu"), InlineKeyboardButton("🃏 ТКарточка", callback_data="cfg_tcardmenu")],
        [InlineKeyboardButton("🎯 Таргет переводов", callback_data="cfg_target")]
    ]
    
    # Докидываем кнопки админа
    if chat_id in ADMIN_IDS:
        kb[-1].append(InlineKeyboardButton("📋 Сессии", callback_data="cfg_sesslist"))
        kb.append([InlineKeyboardButton("📊 Дашборд", callback_data="cfg_admin_dashboard"), InlineKeyboardButton("🗂 Менеджер", callback_data="cfg_sessmanage_0")])
    else:
        kb[-1].append(InlineKeyboardButton("📈 Статистика", callback_data="cfg_user_stats"))
        
    return InlineKeyboardMarkup(kb)
def setup_bot_handlers(bot: Client):
    @bot.on_message(filters.command("start") & filters.private)
    async def start_cmd(c, m):
        user_states[m.chat.id] = {"step": "IDLE"}
        await m.reply_text(
            "📟 **PGUB TERMINAL v3.1 (PRO)**\n\n"
            "Доступные команды терминала (также доступны через кнопку 'Меню'):\n"
            "🟣 **/auth** — Привязать новый аккаунт\n"
            "🟣 **/config** — Открыть панель управления сессиями"
        )

    @bot.on_message(filters.command("update") & filters.private)
    async def update_cmd(c, m):
        if m.chat.id not in ADMIN_IDS:
            await m.reply_text("🚫 Доступ запрещен.")
            return

        status_msg = await m.reply_text("⏳ **Запуск процесса обновления...**\nСинхронизирую локальные файлы с репозиторием Git...")
        try:
            # git pull через изолированный процесс
            process = subprocess.Popen(["git", "pull"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = process.communicate()

            if process.returncode != 0:
                await status_msg.edit_text(f"❌ **Ошибка при синхронизации Git Pull:**\n```{stderr}```")
                return

            if "Already up to date" in stdout:
                await status_msg.edit_text("🟢 **Изменений в репозитории не обнаружено.** Бот уже обновлен до последней версии.")
                return

            await status_msg.edit_text("✅ **Файлы успешно скачаны!**\nПерезапускаю ядро терминала...")
            
            # Если в коде используется глобальный scheduler, перед перезапуском тушим его:
            try:
                if 'scheduler' in globals() and scheduler.running:
                    scheduler.shutdown(wait=False)
            except Exception:
                pass

            # Горячая подмена процесса в ОС (Мягкий перезапуск)
            os.execv(sys.executable, [sys.executable] + sys.argv)

        except Exception as e:
            await status_msg.edit_text(f"💥 **Критический сбой модуля обновлений:**\n`{e}`")

    @bot.on_message(filters.private & (filters.text | filters.contact))
    async def process_text(c, m):
        text = m.text.strip() if m.text else ""
        chat_id = m.chat.id
        state = user_states.get(chat_id, {"step": "IDLE"})
        step = state.get("step")

        if text.lower() in ["/config", "/настройки"]:
            user_states[chat_id] = {"step": "IDLE"}
            owned_sessions = get_owned_sessions(chat_id)
            
            if not owned_sessions:
                await m.reply_text("❌ **Сессий прикрепленных к твоему аккаунту нет, авторизуйся заново через /auth.**")
                return
                
            sess = state.get("editing_sess")
            if sess not in owned_sessions: 
                sess = owned_sessions[0]
                
            user_states[chat_id]["editing_sess"] = sess
            await m.reply_text(f"💻 **ПАНЕЛЬ УПРАВЛЕНИЯ**\n\n🟣 **Сессия:** `{sess}`", reply_markup=get_main_keyboard(sess, chat_id))
            return

        if text.lower() in ["/auth", "/авторизация"]:
            user_states[chat_id] = {"step": "WAIT_PHONE"}
            kb = [
                [KeyboardButton("📱 Поделиться номером", request_contact=True)],
                [KeyboardButton("❌ Отмена")]
            ]
            await m.reply_text(
                "📱 **Инициализация привязки аккаунта**\n\n"
                "Нажми кнопку **'Поделиться номером'** ниже или отправь номер строкой (`+79991234567`):",
                reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True)
            )
            return

        if text == "❌ Отмена":
            user_states[chat_id] = {"step": "IDLE"}
            await m.reply_text("🛑 Действие отменено.", reply_markup=ReplyKeyboardRemove())
            return

        if step == "WAIT_PHONE":
            phone = None
            if m.contact:
                phone = m.contact.phone_number
                if not phone.startswith("+"):
                    phone = "+" + phone
            elif text.startswith("+") and len(text) > 9:
                phone = text.replace(" ", "")

            if phone:
                session_name = f"user_{phone.replace('+', '')}"
                
                # Защита от database is locked. Проверяем, не держит ли уже кто-то этот файл.
                if session_name in active_clients:
                    await m.reply_text("❌ Этот аккаунт уже авторизован и работает в фоне! База данных заблокирована.\nСначала останови его через Менеджер.")
                    user_states[chat_id] = {"step": "IDLE"}
                    return

                cfg = load_config(session_name)
                cfg["owner_id"] = chat_id
                save_config(session_name, cfg)

                dev = cfg["device"]
                await m.reply_text(f"Связываюсь с Telegram... \n(Маскировка: {dev['device_model']}) ⏳", reply_markup=ReplyKeyboardRemove())
                
                client = Client(
                    name=session_name, workdir=SESSIONS_DIR, api_id=int(API_ID), api_hash=API_HASH, in_memory=False,
                    device_model=dev["device_model"], system_version=dev["system_version"], app_version=dev["app_version"]
                )
                try:
                    await client.connect()
                    code_info = await client.send_code(phone)
                    user_states[chat_id] = {
                        "step": "WAIT_CODE", "phone": phone, "session_name": session_name,
                        "client": client, "phone_code_hash": code_info.phone_code_hash, "entered_code": ""
                    }
                    await m.reply_text(f"📲 Код отправлен на {phone}.\n\n**ПИН-ПАД:** {format_code_display('')}\n\nВводи:", reply_markup=get_pin_keyboard())
                except Exception as e:
                    await m.reply_text(f"❌ Ошибка: {e}")
                    # Безопасное отключение. Проверяем, открыта ли дверь, прежде чем её закрывать.
                    try:
                        if client.is_connected:
                            await client.disconnect()
                    except: pass
                    user_states[chat_id] = {"step": "IDLE"}
            else:
                await m.reply_text("❌ Неверный формат номера.")

        elif step == "WAIT_CODE":
            msg = await m.reply_text("⚠️ Вводи код инлайн-кнопками выше.")
            await asyncio.sleep(3)
            await msg.delete()

        elif step == "WAIT_PASSWORD":
            client, session_name = state["client"], state["session_name"]
            try:
                await client.check_password(text)
                await m.reply_text("✅ 2FA подтвержден.\nОткрой настройки через **/config**")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}
                asyncio.create_task(launch_userbot_instance(session_name))
            except Exception as e:
                await m.reply_text(f"❌ Неверный пароль: {e}")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}

        elif step == "WAIT_TARGET":
            sess = state.get("editing_sess")
            if sess not in get_owned_sessions(chat_id): return
            parts = text.split()
            if len(parts) >= 2:
                target_user = parts[0]
                try:
                    amount = int(parts[1])
                    cfg = load_config(sess)
                    cfg["target_user"], cfg["target_amount"] = target_user, amount
                    if sess in active_clients:
                        try:
                            t_obj = await active_clients[sess].get_users(target_user)
                            cfg["target_user_id"] = t_obj.id
                        except: pass
                    save_config(sess, cfg)
                    user_states[chat_id] = {"step": "IDLE"}
                    await m.reply_text(f"🎯 **Цель обновлена**\nСумма: {amount}", reply_markup=get_main_keyboard(sess, chat_id))
                except ValueError:
                    await m.reply_text("❌ Сумма - число (пример: `@undef 1000`):")
            else:
                await m.reply_text("❌ Формат: `@undef 1000`")

        elif step == "WAIT_RENAME":
            if chat_id not in ADMIN_IDS: return
            old_sess = state.get("editing_sess")
            if old_sess not in get_owned_sessions(chat_id): return
            new_sess = text.replace(" ", "_")
            if new_sess in active_clients or new_sess in ["auth_manager_bot", "master_bot", "master_bot_v2"]:
                await m.reply_text("❌ Имя занято. Введи другое:")
                return
            if old_sess in active_clients:
                try: await active_clients[old_sess].stop()
                except: pass
                del active_clients[old_sess]

            # Переименовываем физический файл сессии
            old_db, new_db = os.path.join(SESSIONS_DIR, f"{old_sess}.session"), os.path.join(SESSIONS_DIR, f"{new_sess}.session")
            if os.path.exists(old_db): os.rename(old_db, new_db)
            
            # Корректно обновляем имя сессии в базе SQLite
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            cursor = conn.cursor()
            cursor.execute("UPDATE configs SET session_name = ? WHERE session_name = ?", (new_sess, old_sess))
            cursor.execute("UPDATE stats SET session_name = ? WHERE session_name = ?", (new_sess, old_sess))
            conn.commit()
            conn.close()

            user_states[chat_id] = {"step": "IDLE", "editing_sess": new_sess}
            await m.reply_text(f"🔄 Сессия успешно переименована в `{new_sess}`.", reply_markup=get_main_keyboard(new_sess, chat_id))
            asyncio.create_task(launch_userbot_instance(new_sess))

        elif step == "WAIT_SEARCH":
            if chat_id not in ADMIN_IDS: return
            query = text.lower()
            owned = get_owned_sessions(chat_id)
            results = [s for s in owned if query in s.lower()]
            user_states[chat_id]["step"] = "IDLE"
            
            if not results:
                await m.reply_text(f"🔍 По запросу `{text}` ничего не найдено.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К списку", callback_data="cfg_sessmanage_0")]]))
                return
                
            kb = [[InlineKeyboardButton(f"⚙️ {s}", callback_data=f"cfg_select_{s}")] for s in results]
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="cfg_sessmanage_0")])
            await m.reply_text(f"🔍 **Результаты поиска:**", reply_markup=InlineKeyboardMarkup(kb))

    @bot.on_callback_query(filters.regex(r"^pin_"))
    async def pin_callback(c, cq: CallbackQuery):
        chat_id = cq.message.chat.id
        state   = user_states.get(chat_id)
        if not state or state.get("step") != "WAIT_CODE":
            await cq.answer("Истекло", show_alert=True)
            return

        action, current_code, client = cq.data.split("_")[1], state.get("entered_code", ""), state["client"]

        if action == "cancel":
            await client.disconnect()
            user_states[chat_id] = {"step": "IDLE"}
            await cq.message.edit_text("🛑 Отменено.")
            return
        elif action == "clear": current_code = ""
        elif action == "del": current_code = current_code[:-1]
        elif action.isdigit() and len(current_code) < 5: current_code += action

        state["entered_code"] = current_code
        if len(current_code) == 5:
            await cq.message.edit_text(f"🔐 Синхронизация: {format_code_display(current_code)} …")
            try:
                await client.sign_in(state["phone"], state["phone_code_hash"], current_code)
                sess = state["session_name"]
                
                cfg = load_config(sess)
                cfg["owner_id"] = chat_id
                save_config(sess, cfg)
                
                user_states[chat_id] = {"step": "IDLE", "editing_sess": sess}
                await client.disconnect()
                await cq.message.edit_text("🟢 **Доступ подтвержден.**", reply_markup=get_main_keyboard(sess, chat_id))
                asyncio.create_task(launch_userbot_instance(sess))
            except SessionPasswordNeeded:
                user_states[chat_id]["step"] = "WAIT_PASSWORD"
                await cq.message.edit_text("🔒 Введи пароль 2FA текстом:")
                # Мусорный лог в админку убран
            except (PhoneCodeInvalid, PhoneCodeExpired):
                await cq.message.edit_text("❌ Код отклонен. /auth")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}
            except Exception as e:
                await cq.message.edit_text(f"❌ Сбой: {e}")
                await client.disconnect()
                user_states[chat_id] = {"step": "IDLE"}
        else:
            try: await cq.message.edit_text(f"📲 ПИН-ПАД:\n**Ввод:** {format_code_display(current_code)}", reply_markup=get_pin_keyboard())
            except: pass
            await cq.answer()

    @bot.on_callback_query(filters.regex(r"^cfg_"))
    async def config_callback(c, cq: CallbackQuery):
        chat_id = cq.message.chat.id
        sess = user_states.get(chat_id, {}).get("editing_sess")
        owned = get_owned_sessions(chat_id)
        
        if not sess or sess not in owned:
            await cq.answer("❌ Сессия недоступна. Напиши /config", show_alert=True)
            return
            
        data = cq.data
        cfg = load_config(sess)

        if data == "cfg_main":
            user_states[chat_id]["step"] = "IDLE"
            await cq.message.edit_text(f"💻 **ПАНЕЛЬ УПРАВЛЕНИЯ**\n\n🟣 **Сессия:** `{sess}`", reply_markup=get_main_keyboard(sess, chat_id))

        elif data == "cfg_toggle":
            cfg["enabled"] = not cfg.get("enabled")
            save_config(sess, cfg)
            update_scheduler_jobs(active_clients.get(sess), sess)
            await cq.message.edit_reply_markup(reply_markup=get_main_keyboard(sess, chat_id))

        elif data == "cfg_wstoggle":
            cfg["workshop_enabled"] = not cfg.get("workshop_enabled")
            save_config(sess, cfg)
            await cq.message.edit_reply_markup(reply_markup=get_main_keyboard(sess, chat_id))

        elif data == "cfg_edaytoggle":
            cfg["eday_enabled"] = not cfg.get("eday_enabled")
            save_config(sess, cfg)
            await cq.message.edit_reply_markup(reply_markup=get_main_keyboard(sess, chat_id))

        elif data == "cfg_target":
            user_states[chat_id]["step"] = "WAIT_TARGET"
            kb = [[InlineKeyboardButton("🔙 Отмена", callback_data="cfg_main")]]
            await cq.message.edit_text("🎯 **Конфигуратор переводов**\nПример: `@username 5000`", reply_markup=InlineKeyboardMarkup(kb))

        elif data == "cfg_admin_dashboard":
            if chat_id not in ADMIN_IDS:
                await cq.answer("🚫 Доступ разрешен только администраторам!", show_alert=True)
                return
            
            leaderboard = get_admin_dashboard_stats()
            if not leaderboard:
                await cq.message.edit_text("📊 **Дашборд пуст. Статистика еще не собрана.**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="cfg_main")]]))
                return
            
            top_week = sorted(leaderboard.items(), key=lambda x: x[1]['w_farm'], reverse=True)[:10]
            top_month = sorted(leaderboard.items(), key=lambda x: x[1]['m_farm'], reverse=True)[:10]
            
            text = "📊 **АДМИН-ДАШБОРД (РЕЙТИНГ АКТИВОВ)**\n\n"
            text += "📅 **ТОП за последнюю неделю (Ферма):**\n"
            for i, (name, s) in enumerate(top_week, 1):
                text += f"{i}. `{name}` — {s['w_farm']} ⛏ | {s['w_rep']} 🛠 | {s['w_buy']} 🛍\n"
                
            text += "\n📅 **ТОП за последний месяц (Ферма):**\n"
            for i, (name, s) in enumerate(top_month, 1):
                text += f"{i}. `{name}` — {s['m_farm']} ⛏ | {s['m_rep']} 🛠 | {s['m_buy']} 🛍\n"
                
            kb = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="cfg_main")]]
            await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))

        elif data == "cfg_user_stats":
            stats = get_user_all_time_stats(sess)
            text = (
                f"📈 **СТАТИСТИКА АККАУНТА (ЗА ВСЁ ВРЕМЯ)**\n"
                f"───────────────────\n"
                f"👤 Сессия: `{sess}`\n\n"
                f"⛏ Собрано ферм: **{stats['farm']}** раз\n"
                f"🛠 Принято ремонтов: **{stats['repair']}** шт.\n"
                f"🛍 Куплено телефонов: **{stats['buy']}** шт.\n"
                f"───────────────────\n"
                f"_(Статистика отображает полный исторический стек)_"
            )
            kb = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="cfg_main")]]
            await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))

        elif data == "cfg_sesslist":
            if chat_id not in ADMIN_IDS:
                await cq.answer("🚫 Доступ разрешен только администраторам!", show_alert=True)
                return
            
            files = [f.replace(".session", "") for f in os.listdir(SESSIONS_DIR) if f.endswith(".session")]
            clean_files = [f for f in files if f not in ["master_bot", "master_bot_v2", "auth_manager_bot"]]
            
            if not clean_files:
                text = "📋 **Активных сессий в хранилище не найдено.**"
            else:
                text = "📋 **СПИСОК ВСЕХ СЕССИЙ ДЛЯ КОПИРОВАНИЯ:**\n\n"
                for f in clean_files:
                    text += f"`{f}`\n"
                    
            kb = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="cfg_main")]]
            await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))

        elif data == "cfg_debug":
            uptime = int(time.time() - START_TIME)
            u_h, u_m, u_s = uptime // 3600, (uptime % 3600) // 60, uptime % 60
            
            try:
                cpu_pct = psutil.cpu_percent(interval=0.1)
                ram_pct = psutil.virtual_memory().percent
                disk_pct = psutil.disk_usage('/').percent
                hw_stats = (
                    f"⚙️ **HARDWARE LOAD (SERVER)**\n"
                    f"CPU: `[{make_bar(cpu_pct)}] {cpu_pct}%`\n"
                    f"RAM: `[{make_bar(ram_pct)}] {ram_pct}%`\n"
                    f"ROM: `[{make_bar(disk_pct)}] {disk_pct}%`\n"
                    f"───────────────────\n"
                )
            except Exception:
                hw_stats = "⚙️ **HARDWARE LOAD:** Недоступно (psutil не установлен)\n───────────────────\n"
            
            # Красиво и безопасно формируем статусы
            tcard_status = f"🟢 {cfg.get('tcard_interval')} мин" if cfg.get("tcard_enabled") else "🔴 выкл"
            buy_status = f"🟢 {cfg.get('buy_rarity')} × {cfg.get('buy_count')}" if cfg.get("buy_enabled") else "🔴 выкл"
            
            text = (
                f"🛠 **SYSTEM DEBUG**\n"
                f"───────────────────\n"
                f"⏱️ Аптайм: {u_h}ч {u_m}м {u_s}с\n"
                f"───────────────────\n"
                f"{hw_stats}"
                f"⚙️ Сессия: `{sess}`\n"
                f"📱 Устройство: {cfg['device']['device_model']}\n"
                f"🤖 Статус: {'🟢 Активен' if cfg.get('enabled') else '🔴 Остановлен'}\n"
                f"🛠 Мастерская: {'🟢 Вкл' if cfg.get('workshop_enabled') else '🔴 Выкл'}\n"
                f"🎁 Ежедневка: {'🟢 Вкл' if cfg.get('eday_enabled') else '🔴 Выкл'}\n"
                f"💸 Таргет: {cfg.get('target_user') or '❌'} ({cfg.get('target_amount') or 0})\n"
                f"🃏 ТКарточка: {tcard_status}\n"
                f"🛍 Закупка: {buy_status}\n"
            )
            kb = [[InlineKeyboardButton("🔙 Назад", callback_data="cfg_main")]]
            await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))

        elif data == "cfg_tcardmenu":
            kb = [[InlineKeyboardButton("🚫 Отключить", callback_data="cfg_tcardset_0")]]
            row = []
            for m in TCARD_INTERVALS:
                row.append(InlineKeyboardButton(f"{m} мин", callback_data=f"cfg_tcardset_{m}"))
                if len(row) == 2:
                    kb.append(row)
                    row = []
            if row: kb.append(row)
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="cfg_main")])
            await cq.message.edit_text("🃏 **ТКарточка Manager**", reply_markup=InlineKeyboardMarkup(kb))

        elif data.startswith("cfg_tcardset_"):
            val = int(data.split("_")[2])
            cfg["tcard_enabled"] = (val > 0)
            cfg["tcard_interval"] = val
            save_config(sess, cfg)
            update_scheduler_jobs(active_clients.get(sess), sess)
            await cq.message.edit_text(f"✅ Обновлено.", reply_markup=get_main_keyboard(sess, chat_id))

        elif data == "cfg_buymenu":
            kb = [[InlineKeyboardButton("🚫 Отключить", callback_data="cfg_buyset_OFF_0")]]
            row = []
            for label, key, short in RARITIES:
                row.append(InlineKeyboardButton(label, callback_data=f"cfg_buyrar_{short}"))
                if len(row) == 2:
                    kb.append(row)
                    row = []
            if row: kb.append(row)
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="cfg_main")])
            await cq.message.edit_text("🛍 **Auto-Shop Matrix**", reply_markup=InlineKeyboardMarkup(kb))

        elif data.startswith("cfg_buyrar_"):
            short = data.split("_")[2]
            label = next((l for l, k, c in RARITIES if c == short), "Неизвестно")
            kb = []
            row = []
            for i in range(1, 26):
                row.append(InlineKeyboardButton(str(i), callback_data=f"cfg_buyset_{short}_{i}"))
                if len(row) == 5:
                    kb.append(row)
                    row = []
            kb.append([InlineKeyboardButton("🔙 Назад", callback_data="cfg_buymenu")])
            await cq.message.edit_text(f"🛍 Категория: **{label}**\nМножитель:", reply_markup=InlineKeyboardMarkup(kb))

        elif data.startswith("cfg_buyset_"):
            short, qty = data.split("_")[2], data.split("_")[3]
            if short == "OFF":
                cfg["buy_enabled"] = False
            else:
                cfg["buy_enabled"] = True
                cfg["buy_rarity"] = next((k for l, k, c in RARITIES if c == short), None)
                cfg["buy_count"] = int(qty)
            save_config(sess, cfg)
            await cq.message.edit_text("✅ Закупка инициализирована.", reply_markup=get_main_keyboard(sess, chat_id))

        elif data.startswith("cfg_reauth_"):
            sess = data.replace("cfg_reauth_", "")
            phone_num = sess.replace("user_", "+")
            user_states[chat_id] = {"step": "WAIT_PHONE"}
            await cq.message.reply_text(f"📱 **Режим переавторизации**\n\nСессия: `{sess}`\nПожалуйста, отправь номер `{phone_num}` в чат, чтобы мы запросили новый код:")
            await cq.answer()

        elif data.startswith("cfg_sessmanage_") or data.startswith("cfg_multitoggle_") or data.startswith("cfg_multimode_") or data.startswith("cfg_mass_") or data.startswith("cfg_ind_") or data in ["cfg_search", "cfg_rename", "cfg_delete", "cfg_confirmdel"] or data.startswith("cfg_select_"):
            if chat_id not in ADMIN_IDS:
                await cq.answer("🚫 Управление сессиями доступно только администраторам!", show_alert=True)
                return

            # Инициализируем состояния мультивыбора
            if "selected_sessions" not in user_states[chat_id]:
                user_states[chat_id]["selected_sessions"] = []
            if "multi_mode" not in user_states[chat_id]:
                user_states[chat_id]["multi_mode"] = False
                
            sel_list = user_states[chat_id]["selected_sessions"]

            # Включение / выключение мультивыбора
            if data.startswith("cfg_multimode_"):
                mode = data.split("_")[2]
                page = int(data.split("_")[3])
                if mode == "on":
                    user_states[chat_id]["multi_mode"] = True
                else:
                    user_states[chat_id]["multi_mode"] = False
                    sel_list.clear() # Очищаем выбранное при выходе
                data = f"cfg_sessmanage_{page}"

            # Обработка клика по сессии в режиме мультивыбора
            if data.startswith("cfg_multitoggle_"):
                # разделитель "|", чтобы не ломать парсинг из-за имен
                raw_payload = data.replace("cfg_multitoggle_", "")
                target_s, page_str = raw_payload.split("|")
                page = int(page_str)
                
                if target_s in sel_list:
                    sel_list.remove(target_s)
                else:
                    # АДМИНЫ МОГУТ ВЫБИРАТЬ ЛЮБУЮ СЕССИЮ В МУЛЬТИВЫБОРЕ
                    if chat_id in ADMIN_IDS and target_s in get_all_sessions():
                        sel_list.append(target_s)
                    elif target_s in owned:
                        sel_list.append(target_s)
                data = f"cfg_sessmanage_{page}"

            # 1. Отрисовка списка Менеджера
            if data.startswith("cfg_sessmanage_"):
                page = int(data.split("_")[2])
                per_page = 7
                
                # АДМИНЫ ВИДЯТ ВСЕ СЕССИИ, ЮЗЕРЫ - ТОЛЬКО СВОИ
                display_list = get_all_sessions() if chat_id in ADMIN_IDS else owned
                
                total_pages = max(1, (len(display_list) + per_page - 1) // per_page)
                if total_pages == 0: total_pages = 1 # Защита от пустой базы
                
                current_sessions = display_list[page * per_page : (page + 1) * per_page]
                multi_mode = user_states[chat_id]["multi_mode"]
                
                kb = []
                for s in current_sessions:
                    if multi_mode:
                        icon = "✅" if s in sel_list else "❌"
                        kb.append([InlineKeyboardButton(f"{icon} {s}", callback_data=f"cfg_multitoggle_{s}|{page}")])
                    else:
                        kb.append([InlineKeyboardButton(f"⚙️ {s}", callback_data=f"cfg_select_{s}")])
                
                nav_row = []
                if page > 0: nav_row.append(InlineKeyboardButton("⬅️", callback_data=f"cfg_sessmanage_{page-1}"))
                if page < total_pages - 1: nav_row.append(InlineKeyboardButton("➡️", callback_data=f"cfg_sessmanage_{page+1}"))
                if nav_row: kb.append(nav_row)
                
                if multi_mode:
                    if sel_list:
                        kb.append([InlineKeyboardButton(f"⚡️ Массовые действия ({len(sel_list)})", callback_data="cfg_mass_menu")])
                    kb.append([InlineKeyboardButton("🔙 Назад", callback_data=f"cfg_multimode_off_{page}")])
                else:
                    # Мультивыбор, Поиск, Назад
                    kb.append([InlineKeyboardButton("📑 Мультивыбор", callback_data=f"cfg_multimode_on_{page}")])
                    kb.append([InlineKeyboardButton("🔍 Поиск", callback_data="cfg_search")])
                    kb.append([InlineKeyboardButton("🔙 Назад", callback_data="cfg_main")])
                
                if multi_mode:
                    text = f"🗂 **МУЛЬТИВЫБОР (Стр. {page+1}/{total_pages})**\nОтмечайте сессии для применения групповых настроек."
                else:
                    text = f"🗂 **МЕНЕДЖЕР АКТИВОВ (Стр. {page+1}/{total_pages})**\nВыберите сессию для индивидуальной настройки."
                    
                await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))

            elif data == "cfg_search":
                user_states[chat_id]["step"] = "WAIT_SEARCH"
                kb = [[InlineKeyboardButton("🔙 Отмена", callback_data="cfg_sessmanage_0")]]
                await cq.message.edit_text("🔍 Введи часть имени сессии для поиска:", reply_markup=InlineKeyboardMarkup(kb))

            # 2. Меню контроля одиночной сессии
            elif data.startswith("cfg_select_"):
                selected_sess = data.replace("cfg_select_", "")
                
                # АДМИНАМ РАЗРЕШЕНО ОТКРЫВАТЬ ЛЮБУЮ СЕССИЮ
                if chat_id in ADMIN_IDS:
                    if selected_sess not in get_all_sessions(): return
                else:
                    if selected_sess not in owned: return
                    
                user_states[chat_id]["editing_sess"] = selected_sess
                sess = selected_sess
                
                cfg = load_config(sess)
                status_text = "🔴 Стоп" if cfg.get("enabled") else "🟢 Старт"
                ws_text = "🛠 Мастерская: Вкл" if cfg.get("workshop_enabled") else "🛠 Мастерская: Выкл"
                eday_text = "🎁 Ежедневка: Вкл" if cfg.get("eday_enabled") else "🎁 Ежедневка: Выкл"
                tcard_text = "🃏 ТКарточка: Вкл" if cfg.get("tcard_enabled") else "🃏 ТКарточка: Выкл"
                tcard_status = f"🟢 {cfg.get('tcard_interval')} мин" if cfg.get("tcard_enabled") else "🔴 Выкл"

                # Проверяем реальный статус подключения клиента к серверам TG
                cli_instance = active_clients.get(sess)
                net_status = "⚡️ В сети" if cli_instance and cli_instance.is_connected else "💤 Отключен"

                text = (
                    f"🗂 **МЕНЕДЖЕР СЕССИИ:** `{sess}`\n"
                    f"───────────────────\n"
                    f"🔌 Подключение: {net_status}\n"
                    f"🤖 Ткарточка: {'🟢 Активна' if cfg.get('enabled') else '🔴 Остановлен'}\n"
                    f"🛠 Мастерская: {'🟢 Принимает' if cfg.get('workshop_enabled') else '🔴 Выкл'}\n"
                    f"🎁 Ежедневка: {'🟢 Активна' if cfg.get('eday_enabled') else '🔴 Выкл'}\n"
                    f"🃏 ТКарточка: {tcard_status}\n"
                    f"📱 Модель: {cfg['device']['device_model']}\n"
                    f"───────────────────"
                )
                kb = [
                    [InlineKeyboardButton("🎁 Забрать награду прямо сейчас", callback_data=f"cfg_force_eday_{sess}")],
                    [InlineKeyboardButton(status_text, callback_data="cfg_ind_toggle"), InlineKeyboardButton("📝 Переименовать", callback_data="cfg_rename")],
                    [InlineKeyboardButton(ws_text, callback_data="cfg_ind_wstoggle"), InlineKeyboardButton(eday_text, callback_data="cfg_ind_edaytoggle")],
                    [InlineKeyboardButton(tcard_text, callback_data="cfg_ind_tcardtoggle"), InlineKeyboardButton("🗑 Удалить", callback_data="cfg_delete")],
                    [InlineKeyboardButton("🔙 Назад в список", callback_data="cfg_sessmanage_0")]
                ]
                await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))

            elif data.startswith("cfg_force_eday_"):
                sess = data.replace("cfg_force_eday_", "")
                cli = active_clients.get(sess)
                if cli and cli.is_connected:
                    await cq.answer("Имитирую набор текста и отправляю запрос...", show_alert=False)
                    asyncio.create_task(cli.send_chat_action(GAME_BOT, ChatAction.TYPING))
                    await asyncio.sleep(2)
                    await cli.send_message(GAME_BOT, "Ежедневная награда")
                else:
                    await cq.answer("❌ Юзербот сейчас отключен от сети!", show_alert=True)

            # Логика инлайн-тумблеров для одиночной сессии внутри менеджера
            elif data in ["cfg_ind_toggle", "cfg_ind_wstoggle", "cfg_ind_edaytoggle", "cfg_ind_tcardtoggle"]:
                cfg = load_config(sess)
                if data == "cfg_ind_toggle":
                    cfg["enabled"] = not cfg.get("enabled")
                    update_scheduler_jobs(active_clients.get(sess), sess)
                elif data == "cfg_ind_wstoggle":
                    cfg["workshop_enabled"] = not cfg.get("workshop_enabled")
                elif data == "cfg_ind_edaytoggle":
                    cfg["eday_enabled"] = not cfg.get("eday_enabled")
                elif data == "cfg_ind_tcardtoggle":
                    cfg["tcard_enabled"] = not cfg.get("tcard_enabled")
                    update_scheduler_jobs(active_clients.get(sess), sess)
                
                save_config(sess, cfg)
                
                # Мгновенно обновляем интерфейс
                cfg = load_config(sess)
                status_text = "🔴 Стоп" if cfg.get("enabled") else "🟢 Старт"
                ws_text = "🛠 Мастерская: Вкл" if cfg.get("workshop_enabled") else "🛠 Мастерская: Выкл"
                eday_text = "🎁 Ежедневка: Вкл" if cfg.get("eday_enabled") else "🎁 Ежедневка: Выкл"
                tcard_text = "🃏 ТКарточка: Вкл" if cfg.get("tcard_enabled") else "🃏 ТКарточка: Выкл"
                tcard_status = f"🟢 {cfg.get('tcard_interval')} мин" if cfg.get("tcard_enabled") else "🔴 выкл"

                text = (
                    f"🗂 **МЕНЕДЖЕР СЕССИИ:** `{sess}`\n"
                    f"───────────────────\n"
                    f"🤖 Статус: {'🟢 Активен' if cfg.get('enabled') else '🔴 Остановлен'}\n"
                    f"🛠 Мастерская: {'🟢 Вкл' if cfg.get('workshop_enabled') else '🔴 Выкл'}\n"
                    f"🎁 Ежедневка: {'🟢 Вкл' if cfg.get('eday_enabled') else '🔴 Выкл'}\n"
                    f"🃏 ТКарточка: {tcard_status}\n"
                    f"📱 Модель: {cfg['device']['device_model']}\n"
                    f"───────────────────"
                )
                kb = [
                    [InlineKeyboardButton(status_text, callback_data="cfg_ind_toggle"), InlineKeyboardButton("📝 Переименовать", callback_data="cfg_rename")],
                    [InlineKeyboardButton(ws_text, callback_data="cfg_ind_wstoggle"), InlineKeyboardButton(eday_text, callback_data="cfg_ind_edaytoggle")],
                    [InlineKeyboardButton(tcard_text, callback_data="cfg_ind_tcardtoggle"), InlineKeyboardButton("🗑 Удалить навсегда", callback_data="cfg_delete")],
                    [InlineKeyboardButton("🔙 Назад в список", callback_data="cfg_sessmanage_0")]
                ]
                await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))

            elif data == "cfg_rename":
                user_states[chat_id]["step"] = "WAIT_RENAME"
                kb = [[InlineKeyboardButton("🔙 Отмена", callback_data=f"cfg_select_{sess}")]]
                await cq.message.edit_text(f"📝 Введи новое имя для сессии `{sess}`:", reply_markup=InlineKeyboardMarkup(kb))

            elif data == "cfg_delete":
                kb = [
                    [InlineKeyboardButton("⚠️ ДА, УНИЧТОЖИТЬ", callback_data="cfg_confirmdel")],
                    [InlineKeyboardButton("❌ Отмена", callback_data=f"cfg_select_{sess}")]
                ]
                await cq.message.edit_text(f"🗑 **ОПАСНАЯ ЗОНА (ДАБЛ-ЧЕК)**\n\nВы уверены, что хотите навсегда ликвидировать сессию `{sess}`?\nЭто действие сотрет файл и очистит базу данных!", reply_markup=InlineKeyboardMarkup(kb))

            elif data == "cfg_confirmdel":
                if sess in active_clients:
                    try: await active_clients[sess].stop()
                    except: pass
                    del active_clients[sess]
                    
                p_sess = os.path.join(SESSIONS_DIR, f"{sess}.session")
                if os.path.exists(p_sess): os.remove(p_sess)
                
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM configs WHERE session_name = ?", (sess,))
                cursor.execute("DELETE FROM stats WHERE session_name = ?", (sess,))
                conn.commit()
                conn.close()
                
                if sess in sel_list: sel_list.remove(sess)
                owned.remove(sess) if sess in owned else None
                user_states[chat_id]["editing_sess"] = owned[0] if owned else None
                
                await cq.answer("💥 Сессия безвозвратно стерта!", show_alert=True)
                data = "cfg_sessmanage_0"

            # 3. ЭКРАН МАССОВОГО УПРАВЛЕНИЯ ВЫБРАННЫМИ СЕССИЯМИ
            elif data == "cfg_mass_menu":
                if not sel_list:
                    await cq.answer("❌ Нет выбранных сессий!", show_alert=True)
                    return
                text = f"⚡️ **МАССОВОЕ УПРАВЛЕНИЕ ({len(sel_list)} акк.)**\n\nТекущий выбор:\n" + ", ".join([f"`{s}`" for s in sel_list])
                kb = [
                    [InlineKeyboardButton("🟢 Включить все", callback_data="cfg_mass_action_enable"), InlineKeyboardButton("🔴 Выключить все", callback_data="cfg_mass_action_disable")],
                    [InlineKeyboardButton("🛠 Мастерская: Вкл", callback_data="cfg_mass_action_wson"), InlineKeyboardButton("🛠 Мастерская: Выкл", callback_data="cfg_mass_action_wsoff")],
                    [InlineKeyboardButton("🎁 Ежедневка: Вкл", callback_data="cfg_mass_action_edayon"), InlineKeyboardButton("🎁 Ежедневка: Выкл", callback_data="cfg_mass_action_edayoff")],
                    [InlineKeyboardButton("🗑 Удалить выбранные", callback_data="cfg_mass_action_delete")],
                    [InlineKeyboardButton("🔲 Сбросить выбор", callback_data="cfg_mass_clear"), InlineKeyboardButton("🔙 Назад к списку", callback_data="cfg_sessmanage_0")]
                ]
                await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))

            elif data == "cfg_mass_clear":
                sel_list.clear()
                user_states[chat_id]["multi_mode"] = False
                await cq.answer("Выбор успешно очищен")
                data = "cfg_sessmanage_0"

            elif data.startswith("cfg_mass_action_"):
                action = data.replace("cfg_mass_action_", "")
                
                if action == "delete":
                    kb = [
                        [InlineKeyboardButton("⚠️ ДА, ТОТАЛЬНОЕ УДАЛЕНИЕ", callback_data="cfg_mass_confirm_delete")],
                        [InlineKeyboardButton("❌ Отмена", callback_data="cfg_mass_menu")]
                    ]
                    await cq.message.edit_text(f"🚨 **УЛЬТРА ОПАСНОЕ ДЕЙСТВИЕ!**\n\nВы собираетесь стереть разом **{len(sel_list)}** сессий!\nЭто действие абсолютно необратимо. Продолжаем?", reply_markup=InlineKeyboardMarkup(kb))
                    return

                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                for s in list(sel_list):
                    # АДМИНАМ РАЗРЕШЕНО ПРИМЕНЯТЬ МАССОВЫЕ ДЕЙСТВИЯ К ЛЮБЫМ СЕССИЯМ
                    if chat_id not in ADMIN_IDS and s not in owned: continue
                    cfg = load_config(s)
                    
                    if action == "enable":
                        cfg["enabled"] = True
                        update_scheduler_jobs(active_clients.get(s), s)
                    elif action == "disable":
                        cfg["enabled"] = False
                        update_scheduler_jobs(active_clients.get(s), s)
                    elif action == "wson": cfg["workshop_enabled"] = True
                    elif action == "wsoff": cfg["workshop_enabled"] = False
                    elif action == "edayon": cfg["eday_enabled"] = True
                    elif action == "edayoff": cfg["eday_enabled"] = False
                        
                    cursor.execute("INSERT OR REPLACE INTO configs (session_name, config_json) VALUES (?, ?)", (s, json.dumps(cfg, ensure_ascii=False, indent=4)))
                conn.commit()
                conn.close()
                
                await cq.answer(f"🚀 Изменения успешно применены к {len(sel_list)} сессиям!", show_alert=True)
                data = "cfg_mass_menu"

            elif data == "cfg_mass_confirm_delete":
                count = 0
                conn = sqlite3.connect(DB_PATH, timeout=30.0)
                cursor = conn.cursor()
                for s in list(sel_list):
                    # АДМИНАМ РАЗРЕШЕНО УДАЛЯТЬ ЛЮБЫЕ СЕССИИ
                    if chat_id not in ADMIN_IDS and s not in owned: continue
                    if s in active_clients:
                        try: await active_clients[s].stop()
                        except: pass
                        del active_clients[s]
                    p_sess = os.path.join(SESSIONS_DIR, f"{s}.session")
                    if os.path.exists(p_sess): os.remove(p_sess)
                    
                    cursor.execute("DELETE FROM configs WHERE session_name = ?", (s,))
                    cursor.execute("DELETE FROM stats WHERE session_name = ?", (s,))
                    count += 1
                conn.commit()
                conn.close()
                
                sel_list.clear()
                user_states[chat_id]["multi_mode"] = False
                await cq.answer(f"💥 Успешно аннигилировано сессий: {count}", show_alert=True)
                owned = get_owned_sessions(chat_id)
                user_states[chat_id]["editing_sess"] = owned[0] if owned else None
                data = "cfg_sessmanage_0"
        try: await cq.answer()
        except: pass

# ──────────────────────────────────────────────
# WEB APP ДАШБОРД (HTTP СЕРВЕР)
# ──────────────────────────────────────────────
async def web_index(request):
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, minimum-scale=1.0, maximum-scale=1.0, user-scalable=no" />
        <title>PGUB Admin Panel</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <style>
            body { 
                background-color: var(--tg-theme-bg-color, #1c1c1d); 
                color: var(--tg-theme-text-color, #ffffff); 
                font-family: -apple-system, BlinkMacSystemFont, sans-serif; 
                padding: 16px; margin: 0; 
            }
            .loader { text-align: center; margin-top: 50px; font-size: 16px; color: var(--tg-theme-hint-color, #aaaaaa); }
            .card { background-color: var(--tg-theme-secondary-bg-color, #2c2c2e); padding: 16px; border-radius: 12px; margin-bottom: 16px; }
            
            /* Стиль кликабельного контейнера сессии */
            .session-item { 
                background-color: var(--tg-theme-bg-color, #3a3a3c); 
                padding: 14px; border-radius: 8px; margin-top: 8px;
                font-family: monospace; cursor: pointer; 
                border-left: 4px solid #34c759;
                transition: background 0.2s ease;
                display: flex; justify-content: space-between; align-items: center;
            }
            .session-item:hover { background-color: rgba(255,255,255,0.05); }
            .session-item::after { content: '▼'; font-size: 10px; opacity: 0.5; }
            .session-item.active::after { content: '▲'; }

            /* Выезжающая панель настроек */
            .settings-panel { 
                display: none; background: rgba(0,0,0,0.15); 
                padding: 12px; border-radius: 0 0 8px 8px; 
                border-left: 4px solid #34c759; margin-bottom: 8px;
                border-top: 1px solid rgba(255,255,255,0.05);
            }
            
            .btn-group { display: flex; gap: 8px; margin-top: 8px; }
            .btn { 
                flex: 1; background: var(--tg-theme-button-color, #0a84ff); 
                color: var(--tg-theme-button-text-color, #ffffff); 
                border: none; padding: 10px; border-radius: 6px; 
                cursor: pointer; font-size: 13px; font-weight: 500;
            }
            .btn-danger { background: #ff3b30; }
            .error-msg { text-align: center; color: #ff3b30; font-size: 18px; margin-top: 60px; font-weight: bold; }
            h2, h3 { margin-top: 0; color: var(--tg-theme-text-color, #ffffff); }
        </style>
    </head>
    <body>
        <div id="loading" class="loader">🔐 Верификация администратора...</div>
        
        <div id="app" style="display:none;">
            <h2>🎛 Мастер-Панель</h2>
            <div class="card" id="sessions-container"></div>
        </div>

        <script>
            window.Telegram.WebApp.ready();
            window.Telegram.WebApp.expand();

            // Отправляем данные инициализации на бэкенд для проверки прав
            fetch('/api/dashboard', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ initData: window.Telegram.WebApp.initData })
            })
            .then(res => {
                if (res.status === 200) return res.json();
                throw new Error('Forbidden');
            })
            .then(data => {
                document.getElementById('loading').style.display = 'none';
                document.getElementById('app').style.display = 'block';
                
                const container = document.getElementById('sessions-container');
                if (data.sessions.length === 0) {
                    container.innerHTML = '<h3>Активные узлы (0)</h3><i>Нет запущенных сессий</i>';
                    return;
                }
                
                let html = `<h3>Активные узлы (${data.sessions.length})</h3>`;
                data.sessions.forEach((session, index) => {
                    html += `
                        <div class="session-item" id="item-${index}" onclick="toggleSettings(${index})">
                            <span>⚙️ ${session}</span>
                        </div>
                        <div class="settings-panel" id="settings-${index}">
                            <div style="font-size: 12px; opacity: 0.6;">Управление конфигурацией:</div>
                            <div class="btn-group">
                                <button class="btn" onclick="triggerAction('${session}', 'Перезапустить')">🔄 Сброс</button>
                                <button class="btn" onclick="triggerAction('${session}', 'Остановить')">⏸ Стоп</button>
                                <button class="btn btn-danger" onclick="triggerAction('${session}', 'Удалить')">🗑 Удалить</button>
                            </div>
                        </div>
                    `;
                });
                container.innerHTML = html;
            })
            .catch(err => {
                document.getElementById('loading').innerHTML = `
                    <div class="error-msg">
                        🛑 Доступ ограничен
                        <div style="font-size:13px; font-weight:normal; margin-top:8px; color:var(--tg-theme-hint-color);">
                            Этот дашборд доступен только администраторам бота.
                        </div>
                    </div>`;
            });

            function toggleSettings(index) {
                const item = document.getElementById(`item-${index}`);
                const panel = document.getElementById(`settings-${index}`);
                const isVisible = panel.style.display === 'block';
                
                panel.style.display = isVisible ? 'none' : 'block';
                if (isVisible) item.classList.remove('active'); else item.classList.add('active');
            }

            function triggerAction(session, actionType) {
                window.Telegram.WebApp.showPopup({
                    title: actionType,
                    message: `Вы уверены, что хотите выполнить действие "${actionType}" для сессии ${session}?`,
                    buttons: [{type: 'ok', text: 'Да'}, {type: 'cancel', text: 'Отмена'}]
                });
            }
        </script>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')

def verify_telegram_webapp_data(init_data: str, bot_token: str):
    import hmac
    import hashlib
    import urllib.parse
    import json
    try:
        parsed = urllib.parse.parse_qs(init_data)
        if 'hash' not in parsed:
            return None
        received_hash = parsed.pop('hash')[0]
        
        # Формируем строку проверки, сортируя параметры по алфавиту
        sorted_items = sorted([(k, v[0]) for k, v in parsed.items()])
        data_check_string = "\n".join([f"{k}={v}" for k, v in sorted_items])
        
        # Хешируем токен бота с солью "WebAppData"
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if computed_hash == received_hash:
            return json.loads(parsed.get('user')[0])
    except Exception:
        pass
    return None

async def web_dashboard_api(request):
    try:
        data = await request.json()
        init_data = data.get("initData", "")
        
        # 1. Проверяем, что запрос действительно из Телеграма и не подделан
        user_info = verify_telegram_webapp_data(init_data, BOT_TOKEN)
        if not user_info:
            return web.Response(status=403, text="Ошибка авторизации")
            
        user_id = user_info.get("id")
        
        # 2. Проверяем права администратора
        # Код автоматически ищет список ADMIN_IDS
        allowed_admins = globals().get("ADMINS") or globals().get("ADMIN_IDS") or []
        
        if user_id not in allowed_admins:
            return web.Response(status=403, text="Доступ запрещен")
            
        # 3. Если админ — вытягиваем сессии из базы
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT session_name FROM configs")
        rows = cursor.fetchall()
        conn.close()
        
        sessions = [r[0] for r in rows]
        return web.json_response({"sessions": sessions})
        
    except Exception as e:
        return web.Response(status=500, text=str(e))

async def start_web_server():
    try:
        app = web.Application()
        app.router.add_get('/', web_index)
        app.router.add_post('/api/dashboard', web_dashboard_api) # <-- роут проверки
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 8000)
        await site.start()
        print("✅ [🌐 WEB] Веб-сервер успешно запущен на 8000!")
    except OSError:
        print("ℹ️ [🌐 WEB] Веб-сервер уже работает")


# ──────────────────────────────────────────────
# Main - Точка Входа
# ──────────────────────────────────────────────

async def main():
    # Запускаем веб-сервер в фоне
    asyncio.create_task(start_web_server())

    # Настройка глобального обработчика ошибок asyncio
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(custom_exception_handler)

    # Инициализация планировщика задач
    if not scheduler.running:
        scheduler.start()

    # Запуск существующих сессий юзерботов
    asyncio.create_task(init_existing_sessions())

    bot_client = None
    
    if BOT_TOKEN:
        print("📡 Инициализация Мастер-Терминала…")
        try:
            bot_client = Client(name="master_bot_v2", api_id=int(API_ID), api_hash=API_HASH, bot_token=BOT_TOKEN, workdir=SESSIONS_DIR)
            setup_bot_handlers(bot_client)
            await bot_client.start()
        
            # Импортируем типы кнопок меню (добавь импорт в начало функции или файла)
            from pyrogram.types import MenuButtonWebApp, MenuButtonDefault

            # Получаем список админов
            allowed_admins = globals().get("ADMINS") or globals().get("ADMIN_IDS") or []

            # 🌐 АВТО-ОБНОВЛЕНИЕ КНОПКИ ДАШБОРДА ДЛЯ ВСЕХ (Обход BotFather)
            from pyrogram.types import MenuButtonWebApp, WebAppInfo
            WEBAPP_URL = os.getenv("WEBAPP_URL")
            
            if WEBAPP_URL:
                try:
                    # Если chat_id не указан, Pyrogram меняет кнопку ГЛОБАЛЬНО для всего бота
                    await bot_client.set_chat_menu_button(
                        menu_button=MenuButtonWebApp(
                            text="Открыть веб", 
                            web_app=WebAppInfo(url=WEBAPP_URL)
                        )
                    )
                    print(f"✅ [SYSTEM] Глобальная кнопка WebApp успешно обновлена в Telegram на: {WEBAPP_URL}")
                except Exception as btn_err:
                    print(f"⚠️ [ERROR] Не удалось автоматически обновить глобальную кнопку: {btn_err}")
            else:
                print("ℹ️ Переменная WEBAPP_URL в .env не задана. Кнопка меню не обновлялась.")

            global master_bot_instance
            master_bot_instance = bot_client
            
            # Регистрируем ежедневную отправку сводки в 20:00
            scheduler.add_job(daily_summary_job, 'cron', hour=20, minute=0, args=[bot_client], id="daily_summary_report")
            
            # команды бота
            await bot_client.set_bot_commands([
                BotCommand("start", "Перезапустить terminal"),
                BotCommand("auth", "Привязать новый аккаунт"),
                BotCommand("config", "Панель управления и статистика"),
                BotCommand("update", "Синхронизировать код с Git")
            ])
            
            print("🟢 Терминал онлайн и готов к приему команд.")
        except Exception as e:
            print(f"🔴 КРАШ ТЕРМИНАЛА: {e}")
            return
    else:
        print("⚠️ Предупреждение: BOT_TOKEN не обнаружен.")
        return

    # вечно
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[🔴 SYSTEM] Терминал остановлен (Ctrl+C).")
    except asyncio.CancelledError:
        pass
