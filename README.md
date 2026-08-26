# Australian Fuel Data

Automated collector for retail fuel prices across all eight Australian states and
territories. A GitHub Action runs every 45 minutes, pulls each jurisdiction's
official price feed, and commits the normalised result to `data/`.

The data is also published to a CDN, which is what applications should read —
see [Consuming the data](#consuming-the-data).

## Sources

| Jurisdiction | Source |
|---|---|
| NSW, TAS, ACT | NSW FuelCheck API (v2) |
| QLD | Queensland Fuel Price Disclosure API |
| VIC | Victoria Fair Fuel Open Data API |
| SA | South Australia FPIS API |
| NT | MyFuel NT Third Party API |
| WA | FuelWatch RSS feed |

Each provider's terms govern reuse of its data. This repository is an
aggregator: it does not relicense the underlying feeds, and it is not affiliated
with or endorsed by any of the agencies above.

## Layout

```
data/
  index.json               # manifest: generation time, per-state counts
  brands.json              # normalised brand list
  prices_compact.json      # all current prices, compact encoding
  stations_metadata.json   # station id -> name, brand, address, coordinates
  stations_no_prices.json  # known stations currently reporting no price
  AUS/latest.json          # every station nationally, current prices
  <state>/latest.json      # per-state current prices (act, nsw, nt, qld, sa, tas, vic, wa)
  cheapest/<fuel>.json     # cheapest stations per fuel type
  tiles/lat_<lat>_lng_<lng>.json  # 2-degree geographic tiles, for map queries
  history/<state>.json     # rolling price averages
```

### What is not stored here

The collector writes a timestamped snapshot per state on every run. Those are
**not** committed — they are gitignored. Full price history is kept in
Cloudflare D1, which ingests from `data/AUS/latest.json`. Committing the
snapshots previously grew this repository to roughly 6 GB across 21,000 files.

Only current-state files are versioned. `git log` on `data/` therefore gives you
change-over-time for free, without the file sprawl.

## Consuming the data

Prefer the Cloudflare Pages mirror over the GitHub raw URLs — it is a proper
CDN, has permissive CORS headers, and is not subject to GitHub's rate limits:

```
https://<your-pages-domain>/AUS/latest.json
https://<your-pages-domain>/nsw/latest.json
https://<your-pages-domain>/cheapest/u91.json
```

All responses are served with `Access-Control-Allow-Origin: *` and
`Cache-Control: public, max-age=300`.

## Running locally

```bash
pip install requests
python scripts/fetch_all_fuel_data.py
```

The script reads credentials from the environment. Without them it will skip the
affected jurisdictions rather than fail outright:

| Variable | Covers |
|---|---|
| `NSW_FUEL_API_KEY`, `NSW_FUEL_API_SECRET`, `NSW_FUEL_AUTH_HEADER` | NSW, TAS, ACT |
| `QLD_TOKEN` | QLD |
| `VIC_TOKEN` | VIC |
| `SA_TOKEN` | SA |
| `NT_USERNAME`, `NT_TOKEN` | NT |

WA needs no credentials — it reads a public RSS feed.

In CI these come from repository secrets. GitHub does not expose secrets to
workflows triggered by pull requests from forks, so they remain safe in a public
repository; the scheduled and manual runs on this repository can read them.

## Accuracy

Prices come directly from the jurisdictions' own feeds and are only as current
as those feeds. Individual sites may report late or not at all. Nothing here is
a guarantee of the price at the pump.
