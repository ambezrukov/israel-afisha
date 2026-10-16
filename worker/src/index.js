// Сервер оценок афиши (Cloudflare Worker).
//
// Принимает 👍/👎 только от владельца (OWNER_ID): из мини-аппа — с проверкой
// подписи Telegram (initData), из сообщений бота — по id отправителя.
// Оценки: up — интересно, down — не моё (обе учат модель), hide — просто скрыть (не учит).
// Утренний сбор забирает накопленные оценки через GET /api/feedback.
//
// Секреты (wrangler secret put): BOT_TOKEN, READ_KEY, WEBHOOK_SECRET.
// Переменные (wrangler.toml): OWNER_ID, SITE_URL, ALLOWED_ORIGIN.

const VOTES_KEY = "votes";
const INIT_DATA_MAX_AGE = 30 * 24 * 3600; // сек.; мини-апп может быть открыт долго

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return cors(env, new Response(null, { status: 204 }));
    try {
      if (url.pathname === "/api/me" && request.method === "POST") return cors(env, await apiMe(request, env));
      if (url.pathname === "/api/vote" && request.method === "POST") return cors(env, await apiVote(request, env));
      if (url.pathname === "/api/feedback" && request.method === "GET") return await apiFeedback(request, env);
      if (url.pathname === "/tg" && request.method === "POST") return await telegramWebhook(request, env);
      if (url.pathname === "/setup" && request.method === "GET") return await setup(request, env, url);
      return new Response("Not found", { status: 404 });
    } catch (err) {
      return cors(env, json({ error: String(err) }, 500));
    }
  },
};

// ---------- Ответы ----------
function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json; charset=utf-8" } });
}
function cors(env, response) {
  response.headers.set("access-control-allow-origin", env.ALLOWED_ORIGIN);
  response.headers.set("access-control-allow-methods", "GET, POST, OPTIONS");
  response.headers.set("access-control-allow-headers", "content-type");
  response.headers.set("vary", "origin");
  return response;
}
function bearerOk(request, env) {
  return request.headers.get("authorization") === `Bearer ${env.READ_KEY}`;
}

