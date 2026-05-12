import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")          # главная сессия бота (обязательна)
GAME_BOT_USERNAME = os.getenv("GAME_BOT_USERNAME", "@phonegetcardsbot")

# Главные администраторы (неизменяемые владельцы)
GA_IDS = {8209965013, 6118149728}