# Data Sources

The project ships structured, versioned input files in `data/raw/` so a fresh
clone can generate a dataset locally. The files cover the player list, Serie A
calendar, historical statistics, club priors, likely starters, set-piece order,
and auction tiers.

The private fantasy-league calendar is deliberately not committed. Upload it
from **Impostazioni** after cloning the project. Its participating teams must
match the profile participants before generation can proceed.

The application treats the files as input data, not as a remote scraping layer.
If you replace them for another season, update the profile source declarations
and retain attribution required by the data license.

## Goalkeeper hierarchy

`titolari.csv` carries `gerarchia_portiere` for active goalkeepers. Canonical
values are `PRIMO`, `SECONDO`, `TERZO`, and contiguous slash-separated contests
such as `PRIMO/SECONDO` or `SECONDO/TERZO`. Generation rejects unknown,
non-contiguous, unresolved, duplicate, or outfield assignments and preserves
the hierarchy in `auction_data.json`.

The source note remains the evidence and observation context. Missing ranks are
left unknown rather than inferred from FVM, names, or absence from an article.
