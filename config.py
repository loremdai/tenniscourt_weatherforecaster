"""
集中配置模块

所有可调常量、API 端点、阈值和超参数都汇集于此文件中，
其他模块统一通过 ``from config import XXX`` 获取配置值，
避免在代码中散落硬编码。

配置分区：
    1. 球场位置
    2. 雷达分析参数
    3. 地理 / 时区常量
    4. 外部 API 配置
    5. LLM 大模型配置
    6. 风险引擎超参数
    7. LLM 输出审查
    8. 仪表盘服务端口
"""

from __future__ import annotations

import os
from datetime import timedelta, timezone

import numpy as np


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 球场位置
# ═══════════════════════════════════════════════════════════════════════════════

# 当前分析目标球场的基本信息。
# lon/lat 是所有 API 请求和雷达裁切的中心坐标，
# 修改后整条数据链路（请求位置、雷达分析区域、预报输出）都会联动变化。
COURT = {
    "id": "Keji 4th Road Tennis Court",  # 球场英文标识，用于日志和数据标记
    "name": "科技四路网球场",  # 中文名称，显示在前端界面
    "lon": 113.55,  # 经度（WGS-84）
    "lat": 22.39,  # 纬度（WGS-84）
}

# 备选球场（取消注释即可切换）：
# COURT = {
#     "id": "qiaoguang_commercial_centre",
#     "name": "侨光商业中心",
#     "lon": 113.54,
#     "lat": 22.20,
# }
# COURT = {
#     "id": "Haibo Garden Bld.4",
#     "name": "海波花园四栋",
#     "lon": 113.52,
#     "lat": 22.25,
# }

# 雷达分析半径（公里）。
# 只统计球场周围此半径范围内的雷达回波像素。
# 值越大统计越宏观、漏报率低，但误报率会上升（远处降水被纳入）。
RADIUS_KM = 5.0


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 雷达分析参数
# ═══════════════════════════════════════════════════════════════════════════════

# --- dBZ（雷达反射率因子）阈值体系 ---
# dBZ 是衡量降水强度的核心单位，值越大降水越强。

# 最低有意义回波阈值：低于此值的像素视为"无降水"，不参与任何统计。
RADAR_ECHO_THRESHOLD_DBZ = 15

# 影响打球的回波阈值：达到此值的像素开始计入"不可打"覆盖率。
PLAYABLE_RAIN_THRESHOLD_DBZ = 25

# 中等降水 / 对流风险阈值：风险引擎中对应"中等风险"的权重跃升点。
DBZ_MODERATE = 35

# 强回波阈值：超过此值可能触发安全否决机制，建议避免户外运动。
DBZ_STRONG = 45

# 向后兼容别名，在 risk_engine.py 中使用。
# DBZ_NONE 等效于 RADAR_ECHO_THRESHOLD_DBZ (15)
# DBZ_WEAK 等效于 PLAYABLE_RAIN_THRESHOLD_DBZ (25)
DBZ_NONE = RADAR_ECHO_THRESHOLD_DBZ
DBZ_WEAK = PLAYABLE_RAIN_THRESHOLD_DBZ

# --- 图像处理参数 ---
# CAPPI 雷达图为 RGBA 格式，alpha 值低于此阈值的像素视为透明（底图背景），
# 不参与 dBZ 颜色反算。
ALPHA_THRESHOLD = 8

# --- 预报时段配置 ---
# 键为时段标签，值为对应的 CAPPI 帧步数（每帧间隔 6 分钟）。
# 例如 "30min" 对应 5 帧 × 6分钟 = 30分钟。
HORIZONS = {"30min": 5, "60min": 10, "120min": 20}

# --- CAPPI 雷达色卡查找表 ---
# 每个元素为 (dBZ 值, RGB 颜色) 的对应关系。
# 用于将雷达图像的像素颜色反推（最近邻匹配）为 dBZ 数值，
# 从而实现对雷达图片的定量化分析。
# 颜色值取自广东气象局 CAPPI 产品的实际图例。
DBZ_PALETTE = [
    (5, (0, 221, 208)),  # 极弱回波，几乎无降水意义
    (10, (0, 169, 214)),  # 极弱回波
    (15, (5, 51, 245)),  # 最低有意义回波（对应 RADAR_ECHO_THRESHOLD_DBZ）
    (20, (0, 238, 0)),  # 弱回波，毛毛雨级别
    (25, (0, 214, 50)),  # 弱-中回波，小雨（对应 PLAYABLE_RAIN_THRESHOLD_DBZ）
    (30, (0, 141, 31)),  # 中等回波，小到中雨
    (35, (255, 242, 0)),  # 中等回波，中雨（对应 DBZ_MODERATE）
    (40, (229, 201, 0)),  # 较强回波，中到大雨
    (45, (255, 140, 20)),  # 强回波，大雨（对应 DBZ_STRONG）
    (50, (255, 41, 41)),  # 强回波，暴雨
    (55, (201, 20, 20)),  # 很强回波，大暴雨
    (60, (123, 0, 0)),  # 极强回波，特大暴雨
    (65, (255, 77, 255)),  # 冰雹或超强对流
    (70, (153, 73, 191)),  # 极端回波（罕见）
]


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 地理 / 时区常量
# ═══════════════════════════════════════════════════════════════════════════════

