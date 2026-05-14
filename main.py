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
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

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
)
from risk_engine import booking_decision, save_calibration_log
from diagnose_forecast import extract_context, PROMPT_TEMPLATE, check_banned_phrases


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
        "--daemon", action="store_true",
        help="Fetch live data from API (online mode).",
    )
    parser.add_argument(
        "--interval", type=int, default=360,
        help="Seconds between refreshes in daemon mode (default: 360).",
    )

    # Booking
    parser.add_argument(
        "--target-time", default="now",
        help="Booking start time: HH:MM or 'now' (default: now).",
    )
    parser.add_argument(
        "--play-duration", type=int, default=120,
        help="Play duration in minutes (default: 120).",
    )

    # Output
    parser.add_argument(
        "--output", default="output/forecast.json",
        help="Output JSON report path.",
    )
    parser.add_argument(
        "--diagnosis-output", default="output/diagnosis.json",
        help="Output JSON for LLM diagnosis.",
    )
    parser.add_argument(
        "--debug-image", default="output/debug_court_radius.png",
        help="Debug image path. Use '' to skip.",
    )
    parser.add_argument(
        "--max-frames", type=int, default=12,
        help="Max CAPPI frames to use.",
    )

    # LLM
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Skip LLM diagnosis (rule-engine only).",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="DashScope API key (default: from env DASHSCOPE_API_KEY).",
    )

    return parser.parse_args()


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
        print(f"Warning: Cannot parse target-time '{target_time_str}', using 'now'.",
              file=sys.stderr)
        return 0.0, now.strftime("%H:%M")


def run_llm_diagnosis(context: dict[str, Any], api_key: str | None,
                      output_path: Path) -> dict[str, Any] | None:
    """Run LLM diagnosis and return parsed result."""
    import os
    try:
        from openai import OpenAI
    except ImportError:
        print("Warning: openai package not installed, skipping LLM diagnosis.",
              file=sys.stderr)
        return None

    key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
    prompt = PROMPT_TEMPLATE.format(context=json.dumps(context, ensure_ascii=False, indent=2))

    client = OpenAI(
        api_key=key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    print("Sending context to deepseek-v4-pro for analysis...")
    try:
        completion = client.chat.completions.create(
            model="deepseek-v4-pro",
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
            raw = raw[first_nl + 1:]
        else:
            raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    # Find outermost JSON object
    start_idx = raw.find("{")
    end_idx = raw.rfind("}")
    if start_idx >= 0 and end_idx > start_idx:
        json_str = raw[start_idx:end_idx + 1]
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
            '\u201c',  # Replace with left Chinese quote
            json_str,
        )
        fixed = re.sub(
            r'(?<=[\u4e00-\u9fff])"(?=[\u4e00-\u9fff,\u3002\uff0c])',
            '\u201d',  # Replace with right Chinese quote
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
        print(f"Warning: LLM used {len(banned)} banned phrase(s): {banned}",
              file=sys.stderr)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(parsed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Saved LLM diagnosis to {output_path}")
    return parsed


def run_once(args: argparse.Namespace) -> None:
    """Single execution cycle: fetch → analyze → decide → diagnose."""
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M:%S")

    # ---- Step 1: Fetch data ----
    grid_data = None
    if args.daemon:
        print(f"[{ts}] Fetching live data for lon={COURT['lon']}, lat={COURT['lat']}...")
        payload = fetch_weather_data(COURT["lon"], COURT["lat"])
        grid_data = fetch_grid_weather(COURT["lon"], COURT["lat"])
    else:
        print(f"[{ts}] Loading local response from {args.response}...")
        payload = load_response(Path(args.response))

    # ---- Step 2: Radar analysis + risk scores ----
    row = first_row(payload)
    bounds = parse_bounds(row)
    entries = collect_cappi(row, args.max_frames)
    frames = load_frames(entries, Path("data/cappi"))
    report = build_report(row, bounds, frames, args.debug_image, grid_data)

    # ---- Step 3: Booking decision ----
    lead_hours, target_str = compute_lead_time(args.target_time, now)
    risk_scores = report.get("risk_scores", {})
    station = report.get("station_realtime", {})

    from nowcast import check_qpf_rain
    qpf_all_zero = not check_qpf_rain(row, 20)

    booking = booking_decision(
        risk_scores=risk_scores,
        lead_time_hours=lead_hours,
        target_time_str=target_str,
        play_duration_minutes=args.play_duration,
        hourly_forecast=station.get("hourly_forecast", []),
        seven_day_forecast=station.get("seven_day_forecast", []),
        qpf6min_all_zero=qpf_all_zero,
        rain_flag=station.get("rain_2h_flag", 0) or 0,
        now=now,
    )
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
    print(f"  🎾 预约决策: {booking['decision_cn']}")
    print(f"  📅 打球窗口: {booking['play_window']}")
    print(f"  ⏱  距开场:   {booking['lead_time_hours']}h ({booking['lead_time_band']})")
    print(f"  🔄 建议复查: {booking['check_again_at']}")
    for r in booking["reason"]:
        print(f"  · {r}")
    if booking["caveat"] and booking["caveat"][0] != "无特别注意事项":
        for c in booking["caveat"]:
            print(f"  ⚠ {c}")
    print("=" * 50 + "\n")

    # ---- Step 5: LLM diagnosis (optional) ----
    if not args.no_llm:
        context = extract_context(report)
        # Inject booking info into context for LLM
        context["booking"] = booking
        run_llm_diagnosis(context, args.api_key, Path(args.diagnosis_output))


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
