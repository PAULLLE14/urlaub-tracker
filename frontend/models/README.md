# 3D-Modelle der Maschinen

Die Startseite zeigt echte 3D-Modelle (`.glb`) mit Googles `<model-viewer>`. Lege die Dateien
**genau so benannt** in diesen Ordner, dann erscheinen sie automatisch (Draco-Kompression geht):

| Datei               | Maschine                          |
|---------------------|-----------------------------------|
| `qatar-b787.glb`    | Boeing 787-9 (Qatar Airways)      |
| `qatar-a380.glb`    | Airbus A380-800 (Qatar Airways)   |
| `bangkok-a319.glb`  | Airbus A319 (Bangkok Airways)     |

Solange eine Datei fehlt, steht an der Stelle ein Hinweis statt eines Bilds.

## Freie Kandidaten (Sketchfab, Lizenz CC BY, Namensnennung nötig)

Download geht nur mit kostenlosem Sketchfab-Konto: Seite öffnen, **Download 3D model**, Format
**glTF** (enthält `.glb`) wählen, Datei umbenennen und hier ablegen.

- **A380 (Qatar-Lackierung):**
  [A380 qatar Airways](https://sketchfab.com/3d-models/none-fbc6580348874d849bf410d1eb2f4e2f) (68.000 Flächen)
  oder [Qatar Airways 380](https://sketchfab.com/3d-models/none-006924d0c082403a85d71e133783c299)
- **Boeing 787-9:**
  [Boeing 787-9](https://sketchfab.com/3d-models/none-b6711e2e698e4e469675c1154a50b7a3) (9.500 Flächen, leicht),
  detailreicher: [Boeing-787-Dreamliner](https://sketchfab.com/3d-models/none-524f485c748146b7968e0dd8eb84e786) (204.000 Flächen, schwer)
- **A319:**
  [Airbus A319-100](https://sketchfab.com/3d-models/none-edd33a7bc5724a8190fcb8d1ee528885) (39.700 Flächen)
  oder [A319 plane](https://sketchfab.com/3d-models/none-8cc47f95310b4932b7cf71ecb4436811) (11.000 Flächen)

Neutrale Modelle bekommen automatisch Qatar-Burgund bzw. Bangkok-Türkis auf Leitwerk und Streifen
(Materialnamen mit `tail`, `fin`, `rudder`, `stripe`, `belly`, `livery`).

Tipp: Große Dateien (über 10 MB) vorher in Blender oder mit `gltf-transform optimize` komprimieren,
sonst lädt die Seite auf dem Handy langsam.

## Namensnennung (Pflicht bei CC BY)

Trage jedes verwendete Modell in `CREDITS.json` ein, die Seite zeigt es unten bei den Nachweisen:

```json
[
  { "name": "A380 qatar Airways", "author": "Name des Autors", "license": "CC BY 4.0",
    "url": "https://sketchfab.com/3d-models/..." }
]
```