// ---------- Проверка подписи мини-аппа ----------
async function hmac(key, data) {
  const k = await crypto.subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", k, data));
}
const enc = (s) => new TextEncoder().encode(s);
const hex = (bytes) => [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");

// Возвращает пользователя из initData, если подпись верна и данные свежие; иначе null.
async function verifyInitData(initData, botToken) {
  if (!initData) return null;
  const params = new URLSearchParams(initData);
  const hash = params.get("hash");
  if (!hash) return null;
  params.delete("hash");
  const dataCheck = [...params.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${k}=${v}`).join("\n");
  const secret = await hmac(enc("WebAppData"), enc(botToken));
  if (hex(await hmac(secret, enc(dataCheck))) !== hash) return null;
  const authDate = Number(params.get("auth_date") || 0);
  if (Date.now() / 1000 - authDate > INIT_DATA_MAX_AGE) return null;
  try { return JSON.parse(params.get("user") || "null"); } catch { return null; }
}
async function ownerFrom(request, env) {
  const body = await request.json().catch(() => ({}));
  const user = await verifyInitData(body.initData, env.BOT_TOKEN);
  const isOwner = !!user && String(user.id) === String(env.OWNER_ID);
  return { body, isOwner };
}

// ---------- Хранилище оценок ----------
async function loadVotes(env) {
  return (await env.FEEDBACK.get(VOTES_KEY, "json")) || {};
}
async function saveVote(env, id, vote, title) {
  const votes = await loadVotes(env);
  if (vote === "up" || vote === "down" || vote === "hide") votes[id] = { vote, title: title || votes[id]?.title || "", ts: new Date().toISOString() };
  else delete votes[id];
  await env.FEEDBACK.put(VOTES_KEY, JSON.stringify(votes));
  return votes;
}

// ---------- API для страницы ----------
async function apiMe(request, env) {
  const { isOwner } = await ownerFrom(request, env);
  if (!isOwner) return json({ owner: false });
  const votes = await loadVotes(env);
  return json({ owner: true, votes: Object.fromEntries(Object.entries(votes).map(([id, v]) => [id, v.vote])) });
}
async function apiVote(request, env) {
  const { body, isOwner } = await ownerFrom(request, env);
  if (!isOwner) return json({ error: "forbidden" }, 403);
  if (!/^[0-9a-f]{6,40}$/.test(body.id || "")) return json({ error: "bad id" }, 400);
  await saveVote(env, body.id, body.vote, String(body.title || "").slice(0, 200));
  return json({ ok: true });
}

// ---------- API для утреннего сбора ----------
async function apiFeedback(request, env) {
  if (!bearerOk(request, env)) return json({ error: "forbidden" }, 403);
  const votes = await loadVotes(env);
  const list = (kind) =>
    Object.entries(votes).filter(([, v]) => v.vote === kind)
      .sort((a, b) => a[1].ts.localeCompare(b[1].ts))
      .map(([id, v]) => ({ id, title: v.title, ts: v.ts }));
  // hidden — только чтобы не присылать скрытое в утреннем сообщении; в обучение не идёт.
  return json({ likes: list("up"), dislikes: list("down"), hidden: list("hide") });
}

// ---------- Бот ----------
async function tg(env, method, payload) {
  const r = await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/${method}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload),
  });
  return r.json();
}

async function eventTitle(env, id) {
  try {
    const data = await (await fetch(`${env.SITE_URL}data/events.json`, { cf: { cacheTtl: 300 } })).json();
    return (data.events || []).find((e) => e.id === id)?.title || "";
  } catch { return ""; }
}

async function telegramWebhook(request, env) {
  if (request.headers.get("x-telegram-bot-api-secret-token") !== env.WEBHOOK_SECRET) return new Response("forbidden", { status: 403 });
  const update = await request.json();

  // Кнопки под сообщениями бота: callback_data = "v|up|<id>", "v|hide|<id>" или "v|down|<id>".
  const cb = update.callback_query;
  if (cb) {
    const [kind, vote, id] = String(cb.data || "").split("|");
    if (kind !== "v" || String(cb.from.id) !== String(env.OWNER_ID)) {
      await tg(env, "answerCallbackQuery", { callback_query_id: cb.id, text: "Оценки принимаются только от владельца афиши" });
    } else {
      await saveVote(env, id, vote, await eventTitle(env, id));
      const answers = { up: "👍 Учтено", hide: "✕ Скрыто из вашего топа", down: "👎 Учтено — такое показывать реже" };
      await tg(env, "answerCallbackQuery", { callback_query_id: cb.id, text: answers[vote] || "Учтено" });
    }
    return new Response("ok");
  }

  // Любое сообщение боту — ответ с кнопкой мини-аппа.
  const msg = update.message;
  if (msg?.chat?.id) {
    await tg(env, "sendMessage", {
      chat_id: msg.chat.id,
      text: "Афиша культурных событий Израиля — концерты, театр, лекции, выставки и экскурсии. Обновляется каждое утро.",
      reply_markup: { inline_keyboard: [[{ text: "Открыть афишу", web_app: { url: env.SITE_URL } }]] },
    });
  }
  return new Response("ok");
}

// Однократная настройка бота: вебхук, кнопка меню и описание.
async function setup(request, env, url) {
  if (url.searchParams.get("key") !== env.READ_KEY) return json({ error: "forbidden" }, 403);
  const results = {};
  results.webhook = await tg(env, "setWebhook", {
    url: `${url.origin}/tg`, secret_token: env.WEBHOOK_SECRET, allowed_updates: ["message", "callback_query"],
  });
  results.menu = await tg(env, "setChatMenuButton", { menu_button: { type: "web_app", text: "Афиша", web_app: { url: env.SITE_URL } } });
  results.description = await tg(env, "setMyShortDescription", { short_description: "Концерты, театр, лекции и выставки в Израиле — сводка из Telegram-каналов каждое утро." });
  return json(results);
}
