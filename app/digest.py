"""Taegliche Zusammenfassung per E-Mail ("Guten Morgen, so steht es um Santiburi").

Wird vom Scheduler (Worker) einmal am Tag zur konfigurierten Uhrzeit
(``alerts.daily_digest``) verschickt. ``build_digest`` ist reine Darstellung
auf den bereits gespeicherten Daten und damit gut testbar.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .config import Config, Secrets
from .logging_setup import get_logger
from .logic.trends import history_series
from .models import BestSnapshot, CheckRun, HotelOfferRow
from .notify import send_email
from .queries import summary_payload

log = get_logger("digest")

_MONTHS = ["", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
           "August", "September", "Oktober", "November", "Dezember"]


def eur(x: float | None, decimals: int = 0) -> str:
    if x is None:
        return "–"
    s = f"{x:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"


def _date(iso: str) -> str:
    y, m, d = (int(p) for p in iso.split("-"))
    return f"{d}. {_MONTHS[m]}"


def _provider(source: str) -> str:
    return source.replace(" (Google)", "")


def _top_providers(session, run_id: int | None, limit: int = 5) -> list[dict]:
    if not run_id:
        return []
    rows = session.scalars(select(HotelOfferRow).where(HotelOfferRow.run_id == run_id))
    best: dict[str, HotelOfferRow] = {}
    for r in rows:
        if not r.ok or not r.price_total or r.is_reference or (r.raw or {}).get("estimate"):
            continue
        price = (r.raw or {}).get("price_total_incl_fees_estimate") or r.price_total
        name = _provider(r.source)
        if name not in best or price < best[name][0]:
            best[name] = (price, r)
    ranked = sorted(best.items(), key=lambda kv: kv[1][0])[:limit]
    return [{"name": n, "price": p, "url": r.deep_link} for n, (p, r) in ranked]


def _change_since(series_t: list[str], series_v: list[float | None], hours: float) -> float | None:
    """Preis-Differenz zum Wert von vor ~``hours`` Stunden (None wenn unbekannt)."""
    pairs = [(datetime.fromisoformat(t), v) for t, v in zip(series_t, series_v) if v]
    if len(pairs) < 2:
        return None
    now_t, now_v = pairs[-1]
    older = [v for t, v in pairs if t <= now_t - timedelta(hours=hours)]
    return None if not older else round(now_v - older[-1], 2)


def build_digest(session, cfg: Config) -> tuple[str, str, str]:
    """Liefert (Betreff, HTML, Klartext)."""
    s = summary_payload(session, cfg)
    verdict = s.get("verdict") or {}
    hotel = verdict.get("hotel") or {}
    totals = s.get("totals") or {}
    hotel_total = totals.get("hotel_total")
    persons = cfg.trip.persons
    nights = cfg.trip.nights
    ci, co = hotel.get("checkin") or cfg.trip.hotel_checkin.isoformat(), \
        hotel.get("checkout") or cfg.trip.hotel_checkout.isoformat()

    hist = history_series(session, cfg)
    values = [v for v in hist["hotel_total"] if v]
    low = min(values) if values else None
    high = max(values) if values else None
    day = _change_since(hist["t"], hist["hotel_total"], 20)
    trend = (s.get("trends") or {}).get("hotel_total") or {}

    # Wie setzt sich der Preis zusammen?
    combo: list[str] = []
    split = hotel.get("room_split") or {}
    per_size = hotel.get("per_room_size") or {}
    for size in sorted(split, key=int, reverse=True):
        info = per_size.get(size) or {}
        prov = info.get("provider") or _provider(hotel.get("source", ""))
        cnt = split[size]
        each = info.get("cheapest")
        line = f"{cnt} × {size}er-Zimmer bei {prov}"
        if each:
            line += f" – je {eur(each)}"
        combo.append(line)
    if not combo and hotel:
        combo.append(f"Alle Zimmer bei {_provider(hotel.get('source', ''))}")

    pp = hotel_total / persons if hotel_total else None
    pn = hotel_total / persons / nights if hotel_total else None
    flight = totals.get("flight_total")
    grand = totals.get("separate_total")
    top = _top_providers(session, s.get("run_id"))

    # Stimmungszeile
    if day is None:
        mood = "Erste Messung – ab morgen sehe ich Veränderungen."
    elif day < -1:
        mood = f"Gute Nachricht: Seit gestern ist es um {eur(abs(day))} günstiger geworden."
    elif day > 1:
        mood = f"Leider {eur(day)} teurer als gestern."
    else:
        mood = "Der Preis ist seit gestern unverändert."
    if low and hotel_total and hotel_total <= low:
        mood += " Das ist der bisher niedrigste Preis seit Messbeginn!"
    elif trend.get("label"):
        mood += f" Einordnung: {trend['label']}."

    today = datetime.now(ZoneInfo("Europe/Berlin"))
    subject = f"Santiburi {_date(ci)}–{_date(co)}: aktuell {eur(hotel_total)} fürs Hotel"
    if day is not None and abs(day) > 1:
        subject += f" ({'−' if day < 0 else '+'}{eur(abs(day))})"

    # --- Klartext ---
    t = [f"Guten Morgen! Stand {today:%d.%m.%Y}.", "",
         f"Günstigste Hotel-Kombination für {persons} Personen, {nights} Nächte:",
         f"  {eur(hotel_total)}  (≈ {eur(pp)} pro Person, {eur(pn)} pro Person und Nacht)"]
    t += [f"  - {c}" for c in combo]
    t += ["", mood, ""]
    if flight:
        t.append(f"Flug (gebucht): {eur(flight, 2)} · Gesamt Flug + Hotel: {eur(grand)}")
    if top:
        t += ["", "Top-Anbieter heute:"] + [f"  {i}. {x['name']}: {eur(x['price'])}  {x['url']}"
                                            for i, x in enumerate(top, 1)]
    if hotel.get("deep_link"):
        t += ["", f"Zum Angebot: {hotel['deep_link']}"]
    text = "\n".join(t)

    # --- HTML ---
    def row(i, x):
        link = (f'<a href="{escape(x["url"])}" style="color:#0f766e;text-decoration:none">'
                f'{escape(x["name"])}</a>') if x["url"] else escape(x["name"])
        return (f'<tr><td style="padding:8px 0;border-top:1px solid #eef0f2;color:#6b7280;width:24px">{i}</td>'
                f'<td style="padding:8px 0;border-top:1px solid #eef0f2">{link}</td>'
                f'<td style="padding:8px 0;border-top:1px solid #eef0f2;text-align:right;font-weight:600">'
                f'{eur(x["price"])}</td></tr>')

    combo_html = "".join(f'<li style="margin:4px 0">{escape(c)}</li>' for c in combo)
    top_html = "".join(row(i, x) for i, x in enumerate(top, 1))
    stats = []
    if low:
        stats.append(f"Tiefstwert bisher: <b>{eur(low)}</b>")
    if high:
        stats.append(f"Höchstwert: <b>{eur(high)}</b>")
    if trend.get("avg"):
        stats.append(f"Ø der letzten Checks: <b>{eur(trend['avg'])}</b>")
    cta = (f'<a href="{escape(hotel["deep_link"])}" style="display:inline-block;background:#0f766e;'
           f'color:#fff;text-decoration:none;padding:12px 22px;border-radius:10px;font-weight:600">'
           f'Zum Angebot</a>') if hotel.get("deep_link") else ""
    flight_html = (f'<p style="margin:0;color:#374151">✈️ Flug (gebucht): <b>{eur(flight, 2)}</b> · '
                   f'Flug + Hotel zusammen: <b>{eur(grand)}</b></p>') if flight else ""

    html = f"""<!doctype html><html><body style="margin:0;background:#f3f4f6;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#111827">
