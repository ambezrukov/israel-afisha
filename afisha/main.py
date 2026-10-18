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
import hashlib
import json
import logging
import os
import tomllib
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import copy

from . import kinorai, notify, tg, tickchak, webpages
from .extract import Extractor
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
# События хранятся на сервере оценок (видны только владельцу); здесь — рабочая копия на время запуска.
EVENTS_PATH = ROOT / "data_private" / "events.json"
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


def load_feedback() -> dict:
    """Оценки владельца с сервера (FEEDBACK_URL + FEEDBACK_KEY); при сбое — пусто, сбор не останавливаем."""
    url, key = os.environ.get("FEEDBACK_URL"), os.environ.get("FEEDBACK_KEY")
    if not url or not key:
        return load_json(FEEDBACK_PATH, {})
    try:
        req = urllib.request.Request(url, headers={"authorization": f"Bearer {key}", "user-agent": "israel-afisha"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        log.info("Оценки владельца: 👍 %d, 👎 %d", len(data.get("likes", [])), len(data.get("dislikes", [])))
        return data
    except Exception as err:  # noqa: BLE001
        log.warning("Не удалось получить оценки владельца: %s", err)
        return {"unavailable": True}


def _server(path: str) -> tuple[str, str] | None:
    url, key = os.environ.get("FEEDBACK_URL"), os.environ.get("FEEDBACK_KEY")
    return (url.replace("/api/feedback", path), key) if url and key else None


def pull_events() -> None:
    """Забирает файл событий с сервера в рабочую копию. Без сервера (локально) — работаем с файлом как есть."""
    target = _server("/api/data")
    if not target:
        return
    req = urllib.request.Request(target[0], headers={"authorization": f"Bearer {target[1]}", "user-agent": "israel-afisha"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVENTS_PATH.write_bytes(body)
    log.info("События с сервера: %d", json.loads(body).get("count", 0))


def push_events() -> None:
    """Кладёт обновлённый файл событий на сервер."""
    target = _server("/api/data")
    if not target:
        return
    req = urllib.request.Request(
        target[0], data=EVENTS_PATH.read_bytes(), method="PUT",
        headers={"authorization": f"Bearer {target[1]}", "content-type": "application/json", "user-agent": "israel-afisha"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        log.info("События сохранены на сервере: %s", r.read().decode("utf-8"))


def feedback_signature(feedback: dict) -> str:
    """Отпечаток набора 👍/👎: меняется, только когда владелец что-то оценил или снял оценку."""
    items = sorted(f"{kind}:{x['id']}" for kind in ("likes", "dislikes") for x in feedback.get(kind, []))
    return hashlib.sha1("|".join(items).encode()).hexdigest()


def rescore_store(store: Store, extractor: Extractor, today) -> None:
    events = store.upcoming(today)
    scores: list[dict] = []
    for i in range(0, len(events), RESCORE_BATCH):
        scores += extractor.rescore(events[i : i + RESCORE_BATCH], today)
    scored, merged = store.apply_scores(scores)
    log.info("Пересчитано оценок: %d, склеено дублей: %d, осталось событий: %d", scored, merged, len(store.events))


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


def scheduled_run_needed(cfg: dict, state: dict) -> bool:
    """Плановый запуск по расписанию GitHub: работаем один раз в день, не раньше часа из настроек."""
    if os.environ.get("GITHUB_EVENT_NAME") != "schedule":
        return True  # ручной запуск — всегда
    now = datetime.now(ZoneInfo(cfg["events"]["timezone"]))
    if now.hour < cfg["events"].get("run_hour", 20):
        log.info("Плановый запуск раньше %d:00 по Израилю — пропускаю", cfg["events"].get("run_hour", 20))
        return False
    if state.get("last_scheduled") == now.date().isoformat():
        log.info("Сегодня плановый сбор уже был — пропускаю")
        return False
    return True


async def run(dry_run: bool) -> None:
    cfg = load_config()
    tz = ZoneInfo(cfg["events"]["timezone"])
    now = datetime.now(tz)
    today = now.date()

    state = load_json(STATE_PATH, {})
    if not scheduled_run_needed(cfg, state):
        return
    pull_events()
    store = Store(EVENTS_PATH)
    store.renormalize()
    feedback = load_feedback()
    extractor = Extractor(
        cfg["model"]["id"], cfg["model"]["effort"], cfg["profile"],
        feedback, store.upcoming(today),
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

        # Сайт касс Tickchak LIVE: только события, которых ещё не видели.
        tk_cfg = cfg.get("tickchak", {})
        tk_seen = None
        if tk_cfg.get("enabled"):
            tk_posts, tk_seen = tickchak.fetch_new(
                tk_cfg.get("pages", []), tk_cfg.get("artists", {}), state.get("tickchak_seen", {}), tz,
            )
            for batch in batches(tk_posts, tk_cfg.get("posts_per_request", 15)):
                events = extractor.extract(batch, today)
                added, merged = store.add(events, today)
                log.info("  Tickchak: добавлено %d, склеено с известными %d", added, merged)

        # Страницы касс театров и коллективов (Батшева и т. п.): модели — только изменившиеся.
        web_hashes = None
        if cfg.get("web_pages"):
            web_posts, web_hashes = webpages.fetch_changed(cfg["web_pages"], state.get("web_hashes", {}))
            for batch in batches(web_posts, 1):
                added, merged = store.add(extractor.extract(batch, today), today)
                log.info("  %s: добавлено %d, склеено с известными %d", batch[0].channel_title, added, merged)

        # Кино на русском от «Кинорая». Расписание снимает задача на компьютере владельца
        # (сайт не пускает облако); здесь берём снимок с сервера. Новые фильмы — через модель,
        # у известных — только новые сеансы, оценки не трогаем.
        kcfg = cfg.get("kinorai", {})
        if kcfg.get("enabled"):
            snap = {}
            try:
                snap = kinorai.load_snapshot()
            except Exception as err:  # noqa: BLE001 — сбой не должен ронять весь сбор
                log.warning("КиноРай: не удалось получить снимок расписания (%s)", err)
            fetched = snap.get("fetched_at", "")
            if fetched:
                age = (datetime.now(tz) - datetime.fromisoformat(fetched)).days
                log.info("КиноРай: снимок от %s (%d дн. назад), фильмов %d", fetched, age, len(snap.get("films", {})))
                if age > kcfg.get("max_snapshot_age_days", 8):
                    log.warning("КиноРай: снимок устарел — компьютер давно не включался")
            by_url = {s["url"]: e for e in store.events for s in e["sources"]}
            new_posts = []
            for url, film in snap.get("films", {}).items():
                known = by_url.get(url)
                if known:
                    update = copy.deepcopy(known)
                    for key in ("id", "first_seen", "updated"):
                        update.pop(key, None)
                    update["sessions"] = film["sessions"]
                    update["same_as"] = known["id"]
                    store.add([update], today)
                else:
                    new_posts.append(kinorai.film_post(url, film, snap.get("about", {}).get(url, "")))
            for batch in batches(new_posts, 3):
                added, merged = store.add(extractor.extract(batch, today), today)
                log.info("  КиноРай: новых фильмов %d, склеено с известными %d", added, merged)

        removed = store.prune(today, cfg["events"]["keep_past_days"], cfg["events"]["horizon_days"])
        log.info("Удалено устаревших: %d. Всего событий: %d", removed, len(store.events))

        # Владелец поставил новые 👍/👎 — пересчитываем личные оценки всех будущих событий.
        sig = None
        if not feedback.get("unavailable"):
            sig = feedback_signature(feedback)
            if sig != state.get("feedback_sig"):
                log.info("Есть новые оценки владельца — пересчитываю оценки будущих событий")
                rescore_store(store, extractor, today)
        store.save(now.isoformat(timespec="minutes"), cfg["profile"])
        push_events()

        # Позицию чтения сохраняем и посты помечаем прочитанными только после
        # успешного сохранения событий — иначе при сбое посты потерялись бы.
        if dry_run:
            log.info("Пробный запуск: прочитанным не помечаю, позицию не сохраняю")
            return
        # Вечернее личное сообщение — не чаще раза в день.
        ncfg = cfg.get("notify", {})
        bot_token = os.environ.get("TG_BOT_TOKEN")
        if ncfg.get("enabled") and bot_token and (state.get("notified") != today.isoformat() or os.environ.get("FORCE_NOTIFY")) and not feedback.get("unavailable"):
            built = notify.build(store.upcoming(today), feedback, cfg["profile"], today, ncfg)
            if built:
                try:
                    notify.send_all(bot_token, ncfg["chat_id"], built)
                    log.info("Вечернее сообщение отправлено")
                except Exception as err:  # noqa: BLE001 — сбой отправки не должен терять собранное
                    log.warning("Не удалось отправить вечернее сообщение: %s", err)
            else:
                log.info("Нового для владельца нет — сообщение не отправляю")
            state["notified"] = today.isoformat()

        if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
            state["last_scheduled"] = today.isoformat()
        if sig:
            state["feedback_sig"] = sig
        if tk_seen is not None:
            state["tickchak_seen"] = tk_seen
        if web_hashes is not None:
            state["web_hashes"] = web_hashes
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
    pull_events()
    store = Store(EVENTS_PATH)
    store.renormalize()
    extractor = Extractor(
        cfg["model"]["id"], cfg["model"]["effort"], cfg["profile"], load_feedback(), store.upcoming(now.date()),
    )
    rescore_store(store, extractor, now.date())
    store.save(now.isoformat(timespec="minutes"), cfg["profile"])
    push_events()


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
