<p align="center">
  <img src="https://img.shields.io/badge/Tennis_Weather-Decision_System-00C853?style=for-the-badge&labelColor=1a1a2e" alt="Tennis Weather Decision System" />
</p>

<h1 align="center">Tennis Court Weather Forecaster<br/>网球天气决策系统</h1>

<p align="center">
  <a href="https://github.com/loremdai/tenniscourt_weatherforcaster/blob/main/LICENSE"><img src="https://img.shields.io/github/license/loremdai/tenniscourt_weatherforcaster?style=flat-square&color=blue" alt="License" /></a>
  <img src="https://img.shields.io/badge/python-3.8+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.8+" />
  <img src="https://img.shields.io/badge/LLM-DeepSeek_V4_Pro-6C5CE7?style=flat-square" alt="DeepSeek V4 Pro" />
  <img src="https://img.shields.io/badge/radar-CAPPI_Optical_Flow-FF6B35?style=flat-square" alt="CAPPI Optical Flow" />
  <img src="https://img.shields.io/badge/frontend-OLED_Dark_Mode-000000?style=flat-square" alt="OLED Dark Mode" />
</p>

<p align="center">
  <a href="#quick-start--快速上手">Quick Start</a> •
  <a href="#features--核心特性">Features</a> •
  <a href="#architecture--系统架构">Architecture</a> •
  <a href="#developer-guide--开发者指南">Developer Guide</a> •
  <a href="#contributing--贡献指南">Contributing</a> •
  <a href="#license--许可证">License</a>
</p>

---

## About / 关于

**English**

Tennis Court Weather Forecaster is a high-precision, hyper-local nowcasting system purpose-built for outdoor tennis courts. It fuses official QPF (Quantitative Precipitation Forecast) data with a self-developed CAPPI radar optical-flow extrapolation algorithm, a four-layer progressive risk assessment engine, and DeepSeek V4 Pro LLM diagnostics — delivering **minute-level, court-level** rain predictions and actionable play/cancel recommendations within a 0–2 hour window.

The system ships with a background data-fetching daemon and a modern OLED-dark-mode Bento Grid dashboard for real-time monitoring.

**中文**

网球天气决策系统是一款面向户外网球场景的短临天气预报与决策工具。系统结合官方 QPF 短临降雨预报与自研 CAPPI 雷达光流外推算法、四层风险评估模型及 DeepSeek V4 Pro 大语言模型，提供 0–2 小时内 **分钟级、场地级** 的降雨预测和打球建议。

系统包含后台数据采集守护进程，以及基于深色模式与 Bento Grid 布局的前端监控看板。

---

## Features / 核心特性

| Feature | Description |
|:---|:---|
| **Dual-Engine Data Fusion** | Cross-validates official QPF short-range rainfall forecasts with self-developed CAPPI radar optical-flow extrapolation. <br/> 双源数据融合：官方 QPF 短临预报与自研 CAPPI 雷达光流外推相互校验。 |
| **AI Deep Diagnosis** | Leverages DeepSeek V4 Pro to generate human-readable, evidence-based weather reports — like having a professional meteorologist on call. <br/> AI 诊断：接入 DeepSeek V4 Pro，基于结构化数据生成可读性强的打球环境报告。 |
| **Four-Layer Risk Engine** | Progressive analysis across Radar → Grid → Precipitation → Background layers, outputting a precise risk-score matrix with conservative decision interception. <br/> 四层风险模型：逐级分析雷达层、格点层、降雨层与背景场，输出多维度风险评分。 |
| **Real-Time Dashboard** | OLED dark-mode Bento Grid dashboard with 12-hour forecasts, 30/60/120-min precipitation probabilities, radar timeline player, and auto-refresh every 30 seconds. <br/> 实时监控看板：包含逐小时预报、降水概率、雷达回放和 AI 诊断，每 30 秒自动刷新。 |
| **Booking Decision Engine** | Three-band lead-time aware decision system (0–2h / 2–6h / 6h+) with auto recheck scheduling. <br/> 预约决策：按提前量分三档 (0–2h / 2–6h / 6h+) 给出建议，并自动安排复查时间。 |
| **Daemon Mode** | Background process with configurable refresh interval (default: 6 min), automatic cache cleanup, and calibration logging. <br/> 守护进程模式：可配置刷新间隔、自动清理缓存、校准日志记录。 |

