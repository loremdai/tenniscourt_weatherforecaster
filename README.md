# 科技四路文体公园短临降雨 MVP

This MVP reads a captured Guangdong weather response, downloads the referenced
CAPPI radar PNG frames, extrapolates radar motion, and outputs a JSON rain
probability report for one fixed court:

- Name: 科技四路文体公园
- Lon/lat: `113.55, 22.39`
- Detection radius: `5 km`
- Rain threshold: `>= 15 dBZ`

## 运行方式

### 1. 本地测试模式 (一次性运行)

依赖手动抓包获取 `stream-response.txt`：

```bash
python3 nowcast.py --response response.txt
```

### 2. 持续监测模式 (守护进程)

直接调用广东天气 API 持续拉取最新数据，并覆盖更新输出结果：

```bash
python3 nowcast.py --daemon
```

默认每 6 分钟 (360秒) 刷新一次。也可以自定义间隔（例如 10 分钟）：

```bash
python3 nowcast.py --daemon --interval 600
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
