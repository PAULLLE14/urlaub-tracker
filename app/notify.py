"""Benachrichtigungen: E-Mail (SMTP) und Push (ntfy.sh).

Alarm nur, wenn ein Kategoriepreis eine konfigurierte Schwelle unterschreitet
UND seit dem letzten Alarm dieser Kategorie die Cooldown-Zeit vergangen ist
(``alerts.cooldown_hours``). Kanaele haengen an den .env-Credentials -
fehlt SMTP/ntfy-Config, wird der Kanal still uebersprungen.
"""
from __future__ import annotations

import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import httpx
from sqlalchemy import select

from .config import Config, Secrets
from .logging_setup import get_logger
from .models import AlertLog

log = get_logger("notify")

_CATEGORY_LABEL = {
    "flight_total": "Flug (Hin+Rueck, 8 Pax)",
    "hotel_total": "Hotel (3 Villen x 14 Naechte)",
    "separate_total": "Einzelbuchung (Flug + Hotel)",
    "package_total": "Pauschalreise",
}


def _send_email(sec: Secrets, subject: str, body: str) -> bool:
    if not (sec.smtp_host and sec.alert_email_to):
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sec.smtp_from
    msg["To"] = ", ".join(sec.alert_email_to)
    try:
        with smtplib.SMTP(sec.smtp_host, sec.smtp_port, timeout=20) as s:
            if sec.smtp_starttls:
                s.starttls()
            if sec.smtp_user:
                s.login(sec.smtp_user, sec.smtp_password or "")
            s.sendmail(sec.smtp_from, sec.alert_email_to, msg.as_string())
        log.info("E-Mail-Alarm an %s gesendet", sec.alert_email_to)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("E-Mail-Versand fehlgeschlagen: %s", exc)
        return False


def _send_ntfy(sec: Secrets, title: str, body: str) -> bool:
    if not sec.ntfy_topic:
        return False
    url = f"{sec.ntfy_url.rstrip('/')}/{sec.ntfy_topic}"
    headers = {"Title": title.encode("ascii", "replace").decode(), "Priority": "high",
               "Tags": "money_with_wings"}
    if sec.ntfy_token:
        headers["Authorization"] = f"Bearer {sec.ntfy_token}"
    try:
        r = httpx.post(url, data=body.encode("utf-8"), headers=headers, timeout=15)
        r.raise_for_status()
        log.info("ntfy-Alarm an %s gesendet", url)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("ntfy-Versand fehlgeschlagen: %s", exc)
        return False


def _recent_alert(session, category: str, cooldown_h: float) -> AlertLog | None:
    since = datetime.now(timezone.utc) - timedelta(hours=cooldown_h)
    return session.scalars(
        select(AlertLog).where(AlertLog.category == category, AlertLog.sent_at >= since)
        .order_by(AlertLog.sent_at.desc())
    ).first()


def maybe_alert(session, cfg: Config, sec: Secrets, snapshot_values: dict,
                verdict: dict) -> list[str]:
    """Prueft alle Schwellen und verschickt ggf. Alarme. Returns Log-Zeilen."""
    if not cfg.alerts.enabled:
        return []
    out: list[str] = []
    thresholds = cfg.alerts.thresholds.model_dump()

    for category, limit in thresholds.items():
        if limit is None:
            continue
        current = snapshot_values.get(category)
        if current is None or current <= 0 or current > limit:
            continue

        prev = _recent_alert(session, category, cfg.alerts.cooldown_hours)
        if prev and prev.price <= current:
            # schon alarmiert und nicht guenstiger geworden -> Cooldown
            continue

        label = _CATEGORY_LABEL.get(category, category)
        cur = cfg.trip.currency
        title = f"Preisalarm {label}: {current:.0f} {cur}"
        body = (
            f"{cfg.trip.label}\n\n"
            f"{label}: {current:.2f} {cur}  (Schwelle {limit:.0f} {cur})\n"
            f"Guenstiger: {'Pauschalreise' if verdict.get('winner') == 'pauschalreise' else 'Einzelbuchung'}\n"
            f"Flug gesamt:  {verdict.get('flight_total')}\n"
            f"Hotel gesamt: {verdict.get('hotel_total')}\n"
            f"Einzelbuchung: {verdict.get('separate_total')}\n"
            f"Pauschalreise: {verdict.get('package_total')}\n\n"
            f"Erfasst: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}\n"
        )

        channels = []
        if "email" in cfg.alerts.channels and _send_email(sec, title, body):
            channels.append("email")
        if "ntfy" in cfg.alerts.channels and _send_ntfy(sec, title, body):
            channels.append("ntfy")

        session.add(AlertLog(category=category, price=current,
                             channel=",".join(channels) or "none",
                             detail=title))
        line = f"ALERT {category}={current:.0f} -> {channels or 'kein Kanal konfiguriert'}"
        out.append(line)
        log.info(line)

    if out:
        session.commit()
    return out
