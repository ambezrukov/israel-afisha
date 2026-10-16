"""Превращение постов в карточки событий и их оценка через Claude API."""

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

SCORING_RULES = """## Общая значимость (interest, 1–10)
Насколько событие заметно для широкой русскоязычной аудитории Израиля:
- 9–10 — звёзды большого масштаба, редкие гастроли («впервые в Израиле»,
  «единственный концерт»), крупные фестивали.
- 7–8 — известные артисты и театры, премьеры, громкие лекторы.
- 5–6 — качественные регулярные события: камерные концерты известных
  коллективов, спектакли местных театров, экскурсии хороших гидов.
- 3–4 — открытия выставок, вернисажи в галереях, клубные встречи.
- 1–2 — открытые микрофоны, квизы, мастер-классы, презентации книг
  малоизвестных авторов.
Повышай за уникальность («впервые», «единственный», «последний», «премьера»),
ограниченность (мало мест, билеты заканчиваются), масштаб площадки (Аудиториум,
опера, большие залы). Понижай за коммерческий оттенок (событие — по сути реклама)
и неясность (нет подробностей). Девятки и десятки — редкость, а не норма.

## Личная оценка (personal, 1–10)
Насколько событие интересно владельцу афиши — по профилю ниже, только по
содержанию. География НЕ учитывается: её учтёт программа.
Концерт любимого артиста — 10. Если предпочтения о событии ничего не говорят,
опирайся на общую значимость.

## favorite_artist
Имя любимого артиста из профиля, если он участвует в событии; иначе пустая строка."""

EXTRACT_RULES = f"""Ты редактор культурной афиши для русскоязычных жителей Израиля.
Тебе приходят посты из Telegram-каналов с анонсами. Найди в них культурные и
досуговые события и опиши каждое строго по схеме.

Что считать событием: концерты, спектакли, стендап, лекции и встречи с авторами,
выставки, фестивали, экскурсии и поездки, кинопоказы, детские мероприятия,
литературные вечера, вечеринки, мастер-классы, квизы и игры.

Что НЕ брать: политические встречи и митинги, предвыборные мероприятия,
открытия магазинов, рекламу услуг и товаров, новости без конкретного события,
фотоотчёты о прошедших событиях, вебинары без культурного содержания,
события целиком в прошлом.

Правила:
- Один пост может содержать несколько событий (подборки) — выдели каждое.
- Если событие проходит в нескольких городах или в несколько дат, это одно
  событие с несколькими sessions.
- Выставки и фестивали с периодом: date — начало, end_date — конец.
  Для однодневных событий end_date — пустая строка.
  Если сказано «с DD.MM по DD.MM, города A, B, C» без привязки дат к городам —
  сделай по сеансу на город с этим периодом.
- Даты в формате YYYY-MM-DD. Если год не указан, выбери ближайший будущий
  относительно сегодняшней даты. Время — HH:MM или пустая строка.
- city — город по-русски: Хайфа (районы Кирьят-Хаим, Адар, Бат-Галим — тоже
  Хайфа), Крайот (Кирьят-Моцкин, Кирьят-Бялик, Кирьят-Ата, Кирьят-Ям), Нешер,
  Тират-Кармель, Нагария, Акко, Кармиэль, Зихрон-Яаков, Хадера, Тель-Авив
  (включая Яффо), Рамат-Ган, Гиватаим, Холон, Бат-Ям, Герцлия, Раанана,
  Кфар-Саба, Нетания, Петах-Тиква, Ришон-ле-Цион, Реховот, Ашдод, Ашкелон,
  Беэр-Шева, Иерусалим, Эйлат и т.д. Онлайн — «Онлайн». Неизвестен — «Неизвестно».
- summary — 1–2 предложения СВОИМИ словами по-русски, до 220 знаков.
  Не копируй текст поста.
- title — короткое название по-русски (имена собственные можно в оригинале).
  Для одного и того же события держи одно название: если оно уже есть в списке
  известных событий, повтори то название.
- language — язык мероприятия: ru, he, en, mixed (несколько), none (язык не
  важен: музыка без слов, выставка), unknown.
- kids — yes, если событие явно подходит детям или семьям; no, если 18+ или
  явно взрослое; иначе unknown.
- price_min — минимальная цена в шекелях; 0 — бесплатно; -1 — неизвестно.
- ticket_url — ссылка на билеты или регистрацию из поста, иначе пустая строка.
- needs_check — true ТОЛЬКО если дата или город неизвестны либо угаданы,
  или данные прочитаны с картинки. Обычные подробные анонсы — false.
- same_as — id события из списка известных, если это то же самое событие
  (тот же спектакль, концерт, тур, выставка — даже под другим названием и
  из другого канала, с пересекающимися датами); иначе пустая строка.
- post_index — номер поста, из которого взято событие.
- interest_reason, personal_reason — коротко, почему такие оценки.

Категории: {", ".join(CATEGORIES)}.
Если в постах нет подходящих событий, верни пустой список events.

{SCORING_RULES}"""

