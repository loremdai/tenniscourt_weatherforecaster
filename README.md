# Tennis Weather Forecaster

Tennis Weather Forecaster (网球天气决策系统) 是一个专为网球场及户外运动场景设计的高精度短临天气预报与智能决策引擎。系统基于官方气象数据源，结合自研的雷达光流外推算法、四层递进式风险评估模型以及 DeepSeek 大语言模型，为您提供 0-2 小时内“精确到分钟、精确到场地”的降雨预测及打球建议。

该系统包含一个常驻后台的数据抓取与分析守护进程，以及一个采用极致暗黑模式 (OLED Dark Mode) 与 Bento Grid 布局设计的现代化前端监控看板。

---

## 核心特性
- 双擎数据融合：官方 QPF 短临降雨预报与自研 CAPPI 雷达光流外推算法相互交叉验证。
- AI 深度诊断：接入 DeepSeek-V4-Pro，像专业气象播报员一样出具易读、可靠的打球环境诊断报告。
- 四层风控模型：逐级分析 雷达层、格点层、降雨层 与 背景场，输出精准 Risk Score 矩阵，支持极度保守的决策拦截。
- 极客风可视化：包含 12 小时预报、30/60/120 分钟降水概率、雷达动态捕捉的实时可视化大屏。

---

## 面向使用者 (User Guide)

本章节面向直接使用该系统进行打球决策或部署本地监控看板的用户。

### 1. 环境准备
确保您的计算机上已安装 Python 3.8 或以上版本。打开终端安装必要的依赖：
```bash
pip install openai opencv-python numpy Pillow
```

### 2. 启动系统

系统分为后台数据预报服务与前端可视化看板，请在终端中分别启动两个服务：

**A. 启动后台天气预报守护进程**
打开一个终端，在 `weather_forcaster` 目录下执行以下命令开启常驻监控，系统默认每 6 分钟拉取一次最新数据并更新缓存（内部会自动清理过期雷达图片避免占用空间）：
```bash
python3 main.py --daemon
```
> **进阶用法 (指定目标时间与打球时长)**:
> 
> 如果您想针对特定的时间点（例如今晚 20:00，计划打 2 小时），可以结合 `--target-time` 和 `--play-duration` 参数运行。系统会持续关注并为您评估该特定时间段的下雨风险：
> ```bash
> python3 main.py --target-time 20:00 --play-duration 120 --daemon
> ```
> *(注：如果不加 `--daemon`，则只会执行一次即刻诊断；加了 `--daemon` 系统会每 6 分钟刷新并重新评估目标时间的风险。)*

**B. 启动前端监控看板服务**
打开另一个新终端窗口（保持刚刚的进程继续运行），启动本地网页服务：
```bash
python3 serve_dashboard.py
```

### 3. 查看监控看板
成功启动上述两个服务后，打开您的浏览器，访问：
[http://localhost:8080/frontend/index.html](http://localhost:8080/frontend/index.html)

网页将会自动每 30 秒从后台拉取最新的预报文件并刷新 UI，提供无缝的天气监控体验。

---

## 面向开发者 (Developer Guide)

本章节面向希望深入理解系统架构、参与二次开发或定制自有场地的开发者。

### 1. 架构与核心模块

本系统主要由四大模块协同工作：
- **`nowcast.py` (雷达光流计算)**: 核心算法模块。负责抓取官方接口中的 CAPPI 雷达图，将其转换为网格化 dBZ 强度阵列，并应用 OpenCV 的 Farneback 光流算法计算云团移动向量，推算指定场地范围（默认 5km）内的物理降雨概率。
- **`risk_engine.py` (风险决策引擎)**: 执行严格的四层检查机制。综合评估当前状态、未来演变、上游回波以及背景气象场，输出结构化的打球窗口决策 (`booking_decision`) 和安全提示。
- **`diagnose_forecast.py` (LLM AI 诊断)**: 负责构建精密的 Prompt Context 并调用 DeepSeek 接口，在严格约束模型“保守幻觉”的前提下，对结构化数据进行提炼，输出自然语言诊断结果。
- **`main.py` (主入口统筹)**: 统筹协调抓取、推算、决策与诊断流程，支撑单次触发与 Daemon 常驻循环，并维护回测校准日志 (`calibration_log.jsonl`) 的写入。
- **前端系统 (`frontend/`)**: 轻量级的静态数据消费端，基于 Tailwind CSS 构建的无状态页面，逻辑存放在 `app.js`。

### 2. 输出文件字典 (`output/`)
系统的数据流最终收敛于 `output/` 目录，前端也是通过访问该目录实现数据剥离与解耦：
- `forecast.json`: 原始分析数据的大一统 JSON，涵盖场地图表、光流计算向量、实时天气与各时间窗口风险矩阵。
- `diagnosis.json`: 结构化的 LLM 诊断报告，包含核心标题、数据支持、推理过程和最终结论。
- `debug_court_radius.png`: 带有 5km 探测半径遮罩和目标地标标识的实时雷达落点图，主要用于人工复核或调试。
- `calibration_log.jsonl`: 历史校验日志，用于在后期做大规模回测分析以验证并微调概率阈值。

### 3. 如何定制监控场地？
系统目前默认对准了“侨光商业中心”网球场。如果您想将其应用到您的专属场地，请打开 `nowcast.py` 并在文件顶部修改 `COURT` 变量：
```python
COURT = {
    "id": "my_tennis_court",
    "name": "我的专属网球场",
    "lon": 113.1234, # 您场地的准确经度
    "lat": 22.5678,  # 您场地的准确纬度
}
# 您也可以根据场地周边的小气候复杂程度，调节探测半径 (默认 5km)
RADIUS_KM = 5.0
```

### 4. 环境变量配置
要启用深度 AI 诊断功能，建议您配置个人专属的 DashScope API Key（兼容 OpenAI 规范）：
```bash
export DASHSCOPE_API_KEY="您的_API_KEY"
```
（如果不配置，系统将尝试读取代码内的默认演示 Key。您也可以通过向 `main.py` 传入 `--no-llm` 参数完全关闭 AI 推理环节，让系统以极简的纯算力规则引擎模式极速运行）。

---
Created for uninterrupted tennis matches. Never let sudden rain ruin a good game again.
