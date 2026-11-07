#!/usr/bin/env bash
# Подготовка чистого сервера Ubuntu 22.04/24.04 под бота.
# Запускать от root:  bash setup-server.sh
set -euo pipefail

REPO="${REPO:-https://github.com/MichaelMamoyan/telegram-traffic-filter-bot.git}"
APP_DIR="${APP_DIR:-/opt/filterbot}"
SWAP_SIZE="${SWAP_SIZE:-2G}"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

if [[ $EUID -ne 0 ]]; then
    echo "Запускать от root: sudo bash $0" >&2
    exit 1
fi

log "Обновляю систему"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq

log "Ставлю базовые пакеты"
apt-get install -y -qq git curl ufw cron

# Swap: у облачных провайдеров его обычно нет, а на машине с 1-2 ГБ он
# страхует от падения сборки образа и редких пиков потребления.
if ! swapon --show | grep -q .; then
    log "Создаю swap на $SWAP_SIZE"
    fallocate -l "$SWAP_SIZE" /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    sysctl -q vm.swappiness=10
    grep -q 'vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf
else
    log "Swap уже есть, пропускаю"
fi

if ! command -v docker >/dev/null; then
    log "Ставлю Docker"
    curl -fsSL https://get.docker.com | sh >/dev/null
    systemctl enable --now docker
else
    log "Docker уже установлен"
fi

# Наружу открыт только SSH: бот работает через polling и входящих
# соединений не принимает, Postgres и Redis доступны лишь внутри Docker.
log "Настраиваю файрвол (наружу только SSH)"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow OpenSSH >/dev/null
ufw --force enable >/dev/null

log "Забираю код в $APP_DIR"
if [[ -d "$APP_DIR/.git" ]]; then
    git -C "$APP_DIR" pull --ff-only
else
    git clone --quiet "$REPO" "$APP_DIR"
fi

mkdir -p "$APP_DIR/secrets" /root/backups

if [[ ! -f "$APP_DIR/.env" ]]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    log "Создан $APP_DIR/.env — заполните BOT_TOKEN и ADMIN_IDS, затем:"
    echo "    cd $APP_DIR && docker compose up -d --build"
else
    log "Поднимаю контейнеры"
    cd "$APP_DIR"
    docker compose up -d --build
fi

# Ежедневный дамп базы в 04:00, хранить 14 последних.
log "Ставлю ежедневные резервные копии базы"
cat > /usr/local/bin/filterbot-backup <<'CRON'
#!/usr/bin/env bash
set -euo pipefail
cd /opt/filterbot
mkdir -p /root/backups
docker compose exec -T postgres pg_dump -U bot filterbot \
    | gzip > "/root/backups/filterbot_$(date +%F_%H%M).sql.gz"
# Чистим старые, оставляя две недели.
find /root/backups -name 'filterbot_*.sql.gz' -mtime +14 -delete
CRON
chmod +x /usr/local/bin/filterbot-backup
# `|| true` обязателен: при set -e пустой crontab возвращает ошибку и
# обрывает подоболочку до записи новой строки.
{ crontab -l 2>/dev/null || true; } | grep -v filterbot-backup > /tmp/cron.tmp || true
echo "0 4 * * * /usr/local/bin/filterbot-backup" >> /tmp/cron.tmp
crontab /tmp/cron.tmp
rm -f /tmp/cron.tmp

log "Готово"
echo "Логи:     cd $APP_DIR && docker compose logs -f bot"
echo "Бэкап:    /usr/local/bin/filterbot-backup"
echo "Копии:    /root/backups"