RESCORE_RULES = f"""Ты редактор культурной афиши для русскоязычных жителей Израиля.
Тебе приходит список уже собранных событий. Для каждого поставь оценки по
правилам ниже и найди дубли.

- duplicate_of — если событие — дубль другого события из этого же списка или из
  списка известных событий (тот же спектакль, концерт, тур, выставка, даты
  пересекаются), укажи id того события, которое выглядит полнее; иначе пустая
  строка. Не помечай дублем оба события пары друг на друга.

{SCORING_RULES}"""

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
        "same_as": {"type": "string"},
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
        "personal": {"type": "integer"},
        "personal_reason": {"type": "string"},
        "favorite_artist": {"type": "string"},
        "needs_check": {"type": "boolean"},
    },
    "additionalProperties": False,
}
EVENT_SCHEMA["required"] = list(EVENT_SCHEMA["properties"])

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {"events": {"type": "array", "items": EVENT_SCHEMA}},
    "required": ["events"],
    "additionalProperties": False,
}

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "interest": {"type": "integer"},
        "interest_reason": {"type": "string"},
        "personal": {"type": "integer"},
        "personal_reason": {"type": "string"},
        "favorite_artist": {"type": "string"},
        "duplicate_of": {"type": "string"},
    },
    "additionalProperties": False,
}
SCORE_SCHEMA["required"] = list(SCORE_SCHEMA["properties"])

RESCORE_SCHEMA = {
    "type": "object",
    "properties": {"scores": {"type": "array", "items": SCORE_SCHEMA}},
    "required": ["scores"],
    "additionalProperties": False,
}


def profile_text(profile: dict, feedback: dict) -> str:
    """Профиль владельца и его прошлые оценки — для личной оценки."""
    lines = ["## Профиль владельца афиши", "Любимые артисты:"]
    lines += [f"- {a}" for a in profile.get("favorite_artists", [])]
    lines += ["Предпочтения:", profile.get("taste", "").strip()]
    likes = feedback.get("likes", [])[-40:]
    dislikes = feedback.get("dislikes", [])[-40:]
    if likes:
        lines += ["Владелец отметил как интересные (👍):"] + [f"- {x['title']}" for x in likes]
    if dislikes:
        lines += ["Владелец отметил как неинтересные (👎):"] + [f"- {x['title']}" for x in dislikes]
    return "\n".join(lines)


def known_events_text(events: list[dict]) -> str:
    """Короткий список уже известных событий — для распознавания дублей."""
    rows = ["## Известные события (id | название | первая дата | города)"]
    for e in events:
        cities = ", ".join(dict.fromkeys(s["city"] for s in e["sessions"]))
        rows.append(f"{e['id']} | {e['title']} | {e['sessions'][0]['date']} | {cities}")
    if len(rows) == 1:
        rows.append("(пока нет)")
    return "\n".join(rows)


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
    def __init__(self, model: str, effort: str, profile: dict, feedback: dict, known: list[dict]):
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        # Профиль и список известных событий неизменны в течение запуска —
        # кладём их в system и кешируем, чтобы не платить за них в каждом запросе.
        self.context = profile_text(profile, feedback) + "\n\n" + known_events_text(known)

    def _call(self, rules: str, schema: dict, content: list[dict]) -> dict | None:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=32000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=[
                {"type": "text", "text": rules},
                {"type": "text", "text": self.context, "cache_control": {"type": "ephemeral"}},
            ],
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": content}],
        )
        if response.stop_reason == "refusal":
            log.warning("Модель отказалась обрабатывать пачку: %s", response.stop_details)
            return None
        if response.stop_reason == "max_tokens":
            log.warning("Ответ обрезан по max_tokens, пачка пропущена")
            return None
        u = response.usage
        log.info(
            "  токены: вход %d (из кеша %d), выход %d",
            u.input_tokens, u.cache_read_input_tokens or 0, u.output_tokens,
        )
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)

    def extract(self, posts: list[Post], today: date) -> list[dict]:
        """События из пачки постов; к каждому добавляется ссылка на исходный пост."""
        content: list[dict] = [{"type": "text", "text": f"Сегодня {today.isoformat()}. Посты:"}]
        for i, post in enumerate(posts):
            content.extend(_post_blocks(i, post))
        data = self._call(EXTRACT_RULES, EXTRACT_SCHEMA, content)
        if not data:
            return []
        result = []
        for ev in data["events"]:
            idx = ev.pop("post_index")
            if not 0 <= idx < len(posts):
                continue
            post = posts[idx]
            ev["sources"] = [{"channel": post.channel_title, "url": post.url, "posted_at": post.posted_at}]
            result.append(ev)
        log.info("Постов: %d → событий: %d", len(posts), len(result))
        return result

    def rescore(self, events: list[dict], today: date) -> list[dict]:
        """Новые оценки и дубли для уже собранных событий."""
        cards = [
            {
                "id": e["id"],
                "title": e["title"],
                "summary": e["summary"],
                "category": e["category"],
                "sessions": [f"{s['date']} {s.get('time', '')} {s['city']}, {s.get('venue', '')}".strip() for s in e["sessions"][:6]],
                "price": e.get("price_text", ""),
            }
            for e in events
        ]
        content = [{"type": "text", "text": f"Сегодня {today.isoformat()}. События:\n" + json.dumps(cards, ensure_ascii=False, indent=0)}]
        data = self._call(RESCORE_RULES, RESCORE_SCHEMA, content)
        return data["scores"] if data else []
