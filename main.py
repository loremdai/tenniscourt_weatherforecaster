#!/usr/bin/env python3
"""Unified entry point for tennis court weather decision system.

Usage:
    # Immediate nowcast (default: "now")
    python3 main.py

    # Booking decision for tonight 8pm, 2h session
    python3 main.py --target-time 20:00 --play-duration 120

    # Daemon mode: refresh every 6 minutes
    python3 main.py --daemon --interval 360 --target-time 20:00

    # Skip LLM diagnosis (faster, rule-engine only)
    python3 main.py --target-time 20:00 --no-llm
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def _load_dotenv(path: str = ".env") -> None:
    """Load key=value pairs from a .env file into os.environ."""
    env_path = Path(path)
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

# Local modules
from nowcast import (
    COURT,
    fetch_weather_data,
    fetch_grid_weather,
    load_response,
    first_row,
    parse_bounds,
    collect_cappi,
    load_frames,
    build_report,
    check_qpf_rain,
)
from risk_engine import (
    booking_decision,
    compute_playability,
    compute_risk_scores,
    save_calibration_log,
)
from diagnose_forecast import extract_context, PROMPT_TEMPLATE, check_banned_phrases


LLM_BASE_URL = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
RADAR_VISION_MODEL = "qwen3.6-plus"

RADAR_VISUAL_QA_FALLBACK = {
    "quality": "unknown",
    "echo_pattern": "unknown",
    "near_court_signal": "unknown",
    "upstream_signal": "unknown",
    "trend": "unknown",
    "motion_readable": True,
    "radar_confidence_adjustment": "neutral",
    "reason_cn": "雷达视觉审查未完成，本轮按常规雷达规则处理。",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tennis court weather decision system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # Data source
    parser.add_argument(
        "--response",
        default="response.txt",
        help="Local JSON response file (offline mode).",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Fetch live data from API (online mode).",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=360,
        help="Seconds between refreshes in daemon mode (default: 360).",
    )

    # Booking
    parser.add_argument(
        "--target-time",
        default="now",
        help="Booking start time: HH:MM or 'now' (default: now).",
    )
    parser.add_argument(
        "--play-duration",
        type=int,
        default=120,
        help="Play duration in minutes (default: 120).",
    )

    # Output
    parser.add_argument(
        "--output",
        default="output/forecast.json",
        help="Output JSON report path.",
    )
    parser.add_argument(
        "--diagnosis-output",
        default="output/diagnosis.json",
        help="Output JSON for LLM diagnosis.",
    )
    parser.add_argument(
        "--debug-image",
        default="output/debug_court_radius.png",
        help="Debug image path. Use '' to skip.",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=12,
        help="Max CAPPI frames to use.",
    )
    parser.add_argument(
        "--network-timeout",
        type=float,
        default=30.0,
        help="Timeout in seconds for each network/API request (default: 30).",
    )

    # LLM
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM diagnosis (rule-engine only).",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="DashScope API key (default: from env DASHSCOPE_API_KEY).",
    )
    parser.add_argument(
        "--llm-timeout",
        type=float,
        default=45.0,
        help="Timeout in seconds for each LLM API read/write operation (default: 45).",
    )
    parser.add_argument(
        "--radar-vision",
        choices=("off", "auto", "always"),
        default=os.getenv("RADAR_VISION_MODE", "off"),
        help=(
            "Radar multimodal QA mode: off=never call, auto=only controversial "
            "radar samples, always=call every cycle (default: env RADAR_VISION_MODE or off)."
        ),
    )

    args = parser.parse_args()
    if args.radar_vision not in {"off", "auto", "always"}:
        print(
            f"Warning: invalid RADAR_VISION_MODE={args.radar_vision!r}; using 'off'.",
            file=sys.stderr,
        )
        args.radar_vision = "off"
    return args


def compute_lead_time(target_time_str: str, now: datetime) -> tuple[float, str]:
    """Return (lead_time_hours, normalized_target_str HH:MM)."""
    if target_time_str.lower() == "now":
        return 0.0, now.strftime("%H:%M")

    try:
        parts = target_time_str.split(":")
        t_h, t_m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        target = now.replace(hour=t_h, minute=t_m, second=0, microsecond=0)
        # If target is in the past, assume tomorrow
        if target < now:
            target += timedelta(days=1)
        lead = (target - now).total_seconds() / 3600.0
        return max(0.0, lead), f"{t_h:02d}:{t_m:02d}"
    except (ValueError, IndexError):
        print(
            f"Warning: Cannot parse target-time '{target_time_str}', using 'now'.",
            file=sys.stderr,
        )
        return 0.0, now.strftime("%H:%M")


def run_llm_diagnosis(
    context: dict[str, Any],
    api_key: str | None,
    output_path: Path,
    timeout: float = 45.0,
) -> dict[str, Any] | None:
    """Run LLM diagnosis and return parsed result."""
    try:
        from openai import OpenAI
    except ImportError:
        print(
            "Warning: openai package not installed, skipping LLM diagnosis.",
            file=sys.stderr,
        )
        return None

    key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
    prompt = PROMPT_TEMPLATE.format(
        context=json.dumps(context, ensure_ascii=False, indent=2)
    )

    client = OpenAI(
        api_key=key,
        base_url=LLM_BASE_URL,
        timeout=timeout,
    )

    print("Sending context to deepseek-v4-pro for analysis...", flush=True)
    try:
        completion = client.chat.completions.create(
            model="glm-5",
            messages=[{"role": "user", "content": prompt}],
            extra_body={"enable_thinking": True},
            stream=True,
            stream_options={"include_usage": True},
        )
    except Exception as e:
        print(f"LLM API call failed: {e}", file=sys.stderr)
        return None

    reasoning_content = ""
    answer_content = ""
    is_answering = False

    print("\n" + "=" * 20 + " 模型思考过程 " + "=" * 20 + "\n")

    for chunk in completion:
        if not chunk.choices:
            if hasattr(chunk, "usage") and chunk.usage:
                print("\n" + "=" * 20 + " Token 消耗 " + "=" * 20 + "\n")
                print(chunk.usage)
            continue

        delta = chunk.choices[0].delta

        if hasattr(delta, "reasoning_content") and delta.reasoning_content is not None:
            if not is_answering:
                sys.stdout.write(delta.reasoning_content)
                sys.stdout.flush()
                reasoning_content += delta.reasoning_content

        if hasattr(delta, "content") and delta.content is not None:
            if not is_answering:
                print("\n\n" + "=" * 20 + " 分析报告 " + "=" * 20 + "\n")
                is_answering = True
            sys.stdout.write(delta.content)
            sys.stdout.flush()
            answer_content += delta.content

    print("\n\n" + "=" * 50)

    # Robust JSON extraction: find outermost { } pair
    raw = answer_content.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        first_nl = raw.find("\n")
        if first_nl > 0:
            raw = raw[first_nl + 1 :]
        else:
            raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    # Find outermost JSON object
    start_idx = raw.find("{")
    end_idx = raw.rfind("}")
    if start_idx >= 0 and end_idx > start_idx:
        json_str = raw[start_idx : end_idx + 1]
    else:
        json_str = raw

    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"Warning: Failed to parse LLM output as JSON: {e}", file=sys.stderr)
        print(f"Attempting to salvage by fixing common issues...", file=sys.stderr)
        # Last resort: try to fix unescaped Chinese quotes
        import re

        # Replace unescaped inner double quotes that break JSON
        # (this is a best-effort heuristic, not a full JSON fixer)
        fixed = re.sub(
            r'(?<=[\u4e00-\u9fff])"(?=[\u4e00-\u9fff])',
            "\u201c",  # Replace with left Chinese quote
            json_str,
        )
        fixed = re.sub(
            r'(?<=[\u4e00-\u9fff])"(?=[\u4e00-\u9fff,\u3002\uff0c])',
            "\u201d",  # Replace with right Chinese quote
            fixed,
        )
        try:
            parsed = json.loads(fixed)
            print("Salvage succeeded after fixing Chinese quotes.", file=sys.stderr)
        except json.JSONDecodeError:
            parsed = {"error": "JSON Parse Error", "raw_output": answer_content}

    # Banned phrase check
    banned = check_banned_phrases(answer_content)
    if banned:
        parsed["_tone_warnings"] = banned
        print(
            f"Warning: LLM used {len(banned)} banned phrase(s): {banned}",
            file=sys.stderr,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(parsed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Saved LLM diagnosis to {output_path}")
    return parsed


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        first_nl = raw.find("\n")
        raw = raw[first_nl + 1 :] if first_nl > 0 else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()
    start_idx = raw.find("{")
    end_idx = raw.rfind("}")
    if start_idx >= 0 and end_idx > start_idx:
        raw = raw[start_idx : end_idx + 1]
    return json.loads(raw)


def _normalized_radar_visual_qa(value: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(RADAR_VISUAL_QA_FALLBACK)
    if not isinstance(value, dict):
        return result

    enums = {
        "quality": {"good", "degraded", "bad", "unknown"},
        "echo_pattern": {
            "none",
            "trace",
            "scattered_weak",
            "organized_band",
            "convective_cells",
            "unknown",
        },
        "near_court_signal": {"none", "weak", "moderate", "strong", "unknown"},
        "upstream_signal": {"none", "trace", "weak", "organized", "unknown"},
        "trend": {"weakening", "stable", "strengthening", "unclear", "unknown"},
        "radar_confidence_adjustment": {"down", "neutral", "up"},
    }
    for key, allowed in enums.items():
        raw = value.get(key)
        if raw in allowed:
            result[key] = raw
    if isinstance(value.get("motion_readable"), bool):
        result["motion_readable"] = value["motion_readable"]
    reason = value.get("reason_cn")
    if isinstance(reason, str) and reason.strip():
        result["reason_cn"] = reason.strip()[:160]
    return result


def _radar_visual_qa_skip(reason: str) -> dict[str, Any]:
    fallback = dict(RADAR_VISUAL_QA_FALLBACK)
    fallback["reason_cn"] = reason
    return fallback


def should_run_radar_visual_qa(
    report: dict[str, Any], row: dict[str, Any]
) -> tuple[bool, str]:
    """Return whether multimodal radar QA is worth spending on this cycle."""
    station = report.get("station_realtime", {})
    current = report.get("current", {})
    upstream = report.get("upstream_echo", {})
    motion = report.get("motion", {})
    rain_probability = report.get("rain_probability", {})
    frame_quality = report.get("frame_quality", [])

    qpf_has_rain = check_qpf_rain(row, 20)
    rain_flag = station.get("rain_2h_flag", 0) or 0
    rain_5m = float(station.get("rain_5m_mm", 0) or 0)
    rain_1h = float(station.get("rain_1h_mm", 0) or 0)
    official_clear = (
        not qpf_has_rain and rain_flag == 0 and rain_5m == 0 and rain_1h == 0
    )

    max_dbz = float(current.get("max_dbz_nearby", 0) or 0)
    echo_coverage = float(current.get("echo_coverage", 0) or 0)
    playable_coverage = float(current.get("playable_coverage", 0) or 0)
    motion_consistency = float(motion.get("consistency", 1) or 0)
    rain_prob_30 = float(rain_probability.get("30min", 0) or 0)

    if official_clear and 15 <= max_dbz < 35:
        return True, "official_clear_but_weak_radar_echo"
    if max_dbz >= 35 and playable_coverage < 0.01 and echo_coverage < 0.03:
        return True, "isolated_strong_radar_pixel"
    if motion_consistency < 0.4 and max_dbz >= 15:
        return True, "low_motion_confidence_with_echo"
    if qpf_has_rain and max_dbz < 15:
        return True, "qpf_rain_but_radar_clear"
    if official_clear and rain_prob_30 >= 0.3:
        return True, "radar_extrapolation_conflicts_with_official_clear"
    if (
        upstream.get("has_upstream_echo")
        and float(upstream.get("upstream_coverage_25", 0) or 0) >= 0.03
        and max_dbz < 25
        and rain_5m == 0
    ):
        return True, "organized_upstream_watch"
    if any(isinstance(q, (int, float)) and q < 0.7 for q in frame_quality):
        return True, "radar_frame_quality_suspected"

    return False, "no_radar_visual_qa_trigger"


def run_radar_visual_qa(
    report: dict[str, Any], api_key: str | None, timeout: float = 45.0
) -> dict[str, Any]:
    """Use qwen3.6-plus as a radar visual QA assistant with neutral fallback."""
    contact_sheet = report.get("mapping_debug", {}).get("radar_contact_sheet", "")
    if not contact_sheet or not Path(contact_sheet).exists():
        fallback = dict(RADAR_VISUAL_QA_FALLBACK)
        fallback["reason_cn"] = "未找到雷达拼图，无法进行视觉审查。"
        return fallback

    key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
    if not key:
        fallback = dict(RADAR_VISUAL_QA_FALLBACK)
        fallback["reason_cn"] = "未配置多模态 API key，雷达视觉审查已跳过。"
        return fallback

    context_json = json.dumps(
        {
            "current": report.get("current"),
            "trends": report.get("trends"),
            "upstream_echo": report.get("upstream_echo"),
            "motion": report.get("motion"),
            "official_qpf6min_summary": report.get("official_qpf6min_summary"),
            "station_realtime": {
                k: report.get("station_realtime", {}).get(k)
                for k in (
                    "weather_state",
                    "rain_5m_mm",
                    "rain_1h_mm",
                    "humidity_pct",
                )
            },
        },
        ensure_ascii=False,
    )

    prompt = f"""你是雷达图像质检助手，只审查输入的 CAPPI 雷达拼图。
