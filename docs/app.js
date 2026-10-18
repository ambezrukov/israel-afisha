(() => {
  "use strict";

  const TZ = "Asia/Jerusalem";
  const STORE_KEY = "afisha-filters-v1";
  const VOTES_KEY = "afisha-votes-v1";
  const API = "https://israel-afisha.ambezrukov.workers.dev";

  const CAT_ICON = {
    "концерт": "🎵", "театр": "🎭", "стендап": "🎤", "лекция": "🗣", "выставка": "🖼",
    "фестиваль": "🎪", "экскурсия": "🥾", "кино": "🎬", "детям": "🧸", "литература": "📚",
    "вечеринка": "🪩", "мастер-класс": "✂️", "игра": "🧩", "другое": "✨",
  };
  const LANG_LABEL = { ru: "на русском", he: "на иврите", en: "на английском", mixed: "несколько языков", none: "", unknown: "" };
  const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  const WEEKDAYS = ["воскресенье", "понедельник", "вторник", "среда", "четверг", "пятница", "суббота"];

  // ---------- Telegram Mini App ----------
  const tgApp = window.Telegram && window.Telegram.WebApp;
  if (tgApp && tgApp.initData !== undefined && tgApp.platform !== "unknown") {
    tgApp.ready();
    tgApp.expand();
    document.documentElement.dataset.theme = tgApp.colorScheme === "dark" ? "dark" : "light";
  }
  function openLink(url) {
    if (tgApp && tgApp.platform !== "unknown") {
      if (/^https:\/\/t\.me\//.test(url)) tgApp.openTelegramLink(url);
      else tgApp.openLink(url);
      return true;
    }
    return false;
  }
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[href^='http']");
    if (a && openLink(a.href)) e.preventDefault();
  });

  // ---------- Даты ----------
  function isoToday() {
    return new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  }
  function addDays(iso, n) {
    const d = new Date(iso + "T12:00:00Z");
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  }
  function dow(iso) { return new Date(iso + "T12:00:00Z").getUTCDay(); }
  function human(iso, withDow = true) {
    const d = new Date(iso + "T12:00:00Z");
    const s = `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
    return withDow ? `${s}, ${WEEKDAYS[d.getUTCDay()]}` : s;
  }
  function dayLabel(iso, today) {
    if (iso === today) return "Сегодня · " + human(iso);
    if (iso === addDays(today, 1)) return "Завтра · " + human(iso);
    return human(iso);
  }

  // ---------- Утилиты ----------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "");
  const $ = (id) => document.getElementById(id);

  function loadPrefs() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch { return {}; }
  }
  function savePrefs(p) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(p)); } catch { /* приватный режим */ }
  }

  // ---------- Состояние ----------
  let EVENTS = [];
  let GENERATED = "";
  let PROFILE = {};       // правила географии владельца из events.json
  let OWNER = false;      // страница открыта владельцем в Telegram (подтверждено сервером)
  let VOTES = {};         // id события → "up" | "down"
  const today = isoToday();
  const prefs = Object.assign(
    { q: "", place: "all", when: "all", dow: "all", lang: "all", price: "all", cats: [], kids: false, oneoff: false, fresh: false,
      topMode: "all", sort: "score", myRegion: "all", myCats: [], myLang: "all", myKids: false },
    loadPrefs()
  );
  try { VOTES = JSON.parse(localStorage.getItem(VOTES_KEY)) || {}; } catch { VOTES = {}; }

  // Сеанс «идёт» в день d, если d в [date, end_date].
  const sessEnd = (s) => s.end_date || s.date;
  const isPeriod = (s) => !!s.end_date && s.end_date !== s.date;

  function placeMatch(s) {
    if (prefs.place === "all") return true;
    const [kind, val] = prefs.place.split(":");
    return kind === "r" ? s.region === val : s.city === val;
  }
  function dateRange() {
    switch (prefs.when) {
      case "today": return [today, today];
      case "tomorrow": { const t = addDays(today, 1); return [t, t]; }
      case "week": return [today, addDays(today, 6)];
      case "month": return [today, addDays(today, 29)];
      case "weekend": {
        // Израильские выходные: пятница и суббота.
        let d = today;
        while (dow(d) !== 5 && dow(d) !== 6) d = addDays(d, 1);
        const fri = dow(d) === 6 ? addDays(d, -1) : d;
        return [fri < today ? today : fri, addDays(fri, 1)];
      }
      default: return [today, "9999-12-31"];
    }
  }
  function dowMatch(dayIso) {
    const w = dow(dayIso);
    if (prefs.dow === "fri") return w === 5;
    if (prefs.dow === "sat") return w === 6;
    if (prefs.dow === "weekday") return w >= 0 && w <= 4;
    return true;
  }
  // Первый день внутри [from, to], когда сеанс идёт и подходит по дню недели.
  function firstDay(s, from, to) {
    let d = s.date > from ? s.date : from;
    const end = sessEnd(s) < to ? sessEnd(s) : to;
    for (let i = 0; d <= end && i < 400; i++, d = addDays(d, 1)) {
      if (dowMatch(d)) return d;
    }
    return null;
  }

  // Правила профиля «не показывать вовсе» (например, стендап на иврите).
  function excluded(e) {
    return (PROFILE.exclude || []).some((r) => (!r.category || e.category === r.category) && (!r.language || e.language === r.language));
  }

  function eventMatches(e) {
    if (excluded(e)) return false;
    if (prefs.cats.length && !prefs.cats.includes(e.category)) return false;
    if (prefs.lang !== "all") {
      if (prefs.lang === "none" ? e.language !== "none" : !(e.language === prefs.lang || e.language === "mixed")) return false;
    }
    if (prefs.price === "free" && e.price_min !== 0) return false;
    if ((prefs.price === "100" || prefs.price === "200") && !(e.price_min >= 0 && e.price_min <= +prefs.price)) return false;
    if (prefs.kids && e.kids !== "yes" && e.category !== "детям") return false;
    if (prefs.fresh && !isFresh(e)) return false;
    if (prefs.q) {
      const hay = [e.title, e.summary, e.category, ...e.sessions.map((s) => `${s.city} ${s.venue}`)].join(" ").toLowerCase();
      if (!prefs.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
    }
    return true;
  }
  function isFresh(e) {
    const gen = (GENERATED || today).slice(0, 10);
    return e.first_seen >= addDays(gen, -1);
  }

  // Подходящие сеансы события с датой показа.
  function matchedSessions(e, from, to) {
    const out = [];
    for (const s of e.sessions) {
      if (!placeMatch(s)) continue;
      if (prefs.oneoff && isPeriod(s)) continue;
      const day = firstDay(s, from, to);
      if (day) out.push({ s, day });
    }
    return out.sort((a, b) => (a.day + (a.s.time || "")).localeCompare(b.day + (b.s.time || "")));
  }

  // ---------- Рендер ----------
  function whenText(s, day) {
    if (isPeriod(s)) return `до ${human(s.end_date, false)}`;
    return human(day) + (s.time ? `, ${s.time}` : "");
  }
  function priceText(e) {
    if (e.price_min === 0) return "бесплатно";
    if (e.price_text) return e.price_text;
    if (e.price_min > 0) return `от ${e.price_min} ₪`;
    return "";
  }
  function gcalUrl(e, s, day) {
    const d = day.replace(/-/g, "");
    let dates;
    if (s.time && !isPeriod(s)) {
      const [h, m] = s.time.split(":").map(Number);
      const end = `${String(Math.min(h + 2, 23)).padStart(2, "0")}${String(m).padStart(2, "0")}00`;
      dates = `${d}T${s.time.replace(":", "")}00/${d}T${end}`;
    } else {
      dates = `${d}/${addDays(day, 1).replace(/-/g, "")}`;
    }
    const src = e.sources[0] ? e.sources[0].url : "";
    const p = new URLSearchParams({
      action: "TEMPLATE", text: e.title, dates, ctz: TZ,
      details: `${e.summary}\n\n${safeUrl(e.ticket_url) || src}`,
      location: [s.venue, s.city].filter(Boolean).join(", "),
    });
    return "https://calendar.google.com/calendar/render?" + p.toString();
  }

  function card(e, m, extra, fav) {
    const { s, day } = m;
    const tags = [
      `<span class="tag cat">${CAT_ICON[e.category] || ""} ${esc(e.category)}</span>`,
      LANG_LABEL[e.language] ? `<span class="tag">${LANG_LABEL[e.language]}</span>` : "",
      priceText(e) ? `<span class="tag ${e.price_min === 0 ? "free" : ""}">${esc(priceText(e))}</span>` : "",
      e.kids === "yes" ? `<span class="tag">детям</span>` : "",
      isFresh(e) ? `<span class="tag new">новое</span>` : "",
      e.needs_check ? `<span class="tag check" title="Данные распознаны с картинки или неоднозначны">проверить по ссылке</span>` : "",
    ].join("");
    const ticket = safeUrl(e.ticket_url);
    const sources = e.sources.map((x, i) =>
      `<a class="btn" href="${esc(safeUrl(x.url))}" target="_blank" rel="noopener">${
        /^https:\/\/t\.me\//.test(x.url) ? (e.sources.length > 1 ? `Пост ${i + 1}` : "Пост в Telegram") : esc(x.channel)
      }</a>`
    ).join("");
    const others = extra.length
      ? `<div class="more">Ещё: ${extra.slice(0, 6).map((o) => esc(`${human(o.day, false)}${o.s.time ? " " + o.s.time : ""} — ${o.s.city}`)).join(" · ")}${extra.length > 6 ? " …" : ""}</div>`
      : "";
    return `<article class="card" id="ev-${esc(e.id)}">
      ${fav ? `<span class="fav">🔔 ${esc(e.favorite_artist)}</span>` : ""}
      <h3>${esc(e.title)}</h3>
      <div class="where">${esc(whenText(s, day))} · ${esc([s.city, s.venue].filter(Boolean).join(", "))}</div>
      <p class="summary">${esc(e.summary)}</p>
      <div class="tags">${tags}</div>
      <div class="actions">
        ${ticket ? `<a class="btn primary" href="${esc(ticket)}" target="_blank" rel="noopener">Билеты</a>` : ""}
        <a class="btn" href="${esc(gcalUrl(e, s, day))}" target="_blank" rel="noopener">В календарь</a>
        ${sources}
        ${voteButtons(e)}
      </div>
      ${others}
    </article>`;
  }

  // ---------- Оценки 👍/👎 ----------
  function voteButtons(e) {
    const v = VOTES[e.id];
    return `<span class="votes" data-vote-id="${esc(e.id)}">
      <button type="button" class="vote" data-vote="up" aria-pressed="${v === "up"}" title="Показывать такое чаще">👍 Интересно</button>
      <button type="button" class="vote" data-vote="hide" aria-pressed="${v === "hide"}" title="Убрать это событие, вкус не менять: не подходит дата, место, настроение">🙈 Не сейчас</button>
      <button type="button" class="vote" data-vote="down" aria-pressed="${v === "down"}" title="Убрать и показывать такое реже">👎 Не моё</button></span>`;
  }
  function vote(id, v) {
    const next = VOTES[id] === v ? "none" : v;
    if (next === "none") delete VOTES[id]; else VOTES[id] = next;
    if (OWNER) {
      // Оценки владельца уходят на сервер и учат модель; ошибка сети не мешает работе страницы.
      const e = EVENTS.find((x) => x.id === id);
      fetch(API + "/api/vote", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ initData: tgApp.initData, id, vote: next, title: e ? e.title : "" }),
      }).catch(() => {});
    } else {
      try { localStorage.setItem(VOTES_KEY, JSON.stringify(VOTES)); } catch { /* приватный режим */ }
    }
    render();
  }

  function filtersActive() {
    return !!(prefs.q || prefs.place !== "all" || prefs.when !== "all" || prefs.dow !== "all" || prefs.lang !== "all"
      || prefs.price !== "all" || prefs.cats.length || prefs.kids || prefs.oneoff || prefs.fresh);
  }

  // ---------- Рейтинг ----------
  function myScore(e, m) {
    let score, home;
    if (OWNER) {
      score = e.personal ?? e.interest;
      home = PROFILE.home_region || "all";
    } else {
      score = e.interest
        + (prefs.myCats.includes(e.category) ? 2 : 0)
        + (prefs.myLang !== "all" && (e.language === prefs.myLang || e.language === "none") ? 1 : 0)
        + (prefs.myKids && (e.kids === "yes" || e.category === "детям") ? 1 : 0);
      home = prefs.myRegion;
    }
    if (home !== "all" && m.s.region !== home) {
      score -= e.interest >= (PROFILE.major_interest || 8) ? (PROFILE.far_penalty_if_major ?? 1) : (PROFILE.far_penalty ?? 3);
    }
    if (VOTES[e.id] === "up") score += 50;
    if (OWNER && e.favorite_artist) score += 100;
    return score;
  }
  // Поправка на близость: ближайшие две недели чуть выше, далёкие месяцы — чуть ниже.
  function proximity(day) {
    const days = Math.round((new Date(day + "T12:00:00Z") - new Date(today + "T12:00:00Z")) / 864e5);
    if (days <= 14) return 1;
    if (days <= 45) return 0;
    if (days <= 120) return -1;
    return -2;
  }

  function renderList() {
    const [from, to] = dateRange();
    const mine = prefs.topMode === "mine";
    const byDate = prefs.sort === "date";
    const home = mine ? (OWNER ? PROFILE.home_region : prefs.myRegion) : null;
    const rows = [];
    for (const e of EVENTS) {
      if (mine && (VOTES[e.id] === "down" || VOTES[e.id] === "hide")) continue;
      if (!eventMatches(e)) continue;
      const ms = matchedSessions(e, from, to);
      if (!ms.length) continue;
      const m = (!byDate && home && home !== "all" && ms.find((x) => x.s.region === home)) || ms[0];
      const score = (mine ? myScore(e, m) : e.interest) + proximity(m.day);
      rows.push({ e, m, extra: ms.filter((x) => x !== m), score });
    }
    if (byDate) {
      rows.sort((a, b) => {
        // Выставки и прочие «длящиеся» события — в конце своего дня.
        const pa = isPeriod(a.m.s) ? 1 : 0, pb = isPeriod(b.m.s) ? 1 : 0;
        return a.m.day.localeCompare(b.m.day) || pa - pb || (a.m.s.time || "99").localeCompare(b.m.s.time || "99") || b.score - a.score;
      });
    } else {
      rows.sort((a, b) => b.score - a.score || a.m.day.localeCompare(b.m.day));
    }

    const counts = (v) => Object.values(VOTES).filter((x) => x === v).length;
    $("top-hint").textContent = (mine
      ? (OWNER ? `Мой рейтинг: по вашему профилю и оценкам (👍 ${counts("up")} · 🙈 ${counts("hide")} · 👎 ${counts("down")})` : "Мой рейтинг: по вашим настройкам")
      : "Общий рейтинг: по значимости события")
      + (byDate ? " · по дате" : " · ближайшие события чуть выше далёких")
      + (filtersActive() ? " · с учётом фильтров" : "");
    $("my-settings").hidden = !(mine && !OWNER);
    $("count").textContent = `${rows.length} из ${EVENTS.length}`;
    if (!rows.length) {
      $("list").innerHTML = `<div class="empty">Ничего не нашлось — попробуйте ослабить фильтры.</div>`;
      return;
    }
    let html = "", cur = "";
    for (const r of rows) {
      if (byDate && r.m.day !== cur) { cur = r.m.day; html += `<div class="day">${esc(dayLabel(cur, today))}</div>`; }
      html += card(r.e, r.m, r.extra, mine && OWNER && r.e.favorite_artist);
    }
    $("list").innerHTML = html;
  }

  function render() {
    savePrefs(prefs);
    renderList();
  }

  // ---------- Фильтры ----------
  function buildPlaceOptions() {
    const regions = new Map();
    for (const e of EVENTS) for (const s of e.sessions) {
      if (sessEnd(s) < today) continue;
      if (!regions.has(s.region)) regions.set(s.region, new Map());
      const cities = regions.get(s.region);
      if (!cities.has(s.city)) cities.set(s.city, new Set());
      cities.get(s.city).add(e.id);
    }
    const order = ["Север", "Центр", "Иерусалим", "Юг", "Онлайн", "Другое", "Неизвестно"];
    let html = `<option value="all">Вся страна</option>`;
    for (const r of order) {
      if (!regions.has(r)) continue;
      html += `<optgroup label="${esc(r)}"><option value="r:${esc(r)}">${esc(r)} — весь регион</option>`;
      const cities = [...regions.get(r).entries()].map(([c, ids]) => [c, ids.size]).sort((a, b) => b[1] - a[1]);
      if (cities.length > 1 || cities[0][0] !== r) {
        for (const [c, n] of cities) html += `<option value="c:${esc(c)}">${esc(c)} (${n})</option>`;
      }
      html += `</optgroup>`;
    }
    $("place").innerHTML = html;
    if (![...$("place").options].some((o) => o.value === prefs.place)) prefs.place = "all";
    $("place").value = prefs.place;
  }
  function buildCats() {
    const present = new Set(EVENTS.map((e) => e.category));
    const cats = Object.keys(CAT_ICON).filter((c) => present.has(c));
    prefs.cats = prefs.cats.filter((c) => present.has(c));
    $("cats").innerHTML = cats.map((c) =>
      `<button type="button" class="chip" data-cat="${esc(c)}" aria-pressed="${prefs.cats.includes(c)}">${CAT_ICON[c]} ${esc(c)}</button>`
    ).join("");
  }
  function buildMySettings() {
    const regions = ["Север", "Центр", "Иерусалим", "Юг"];
    $("my-region").innerHTML = `<option value="all">Вся страна</option>` + regions.map((r) => `<option value="${r}">${r}</option>`).join("");
    $("my-region").value = prefs.myRegion;
    $("my-lang").value = prefs.myLang;
    $("my-kids").checked = prefs.myKids;
    $("my-cats").innerHTML = Object.keys(CAT_ICON).filter((c) => c !== "другое").map((c) =>
      `<button type="button" class="chip" data-cat="${esc(c)}" aria-pressed="${prefs.myCats.includes(c)}">${CAT_ICON[c]} ${esc(c)}</button>`
    ).join("");
  }
  function syncSeg() {
    for (const x of document.querySelectorAll(".seg button[data-mode]")) x.setAttribute("aria-pressed", x.dataset.mode === prefs.topMode);
    for (const x of document.querySelectorAll(".seg button[data-sort]")) x.setAttribute("aria-pressed", x.dataset.sort === prefs.sort);
  }
  function bindTop() {
    syncSeg();
    for (const b of document.querySelectorAll(".seg button[data-mode]")) {
      b.addEventListener("click", () => { prefs.topMode = b.dataset.mode; syncSeg(); render(); });
    }
    for (const b of document.querySelectorAll(".seg button[data-sort]")) {
      b.addEventListener("click", () => { prefs.sort = b.dataset.sort; syncSeg(); render(); });
    }
    $("my-region").addEventListener("change", () => { prefs.myRegion = $("my-region").value; render(); });
    $("my-lang").addEventListener("change", () => { prefs.myLang = $("my-lang").value; render(); });
    $("my-kids").addEventListener("change", () => { prefs.myKids = $("my-kids").checked; render(); });
    $("my-cats").addEventListener("click", (e) => {
      const b = e.target.closest(".chip");
      if (!b) return;
      const c = b.dataset.cat;
      prefs.myCats = prefs.myCats.includes(c) ? prefs.myCats.filter((x) => x !== c) : [...prefs.myCats, c];
      b.setAttribute("aria-pressed", prefs.myCats.includes(c));
      render();
    });
    // Кнопки оценок есть и в топе, и в списке; перехватываем раньше перехода к карточке.
    document.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-vote]");
      if (!b) return;
      e.stopPropagation();
      vote(b.closest("[data-vote-id]").dataset.voteId, b.dataset.vote);
    }, true);
  }
  function bind() {
    for (const id of ["place", "when", "dow", "lang", "price"]) {
      $(id).value = prefs[id];
      $(id).addEventListener("change", () => { prefs[id] = $(id).value; render(); });
    }
    for (const id of ["kids", "oneoff", "fresh"]) {
      $(id).checked = prefs[id];
      $(id).addEventListener("change", () => { prefs[id] = $(id).checked; render(); });
    }
    $("q").value = prefs.q;
    let t;
    $("q").addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { prefs.q = $("q").value.trim(); render(); }, 150); });
    $("cats").addEventListener("click", (e) => {
      const b = e.target.closest(".chip");
      if (!b) return;
      const c = b.dataset.cat;
      prefs.cats = prefs.cats.includes(c) ? prefs.cats.filter((x) => x !== c) : [...prefs.cats, c];
      b.setAttribute("aria-pressed", prefs.cats.includes(c));
      render();
    });
    $("reset").addEventListener("click", () => {
      Object.assign(prefs, { q: "", place: "all", when: "all", dow: "all", lang: "all", price: "all", cats: [], kids: false, oneoff: false, fresh: false });
      buildCats();
      for (const id of ["place", "when", "dow", "lang", "price"]) $(id).value = prefs[id];
      for (const id of ["kids", "oneoff", "fresh"]) $(id).checked = false;
      $("q").value = "";
      render();
    });
  }

  // ---------- Старт ----------
  // Афиша личная: данные отдаёт сервер, и только если страница открыта из Telegram владельца
  // (сервер проверяет подпись Telegram). В обычном браузере данных нет.
  function denied(text) {
    $("meta").textContent = "";
    document.querySelector("main").innerHTML =
      `<div class="empty locked"><p><b>Личная афиша.</b></p><p>${esc(text)}</p></div>`;
  }
  function start(payload) {
    const data = payload.data || {};
    OWNER = true;
    VOTES = payload.votes || {};
    if (!loadPrefs().topMode) prefs.topMode = "mine";
    GENERATED = data.generated_at || "";
    PROFILE = data.profile || {};
    EVENTS = (data.events || []).filter((e) => e.sessions && e.sessions.some((s) => sessEnd(s) >= today));
    const when = GENERATED ? new Date(GENERATED) : null;
    $("meta").textContent = `Обновлено ${when ? when.toLocaleString("ru-RU", { timeZone: TZ, day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }) : "—"} · событий впереди: ${EVENTS.length}`;
    buildPlaceOptions();
    buildCats();
    buildMySettings();
    bind();
    bindTop();
    render();
  }
  if (!tgApp || !tgApp.initData) {
    denied("Она открывается только из Telegram-бота владельца.");
  } else {
    fetch(API + "/api/data", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ initData: tgApp.initData }),
    })
      .then((r) => (r.status === 403 ? null : r.json()))
      .then((payload) => (payload ? start(payload) : denied("Доступ только у владельца.")))
      .catch(() => { $("meta").textContent = "Не удалось загрузить данные — проверьте интернет и откройте ещё раз."; });
  }
})();
