# VPS deployment

## Главное правило

Код синхронизируем через Git. Статистику не синхронизируем через Git, потому что это живая SQLite-база, в которую бот постоянно пишет. Если коммитить `data/*.db`, можно получить конфликты, потерять сделки или случайно откатить серверную статистику.

Правильная схема:

1. Локально меняем код.
2. Локально коммитим и пушим код в Git.
3. VPS делает `git pull --ff-only`.
4. systemd перезапускает бота.
5. Серверная SQLite-база остаётся главным источником статистики.
6. Если статистику нужно посмотреть локально, скачиваем копию базы с VPS отдельной командой.

## Один раз настроить адрес VPS

Локально:

```bash
cp deploy/vps.env.example deploy/vps.env
```

Откройте `deploy/vps.env` и замените `SERVER_IP`:

```env
HTX_VPS_HOST=nikoshot@SERVER_IP
HTX_VPS_PATH=/srv/bots/htx
HTX_SERVICE=htx-bot
HTX_REMOTE=origin
HTX_VPS_OWNER=nikoshot:nikoshot
```

`deploy/vps.env` не попадает в Git.

## Первичная установка systemd на VPS

После того как папка `/srv/bots/htx` уже создана, репозиторий клонирован и `.env` лежит на сервере:

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

Логи:

```bash
journalctl -u htx-bot -f
```

## Обычный деплой кода

Локально после правок:

```bash
python3 -m unittest discover -s tests -v
git status --short
git add .
git commit -m "описание изменения"
scripts/deploy_code.sh
```

Скрипт делает всё нужное:

- проверяет, что локальные изменения закоммичены;
- запускает локальные тесты;
- делает `git push`;
- заходит на VPS по SSH;
- делает `git pull --ff-only`;
- запускает тесты на VPS;
- обновляет systemd unit;
- перезапускает `htx-bot`;
- показывает статус сервиса.

Если `deploy/vps.env` не создан, можно передать сервер явно:

```bash
scripts/deploy_code.sh nikoshot@SERVER_IP
```

## Перенести старую локальную статистику на VPS

Это нужно сделать один раз, если бот раньше работал локально, а после переноса на VPS `/stats` показывает нули.

Локально проверить, что старая статистика есть:

```bash
python3 - <<'PY'
import sqlite3
db = sqlite3.connect("data/eth_live_simulator.db")
trades, profit = db.execute("SELECT COUNT(*), COALESCE(SUM(net_profit), 0) FROM trades").fetchone()
positions = db.execute("SELECT COUNT(*) FROM position").fetchone()[0]
print(f"trades={trades}, profit={profit:.8f}, positions={positions}")
db.close()
PY
```

Затем заменить пустую базу на VPS локальной базой:

```bash
scripts/import_local_stats_to_vps.sh --yes
```

Если `deploy/vps.env` не создан:

```bash
scripts/import_local_stats_to_vps.sh nikoshot@SERVER_IP --yes
```

Скрипт:

- делает консистентную копию локальной SQLite-базы;
- загружает её на VPS;
- останавливает `htx-bot`;
- сохраняет старую серверную базу в `/srv/bots/htx/backups`;
- ставит локальную базу как `data/eth_live_simulator.db`;
- запускает `htx-bot` обратно.

Не используйте этот импорт, если на VPS уже накопились новые важные сделки. В таком случае сначала сохраните обе базы и объединяйте данные отдельно.

## Скачать серверную статистику локально

Когда бот уже постоянно работает на VPS, серверная база становится главной. Чтобы получить свежую копию статистики на локальный компьютер:

```bash
scripts/pull_stats_from_vps.sh
```

Если `deploy/vps.env` не создан:

```bash
scripts/pull_stats_from_vps.sh nikoshot@SERVER_IP
```

Скрипт делает backup текущей локальной базы в `backups/`, затем скачивает консистентную копию базы с VPS в `data/eth_live_simulator.db`.

## Проверить синхронизацию и работу сервиса

Локально:

```bash
scripts/check_vps_sync.sh
```

Проверка показывает:

- Git branch и commit локально;
- Git branch и commit на VPS;
- количество незакоммиченных файлов;
- статистику локальной и серверной SQLite-базы;
- включён ли `htx-bot` в автозапуск;
- работает ли `htx-bot` сейчас;
- включён ли restart policy у systemd-сервиса.

Нормальное состояние:

```text
LOCAL head = VPS head
LOCAL dirty_files = 0
VPS dirty_files = 0
VPS service_enabled = enabled
VPS service_active = active
VPS service_restart = always
```

Если серверная статистика свежее локальной, это нормально: бот работает на VPS и пишет в серверную базу. Чтобы обновить локальную копию, выполните:

```bash
scripts/pull_stats_from_vps.sh
```

## sudo без пароля только для сервиса

Чтобы `scripts/deploy_code.sh` не спрашивал пароль sudo при перезапуске сервиса, можно разрешить пользователю `nikoshot` управлять только этим сервисом.

На VPS:

```bash
command -v systemctl
sudo visudo -f /etc/sudoers.d/htx-bot
```

Если `systemctl` находится в `/usr/bin/systemctl`, добавьте:

```text
nikoshot ALL=NOPASSWD: /usr/bin/systemctl restart htx-bot, /usr/bin/systemctl status htx-bot, /usr/bin/systemctl start htx-bot, /usr/bin/systemctl stop htx-bot, /usr/bin/systemctl enable htx-bot, /usr/bin/systemctl daemon-reload
```

## Что не коммитить

- `.env`
- `deploy/vps.env`
- `data/*.db`
- `data/*.db-*`
- `backups/`
