# 科技四路文体公园短临降雨 MVP

This MVP reads a captured Guangdong weather response, downloads the referenced
CAPPI radar PNG frames, extrapolates radar motion, and outputs a JSON rain
probability report for one fixed court:

- Name: 科技四路文体公园
- Lon/lat: `113.55, 22.39`
- Detection radius: `5 km`
- Rain threshold: `>= 15 dBZ`

## Run

```bash
python3 nowcast.py --response response.txt
```

Default outputs:

- `output/forecast.json`: machine-readable rain probability report
- `output/debug_court_radius.png`: latest CAPPI frame with the court point and
  5 km radius overlay
- `data/cappi/`: downloaded CAPPI PNG cache

## Notes

- `cappi_bounds` is interpreted as `[[minLat, minLon], [maxLat, maxLon]]`.
- The first version uses a linear lon/lat-to-pixel mapping because the CAPPI
  product is a fixed regional image with explicit bounds.
- `qpf6min` from the source response is included as an official reference only;
  the MVP probability is computed from CAPPI frames.
