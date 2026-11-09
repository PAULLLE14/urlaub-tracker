"""Taegliche Zusammenfassung per E-Mail ("Guten Morgen, so steht es um Santiburi").

Wird vom Scheduler (Worker) einmal am Tag zur konfigurierten Uhrzeit
(``alerts.daily_digest``) verschickt. ``build_digest`` ist reine Darstellung
auf den bereits gespeicherten Daten und damit gut testbar. Layout: Vorlage
"Tropisch-Luxurioes" (verschachtelte Tabellen + Inline-CSS fuer Mail-Clients).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .config import Config, Secrets
from .logging_setup import get_logger
from .logic.trends import history_series
from .models import HotelOfferRow
from .notify import send_email
from .queries import latest_run, summary_payload

log = get_logger("digest")

HEADER_IMAGE = Path(__file__).parent / "assets" / "digest_header.jpg"

GREEN, GOLD, CREAM = "#1B3B36", "#C4A468", "#F3EFE4"
SERIF = "'Playfair Display',Georgia,'Times New Roman',serif"
SANS = "'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif"

_MONTHS = ["", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
           "August", "September", "Oktober", "November", "Dezember"]


def eur(x: float | None, decimals: int = 0) -> str:
    if x is None:
        return "–"
    s = f"{x:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"


def _date(iso: str) -> str:
    _y, m, d = (int(p) for p in iso.split("-"))
    return f"{d}. {_MONTHS[m]}"


def _provider(source: str) -> str:
    return source.replace(" (Google)", "")


def _top_providers(session, run_id: int | None, limit: int = 5) -> list[dict]:
    if not run_id:
        return []
    rows = session.scalars(select(HotelOfferRow).where(HotelOfferRow.run_id == run_id))
    best: dict[str, tuple] = {}
    for r in rows:
        if r.source.startswith("Mix:"):
            continue   # steht schon als Kombination oben
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


def _tr(inner: str, style: str = "") -> str:
    return f'<tr><td style="{style}">{inner}</td></tr>'


def build_digest(session, cfg: Config) -> tuple[str, str, str]:
    """Liefert (Betreff, HTML, Klartext)."""
    s = summary_payload(session, cfg)
    verdict = s.get("verdict") or {}
    hotel = verdict.get("hotel") or {}
    totals = s.get("totals") or {}
    hotel_total = totals.get("hotel_total")
    persons = cfg.trip.persons
    nights = cfg.trip.nights
    ci = hotel.get("checkin") or cfg.trip.hotel_checkin.isoformat()
    co = hotel.get("checkout") or cfg.trip.hotel_checkout.isoformat()

    hist = history_series(session, cfg)
    values = [v for v in hist["hotel_total"] if v]
    low = min(values) if values else None
    day = _change_since(hist["t"], hist["hotel_total"], 20)
    trend = (s.get("trends") or {}).get("hotel_total") or {}

    # Wie setzt sich der Preis zusammen?
    combo: list[str] = []
    split = hotel.get("room_split") or {}
    per_size = hotel.get("per_room_size") or {}
    for size in sorted(split, key=int, reverse=True):
        info = per_size.get(size) or {}
        prov = info.get("provider") or _provider(hotel.get("source", ""))
        line = f"{split[size]} × {size}er-Zimmer bei {prov}"
        if info.get("cheapest"):
            line += f" – je {eur(info['cheapest'])}"
        combo.append(line)
    if not combo and hotel:
        combo.append(f"Alle Zimmer bei {_provider(hotel.get('source', ''))}")

    pp = hotel_total / persons if hotel_total else None
    pn = hotel_total / persons / nights if hotel_total else None
    flight = totals.get("flight_total")
    grand = totals.get("separate_total")
    top = _top_providers(session, s.get("run_id"))
    link = hotel.get("deep_link") or ""

    # Stimmungszeile: (fett, Rest)
    if day is None:
        lead, rest = "Erste Messung:", "ab morgen sehe ich Veränderungen."
    elif day < -1:
        lead, rest = "Gute Nachricht:", f"Seit gestern ist es um {eur(abs(day))} günstiger geworden."
    elif day > 1:
        lead, rest = "Achtung:", f"Seit gestern ist es um {eur(day)} teurer geworden."
    else:
        lead, rest = "Stabil:", "Der Preis ist seit gestern unverändert."
    if low and hotel_total and hotel_total <= low:
        rest += " Das ist der bisher niedrigste Preis seit Messbeginn!"
    elif trend.get("label"):
        rest += f" Einordnung: {trend['label'].replace('guenstig', 'günstig')}."
    mood = f"{lead} {rest}"

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
        t.append(f"Flug (gebucht): {eur(flight, 2)} · Flug + Hotel zusammen: {eur(grand)}")
    if top:
        t += ["", "Die besten Anbieter heute:"] + [
            f"  {i}. {x['name']}: {eur(x['price'])}" for i, x in enumerate(top, 1)]
    if link:
        t += ["", "Zum Angebot: Link in der HTML-Ansicht dieser Mail oder im Dashboard."]
    if cfg.alerts.daily_digest.dashboard_url:
        t += [f"Zum Tracker: {cfg.alerts.daily_digest.dashboard_url}"]
    text = "\n".join(t)

    # --- HTML ---
    if day is not None and day < -1:
        bg, fg, icon = "#E3F4E8", "#1E6B3A", "↗"
    elif day is not None and day > 1:
        bg, fg, icon = "#FDE8E8", "#B3261E", "↗"
    else:
        bg, fg, icon = "#F1F1EE", "#555555", "•"
    banner = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
              f'<td style="background:{bg};color:{fg};border-radius:10px;padding:11px 14px;font:14px/1.45 {SANS};'
              f'text-align:center">{icon}&nbsp; <b>{escape(lead)}</b> {escape(rest)}</td></tr></table>')

    img = ('<img src="cid:header" width="600" alt="Santiburi Koh Samui" '
           'style="display:block;width:100%;max-width:600px;height:auto;border:0">')
    if link:
        img = f'<a href="{escape(link)}">{img}</a>'

    combo_html = "".join(
        f'<div style="font:14px/1.6 {SANS};color:#222222">{escape(c)}</div>' for c in combo)
    button = (f'<a href="{escape(link)}" style="display:block;background:{GREEN};color:#FFFFFF;border-radius:10px;'
              f'padding:15px 20px;font:bold 17px {SANS};text-align:center;text-decoration:none">Zum Angebot</a>'
              ) if link else ""
    tracker = cfg.alerts.daily_digest.dashboard_url
    if tracker:
        button += (f'<a href="{escape(tracker)}" style="display:block;margin-top:10px;background:#FFFFFF;color:{GREEN};'
                   f'border:2px solid {GREEN};border-radius:10px;padding:13px 20px;font:bold 16px {SANS};'
                   f'text-align:center;text-decoration:none">Zum Tracker</a>')
    flight_html = (f'<div style="padding:12px 0 14px;font:12px/1.5 {SANS};color:#555555;text-align:center;'
                   f'border-bottom:1px solid #E3E3DE">Flug (gebucht): {eur(flight, 2)} · '
                   f'Flug + Hotel zusammen: {eur(grand)}</div>') if flight else ""

    def prov_row(x: dict) -> str:
        name = (f'<a href="{escape(x["url"])}" style="color:#222222;text-decoration:none">{escape(x["name"])}</a>'
                if x["url"] else escape(x["name"]))
        return (f'<tr><td align="left" style="padding:4px 0;font:14px {SANS};color:#222222;text-align:left">{name}</td>'
                f'<td align="right" style="padding:4px 0;font:bold 14px {SANS};color:{GREEN}">{eur(x["price"])}</td></tr>')

    top_html = "".join(prov_row(x) for x in top)

    html = f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"></head>
<body style="margin:0;padding:0;background:#E6E2D8">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#E6E2D8"><tr><td align="center" style="padding:16px 8px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#FFFFFF">
{_tr(f'Guten Morgen · {today:%d.%m.%Y}', f"background:{GREEN};color:{GOLD};font:12px {SANS};letter-spacing:.06em;text-align:center;padding:11px")}
{_tr(img, "padding:0;line-height:0")}
{_tr(f'{_date(ci)} – {_date(co)} {co[:4]} · {persons} Personen · {nights} Nächte',
     f"background:{CREAM};padding:13px 16px;font:14px {SANS};color:#222222;text-align:center")}
<tr><td style="padding:22px 28px 0;text-align:center">
  <div style="font:bold 46px/1.1 {SERIF};color:{GREEN}">{eur(hotel_total)}</div>
  <div style="font:15px/1.5 {SANS};color:#222222;padding-top:4px">Günstigster Tarif aktuell</div>
  <div style="font:11px/1.5 {SANS};color:#666666;padding:2px 0 14px">≈ {eur(pp)} pro Person · {eur(pn)} pro Person und Nacht · inkl. Steuern und Gebühren</div>
  {banner}
  <div style="font:bold 16px {SANS};color:#222222;padding:22px 0 6px">So setzt sich der Preis zusammen</div>
  {combo_html}
  <div style="padding:18px 0 4px">{button}</div>
  {flight_html}
  <div style="font:bold 16px {SANS};color:#222222;padding:16px 0 8px">Die besten Anbieter heute</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:0 6px">{top_html}</table>
</td></tr>
<tr><td style="padding:18px 0 0"><div style="height:2px;background:{GOLD};opacity:.7;line-height:2px;font-size:0">&nbsp;</div></td></tr>
{_tr('Urlaub-Tracker · täglich 9 Uhr · Preise ohne Gewähr', f"background:{GREEN};color:#E9DFC4;font:12px {SANS};text-align:center;padding:14px")}
</table></td></tr></table></body></html>"""
    return subject, html, text


def send_daily_digest(session, cfg: Config, sec: Secrets) -> bool:
    if latest_run(session) is None:
        log.info("Digest uebersprungen: noch keine Messung")
        return False
    subject, html, text = build_digest(session, cfg)
    return send_email(sec, subject, text, html=html, inline_images={"header": HEADER_IMAGE})


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
