# VPS-Betrieb

## Zweck

Die Anwendung kann auf einem Linux-VPS als systemd-Service laufen. Der laufende SQLite-Zustand und die Statistik werden nicht über Git synchronisiert.

Empfohlene Reihenfolge:

1. Änderungen lokal vornehmen und testen.
2. Code lokal committen und nach GitHub pushen.
3. Auf dem VPS `git pull --ff-only` ausführen.
4. Den Service kontrolliert neu starten und die Logs prüfen.

Die folgenden Schritte sind Dokumentation. Dieses Repository führt keinen VPS-Deploy automatisch aus.

## Konfiguration

```bash
cp deploy/vps.env.example deploy/vps.env
```

`deploy/vps.env` enthält Host- und Service-Daten und ist von Git ausgeschlossen. Die produktive `.env` mit dem Telegram-Token wird separat auf dem VPS verwaltet.

## systemd-Service

Nach dem Klonen des Repositorys auf dem VPS:

```bash
cd /srv/bots/htx
git pull --ff-only
mkdir -p data
chmod 700 data

sudo cp deploy/htx-bot.service /etc/systemd/system/htx-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now htx-bot
sudo systemctl status htx-bot --no-pager
```

Logs anzeigen:

```bash
journalctl -u htx-bot -f
```

## Sicherheit der Datenbank

`data/*.db` und `backups/` gehören nicht in Git. Vor dem Austausch einer produktiven Datenbank müssen beide Datenbestände gesichert und separat geprüft werden. Die Skripte zum Übertragen oder Herunterladen von Statistiken sind bewusst manuell aufzurufen.

## Hinweise

- Keine produktive Datenbank löschen oder überschreiben, ohne vorher ein Backup zu erstellen.
- Keine VPS-Skripte ausführen, wenn Zielhost oder Pfad nicht geprüft wurden.
- Für ein Portfolio-Update genügt zunächst die lokale Prüfung, ein Commit und anschließend ein normaler GitHub-Push.
