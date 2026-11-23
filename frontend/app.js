"use strict";

const $ = (s) => document.querySelector(s);
const el = (t, c) => { const e = document.createElement(t); if (c) e.className = c; return e; };

let CURRENCY = "EUR";
const state = { flights: null, chart: null, chartSeries: {}, employeeDiscounts: {} };

// Nutzer-Fund 16.09.26: Mitarbeiter-Rabattportal zeigt Zusatzrabatte auf
// einzelne Anbieter (z.B. lastminute.com 7%) - manuell in config.yaml
// gepflegt (trip.employee_discounts), hier nur als Hinweistext neben dem
// jeweiligen Anbieter angezeigt, AENDERT NIE einen angezeigten Preis (der
// genaue Rabatt/Verfuegbarkeit steht erst beim Checkout fest).
// Prozentwert aus dem Rabatt-Hinweis ("bis 12% Rabatt ..."), nur reine
// Hotel-Rabatte (Flug+Hotel-Paketrabatte gelten hier nicht).
function niceName(source) {
  return (source || "").replace(/^referenz:/, "");
}

function employeeDiscountPct(provider) {
  if (!provider) return null;
  const p = provider.toLowerCase();
  const hit = Object.entries(state.employeeDiscounts).find(([name]) => {
    const n = name.toLowerCase();
    return p.includes(n) || n.includes(p);
  });
  if (!hit || /flug/i.test(hit[1])) return null;
  const m = hit[1].match(/(\d+)\s?%/);
  return m ? Number(m[1]) : null;
}

function employeeDiscountNote(provider) {
  if (!provider) return "";
  const p = provider.toLowerCase();
  const hit = Object.entries(state.employeeDiscounts).find(([name]) => {
    const n = name.toLowerCase();
    return p.includes(n) || n.includes(p);
  });
  return hit ? ` <span class="tag teal" title="${hit[1].replace(/"/g, '&quot;')}">Mitarbeiterrabatt möglich</span>` : "";
}

const CAT_LABELS = {
  hotel_total: "Hotel (günstigster Tarif)",
  separate_total: "Flug + Hotel",
  flight_total: "Flug",
  package_total: "Pauschalreise",
};
const CAT_COLORS = {
  flight_total: "#e2793d",
  hotel_total: "#6db3aa",
  separate_total: "#dba43c",
  package_total: "#d9614a",
};

function money(v) {
  if (v === null || v === undefined) return "–";
  return new Intl.NumberFormat("de-DE", { style: "currency", currency: CURRENCY, maximumFractionDigits: 0 }).format(v);
}
function money2(v) {
  if (v === null || v === undefined) return "–";
  return new Intl.NumberFormat("de-DE", { style: "currency", currency: CURRENCY }).format(v);
}
function dt(s) { return s ? new Date(s).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }) : "–"; }
function hm(mins) {
  if (!mins && mins !== 0) return "–";
  const h = Math.floor(mins / 60), m = mins % 60;
  return `${h}h${m ? " " + m + "m" : ""}`;
}
function timeTZ(iso) {
  // Nutzer-Fund 13.09.26: der Timezone-Teil (z.B. "Europe/Berlin") wurde als
  // Ortsname angezeigt ("21:50 Berlin") - IANA nennt die Zeitzone fuer ganz
  // Deutschland "Berlin", auch wenn der Flughafen Stuttgart oder Frankfurt
  // ist. Das sah wie ein falscher/erfundener Abflugort aus. Zeit jetzt ohne
  // diesen irrefuehrenden Zusatz, Flughafencode steht ohnehin daneben.
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}
function showError(msg) {
  const e = $("#error");
  e.textContent = msg;
  e.style.display = msg ? "block" : "none";
}

/* ---------------- status header ---------------- */
function renderCountdown(s) {
  const target = s.hotel_checkin || s.departure_date;
  const box = $("#countdown");
  if (!target) { box.hidden = true; return; }
  const days = Math.ceil((new Date(target + "T00:00:00") - new Date()) / 86400000);
  box.hidden = false;
  if (days > 0) {
    $("#countdownNum").textContent = days;
    $("#countdownLbl").textContent = days === 1 ? "Tag bis Thailand 🌴" : "Tage bis Thailand 🌴";
  } else if (days === 0) {
    $("#countdownNum").textContent = "🏝";
    $("#countdownLbl").textContent = "heute geht's los!";
  } else {
    box.hidden = true; // Reise ist vorbei - Countdown nicht mehr sinnvoll
  }
}

async function loadStatus() {
  const s = await api("/api/status");
  CURRENCY = s.currency || "EUR";
  state.employeeDiscounts = s.employee_discounts || {};
  $("#tripLabel").textContent = s.trip || "";
  if (s.last_run) {
    $("#lastRun").textContent = `letzter Check: ${dt(s.last_run.finished_at || s.last_run.started_at)} (#${s.last_run.id}, ${s.last_run.status})`;
  }
  const sc = s.scheduler || {};
  $("#nextRun").textContent = sc.running && sc.next_run
    ? `nächster: ${dt(sc.next_run)}`
    : "Scheduler: aus";
  renderCountdown(s);
}

/* ---------------- summary / verdict ---------------- */
async function loadSummary() {
  const s = await api("/api/summary");
  CURRENCY = s.currency || CURRENCY;
  state.summary = s;
  renderBestHotel(s.verdict || {}, s.totals || {}, s.persons);
  renderVerdictCards(s.trends || {}, s.totals || {}, s.persons, s.flight);
  renderCheapestFlight(s.flight, (s.verdict || {}).return_reference);
}

// Nutzer-Fund 14.09.26 (Roadmap Runde 2, 1.6): die Kopfzeile "8 Personen, 3
// Villen, 14 Nächte" war fest im HTML eincodiert und driftete auseinander,
// sobald die gewaehlte Zimmeraufteilung/Naechte-Zahl real etwas anderes war
// (hier: 4× Doppelzimmer statt "3 Villen", 12/13 statt 14 Naechte). Jetzt
// aus den echten Verdict-Daten gebaut.
const ROOM_LABELS = { 2: "Doppelzimmer", 3: "Dreier-/Villenzimmer" };
function versusSubline(verdict, persons) {
  const h = verdict.hotel;
  let roomTxt = "Zimmeraufteilung noch offen";
  if (h && h.room_split) {
    const entries = Object.entries(h.room_split);
    const totalRooms = entries.reduce((sum, [, count]) => sum + count, 0);
    const detail = entries.map(([size, count]) => `${count}× ${ROOM_LABELS[size] || `${size}er-Zimmer`}`).join(" + ");
    roomTxt = `${totalRooms} Zimmer (${detail})`;
  }
  const nightsTxt = verdict.nights_used != null ? `${verdict.nights_used} Nächte` : "Nächte noch offen";
  return `${persons != null ? persons : "?"} Personen, ${roomTxt}, ${nightsTxt}`;
}

function perPerson(total, persons) {
  return total != null && persons ? `≈ ${money(total / persons)} p. P.` : "";
}

function renderVerdictCards(trends, totals, persons, flight) {
  const box = $("#verdictCards");
  box.innerHTML = "";
  const t = trends.hotel_total || { state: "unbekannt", label: "keine Daten" };
  const c = el("div", "card");
  const head = el("div");
  head.appendChild(el("span", `dot ${t.state}`));
  head.appendChild(document.createTextNode(CAT_LABELS.hotel_total));
  const big = el("div", "big");
  big.textContent = money(totals.hotel_total);
  const pp = el("div", "small muted");
  pp.textContent = perPerson(totals.hotel_total, persons);
  const pill = el("span", `pill ${t.state}`);
  pill.textContent = t.label;
  const sub = el("div", "sub");
  if (t.avg) {
    const arrow = t.pct_vs_avg > 0 ? "▲" : t.pct_vs_avg < 0 ? "▼" : "•";
    sub.textContent = `Ø ${money(t.avg)}  ${arrow} ${t.pct_vs_avg ?? 0}%  (n=${t.n})`;
  } else {
    sub.textContent = "noch keine Vergleichsbasis";
  }
  c.append(head, big, pp, pill, sub);
  box.appendChild(c);

  if (flight && flight.pax_mode === "booked") {
    const f = el("div", "card");
    const fh = el("div");
    fh.appendChild(el("span", "dot guenstig"));
    fh.appendChild(document.createTextNode("Flug (Hin+Rück)"));
    const fb = el("div", "big");
    fb.textContent = money(flight.price_total);
    const fp = el("div", "small muted");
    fp.textContent = perPerson(flight.price_total, persons);
    const fs = el("div", "sub");
    fs.textContent = "gebucht";
    f.append(fh, fb, fp, fs);
    box.appendChild(f);
  }
}