# WGS-84 椭球体的平均地球半径（公里），用于 Haversine 公式计算经纬度距离。
EARTH_RADIUS_KM = 6371.0088

# 广东气象数据的时区（UTC+8），解析 GD121 API 返回的时间戳时使用。
SOURCE_TZ = timezone(timedelta(hours=8))


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 外部 API 配置
# ═══════════════════════════════════════════════════════════════════════════════

# ---- GD121 主 API（CAPPI 雷达 + QPF 定量降水预报）----
# 数据源：广东省气象局 GD121 平台。
# URL 中 {lon} 和 {lat} 会在运行时替换为 COURT 的坐标。
# DISTRICTCODE=440402 为珠海市香洲区的行政区划代码。
API_URL_TEMPLATE = (
    "https://wxc.gd121.cn/gdecloud/servlet/servletcityweatherall4?"
    "DISTRICTCODE=440402&LNG={lon}&LAT={lat}&FROM=binfen"
)

# 请求头：模拟微信小程序"缤纷微天气"的访问环境。
# 修改 User-Agent 或 Referer 可能导致 API 拒绝请求。
API_HEADERS = {
    "Host": "wxc.gd121.cn",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://mp.gd121.cn",
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 26_4_2 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
        "MicroMessenger/8.0.73(0x18004923) NetType/4G Language/zh_CN "
        "miniProgram/wx4e37a66956191c3a"
    ),
    "Referer": "https://mp.gd121.cn/",
    "Accept-Language": "en-US,en;q=0.9",
}

# ---- Grid 网格气象 API（精细化格点实况）----
# 数据源：ra.gd121.cn，返回经纬度插值后的实时天气（温湿风）、
# 逐时预报和 7 天预报。
RA_API_URL_TEMPLATE = (
    "https://ra.gd121.cn/grid/api/index/weatherInfo?"
    "longitude={lon}&latitude={lat}&FROM=binfen"
)

# 请求头：同样模拟微信小程序环境。
RA_API_HEADERS = {
    "Host": "ra.gd121.cn",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://mp.gd121.cn",
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 26_4_2 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
        "MicroMessenger/8.0.73(0x18004923) NetType/4G Language/zh_CN "
        "miniProgram/wx4e37a66956191c3a"
    ),
    "Referer": "https://mp.gd121.cn/",
    "Accept-Language": "en-US,en;q=0.9",
}


# ═══════════════════════════════════════════════════════════════════════════════
# 5. LLM 大模型配置
# ═══════════════════════════════════════════════════════════════════════════════

# 阿里云百炼平台（DashScope）的 OpenAI 兼容 API 端点。
# 所有 LLM 调用都通过此 base URL 发起。
LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# 文本推理/诊断模型：负责综合分析气象数据并输出预约决策建议。
LLM_DIAGNOSIS_MODEL = "deepseek-v4-pro"

# 多模态视觉模型：负责对 CAPPI 雷达拼图进行图像质检和回波审查。
RADAR_VISION_MODEL = "qwen3.6-plus"

# 雷达视觉审查的安全回退值（fallback）。
# 当视觉审查失败、被跳过或超时时返回此字典。
# 所有字段设为 "unknown" / "neutral"，确保不会影响正常的规则引擎决策。
RADAR_VISUAL_QA_FALLBACK = {
    "quality": "unknown",  # 图像质量：未知
    "echo_pattern": "unknown",  # 回波形态：未知
    "near_court_signal": "unknown",  # 球场附近信号：未知
    "upstream_signal": "unknown",  # 上游方向信号：未知
    "trend": "unknown",  # 趋势：未知
    "motion_readable": True,  # 运动可读性：默认可读
    "radar_confidence_adjustment": "neutral",  # 雷达置信度调整：中性（不上调也不下调）
    "reason_cn": "雷达视觉审查未完成，本轮按常规雷达规则处理。",
}


# ═══════════════════════════════════════════════════════════════════════════════
# 6. 风险引擎超参数
# ═══════════════════════════════════════════════════════════════════════════════

