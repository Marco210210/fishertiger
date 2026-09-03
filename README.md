# AstaFanta Support

Local-first advisor for a Classic Fantacalcio Serie A auction. It builds player
projections, supports a live auction, replays randomized auctions, and runs a
season-level Monte Carlo simulation using the configured league rules.

The **FantaLab live** screen can read the current lot, price, leader and
authoritative purchase ledger directly from a FantaLab room. Purchases are
merged into the local league board, so opponent credits, roster needs,
scarcity, observed inflation and the recommended walk-away price update while
the auction is running. The connector is read-only and a complete manual mode
always remains available.

**Scout AI** stores source-linked, dated news outside the live path and applies
a deliberately bounded adjustment to player value. Claude Code is supported
for a pre-auction refresh; if it is unavailable, the updater falls back to the
official Fantacalcio.it injury list. No AI failure can stop an auction.

## License

- Software: [MIT](LICENSE)
- Structured base data in `data/raw/`: [CC BY 4.0](DATA_LICENSE.md)
- Model choices: [MODEL.md](MODEL.md)
- Input data and private calendar: [DATA_SOURCES.md](DATA_SOURCES.md)

## Requirements

- Python 3.10+
- Node.js 22+

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cd web && npm install
```

## Run Locally

Start the local API from the repository root:

```bash
.venv/bin/python -m advisor.server --host 127.0.0.1 --port 8000
```

In another terminal, start Vite:

```bash
cd web
npm run dev
```

Open the Vite URL. On first launch the application opens **Impostazioni**.
The included sources are enough to use **Genera dati** for the dashboard,
projections, and auction tools. Upload a compatible private
`calendario_lega.xlsx` and regenerate the dataset only before running the
season simulation. Generated datasets and simulations stay local under
`data/processed/<profile_id>/<season>/`.

## Inputs And Profiles

`config/default_profile.json` is the single public default profile. The API
serves it to the client; there is no duplicate browser profile.

The repository includes base inputs in `data/raw/`. The only excluded input is
`data/raw/calendario_lega.xlsx`, because it identifies a user's fantasy league.
It is optional for generation and required for season simulation. Download the
sanitized model from **Impostazioni** when needed. The profile source
declarations identify the expected files and seasons.

Quotations and current-season statistics are stored in the public JSON
snapshot `data/raw/fantacalcio_2026_27.json`. Refresh it from Fantacalcio's
public pages with:

```bash
.venv/bin/python -m scripts.update_official_data
```

On GitHub the **Update official Fantacalcio data** workflow performs the same
check every day, refreshes the official injury snapshot, and commits only when
the public data actually changed.

Run a manual Scout AI refresh with a Claude subscription authenticated in
Claude Code:

```bash
.venv/bin/python -m scripts.update_scout_ai
```

The Serie A input is always a 20-team, 38-matchday, 380-match calendar. The
fantasy league can use a shorter configured interval through
`fantasy_start_matchday`, `fantasy_end_matchday`, and `fantasy_matchdays`.

## CLI

The UI is the normal workflow. The pipeline command works with the included
sources; supply a matching private calendar before the simulation command:

```bash
.venv/bin/python -m advisor.pipeline --profile config/default_profile.json --raw-dir data/raw --output-dir data/processed
.venv/bin/python -m advisor.simulate --profile config/default_profile.json --raw-dir data/raw --output-dir data/processed --iterations 1000 --seed 202627
```

## Verification

```bash
.venv/bin/python -m pytest
cd web && npm test && npm run build
```

## Deploy From A GitHub Fork

The repository includes a production `Dockerfile` and a Render Blueprint. The
container builds the React frontend, serves it from the Python process, and
keeps the browser and API on the same HTTPS origin. `FISHERTIGER_USERNAME` and
`FISHERTIGER_PASSWORD` protect the entire application with HTTP Basic auth;
configure both as hosting secrets and never commit their values.

Import `render.yaml` from the GitHub fork in Render to create a web service.
Every commit pushed to the fork's `main` branch triggers a new deployment. The
included `free` plan keeps the deployment free. Its filesystem is ephemeral,
so export each league profile from the application and re-import it after a
restart or redeploy; committed source data remains available from GitHub.

Keep the fork connected to the original repository with two remotes:

```bash
git fetch upstream
git merge upstream/main
git push origin main
```

For the persistent, free Oracle Cloud deployment use
[`compose.oracle.yaml`](compose.oracle.yaml) and follow
[`ORACLE_DEPLOYMENT.md`](ORACLE_DEPLOYMENT.md). Third-party attribution for the
Fantabot-derived integration is recorded in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