// Hero: guenstigster Santiburi-Tarif fuer den gebuchten Zeitraum.
function renderBestHotel(verdict, totals, persons) {
  const box = $("#bestHotel");
  const h = verdict.hotel;
  if (!h || totals.hotel_total == null) {
    box.innerHTML = `<div class="card"><div class="sub">Noch kein Hotelpreis vorhanden.</div></div>`;
    return;
  }
  $("#bestHotelDates").textContent = `${shortDate(h.checkin)}–${shortDate(h.checkout)}${verdict.nights_used ? ` · ${verdict.nights_used} Nächte` : ""}`;
  const nights = verdict.nights_used || 0;
  const perNight = nights ? totals.hotel_total / nights : null;
  const c = el("div", "card");
  const link = h.deep_link ? `<a href="${h.deep_link}" target="_blank" rel="noopener">${niceName(h.source)} öffnen →</a>` : niceName(h.source);
  let split = "";
  if (h.room_split && h.per_room_size) {
    const parts = Object.entries(h.room_split).map(([size, count]) => {
      const info = h.per_room_size[size] || {};
      const label = `${count}× ${size}er-Zimmer`;
      return info.url ? `<a href="${info.url}" target="_blank" rel="noopener">${label}</a>` : label;
    });
    split = `<div class="seg muted">${parts.join(" + ")} (je Zimmergröße eigener Link)</div>`;
  }
  c.innerHTML = `<div class="label">${niceName(h.source)}</div>
    <div class="big">${money(totals.hotel_total)}</div>
    <div class="small muted">${perPerson(totals.hotel_total, persons)}${perNight ? ` · ${money(perNight)} pro Nacht (alle Zimmer)` : ""}${perNight && persons ? ` · <strong>${money2(perNight / persons)} pro Nacht p. P.</strong>` : ""} · inkl. Steuern/Gebühren${(h.source || "").startsWith("santiburi") ? " (geschätzt)" : ""}</div>
    <div class="sub" style="margin-top:8px">${link}</div>${split}`;
  box.innerHTML = "";
  box.appendChild(c);
  $("#verdictNotes").textContent = (verdict.notes || []).join("  —  ");
}

function renderVersus(verdict, totals, persons) {
  $("#versusSubline").textContent = versusSubline(verdict, persons);
  const sep = totals.separate_total, pkg = totals.package_total;
  $("#sepAmount").innerHTML = `${money(sep)} <span class="small muted">${perPerson(sep, persons)}</span>`;
  $("#pkgAmount").innerHTML = `${money(pkg)} <span class="small muted">${perPerson(pkg, persons)}</span>`;
  const f0 = verdict.flight;
  const isSplit0 = f0 && (f0.pax_mode || "").startsWith("split_");
  const fsrc = isSplit0 ? " (2 getrennte Tickets, Schätzung)"
    : verdict.flight_source === "round_trip" ? " (Round-Trip)"
    : verdict.flight_source === "multi_city" ? " (Multi-City)" : "";

  // Bugfix 13.09.26 (Nutzer: "die Preise werden mir garnicht gezeigt,
  // kannst du mir Links mitgeben") - vorher reiner Text ohne jeden Link.
  // Jetzt: Flug- und Hotel-Preis direkt anklickbar, Hotel zusaetzlich mit
  // der Zimmergroessen-Aufschluesselung (je Zimmergroesse ein eigener Link).
  const f = verdict.flight, h = verdict.hotel, p = verdict.package;
  let flightLink = (f && f.deep_link)
    ? `<a href="${f.deep_link}" target="_blank" rel="noopener">Flug${fsrc} ${money(totals.flight_total)}</a>`
    : `Flug${fsrc} ${money(totals.flight_total)}`;
  // Nutzer-Fund 17.09.26: "die 7000€ von lastminute werden nicht NEBEN den
  // 7300€ von Qatar direkt angezeigt" - die Buchungsoptionen standen bisher
  // nur in der Detail-Karte weiter unten, nicht in dieser Kopfzeile, wo der
  // Nutzer zuerst hinschaut. Guenstigste Alternative (falls != Airline-Preis)
  // jetzt direkt daneben.
  if (f && f.booking_options && f.booking_options.length) {
    const cheapest = f.booking_options[0];
    if (Math.round(cheapest.price) < Math.round(totals.flight_total)) {
      flightLink += ` <span class="small" style="color:var(--success)">` +
        `(günstiger bei ${cheapest.provider}: ${money(cheapest.price)}${employeeDiscountNote(cheapest.provider)})</span>`;
    }
  }
  let hotelLink = (h && h.deep_link)
    ? `<a href="${h.deep_link}" target="_blank" rel="noopener">Hotel (${h.source}) ${money(totals.hotel_total)}</a>`
    : `Hotel ${money(totals.hotel_total)}`;
  if (h && h.checkin) {
    hotelLink += ` <span class="small muted">${shortDate(h.checkin)}–${shortDate(h.checkout)}` +
      `${h.date_exact ? "" : " (umgerechnet, Schätzung)"}</span>`;
  }
  // Nutzer-Fund 14.09.26: hier wurden bisher ALLE gesuchten Zimmergroessen
  // aufgelistet (inkl. z.B. "1 Zi./1 Erw." aus der Preisermittlung), nicht
  // nur die tatsaechlich gebuchte Aufteilung - sah aus wie Fantasiezahlen,
  // die nicht zu den 8 Personen passen. Jetzt: nur raw.room_split (die
  // wirklich gewaehlte Kombination), mit Anzahl je Zimmergroesse.
  if (h && h.room_split && h.per_room_size) {
    const parts = Object.entries(h.room_split).map(([size, count]) => {
      const info = h.per_room_size[size] || {};
      const price = info.cheapest != null ? money(info.cheapest) : "?";
      const label = `${count}× (1 Zi./${size} Erw.) ${price}`;
      return info.url
        ? `<a href="${info.url}" target="_blank" rel="noopener">${label}</a>`
        : label;
    });
    if (parts.length) hotelLink += ` <span class="small muted">(${parts.join(" + ")})</span>`;
  }
  $("#sepBreak").innerHTML = `${flightLink} + ${hotelLink}`;

  const winner = verdict.winner || "unbekannt";
  $("#sideSeparate").classList.toggle("win", winner === "einzelbuchung");
  $("#sidePackage").classList.toggle("win", winner === "pauschalreise");
  let pkgInfoTxt;
  if (verdict.delta !== null && verdict.delta !== undefined) {
    const d = verdict.delta;
    pkgInfoTxt = d < 0
      ? `${money(Math.abs(d))} günstiger als Einzelbuchung`
      : `${money(d)} teurer als Einzelbuchung`;
  } else {
    pkgInfoTxt = "kein Vergleich möglich";
  }
  const pkgLink = (p && p.deep_link)
    ? ` · <a href="${p.deep_link}" target="_blank" rel="noopener">${p.operator || "Angebot"} öffnen</a>`
    : "";
  $("#pkgInfo").innerHTML = pkgInfoTxt + pkgLink;
  $("#verdictNotes").textContent = (verdict.notes || []).join("  —  ");
}

// Nutzer-Fund 15.09.26 (Roadmap Runde 2, 1.3): bei approximierten Segmenten
// (segment_times_approximate) sind Einzel-Abflug-/Ankunftszeiten und
// Umstiegsdauern gleichmaessig auf die Gesamtreisezeit VERTEILT, kein
// echtes Timing (siehe flight_cards.build_approx_segments) - sahen im
// Dashboard aber aus wie echte Zeiten ("Umstieg DOH: 0h"). Fuer solche
// Segmente nur noch Route/Flughaefen zeigen, keine erfundenen Zeiten.
function segList(segments, layoverAirports, layoverMinutes, approximate) {
  const box = document.createDocumentFragment();
  if (approximate) {
    const route = (segments || []).map((s) => s.from).concat(segments.length ? [segments[segments.length - 1].to] : []);
    const seg = el("div", "seg");
    seg.innerHTML = `<strong>${route.join(" → ")}</strong>`;
    box.appendChild(seg);
    const note = el("div", "seg muted");
    note.textContent = "Zwischenzeiten nicht bekannt (nur Gesamt-Abflug/-Ankunft/-Dauer aus Google Flights sichtbar)";
    box.appendChild(note);
    return box;
  }
  (segments || []).forEach((s, i) => {
    const seg = el("div", "seg");
    seg.innerHTML = `<strong>${s.from} → ${s.to}</strong> &nbsp; ${timeTZ(s.departure, s.departure_tz)} &rarr; ${timeTZ(s.arrival, s.arrival_tz)} &nbsp; · ${hm(s.duration_minutes)}` +
      (s.airline ? ` · ${s.airline}` : "") + (s.plane_type ? ` · ${s.plane_type}` : "");
    box.appendChild(seg);
    if (i < segments.length - 1 && layoverMinutes[i] !== undefined) {
      const lay = el("div", "seg muted");
      lay.textContent = `   ↳ Umstieg ${layoverAirports[i]}: ${hm(layoverMinutes[i])}`;
      box.appendChild(lay);
    }
  });
  return box;
}

