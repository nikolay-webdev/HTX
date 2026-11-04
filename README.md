# HTX Telegram Bot

Python-basierter Telegram-Bot zur Analyse öffentlicher Marktdaten und zur Simulation virtueller Transaktionen.

Das Projekt demonstriert Python-Entwicklung, REST-API-Integration, Verarbeitung von JSON- und CSV-Daten, persistente Speicherung mit SQLite sowie den Betrieb eines Hintergrundprozesses unter Linux. Es handelt sich ausschließlich um eine Simulation: Es werden keine echten Orders gesendet und keine HTX-API-Schlüssel verwendet.

## Funktionen

- Abruf öffentlicher Candlestick-Daten über die HTX REST API
- historischer Backtest mit CSV-Daten
- kontinuierlicher Live-Dry-Run mit Telegram-Benachrichtigungen
- virtuelle Positionen und statistische Auswertung
- persistenter Zustand in SQLite mit Wiederherstellung nach Neustart
- kontrollierte Wiederholungen bei temporären Netzwerkfehlern
- optionaler Betrieb als systemd-Service auf einem Linux-VPS

Die vorhandene Simulationslogik dient als Beispiel für anwendungsbezogene Geschäftslogik. Das Projekt ist kein System für reale Handelsausführung und keine Aussage über finanzielle Ergebnisse.

## Technologien

| Technologie | Verwendung |
|---|---|
| Python 3.11+ | Anwendungslogik und CLI |
| Telegram Bot API | Benutzerinteraktion und Benachrichtigungen |
| HTX REST API | Abruf öffentlicher Marktdaten |
| JSON | Verarbeitung der API-Antworten |
| SQLite | Persistenter Zustand und Simulationsergebnisse |
| CSV | Import historischer Candlestick-Daten |
| Linux / systemd | Dauerbetrieb und automatischer Neustart |
| Git | Versionsverwaltung |
| unittest | Automatisierte Tests |

## Architektur

```text
Telegram-Benutzer
        ↕
Telegram Bot API
        ↕
Python-Anwendung
   ↙          ↘
HTX REST API   SQLite-Datenbank
```

Die Telegram-Schicht nimmt Befehle entgegen und sendet Antworten. Die Daten-Schicht lädt und validiert Marktdaten. Die Simulationslogik verarbeitet diese Daten, während SQLite Zustand, Positionen, Ereignisse und Ergebnisse dauerhaft speichert.

## Projektstruktur

| Pfad | Verantwortung |
|---|---|
| `htx_bot/telegram.py` | Kommunikation mit der Telegram Bot API |
| `htx_bot/data.py` | HTX-Datenabruf sowie CSV-Import und -Validierung |
| `htx_bot/strategy.py` | Simulations- und Backtestlogik |
| `htx_bot/live.py` | Laufender Simulator und Telegram-Befehle |
| `htx_bot/storage.py` | SQLite-Speicherung und Zustandsmigration |
| `htx_bot/config.py` | Konfiguration und Validierung |
| `htx_bot/cli.py` | Kommandozeilenschnittstelle |
| `htx_bot/report.py` | Formatierung der Simulationsergebnisse |
| `tests/` | Automatisierte Tests |
| `deploy/` | systemd- und VPS-Dokumentation |
| `scripts/` | Manuell auszuführende Synchronisationsskripte |

## Installation und lokaler Start

Voraussetzung ist Python 3.11 oder höher. Das Projekt verwendet ausschließlich die Python-Standardbibliothek.

```bash
git clone https://github.com/nikolay-webdev/HTX.git
cd HTX
python3 --version
```

Historischen Backtest mit einer CSV-Datei ausführen:

```bash
python3 -m htx_bot backtest --input data/ethusdt_1m.csv
```

Die letzten öffentlichen Candles laden und direkt simulieren:

```bash
python3 -m htx_bot backtest --fetch --size 2000
```

CSV-Dateien verwenden die Spalten `timestamp,open,high,low,close,volume`. Zeitstempel können in Sekunden oder als ISO-Datum vorliegen.

## Telegram-Bot

1. Über `@BotFather` einen Bot anlegen und den Token sicher aufbewahren.
2. Lokale Konfiguration anlegen:

   ```bash
   cp .env.example .env
   ```

3. `TELEGRAM_BOT_TOKEN` in `.env` eintragen.
4. Dem Bot `/start` senden und die Chat-ID ermitteln:

   ```bash
   python3 -m htx_bot telegram-info
   ```

5. `TELEGRAM_CHAT_ID` in `.env` eintragen und testen:

   ```bash
   python3 -m htx_bot telegram-test
   ```

6. Den Live-Dry-Run starten:

   ```bash
   python3 -m htx_bot live
   ```

Der Token bleibt ausschließlich in `.env`; diese Datei ist durch `.gitignore` vom Repository ausgeschlossen.

### Verfügbare Befehle

| Befehl | Funktion |
|---|---|
| `/status` | Betriebszustand, letzte Marktdaten und offene Positionen |
| `/position` | Details der offenen virtuellen Positionen |
| `/current_market_price` | Zuletzt bekannte Marktpreis-Information |
| `/stats` | Ergebnisse und statistische Kennzahlen |
| `/pause` | Neue virtuelle Einstiege pausieren |
| `/resume` | Analyse und neue Einstiege fortsetzen |
| `/help` | Hilfe anzeigen |

Befehle werden nur von der in `TELEGRAM_CHAT_ID` hinterlegten Chat-ID akzeptiert. Eine Pause beendet nicht die Überwachung bereits geöffneter Positionen.

## Datenverarbeitung und Simulation

Die Anwendung lädt öffentliche Marktdaten, validiert API-Antworten und verarbeitet abgeschlossene Candles. Die vorhandene Simulationslogik erkennt Einstiegssituationen, berechnet virtuelle Positionen und speichert Ergebnisse in SQLite. Der Live-Modus prüft den Markt regelmäßig und meldet Zustandsänderungen über Telegram.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Linux-VPS und systemd

Die Anwendung kann als systemd-Service dauerhaft auf einem Linux-VPS laufen. Unit-Datei, Konfigurationsvorlage und Betriebsdokumentation befinden sich in [`deploy/`](deploy/). Die produktive SQLite-Datenbank bleibt außerhalb von Git.

Deployment-Skripte führen externe Änderungen aus und werden nicht automatisch gestartet. Vor einem Einsatz sind Zielhost, Pfade, Backups und Auswirkungen zu prüfen.

## Sicherheit und Einschränkungen

- Telegram-Token und VPS-Zugangsdaten gehören ausschließlich in lokale, ignorierte Konfigurationsdateien.
- SQLite-Datenbanken, Backups und laufende Statistiken werden nicht in Git versioniert.
- Neue Marktdaten hängen von der Verfügbarkeit der externen API ab.
- Für Telegram-Funktionen ist ein gültiger Bot-Token erforderlich.
- Für den kontinuierlichen Live-Modus muss der Prozess oder systemd-Service laufen.
- Es werden keine echten Orders ausgeführt und keine API-Schlüssel für Handelszugriffe benötigt.

## Mögliche Weiterentwicklung

- Erweiterung der Testabdeckung für Konfigurations- und Fehlerfälle
- strukturierteres Logging und bessere Diagnose im Dauerbetrieb
- zusätzliche Resilienz bei externen API-Ausfällen
- Überwachung von Prozesszustand und Datenaktualität

## Portfolio-Kontext

Dieses Repository ist ein technisches Lern- und Portfolio-Projekt. Der Schwerpunkt liegt auf modularer Python-Entwicklung, API-Integration, Persistenz, Testbarkeit und automatisiertem Betrieb.
