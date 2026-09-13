"use strict";

const $ = (s) => document.querySelector(s);
const el = (t, c) => { const e = document.createElement(t); if (c) e.className = c; return e; };

let CURRENCY = "EUR";
const state = { flights: null, chart: null, chartSeries: {} };

const CAT_LABELS = {
  flight_total: "Flug (Hin+Rück)",
  hotel_total: "Hotel",
  separate_total: "Einzelbuchung",
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
  renderVerdictCards(s.trends || {}, s.totals || {});
  renderVersus(s.verdict || {}, s.totals || {});
  renderCheapestFlight(s.flight, (s.verdict || {}).return_reference);
}

function renderVerdictCards(trends, totals) {
  const box = $("#verdictCards");
  box.innerHTML = "";
  for (const cat of ["flight_total", "hotel_total", "package_total"]) {
    const t = trends[cat] || { state: "unbekannt", label: "keine Daten" };
    const c = el("div", "card");
    const head = el("div");
    const dot = el("span", `dot ${t.state}`);
    head.appendChild(dot);
    head.appendChild(document.createTextNode(CAT_LABELS[cat]));
    const big = el("div", "big");
    big.textContent = money(totals[cat]);
    const pill = el("span", `pill ${t.state}`);
    pill.textContent = t.label;
    const sub = el("div", "sub");
    if (t.avg) {
      const arrow = t.pct_vs_avg > 0 ? "▲" : t.pct_vs_avg < 0 ? "▼" : "•";
      sub.textContent = `Ø ${money(t.avg)}  ${arrow} ${t.pct_vs_avg ?? 0}%  (n=${t.n})`;
    } else {
      sub.textContent = `noch keine Vergleichsbasis`;
    }
    c.append(head, big, pill, sub);
    box.appendChild(c);
  }
}