function flightCard(f) {
  const c = el("div", "card");
  if (!f) {
    c.innerHTML = `<div class="label">Günstigste zusammenhängende Buchung</div>
      <div class="sub">Noch keine Round-Trip- oder Multi-City-Buchung gefunden. Einzelrichtungs-Tickets
      werden hier bewusst nicht angezeigt.</div>`;
    return c;
  }
  if (f.pax_mode === "booked") {
    c.innerHTML = `<div class="label">Flug gebucht ✓</div>
      <div class="big">${money(f.price_total)}</div>
      <div class="sub">${money2(f.price_per_person)} p.P. · Hinflug ${f.search_date} · zurück ${f.return_date}${(f.airlines || []).length ? " · " + f.airlines.join(", ") : ""}</div>
      <div class="seg muted">${f.price_confidence || ""}</div>`;
    return c;
  }
  const isMC = f.trip_type === "multi_city";
  const isSplit = (f.pax_mode || "").startsWith("split_");
  const h = el("div", "label");
  // Nutzer-Fund 15.09.26: eine split_4_4-Buchung (2 GETRENNTE Tickets auf
  // demselben Flug) zeigte hier "ein Ticket" - das stimmt nicht, es sind
  // zwei separate Buchungen (siehe pax_mode-Kommentar in offers.py).
  h.textContent = isSplit
    ? `${(f.pax_mode.replace("split_", "").split("_").length)} Buchungen (${f.pax_mode.replace("split_", "").replace("_", "+")}) auf demselben Flug – Schätzung`
    : isMC ? "Multi-City — ein Ticket, unterschiedliche Flughäfen" : "Hin + Rück — ein Ticket";
  const big = el("div", "big");
  big.textContent = money(f.price_total);
  const sub = el("div", "sub");
  const outN = Math.max(0, (f.segments || []).length - 1);
  const retN = Math.max(0, (f.return_segments || []).length - 1);
  sub.textContent = isMC
    ? `${money2(f.price_per_person)} p.P. · Hin ${outN} Stopp(s), Rück ${retN} Stopp(s) · ${hm(f.total_duration_minutes)} Flugzeit gesamt`
    : `${money2(f.price_per_person)} p.P. · ${f.stops} Stopp(s) · ${hm(f.total_duration_minutes)} · zurück am ${f.return_date || "?"}`;
  c.append(h, big, sub);

  // Hinflug
  const approx = !!f.segment_times_approximate;
  if (approx) {
    // Roadmap 1.4: die Layover-Mindestzeit-Pruefung (route_filter.py) wird
    // bei approximierten Segmenten bewusst uebersprungen (die Zeiten sind
    // gleichverteilte Schaetzungen, kein echtes Timing) - im Dashboard muss
    // sichtbar sein, dass dieser Sicherheitscheck hier NICHT gegriffen hat.
    const warn = el("div", "seg");
    warn.style.marginTop = "6px";
    warn.innerHTML = `<span class="tag amber" title="Umstiegs-Mindestzeit konnte fuer dieses Angebot nicht geprueft werden, da Google Flights hier keine Einzel-Umstiegszeiten zeigt">⚠ Umstiegszeiten ungeprüft</span>`;
    c.appendChild(warn);
  }
  const outLay = f.layover_airports.slice(0, outN), outMin = f.layover_minutes.slice(0, outN);
  const outHead = el("div", "seg muted"); outHead.style.marginTop = "6px";
  outHead.textContent = "Hinflug" + (f.search_date ? ` · ${f.search_date}` : "");
  c.appendChild(outHead);
  c.appendChild(segList(f.segments, outLay, outMin, approx));

  if (isMC && f.return_segments && f.return_segments.length) {
    const retLay = f.layover_airports.slice(outN), retMin = f.layover_minutes.slice(outN);
    const retHead = el("div", "seg muted"); retHead.style.marginTop = "6px";
    retHead.textContent = "Rückflug" + (f.return_date ? ` · ${f.return_date}` : "");
    c.appendChild(retHead);
    c.appendChild(segList(f.return_segments, retLay, retMin, approx));
  } else if (!isMC) {
    const note = el("div", "seg muted");
    note.textContent = "Rückflug-Routing zeigt Google erst nach Auswahl des Hinflugs; Preis ist der Gesamtpreis.";
    c.appendChild(note);
  }

  // Nutzer-Fund 16.09.26: die Ergebniskarte zeigt nur den Airline-Preis -
  // Googles Buchungsoptionen-Seite hat oft einen deutlich guenstigeren
  // Drittanbieter (Live-Fund: lastminute.com 300 EUR unter Qatar Airways
  // direkt). Nur fuer die eine guenstigste bestaetigte Kombi abgefragt.
  if (f.booking_options && f.booking_options.length) {
    const box = el("div", "seg"); box.style.marginTop = "6px";
    const label = el("div", "muted"); label.textContent = "Buchungsoptionen:";
    box.appendChild(label);
    f.booking_options.forEach((o) => {
      const line = el("div");
      line.innerHTML = `${o.is_airline ? `<strong>${o.provider}</strong> (Fluggesellschaft)` : o.provider}: ${money2(o.price)}${employeeDiscountNote(o.provider)}`;
      box.appendChild(line);
    });
    c.appendChild(box);
  }
  if (f.price_ladder && Object.keys(f.price_ladder).length) {
    const LADDER_LABELS = { "1_pax_ohne_gepaeck": "1 Pax ohne Gepäck", "1_pax_mit_gepaeck": "1 Pax mit Gepäck", "4_pax": "4 Pax", "8_pax": "8 Pax" };
    const order = ["1_pax_ohne_gepaeck", "1_pax_mit_gepaeck", "4_pax", "8_pax"];
    const parts = order.filter((k) => f.price_ladder[k] != null).map((k) => `${LADDER_LABELS[k]} ${money2(f.price_ladder[k])}`);
    if (parts.length) {
      const ladder = el("div", "seg muted"); ladder.style.marginTop = "6px";
      ladder.textContent = "Preis-Leiter p.P.: " + parts.join(" · ");
      c.appendChild(ladder);
    }
  }
  if (f.airlines && f.airlines.length) {
    const al = el("div", "seg"); al.style.marginTop = "6px";
    al.textContent = "Airlines: " + f.airlines.join(", ");
    c.appendChild(al);
  }
  if (f.carbon_grams) {
    const co = el("div", "seg muted");
    co.textContent = `CO₂ ≈ ${Math.round(f.carbon_grams / 1000)} kg p.P.`;
    c.appendChild(co);
  }
  const foot = el("div", "seg");
  const link = el("a");
  link.href = f.deep_link || "#"; link.target = "_blank"; link.rel = "noopener";
  link.textContent = "→ Suche öffnen (Google Flights Deep-Link)";
  foot.appendChild(link);
  foot.appendChild(document.createTextNode(`  · erfasst ${dt(f.captured_at)}`));
  c.appendChild(foot);
  return c;
}

// Kurzer, selbsterklaerender Hinweis statt einer eigenen Karte mit
// Fachbegriffen ("Referenzsuche" etc.) - die alte Version war fuer normale
// Nutzer nicht verstaendlich. Wird komplett weggelassen, wenn nichts
// gefunden wurde, statt eine leere/verwirrende Box zu zeigen.
function appendReturnReferenceLine(card, r) {
  if (!r) return;
  const line = el("div", "seg muted");
  line.style.marginTop = "8px";
  const price = money2(r.price_per_person);
  const link = r.deep_link
    ? ` · <a href="${r.deep_link}" target="_blank" rel="noopener">Preis prüfen</a>`
    : "";
  line.innerHTML = `Nur der Rückflug allein würde aktuell ab ${price} p.P. kosten `
    + `(separat gesucht, nur zur Einordnung — keine eigene Buchung)${link}`;
  card.appendChild(line);
}

function renderCheapestFlight(f, returnRef) {
  const box = $("#cheapestFlight");
  box.innerHTML = "";
  const card = flightCard(f);
  if (f && f.trip_type === "round_trip") {
    appendReturnReferenceLine(card, returnRef);
  }
  box.appendChild(card);
}

/* ---------------- history chart ---------------- */
const histUi = { days: 30, completeOnly: true, cats: null, data: null };

function histCats(h) {
  // Flug gebucht: nur Hotel (und optional Flug + Hotel) - Flug/Pauschal sind konstant bzw. leer.
  return h.booked_flight ? ["hotel_total", "separate_total"] : ["flight_total", "hotel_total", "separate_total", "package_total"];
}

