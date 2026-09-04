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

The systemd installation stores the account database in
`/var/lib/astafanta-support/users.json`. The first startup imports the admin
credentials from the environment; after that, use **Accessi** in the web app to
change the admin password and manage collaborator accounts without restarting
the service. Keep this file in backups together with profiles and datasets.

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
the public realtime namespaces once and then reuse the discovered shard, but
FantaLab's public read only reveals a team's real name/position once it leads
or buys, so the app asks to confirm each anonymous team once.

Configuring a server-side credential removes that manual step and adds
official room/team names immediately. Two credentials are supported, and both
are never returned to the browser or logged:

- `FISHERTIGER_FANTALAB_TOKEN`: a ready-to-use Firebase ID token (the
  `Authorization: Bearer …` value from a request to `api.fantalab.it` while
  logged in, captured from the browser's DevTools Network tab). Simple, but
  Firebase ID tokens expire after about an hour, so this needs recapturing
  periodically during a long auction.
- `FISHERTIGER_FANTALAB_REFRESH_TOKEN` (recommended): a long-lived Firebase
  refresh token. The server exchanges it for a fresh ID token itself and
  keeps renewing automatically forever — including across restarts and
  redeploys — because it persists the rotated refresh token Google returns on
  every renewal to
  `<updates-dir>/fantalab/credentials.json`. It only needs to be captured
  once, ever, regardless of the FantaLab login method (email/password,
  Google, …), because Firebase issues its own refresh token after any
  successful sign-in. If both variables are set, the refresh token takes
  priority; `FISHERTIGER_FANTALAB_TOKEN` is used only if renewing from the
  refresh token fails.

To capture the initial refresh token: log into `app.fantalab.it`, open the
auction room, open DevTools (F12), go to the **Console** tab and paste (Chrome
may ask you to type `allow pasting` first, as a self-XSS guard):

```js
(async () => {
  for (const {name} of await indexedDB.databases()) {
    if (!/firebase/i.test(name)) continue;
    const db = await new Promise((res, rej) => {
      const r = indexedDB.open(name);
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
    for (const store of db.objectStoreNames) {
      const rows = await new Promise((res, rej) => {
        const r = db.transaction(store).objectStore(store).getAll();
        r.onsuccess = () => res(r.result);
        r.onerror = () => rej(r.error);
      });
      console.log(name, store, rows);
    }
  }
})();
```

This prints the browser's Firebase auth state. Expand the logged object(s) in
the console until you find `stsTokenManager.refreshToken` (a long string) —
right-click it and "Copy value". Set it as `FISHERTIGER_FANTALAB_REFRESH_TOKEN`
in `/etc/astafanta-support.env` (systemd installation) and restart the
service (`sudo systemctl restart astafanta-support.service`); no rebuild is
needed since only an environment variable changed.

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
