# Развёртывание на сервере

Инструкция рассчитана на чистый VPS с Ubuntu 22.04/24.04. Весь стек поднимается
в Docker: бот, PostgreSQL и Redis. Отдельно ничего устанавливать не нужно.

Минимальные требования: 1 vCPU, 1 ГБ RAM, 10 ГБ диска.

---

## ⚠️ Один токен — один запущенный бот

Telegram не разрешает двум процессам читать обновления одного бота. Если бот
уже где-то запущен (например, на машине разработчика), **его нужно остановить
перед запуском на сервере**, иначе оба будут падать с ошибкой
`Conflict: terminated by other getUpdates request`.

Либо создайте для сервера отдельного бота в [@BotFather](https://t.me/BotFather).
Тогда не забудьте добавить **нового** бота администратором во все целевые
каналы с правом «Добавление подписчиков» — инвайт-ссылки старого бота новому
не подойдут.

---

## Быстрый путь: один скрипт

На чистом сервере всё ставится автоматически:

```bash
curl -fsSL https://raw.githubusercontent.com/MichaelMamoyan/telegram-traffic-filter-bot/main/deploy/setup-server.sh | bash
```

Скрипт поставит Docker, создаст swap, закроет всё кроме SSH, заберёт код,
поднимет контейнеры и настроит ежедневные резервные копии базы.

После первого запуска нужно заполнить `/opt/filterbot/.env` (BOT_TOKEN
и ADMIN_IDS) и выполнить:

```bash
cd /opt/filterbot && docker compose up -d --build
```

Ниже — те же шаги вручную, если хочется контролировать каждый.

## 1. Установка Docker

```bash
curl -fsSL https://get.docker.com | sh
```

Проверка:
```bash
docker --version && docker compose version
```

Docker стартует вместе с сервером, а контейнеры помечены `restart: unless-stopped`,
поэтому после перезагрузки всё поднимется само.

## 2. Код

```bash
git clone https://github.com/MichaelMamoyan/telegram-traffic-filter-bot.git
cd telegram-traffic-filter-bot
```

## 3. Настройки

```bash
cp .env.example .env
nano .env
```

Обязательны две строки:

```
BOT_TOKEN=токен из @BotFather
ADMIN_IDS=ваш Telegram ID
```

Свой ID можно узнать у [@userinfobot](https://t.me/userinfobot). Несколько
админов — через запятую: `ADMIN_IDS=111,222`.

Пароль базы по умолчанию `bot/bot`. Наружу порты Postgres и Redis не
публикуются, они доступны только контейнерам, но на боевом сервере пароль
лучше заменить:

```
POSTGRES_PASSWORD=свой-пароль
DB_DSN=postgresql+asyncpg://bot:свой-пароль@postgres:5432/filterbot
```

## 4. Запуск

```bash
docker compose up -d --build
docker compose logs -f bot
```

В логах должно появиться:

```
Запускаю @ваш_бот (admins: [...])
Start polling
```

Таблицы в базе создаются автоматически при старте (Alembic).

## 5. Google Sheets (необязательно)

Без этого бот работает, выгрузка остаётся в XLSX через админку.

1. [console.cloud.google.com](https://console.cloud.google.com) → новый проект
2. Включить **Google Sheets API** (Drive API не нужен)
3. **APIs & Services → Credentials → Create credentials → Service account**,
   роль не назначать
4. У созданного аккаунта: **Keys → Add key → JSON**, скачать файл
5. Положить его на сервер как `secrets/google.json`:
   ```bash
   scp ~/Downloads/ваш-ключ.json root@СЕРВЕР:~/telegram-traffic-filter-bot/secrets/google.json
   ```
6. Создать таблицу, расшарить её на email сервисного аккаунта
   (вида `имя@проект.iam.gserviceaccount.com`) с правом **Editor**
7. В `.env` прописать ID таблицы из её адреса:
   ```
   GOOGLE_SPREADSHEET_ID=1AbC...XyZ
   ```
8. `docker compose restart bot`

Листы бот создаст сам при первой синхронизации.

---

## Эксплуатация

**Логи**
```bash
docker compose logs -f bot          # в реальном времени
docker compose logs bot --tail 200  # последние строки
```

**Перезапуск после правки `.env`**
```bash
docker compose restart bot
```

**Обновление кода**
```bash
git pull && docker compose up -d --build
```
База при этом сохраняется, миграции применяются автоматически.

**Остановить / запустить**
```bash
docker compose stop
docker compose start
```

**Резервная копия базы**
```bash
docker compose exec -T postgres pg_dump -U bot filterbot | gzip > backup_$(date +%F).sql.gz
```

Восстановление:
```bash
gunzip -c backup_2026-11-05.sql.gz | docker compose exec -T postgres psql -U bot filterbot
```

Копию стоит делать по расписанию — например, добавить строку в `crontab -e`:
```
0 4 * * * cd ~/telegram-traffic-filter-bot && docker compose exec -T postgres pg_dump -U bot filterbot | gzip > ~/backups/bot_$(date +\%F).sql.gz
```

---

## Первая настройка в боте

1. Добавить бота администратором в каждый целевой канал с правом
   **«Добавление подписчиков»**
2. В боте: `/admin` → **📢 Каналы** → **➕ Добавить канал** → переслать любой
   пост из канала
3. На карточке канала отметить сегменты и нажать **▶️ Включить канал**.
   Строка «Права бота» сразу покажет, всё ли настроено
4. **🔗 Ссылки** → создать ссылку на каждого байера
5. **👥 Байеры** → добавить байера с его Telegram ID, тогда он сможет смотреть
   свою статистику командой `/stats`

**Каналы делайте приватными.** В публичный канал можно вступить напрямую по
`@username`, минуя заявку, и такая подписка не будет учтена в статистике.

---

## Если что-то не так

| Симптом | Причина |
|---|---|
| `Conflict: terminated by other getUpdates` | бот с этим токеном уже где-то запущен |
| `Unauthorized` | опечатка в `BOT_TOKEN` |
| Админка не открывается | неверный `ADMIN_IDS`, нужен числовой ID |
| «Нет доступных каналов» у юзера | канал выключен, не заданы сегменты или у бота нет права приглашать |
| Подписки не считаются | канал публичный, либо нет права «Добавление подписчиков» |
| Sheets не пишется | таблица не расшарена на сервисный аккаунт |

Диагностика почти всегда в логах: `docker compose logs bot --tail 100`.
