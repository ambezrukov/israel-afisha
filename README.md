# Афиша Израиля

Каждое утро программа читает Telegram-каналы из папки «Афиша» служебного аккаунта,
с помощью Claude API вытаскивает из постов события (что, где, когда, цена, язык),
склеивает дубли и публикует страницу с топ-10 и фильтрами.

## Как устроено

| Что | Где |
|---|---|
| Настройки (папка, модель, сроки) | `config.toml` |
| Чтение Telegram | `afisha/tg.py` |
| Разбор постов моделью | `afisha/extract.py` |
| Хранение, склейка дублей, регионы | `afisha/store.py` |
| Ежедневный запуск | `.github/workflows/daily.yml` (20:00 по Израилю) |
| Страница | `docs/` (GitHub Pages), данные — `docs/data/events.json` |
| Позиция чтения каналов | `state/state.json` |

## Секреты GitHub (Settings → Secrets and variables → Actions)

- `TG_API_ID`, `TG_API_HASH`, `TG_SESSION` — записываются скриптом `python scripts/make_session.py`;
- `ANTHROPIC_API_KEY` — ключ из Claude Console.

## Ручной запуск

GitHub → Actions → «Ежедневный сбор афиши» → Run workflow.
Локально: `python -m afisha.main --dry-run` (не помечает посты прочитанными).