# --- 相对湿度阈值（百分比） ---
# 用于评估湿度对打球体验和球场状态的影响。
RH_LOW = 70.0  # 低于此值：湿度舒适，对打球无影响
RH_MID = 85.0  # 达到此值：开始影响体感舒适度和球场抓地力
RH_HIGH = 90.0  # 达到此值：高湿度预警，球场可能偏滑

# --- 雷达趋势分析的时间加权系数 ---
# 数组元素从旧到新排列，越近的帧权重越大。
# 加权后能更好地反映"当前正在发生什么"而非"过去发生过什么"。

# 6 帧趋势权重（覆盖约 36 分钟），用于帧数充足时的趋势计算。
TREND_WEIGHTS_6 = np.array([0.5, 0.7, 0.9, 1.1, 1.3, 1.5])

# 3 帧趋势权重（覆盖约 18 分钟），在帧数不足 6 时作为降级方案。
TREND_WEIGHTS_3 = np.array([0.7, 1.0, 1.3])

# --- 降水天气关键词集合 ---
# 用于匹配逐时预报文本中的降水类型描述。
# 若某时段的 "weather" 字段包含这些关键词，则视为该时段有降水。
RAIN_KEYWORDS = {"小雨", "中雨", "大雨", "暴雨", "雷阵雨", "阵雨", "雷雨"}

# --- 上游回波检测 ---
# 上游方向回波（≥25 dBZ）覆盖率的最低阈值。
# 低于 2% 不视为有效的上游降水威胁，避免零散噪点误触发预警。
MIN_UPSTREAM_COVERAGE_25 = 0.02

# --- 可打性（Playability）综合评分 ---

# 五维度基础权重，用于加权计算可打性总分。
# rain（降雨指标）和 thermal（体感指标）各占 30%，权重最高；
# nowcast（短临预报）和 wind（风力）各占 15%；
# aqi（空气质量）占 10%。
PLAYABILITY_BASE_WEIGHTS: dict[str, float] = {
    "rain": 0.30,  # 降雨指标权重
    "thermal": 0.30,  # 体感温度指标权重
    "nowcast": 0.15,  # 短临预报指标权重
    "wind": 0.15,  # 风力指标权重
    "aqi": 0.10,  # 空气质量指标权重
}

# 总分 → 等级映射表，从高到低匹配。
# 元组格式：(最低分, 中文等级, 英文标识)
# 例如：总分 ≥ 85 → "极佳"，总分 ≥ 70 → "良好"，以此类推。
PLAYABILITY_GRADE_TABLE = [
    (85, "极佳", "conditions_excellent"),  # 完美打球条件
    (70, "良好", "conditions_good"),  # 适宜打球
    (55, "一般", "conditions_fair"),  # 勉强可以，需关注天气变化
    (40, "较差", "conditions_poor"),  # 不太适合，建议备选方案
    (25, "差", "conditions_bad"),  # 不建议打球
    (0, "不宜", "conditions_unsuitable"),  # 不应打球
]


# ═══════════════════════════════════════════════════════════════════════════════
# 7. LLM 输出审查
# ═══════════════════════════════════════════════════════════════════════════════

# LLM 输出中的禁用短语列表。
# 天气预报中不应出现绝对化、过度乐观的表述，这是专业气象表达的基本规范。
# 若 LLM 诊断输出中包含以下任一短语，将触发 _tone_warnings 警告，
# 并在 Langfuse 中记录为质量扣分项。
BANNED_PHRASES = [
    "完全无风险",
    "完全没有雨",
    "绝对不会",
    "绝对安全",
    "完全排除",
    "空中无降水粒子",
    "无任何降雨威胁",
    "球场保持干燥",
    "地面干燥",
    "地面干燥，无湿滑风险",
    "无任何降水回波",
    "对比赛无实质影响",
    "完全排除降雨可能",
]


# ═══════════════════════════════════════════════════════════════════════════════
# 8. 高德地图 API 配置
# ═══════════════════════════════════════════════════════════════════════════════

# 高德 Web 服务 API Key，用于地址搜索（Input Tips）功能。
# 通过后端代理转发调用，不暴露给前端。
AMAP_API_KEY = os.getenv("AMAP_MAPS_API_KEY", "")

# 输入提示（自动补全）API 端点。
# 文档：https://lbs.amap.com/api/webservice/guide/api/inputtips
AMAP_INPUT_TIPS_URL = "https://restapi.amap.com/v3/assistant/inputtips"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. 仪表盘服务端口
# ═══════════════════════════════════════════════════════════════════════════════

# 前端仪表盘静态文件 HTTP 服务的监听端口。
# 部署时需确保此端口未被占用，且 Nginx 反向代理指向此端口。
DASHBOARD_PORT = 2081