function renderHistory() {
  const h = histUi.data;
  if (!h) return;
  const cats = histCats(h);
  if (!histUi.cats) histUi.cats = new Set(h.booked_flight ? ["hotel_total"] : cats);
  const cutoff = histUi.days ? Date.now() - histUi.days * 86400000 : 0;
  const keep = [];
  h.t.forEach((t, i) => {
    if (new Date(t).getTime() < cutoff) return;
    if (histUi.completeOnly && h.complete && h.complete[i] === false) return;
    keep.push(i);
  });
  const labels = keep.map((i) => {
    const d = new Date(h.t[i]);
    return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" }) +
      (histUi.days && histUi.days <= 7 ? " " + d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) : "");
  });
  const datasets = cats.map((cat) => ({
    label: CAT_LABELS[cat],
    data: keep.map((i) => h[cat][i]),
    borderColor: CAT_COLORS[cat],
    backgroundColor: CAT_COLORS[cat] + "22",
    spanGaps: true,
    tension: 0.25,
    pointRadius: 2,
    hidden: !histUi.cats.has(cat),
  }));
  if (state.chart) state.chart.destroy();
  state.chart = new Chart($("#chart"), {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { grid: { color: "#2b3a49" }, ticks: { color: "#93a4b3", maxTicksLimit: 12 } },
        y: { grid: { color: "#2b3a49" }, ticks: { color: "#93a4b3", callback: (v) => money(v) } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${money2(c.parsed.y)}` } },
      },
    },
  });
  const info = $("#chartInfo");
  if (info) {
    const hidden = h.complete ? h.complete.filter((x) => x === false).length : 0;
    info.textContent = `${keep.length} Messungen` +
      (histUi.completeOnly && hidden ? ` (${hidden} unvollständige Läufe ausgeblendet)` : "");
  }
}

async function loadHistory() {
  if (typeof Chart === "undefined") { console.warn("Chart.js nicht geladen – Verlauf übersprungen"); return; }
  const h = await api("/api/history");
  CURRENCY = h.currency || CURRENCY;
  state.chartSeries = h;
  histUi.data = h;
  histUi.cats = null;
  const toggles = $("#chartToggles");
  toggles.innerHTML = "";

  const range = el("span");
  range.className = "chart-range";
  for (const [days, label] of [[7, "7 Tage"], [30, "30 Tage"], [0, "Alles"]]) {
    const btn = el("button");
    btn.type = "button";
    btn.textContent = label;
    btn.className = days === histUi.days ? "active" : "";
    btn.addEventListener("click", () => {
      histUi.days = days;
      range.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === btn));
      renderHistory();
    });
    range.appendChild(btn);
  }
  toggles.appendChild(range);

  const comp = el("label");
  comp.title = "Läufe, in denen Google nur einen Teil der Anbieter lieferte, zeigen zu hohe Preise";
  comp.innerHTML = `<input type="checkbox" id="histComplete" ${histUi.completeOnly ? "checked" : ""} /> nur vollständige Läufe`;
  comp.querySelector("input").addEventListener("change", (e) => { histUi.completeOnly = e.target.checked; renderHistory(); });
  toggles.appendChild(comp);

  for (const cat of histCats(h)) {
    const lab = el("label");
    const on = h.booked_flight ? cat === "hotel_total" : true;
    lab.innerHTML = `<input type="checkbox" data-cat="${cat}" ${on ? "checked" : ""} /> <span style="color:${CAT_COLORS[cat]}">●</span> ${CAT_LABELS[cat]}`;
    lab.querySelector("input").addEventListener("change", (e) => {
      if (!histUi.cats) histUi.cats = new Set();
      e.target.checked ? histUi.cats.add(cat) : histUi.cats.delete(cat);
      renderHistory();
    });
    toggles.appendChild(lab);
  }
  const info = el("span");
  info.id = "chartInfo";
  info.style.marginLeft = "auto";
  toggles.appendChild(info);
  renderHistory();
}

/* ---------------- flights table ---------------- */
let sortDescending = false;

async function loadFlights() {
  const p = new URLSearchParams();
  const dir = $("#fDirection").value; if (dir) p.set("direction", dir);
  const org = $("#fOrigin").value; if (org) p.set("origin", org);
  const air = $("#fAirline").value.trim(); if (air) p.set("airline", air);
  const st = $("#fStops").value; if (st) p.set("max_stops", st);
  p.set("sort", $("#fSort").value);
  p.set("descending", sortDescending ? "true" : "false");
  p.set("include_excluded", $("#fExcluded").checked ? "true" : "false");
  const data = await api("/api/flights?" + p.toString());
  state.flights = data.rows;
  renderFlights(filterEstimates(data.rows));
  const sel = $("#fOrigin");
  if (sel.options.length <= 1) {
    const origins = [...new Set(data.rows.map((r) => r.origin))].sort();
    origins.forEach((o) => { const op = el("option"); op.value = o; op.textContent = o; sel.appendChild(op); });
  }
}

// Nutzer-Fund 13.09.26: "es wurde trotzdem nach einer Person gesucht" - die
// 1-Pax-x8-Hochrechnungen (pax_mode "estimated") standen unlabelt neben den
// echt fuer 8 bzw. 4+4 Personen geprueften Preisen und sahen dadurch wie der
// tatsaechliche Preis aus. Sobald fuer eine Route/Termin-Kombination ein
// echter Preis (group/split_*) vorliegt, sind die Hochrechnungen fuer diese
// Kombination reine Rohdaten-Kandidaten, keine verlaessliche Preisangabe
// mehr - werden deshalb standardmaessig ausgeblendet (Checkbox holt sie zurueck).
function filterEstimates(rows) {
  if ($("#fEstimates").checked) return rows;
  const verifiedKeys = new Set(
    rows.filter((r) => r.pax_mode === "group" || (r.pax_mode || "").startsWith("split_"))
        .map((r) => [r.trip_type, r.origin, r.destination, r.search_date, r.return_date].join("|"))
  );
  return rows.filter((r) => {
    if (r.pax_mode !== "estimated") return true;
    const key = [r.trip_type, r.origin, r.destination, r.search_date, r.return_date].join("|");
    return !verifiedKeys.has(key);
  });
}

function renderFlights(rows) {
  const tb = $("#flightsTable tbody");
  tb.innerHTML = "";
  if (!rows.length) {
    const tr = el("tr");
    tr.innerHTML = `<td colspan="12" class="muted">keine zusammenhängenden Buchungen gefunden – Quelle evtl. leer (Mai 2027 oft noch nicht im Verkauf).</td>`;
    tb.appendChild(tr);
    return;
  }
  for (const r of rows) {
    const tr = el("tr", r.excluded ? "excluded" : "");
    const isMC = r.trip_type === "multi_city";
    const outN = Math.max(0, (r.segments || []).length - 1);

    let segTxt;
    if (isMC) {
      const outTxt = (r.segments || []).map((s) => `${s.from}→${s.to} ${timeTZ(s.departure, s.departure_tz)}` + (s.plane_type ? ` (${s.plane_type})` : "")).join(" · ");
      const retTxt = (r.return_segments || []).map((s) => `${s.from}→${s.to} ${timeTZ(s.departure, s.departure_tz)}` + (s.plane_type ? ` (${s.plane_type})` : "")).join(" · ");
      segTxt = `<div><strong>Hin</strong> ${outTxt}</div><div><strong>Rück</strong> ${retTxt}</div>`;
    } else {
      segTxt = (r.segments || []).map((s) => `${s.from}→${s.to} ${timeTZ(s.departure, s.departure_tz)}` + (s.plane_type ? ` (${s.plane_type})` : "")).join("  ·  ");
    }
    // Roadmap 2.1 "Preis-Leiter": macht sichtbar, wie viel vom Unterschied
    // zu einer schnellen manuellen Suche am Gepaeck bzw. an der Personenzahl
    // liegt, statt an einem echten Bucket-Unterschied.
    if (r.price_ladder && Object.keys(r.price_ladder).length) {
      const LADDER_LABELS = { "1_pax_ohne_gepaeck": "1 Pax o. Gepäck", "1_pax_mit_gepaeck": "1 Pax m. Gepäck", "4_pax": "4 Pax", "8_pax": "8 Pax" };
      const order = ["1_pax_ohne_gepaeck", "1_pax_mit_gepaeck", "4_pax", "8_pax"];
      const parts = order.filter((k) => r.price_ladder[k] != null).map((k) => `${LADDER_LABELS[k]} ${money2(r.price_ladder[k])}`);
      if (parts.length) segTxt += `<div class="seg muted">Preis-Leiter (p.P.): ${parts.join(" · ")}</div>`;
    }
    if (r.booking_options && r.booking_options.length) {
      const opts = r.booking_options.map((o) => `${o.provider}${o.is_airline ? " (Airline)" : ""} ${money2(o.price)}`).join(" · ");
      segTxt += `<div class="seg muted">Buchungsoptionen: ${opts}</div>`;
    }
    // Roadmap 1.3: bei approximierten Segmenten sind die Umstiegsminuten
    // erfunden (gleichverteilt) - nur die echten Flughafen-Codes zeigen,
    // kein "0h", das wie eine echte Messung aussieht.
    const layTxt = r.segment_times_approximate
      ? (r.layover_airports || []).join(", ") || "–"
      : (r.layover_airports || []).map((a, i) => `${a} ${hm(r.layover_minutes[i])}`).join(", ") || "–";
    const typeBadge = (isMC
      ? `<span class="pill durchschnitt" style="font-size:10px">Multi-City</span>`
      : `<span class="pill guenstig" style="font-size:10px">Round-Trip</span>`)
      + (r.segment_times_approximate
        ? ` <span class="tag amber" title="Umstiegs-Mindestzeit konnte fuer dieses Angebot nicht geprueft werden (keine Einzel-Umstiegszeiten von Google Flights sichtbar)">⚠ Umstiegszeiten ungeprüft</span>`
        : "")
      + (r.pax_mode === "group"
        ? ` <span class="tag teal" title="Mit der vollen Personenzahl (8) real gesucht, nicht aus 1 Person hochgerechnet">Gruppe geprüft</span>`
        : r.pax_mode && r.pax_mode.startsWith("split_")
        ? (() => {
            const parts = r.pax_mode.replace("split_", "").split("_");
            // 14.09.26 (externe Review, Punkt A2): ein Split-Preis ist NIE
            // bestaetigt - je nachdem ob ein echter 8-Pax-Vergleichspreis
            // vorlag, ist es entweder nur eine Preis-UNTERGRENZE oder eine
            // konservative Schaetzung (2. Familie zum vollen 8er-Preis
            // kalkuliert) - siehe price_confidence (immer gesetzt fuer split_4_4).
            const isLowerBound = (r.price_confidence || "").includes("UNTERGRENZE");
            const label = isLowerBound ? "Untergrenze" : "Schätzung";
            const tip = (r.price_confidence || "") +
              " — erst Familie 1 buchen, dann Preis für Familie 2 sofort neu prüfen.";
            return ` <span class="tag amber" title="${tip.replace(/"/g, '&quot;')}">${parts.length}× getrennt (${parts.join("+")}) · ${label}</span>`;
          })()
        : r.pax_mode === "estimated"
        ? ` <span class="tag" style="opacity:.6" title="Nur 1 Person real gesucht, Preis ×8 hochgerechnet - fuer diese Route/Termin gibt es i.d.R. weiter unten eine echt fuer 8 bzw. 4+4 Personen gepruefte Zeile (Badge 'Gruppe geprüft' / 'getrennt')">Hochrechnung</span>`
        : "")
      // Nutzerwunsch 13.09.26: Rueckflug nachmittags ab USM bevorzugen (kein
      // Ausschluss, nur Hinweis-Badge) - echte Rueckflug-Abflugzeit liegt nur
      // bei Multi-City vor (round_trip liefert von Google nur die Hinstrecke
      // im Detail, siehe FlightOffer.return_segments-Kommentar).
      + (isMC && r.return_segments && r.return_segments[0]
          && new Date(r.return_segments[0].departure).getHours() >= 12
        ? ` <span class="tag teal" title="Rückflug startet nachmittags ab USM">USM nachmittags</span>`
        : "");
    // Roadmap 1.5: "zurück ab USM" verschluckte die eigentliche Rueckroute;
    // jetzt die volle Rueckstrecke ausschreiben statt nur den Startflughafen.
    const routeTxt = isMC && r.return_route.length
      ? `${(r.route || []).join("→")} <span class="muted">/ Rück ${(r.return_route || []).join("→")}</span>`
      : (r.route || [r.origin, r.destination]).join("→");
    const dateTxt = `${r.search_date} → ${r.return_date || "?"}`;
    // Roadmap 1.5: bei Multi-City zaehlte diese Spalte Hin+Rueck-Stopps
    // zusammen - Constraint ist aber "max. 2 PRO RICHTUNG", "4 Stopps"
    // suggerierte faelschlich eine Verletzung. "2 + 2" (Hin + Rück) zeigen.
    const retN = isMC ? Math.max(0, (r.return_segments || []).length - 1) : 0;
    const stopsTxt = isMC ? `${outN} + ${retN}` : String(r.stops);
    tr.innerHTML = `
      <td class="mono nowrap">${money(r.price_total)}</td>
      <td class="mono nowrap">${money(r.price_per_person)}</td>
      <td class="nowrap">${r.deep_link ? `<a href="${r.deep_link}" target="_blank" rel="noopener">→</a>` : ""}</td>
      <td>${typeBadge}</td>
      <td class="nowrap">${routeTxt}<div class="seg">${dateTxt}</div>${r.excluded ? `<div class="reason">✗ ${r.exclude_reason}</div>` : ""}</td>
      <td class="mono">${stopsTxt}</td>
      <td class="small hide-narrow">${layTxt}</td>
      <td class="mono nowrap">${hm(r.total_duration_minutes)}</td>
      <td class="small nowrap">${r.segments[0] ? r.segments[0].from + " " : ""}${timeTZ(r.departure)}</td>
      <td class="small hide-narrow">${(r.airlines || []).join(", ") || "–"}</td>
      <td class="small hide-narrow">${segTxt}</td>
      <td class="small nowrap hide-narrow">${dt(r.captured_at)}</td>`;
    tb.appendChild(tr);
  }
}

