# model_forecast

Interactive forecast map of **southeast Asia** (90-128E, 12S-30N) from the latest ECMWF open data, one tab per
model (**IFS** and **AIFS**), with a time slider over every forecast step.

```
pip install -r requirements.txt
python wind_forecast.py                  # -> output/index.html (+ output/data/)
python -m http.server -d output          # then open http://localhost:8000
```

The page loads its data with `fetch()`, so it has to be served over HTTP (opening the file directly does not work).
It also needs internet for the Leaflet library and the map tiles.

## What you can show

* **Shading** (one at a time): wind speed at 925 / 850 / 700 hPa, rain, relative humidity at 700 hPa.
* **Flow** (independent of the shading): 925 / 850 / 700 hPa wind as **arrows**, **streamlines**, or off.
* Wind speed uses your knots colour scale (5-kt bins). Rain is the amount in the preceding model step interval:
  **3 h for IFS, 6 h for AIFS** (IFS steps beyond +144 h are 6-hourly). Humidity is computed from specific humidity
  and temperature for both models (AIFS has no relative humidity field).
* Switching tab keeps the current valid time (nearest step). Play/pause, the slider and left/right arrow keys step
  through time. Hovering shows the values under the cursor.

## How it stays fast

* Each model and variable is a separate small binary file (`output/data/<model>_<run>_<var>.bin.gz`) loaded only
  when selected; the page itself is ~35 KB. A wind level is ~2.4 MB for IFS and ~1 MB for AIFS (gzip), humidity and
  rain a few hundred KB.
* The browser draws the fields itself from the raw grid (shading, arrows, streamlines on canvas layers), so there are
  no per-frame images to decode and changing a frame takes a few milliseconds.

## Options

| option | default | |
|---|---|---|
| `--models ifs aifs` | both | models to include |
| `--max-step 144` | 144 | last forecast step in hours (IFS 00/12 UTC runs go to 360, 06/18 UTC to 144; AIFS to 360) |
| `--workers 8` | 8 | parallel downloads |
| `--cache-dir` | `cache/` | where the cropped data is kept |
| `--no-fetch` / `--fetch-only` | | build from the cache only / refresh the cache only |
| `--source aws` | aws | open-data mirror: `aws`, `google`, `azure`, `ecmwf` |
| `--out` | `output/index.html` | |

Steps: IFS every 3 h (6 h after +144 h on 00/12 UTC runs); AIFS every 6 h. The region is set by
`LAT_N/LAT_S/LON_W/LON_E` at the top of `wind_forecast.py` (regenerate `coastlines.geojson` with
`tools/make_coastlines.mjs` after changing it).

## Data cache (the `data` branch)

Downloading a run takes a while, so the cropped region (int16, ~16 MB for both models) is kept in
`cache/<model>_<run>.npz`. When a newer run exists the old file is deleted. In CI the cache lives on the `data`
branch: a single commit that the workflow force-pushes whenever there is a new run, so the history never grows.

For development, get the data without touching ECMWF:

```
git clone --depth 1 --branch data https://github.com/zgkn/model_forecast cache
python wind_forecast.py --no-fetch        # builds the site from the cache in seconds
```

## Publishing (GitHub Pages)

`.github/workflows/pages.yml` runs four times a day (02:30, 08:30, 14:30, 20:30 UTC, after each ECMWF run is
released). It loads the `data` branch, downloads only if ECMWF has a newer run, rebuilds the site, refreshes the
`data` branch, and deploys to GitHub Pages. A scheduled run that finds the live page (`runs.json`) already showing
the newest runs does not redeploy. Pushes to any branch build only; the default branch also deploys and writes
`data`. One-time setup: repository **Settings -> Pages -> Source: GitHub Actions**. Scheduled workflows only run
from the default branch.

## Map data

* Background tiles: Esri World Light Gray (or OpenStreetMap, layer control top right).
* Coastlines and borders: `coastlines.geojson` (Natural Earth 10m, public domain), drawn by the page so they show
  even if tiles fail.
* Weather data: ECMWF open data, CC BY 4.0.

## Files

* `wind_forecast.py` - download, cache and site build
* `viewer.html`, `viewer.css`, `viewer.js` - the UI that is inlined into the Folium page
* `coastlines.geojson`, `tools/make_coastlines.mjs` - coastline/border data and its generator
