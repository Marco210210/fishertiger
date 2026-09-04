#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/home/ubuntu/progetti/astafanta-support
cd "$APP_DIR"

git fetch origin main
git pull --ff-only origin main
.venv/bin/pip install --disable-pip-version-check -q -r requirements.txt

cd web
if ! cmp -s package-lock.json node_modules/.astafanta-package-lock.json; then
    npm ci --prefer-offline --no-audit --no-fund
    cp package-lock.json node_modules/.astafanta-package-lock.json
fi
npm run build

sudo systemctl restart astafanta-support
for attempt in 1 2 3 4 5; do
    status="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8092/ || true)"
    if [ "$status" = "401" ]; then
        echo "AstaFanta Support aggiornato e attivo."
        exit 0
    fi
    sleep 1
done

echo "Il servizio non ha superato il controllo locale." >&2
sudo systemctl status astafanta-support --no-pager
exit 1
