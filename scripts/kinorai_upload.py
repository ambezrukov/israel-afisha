"""Снимок расписания «Кинорая» с компьютера владельца → сервер оценок.

Запускается планировщиком Windows раз в день (до утреннего облачного сбора).
Сайт kinorai.co.il не отвечает облачным серверам из США, поэтому снимок делается
здесь, в Израиле, и кладётся на сервер; облачный сбор забирает его оттуда.

Ключ доступа к серверу хранится только на этом компьютере:
    %APPDATA%\\israel-afisha\\key.txt
Журнал запусков: %APPDATA%\\israel-afisha\\kinorai_upload.log
"""

import json
import logging
import os
import sys
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from afisha import kinorai  # noqa: E402

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "israel-afisha"
SERVER = "https://israel-afisha.ambezrukov.workers.dev/kinorai"


def main() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=APP_DIR / "kinorai_upload.log", level=logging.INFO, encoding="utf-8",
        format="%(asctime)s %(levelname)s %(message)s",
    )
    key = (APP_DIR / "key.txt").read_text(encoding="utf-8").strip()
    with open(ROOT / "config.toml", "rb") as f:
        cities = tomllib.load(f).get("kinorai", {}).get("cities", ["Хайфа"])
    snap = kinorai.make_snapshot(cities)
    body = json.dumps(snap, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        SERVER, data=body, method="PUT",
        headers={"authorization": f"Bearer {key}", "content-type": "application/json", "user-agent": "israel-afisha"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        result = r.read().decode("utf-8")
    logging.info("Снимок отправлен: фильмов %d, ответ %s", len(snap["films"]), result)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("Не удалось отправить снимок")
        raise
