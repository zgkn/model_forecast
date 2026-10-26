# model_forecast

Interactive map of the **925 hPa wind over southeast Asia** (98-120E, 6S-8N) from the latest ECMWF open-data forecast,
one tab per model (**IFS** and **AIFS**), with a time slider over every forecast step.

```
pip install -r requirements.txt
python wind_forecast.py            # -> output/wind925.html
```

Open `output/wind925.html` in a browser (it needs internet for the Leaflet library and map tiles).

* Finds the newest published 00/06/12/18 UTC run of each model and downloads only u/v at 925 hPa
  (one small GRIB per step, cached in `data/`).
* Colour = wind speed in knots (5-kt bins, 50+ kt over-range), one scale for all frames and both models; arrows = direction and strength.
* Speed field is cubic-interpolated from the 0.25 degree grid to ~0.03 degrees.
* Switching tab keeps the current valid time (nearest step). Play/pause, slider, and left/right arrow keys step through time.

| option | default | |
|---|---|---|
| `--models ifs aifs` | both | models to include |
| `--max-step 144` | 144 | last forecast step in hours (IFS 00/12 UTC runs go to 360, 06/18 UTC to 144; AIFS to 360) |
| `--source aws` | aws | open-data mirror: `aws`, `google`, `azure`, `ecmwf` |
| `--out` | `output/wind925.html` | |

Steps: IFS every 3 h (6 h after +144 h on 00/12 UTC runs); AIFS every 6 h.
The page is small (roughly 0.05 MB per step).
The region is set by `LAT_N/LAT_S/LON_W/LON_E` at the top of `wind_forecast.py`.

Data: ECMWF open data, CC BY 4.0.

## Files

* `wind_forecast.py` - download, processing, and page assembly
* `viewer.html`, `viewer.css`, `viewer.js` - the UI that is inlined into the Folium page
