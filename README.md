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
                         ┌──────────────────────┐
                         │      main.py         │
                         │    CLI 入口 + 参数    │
                         └──────────┬───────────┘
                                    │
                         ┌──────────▼───────────┐
                         │    pipeline.py       │
                         │   流水线编排 + 守护    │
                         └──┬─────┬────────┬────┘
                            │     │        │
              ┌─────────────▼┐    │   ┌────▼──────────────┐
              │  nowcast.py  │    │   │  llm_service.py   │
              │  雷达协调层   │    │   │  LLM + Langfuse   │
              │  (re-export) │    │   └────┬──────────────┘
        ┌─────┴──────┐       │    │        │
        │            │       │    │   ┌────▼──────────────┐
   ┌────▼────┐ ┌─────▼─────┐ │    │   │diagnose_forecast │
   │  data   │ │  radar    │ │    │   │   GLM-5 提示工程  │
   │ fetcher │ │ processor │ │    │   └──────────────────┘
   │  API/IO │ │ 图像+光流  │ │    │
   └─────────┘ └───────────┘ │    │
                      ┌──────▼────▼──────────┐
                      │   risk_engine.py     │
                      │  四层风险 (re-export) │
                      └──┬───────────┬───────┘
                    ┌────▼────┐ ┌────▼──────────┐
                    │playabi- │ │booking_engine │
                    │lity.py  │ │  预约决策引擎  │
                    │五维评分  │ └───────────────┘
                    └─────────┘
   ┌──────────┐                  ┌──────────────────┐
   │config.py │  ← 全局配置       │   frontend/      │
   │utils.py  │  ← 公共工具       │   OLED Bento UI  │
   └──────────┘                  └──────┬───────────┘
                                        │ reads JSON
                      ┌─────────────────▼────────────┐
                      │serve_dashboard.py → output/  │
                      └──────────────────────────────┘