---

## Architecture / 系统架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        main.py  (Orchestrator)                   │
│              Fetch → Analyze → Decide → Diagnose                 │
└────────┬─────────────┬──────────────┬──────────────┬─────────────┘
         │             │              │              │
         ▼             ▼              ▼              ▼
  ┌─────────────┐ ┌──────────┐ ┌────────────┐ ┌──────────────────┐
  │ nowcast.py  │ │risk_     │ │diagnose_   │ │   frontend/      │
  │             │ │engine.py │ │forecast.py │ │                  │
  │ CAPPI Radar │ │          │ │            │ │  index.html      │
  │ Optical Flow│ │ 4-Layer  │ │ DeepSeek   │ │  css/style.css   │
  │ dBZ Mapping │ │ Risk     │ │ V4 Pro LLM │ │  js/app.js       │
  │ QPF Parsing │ │ Scoring  │ │ Prompt Eng │ │                  │
  └──────┬──────┘ └────┬─────┘ └─────┬──────┘ └────────┬─────────┘
         │             │             │                  │
         └─────────────┴─────────────┘                  │
                       │                                │
                       ▼                                │
              ┌─────────────────┐                       │
              │   output/       │◄──────────────────────┘
              │                 │   (reads JSON via HTTP)
              │ forecast.json   │
              │ diagnosis.json  │
              │ debug_*.png     │
              │ calibration_log │
              └─────────────────┘
```

### Module Breakdown / 模块说明

| Module | Role |
|:---|:---|
| **`main.py`** | Orchestrator — coordinates fetch, analysis, decision, and diagnosis pipeline. Supports single-shot and daemon modes. <br/> 主入口：协调数据抓取、分析、决策与诊断流程，支持单次执行和守护模式。 |
| **`nowcast.py`** | Core algorithm — fetches CAPPI radar imagery, converts to dBZ grids, applies Farneback optical-flow for echo motion extrapolation, and computes rain probability within a configurable radius. <br/> 核心算法模块：下载 CAPPI 雷达图、转换 dBZ 网格、光流外推回波运动、计算指定半径内降雨概率。 |
| **`risk_engine.py`** | Four-layer decision engine — frame quality control, dual-window trend analysis, upstream echo detection, risk scoring, booking decisions, and calibration logging. <br/> 风险决策引擎：帧质量控制、双窗口趋势分析、上游回波检测、风险评分与预约决策。 |
| **`diagnose_forecast.py`** | LLM diagnostic layer — builds precisely constrained prompts, queries DeepSeek V4 Pro, enforces conservative-language guardrails, and outputs structured natural-language reports. <br/> LLM 诊断模块：构建约束提示词、调用 DeepSeek V4 Pro，输出结构化自然语言诊断报告。 |
| **`serve_dashboard.py`** | Lightweight HTTP server — serves the static frontend and output data with no-cache headers for real-time data freshness. <br/> 轻量 HTTP 服务器：托管前端页面和输出数据，禁用缓存确保数据实时性。 |
| **`frontend/`** | Static dashboard — zero-state Bento Grid UI consuming JSON from `output/`, auto-refreshing every 30 seconds. <br/> 静态前端看板：读取 `output/` 目录的 JSON 数据，每 30 秒自动刷新。 |

---

## Quick Start / 快速上手

### Prerequisites / 环境准备

- **Python 3.8+**
- Dependencies:

```bash
pip install openai opencv-python numpy Pillow
```

### 1. Start the Backend Daemon / 启动后台守护进程

```bash
# Basic — real-time monitoring, refreshes every 6 minutes
# 基础用法 — 实时监控，每 6 分钟刷新
python3 main.py --daemon
```

<details>
<summary><b>Advanced: Target a specific booking time / 进阶：指定预约时间</b></summary>

```bash
# Target tonight 8pm, 2-hour session, daemon mode
# 针对今晚 20:00、计划打 2 小时，守护模式持续监控
python3 main.py --target-time 20:00 --play-duration 120 --daemon
```

Without `--daemon`, the system runs a single-shot analysis and exits.
不加 `--daemon` 则只执行一次即刻诊断后退出。

</details>

### 2. Start the Dashboard Server / 启动前端看板

Open a **new terminal** (keep the daemon running):

```bash
python3 serve_dashboard.py
```

### 3. View the Dashboard / 查看监控看板

Open your browser and navigate to:

**[http://localhost:2081/frontend/index.html](http://localhost:2081/frontend/index.html)**

The dashboard auto-refreshes every 30 seconds.
看板每 30 秒自动拉取最新预报并刷新 UI。

---

## Developer Guide / 开发者指南

### Output Files / 输出文件字典

系统的数据输出汇总在 `output/` 目录，前端通过 HTTP 读取该目录：

| File | Description |
|:---|:---|
| `forecast.json` | Unified analysis JSON — court metadata, optical-flow vectors, real-time weather, and risk matrices for all time windows. <br/> 综合分析 JSON：包含场地信息、光流向量、实时天气及各时间窗口的风险矩阵。 |
| `diagnosis.json` | Structured LLM diagnosis — headline, data support, reasoning chain, and actionable conclusion. <br/> LLM 诊断报告：包含标题、数据依据、推理过程和结论。 |
| `debug_court_radius.png` | Radar overlay with 5km detection radius mask and court landmark — useful for manual review. <br/> 调试用雷达叠加图：显示 5km 探测半径和场地标记，便于人工复核。 |
| `calibration_log.jsonl` | Historical snapshot log for large-scale backtesting and probability threshold tuning. <br/> 校准日志：逐次快照记录，用于回测分析和阈值调优。 |
| `radar_frames/` | Individual timestamped CAPPI frames with court marker overlays for the timeline player. <br/> 逐帧雷达图：带时间戳和场地标记，供看板时间线播放器使用。 |

### Customizing Your Court Location / 自定义监控场地

The system defaults to "侨光商业中心" tennis court. To target your own court, edit the `COURT` variable at the top of `nowcast.py`:

```python
COURT = {
    "id": "my_tennis_court",
    "name": "My Tennis Court",
    "lon": 113.1234,  # Your court's longitude / 场地经度
    "lat": 22.5678,   # Your court's latitude  / 场地纬度
}

