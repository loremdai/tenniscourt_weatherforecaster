<p align="center">
  <img src="https://img.shields.io/badge/Tennis_Weather-Decision_System-00C853?style=for-the-badge&labelColor=1a1a2e" alt="Tennis Weather Decision System" />
</p>

<h1 align="center">Tennis Court Weather Forecaster<br/>网球天气决策系统</h1>

<p align="center">
  <a href="https://github.com/loremdai/tenniscourt_weatherforcaster/blob/main/LICENSE"><img src="https://img.shields.io/github/license/loremdai/tenniscourt_weatherforcaster?style=flat-square&color=blue" alt="License" /></a>
  <img src="https://img.shields.io/badge/python-3.8+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.8+" />
  <img src="https://img.shields.io/badge/LLM-GLM--5_Thinking-6C5CE7?style=flat-square" alt="GLM-5" />
  <img src="https://img.shields.io/badge/radar-CAPPI_Optical_Flow-FF6B35?style=flat-square" alt="CAPPI Optical Flow" />
  <img src="https://img.shields.io/badge/vision-Qwen_3.6_Plus-00B8D9?style=flat-square" alt="Qwen 3.6 Plus" />
  <img src="https://img.shields.io/badge/frontend-OLED_Dark_Mode-000000?style=flat-square" alt="OLED Dark Mode" />
</p>

<p align="center">
  <a href="#quick-start--快速上手">Quick Start</a> •
  <a href="#features--核心特性">Features</a> •
  <a href="#architecture--系统架构">Architecture</a> •
  <a href="#developer-guide--开发者指南">Developer Guide</a> •
  <a href="#deployment--部署指南">Deployment</a> •
  <a href="#contributing--贡献指南">Contributing</a> •
  <a href="#license--许可证">License</a>
</p>

---

## About / 关于

**English**

Tennis Court Weather Forecaster is a high-precision, hyper-local nowcasting system purpose-built for outdoor tennis courts. It fuses official QPF (Quantitative Precipitation Forecast) data with a self-developed CAPPI radar optical-flow extrapolation algorithm, a four-layer progressive risk assessment engine, a five-dimensional playability scoring system with semi-physical court wetness modeling, multimodal radar visual QA (Qwen 3.6 Plus), and GLM-5 LLM diagnostics — delivering **minute-level, court-level** rain predictions and actionable play/cancel recommendations within a 0–2 hour window.

The system ships with a background data-fetching daemon, a modern OLED-dark-mode Bento Grid dashboard for real-time monitoring, and production Nginx deployment support.

**中文**

网球天气决策系统是一款面向户外网球场景的短临天气预报与决策工具。系统结合官方 QPF 短临降雨预报与自研 CAPPI 雷达光流外推算法、四层风险评估模型、五维可打率评分（含半物理场地蒸发模型）、多模态雷达视觉审查（Qwen 3.6 Plus）及 GLM-5 大语言模型，提供 0–2 小时内 **分钟级、场地级** 的降雨预测和打球建议。

系统包含后台数据采集守护进程、基于深色模式与 Bento Grid 布局的前端监控看板，以及生产环境 Nginx 部署配置。

---

## Features / 核心特性

