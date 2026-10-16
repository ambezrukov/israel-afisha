"""Превращение постов в карточки событий через Claude API."""

from __future__ import annotations

import base64
import json
import logging
from datetime import date

import anthropic

from .tg import Post

log = logging.getLogger(__name__)

CATEGORIES = [
    "концерт",
    "театр",
    "стендап",
    "лекция",
    "выставка",
    "фестиваль",
    "экскурсия",
    "кино",
    "детям",
    "литература",
    "вечеринка",
    "мастер-класс",
    "игра",
    "другое",
]

LANGUAGES = ["ru", "he", "en", "mixed", "none", "unknown"]

SYSTEM_PROMPT = f"""Ты редактор культурной афиши для русскоязычных жителей Израиля.
Тебе приходят посты из Telegram-каналов с анонсами. Твоя задача — найти в них
культурные и досуговые события и описать каждое строго по схеме.

Что считать событием: концерты, спектакли, стендап, лекции и встречи с авторами,
выставки, фестивали, экскурсии и походы, кинопоказы, детские мероприятия,
литературные вечера, вечеринки, мастер-классы, квизы и игры.

Что НЕ брать: политические встречи и митинги, предвыборные мероприятия,
открытия магазинов, рекламу услуг и товаров, новости без конкретного события,
вебинары без культурного содержания, события целиком в прошлом.

Правила:
- Один пост может содержать несколько событий (подборки) — выдели каждое.
- Если событие проходит в нескольких городах или в несколько дат, это одно
  событие с несколькими sessions.
- Выставки и фестивали с периодом: date — начало, end_date — конец.
  Для однодневных событий end_date — пустая строка.
- Даты в формате YYYY-MM-DD. Если год не указан, выбери ближайший будущий
  относительно сегодняшней даты. Время — HH:MM или пустая строка.
- city — город по-русски в привычном написании: Хайфа, Тель-Авив, Иерусалим,
  Крайот, Кирьят-Ям, Нагария, Акко, Нетания, Раанана, Герцлия, Ришон-ле-Цион,
  Рамат-Ган, Гиватаим, Холон, Бат-Ям, Петах-Тиква, Ашдод, Ашкелон, Беэр-Шева,
  Реховот, Хадера, Кейсария, Зихрон-Яаков и т.д. Для онлайн — «Онлайн».
  Если город неизвестен — «Неизвестно».
- summary — 1–2 предложения СВОИМИ словами по-русски, до 220 знаков.
  Не копируй текст поста.
- title — короткое название по-русски (имена собственные можно оставить
  в оригинале).
- language — язык, на котором идёт мероприятие: ru, he, en, mixed (несколько),
  none (язык не важен: музыка без слов, выставка), unknown.
- kids — yes, если событие явно подходит детям или семьям; no, если 18+ или
  явно взрослое; иначе unknown.
- price_min — минимальная цена в шекелях; 0 — бесплатно; -1 — неизвестно.
- ticket_url — ссылка на билеты или регистрацию из поста, иначе пустая строка.
- interest — оценка 1–10, насколько событие заметное и интересное широкой
  русскоязычной аудитории: известность исполнителей, редкость (премьера,
  гастроли, фестиваль), качество организаторов. Рядовые регулярные встречи — 3–5.
- interest_reason — коротко, почему такая оценка.
- needs_check — true, если данные взяты с картинки или пост неоднозначен
  (неясна дата, город, год).
- post_index — номер поста, из которого взято событие.

Категории: {", ".join(CATEGORIES)}.
Если в постах нет подходящих событий, верни пустой список events."""

SESSION_SCHEMA = {
    "type": "object",
    "properties": {
        "date": {"type": "string"},
        "end_date": {"type": "string"},
        "time": {"type": "string"},
        "city": {"type": "string"},
        "venue": {"type": "string"},
    },
    "required": ["date", "end_date", "time", "city", "venue"],
    "additionalProperties": False,
}

EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "post_index": {"type": "integer"},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "category": {"type": "string", "enum": CATEGORIES},
        "language": {"type": "string", "enum": LANGUAGES},
        "kids": {"type": "string", "enum": ["yes", "no", "unknown"]},
        "price_min": {"type": "number"},
        "price_text": {"type": "string"},
        "ticket_url": {"type": "string"},
        "sessions": {"type": "array", "items": SESSION_SCHEMA},
        "interest": {"type": "integer"},
        "interest_reason": {"type": "string"},
        "needs_check": {"type": "boolean"},
    },
    "additionalProperties": False,
}
EVENT_SCHEMA["required"] = list(EVENT_SCHEMA["properties"])

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"events": {"type": "array", "items": EVENT_SCHEMA}},
    "required": ["events"],
    "additionalProperties": False,
}


def _guess_media_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _post_blocks(index: int, post: Post) -> list[dict]:
    header = (
        f"### Пост {index}\n"
        f"Канал: {post.channel_title}"
        + (f" (@{post.channel_username})" if post.channel_username else "")
        + f"\nОпубликован: {post.posted_at}\nТекст:\n{post.text or '(без текста, см. картинки)'}"
    )
    blocks: list[dict] = [{"type": "text", "text": header}]
    for img in post.images:
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": _guess_media_type(img),
                    "data": base64.standard_b64encode(img).decode("ascii"),
                },
            }
        )
    return blocks


class Extractor:
    def __init__(self, model: str, effort: str):
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort

    def extract(self, posts: list[Post], today: date) -> list[dict]:
        """События из пачки постов; к каждому добавляется ссылка на исходный пост."""
        content: list[dict] = [
            {"type": "text", "text": f"Сегодня {today.isoformat()}. Посты:"}
        ]
        for i, post in enumerate(posts):
            content.extend(_post_blocks(i, post))

        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
            messages=[{"role": "user", "content": content}],
        )
        if response.stop_reason == "refusal":
            log.warning("Модель отказалась обрабатывать пачку постов: %s", response.stop_details)
            return []
        if response.stop_reason == "max_tokens":
            log.warning("Ответ обрезан по max_tokens, пачка пропущена")
            return []

        text = next(b.text for b in response.content if b.type == "text")
        events = json.loads(text)["events"]
        log.info(
            "Постов: %d → событий: %d (вход %d, выход %d токенов)",
            len(posts),
            len(events),
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

        result = []
        for ev in events:
            idx = ev.pop("post_index")
            if not 0 <= idx < len(posts):
                continue
            post = posts[idx]
            ev["sources"] = [
                {
                    "channel": post.channel_title,
                    "url": post.url,
                    "posted_at": post.posted_at,
                }
            ]
            result.append(ev)
        return result
