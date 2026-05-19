"""LLM service layer for weather diagnosis and radar visual QA.

Centralizes all LLM API interactions including:
- Langfuse observability initialization
- Unified JSON parsing / repair for LLM outputs
- Streaming completion reception with thinking-mode support
- Text-based weather diagnosis (deepseek-v4-pro)
- Multimodal radar visual QA (qwen3.6-plus)
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from config import (
    BANNED_PHRASES,
    LLM_BASE_URL,
    LLM_DIAGNOSIS_MODEL,
    RADAR_VISION_MODEL,
    RADAR_VISUAL_QA_FALLBACK,
)
from nowcast import check_qpf_rain
from risk_engine import compute_playability, compute_risk_scores


# ═══════════════════════════════════════════════════════════════════════════════
# Langfuse LLM Observability
# ═══════════════════════════════════════════════════════════════════════════════

try:
    from langfuse import observe as langfuse_observe
    from langfuse import Langfuse

    _langfuse = Langfuse()
    LANGFUSE_AVAILABLE = _langfuse.auth_check()
    if LANGFUSE_AVAILABLE:
        print("Langfuse LLM observability: enabled", flush=True)
    else:
        print(
            "Langfuse: auth check failed, running without observability.",
            flush=True,
        )
except Exception:
    LANGFUSE_AVAILABLE = False
    _langfuse = None

    def langfuse_observe(*args, **kwargs):  # noqa: F811 – fallback no-op decorator
        if args and callable(args[0]):
            return args[0]

        def _wrap(fn):
            return fn

        return _wrap


def flush_langfuse() -> None:
    """Flush pending Langfuse traces.  Safe to call even when disabled."""
    if LANGFUSE_AVAILABLE and _langfuse:
        try:
            _langfuse.flush()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Unified JSON Parsing
# ═══════════════════════════════════════════════════════════════════════════════


def parse_llm_json(text: str, *, salvage: bool = True) -> dict[str, Any]:
    """Extract and parse a JSON object from LLM output text.

    Handles:
    - Markdown code fences (```json ... ```)
    - Outermost { } extraction
    - Chinese-quote salvage (when *salvage* is True)

    Returns the parsed dict, or a fallback ``{"error": ...}`` dict on failure.
    """
    raw = text.strip()

    # Strip markdown code fences
    if raw.startswith("```"):
        first_nl = raw.find("\n")
        raw = raw[first_nl + 1:] if first_nl > 0 else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    # Find outermost JSON object
    start_idx = raw.find("{")
    end_idx = raw.rfind("}")
    if start_idx >= 0 and end_idx > start_idx:
        json_str = raw[start_idx: end_idx + 1]
    else:
        json_str = raw

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        if not salvage:
            raise
        print(f"Warning: Failed to parse LLM output as JSON: {e}", file=sys.stderr)
        print("Attempting to salvage by fixing common issues...", file=sys.stderr)

        # Best-effort: replace unescaped inner double quotes in Chinese text
        fixed = re.sub(
            r'(?<=[\u4e00-\u9fff])"(?=[\u4e00-\u9fff])',
            "\u201c",
            json_str,
        )
        fixed = re.sub(
            r'(?<=[\u4e00-\u9fff])"(?=[\u4e00-\u9fff,\u3002\uff0c])',
            "\u201d",
            fixed,
        )
        try:
            parsed = json.loads(fixed)
            print("Salvage succeeded after fixing Chinese quotes.", file=sys.stderr)
            return parsed
        except json.JSONDecodeError:
            return {"error": "JSON Parse Error", "raw_output": text}


# ═══════════════════════════════════════════════════════════════════════════════
# Streaming Completion
# ═══════════════════════════════════════════════════════════════════════════════


def stream_llm_completion(completion) -> str:
    """Consume a streaming chat completion, print thinking/answer, return answer text.

    Works with DashScope's ``enable_thinking`` mode that produces
    ``reasoning_content`` and ``content`` deltas.
    """
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
    return answer_content


# ═══════════════════════════════════════════════════════════════════════════════
# Text-based LLM Diagnosis
# ═══════════════════════════════════════════════════════════════════════════════


def run_llm_diagnosis(
    context: dict[str, Any],
    api_key: str | None,
    output_path: Path,
    timeout: float = 45.0,
) -> dict[str, Any] | None:
    """Run LLM diagnosis and return parsed result."""
    # Lazy import to avoid circular dependency with diagnose_forecast
    from diagnose_forecast import PROMPT_TEMPLATE, check_banned_phrases

    try:
        from langfuse.openai import OpenAI
    except ImportError:
        print(
            "Warning: langfuse/openai package not installed, skipping LLM diagnosis.",
            file=sys.stderr,
        )
        return None

    key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
    prompt = PROMPT_TEMPLATE.format(
        context=json.dumps(context, ensure_ascii=False, indent=2)
    )

    client = OpenAI(api_key=key, base_url=LLM_BASE_URL, timeout=timeout)

    print(f"Sending context to {LLM_DIAGNOSIS_MODEL} for analysis...", flush=True)
    try:
        completion = client.chat.completions.create(
            model=LLM_DIAGNOSIS_MODEL,
            messages=[{"role": "user", "content": prompt}],
            extra_body={"enable_thinking": True},
            stream=True,
            stream_options={"include_usage": True},
        )
    except Exception as e:
        print(f"LLM API call failed: {e}", file=sys.stderr)
        return None

    answer_content = stream_llm_completion(completion)
    parsed = parse_llm_json(answer_content)

    # Banned phrase check
    banned = check_banned_phrases(answer_content)
    if banned:
        parsed["_tone_warnings"] = banned
        print(
            f"Warning: LLM used {len(banned)} banned phrase(s): {banned}",
            file=sys.stderr,
        )

    # Langfuse quality scoring
    if LANGFUSE_AVAILABLE and _langfuse:
        try:
            _langfuse.score_current_trace(
                name="json_parse_success",
                value=0.0 if "error" in parsed else 1.0,
                comment="LLM output JSON parsed successfully"
                if "error" not in parsed
                else "JSON parse failed, used fallback",
            )
            _langfuse.score_current_trace(
                name="banned_phrase_count",
                value=float(len(banned)),
                comment=f"Banned phrases: {banned}" if banned else "No banned phrases",
            )
        except Exception:
            pass  # Non-critical

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(parsed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Saved LLM diagnosis to {output_path}")
    return parsed


# ═══════════════════════════════════════════════════════════════════════════════
# Radar Visual QA (Multimodal)
# ═══════════════════════════════════════════════════════════════════════════════


def _normalized_radar_visual_qa(value: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize and validate radar visual QA output against allowed enums."""
    result = dict(RADAR_VISUAL_QA_FALLBACK)
    if not isinstance(value, dict):
        return result

    enums = {
        "quality": {"good", "degraded", "bad", "unknown"},
        "echo_pattern": {
            "none", "trace", "scattered_weak",
            "organized_band", "convective_cells", "unknown",
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
    """Return a neutral fallback with a custom skip reason."""
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
    """Use multimodal model as a radar visual QA assistant with neutral fallback."""
    contact_sheet = report.get("mapping_debug", {}).get("radar_contact_sheet", "")
    if not contact_sheet or not Path(contact_sheet).exists():
        return _radar_visual_qa_skip("未找到雷达拼图，无法进行视觉审查。")

    key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
    if not key:
        return _radar_visual_qa_skip("未配置多模态 API key，雷达视觉审查已跳过。")

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
        from langfuse.openai import OpenAI

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
        return _normalized_radar_visual_qa(parse_llm_json(content, salvage=False))
    except Exception as exc:
        return _radar_visual_qa_skip(f"雷达视觉审查失败，已按常规雷达规则处理：{exc}")


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