/* ---------------- hotels / packages ---------------- */
/* ---------------- ITA Matrix (Recherche-Referenz) ---------------- */
async function loadItaMatrix() {
  const data = await api("/api/ita_matrix");
  const tb = $("#itaMatrixTable tbody");
  tb.innerHTML = "";
  if (!data.rows.length) {
    tb.innerHTML = `<tr><td colspan="5" class="muted">noch keine Daten</td></tr>`;
    return;
  }
  for (const r of data.rows) {
    const tr = el("tr", r.ok ? "" : "excluded");
    const links = [
      r.deep_link_1pax ? `<a href="${r.deep_link_1pax}" target="_blank" rel="noopener">1 Pax</a>` : "",
      r.deep_link_group ? `<a href="${r.deep_link_group}" target="_blank" rel="noopener">8 Pax (lädt lang)</a>` : "",
      r.google_flights_link ? `<a href="${r.google_flights_link}" target="_blank" rel="noopener">buchbar suchen →</a>` : "",
    ].filter(Boolean).join(" · ");
    tr.innerHTML = `
      <td class="nowrap">${r.origin}→${r.destination}</td>
      <td class="small nowrap">${r.search_date} → ${r.return_date}</td>
      <td class="mono nowrap">${r.ok ? money(r.price_total) : "–"}</td>
      <td class="mono nowrap">${r.ok ? money2(r.price_per_person) : "–"}</td>
      <td class="small">${links}${!r.ok ? `<div class="reason">kein Preis gefunden</div>` : ""}</td>`;
    tb.appendChild(tr);
  }
}

function shortDate(iso) {
  const [, m, d] = (iso || "").split("-");
  return d ? `${d}.${m}.` : "?";
}

// Vergleichbarer Endpreis (inkl. Steuern/Gebuehren): Santiburi direkt zeigt
// Preise ohne, dafuer gibt es eine Schaetzung; Google-Anbieter sind inkl.
function comparable(r) {
  const raw = r.raw || {};
  return raw.price_total_incl_fees_estimate || r.price_total;
}

function renderHotelCompare(rows, ci, co) {
  const box = $("#hotelCompare");
  const list = rows
    .filter((r) => r.ok && r.price_total != null &&
      (r.is_reference || ((r.raw || {}).checkin === ci && (r.raw || {}).checkout === co)))
    .sort((a, b) => comparable(a) - comparable(b));
  if (!list.length) { box.innerHTML = `<p class="muted small">Keine Angebote für diesen Zeitraum.</p>`; return; }
  const best = comparable(list[0]);
  const persons = (state.summary || {}).persons;
  const nights = Math.round((new Date(co) - new Date(ci)) / 86400000);
  const body = list.map((r, i) => {
    const raw = r.raw || {};
    const price = comparable(r);
    const direct = (r.source || "").startsWith("santiburi");
    const parts = raw.room_split && raw.per_room_size
      ? Object.entries(raw.room_split).map(([size, cnt]) => {
          const info = raw.per_room_size[size] || {};
          const lbl = `${cnt}× ${size}er`;
          return info.url ? `<a href="${info.url}" target="_blank" rel="noopener">${lbl}</a>` : lbl;
        }).join(" + ")
      : "";
    const notes = [];
    if (direct) {
      notes.push(`<span title="${(raw.tax_fee_note || "").replace(/"/g, "&quot;")}">ohne Steuern ${money(r.price_total)}, Gebühren geschätzt</span>`);
      if (raw.member_rate_note) notes.push(`<span style="color:var(--teal)">👤 Member-Rate</span>`);
    } else if (raw.via) {
      notes.push(`via ${raw.via}, inkl. Steuern/Gebühren`);
    }
    if (raw.rate_note) notes.push(raw.rate_note);
    if (r.is_reference) notes.push(`<span class="tag">manuell erfasst</span> ${raw.note || ""}`);
    if (raw.cheapest_refundable) notes.push(`kostenlos stornierbar ab ${money(raw.cheapest_refundable)}`);
    const diff = i === 0 ? `<span class="tag teal">günstigster</span>` : `<span class="muted">+${money(price - best)}</span>`;
    const link = r.deep_link ? `<a href="${r.deep_link}" target="_blank" rel="noopener">öffnen →</a>` : "";
    return `<tr class="${i === 0 ? "win" : ""}">
      <td>${i + 1}</td>
      <td>${niceName(r.source)}${r.is_reference ? "" : employeeDiscountNote(raw.provider || r.source)}</td>
      <td class="mono nowrap">${money(price)}<div class="seg muted">${diff}</div>${(() => {
        const pct = r.is_reference ? null : employeeDiscountPct(raw.provider || r.source);  // Referenzen enthalten den Rabatt schon
        return pct ? `<div class="seg" style="color:var(--teal)" title="Rabatt laut Mitarbeiterportal, genauer Wert erst beim Checkout">mit bis zu ${pct}% ≈ ${money(price * (1 - pct / 100))}</div>` : "";
      })()}</td>
      <td class="mono nowrap">${persons ? money(price / persons) : "–"}</td>
      <td class="mono nowrap">${persons && nights ? money2(price / persons / nights) : "–"}</td>
      <td class="mono nowrap">${nights ? money(price / nights) : "–"}</td>
      <td class="small">${parts}</td>
      <td class="small muted">${notes.join(" · ")}</td>
      <td class="nowrap">${link}</td></tr>`;
  }).join("");
  box.innerHTML = `<table class="stack-narrow"><thead><tr>
    <th>#</th><th>Anbieter</th><th>Gesamt</th><th>p. P.</th><th>p. P./Nacht</th><th>pro Nacht</th><th>Zimmer</th><th>Hinweis</th><th></th>
    </tr></thead><tbody>${body}</tbody></table>`;
}

