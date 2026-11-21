// Liest auf Google Hotels (Santiburi) die Anbieterpreise aus der Seite und gibt sie an den
// Hintergrund-Dienst weiter. Komplett unsichtbar: keine Oberflaeche, keine Meldungen.
// Gesendet werden NUR Anbietername, Preis pro Nacht, Gesamtpreis und Anbieter-Link,
// zusammen mit Zeitraum und Gaestezahl. Keine Cookies, kein Konto, keine anderen Seiteninhalte.
(() => {
  const ENTRY = /\["([^"\\]{2,40})",(\d+),"(\/travel\/lodging\/clk[^"]*)"/g;
  const PAIR = /\["([\d.,]+)\s?€"\],\["([\d.,]+)\s?€"\]/;
  const SPONSORED = /data-id="j2tiVc_([^"]+)"/g;
  const SPONSORED_PRICE = />([\d.,]+\s?€)</g;
  const SPONSORED_LINK = /href="(\/aclk[^"]+)"/;
  const EVERY_MS = 20000;
  const RESEND_MS = 20 * 60 * 1000;

  let lastSignature = "";
  let lastSentAt = 0;

  const money = (s) => {
    const n = parseFloat(String(s).replace(/[^\d.,]/g, "").replace(/\./g, "").replace(",", "."));
    return Number.isFinite(n) ? n : null;
  };

  // --- ts-Parameter (Protobuf, base64url): Datum und Gaestezahl -----------------------------
  function readVarint(b, i) {
    let r = 0, shift = 0;
    for (;;) {
      if (i >= b.length) return null;
      const x = b[i++];
      r += (x & 0x7f) * Math.pow(2, shift);
      if (!(x & 0x80)) return [r, i];
      shift += 7;
    }
  }
  function fields(b) {
    const out = [];
    let i = 0;
    while (i < b.length) {
      const t = readVarint(b, i);
      if (!t) return null;
      let [tag, j] = t;
      const wt = tag & 7;
      if (wt === 0) {
        const v = readVarint(b, j);
        if (!v) return null;
        out.push([tag >> 3, 0, v[0]]);
        i = v[1];
      } else if (wt === 2) {
        const l = readVarint(b, j);
        if (!l) return null;
        const end = l[1] + l[0];
        if (end > b.length) return null;
        out.push([tag >> 3, 2, b.slice(l[1], end)]);
        i = end;
      } else {
        return null;
      }
    }
    return out;
  }
  function findDates(b, acc) {
    const f = fields(b);
    if (!f) return;
    if (f.length === 3 && f.every((x) => x[1] === 0)) {
      const [y, m, d] = f.map((x) => x[2]);
      if (y >= 2000 && y <= 2100 && m >= 1 && m <= 12 && d >= 1 && d <= 31) acc.push([y, m, d]);
    }
    for (const x of f) if (x[1] === 2) findDates(x[2], acc);
  }
  function tripFromUrl() {
    const ts = new URL(location.href).searchParams.get("ts");
    if (!ts) return null;
    let bytes;
    try {
      const s = ts.replace(/-/g, "+").replace(/_/g, "/");
      const bin = atob(s + "=".repeat((4 - (s.length % 4)) % 4));
      bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
    } catch (e) {
      return null;
    }
    const dates = [];
    findDates(bytes, dates);
    if (dates.length < 2) return null;
    let adults = 0;
    for (let i = 0; i + 3 < bytes.length; i++) {
      if (bytes[i] === 0x0a && bytes[i + 1] === 0x02 && bytes[i + 2] === 0x08 && bytes[i + 3] === 0x03) adults++;
    }
    if (adults === 0) adults = 2; // Googles Standard ohne explizite Angabe
    const iso = ([y, m, d]) => `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    return { checkin: iso(dates[0]), checkout: iso(dates[1]), adults };
  }

  // --- Anbieterzeilen aus dem Seitenquelltext ------------------------------------------------
  function providers(html) {
    const best = new Map();
    const entries = [];
    let m;
    ENTRY.lastIndex = 0;
    while ((m = ENTRY.exec(html))) entries.push({ name: m[1], end: ENTRY.lastIndex, raw: m[3] });
    entries.forEach((e, k) => {
      const next = k + 1 < entries.length ? entries[k + 1].end - 200 : e.end + 3500;
      const seg = html.slice(e.end, Math.min(e.end + 3500, Math.max(next, e.end + 200)));
      const pm = PAIR.exec(seg);
      if (!pm) return;
      const perNight = money(pm[1]), total = money(pm[2]);
      if (!perNight || !total) return;
      let raw = e.raw;
      try { raw = JSON.parse('"' + e.raw + '"'); } catch (err) { /* roh lassen */ }
      let url = "https://www.google.com" + raw;
      try { url = new URL(url).searchParams.get("pcurl") || url; } catch (err) { /* roh lassen */ }
      const old = best.get(e.name);
      if (!old || total < old.total) best.set(e.name, { name: e.name, per_night: perNight, total, url });
    });
    // gesponserte "Vorgestellte Optionen" (nur als HTML-Block, nicht im JSON)
    const blocks = [];
    SPONSORED.lastIndex = 0;
    while ((m = SPONSORED.exec(html))) blocks.push({ name: m[1], end: SPONSORED.lastIndex });
    blocks.forEach((b, k) => {
      const next = k + 1 < blocks.length ? blocks[k + 1].end : b.end + 4000;
      const seg = html.slice(b.end, Math.min(next, b.end + 4000));
      const prices = [...seg.matchAll(SPONSORED_PRICE)].map((x) => money(x[1]));
      if (prices.length < 3 || !prices[0] || !prices[2]) return;
      const link = SPONSORED_LINK.exec(seg);
      const url = link ? "https://www.google.com" + link[1].replace(/&amp;/g, "&") : "";
      const old = best.get(b.name);
      if (!old || prices[2] < old.total) best.set(b.name, { name: b.name, per_night: prices[0], total: prices[2], url });
    });
    return [...best.values()].sort((a, b) => a.total - b.total);
  }

  function tick() {
    try {
      if (document.visibilityState !== "visible") return;
      if (!/Santiburi/i.test(document.title)) return;
      const trip = tripFromUrl();
      if (!trip) return;
      const list = providers(document.documentElement.outerHTML);
      if (list.length < 3) return;
      const signature = JSON.stringify([trip, list.map((p) => [p.name, p.total])]);
      const now = Date.now();
      if (signature === lastSignature && now - lastSentAt < RESEND_MS) return;
      lastSignature = signature;
      lastSentAt = now;
      chrome.runtime.sendMessage({ type: "prices", payload: { ...trip, providers: list } });
    } catch (e) {
      /* still bleiben */
    }
  }

  setInterval(tick, EVERY_MS);
  setTimeout(tick, 6000);
})();