| Feature | Description |
|:---|:---|
| **Dual-Engine Data Fusion** | Cross-validates official QPF short-range rainfall forecasts with self-developed CAPPI radar optical-flow extrapolation within a configurable detection radius (default: 7km). <br/> 双源数据融合：官方 QPF 短临预报与自研 CAPPI 雷达光流外推相互校验，可配置探测半径（默认 7km）。 |
| **AI Deep Diagnosis** | Leverages GLM-5 (with thinking mode) to generate human-readable, evidence-based weather reports — like having a professional meteorologist on call. <br/> AI 诊断：接入 GLM-5（思考模式），基于结构化数据生成可读性强的打球环境报告。 |
| **Multimodal Radar Visual QA** | Uses Qwen 3.6 Plus for radar image quality verification — automatically triggered when radar data conflicts with official forecasts, detecting bad frames, isolated pixels, and spurious echoes. <br/> 多模态雷达审查：使用 Qwen 3.6 Plus 对雷达拼图进行图像质量校验，自动在雷达与官方短临冲突时触发，检测坏帧、孤立像素和虚假回波。 |
| **Four-Layer Risk Engine** | Progressive analysis across Official QPF → Radar Modification → Surface Environment → Background Forecast layers, outputting a precise risk-score matrix with conservative decision interception. <br/> 四层风险模型：逐级分析官方短临层、雷达修正层、地面环境层与背景预报层，输出多维度风险评分。 |
| **Five-Dimensional Playability Scoring** | Multi-horizon playability assessment (now/30/60/120min) across five factors: precipitation & court wetness, thermal comfort (heat index), wind, AQI, and nowcast stability — with dynamic weight adjustment and hard veto logic. <br/> 五维可打率评分：按时间窗口（当前/30/60/120分钟）从降水与场地湿滑、体感温度（热指数）、风力、空气质量、短临稳定性五个维度综合评分，支持动态权重调整和硬否决机制。 |
| **Court Wetness State Model** | Semi-physical exponential decay model estimating court surface drying based on temperature, humidity, wind speed, and recent rainfall — enabling accurate "is the court still wet?" predictions even after rain stops. <br/> 场地蒸发模型：基于温度、湿度、风速和近期降雨量的半物理指数衰减模型，在雨停后依然能准确评估场地湿滑程度。 |
| **Real-Time Dashboard** | OLED dark-mode Bento Grid dashboard with 12-hour forecasts, 30/60/120-min precipitation probabilities, radar timeline player, playability gauge, and auto-refresh every 30 seconds. <br/> 实时监控看板：包含逐小时预报、降水概率、雷达回放、可打率仪表盘和 AI 诊断，每 30 秒自动刷新。 |
| **Booking Decision Engine** | Three-band lead-time aware decision system (0–2h / 2–6h / 6h+) with auto recheck scheduling and LLM-driven or rule-engine-only modes. <br/> 预约决策：按提前量分三档 (0–2h / 2–6h / 6h+) 给出建议，支持 LLM 驱动或纯规则引擎模式，自动安排复查时间。 |
| **Daemon Mode** | Background process with configurable refresh interval (default: 6 min), automatic cache cleanup, calibration logging, and next-update countdown. <br/> 守护进程模式：可配置刷新间隔、自动清理缓存、校准日志记录、下次更新倒计时。 |

---

## Architecture / 系统架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        main.py  (Orchestrator)                   │
│         Fetch → Analyze → Visual QA → Decide → Diagnose          │
└────────┬─────────────┬──────────────┬──────────────┬─────────────┘
         │             │              │              │
         ▼             ▼              ▼              ▼
  ┌─────────────┐ ┌──────────┐ ┌────────────┐ ┌──────────────────┐
  │ nowcast.py  │ │risk_     │ │diagnose_   │ │   frontend/      │
  │             │ │engine.py │ │forecast.py │ │                  │
  │ CAPPI Radar │ │          │ │            │ │  index.html      │
  │ Optical Flow│ │ 4-Layer  │ │ GLM-5 LLM  │ │  css/style.css   │
  │ dBZ Mapping │ │ Risk     │ │ Prompt Eng │ │  js/app.js       │
  │ QPF Parsing │ │ 5-Dim    │ │            │ │                  │
  │ Grid Weather│ │ Playable │ │ Qwen 3.6+  │ │                  │
  │ Contact Sht │ │ Wetness  │ │ Radar QA   │ │                  │
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
              │ radar_contact   │
              │ calibration_log │
              └─────────────────┘
```

### Module Breakdown / 模块说明

| Module | Role |
|:---|:---|
| **`main.py`** | Orchestrator — coordinates fetch, analysis, radar visual QA, decision, and diagnosis pipeline. Supports single-shot and daemon modes. Includes multimodal radar visual QA trigger logic and LLM-driven booking decisions. <br/> 主入口：协调数据抓取、分析、雷达视觉审查、决策与诊断流程，支持单次执行和守护模式。 |
| **`nowcast.py`** | Core algorithm — fetches CAPPI radar imagery, converts to dBZ grids, applies Farneback optical-flow for echo motion extrapolation, fetches grid-interpolated weather from dual APIs, generates radar contact sheets, and computes rain probability within a configurable radius. <br/> 核心算法模块：下载 CAPPI 雷达图、转换 dBZ 网格、光流外推回波运动、双 API 数据采集、生成雷达拼图、计算指定半径内降雨概率。 |
| **`risk_engine.py`** | Decision engine — frame quality control, dual-window trend analysis, upstream echo detection, four-layer risk scoring, five-dimensional playability scoring (with court wetness state model and heat index), booking decisions, and calibration logging. <br/> 风险决策引擎：帧质量控制、双窗口趋势分析、上游回波检测、四层风险评分、五维可打率评分（含场地蒸发模型和热指数）、预约决策与校准日志。 |
| **`diagnose_forecast.py`** | LLM diagnostic layer — builds precisely constrained prompts, queries GLM-5 with thinking mode, enforces conservative-language guardrails and banned-phrase detection, and outputs structured natural-language reports. <br/> LLM 诊断模块：构建约束提示词、调用 GLM-5（思考模式），输出结构化自然语言诊断报告，内含敏感措辞检测。 |
| **`serve_dashboard.py`** | Lightweight HTTP server — serves the static frontend and output data with no-cache headers for real-time data freshness. <br/> 轻量 HTTP 服务器：托管前端页面和输出数据，禁用缓存确保数据实时性。 |
| **`frontend/`** | Static dashboard — zero-state Bento Grid UI consuming JSON from `output/`, auto-refreshing every 30 seconds. <br/> 静态前端看板：读取 `output/` 目录的 JSON 数据，每 30 秒自动刷新。 |

---

## Quick Start / 快速上手

### Prerequisites / 环境准备

- **Python 3.8+**
- Dependencies:

```bash
pip install -r requirements.txt
```

Or install manually:

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

<details>
<summary><b>Advanced: Radar Visual QA modes / 进阶：雷达视觉审查模式</b></summary>

```bash
# Enable automatic radar visual QA (triggers on conflicting signals)
# 自动模式（雷达与官方短临冲突时触发审查）
python3 main.py --daemon --radar-vision auto