// Nutzer 19.09.26: Hotelpreis je Datumskombination (Ankunft = Abflug+1),
// damit man sieht, was welches Flugdatum-Paar fuers Hotel bedeutet.
function renderHotelMatrix(rows) {
  const box = $("#hotelMatrix");
  const tagged = rows.filter((r) => (r.raw || {}).checkin && !r.is_reference && r.ok);
  if (!tagged.length) { box.innerHTML = ""; return; }
  const key = (r) => `${r.raw.checkin}|${r.raw.checkout}`;
  const combos = [...new Set(tagged.map(key))].sort();
  // Nur ein Zeitraum (Flug gebucht) -> kein Vergleich noetig, Abschnitt ausblenden.
  const sec = box.closest("section");
  if (sec) sec.hidden = combos.length <= 1;
  if (combos.length <= 1) { box.innerHTML = ""; return; }
  const cheapestBySource = {};
  for (const r of tagged) if (r.price_total != null) cheapestBySource[r.source] = Math.min(cheapestBySource[r.source] ?? Infinity, comparable(r));
  const sources = Object.keys(cheapestBySource).sort((a, b) => cheapestBySource[a] - cheapestBySource[b]);
  let best = null;
  for (const r of tagged) if (r.price_total != null && (best === null || comparable(r) < best)) best = comparable(r);
  const head = combos.map((k) => {
    const [a, b] = k.split("|");
    return `<th>${shortDate(a)}–${shortDate(b)}<div class="seg muted">${Math.round((new Date(b) - new Date(a)) / 86400000)} Nächte</div></th>`;
  }).join("");
  const body = sources.map((src) => {
    const cells = combos.map((k) => {
      const r = tagged.find((x) => x.source === src && key(x) === k);
      if (!r) return `<td class="muted">–</td>`;
      if (r.price_total == null) return `<td class="muted">–</td>`;
      const txt = money(comparable(r));
      const inner = r.deep_link ? `<a href="${r.deep_link}" target="_blank" rel="noopener">${txt}</a>` : txt;
      return `<td class="mono${comparable(r) === best ? " win" : ""}">${inner}</td>`;
    }).join("");
    return `<tr><td>${src}</td>${cells}</tr>`;
  }).join("");
  box.innerHTML = `<table class="stack-narrow"><thead><tr><th>Hotel je Zeitraum</th>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

// Jahresverlauf der Santiburi-Direktpreise (statische Momentaufnahme, siehe
// rate_calendar.json - der Server kommt an die Buchungsseite nicht heran).
async function loadRateCalendar() {
  const box = $("#rateCalendar");
  const r = await fetch("/rate_calendar.json");
  if (!r.ok) { box.innerHTML = ""; return; }
  const d = await r.json();
  $("#rateCalNote").textContent = `Preis pro Zimmer/Nacht, 2 Gäste, ohne Steuern · Stand ${d.captured}`;
  const top = Math.max(...d.months.map((m) => m.max));
  box.innerHTML = d.months.map((m) => {
    const w = Math.round((m.avg / top) * 100);
    const lo = Math.round((m.min / top) * 100), hi = Math.round((m.max / top) * 100);
    return `<div style="display:flex;align-items:center;gap:10px;margin:3px 0${m.trip ? ";font-weight:600" : ""}">
      <span class="mono" style="width:78px">${m.m}</span>
      <span style="position:relative;flex:1;height:16px;background:var(--border, #ddd);border-radius:3px">
        <span style="position:absolute;left:${lo}%;width:${Math.max(1, hi - lo)}%;top:6px;height:4px;background:#bbb"></span>
        <span style="position:absolute;left:0;width:${w}%;height:16px;background:${m.trip ? "var(--coral, #d9614a)" : "#6db3aa"};border-radius:3px;opacity:.85"></span>
      </span>
      <span class="mono nowrap" style="width:170px">Ø ${money(m.avg)} <span class="muted small">(${m.min}–${m.max})</span>${m.trip ? " ← eure Reise" : ""}</span></div>`;
  }).join("");
}

async function loadHotels() {
  const data = await api("/api/hotels");
  renderHotelMatrix(data.rows || []);
  const vh = ((state.summary || {}).verdict || {}).hotel;
  if (vh && vh.checkin) renderHotelCompare(data.rows || [], vh.checkin, vh.checkout);
  const tb = $("#hotelsTable tbody");
  tb.innerHTML = "";
  if (!data.rows.length) { tb.innerHTML = `<tr><td colspan="7" class="muted">noch keine Hoteldaten</td></tr>`; return; }
  for (const r of data.rows) {
    const tr = el("tr", r.ok ? "" : "excluded");
    const raw = r.raw || {};
    const refBadge = r.is_reference ? ` <span class="tag">Referenz</span>` : "";
    // Nutzerwunsch 16.09.26: "keine unnoetige Info" - die lange Methodik-
    // Erklaerung (basis/note) steht jetzt als Tooltip auf dem Quellnamen
    // statt als eigene Textzeile, die konkreten Zahlen (Zimmeraufteilung,
    // OTA-Preise, Villa-Preis) bleiben sichtbar.
    const basisTip = (r.is_reference ? raw.note : raw.basis) || "";
    const est = raw.estimate ? ` <span class="pill durchschnitt" style="font-size:10px">Richtwert</span>` : "";
    const villa = raw.villa_min_per_night ? `<div class="seg muted">Villa ≥3 Gäste ab ${money(raw.villa_min_per_night)}/Nacht</div>` : "";
    const otas = raw.otas && Object.keys(raw.otas).length
      ? `<div class="seg muted">OTA/Nacht: ${Object.entries(raw.otas).sort((a,b)=>a[1]-b[1]).map(([k,v])=>`${k} ${money(v)}`).join(" · ")}</div>` : "";
    let roomSplit = "";
    if (raw.room_split && raw.per_room_size) {
      const parts = Object.entries(raw.room_split).map(([size, cnt]) => {
        const info = raw.per_room_size[size] || {};
        const p = info.cheapest !== undefined && info.cheapest !== null ? money(info.cheapest) : "–";
        const label = `${cnt}× (1 Zi./${size} Erw.) ${p}`;
        return info.url ? `<a href="${info.url}" target="_blank" rel="noopener">${label}</a>` : label;
      });
      roomSplit = `<div class="seg muted">${parts.join(" + ")}</div>`;
      if (raw.refundable_total) {
        roomSplit += `<div class="seg muted">kostenlos stornierbar: ${money(raw.refundable_total)}</div>`;
      }
      // Nutzer-Fund 17.09.26: "santiburi direkt guenstig, aber real nicht
      // durch die Fees" - der gescrapte Preis schliesst Steuern/Gebuehren
      // explizit aus (siehe santiburi.py TAX_FEE_NOTE). Geschaetzten
      // Endpreis direkt daneben zeigen, damit der Vergleich fair bleibt.
      if (raw.price_total_incl_fees_estimate) {
        roomSplit += `<div class="seg" style="color:var(--amber)" title="${(raw.tax_fee_note || "").replace(/"/g, '&quot;')}">` +
          `zzgl. Steuern/Gebühren: ca. ${money(raw.price_total_incl_fees_estimate)} gesamt</div>`;
      }
      if (raw.member_rate_note) {
        roomSplit += `<div class="seg" style="color:var(--teal)">👤 ${raw.member_rate_note}</div>`;
      }
    }
    let statusCell;
    if (r.ok) statusCell = `ok${r.error ? `<div class="seg" style="color:var(--amber)">${r.error}</div>` : ""}`;
    else statusCell = `<span class="reason">${r.error || "fehlgeschlagen"}</span>`;
    // Nutzerwunsch 16.09.26: wie bei der Flugtabelle Preis+Link LINKS statt
    // rechts (kein Scrollen zum Wichtigsten noetig), Quelle mit den
    // konkreten Daten (Zimmeraufteilung/OTA/Villa) danach.
    tr.innerHTML = `
      <td class="mono nowrap" data-label="Gesamt">${money(r.price_total)}</td>
      <td class="mono nowrap" data-label="pro Nacht">${money(r.per_night)}<div class="seg muted">×${r.rooms}×${r.nights}N</div></td>
      <td class="nowrap">${r.deep_link ? `<a href="${r.deep_link}" target="_blank" rel="noopener">→</a>` : ""}</td>
      <td title="${basisTip.replace(/"/g, '&quot;')}">${r.source}${raw.checkin ? ` <span class="small muted">${shortDate(raw.checkin)}–${shortDate(raw.checkout)}</span>` : ""}${refBadge}${est}${employeeDiscountNote(r.source)}${roomSplit}${villa}${otas}</td>
      <td class="small" data-label="Zimmer/Gäste">${r.rooms} / ${r.guests}</td>
      <td class="small" data-label="Status">${statusCell}</td>
      <td class="small nowrap hide-narrow" data-label="erfasst">${dt(r.captured_at)}</td>`;
    tb.appendChild(tr);
  }
}

async function loadPackages() {
  const data = await api("/api/packages");
  const tb = $("#packagesTable tbody");
  tb.innerHTML = "";
  if (!data.rows.length) { tb.innerHTML = `<tr><td colspan="8" class="muted">keine Pauschalreisen (für Mai 2027 häufig noch nicht buchbar)</td></tr>`; return; }
  for (const r of data.rows) {
    const tr = el("tr", r.ok ? "" : "excluded");
    const raw = r.raw || {};
    const refBadge = r.is_reference ? ` <span class="tag">Referenz</span>` : "";
    const note = r.is_reference && raw.note ? `<div class="seg muted">${raw.note}</div>` : "";
    let roomSplit = "";
    if (raw.room_split && raw.per_room_size) {
      const parts = Object.entries(raw.room_split).map(([size, cnt]) => {
        const info = raw.per_room_size[size] || {};
        const p = info.price !== undefined && info.price !== null ? money(info.price) : "–";
        const label = `${cnt}× (1 Zi./${size} Erw.) ${p}`;
        return info.url ? `<a href="${info.url}" target="_blank" rel="noopener">${label}</a>` : label;
      });
      roomSplit = `<div class="seg muted">${parts.join(" + ")}</div>`;
    }
    tr.innerHTML = `
      <td>${r.operator || r.source}${refBadge}${note}${roomSplit}</td>
      <td class="mono nowrap" data-label="Gesamt 8 Pax">${money(r.price_total)}</td>
      <td class="small" data-label="Hotel">${r.hotel_name || "–"}${raw.hotel_match === false ? ' <span class="reason">(Name unklar)</span>' : ""}</td>
      <td class="small" data-label="Abflug">${r.dep_airport || "–"}</td>
      <td class="small" data-label="Verpflegung">${r.board || "–"}</td>
      <td class="small" data-label="Status">${r.ok ? "ok" : `<span class="reason">${r.error || "fehlgeschlagen"}</span>`}</td>
      <td class="small nowrap" data-label="erfasst">${dt(r.captured_at)}</td>
      <td class="nowrap">${r.deep_link ? `<a href="${r.deep_link}" target="_blank" rel="noopener">→ öffnen</a>` : ""}</td>`;
    tb.appendChild(tr);
  }
}

