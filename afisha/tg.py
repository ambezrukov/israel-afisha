"""Чтение каналов из папки Telegram через пользовательский аккаунт (Telethon)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import GetDialogFiltersRequest
from telethon.tl.types import Channel, DialogFilter, DialogFilterChatlist

log = logging.getLogger(__name__)

# Предел на один канал за запуск, чтобы случайный завал постов не съел бюджет.
MAX_POSTS_PER_CHANNEL = 150


@dataclass
class Post:
    channel_id: int
    channel_title: str
    channel_username: str
    message_id: int
    posted_at: str
    url: str
    text: str
    images: list[bytes] = field(default_factory=list)


def make_client(api_id: int, api_hash: str, session: str) -> TelegramClient:
    return TelegramClient(StringSession(session), api_id, api_hash)


async def folder_channels(client: TelegramClient, folder: str) -> list[Channel]:
    """Каналы из папки, название которой содержит `folder` (без учёта регистра)."""
    result = await client(GetDialogFiltersRequest())
    filters = getattr(result, "filters", result)
    wanted = folder.strip().lower()
    for f in filters:
        if not isinstance(f, (DialogFilter, DialogFilterChatlist)):
            continue
        title = f.title if isinstance(f.title, str) else f.title.text
        if wanted not in title.lower():
            continue
        channels = []
        for peer in list(f.pinned_peers) + list(f.include_peers):
            entity = await client.get_entity(peer)
            if isinstance(entity, Channel) and entity.broadcast:
                channels.append(entity)
        log.info("Папка «%s»: %d каналов", title, len(channels))
        return channels
    raise RuntimeError(f"Папка «{folder}» не найдена в аккаунте")


def _post_url(channel: Channel, message_id: int) -> str:
    if channel.username:
        return f"https://t.me/{channel.username}/{message_id}"
    return f"https://t.me/c/{channel.id}/{message_id}"


async def fetch_new_posts(
    client: TelegramClient,
    channel: Channel,
    last_id: int,
    first_run_limit: int,
    want_images: bool,
    image_if_text_shorter_than: int,
    max_images: int,
) -> tuple[list[Post], int]:
    """Новые посты канала после `last_id`. Возвращает посты и id последнего сообщения."""
    limit = MAX_POSTS_PER_CHANNEL if last_id else first_run_limit
    messages = [m async for m in client.iter_messages(channel, min_id=last_id, limit=limit)]
    messages.reverse()  # от старых к новым
    if not messages:
        return [], last_id

    # Альбом в Telegram — несколько сообщений с общим grouped_id; склеиваем в один пост.
    groups: list[list] = []
    for m in messages:
        if groups and m.grouped_id and groups[-1][0].grouped_id == m.grouped_id:
            groups[-1].append(m)
        else:
            groups.append([m])

    posts: list[Post] = []
    for group in groups:
        first = group[0]
        text = "\n".join(m.message for m in group if m.message).strip()
        photos = [m for m in group if m.photo]
        images: list[bytes] = []
        if photos and (want_images or len(text) < image_if_text_shorter_than):
            for m in photos[:max_images]:
                data = await client.download_media(m, file=bytes)
                if data:
                    images.append(data)
        if not text and not images:
            continue
        posts.append(
            Post(
                channel_id=channel.id,
                channel_title=channel.title,
                channel_username=channel.username or "",
                message_id=first.id,
                posted_at=first.date.astimezone().isoformat(timespec="minutes")
                if isinstance(first.date, datetime)
                else "",
                url=_post_url(channel, first.id),
                text=text,
                images=images,
            )
        )
    return posts, messages[-1].id


async def mark_read(client: TelegramClient, channel: Channel, max_id: int) -> None:
    await client.send_read_acknowledge(channel, max_id=max_id)