# Adjust detection radius based on local microclimate complexity
# 根据场地周边小气候复杂程度调整探测半径 (默认 5km)
RADIUS_KM = 5.0
```

### Environment Variables / 环境变量

Copy the example file and fill in your key:
复制示例文件并填入你的密钥：

```bash
cp .env.example .env
```

Edit `.env`:

```ini
# DashScope API Key (OpenAI-compatible, for DeepSeek V4 Pro)
DASHSCOPE_API_KEY=sk-your_actual_key_here
```

| Variable | Required | Description |
|:---|:---:|:---|
| `DASHSCOPE_API_KEY` | Recommended | Your DashScope API key (OpenAI-compatible). Enables DeepSeek V4 Pro AI diagnosis. <br/> DashScope API 密钥（兼容 OpenAI 接口），用于启用 AI 诊断功能。 |

The system automatically loads `.env` at startup — no need to manually `export`.
系统启动时会自动加载 `.env` 文件，无需手动 export。

> **Tip / 提示**: Use `--no-llm` flag to run in pure rule-engine mode without LLM — faster and requires no API key.
> 使用 `--no-llm` 参数可跳过 LLM 诊断，仅以规则引擎运行，速度更快且无需 API Key。

### CLI Reference / 命令行参数

```
python3 main.py [OPTIONS]

Options:
  --daemon                 Run as background daemon (live API mode)
  --interval SECONDS       Refresh interval in daemon mode (default: 360)
  --target-time HH:MM      Booking start time, or 'now' (default: now)
  --play-duration MINUTES   Play session duration (default: 120)
  --no-llm                 Skip LLM diagnosis (rule-engine only)
  --api-key KEY            DashScope API key (overrides env var)
  --output PATH            Output JSON report path (default: output/forecast.json)
  --diagnosis-output PATH  LLM diagnosis output path (default: output/diagnosis.json)
  --debug-image PATH       Debug image path (default: output/debug_court_radius.png)
  --max-frames N           Max CAPPI frames to use (default: 12)