/* ---------------- health ---------------- */
// Kurze, unaufgeregte Zusammenfassung fuer den Normalo (kein Fachchinesisch,
// keine Query-Zahlen/Python-Fehlermeldungen) - die vollen technischen Werte
// stehen weiterhin unter "Technische Details" (siehe loadHealth unten).
const HEALTH_LABELS = {
  flights: "Flüge", hotels: "Hotel", packages: "Pauschalreisen",
  ita_matrix: "ITA Matrix (Recherche)",
};
function healthSummaryLine(name, info) {
  const label = HEALTH_LABELS[name] || name;
  if (name === "ita_matrix") return null; // reine Bonus-Quelle, nicht buchbar - nur in Details
  const count = info.count ?? 0;
  if (info.ok) return { icon: "✅", text: `${label}: aktuelle Preise gefunden` };
  if (count > 0) return { icon: "⚠️", text: `${label}: Preise gefunden, ein paar Routen kurzzeitig nicht abrufbar` };
  if (name === "packages") return { icon: "ℹ️", text: `${label}: für diesen Zeitraum noch keine gefunden (üblich, so früh im Voraus)` };
  return { icon: "⚠️", text: `${label}: gerade keine Preise abrufbar, nächster Check versucht's erneut` };
}

async function loadHealth() {
  const s = await api("/api/status");
  const h = (s.last_run && s.last_run.source_health) || {};

  const summaryBox = $("#healthSummary");
  summaryBox.innerHTML = "";
  if (!Object.keys(h).length) {
    summaryBox.innerHTML = `<div class="h-line muted">Noch kein Check gelaufen.</div>`;
  } else {
    for (const [name, info] of Object.entries(h)) {
      const line = healthSummaryLine(name, info);
      if (!line) continue;
      const d = el("div", "h-line");
      d.innerHTML = `<span>${line.icon}</span> ${line.text}`;
      summaryBox.appendChild(d);
    }
  }

  const box = $("#health");
  box.innerHTML = "";
  for (const [name, info] of Object.entries(h)) {
    const cls = info.ok ? "" : (info.count ? "warn" : "bad");
    const d = el("div", "h " + cls);
    let txt = `${name}: ${info.count ?? 0} Treffer`;
    if (info.roundtrip_count !== undefined) txt += ` (RT ${info.roundtrip_count} / MC ${info.multicity_count ?? 0})`;
    if (info.queries) txt += ` / ${info.queries} Abfragen`;
    if (info.blocked_queries) txt += ` · ${info.blocked_queries} blockiert`;
    if (info.failed_queries) txt += ` · ${info.failed_queries} fehlgeschlagen`;
    if (info.group_checked) txt += ` · ${info.group_checked} Gruppen-Check(s)`;
    if (info.attempted) txt += ` / ${info.attempted} versucht`;
    if (info.constraints) txt += ` · ${info.constraints.kept}/${info.constraints.total} nach Filter`;
    if (info.error) txt += ` · ${info.error.slice(0, 160)}`;
    d.textContent = txt;
    box.appendChild(d);
  }
  if (!Object.keys(h).length) box.innerHTML = `<div class="h muted">noch kein Check gelaufen</div>`;

  const mc = (h.flights && h.flights.manual_check) || [];
  const mcBox = $("#manualCheckBox"), mcList = $("#manualCheckList");
  if (mc.length) {
    mcBox.hidden = false;
    mcList.innerHTML = mc.map((m) => `<div style="margin-bottom:4px">${m.label}${m.deep_link
      ? ` — <a href="${m.deep_link}" target="_blank" rel="noopener">selbst prüfen</a>`
      : ""} <span class="muted">(${m.reason})</span></div>`).join("");
  } else {
    mcBox.hidden = true;
  }
}

/* ---------------- Agoda-Preisgarantie ---------------- */
async function loadGuarantee() {
  const g = await api("/api/guarantee");
  const sec = $("#guaranteeSection");
  document.body.classList.toggle("guarantee-mode", !!g.active);   // blendet alles ausser dem Flyer aus
  if (!g.active) { sec.hidden = true; return; }
  sec.hidden = false;
  const box = $("#guaranteeBox");
  const fmtDay = (iso, opt) => new Date(iso + "T12:00:00").toLocaleDateString("de-DE", opt || { weekday: "short", day: "2-digit", month: "long", year: "numeric" });
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const best = g.best;
  const saving = g.saving || 0;
  const days = Math.max(0, Math.ceil((new Date(g.deadline + "T23:59:00") - new Date()) / 86400000));
  const f = g.flight_detail || {};
  const outDay = f.outbound_date ? fmtDay(f.outbound_date) : "";
  const arrIso = f.outbound_date ? new Date(new Date(f.outbound_date + "T12:00:00").getTime() + 86400000).toISOString().slice(0, 10) : "";
  const retDay = f.return_date ? fmtDay(f.return_date) : "";
  const flightDays = f.outbound_date && f.return_date ? Math.round((new Date(f.return_date) - new Date(f.outbound_date)) / 86400000) : null;

  // --- Preisskala: Alarm-Schwelle | dein Preis | bester anerkannter Preis
  const marks = [];
  if (g.threshold) marks.push({ v: g.threshold, label: "Alarm", cls: "mk-alarm" });
  marks.push({ v: g.booked, label: "dein Preis", cls: "mk-booked" });
  if (best) marks.push({ v: best.total, label: best.source, cls: "mk-best" });
  const lo = Math.min(...marks.map((m) => m.v)) * 0.97, hi = Math.max(...marks.map((m) => m.v)) * 1.02;
  const pos = (v) => (((v - lo) / (hi - lo)) * 100).toFixed(1);
  const scale = marks.map((m, i) =>
    `<div class="mk ${m.cls}" style="left:${pos(m.v)}%"><span class="mk-dot"></span><span class="mk-txt ${i % 2 ? "up" : "down"}">${esc(m.label)}<br><b>${money(m.v)}</b></span></div>`).join("");

  // --- Fortschritt zum Ziel
  const tgt = g.target_total, tot = g.total;
  const toTarget = g.to_target;
  const pct = tgt && tot ? Math.min(100, (tgt / tot) * 100).toFixed(1) : null;

  box.innerHTML = `
  <div class="flyer-hero" style="background-image:linear-gradient(180deg,rgba(14,40,38,.15) 0%,rgba(14,40,38,.78) 100%),url('/img/santiburi.jpg')">
    <div class="fh-top"><span class="fh-chip">Reise bestätigt ✓</span><span class="fh-count">${esc(Math.max(0, Math.ceil((new Date(g.checkin + "T00:00:00") - new Date()) / 86400000)))} Tage bis Thailand 🌴</span></div>
    <div class="fh-body">
      <h1 class="fh-title sr">${esc(g.hotel_name)}</h1>
      <div class="fh-meta">${fmtDay(g.checkin, { day: "2-digit", month: "long" })} – ${fmtDay(g.checkout, { day: "2-digit", month: "long", year: "numeric" })} · ${g.nights} Nächte · ${g.persons} Personen · 3 Villen</div>
    </div>
  </div>


  <div class="facts">
    <div class="fact"><span class="fi">🕒</span><div><div class="fl">Jetzt auf Koh Samui</div><div class="fv" id="factLocalTime">–</div></div></div>
    <div class="fact"><span class="fi">🌍</span><div><div class="fl">Zeitverschiebung im Mai</div><div class="fv">+5 Stunden</div></div></div>
    <div class="fact"><span class="fi">🛫</span><div><div class="fl">Abflug in</div><div class="fv">${f.outbound_date ? Math.max(0, Math.ceil((new Date(f.outbound_date + "T00:00:00") - new Date()) / 86400000)) + " Tagen" : "–"}</div></div></div>
    <div class="fact"><span class="fi">🌡</span><div><div class="fl">Mai auf Samui (typisch)</div><div class="fv">28–32 °C · Meer 29 °C</div></div></div>
    <div class="fact"><span class="fi">🌙</span><div><div class="fl">Nachtflug</div><div class="fv">${arrIso ? "Ankunft " + esc(fmtDay(arrIso, { weekday: "short", day: "2-digit", month: "short" })) : "–"}</div></div></div>
    <div class="fact"><span class="fi">👥</span><div><div class="fl">Pro Person gesamt</div><div class="fv">${money2(g.per_person)}</div></div></div>
  </div>

  <div class="flyer-cards">
    <article class="ticket">
      <div class="ticket-head"><span>✈ Flug</span><span class="tag ok">gebucht</span></div>
      <div class="route">
        <div class="stop"><div class="code">${esc(f.origin || "ABFLUG")}</div><div class="when">${outDay ? esc(fmtDay(f.outbound_date, { weekday: "short", day: "2-digit", month: "short" })) : ""}</div></div>
        <div class="line"><span class="plane">✈</span><span class="dur">${flightDays != null ? flightDays + " Tage" : ""}</span></div>
        <div class="stop right"><div class="code">${esc(f.destination || "USM")}</div><div class="when">${arrIso ? "Ankunft " + esc(fmtDay(arrIso, { weekday: "short", day: "2-digit", month: "short" })) : ""}</div></div>
      </div>
      <div class="ticket-rows">
        <div><span>Hinflug</span><b>${outDay ? esc(outDay) : "–"}</b></div>
        ${f.outbound_info ? `<div><span>Details</span><b>${esc(f.outbound_info)}</b></div>` : ""}
        <div><span>Rückflug</span><b>${retDay ? esc(retDay) : "–"}</b></div>
        ${f.return_info ? `<div><span>Details</span><b>${esc(f.return_info)}</b></div>` : ""}
        ${(f.airlines || []).length ? `<div><span>Airline</span><b>${esc(f.airlines.join(", "))}</b></div>` : ""}
        ${f.booking_ref ? `<div><span>Buchungscode</span><b>${esc(f.booking_ref)}</b></div>` : ""}
        <div><span>Passagiere</span><b>${g.persons}</b></div>
      </div>
      <div class="ticket-foot"><span>${money2(g.flight / g.persons)} p. P.</span><b>${money2(g.flight)}</b></div>
    </article>

    <article class="ticket hotel">
      <div class="ticket-head"><span>🏝 Hotel</span><span class="tag ok">gebucht · ${esc(g.provider)}</span></div>
      <div class="hotel-name">${esc(g.hotel_name)}</div>
      <div class="hotel-sub">3 × Deluxe Garten Villa · Frühstück inklusive · WLAN & Parkplatz</div>
      <div class="ticket-rows">
        <div><span>Check-in</span><b>${esc(fmtDay(g.checkin))}</b></div>
        <div><span>Check-out</span><b>${esc(fmtDay(g.checkout))}</b></div>
        <div><span>Gäste</span><b>${g.persons} Erwachsene (3 + 3 + 2)</b></div>
        <div><span>Tarif</span><b>nicht erstattbar</b></div>
        ${g.booked_on ? `<div><span>Gebucht am</span><b>${esc(fmtDay(g.booked_on, { day: "2-digit", month: "long", year: "numeric" }))}</b></div>` : ""}
      </div>
      <div class="ticket-foot"><span>${g.booked_thb ? g.booked_thb.toLocaleString("de-DE", { minimumFractionDigits: 2 }) + " ฿ · " : ""}${money2(g.booked / g.persons / g.nights)} p. P./Nacht</span><b>${money2(g.booked)}</b></div>
    </article>
  </div>

  <div class="flyer-total">
    <div class="ft-main"><div class="ft-label">Gesamtbetrag Flug + Hotel</div><div class="ft-value">${money2(g.total)}</div>
      <div class="ft-sub">${money2(g.per_person)} pro Person${toTarget != null ? ` · ${toTarget > 0 ? "<b>" + money2(toTarget) + "</b> über" : "<b>" + money2(Math.abs(toTarget)) + "</b> unter"} dem Ziel (${money(tgt)})` : ""}</div>
      ${pct ? `<div class="bar"><span style="width:${pct}%"></span></div><div class="bar-cap">${pct} % vom Ziel ${money(tgt)} erreicht</div>` : ""}
    </div>
    <div class="ft-save ${saving > 0 ? "good" : ""}">
      <div class="ft-label">Ersparnis durch Preisgarantie</div>
      <div class="ft-value">${money2(saving)}</div>
      <div class="ft-sub">${best ? (saving > 0 ? `${esc(best.source)} ist günstiger – Antrag möglich` : `Bester anerkannter Preis heute: <b>${money2(best.total)}</b> (${esc(best.source)})`) : "Keine anerkannte Plattform verfügbar"}</div>
    </div>
  </div>

  <div class="flyer-gauge">
    <div class="fg-title">🛡 Agoda-Preisgarantie · Preisvergleich</div>
    <div class="scale"><div class="scale-line"></div>${scale}</div>
    <div class="fg-note">Antrag möglich bis <b>${esc(fmtDay(g.deadline, { day: "2-digit", month: "long", year: "numeric" }))}, 23:59 Uhr</b> (Ortszeit Hotel) – noch <b>${days} Tage</b>. Mail-Alarm ab ${g.threshold ? money2(g.threshold) : "–"} (160 € unter deinem Preis).</div>
  </div>

  ${g.offers.length ? `<div class="flyer-table"><div class="fg-title">Anerkannte Plattformen · aktueller Stand</div>
    <div class="table-wrap"><table><thead><tr><th>Plattform</th><th>Gesamt (3 Zimmer)</th><th>p. P./Nacht</th><th>Abstand zu deinem Preis</th></tr></thead><tbody>
    ${g.offers.slice(0, 8).map((o) => `<tr><td>${o.url ? `<a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.source)}</a>` : esc(o.source)}</td><td>${money2(o.total)}</td><td>${money2(o.per_person_night)}</td><td class="${o.total < g.booked ? "neg" : ""}">${o.total - g.booked >= 0 ? "+" : "−"}${money2(Math.abs(o.total - g.booked))}</td></tr>`).join("")}
    </tbody></table></div></div>` : `<div class="flyer-table small muted">Aktuell bietet keine anerkannte Plattform Santiburi für den Zeitraum an.</div>`}

  <details class="flyer-rules"><summary>So funktioniert die Preisgarantie</summary>
    <ul>
      <li>Gleiche Buchung günstiger auf einer <b>anerkannten Plattform</b>: ${esc(g.platforms.join(", "))}.</li>
      <li>Gleiches Hotel, gleiche Daten, <b>gleicher Zimmer- und Bettentyp</b>, 8 Gäste, gleiche oder flexiblere Stornierung, Preis in Euro inkl. Steuern und Gebühren, Frühstück gleich oder besser.</li>
      <li>Antrag mit Buchungsnummer, Link zur Plattform und bis zu 3 Screenshots – <b>bis 23:59 Uhr am Tag vor dem Check-in</b>. Den günstigeren Preis musst du nicht buchen.</li>
      <li>Agoda gleicht den Preis an oder schreibt die Differenz als <b>AgodaCash</b> gut (nach eigenem Ermessen).</li>
      <li>Nicht anerkannt: Mitglieds-, Code- und Kreditkartenpreise, private oder Paketraten, Aktionen und Gutscheine – und alles über Super.com, Stayforlong, CHECK24 oder Google.</li>
    </ul>
  </details>`;
  const tick = () => { const n = document.getElementById("factLocalTime"); if (n) n.textContent = new Date().toLocaleTimeString("de-DE", { timeZone: "Asia/Bangkok", hour: "2-digit", minute: "2-digit" }) + " Uhr"; };
  tick();
  if (window.__samuiClock) clearInterval(window.__samuiClock);
  window.__samuiClock = setInterval(tick, 30000);
}

