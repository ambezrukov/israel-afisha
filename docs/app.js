(() => {
  "use strict";

  const TZ = "Asia/Jerusalem";
  const TOP_SIZE = 10;
  const TOP_WINDOW_DAYS = 30;
  const STORE_KEY = "afisha-filters-v1";

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
  const today = isoToday();
  const prefs = Object.assign(
    { q: "", place: "all", when: "all", dow: "all", lang: "all", price: "all", cats: [], kids: false, oneoff: false, fresh: false },
    loadPrefs()
  );

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

  function eventMatches(e) {
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

  function card(e, m, extra) {
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
      `<a class="btn" href="${esc(safeUrl(x.url))}" target="_blank" rel="noopener">${e.sources.length > 1 ? `Пост ${i + 1}` : "Пост в Telegram"}</a>`
    ).join("");
    const others = extra.length
      ? `<div class="more">Ещё: ${extra.slice(0, 6).map((o) => esc(`${human(o.day, false)}${o.s.time ? " " + o.s.time : ""} — ${o.s.city}`)).join(" · ")}${extra.length > 6 ? " …" : ""}</div>`
      : "";
    return `<article class="card" id="ev-${esc(e.id)}">
      <h3>${esc(e.title)}</h3>
      <div class="where">${esc(whenText(s, day))} · ${esc([s.city, s.venue].filter(Boolean).join(", "))}</div>
      <p class="summary">${esc(e.summary)}</p>
      <div class="tags">${tags}</div>
      <div class="actions">
        ${ticket ? `<a class="btn primary" href="${esc(ticket)}" target="_blank" rel="noopener">Билеты</a>` : ""}
        <a class="btn" href="${esc(gcalUrl(e, s, day))}" target="_blank" rel="noopener">В календарь</a>
        ${sources}
      </div>
      ${others}
    </article>`;
  }

  function renderTop() {
    const to = addDays(today, TOP_WINDOW_DAYS - 1);
    const items = [];
    for (const e of EVENTS) {
      const ms = e.sessions.filter(placeMatch)
        .map((s) => ({ s, day: s.date > today ? s.date : today }))
        .filter((m) => sessEnd(m.s) >= today && m.day <= to)
        .sort((a, b) => a.day.localeCompare(b.day));
      if (ms.length) items.push({ e, m: ms[0] });
    }
    items.sort((a, b) => b.e.interest - a.e.interest || a.m.day.localeCompare(b.m.day));
    const top = items.slice(0, TOP_SIZE);
    $("top-hint").textContent = prefs.place === "all" ? "по всей стране" : "по выбранному месту";
    $("top").innerHTML = top.length
      ? top.map(({ e, m }) => `<li><button type="button" class="link" data-id="${esc(e.id)}">
          <div class="t">${CAT_ICON[e.category] || ""} ${esc(e.title)}</div>
          <div class="w">${esc(whenText(m.s, m.day))} · ${esc(m.s.city)}</div></button></li>`).join("")
      : `<li class="empty" style="padding-left:14px">Пока нет событий</li>`;
  }

  function renderList() {
    const [from, to] = dateRange();
    const rows = [];
    for (const e of EVENTS) {
      if (!eventMatches(e)) continue;
      const ms = matchedSessions(e, from, to);
      if (ms.length) rows.push({ e, m: ms[0], extra: ms.slice(1) });
    }
    rows.sort((a, b) => {
      // Выставки и прочие «длящиеся» события — в конце своего дня.
      const pa = isPeriod(a.m.s) ? 1 : 0, pb = isPeriod(b.m.s) ? 1 : 0;
      return a.m.day.localeCompare(b.m.day) || pa - pb || (a.m.s.time || "99").localeCompare(b.m.s.time || "99") || b.e.interest - a.e.interest;
    });
    $("count").textContent = `${rows.length} из ${EVENTS.length}`;
    if (!rows.length) {
      $("list").innerHTML = `<div class="empty">Ничего не нашлось — попробуйте ослабить фильтры.</div>`;
      return;
    }
    let html = "", cur = "";
    for (const r of rows) {
      if (r.m.day !== cur) { cur = r.m.day; html += `<div class="day">${esc(dayLabel(cur, today))}</div>`; }
      html += card(r.e, r.m, r.extra);
    }
    $("list").innerHTML = html;
  }

  function render() {
    savePrefs(prefs);
    renderTop();
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
    $("top").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-id]");
      if (!b) return;
      let el = document.getElementById("ev-" + b.dataset.id);
      if (!el) { // событие скрыто фильтрами — сбрасываем их
        $("reset").click();
        el = document.getElementById("ev-" + b.dataset.id);
      }
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "start" });
        el.classList.add("flash");
        setTimeout(() => el.classList.remove("flash"), 1600);
      }
    });
  }

  // ---------- Старт ----------
  fetch("data/events.json", { cache: "no-cache" })
    .then((r) => r.json())
    .then((data) => {
      GENERATED = data.generated_at || "";
      EVENTS = (data.events || []).filter((e) => e.sessions && e.sessions.some((s) => sessEnd(s) >= today));
      const when = GENERATED ? new Date(GENERATED) : null;
      $("meta").textContent = `Обновлено ${when ? when.toLocaleString("ru-RU", { timeZone: TZ, day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }) : "—"} · событий впереди: ${EVENTS.length}`;
      buildPlaceOptions();
      buildCats();
      bind();
      render();
    })
    .catch(() => { $("meta").textContent = "Не удалось загрузить данные"; });
})();
