# n8n NodeRunner CLI

Ein Terminal-Client für das **NodeRunner**-Minigame vom **n8n In The Loop** in Berlin
(13. und 14. Oktober 2026). Entstanden während des Events, um das Minigame schneller
zu absolvieren: Identität anzeigen, Level wählen, Zielzeit eingeben, fertig.

> **Disclaimer**
> Der Code und die Oberfläche sind auf **Deutsch**. Das Projekt wurde mithilfe von
> **Claude Sonnet 5** erstellt. Es ist ein Hackathon-Spaßprojekt und steht in keiner
> offiziellen Verbindung zu n8n.

---

## Features

- **Identitäts-Panel** beim Start: Name, ID, Provider, Fortschritt, verbleibende Codes
  und eine Tabelle aller Level mit Bestzeit, Versuchen und Status
- **Levelauswahl per Pfeiltasten** (oder `j` / `k`, Ziffern als Direktsprung, `q` zum Beenden)
- **Zeitgesteuerter Run**: Zeit im Format `mm:ss:ms` eingeben, der Client startet den Run
  und schickt `complete` exakt zum Zielzeitpunkt (verankert an `t0` des Servers, nicht an
  der lokalen Uhr)
- **Präziser Scheduler**: Sendeabweichung im Bereich von ±0,01 ms, weil das letzte
  Millisekundenfenster aktiv gespinnt wird
- **Leaderboard im Terminal**: Overall plus jedes Level einzeln, mit Blättern per ←/→
  und grün markierter eigener Zeile (`<- du`)
- **Live-Countdown** und eine übersichtliche Ergebnisanzeige mit Bestzeit und Rang
- **Request-Treue**: Header in exakter Reihenfolge, HTTP/1.1 und iOS-Safari-TLS-Fingerprint
  über `curl_cffi`
- **Token-Check**: erkennt das zweiteilige Token-Format des Spiels und warnt, bevor eine
  Session abläuft
- **Dry-Run und Verbose-Modus**, um Requests zu prüfen, ohne etwas zu senden
- **95 Tests** gegen einen lokalen Mock-Server, inklusive echter Pfeiltasten über ein pty

---

## So sieht es aus

### Start: Identität und Fortschritt

```
========================================================================
node-runner
========================================================================
  Name          Max Mustermann
  ID            c2:00000000-0000-0000-0000-000000000000
  Provider      c2
  Trust         verified
------------------------------------------------------------------------
  Fortschritt   2/7 Level abgeschlossen
  naechstes     Level 2
  Codes         3 von 10 uebrig

   0  How to play             best 01:15:298  3 Versuche   fertig
   1  Autonomous agent        best 00:15:210  4 Versuche   fertig
   2  Human in the loop       5 Versuche   offen  als naechstes
   3                          5 Versuche   gesperrt
------------------------------------------------------------------------
  Session       gespeichertes Profil -- gueltig noch 9.8 h
```

### Levelauswahl

Die aktive Zeile ist invertiert hervorgehoben, ganz unten liegt der Eintrag zum Leaderboard.

```
========================================================================
Levelauswahl
========================================================================
Level waehlen:
  > Level 0  How to play         best 01:15:298
    Level 1  Autonomous agent    best 00:15:210
    Level 2  Human in the loop
    Level 3
    Level 4
    Level 5
    Level 6
    Leaderboard
  (arrows / enter, q to quit)
```

### Level-Panel und Run

```
========================================================================
Level 1
========================================================================
  Start sendet POST /api/run/start, wartet die Zeit ab und sendet
  dann POST /api/run/complete mit exakt dieser clientMs.

Aktion:
  > Start
    Zeit eingeben und Start
    Zurueck

Zeit (mm:ss:ms) [01:15:298]: 00:15:200
  =             15200 ms
  ok  runToken erhalten  eyJhbGciOiJIUzI1NiJ9.eyJy...

  Zielzeit      00:15:200
  Anker         server t0 = 14:02:11 UTC
  Wartezeit     15.071 s
Jetzt starten und nach 00:15:200 stoppen? [Y/n]:
  t-   9.4s
  -> sende complete  (Sendeabweichung +0.01 ms)
```

### Ergebnis

```
========================================================================
Ergebnis
========================================================================
  ok  status = recorded
  Dauer         00:15:212
  Bestzeit      00:15:212
  neue Bestzeit ja
  level         1
  attemptsLeft  3
  rank          4
  rankedOutOf   165
```

### Leaderboard

