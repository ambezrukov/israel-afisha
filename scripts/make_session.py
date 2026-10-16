"""Однократный вход в служебный Telegram-аккаунт и запись ключа сессии в секреты GitHub.

Запуск из папки проекта:
    python scripts/make_session.py

Скрипт спросит api_id, api_hash, номер телефона и код из Telegram, затем сам
сохранит в секреты репозитория TG_API_ID, TG_API_HASH и TG_SESSION через `gh`.
Ключ сессии на экран не выводится.
"""

import asyncio
import os
import re
import subprocess
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession


def set_secret(name: str, value: str) -> None:
    subprocess.run(["gh", "secret", "set", name], input=value.encode(), check=True)
    print(f"Секрет {name} сохранён в GitHub.")


async def main() -> None:
    api_id = int(input("api_id (с my.telegram.org): ").strip())
    # Обычный ввод, а не скрытый: в консоли Windows скрытое поле не принимает вставку Ctrl+V.
    api_hash = input("api_hash (32 символа, можно вставить Ctrl+V или правой кнопкой): ").strip()
    if not re.fullmatch(r"[0-9a-f]{32}", api_hash):
        sys.exit("Похоже, api_hash вставился неверно: нужны ровно 32 символа 0-9 и a-f. Запустите скрипт ещё раз.")
    # Очищаем экран, чтобы ключ не оставался видимым.
    os.system("cls" if os.name == "nt" else "clear")
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start()  # спросит телефон, код и пароль двухэтапной проверки, если он есть
    me = await client.get_me()
    print(f"Вход выполнен: {me.first_name} ({me.phone[:4]}…)")
    session = client.session.save()
    await client.disconnect()

    set_secret("TG_API_ID", str(api_id))
    set_secret("TG_API_HASH", api_hash)
    set_secret("TG_SESSION", session)


if __name__ == "__main__":
    asyncio.run(main())