function renderVersus(verdict, totals) {
  const sep = totals.separate_total, pkg = totals.package_total;
  $("#sepAmount").textContent = money(sep);
  $("#pkgAmount").textContent = money(pkg);
  const fsrc = verdict.flight_source === "round_trip" ? " (Round-Trip)"
    : verdict.flight_source === "multi_city" ? " (Multi-City)" : "";

  // Bugfix 13.09.26 (Nutzer: "die Preise werden mir garnicht gezeigt,
  // kannst du mir Links mitgeben") - vorher reiner Text ohne jeden Link.
  // Jetzt: Flug- und Hotel-Preis direkt anklickbar, Hotel zusaetzlich mit
  // der Zimmergroessen-Aufschluesselung (je Zimmergroesse ein eigener Link).
  const f = verdict.flight, h = verdict.hotel, p = verdict.package;
  const flightLink = (f && f.deep_link)
    ? `<a href="${f.deep_link}" target="_blank" rel="noopener">Flug${fsrc} ${money(totals.flight_total)}</a>`
    : `Flug${fsrc} ${money(totals.flight_total)}`;
  let hotelLink = (h && h.deep_link)
    ? `<a href="${h.deep_link}" target="_blank" rel="noopener">Hotel (${h.source}) ${money(totals.hotel_total)}</a>`
    : `Hotel ${money(totals.hotel_total)}`;
  if (h && h.per_room_size) {
    const parts = Object.entries(h.per_room_size)
      .filter(([, v]) => v && v.cheapest != null)
      .map(([size, v]) => v.url
        ? `<a href="${v.url}" target="_blank" rel="noopener">1 Zi./${size} Erw. ${money(v.cheapest)}</a>`
        : `1 Zi./${size} Erw. ${money(v.cheapest)}`);
    if (parts.length) hotelLink += ` <span class="small muted">(${parts.join(" · ")})</span>`;
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

function segList(segments, layoverAirports, layoverMinutes) {
  const box = document.createDocumentFragment();
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
  const isMC = f.trip_type === "multi_city";
  const h = el("div", "label");
  h.textContent = isMC ? "Multi-City — ein Ticket, unterschiedliche Flughäfen" : "Hin + Rück — ein Ticket";
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
  const outLay = f.layover_airports.slice(0, outN), outMin = f.layover_minutes.slice(0, outN);
  const outHead = el("div", "seg muted"); outHead.style.marginTop = "6px";
  outHead.textContent = "Hinflug" + (f.search_date ? ` · ${f.search_date}` : "");
  c.appendChild(outHead);
  c.appendChild(segList(f.segments, outLay, outMin));

  if (isMC && f.return_segments && f.return_segments.length) {
    const retLay = f.layover_airports.slice(outN), retMin = f.layover_minutes.slice(outN);
    const retHead = el("div", "seg muted"); retHead.style.marginTop = "6px";
    retHead.textContent = "Rückflug" + (f.return_date ? ` · ${f.return_date}` : "");
    c.appendChild(retHead);
    c.appendChild(segList(f.return_segments, retLay, retMin));
  } else if (!isMC) {
    const note = el("div", "seg muted");
    note.textContent = "Rückflug-Routing zeigt Google erst nach Auswahl des Hinflugs; Preis ist der Gesamtpreis.";
    c.appendChild(note);
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
async function loadHistory() {
  if (typeof Chart === "undefined") { console.warn("Chart.js nicht geladen – Verlauf übersprungen"); return; }
  const h = await api("/api/history");
  CURRENCY = h.currency || CURRENCY;
  const labels = h.t.map((s) => new Date(s).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" }));
  state.chartSeries = h;
  const toggles = $("#chartToggles");
  toggles.innerHTML = "";
  const datasets = [];
  for (const cat of Object.keys(CAT_LABELS)) {
    const on = cat !== "flight_total" ? true : true;
    datasets.push({
      label: CAT_LABELS[cat],
      data: h[cat],
      borderColor: CAT_COLORS[cat],
      backgroundColor: CAT_COLORS[cat] + "22",
      spanGaps: true,
      tension: 0.25,
      pointRadius: 2,
      hidden: false,
    });
    const lab = el("label");
    lab.innerHTML = `<input type="checkbox" data-cat="${cat}" checked /> <span style="color:${CAT_COLORS[cat]}">●</span> ${CAT_LABELS[cat]}`;
    toggles.appendChild(lab);
  }
  if (state.chart) state.chart.destroy();
  state.chart = new Chart($("#chart"), {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { grid: { color: "#2b3a49" }, ticks: { color: "#93a4b3" } },
        y: { grid: { color: "#2b3a49" }, ticks: { color: "#93a4b3", callback: (v) => money(v) } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${money2(c.parsed.y)}` } },
      },
    },
  });
  toggles.querySelectorAll("input").forEach((inp) => {
    inp.addEventListener("change", () => {
      const idx = Object.keys(CAT_LABELS).indexOf(inp.dataset.cat);
      state.chart.setDatasetVisibility(idx, inp.checked);
      state.chart.update();
    });
  });
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
    const layTxt = (r.layover_airports || []).map((a, i) => `${a} ${hm(r.layover_minutes[i])}`).join(", ") || "–";
    const typeBadge = (isMC
      ? `<span class="pill durchschnitt" style="font-size:10px">Multi-City</span>`
      : `<span class="pill guenstig" style="font-size:10px">Round-Trip</span>`)
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
    const routeTxt = isMC && r.return_route.length
      ? `${(r.route || []).join("→")} <span class="muted">/ zurück ab ${r.return_route[0]}</span>`
      : (r.route || [r.origin, r.destination]).join("→");
    const dateTxt = `${r.search_date} → ${r.return_date || "?"}`;
    tr.innerHTML = `
      <td class="mono nowrap">${money(r.price_total)}</td>
      <td class="mono nowrap">${money(r.price_per_person)}</td>
      <td class="nowrap">${r.deep_link ? `<a href="${r.deep_link}" target="_blank" rel="noopener">→</a>` : ""}</td>
      <td>${typeBadge}</td>
      <td class="nowrap">${routeTxt}<div class="seg">${dateTxt}</div>${r.excluded ? `<div class="reason">✗ ${r.exclude_reason}</div>` : ""}</td>
      <td class="mono">${r.stops}</td>
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

async function loadHotels() {
  const data = await api("/api/hotels");
  const tb = $("#hotelsTable tbody");
  tb.innerHTML = "";
  if (!data.rows.length) { tb.innerHTML = `<tr><td colspan="7" class="muted">noch keine Hoteldaten</td></tr>`; return; }
  for (const r of data.rows) {
    const tr = el("tr", r.ok ? "" : "excluded");
    const raw = r.raw || {};
    const refBadge = r.is_reference ? ` <span class="tag">Referenz</span>` : "";
    const basis = r.is_reference
      ? (raw.note ? `<div class="seg muted">${raw.note}</div>` : "")
      : (raw.basis ? `<div class="seg muted">${raw.basis}</div>` : "");
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
    }
    let statusCell;
    if (r.ok) statusCell = `ok${est}${r.error ? `<div class="seg" style="color:var(--amber)">${r.error}</div>` : ""}`;
    else statusCell = `<span class="reason">${r.error || "fehlgeschlagen"}</span>`;
    tr.innerHTML = `
      <td>${r.source}${refBadge}${basis}${roomSplit}${villa}${otas}</td>
      <td class="mono nowrap" data-label="Gesamt">${money(r.price_total)}</td>
      <td class="mono nowrap" data-label="pro Nacht">${money(r.per_night)}<div class="seg muted">×${r.rooms}×${r.nights}N</div></td>
      <td class="small" data-label="Zimmer/Gäste">${r.rooms} / ${r.guests}</td>
      <td class="small" data-label="Status">${statusCell}</td>
      <td class="small nowrap" data-label="erfasst">${dt(r.captured_at)}</td>
      <td class="nowrap">${r.deep_link ? `<a href="${r.deep_link}" target="_blank" rel="noopener">→ öffnen</a>` : ""}</td>`;
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

/* ---------------- refresh orchestration ---------------- */
async function refreshAll() {
  const jobs = {
    Status: loadStatus, Zusammenfassung: loadSummary, Verlauf: loadHistory,
    Flüge: loadFlights, "ITA Matrix": loadItaMatrix, Hotels: loadHotels, Pauschal: loadPackages, "Quellen-Status": loadHealth,
  };
  const errs = [];
  for (const [name, fn] of Object.entries(jobs)) {
    try { await fn(); }
    catch (e) { errs.push(`${name}: ${e.message}`); console.error(name, e); }
  }
  showError(errs.length ? "Teilweise nicht geladen — " + errs.join(" · ") : "");
}

$("#btnCheck").addEventListener("click", async () => {
  const b = $("#btnCheck");
  b.disabled = true; b.textContent = "läuft…";
  try {
    await api("/api/check/run", { method: "POST" });
    showError("");
    let tries = 0;
    const poll = setInterval(async () => {
      tries++;
      await refreshAll();
      if (tries > 40) clearInterval(poll);
    }, 15000);
  } catch (e) {
    showError("Check konnte nicht gestartet werden: " + e.message);
  } finally {
    setTimeout(() => { b.disabled = false; b.textContent = "Jetzt prüfen"; }, 3000);
  }
});

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
