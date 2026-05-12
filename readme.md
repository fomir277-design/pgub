# PGUB – Userbot для PhoneGetCards

Многопользовательский телеграм-юзербот, работающий через Business Automation.
Автоматизирует ферму, ежедневные награды и переводы в @phonegetcardsbot.

## Установка

1. `pip install -r requirements.txt`
2. `python session_gen.py` – получите SESSION_STRING
3. Создайте `.env` по образцу
4. `python bot.py`

## Деплой на Railway

1. Залейте репозиторий на GitHub
2. Подключите Railway → Deploy from GitHub
3. Добавьте переменные окружения из `.env.example`
4. Примонтируйте Volume в `/app/data` для сохранения настроек