import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import API_ID, API_HASH

async def main():
    print("Генератор сессии PGUB")
    phone = input("Номер телефона (в международном формате): ")
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.start(phone=phone)
    session_str = client.session.save()
    print("\nSESSION_STRING:")
    print(session_str)
    print("\nСкопируйте эту строку в переменную SESSION_STRING на Railway.")
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())