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
- `FISHERTIGER_FANTALAB_REFRESH_TOKEN` + `FISHERTIGER_FANTALAB_FIREBASE_API_KEY`
  (recommended): a long-lived Firebase refresh token, plus the FantaLab
  Firebase project's web API key needed to exchange it. The server renews the
  ID token itself and keeps doing so forever — including across restarts and
  redeploys — because it persists the rotated refresh token Google returns on
  every renewal to `<updates-dir>/fantalab/credentials.json`. The refresh
  token only needs to be captured once, ever, regardless of the FantaLab
  login method (email/password, Google, …), because Firebase issues its own
  refresh token after any successful sign-in. If both this and the token
  above are set, the refresh token takes priority; `FISHERTIGER_FANTALAB_TOKEN`
  is used only if renewing from the refresh token fails.

  The API key is not itself secret (Firebase ships it inside every client
  bundle; it identifies the project, it does not grant access on its own) but
  is still read from an environment variable rather than hardcoded, purely so
  an automated secret scanner never flags it as a leaked credential. Find it
  by opening `app.fantalab.it`'s main JS bundle (referenced from its HTML as
  `/static/js/main.*.js`) and searching for `apiKey` near a `firebaseConfig`-
  looking object; it starts with `AIzaSy`.

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
      for (const row of rows) {
        const token = row?.value?.stsTokenManager?.refreshToken;
        if (token) { copy(token); console.log("Copiato negli appunti."); return; }
      }
    }
  }
  console.log("Token non trovato: assicurati di aver eseguito l'accesso su questa pagina.");
})();
```

`copy()` is a Chrome/Edge DevTools console helper (not available in normal
page scripts) that puts the value straight on the clipboard, so the refresh
token never needs to be manually selected. Set it as
`FISHERTIGER_FANTALAB_REFRESH_TOKEN` in `/etc/astafanta-support.env` (systemd
installation) and restart the service
(`sudo systemctl restart astafanta-support.service`); no rebuild is needed
since only environment variables changed.

This data lives only inside the browser, scoped to the `app.fantalab.it`
origin by the browser's own storage isolation — no other site, and no SSH
session to an unrelated server, can read it. The DevTools console step above
is not an arbitrary hoop: it is the only place this value is reachable at
all, because the code runs in that page's own security context.

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
