"""Ежедневный запуск: каналы → события → docs/data/events.json.

Переменные окружения:
  TG_API_ID, TG_API_HASH, TG_SESSION — доступ к служебному Telegram-аккаунту;
  ANTHROPIC_API_KEY — ключ Claude API.

  python -m afisha.main              обычный запуск
  python -m afisha.main --dry-run    не помечать прочитанным и не сохранять позицию чтения
  python -m afisha.main --rescore    только пересчитать оценки и склеить дубли среди собранного
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import tomllib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import tg
from .extract import Extractor
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
EVENTS_PATH = ROOT / "docs" / "data" / "events.json"
STATE_PATH = ROOT / "state" / "state.json"
FEEDBACK_PATH = ROOT / "state" / "feedback.json"
RESCORE_BATCH = 40
MAX_IMAGES_PER_REQUEST = 12

log = logging.getLogger("afisha")


def load_config() -> dict:
    with open(ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def batches(posts: list[tg.Post], size: int):
    batch: list[tg.Post] = []
    images = 0
    for post in posts:
        if batch and (len(batch) >= size or images + len(post.images) > MAX_IMAGES_PER_REQUEST):
            yield batch
            batch, images = [], 0
        batch.append(post)
        images += len(post.images)
    if batch:
        yield batch


async def run(dry_run: bool) -> None:
    cfg = load_config()
    tz = ZoneInfo(cfg["events"]["timezone"])
    now = datetime.now(tz)
    today = now.date()

    state = load_json(STATE_PATH, {})
    store = Store(EVENTS_PATH)
    store.renormalize()
    extractor = Extractor(
        cfg["model"]["id"], cfg["model"]["effort"], cfg["profile"],
        load_json(FEEDBACK_PATH, {}), store.upcoming(today),
    )
    tcfg = cfg["telegram"]

    client = tg.make_client(int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"], os.environ["TG_SESSION"])
    async with client:
        channels = await tg.folder_channels(client, tcfg["folder"])
        to_mark: list[tuple] = []
        for channel in channels:
            key = str(channel.id)
            posts, last_id = await tg.fetch_new_posts(
                client,
                channel,
                last_id=state.get(key, 0),
                first_run_limit=tcfg["first_run_limit"],
                want_images=(channel.username or "") in tcfg["image_channels"],
                image_if_text_shorter_than=tcfg["image_if_text_shorter_than"],
                max_images=tcfg["max_images_per_post"],
            )
            log.info("%s: новых постов %d", channel.title, len(posts))
            for batch in batches(posts, cfg["model"]["posts_per_request"]):
                events = extractor.extract(batch, today)
                added, merged = store.add(events, today)
                log.info("  добавлено %d, склеено с известными %d", added, merged)
            if last_id > state.get(key, 0):
                to_mark.append((channel, last_id))

        removed = store.prune(today, cfg["events"]["keep_past_days"], cfg["events"]["horizon_days"])
        log.info("Удалено устаревших: %d. Всего событий: %d", removed, len(store.events))
        store.save(now.isoformat(timespec="minutes"), cfg["profile"])

        # Позицию чтения сохраняем и посты помечаем прочитанными только после
        # успешного сохранения событий — иначе при сбое посты потерялись бы.
        if dry_run:
            log.info("Пробный запуск: прочитанным не помечаю, позицию не сохраняю")
            return
        for channel, last_id in to_mark:
            state[str(channel.id)] = last_id
            if tcfg["mark_read"]:
                await tg.mark_read(client, channel, last_id)
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=1), encoding="utf-8")


def rescore() -> None:
    """Пересчёт оценок и поиск дублей среди уже собранных событий (без Telegram)."""
    cfg = load_config()
    tz = ZoneInfo(cfg["events"]["timezone"])
    now = datetime.now(tz)
    store = Store(EVENTS_PATH)
    store.renormalize()
    events = store.upcoming(now.date())
    extractor = Extractor(
        cfg["model"]["id"], cfg["model"]["effort"], cfg["profile"], load_json(FEEDBACK_PATH, {}), events,
    )
    scores: list[dict] = []
    for i in range(0, len(events), RESCORE_BATCH):
        scores += extractor.rescore(events[i : i + RESCORE_BATCH], now.date())
    scored, merged = store.apply_scores(scores)
    log.info("Пересчитано оценок: %d, склеено дублей: %d, осталось событий: %d", scored, merged, len(store.events))
    store.save(now.isoformat(timespec="minutes"), cfg["profile"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rescore", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Логи HTTP-клиента не нужны: свои строки о токенах пишем сами.
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.rescore:
        rescore()
    else:
        asyncio.run(run(args.dry_run))


if __name__ == "__main__":
    main()
