# 3D-Modelle (optional)

Lege hier echte `.glb`-Dateien ab, dann benutzt die Startseite sie automatisch
(Draco-Kompression wird unterstuetzt). Fehlt eine Datei, zeichnet die Seite ein
eingebautes Modell.

| Datei                | Inhalt                         |
|----------------------|--------------------------------|
| `b787-qatar.glb`     | Boeing 787-9 (Qatar Airways)   |
| `a380-qatar.glb`     | Airbus A380-800                |
| `a319-bangkok.glb`   | Airbus A319 (Bangkok Airways)  |
| `santiburi-villa.glb`| Deluxe Garten Villa            |

Hinweise
- Groesse und Schwerpunkt werden beim Laden automatisch normiert (`Box3`).
- Bei neutralen Flugzeugen faerbt die Seite Leitwerk und Streifen nach Materialnamen
  (`tail`, `fin`, `rudder`, `stripe`, ...) in Qatar-Burgund bzw. Bangkok-Tuerkis ein.
- Bei der Villa gelten Meshes mit `roof`/`dach`/`thatch` im Namen (oder aus dem oberen
  Drittel der Bounding-Box) als Dach fuer den Knopf "Dach oeffnen".
- Auf Lizenzen achten (Sketchfab/Poly: CC-BY braucht Namensnennung).