```

### Module Breakdown / 模块说明

| Module | Lines | Role |
|:---|:---:|:---|
| **`main.py`** | 270 | CLI entry point — parses arguments and invokes the pipeline. <br/> CLI 入口：解析命令行参数，调用 pipeline。 |
| **`config.py`** | 316 | Centralized configuration — all hyperparameters, API templates, thresholds, and constants. <br/> 集中配置：所有超参数、API 模板、阈值和常量。 |
| **`utils.py`** | 37 | Shared utilities — lightweight `.env` loader. <br/> 公共工具：轻量 `.env` 加载器。 |
| **`pipeline.py`** | 519 | Workflow orchestration — coordinates fetch → analyze → visual QA → decide → diagnose. Supports single-shot and daemon modes. <br/> 流水线编排：协调数据采集、分析、视觉审查、决策和诊断，支持单次和守护模式。 |
| **`data_fetcher.py`** | 175 | Data I/O layer — GD121 API requests, grid weather API, CAPPI frame download, and cache cleanup. <br/> 数据获取层：API 请求、CAPPI 帧下载与缓存管理（纯 I/O，零计算）。 |
| **`radar_processor.py`** | 584 | Radar image processing — pixel→dBZ conversion, geo-coordinate mapping, Farneback optical flow, dBZ extrapolation, area statistics, rain probability, and visualization. <br/> 雷达处理层：像素→dBZ 反算、坐标转换、光流估计、帧外推、统计分析与可视化。 |
| **`nowcast.py`** | 642 | Radar coordination — data structures (Bounds/Frame), API response parsing, station data extraction, and report assembly (`build_report`). Re-exports `data_fetcher` and `radar_processor` for backward compatibility. <br/> 雷达协调层：数据结构定义、响应解析、气象站数据提取和报告组装，通过 re-export 保持下游兼容。 |
| **`risk_engine.py`** | 497 | Core risk computation — frame quality control, dual-window trend analysis, upstream echo detection, four-layer fusion risk scoring, and calibration logging. Re-exports `playability` and `booking_engine`. <br/> 风险计算核心：帧质量、趋势分析、上游检测、四层融合评分和标定日志。 |
| **`playability.py`** | 590 | Playability scoring — five-dimensional assessment (rain/thermal/wind/AQI/nowcast) with semi-physical court wetness model, dynamic weight adjustment, hard veto logic, and per-horizon grades. <br/> 可打性评分：五维子评分 + 场地湿度衰减模型 + 动态权重 + 否决机制。 |
| **`booking_engine.py`** | 248 | Booking decisions — three-band lead-time logic (0–2h/2–6h/6h+) with hourly forecast scanning, pre-window rain detection, and recheck scheduling. <br/> 预约决策引擎：按提前量分三档决策 + 逐时预报扫描 + 复查时间安排。 |
| **`llm_service.py`** | 496 | LLM integration — DashScope API calls (GLM-5 + Qwen 3.6 Plus), radar visual QA trigger logic, and Langfuse observability. <br/> LLM 服务层：DashScope 调用、雷达视觉审查触发、Langfuse 可观测性。 |
| **`diagnose_forecast.py`** | 360 | LLM prompt engineering — constrained prompts for GLM-5, conservative language guardrails, banned-phrase detection, and structured report output. <br/> LLM 提示工程：约束提示词、保守措辞护栏、敏感词检测和结构化报告。 |
| **`serve_dashboard.py`** | 242 | HTTP server — serves frontend and output data with no-cache headers. <br/> HTTP 服务器：托管前端和输出数据，禁用缓存。 |
| **`frontend/`** | — | Static dashboard — OLED dark-mode Bento Grid UI, auto-refreshes every 30 seconds. <br/> 前端看板：深色模式 Bento Grid 布局，每 30 秒刷新。 |

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

The system defaults to "科技四路网球场" tennis court. To target your own court, edit the `COURT` variable in `config.py`:

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
│
│── Entry & Orchestration ──────────────────────────
├── main.py                 # CLI entry point / CLI 入口
├── config.py               # Centralized configuration / 集中配置
├── utils.py                # Shared utilities / 公共工具
├── pipeline.py             # Workflow orchestration / 流水线编排
│
│── Radar Data Pipeline ────────────────────────────
├── data_fetcher.py         # API I/O + cache / 数据获取与缓存
├── radar_processor.py      # Image processing + optical flow / 雷达图像处理
├── nowcast.py              # Radar coordination + report / 雷达协调与报告组装
│
│── Risk & Decision ────────────────────────────────
├── risk_engine.py          # Four-layer risk scoring / 四层风险评分
├── playability.py          # Five-dim playability / 五维可打性评分
├── booking_engine.py       # Booking decisions / 预约决策引擎
│
│── LLM Layer ──────────────────────────────────────
├── llm_service.py          # LLM API + Langfuse / LLM 服务层
├── diagnose_forecast.py    # GLM-5 prompt engineering / 提示工程
│
│── Frontend & Server ──────────────────────────────
├── serve_dashboard.py      # HTTP server / 看板服务器
├── frontend/
│   ├── index.html          # Dashboard UI (Bento Grid)
│   ├── css/style.css       # OLED dark mode styles
│   └── js/app.js           # Dashboard logic + auto-refresh
│
│── Data & Output ──────────────────────────────────
├── data/cappi/             # Cached CAPPI radar PNG frames
├── output/
│   ├── forecast.json       # Latest analysis report
│   ├── diagnosis.json      # Latest LLM diagnosis
│   ├── debug_court_radius.png
│   ├── radar_contact_sheet.jpg
│   ├── calibration_log.jsonl
│   └── radar_frames/       # Timeline player frames
│
├── requirements.txt        # Python dependencies
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

```bash
# 1. Clone the project on your VPS
git clone https://github.com/loremdai/tenniscourt_weatherforecaster.git /var/www/weather_forcaster

# 2. Install dependencies
cd /var/www/weather_forcaster
pip install -r requirements.txt

# 3. Configure Nginx to serve frontend/ and output/ with no-cache headers
# 参考 Nginx 配置：托管 frontend/ 目录和 output/ 数据，设置无缓存头

# 4. Start the daemon in background
nohup python3 main.py --daemon > /dev/null 2>&1 &
```

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
