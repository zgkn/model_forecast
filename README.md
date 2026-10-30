# model_forecast

Interactive forecast map of **southeast Asia** (88-150E, 12S-30N) from the latest ECMWF open data, one tab per
model (**IFS** and **AIFS**), with a time slider over every forecast step.

```
pip install -r requirements.txt
python wind_forecast.py                  # -> output/index.html (+ output/data/)
python -m http.server -d output          # then open http://localhost:8000
```

The page loads its data with `fetch()`, so it has to be served over HTTP (opening the file directly does not work).
It also needs internet for the Leaflet library and the map tiles.

## What you can show

* **Shading** (one at a time): wind speed at 925 / 850 / 700 hPa, rain, relative humidity at 700 hPa, and
  **consecutive dry days** (see below).
* **Flow** (independent of the shading): 925 / 850 / 700 hPa wind as **arrows**, **streamlines**, or off.
* Wind speed uses your knots colour scale (5-kt bins). Rain is the amount in the preceding model step interval:
  **3 h for IFS, 6 h for AIFS** (IFS steps beyond +144 h are 6-hourly). Humidity is computed from specific humidity
  and temperature for both models (AIFS has no relative humidity field).
* Switching tab keeps the current valid time (nearest step). Play/pause, the slider and left/right arrow keys step
  through time. Hovering shows the values under the cursor.

## Consecutive dry days (CDD)

Fire-susceptibility layer from the 15-day precipitation forecast (`cdd.py`, tested in `tests/test_cdd.py`):

* `tp` (accumulated from the run start) is taken at the **00 UTC** steps out to +360 h; the difference between
  consecutive midnights is the rain of that UTC day (mm, negatives clipped; a missing midnight gives a NaN day).
  A 00Z run gives 15 full days, a 12Z run 14 (its first 12 h is a partial day and is dropped; the 06/18Z
  AIFS runs likewise), so a **15-day spell can only occur in a run that starts at 00Z**.
* A day is **dry** if it has less than 1 mm (missing data is not dry). The streak engine gives, per cell, the longest
  spell so far and the first day a spell reached 5, 10 and 15 days.
* Only land counts: Natural Earth land polygons (`land.geojson`) are sampled 4x4 per grid cell, land if at least 15%.
* The slider switches to **UTC days**: the colours show the longest dry spell **so far** (up to and including that day;
  the last day is the full-forecast maximum). Outlines of the regions that reach 5 / 10 / 15 days are for the whole
  forecast; click one for the earliest date, peak and area (4-connected regions, polygons follow the grid cells).
  `data/<model>_<run>_cdd.nc` (NetCDF, `max_cdd` and `first_day_ge_5/10/15`) can be downloaded from the page.
* IFS uses the latest run that has published step 360 (a 00/12Z run); AIFS its latest run. The CDD run can differ
  from the run of the animated layers and is named in the header.
* Limitations: streaks start at forecast day 0, so a dry spell already under way is under-counted; a UTC day is
  07:00-07:00 local in western Indonesia; skill of a deterministic forecast falls quickly beyond ~10 days.
* If the CDD analysis fails (no land, maximum 0, missing data) the site is still built without the layer and the
  workflow logs a warning. `--no-cdd` skips it.

## EPS tab (Singapore ensemble meteogram)

A third tab, **EPS · Singapore**, shows the latest ECMWF 10-day ensemble meteogram (EPSgram) for Singapore
(1.28967N, 103.85E). Nothing is downloaded or stored by the build: the page asks ECMWF Open Charts for the image
in the visitor's browser (`charts.ecmwf.int/opencharts-api/v1/products/opencharts_meteogram/`), starting with the
newest 00/12 UTC base time and stepping back to older runs until one exists, so it is always the latest available.
If the browser cannot load it there (for example ECMWF refuses cross-site requests) the tab shows links to the same
chart on ECMWF Open Charts instead. The place and product are set at the top of `viewer.js` (`EPS`).
Check ECMWF's terms for Open Charts for your use; the tab credits ECMWF Open Charts.

## CAMS tab (organic matter AOD)

A **CAMS** tab shows the CAMS global forecast of organic matter aerosol optical depth at 550 nm (a smoke signal),
3-hourly out to +120 h. Colour scale 0 to 5 (top class 5 and above): bins at 0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2, 3, 4, 5.

