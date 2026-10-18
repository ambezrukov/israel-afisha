"""Источник «страница сайта»: касса театра или коллектива (например, Батшевы на Smarticket).

Раз в день страница скачивается и превращается в текст; ссылки на конкретные
спектакли сохраняются в тексте, чтобы модель могла указать билеты. Если текст
не изменился со вчера (сверяется отпечаток), модели ничего не отправляется.
Иначе текст уходит модели как обычный пост: она вытаскивает события, переводит
их на русский и узнаёт уже известные (склейка дублей).
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
import urllib.parse
import urllib.request

from .tg import Post

log = logging.getLogger(__name__)

CHUNK = 7000  # символов текста на один «пост» для модели


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"user-agent": "Mozilla/5.0 (israel-afisha; personal digest)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def page_text(page: str, base_url: str, link_pattern: str) -> str:
    page = re.sub(r"<script.*?</script>|<style.*?</style>|<svg.*?</svg>", "", page, flags=re.S)

    # Ссылки на отдельные спектакли оставляем в тексте как « [ссылка]».
    def keep_link(m: re.Match) -> str:
        href = html.unescape(m.group(1))
        if link_pattern and not re.search(link_pattern, href):
            return m.group(0)
        return f"{m.group(0)} [{urllib.parse.urljoin(base_url, href)}] "

    page = re.sub(r'<a\s[^>]*href="([^"]+)"[^>]*>', keep_link, page)
    lines = (html.unescape(x).strip() for x in re.sub(r"<[^>]+>", "\n", page).split("\n"))
    text, prev = [], None
    for line in lines:
        if line and line != prev:  # убираем пустые строки и подряд идущие повторы
            text.append(line)
        prev = line
    return "\n".join(text)


def fetch_changed(pages: list[dict], hashes: dict[str, str]) -> tuple[list[Post], dict[str, str]]:
    """Посты для модели по изменившимся страницам и новые отпечатки."""
    posts: list[Post] = []
    updated = dict(hashes)
    for p in pages:
        url = p["url"]
        try:
            text = page_text(_get(url), url, p.get("link_pattern", ""))
        except Exception as err:  # noqa: BLE001 — один сбойный сайт не должен ронять весь сбор
            log.warning("%s: не удалось прочитать страницу (%s)", p["name"], err)
            continue
        start = p.get("start_marker")
        if start and start in text:
            text = text[text.index(start):]
        end = p.get("end_marker")
        if end and end in text:
            text = text[: text.index(end)]
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if hashes.get(url) == digest:
            log.info("%s: без изменений", p["name"])
            continue
        updated[url] = digest
        chunks = [text[i : i + CHUNK] for i in range(0, len(text), CHUNK)]
        log.info("%s: страница изменилась, частей для модели %d", p["name"], len(chunks))
        for n, chunk in enumerate(chunks):
            posts.append(Post(
                channel_id=0, channel_title=p["name"], channel_username="",
                message_id=n, posted_at="", url=url,
                text=(f"Страница кассы на сайте ({p.get('note', 'иврит')}). Каждая дата — отдельный сеанс; "
                      "одинаковые спектакли в разные дни — одно событие с несколькими sessions. "
                      "В квадратных скобках — ссылка на билеты этого сеанса.\n\n" + chunk),
            ))
    return posts, updated
