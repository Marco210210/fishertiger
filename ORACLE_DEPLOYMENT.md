# Oracle Cloud deployment

## Server currently deployed

The live installation on the existing Oracle VM uses the host Caddy and a
systemd service, so it does not conflict with the other applications already
running on ports 80 and 443:

- public URL: `https://astafanta.130.110.16.97.sslip.io`;
- checkout: `/home/ubuntu/progetti/astafanta-support`;
- persistent data: `/var/lib/astafanta-support`;
- internal listener: `127.0.0.1:8092`;
- service: `astafanta-support.service`.

After changes have been merged and pushed to the fork, update this installation
with:

```bash
cd /home/ubuntu/progetti/astafanta-support
bash deploy/update-oracle-systemd.sh
```

The script performs a fast-forward pull, updates dependencies only when needed,
builds the web application, restarts the service and checks the protected local
endpoint. Profile and auction data are not inside the Git checkout and are not
overwritten.

## Standalone Docker alternative

The production stack runs AstaFanta Support behind Caddy and keeps profiles,
generated datasets and auction-support caches in `./runtime`. It works on an
Always Free Ampere (ARM64) VM because every image used here is multi-platform.

## 1. Prepare the VM

Create an Ubuntu 24.04 Ampere VM, attach a reserved public IP, and allow inbound
TCP 22, 80 and 443 (plus UDP 443 for HTTP/3) in both the Oracle security list
and the VM firewall. Then install Docker from Docker's official Ubuntu
repository and enable the Docker service.

## 2. Clone your fork

```bash
git clone https://github.com/Marco210210/fishertiger.git
cd fishertiger
cp oracle.env.example .env
mkdir -p runtime
chmod 700 runtime
```

Edit `.env`. `SITE_ADDRESS` can be a domain pointed at the reserved IP (Caddy
will obtain HTTPS automatically) or `http://PUBLIC_IP` while testing without a
domain. Always replace the Basic Auth password.

## 3. Start and update

```bash
docker compose --env-file .env -f compose.oracle.yaml up -d --build
docker compose --env-file .env -f compose.oracle.yaml ps
```

Future upstream/fork updates preserve runtime data:

```bash
git fetch origin
git pull --ff-only origin main
docker compose --env-file .env -f compose.oracle.yaml up -d --build
```

Back up `runtime/` periodically. It contains the private profiles and generated
league datasets and is intentionally excluded from Git.

## FantaLab

No token is required after an auction has started: **FantaLab live** can scan
the public realtime namespaces once and then reuse the discovered shard. An
optional server-side `FISHERTIGER_FANTALAB_TOKEN` makes discovery immediate and
adds official room/team names. It is never returned to the browser or logged.

The integration is deliberately read-only. AstaFanta Support cannot bid, assign a
lot or change the FantaLab room.

## Scout AI

The committed snapshot is refreshed daily from Fantacalcio.it by GitHub
Actions. To enhance it with Claude before a later auction, authenticate Claude
Code on a trusted computer and run:

```bash
python -m scripts.update_scout_ai
git add data/raw/scout_ai_2026_27.json
git commit -m "Refresh Scout AI news"
git push origin main
```

The auction never calls Claude in real time. If Claude is unavailable, the
script falls back to the official injury list and the core valuation remains
fully operational.