# Always run radar visual QA every cycle
# 每个周期都执行雷达视觉审查
python3 main.py --daemon --radar-vision always

# Disable radar visual QA (default)
# 关闭雷达视觉审查（默认）
python3 main.py --daemon --radar-vision off
```

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
| `forecast.json` | Unified analysis JSON — court metadata, optical-flow vectors, real-time weather, risk matrices, playability scores, and booking decisions for all time windows. <br/> 综合分析 JSON：包含场地信息、光流向量、实时天气、风险矩阵、可打率评分及各时间窗口的预约决策。 |
| `diagnosis.json` | Structured LLM diagnosis — headline, data support, reasoning chain, risk assessment, and actionable conclusion. <br/> LLM 诊断报告：包含标题、数据依据、推理过程、风险评估和结论。 |
| `debug_court_radius.png` | Radar overlay with 7km detection radius mask and court landmark — useful for manual review. <br/> 调试用雷达叠加图：显示 7km 探测半径和场地标记，便于人工复核。 |
| `radar_contact_sheet.jpg` | Composite radar frame contact sheet for multimodal visual QA input. <br/> 雷达帧拼图：供多模态视觉审查使用的合成雷达图。 |
| `calibration_log.jsonl` | Historical snapshot log for large-scale backtesting and probability threshold tuning. <br/> 校准日志：逐次快照记录，用于回测分析和阈值调优。 |
| `radar_frames/` | Individual timestamped CAPPI frames with court marker overlays for the timeline player. <br/> 逐帧雷达图：带时间戳和场地标记，供看板时间线播放器使用。 |

### Customizing Your Court Location / 自定义监控场地

The system defaults to "科技四路网球场" tennis court. To target your own court, edit the `COURT` variable at the top of `nowcast.py`:

```python
COURT = {
    "id": "my_tennis_court",
    "name": "My Tennis Court",
    "lon": 113.1234,  # Your court's longitude / 场地经度
    "lat": 22.5678,   # Your court's latitude  / 场地纬度
}

# Adjust detection radius based on local microclimate complexity
# 根据场地周边小气候复杂程度调整探测半径 (默认 7km)
RADIUS_KM = 7.0
```

### Environment Variables / 环境变量

Copy the example file and fill in your key:
复制示例文件并填入你的密钥：

```bash
cp .env.example .env
```

Edit `.env`:

```ini
# DashScope API Key (OpenAI-compatible, for GLM-5 + Qwen 3.6 Plus)
DASHSCOPE_API_KEY=sk-your_actual_key_here
```

| Variable | Required | Description |
|:---|:---:|:---|
| `DASHSCOPE_API_KEY` | Recommended | Your DashScope API key (OpenAI-compatible). Enables GLM-5 AI diagnosis and Qwen 3.6 Plus radar visual QA. <br/> DashScope API 密钥（兼容 OpenAI 接口），用于启用 AI 诊断和雷达视觉审查。 |
| `RADAR_VISION_MODE` | Optional | Default radar visual QA mode: `off`, `auto`, or `always`. Overridden by `--radar-vision` CLI flag. <br/> 默认雷达视觉审查模式，可被命令行参数覆盖。 |

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
  --radar-vision MODE      Radar visual QA mode: off|auto|always (default: off)
  --output PATH            Output JSON report path (default: output/forecast.json)
  --diagnosis-output PATH  LLM diagnosis output path (default: output/diagnosis.json)
  --debug-image PATH       Debug image path (default: output/debug_court_radius.png)
  --max-frames N           Max CAPPI frames to use (default: 12)
  --network-timeout SECS   Timeout for network/API requests (default: 30)
  --llm-timeout SECS       Timeout for LLM API operations (default: 45)
```

### Project Structure / 项目结构

