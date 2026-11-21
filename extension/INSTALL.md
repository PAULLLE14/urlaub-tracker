# Preis-Helfer (Chrome-Erweiterung)

Läuft unsichtbar im Hintergrund. Sobald du auf Google Hotels bei **Santiburi** bist
(15.–28. Mai 2027, 2 oder 3 Gäste), liest sie die Anbieterpreise aus der Seite und schickt
sie an den Tracker (Hetzner). Der nächste Lauf rechnet sie in die günstigste Kombination
ein (Quelle: „Name (Browser)"). Es gibt kein Symbol-Popup, keine Meldung, keine Oberfläche.

Gesendet werden nur: Anbietername, Preis pro Nacht, Gesamtpreis, Anbieter-Link, Zeitraum
und Gästezahl. Keine Cookies, kein Google-Konto, keine anderen Seiten.

## Installieren (einmalig)

1. In Chrome `chrome://extensions` öffnen.
2. Oben rechts **Entwicklermodus** einschalten.
3. **Entpackte Erweiterung laden** und den Ordner `extension` aus dem Projekt wählen.

Fertig. Die Datei `config.js` enthält Adresse und Zugangs-Token des Trackers (liegt nur
auf deinem PC und ist nicht im Git).

## Benutzen

Einfach auf Google Hotels gehen (zum Beispiel über den Knopf „Google Hotels" im Dashboard),
Datum und Gäste so einstellen wie gewünscht und die Seite ein paar Sekunden offen lassen.
Preise, die älter als 24 Stunden sind, zählt der Tracker nicht mehr.

## Falls etwas nicht ankommt

- Die Seite muss „Santiburi" im Titel haben und das Datum 15.–28. Mai 2027 zeigen.
- Der Tab muss im Vordergrund sein (Chrome pausiert Hintergrund-Tabs).
- Ändert Google den Seitenaufbau, muss `content.js` angepasst werden.
