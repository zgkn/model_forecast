# model_forecast

Interactive map of the **925 hPa wind over southeast Asia** (98-120E, 6S-8N) from the latest ECMWF open-data forecast,
one tab per model (**IFS** and **AIFS**), with a time slider over every forecast step.

```
pip install -r requirements.txt
python wind_forecast.py            # -> output/wind925.html
```

Open `output/wind925.html` in a browser (it needs internet for the Leaflet library and map tiles).

* Finds the newest published 00/06/12/18 UTC run of each model and downloads only u/v at 925 hPa
  (one small GRIB per step), then keeps just the cropped region in `cache/` (see below).
* Colour = wind speed in knots (5-kt bins, 50+ kt over-range), one scale for all frames and both models; arrows = direction and strength.
* Speed field is cubic-interpolated from the 0.25 degree grid to ~0.03 degrees.
* Switching tab keeps the current valid time (nearest step). Play/pause, slider, and left/right arrow keys step through time.

| option | default | |
|---|---|---|
| `--models ifs aifs` | both | models to include |
| `--max-step 144` | 144 | last forecast step in hours (IFS 00/12 UTC runs go to 360, 06/18 UTC to 144; AIFS to 360) |
| `--workers 8` | 8 | parallel downloads |
| `--cache-dir` | `cache/` | where the cropped data is kept |
| `--no-fetch` / `--fetch-only` | | build from the cache only / refresh the cache only |
| `--source aws` | aws | open-data mirror: `aws`, `google`, `azure`, `ecmwf` |
| `--out` | `output/wind925.html` | |

Steps: IFS every 3 h (6 h after +144 h on 00/12 UTC runs); AIFS every 6 h.
The page is small (roughly 0.05 MB per step).
The region is set by `LAT_N/LAT_S/LON_W/LON_E` at the top of `wind_forecast.py`.

Data: ECMWF open data, CC BY 4.0.

## Files

* `wind_forecast.py` - download, processing, and page assembly
* `viewer.html`, `viewer.css`, `viewer.js` - the UI that is inlined into the Folium page
* `coastlines.geojson`, `tools/make_coastlines.mjs` - bundled coastline/border data and its generator

## Data cache (the `data` branch)

Downloading a run takes minutes, so the cropped region (u/v at 925 hPa, int16, ~0.8 MB for both models) is kept in
`cache/<model>_<run>.npz`. When a newer run exists the old file is deleted. In CI the cache lives on the `data`
branch: a single commit that the workflow force-pushes whenever there is a new run, so the history never grows.

For development, get the data without touching ECMWF:

```
git clone --depth 1 --branch data https://github.com/zgkn/model_forecast cache
python wind_forecast.py --no-fetch        # builds output/wind925.html from the cache in a few seconds
```

`python wind_forecast.py --fetch-only` refreshes the cache; a plain `python wind_forecast.py` does both (and only
downloads when the cached run is not the latest).

## Publishing (GitHub Pages)

`.github/workflows/pages.yml` runs four times a day (02:30, 08:30, 14:30, 20:30 UTC, after each ECMWF run is
released). It loads the `data` branch, downloads only if ECMWF has a newer run, rebuilds the page, refreshes the
`data` branch, and deploys to GitHub Pages. A scheduled run that finds the live page (`runs.json`) already showing
the newest runs does not redeploy. Pushes to any branch build only; the default branch also deploys and writes
`data`. One-time setup: repository **Settings -> Pages -> Source: GitHub Actions**. Scheduled workflows only run
from the default branch.

## Map data

* Background tiles: Esri World Light Gray (or OpenStreetMap, layer control top right).
* Coastlines and borders are drawn from `coastlines.geojson` (Natural Earth 10m, public domain), so they show
  even if tiles fail. Regenerate with `tools/make_coastlines.mjs`.
