# Data Sources

The project ships structured, versioned input files in `data/raw/` so a fresh
clone can generate a dataset locally. The files cover the player list, Serie A
calendar, historical statistics, club priors, likely starters, set-piece order,
and auction tiers.

The private fantasy-league calendar is deliberately not committed. It is
optional for generating dashboard, projection, and auction data, but required
to simulate a season. Upload it from **Impostazioni**, then regenerate the
dataset before simulation. Its teams and matchday count are checked against the
profile during generation.

## League calendar

Download the sanitized workbook model from **Impostazioni**. The importer reads
the legacy Leghe Fantacalcio layout, not an arbitrary spreadsheet:

- Use an `.xlsx` workbook with a worksheet named exactly `Calendario`.
- Keep the two fixture blocks in columns A:D and G:J. The other columns may be
  used for display or score values and are ignored.
- Begin each block with `Nª Giornata lega` in column A or G and `Mª Giornata
  serie a` in column C or I.
- Put each home team in A and away team in D for the left block; use G and J
  for the right block. Fixture rows continue until the next header.
- Blank rows are allowed. Each fixture needs two distinct teams, and a team
  cannot play twice in one matchday.

For example:

```text
A: 1ª Giornata lega       C: 1ª Giornata serie a
A: Squadra 1              D: Squadra 2
A: Squadra 3              D: Squadra 4

G: 2ª Giornata lega       I: 2ª Giornata serie a
G: Squadra 1              J: Squadra 3
G: Squadra 2              J: Squadra 4
```

Generation requires consecutive league matchdays starting at 1 and exactly the
configured number of fantasy matchdays. The team names must match the profile;
the API adopts valid uploaded calendar names as the profile participants when
the data is generated.

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