```
weather_forcaster/
├── main.py                 # Entry point / orchestrator
├── nowcast.py              # CAPPI radar + optical flow engine
├── risk_engine.py          # Four-layer risk + five-dim playability + wetness model
├── diagnose_forecast.py    # LLM diagnostic layer (GLM-5)
├── serve_dashboard.py      # HTTP server for dashboard
├── requirements.txt        # Python dependencies
├── default                 # Nginx production config (reference)
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
│   ├── radar_contact_sheet.jpg
│   ├── calibration_log.jsonl
│   └── radar_frames/       # Timeline player frames
├── LICENSE                 # MIT License
├── .env                    # API keys (gitignored)
├── .gitignore
└── README.md               # This file
```

---

## How It Works / 工作原理

The system executes a **five-stage pipeline** on each cycle:

### Stage 1 — Data Acquisition / 数据采集
Fetches from two official APIs:
- **GD121 CAPPI Radar + QPF**: `wxc.gd121.cn` — radar imagery and 6-minute interval quantitative precipitation forecasts
- **Grid-Interpolated Realtime**: `ra.gd121.cn` — precise weather data at exact court coordinates (temp, humidity, wind, hourly & 7-day forecasts, 2-hour rain flag)

### Stage 2 — Radar Analysis / 雷达分析
- Downloads and caches CAPPI radar PNG frames
- Converts pixel colors to dBZ (reflectivity) using a calibrated 14-color palette via nearest-neighbor matching
- Applies **Farneback optical flow** (OpenCV) across frame pairs to estimate echo motion vectors
- Extrapolates cloud movement to compute **30/60/120-minute rain probability** within a 7km radius of the court
- Generates a radar contact sheet for multimodal visual QA
- Cross-validates radar extrapolation against official QPF data

### Stage 3 — Radar Visual QA (Conditional) / 雷达视觉审查（条件触发）
- Automatically triggered when radar signals conflict with official forecasts (e.g., radar echo but QPF clear, low motion consistency, isolated strong pixels)
- Sends a composite radar contact sheet to **Qwen 3.6 Plus** for image quality assessment
- Evaluates echo pattern, frame quality, upstream signal, and motion readability
- Adjusts radar evidence confidence (up/neutral/down) without overriding final weather decisions

### Stage 4 — Risk Assessment & Playability / 风险评估与可打率
**Four-layer fusion engine:**
1. **Official QPF Layer** — base risk from QPF + rain flag consensus
2. **Radar Modification** — adjustments from current echo intensity, trend analysis, upstream echo detection, and visual QA confidence
3. **Surface Environment** — humidity, current weather state, realtime rain gauge
4. **Background Forecast** — hourly forecast rain-keyword scanning

**Five-dimensional playability scoring (per horizon):**
1. **Precipitation & Court Wetness** — rain probability fused with a semi-physical court wetness model (exponential decay based on temperature, humidity, wind speed)
2. **Thermal Comfort** — heat index (Rothfusz regression) mapping to comfort score
3. **Wind** — impact on serve toss and ball trajectory
4. **Air Quality** — AQI-based outdoor exercise suitability
5. **Nowcast Stability** — data source conflict analysis

Outputs per-horizon risk scores, playability grades, and a booking decision with recheck scheduling.

### Stage 5 — AI Diagnosis (Optional) / AI 诊断（可选）
- Constructs a meticulously constrained prompt with all four layers of evidence
- Queries GLM-5 with thinking mode enabled for chain-of-thought reasoning
- Enforces **conservative language guardrails** — no absolute statements, no hallucinated sensor data, banned-phrase detection
- Outputs a structured JSON report with data summary, reasoning chain, risk assessment, and human-friendly conclusion
- LLM independently generates booking decisions which are merged with rule-engine metadata

---

## Deployment / 部署指南

### VPS Production Deployment / VPS 生产部署

The project includes an Nginx configuration file (`default`) for production deployment:

```bash
# 1. Clone the project on your VPS
git clone https://github.com/loremdai/tenniscourt_weatherforcaster.git /var/www/weather_forcaster

# 2. Install dependencies
cd /var/www/weather_forcaster
pip install -r requirements.txt

# 3. Copy Nginx config
sudo cp default /etc/nginx/sites-available/default
sudo nginx -t && sudo systemctl reload nginx

# 4. Start the daemon in background
nohup python3 main.py --daemon > /dev/null 2>&1 &
```

The Nginx config serves the frontend directly and proxies `output/` data with no-cache headers.
Nginx 配置直接托管前端静态文件，并为 `output/` 目录配置无缓存头。

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
- **Court surface types** — different drying models for hard/clay/grass courts

---

## License / 许可证

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

Copyright (c) 2026 [Alistair Dai](https://github.com/loremdai)

---

<p align="center">
  <sub>Built for uninterrupted tennis matches.</sub>
</p>