/* ---------------- refresh orchestration ---------------- */
async function refreshAll() {
  const jobs = {
    Status: loadStatus, Garantie: loadGuarantee, Zusammenfassung: loadSummary, Verlauf: loadHistory,
    Flüge: loadFlights, "ITA Matrix": loadItaMatrix, Hotels: loadHotels, Jahresverlauf: loadRateCalendar, Pauschal: loadPackages, "Quellen-Status": loadHealth,
  };
  const errs = [];
  for (const [name, fn] of Object.entries(jobs)) {
    try { await fn(); }
    catch (e) { errs.push(`${name}: ${e.message}`); console.error(name, e); }
  }
  showError(errs.length ? "Teilweise nicht geladen — " + errs.join(" · ") : "");
}

$("#btnGoogle").addEventListener("click", async () => {
  try {
    const l = await api("/api/links");
    window.open(l.google_hotels, "_blank", "noopener");
  } catch (e) { console.warn("Google-Hotels-Link fehlgeschlagen", e); }
});

$("#btnCheck").addEventListener("click", async () => {
  const b = $("#btnCheck");
  b.disabled = true; b.textContent = "läuft…";
  try {
    await api("/api/check/run", { method: "POST" });
    showError("");
    pollLiveProgress(); // sofort starten, nicht erst beim naechsten 6s-Tick
  } catch (e) {
    showError("Check konnte nicht gestartet werden: " + e.message);
  } finally {
    setTimeout(() => { b.disabled = false; b.textContent = "Jetzt prüfen"; }, 3000);
  }
});

// Nutzer-Fund 16.09.26: "muss das 45min dauern bis was kommt?" - die DB wird
// (bewusst, wegen Trend/Verlauf-Konsistenz) erst am Ende eines kompletten
// Laufs geschrieben, aber /api/live_progress liefert einen rein ephemeren
// Fortschritts-Stand (siehe app/live_progress.py) direkt nach jeder einzeln
// geprueften Route/Kombination. Pollt alle 6s, solange ein Check laeuft;
// sobald er fertig ist (running:false), EINMAL die echten Tabellen neu
// laden statt weiter zu pollen.
let _wasRunning = false;
async function pollLiveProgress() {
  let p;
  try {
    p = await api("/api/live_progress");
  } catch {
    setTimeout(pollLiveProgress, 6000);
    return;
  }
  const box = $("#liveProgress");
  if (p.running) {
    box.hidden = false;
    const progressTxt = p.total ? `${p.checked}/${p.total} geprüft` : `${p.checked} geprüft`;
    const bestTxt = p.best_price != null ? ` · bisher günstigster gefundener Preis: ${money2(p.best_price)} (${p.best_label})` : "";
    $("#liveProgressText").textContent = `${p.phase || "Check läuft"} — ${progressTxt}${bestTxt}`;
    _wasRunning = true;
  } else {
    box.hidden = true;
    if (_wasRunning) {
      _wasRunning = false;
      await refreshAll(); // Lauf ist gerade fertig geworden - jetzt die echten Daten holen
    }
  }
  setTimeout(pollLiveProgress, 6000);
}
pollLiveProgress();

["#fDirection", "#fOrigin", "#fAirline", "#fStops", "#fSort", "#fExcluded", "#fEstimates"].forEach((s) => {
  $(s).addEventListener("change", loadFlights);
});
$("#fAirline").addEventListener("keyup", (e) => { if (e.key === "Enter") loadFlights(); });

function updateSortDescBtn() {
  const b = $("#fDescBtn");
  b.textContent = sortDescending ? "↓ absteigend" : "↑ aufsteigend";
}
$("#fDescBtn").addEventListener("click", () => {
  sortDescending = !sortDescending;
  updateSortDescBtn();
  loadFlights();
});
document.querySelectorAll("#flightsTable th[data-sort]").forEach((th) => {
  th.addEventListener("click", () => {
    if ($("#fSort").value === th.dataset.sort) {
      sortDescending = !sortDescending;      // zweiter Klick auf dieselbe Spalte: Richtung umkehren
    } else {
      $("#fSort").value = th.dataset.sort;
      sortDescending = false;
    }
    updateSortDescBtn();
    loadFlights();
  });
});
updateSortDescBtn();

refreshAll();
setInterval(refreshAll, 120000);