任务：判断图像质量、回波形态、球场附近和上游方向是否有组织化回波、最近几帧趋势是否可读。
严格限制：
1. 不判断能不能打球，不输出降雨概率，不覆盖官方短临。
2. 只依据图片和给定结构化字段描述雷达图像证据。
3. 若只有孤立像素、零散弱回波、坏帧或无可追踪回波，应降低雷达可信度。
4. 输出必须是合法 JSON，不要 Markdown，不要额外解释。

字段枚举：
quality: good | degraded | bad | unknown
echo_pattern: none | trace | scattered_weak | organized_band | convective_cells | unknown
near_court_signal: none | weak | moderate | strong | unknown
upstream_signal: none | trace | weak | organized | unknown
trend: weakening | stable | strengthening | unclear | unknown
motion_readable: true | false
radar_confidence_adjustment: down | neutral | up

结构化参考：
{context_json}

输出 JSON：
{{
  "quality": "...",
  "echo_pattern": "...",
  "near_court_signal": "...",
  "upstream_signal": "...",
  "trend": "...",
  "motion_readable": true,
  "radar_confidence_adjustment": "...",
  "reason_cn": "只描述雷达图像证据的一句话"
}}
"""

    try:
        from openai import OpenAI

        image_bytes = Path(contact_sheet).read_bytes()
        image_url = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode(
            "ascii"
        )
        client = OpenAI(api_key=key, base_url=LLM_BASE_URL, timeout=timeout)
        completion = client.chat.completions.create(
            model=RADAR_VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            temperature=0,
        )
        content = completion.choices[0].message.content or ""
        return _normalized_radar_visual_qa(_extract_json_object(content))
    except Exception as exc:
        fallback = dict(RADAR_VISUAL_QA_FALLBACK)
        fallback["reason_cn"] = f"雷达视觉审查失败，已按常规雷达规则处理：{exc}"
        return fallback


def apply_radar_visual_qa_to_report(
    report: dict[str, Any],
    row: dict[str, Any],
    radar_visual_qa: dict[str, Any],
) -> dict[str, Any]:
    """Recompute risk/playability after visual QA adjusts radar confidence."""
    station = report.get("station_realtime", {})
    qpf_has_rain = check_qpf_rain(row, 20)
    rain_flag = station.get("rain_2h_flag", 0) or 0
    current = report.get("current", {})
    current_stats = {
        "max_dbz": current.get("max_dbz_nearby", 0),
        "echo_coverage": current.get("echo_coverage", 0),
        "playable_coverage": current.get("playable_coverage", 0),
        "mean_rain_rate": current.get("mean_rain_rate", 0),
    }
    risk_scores = compute_risk_scores(
        current_stats=current_stats,
        rain_probability=report.get("rain_probability", {}),
        trends=report.get("trends", {}),
        upstream=report.get("upstream_echo", {}),
        grid_realtime=station,
        qpf6min_all_zero=not qpf_has_rain,
        rain_flag=rain_flag,
        motion_consistency=report.get("motion", {}).get("consistency", 0),
        hourly_forecast=station.get("hourly_forecast", []),
        radar_visual_qa=radar_visual_qa,
    )
    report["radar_visual_qa"] = radar_visual_qa
    report["risk_scores"] = risk_scores
    report["playability"] = compute_playability(
        rain_probability=report.get("rain_probability", {}),
        risk_scores=risk_scores,
        grid_realtime=station,
        qpf_has_rain=qpf_has_rain,
        rain_flag=rain_flag,
        current_stats=current_stats,
    )
    return risk_scores


def run_once(args: argparse.Namespace) -> None:
    """Single execution cycle: fetch → analyze → decide → diagnose."""
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M:%S")

    # ---- Step 1: Fetch data ----
    grid_data = None
    if args.daemon:
        print(
            f"[{ts}] Fetching live data for lon={COURT['lon']}, lat={COURT['lat']}...",
            flush=True,
        )
        print(f"[{ts}] Fetching official nowcast API...", flush=True)
        payload = fetch_weather_data(
            COURT["lon"], COURT["lat"], timeout=args.network_timeout
        )
        print(f"[{ts}] Official nowcast API returned.", flush=True)
        print(f"[{ts}] Fetching grid weather API...", flush=True)
        grid_data = fetch_grid_weather(
            COURT["lon"], COURT["lat"], timeout=args.network_timeout
        )
        print(f"[{ts}] Grid weather API returned.", flush=True)
    else:
        print(f"[{ts}] Loading local response from {args.response}...", flush=True)
        payload = load_response(Path(args.response))

    # ---- Step 2: Radar analysis + risk scores ----
    print(f"[{ts}] Parsing radar metadata...", flush=True)
    row = first_row(payload)
    bounds = parse_bounds(row)
    entries = collect_cappi(row, args.max_frames)
    print(f"[{ts}] Loading {len(entries)} CAPPI radar frame(s)...", flush=True)
    frames = load_frames(
        entries, Path("data/cappi"), download_timeout=args.network_timeout
    )
    print(f"[{ts}] Building radar report...", flush=True)
    report = build_report(row, bounds, frames, args.debug_image, grid_data)

    # ---- Step 3: Booking Decision ----
    lead_hours, target_str = compute_lead_time(args.target_time, now)

    risk_scores = report.get("risk_scores", {})
    station = report.get("station_realtime", {})

    qpf_all_zero = not check_qpf_rain(row, 20)
    run_visual_qa, visual_qa_reason = should_run_radar_visual_qa(report, row)
    report["radar_visual_qa_trigger"] = {
        "mode": args.radar_vision,
        "run": False,
        "reason": visual_qa_reason,
    }
    if args.no_llm:
        print(f"[{ts}] Skipping radar visual QA because --no-llm is set.", flush=True)
        radar_visual_qa = _radar_visual_qa_skip("已按 --no-llm 跳过雷达视觉审查。")
    elif args.radar_vision == "off":
        print(
            f"[{ts}] Skipping radar visual QA because --radar-vision=off.", flush=True
        )
        radar_visual_qa = _radar_visual_qa_skip(
            "雷达视觉审查开关关闭，本轮未调用多模态模型。"
        )
    elif args.radar_vision == "auto" and not run_visual_qa:
        print(
            f"[{ts}] Skipping radar visual QA: no controversial radar sample.",
            flush=True,
        )
        radar_visual_qa = _radar_visual_qa_skip(
            "本轮雷达与官方短临、实况雨量无明显冲突，未触发视觉审查。"
        )
    else:
        print(
            f"[{ts}] Running radar visual QA ({args.radar_vision}, {visual_qa_reason})...",
            flush=True,
        )
        report["radar_visual_qa_trigger"]["run"] = True
        radar_visual_qa = run_radar_visual_qa(
            report, args.api_key, timeout=args.llm_timeout
        )
    risk_scores = apply_radar_visual_qa_to_report(report, row, radar_visual_qa)

    # Always generate base booking to get metadata like play_window
    base_booking = booking_decision(
        risk_scores=risk_scores,
        lead_time_hours=lead_hours,
        target_time_str=target_str,
        play_duration_minutes=args.play_duration,
        hourly_forecast=station.get("hourly_forecast", []),
        seven_day_forecast=station.get("seven_day_forecast", []),
        qpf6min_all_zero=qpf_all_zero,
        rain_flag=station.get("rain_2h_flag", 0) or 0,
        grid_realtime=station,
        now=now,
    )

    # Compute next_rain_time
    def _get_next_rain(n: datetime, rpt: dict, st: dict) -> str:
        w_state = st.get("weather_state", "")
        r_5m = float(st.get("rain_5m_mm", 0) or 0)
        kw_list = ["雨", "雪", "雹", "冰"]
        if r_5m > 0 or any(k in w_state for k in kw_list):
            return "当前正在下雨"

        # Priority 1.5: Radar extrapolation (0-2h)
        max_dbz = rpt.get("max_dbz_nearby", {})
        visual = rpt.get("radar_visual_qa", {})
        radar_can_hint = (
            visual.get("quality") != "bad"
            and visual.get("motion_readable", True) is not False
            and visual.get("echo_pattern") not in {"none", "trace", "scattered_weak"}
            and visual.get("radar_confidence_adjustment") != "down"
        )
        if radar_can_hint:
            for hz in ["30min", "60min", "120min"]:
                if max_dbz.get(hz, 0) >= 25:
                    mins = int(hz.replace("min", ""))
                    from datetime import timedelta

                    t = n + timedelta(minutes=mins)
                    return f"雷达提示约 {t.strftime('%H:%M')} 需复查"

        # Priority 2: QPF
        qpf = rpt.get("official_qpf6min", [])
        for p in qpf:
            try:
                r_val = float(p.get("r", 0))
            except:
                r_val = 0.0
            if r_val > 0.05:
                dt_str = p.get("dt", "")
                parts = dt_str.split(" ")
                return f"约 {(parts[1] if len(parts) > 1 else dt_str)[:5]}"

        hrly = st.get("hourly_forecast", [])
        for h in hrly:
            t_str = h.get("time", "")
            try:
                h_hour = int(t_str.split(":")[0])
            except:
                continue
            if h_hour < n.hour and len(hrly) > 6:
                break  # crossed midnight
            if any(k in h.get("weather", "") for k in kw_list):
                return f"约 {t_str}"

        return "预估未来无雨"

    base_booking["next_rain_time"] = _get_next_rain(now, report, station)
    if "window_hourly_rain_count" in base_booking:
        del base_booking["window_hourly_rain_count"]

    if args.no_llm:
        # ---- Track 1: Rule Engine ----
        booking = base_booking

        # Post-validation consistency check between playability and booking
        playability = report.get("playability", {}).get("30min", {})
        if playability.get("score", -1) == 0 and "cancel" not in booking.get(
            "decision", ""
        ):
            booking["decision"] = "suggest_cancel"
            booking["decision_cn"] = "建议取消或改期"
            booking["reason"].insert(0, "综合可打率评估为不可打，触发安全否决机制")

        report["booking"] = booking
    else:
        # ---- Track 2: LLM Driven ----
        context = extract_context(report)
        # Pass the base_booking as meta so LLM knows target, window, and rain_count
        context["booking_meta"] = base_booking

        # Run LLM diagnosis and decision
        diagnosis = run_llm_diagnosis(
            context,
            args.api_key,
            Path(args.diagnosis_output),
            timeout=args.llm_timeout,
        )

        # Merge LLM booking decisions over the base metadata
        llm_booking = diagnosis.get("booking", {}) if diagnosis else {}
        if not llm_booking:
            llm_booking = {
                "decision": "keep_but_recheck",
                "decision_cn": "大模型未返回决策，建议赛前复查",
                "check_again_at": "",
                "reason": ["解析诊断失败"],
                "caveat": [],
            }

        booking = {**base_booking, **llm_booking}

        # Post-validation consistency check against rule engine's playability
        playability = report.get("playability", {}).get("30min", {})
        if playability.get("score", -1) == 0 and "cancel" not in booking.get(
            "decision", ""
        ):
            booking["decision"] = "suggest_cancel"
            booking["decision_cn"] = "建议取消或改期"
            booking["reason"].insert(0, "AI决策已被安全否决机制覆盖，强降雨预警")

        report["booking"] = booking

    # ---- Step 4: Save forecast + calibration log ----
    save_calibration_log(report, risk_scores)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[{ts}] Wrote {output}")

    # ---- Print booking summary to console ----
    print("\n" + "=" * 50)
    print(f"  🎾 预约决策: {booking.get('decision_cn', '')}")
    print(f"  📅 打球窗口: {booking.get('play_window', '')}")
    print(
        f"  ⏱  距开场:   {booking.get('lead_time_hours', '')}h ({booking.get('lead_time_band', '')})"
    )
    if booking.get("check_again_at"):
        print(f"  🔄 建议复查: {booking.get('check_again_at', '')}")
    for r in booking.get("reason", []):
        print(f"  · {r}")
    if booking.get("caveat") and booking.get("caveat")[0] != "无特别注意事项":
        for c in booking.get("caveat", []):
            print(f"  ⚠ {c}")
    print("=" * 50 + "\n")

    # ---- Step 6: Stamp next_update_at after ALL processing (incl. LLM) ----
    if args.daemon:
        from datetime import timezone

        next_at = datetime.now().astimezone() + timedelta(seconds=args.interval)
        report["next_update_at"] = next_at.isoformat(timespec="seconds")
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def main() -> int:
    args = parse_args()

    if args.daemon:
        target_label = args.target_time if args.target_time != "now" else "实时"
        print(f"Starting daemon. Target: {target_label}, Interval: {args.interval}s")
        while True:
            try:
                run_once(args)
            except Exception as e:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{ts}] Error: {e}", file=sys.stderr)
            print(f"Waiting {args.interval} seconds...")
            time.sleep(args.interval)
    else:
        run_once(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
