# fishertiger - A Fantacalcio Auction Advisor

Local-first advisor for a Classic Fantacalcio Serie A auction. It builds player
projections, supports a live auction, replays randomized auctions, and runs a
season-level Monte Carlo simulation using the configured league rules.

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
Upload your private `calendario_lega.xlsx`, verify participants and sources,
then use **Genera dati**. Generated datasets and simulations stay local under
`data/processed/<profile_id>/<season>/`.

## Inputs And Profiles

`config/default_profile.json` is the single public default profile. The API
serves it to the client; there is no duplicate browser profile.

The repository includes base inputs in `data/raw/`. The only excluded input is
`data/raw/calendario_lega.xlsx`, because it identifies a user's fantasy league.
It must be uploaded locally. The profile source declarations identify the
expected files and seasons.

The Serie A input is always a 20-team, 38-matchday, 380-match calendar. The
fantasy league can use a shorter configured interval through
`fantasy_start_matchday`, `fantasy_end_matchday`, and `fantasy_matchdays`.

## CLI

The UI is the normal workflow. These commands are useful for local automation
after a matching private calendar has been supplied:

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
included `free` plan is suitable only for trying the deployment: its filesystem
is ephemeral, so saved profiles, uploads, and generated datasets disappear on
restart or redeploy. For durable multi-league use, switch to a paid web-service
plan and attach a persistent disk mounted at `/var/data`.

Keep the fork connected to the original repository with two remotes:

```bash
git fetch upstream
git merge upstream/main
git push origin main
```