```

### Project Structure / 项目结构

```
weather_forcaster/
├── main.py                 # Entry point / orchestrator
├── nowcast.py              # CAPPI radar + optical flow engine
├── risk_engine.py          # Four-layer risk scoring + booking decisions
├── diagnose_forecast.py    # LLM diagnostic layer (DeepSeek V4 Pro)
├── serve_dashboard.py      # HTTP server for dashboard
├── frontend/
│   ├── index.html          # Dashboard UI (Bento Grid layout)
│   ├── css/style.css       # OLED dark mode styles
│   └── js/app.js           # Dashboard logic + auto-refresh
├── data/
│   └── cappi/              # Cached CAPPI radar PNG frames
├── output/
│   ├── forecast.json       # Latest analysis report
│   ├── diagnosis.json      # Latest LLM diagnosis
│   ├── debug_court_radius.png
│   ├── calibration_log.jsonl
│   └── radar_frames/       # Timeline player frames
├── LICENSE                 # MIT License
├── .gitignore
└── README.md               # This file
```

---

## How It Works / 工作原理

The system executes a **four-stage pipeline** on each cycle:

### Stage 1 — Data Acquisition / 数据采集
Fetches from two official APIs:
- **GD121 CAPPI Radar + QPF**: `wxc.gd121.cn` — radar imagery and 6-minute interval quantitative precipitation forecasts
- **Grid-Interpolated Realtime**: `ra.gd121.cn` — precise weather data at exact court coordinates (temp, humidity, wind, hourly & 7-day forecasts)

### Stage 2 — Radar Analysis / 雷达分析
- Downloads and caches CAPPI radar PNG frames
- Converts pixel colors to dBZ (reflectivity) using a calibrated palette via nearest-neighbor matching
- Applies **Farneback optical flow** (OpenCV) across frame pairs to estimate echo motion vectors
- Extrapolates cloud movement to compute **30/60/120-minute rain probability** within a 5km radius of the court
- Cross-validates radar extrapolation against official QPF data

### Stage 3 — Risk Assessment / 风险评估
Four-layer fusion engine:
1. **Official QPF Layer** — base risk from QPF + rain flag consensus
2. **Radar Modification** — adjustments from current echo intensity, trend analysis, and upstream echo detection
3. **Surface Environment** — humidity, current weather state
4. **Background Forecast** — hourly forecast rain-keyword scanning

Outputs per-horizon risk scores (`now`, `30min`, `60min`, `120min`) and a booking decision with recheck scheduling.

### Stage 4 — AI Diagnosis (Optional) / AI 诊断（可选）
- Constructs a meticulously constrained prompt with all four layers of evidence
- Queries DeepSeek V4 Pro with thinking mode enabled
- Enforces **conservative language guardrails** — no absolute statements, no hallucinated sensor data
- Outputs a structured JSON report with data summary, reasoning chain, risk assessment, and human-friendly conclusion

---

## Contributing / 贡献指南

Contributions are welcome! Here's how you can help:
欢迎贡献！以下是参与方式：

1. **Fork** the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Commit your changes: `git commit -m 'Add amazing feature'`
4. Push to the branch: `git push origin feature/amazing-feature`
5. Open a **Pull Request**

### Ideas for Contribution / 贡献方向

- **Multi-region support** — adapter for different weather APIs beyond GD121
- **Mobile-responsive dashboard** — optimize for phone screens
- **Backtesting framework** — automated accuracy evaluation using `calibration_log.jsonl`
- **Push notifications** — WeChat / Telegram alerts when conditions change
- **Unit tests** — coverage for core algorithms

---

## License / 许可证

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

Copyright (c) 2026 [Alistair Dai](https://github.com/loremdai)

---

<p align="center">
  <sub>Built for uninterrupted tennis matches. | 让每一场球赛不再被天气打断。</sub>
</p>