```
========================================================================
Leaderboard -- Overall
========================================================================
Overall
  Total of every best time.
    1.  Erika M.         4:58.520
    2.  Max Mustermann   5:26.789  <- du

  Du bist auf Platz 2 (5:26.789).

  (q schliessen)
```

---

## Installation

Python 3.10 oder neuer.

```bash
git clone git@github.com:oliv5r/n8n-NodeRunner.git
cd n8n-NodeRunner
pip install curl_cffi
```

## Session-Token

Im Repo ist **kein Token** enthalten. Den eigenen Token findest du in der Launch-URL des
Spiels (`/play/<n>?s=<token>`) bzw. im Browser unter `sessionStorage["itl.session"]`.

Es gibt drei Wege, ihn zu übergeben:

```bash
# 1. direkt als Argument
python3 itl.py --jwt '<token>'

# 2. über die komplette Launch-URL
python3 itl.py --url 'https://node-runner-gilt.vercel.app/play/0?s=<token>'

# 3. über eine Umgebungsvariable
export ITL_SESSION='<token>'
python3 itl.py
```

Ein per `--jwt` oder `--url` übergebener Token wird in `~/.itl_session.json` (Modus 600)
gespeichert und beim nächsten Start automatisch verwendet.
Reihenfolge: `--jwt` / `--url`, dann gespeichertes Profil, dann `$ITL_SESSION`.

## Benutzung

```bash
python3 itl.py                                  # Menü: Identität, Level, Zeit, Start
python3 itl.py --leaderboard                    # Rangliste direkt öffnen
python3 itl.py --check                          # nur Identität anzeigen
python3 itl.py --level 2 --time 01:15:298 --yes # ohne Menü, direkt los
python3 itl.py --dry-run                        # Requests anzeigen, nichts senden
python3 itl.py -v                               # jeden Request und jede Response loggen
```

### Zeitformat

`mm:ss:ms`, wobei das letzte Feld ein Sekundenbruchteil ist (`5`, `50` und `500` sind
jeweils 500 ms). `01:15:298` entspricht 75298 ms. Auch `00:01` und `75.298` werden
akzeptiert.

### Steuerung

| Taste | Aktion |
|---|---|
| ↑ / ↓ oder `k` / `j` | im Menü bewegen |
| Enter | auswählen |
| `1` bis `9` | direkt zu einem Eintrag springen |
| ← / → | im Leaderboard blättern |
| `q` | zurück bzw. beenden |

---

## Wie es funktioniert

1. `GET /api/me` liefert Nutzer und Level-Fortschritt
2. **Start** sendet `POST /api/run/start {"level": n}` und bekommt einen `runToken`
3. Der Client liest `t0` aus dem `runToken` und wartet bis `t0 + Zielzeit`
4. `POST /api/run/complete {"runToken": ..., "clientMs": ...}`

**Wichtig:** Die gewertete Zeit misst der Server selbst, von `t0` bis zum Eintreffen von
`/complete`. Das mitgesendete `clientMs` wird ignoriert. Deshalb zählt nur der exakte
Sendezeitpunkt plus die Netzwerklatenz. Weicht die gewertete Zeit um mehr als 50 ms von
der Zielzeit ab, zeigt die Ergebnisanzeige das an.

Das Leaderboard gibt es nur als server-gerendertes HTML (kein JSON-API). Der Client
parst das DOM, dedupliziert die Abschnitte und markiert die eigene Zeile über
`data-you="true"` bzw. ersatzweise über den Anzeigenamen.

## Tests

```bash
python3 test_itl.py
```

Alle Tests laufen gegen einen lokalen Mock-Server, es geht **kein** Traffic an den echten
Host. Abgedeckt sind Zeitparsing, Header-Reihenfolge, Token-Analyse, Request-Bodies,
Timing-Genauigkeit, Session-Auflösung, Leaderboard-Parsing und die Menüsteuerung.

## Dateien

| Datei | Inhalt |
|---|---|
| `itl.py` | Einstiegspunkt, Argumente, Session auflösen |
| `itl_app.py` | Identitätsanzeige, Level- und Play-Screens, Run-Ablauf, Leaderboard-Screen |
| `itl_client.py` | Header-Bau, API-Calls, Token-Analyse |
| `itl_leaderboard.py` | HTML-Parser für `/leaderboard` |
| `itl_time.py` | `mm:ss:ms`-Parsing und präziser Scheduler |
| `itl_ui.py` | Terminal-UI: Menüs, Prompts, Pager, Spinner |
| `itl_config.py` | Host- und Header-Konstanten |
| `test_itl.py` | Testsuite mit Mock-Server |