<div style="max-width:560px;margin:0 auto;padding:24px 16px">
  <div style="background:linear-gradient(135deg,#0f766e,#0e7490);border-radius:16px 16px 0 0;padding:26px 28px;color:#fff">
    <div style="font-size:13px;opacity:.85">Guten Morgen · {today:%d.%m.%Y}</div>
    <div style="font-size:21px;font-weight:700;margin-top:4px">Santiburi Koh Samui</div>
    <div style="font-size:14px;opacity:.9;margin-top:2px">{_date(ci)} – {_date(co)} 2027 · {persons} Personen · {nights} Nächte</div>
  </div>
  <div style="background:#fff;padding:28px;border-radius:0 0 16px 16px">
    <div style="font-size:13px;color:#6b7280;text-transform:uppercase;letter-spacing:.04em">Günstigster Tarif aktuell</div>
    <div style="font-size:40px;font-weight:800;line-height:1.15;margin:4px 0">{eur(hotel_total)}</div>
    <div style="color:#374151">≈ <b>{eur(pp)}</b> pro Person · <b>{eur(pn)}</b> pro Person und Nacht<br>
    <span style="color:#6b7280;font-size:13px">inkl. Steuern und Gebühren</span></div>
    <p style="margin:18px 0 6px;padding:12px 14px;background:#f0fdfa;border-radius:10px;color:#115e59">{escape(mood)}</p>
    <div style="margin-top:18px;font-weight:600">So setzt sich der Preis zusammen</div>
    <ul style="margin:8px 0 0;padding-left:20px;color:#374151">{combo_html}</ul>
    <div style="margin:22px 0">{cta}</div>
    {flight_html}
    <div style="margin-top:22px;font-weight:600">Die besten Anbieter heute</div>
    <table style="width:100%;border-collapse:collapse;font-size:14px;margin-top:6px">{top_html}</table>
    <p style="margin:20px 0 0;font-size:13px;color:#6b7280">{' · '.join(stats)}</p>
  </div>
  <p style="text-align:center;font-size:12px;color:#9ca3af;margin-top:14px">Urlaub-Tracker · täglich 9 Uhr · Preise ohne Gewähr</p>
</div></body></html>"""
    return subject, html, text


def send_daily_digest(session, cfg: Config, sec: Secrets) -> bool:
    from .queries import latest_run

    if latest_run(session) is None:
        log.info("Digest uebersprungen: noch keine Messung")
        return False
    subject, html, text = build_digest(session, cfg)
    return send_email(sec, subject, text, html=html)


if __name__ == "__main__":
    # python -m app.digest          -> Vorschau (Betreff + Text)
    # python -m app.digest --send   -> echt per SMTP verschicken
    import sys

    from .config import get_config, get_secrets
    from .database import get_session

    sess = get_session()
    if "--send" in sys.argv:
        print("gesendet" if send_daily_digest(sess, get_config(), get_secrets()) else "NICHT gesendet")
    else:
        subj, _html, txt = build_digest(sess, get_config())
        print(subj)
        print()
        print(txt)