* `cams.py` downloads it the way the AOD_PM project does: dataset `cams-global-atmospheric-composition-forecasts`
  from the Atmosphere Data Store with `cdsapi`, the newest 00/12 UTC cycle first and older cycles as fallback.
  It is cut to the map region, interpolated from CAMS's ~0.4 degree grid onto the 0.25 degree map grid and cached
  (`cache/cams_<cycle>.npz`, newest only, 0.02 AOD per count so 0 to 5.1 fits in a byte). The page file is ~60 KB.
* It needs an ADS key: add the repository secret **`CDSAPI_KEY`** (the key shown at
  https://ads.atmosphere.copernicus.eu/how-to-api once logged in) and accept the dataset's licence on the ADS site
  once. Locally put the key in `~/.cdsapirc` (`url: https://ads.atmosphere.copernicus.eu/api`, `key: ...`).
* Without the key, or if the download fails, the tab is left out and the workflow logs a warning (the last cached
  cycle is reused if there is one). `--models ifs aifs` skips CAMS; `--models cams` is CAMS only.
* Data: Generated using Copernicus Atmosphere Monitoring Service information.

## How it stays fast

* Each model and variable is a separate small binary file (`output/data/<model>_<run>_<var>.bin.gz`) loaded only
  when selected; the page itself is ~35 KB. A wind level is ~3.8 MB for IFS and ~1.6 MB for AIFS (gzip), humidity and
  rain a few hundred KB.
* The browser draws the fields itself from the raw grid (shading, arrows, streamlines on canvas layers), so there are
  no per-frame images to decode and changing a frame takes a few milliseconds.

## Options

| option | default | |
|---|---|---|
| `--models ifs aifs cams` | all | models to include; `cams` needs ADS credentials |
| `--max-step 144` | 144 | last forecast step in hours (IFS 00/12 UTC runs go to 360, 06/18 UTC to 144; AIFS to 360) |
| `--workers 4` | 4 | parallel downloads (S3 answers "503 Slow Down" if hit harder) |
| `--cache-dir` | `cache/` | where the cropped data is kept |
| `--no-fetch` / `--fetch-only` | | build from the cache only / refresh the cache only |
| `--no-cdd` | | skip the consecutive-dry-days layer |
| `--source google` | google | open-data mirror: `google`, `aws`, `azure`, `ecmwf`; if it fails the other of google/aws is tried (AWS throttles with `503 Slow Down` right after a release) |
| `--out` | `output/index.html` | |

Steps: IFS every 3 h (6 h after +144 h on 00/12 UTC runs); AIFS every 6 h. The region is set by
`LAT_N/LAT_S/LON_W/LON_E` at the top of `wind_forecast.py` (regenerate `coastlines.geojson` with
`tools/make_coastlines.mjs` after changing it).

## Data cache (the `data` branch)

Downloading a run takes a while, so the cropped region (int16, ~27 MB for both models, plus the small CDD files) is kept in
`cache/<model>_<run>.npz`. When a newer run exists the old file is deleted. In CI the cache lives on the `data`
branch: a single commit that the workflow force-pushes whenever there is a new run, so the history never grows.

For development, get the data without touching ECMWF:

```
git clone --depth 1 --branch data https://github.com/zgkn/model_forecast cache
python wind_forecast.py --no-fetch        # builds the site from the cache in seconds
```

## Publishing (GitHub Pages)

`.github/workflows/pages.yml` runs four times a day (02:30, 08:30, 14:30, 20:30 UTC, after each ECMWF run is
released). It loads the `data` branch, refreshes the ECMWF data in a **time-boxed step** (12 min; ECMWF's server throttles
right after a new run appears and a failure there is tolerated), builds the site from whatever the cache holds,
updates the `data` branch, and deploys to GitHub Pages. A slow download therefore delays the newest data, never the deploy. A scheduled run that finds the live page (`runs.json`) already showing
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
* `cdd.py`, `cams.py`, `tests/` - consecutive-dry-days engine, CAMS download and their tests (`python -m pytest`)
* `coastlines.geojson`, `land.geojson`, `tools/make_coastlines.mjs` - coastline/border/land data and its generator
