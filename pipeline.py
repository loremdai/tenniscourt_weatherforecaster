"""Forecast pipeline orchestration.

Coordinates the full weather forecast cycle:
data fetch → radar analysis → visual QA → booking decision → LLM diagnosis → output.
"""

from __future__ import annotations

import copy
import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import argparse

from config import COURT
from nowcast import (
    fetch_weather_data,
    fetch_grid_weather,
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
from diagnose_forecast import extract_context
from llm_service import (
    langfuse_observe,
    run_llm_diagnosis,
    should_run_radar_visual_qa,
    run_radar_visual_qa,
    apply_radar_visual_qa_to_report,
    _radar_visual_qa_skip,
)


def is_location_changed_or_first_run(
    args: argparse.Namespace, current_court: dict[str, Any]
) -> bool:
    """Check if the location has changed or if it is the first run."""
    forecast_path = Path(args.output)
    diag_path = Path(args.diagnosis_output)
    if not forecast_path.is_file() or not diag_path.is_file():
        return True

    try:
        old_forecast = json.loads(forecast_path.read_text(encoding="utf-8"))
        old_court = old_forecast.get("court", {})

        lon_match = (
            abs(float(old_court.get("lon", 0)) - float(current_court["lon"]))
            < 1e-4
        )
        lat_match = (
            abs(float(old_court.get("lat", 0)) - float(current_court["lat"]))
            < 1e-4
        )
        name_match = old_court.get("name") == current_court["name"]

        if not (lon_match and lat_match and name_match):
            return True

        old_diag = json.loads(diag_path.read_text(encoding="utf-8"))
        if old_diag.get("llm_generating") and len(old_diag) <= 2:
            return True

        return False
    except Exception:
        return True


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


def compute_next_rain_time(
    now: datetime, report: dict[str, Any], station: dict[str, Any]
) -> str:
    """Estimate the next rain time from multiple data sources."""
    w_state = station.get("weather_state", "")
    r_5m = float(station.get("rain_5m_mm", 0) or 0)
    kw_list = ["雨", "雪", "雹", "冰"]

    if r_5m > 0 or any(k in w_state for k in kw_list):
        return "当前正在下雨"

    # Radar extrapolation (0-2h)
    max_dbz = report.get("max_dbz_nearby", {})
    visual = report.get("radar_visual_qa", {})
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
                t = now + timedelta(minutes=mins)
                return f"雷达提示约 {t.strftime('%H:%M')} 需复查"

    # QPF
    qpf = report.get("official_qpf6min", [])
    for p in qpf:
        try:
            r_val = float(p.get("r", 0))
        except (ValueError, TypeError):
            r_val = 0.0
        if r_val > 0.05:
            dt_str = p.get("dt", "")
            parts = dt_str.split(" ")
            return f"约 {(parts[1] if len(parts) > 1 else dt_str)[:5]}"

    # Hourly forecast
    hrly = station.get("hourly_forecast", [])
    for h in hrly:
        t_str = h.get("time", "")
        try:
            h_hour = int(t_str.split(":")[0])
        except (ValueError, IndexError):
            continue
        if h_hour < now.hour and len(hrly) > 6:
            break  # crossed midnight
        if any(k in h.get("weather", "") for k in kw_list):
            return f"约 {t_str}"

    return "预估未来无雨"


# ═══════════════════════════════════════════════════════════════════════════════
# Console Output
# ═══════════════════════════════════════════════════════════════════════════════


def _print_booking_summary(booking: dict[str, Any]) -> None:
    """Print a human-readable booking decision summary to console."""
    print("\n" + "=" * 50)
    print(f"  🎾 预约决策: {booking.get('decision_cn', '')}")
    print(f"  📅 打球窗口: {booking.get('play_window', '')}")
    print(
        f"  ⏱  距开场:   {booking.get('lead_time_hours', '')}h "
        f"({booking.get('lead_time_band', '')})"
    )
    if booking.get("check_again_at"):
        print(f"  🔄 建议复查: {booking.get('check_again_at', '')}")
    for r in booking.get("reason", []):
        print(f"  · {r}")
    if booking.get("caveat") and booking.get("caveat")[0] != "无特别注意事项":
        for c in booking.get("caveat", []):
            print(f"  ⚠ {c}")
    print("=" * 50 + "\n")


def run_background_visual_qa_flow(
    report_copy: dict[str, Any],
    row_copy: dict[str, Any],
    args: argparse.Namespace,
    now: datetime,
) -> None:
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] [Async Visual QA] Background thread started.", flush=True)

        # 1. Run visual QA API call (takes a few seconds)
        radar_visual_qa = run_radar_visual_qa(
            report_copy, args.api_key, timeout=args.llm_timeout
        )
        print(
            f"[{ts}] [Async Visual QA] Visual QA finished. Result: "
            f"{radar_visual_qa.get('radar_confidence_adjustment')}",
            flush=True,
        )

        # 2. Update report with visual QA results
        report_copy["radar_visual_qa"] = radar_visual_qa
        risk_scores = apply_radar_visual_qa_to_report(
            report_copy, row_copy, radar_visual_qa
        )

        # 3. Re-run booking decision
        lead_hours, target_str = compute_lead_time(args.target_time, now)
        qpf_all_zero = not check_qpf_rain(row_copy, 20)
        station = report_copy.get("station_realtime", {})
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
        base_booking["next_rain_time"] = compute_next_rain_time(
            now, report_copy, station
        )
        if "window_hourly_rain_count" in base_booking:
            del base_booking["window_hourly_rain_count"]

        # 4. Re-run diagnosis
        if args.no_llm:
            booking = base_booking
            playability = report_copy.get("playability", {}).get("30min", {})
            if playability.get("score", -1) == 0 and "cancel" not in booking.get(
                "decision", ""
            ):
                booking["decision"] = "suggest_cancel"
                booking["decision_cn"] = "建议取消或改期"
                booking["reason"].insert(0, "综合可打率评估为不可打，触发安全否决机制")
            report_copy["booking"] = booking
        else:
            context = extract_context(report_copy)
            context["booking_meta"] = base_booking

            diagnosis = run_llm_diagnosis(
                context,
                args.api_key,
                Path(args.diagnosis_output),
                timeout=args.llm_timeout,
            )

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

            # Post-validation: playability veto
            playability = report_copy.get("playability", {}).get("30min", {})
            if playability.get("score", -1) == 0 and "cancel" not in booking.get(
                "decision", ""
            ):
                booking["decision"] = "suggest_cancel"
                booking["decision_cn"] = "建议取消或改期"
                booking["reason"].insert(
                    0, "AI决策已被安全否决机制覆盖，强降雨预警"
                )
            report_copy["booking"] = booking

        # 5. Save updated report to disk
        save_calibration_log(report_copy, risk_scores)
        output = Path(args.output)
        report_copy.pop("llm_generating", None)

        if not args.once:
            next_at = datetime.now().astimezone() + timedelta(seconds=args.interval)
            report_copy["next_update_at"] = next_at.isoformat(timespec="seconds")

        output.write_text(
            json.dumps(report_copy, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[{ts}] [Async Visual QA] Wrote updated forecast to {output}", flush=True)
        _print_booking_summary(booking)

    except Exception as e:
        print(
            f"Error in background visual QA thread: {e}",
            file=sys.stderr,
            flush=True,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Main Pipeline
# ═══════════════════════════════════════════════════════════════════════════════


@langfuse_observe(name="weather_forecast_cycle")
def run_once(args: argparse.Namespace) -> None:
    """Single execution cycle: fetch → analyze → decide → diagnose."""
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M:%S")

    # ---- Step 0: Dynamic location loading ----
    # 优先读取前端提交的运行时位置，不存在时降级使用 config.py 默认值。
    # 直接更新 config.COURT 字典，使所有下游模块（nowcast 等）自动生效。
    runtime_loc = Path("output/runtime_location.json")
    if runtime_loc.is_file():
        try:
            loc = json.loads(runtime_loc.read_text(encoding="utf-8"))
            if loc.get("lon") and loc.get("lat"):
                COURT["id"] = loc.get("id", "user_selected")
                COURT["name"] = loc.get("name", "用户选择位置")
                COURT["lon"] = float(loc["lon"])
                COURT["lat"] = float(loc["lat"])
                print(
                    f"[{ts}] Using runtime location: "
                    f"{COURT['name']} ({COURT['lon']}, {COURT['lat']})",
                    flush=True,
                )
        except (json.JSONDecodeError, OSError, ValueError) as e:
            print(f"[{ts}] Warning: failed to load runtime location: {e}",
                  file=sys.stderr)

    # ---- Step 1: Fetch data ----
    grid_data = None
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

    # ---- Fast Feedback: Write intermediate report ----
    # Save the preliminary report before time-consuming LLM steps
    if is_location_changed_or_first_run(args, COURT):
        report["llm_generating"] = True
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        # Write intermediate diagnosis to show skeletons on frontend
        diag_output = Path(args.diagnosis_output)
        diag_output.parent.mkdir(parents=True, exist_ok=True)
        diag_output.write_text(
            json.dumps({"llm_generating": True}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # ---- Step 3: Radar visual QA ----
    risk_scores = report.get("risk_scores", {})
    station = report.get("station_realtime", {})
    qpf_all_zero = not check_qpf_rain(row, 20)

    run_visual_qa, visual_qa_reason = should_run_radar_visual_qa(report, row)
    report["radar_visual_qa_trigger"] = {
        "mode": args.radar_vision,
        "run": False,
        "reason": visual_qa_reason,
    }

    radar_visual_qa_trigger_run = False

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
            f"[{ts}] Deferring radar visual QA to background thread ({args.radar_vision}, {visual_qa_reason})...",
            flush=True,
        )
        # Main thread uses a quick skipped state to proceed immediately
        radar_visual_qa = _radar_visual_qa_skip("雷达质检已在后台异步运行。")
        radar_visual_qa_trigger_run = True

    risk_scores = apply_radar_visual_qa_to_report(report, row, radar_visual_qa)

    # ---- Step 4: Booking decision ----
    lead_hours, target_str = compute_lead_time(args.target_time, now)

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
    base_booking["next_rain_time"] = compute_next_rain_time(now, report, station)
    if "window_hourly_rain_count" in base_booking:
        del base_booking["window_hourly_rain_count"]

    # ---- Step 5: LLM diagnosis + safety veto ----
    if args.no_llm:
        booking = base_booking

        # Post-validation: playability veto
        playability = report.get("playability", {}).get("30min", {})
        if playability.get("score", -1) == 0 and "cancel" not in booking.get(
            "decision", ""
        ):
            booking["decision"] = "suggest_cancel"
            booking["decision_cn"] = "建议取消或改期"
            booking["reason"].insert(0, "综合可打率评估为不可打，触发安全否决机制")

        report["booking"] = booking
    else:
        context = extract_context(report)
        context["booking_meta"] = base_booking

        diagnosis = run_llm_diagnosis(
            context,
            args.api_key,
            Path(args.diagnosis_output),
            timeout=args.llm_timeout,
        )

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

        # Post-validation: playability veto
        playability = report.get("playability", {}).get("30min", {})
        if playability.get("score", -1) == 0 and "cancel" not in booking.get(
            "decision", ""
        ):
            booking["decision"] = "suggest_cancel"
            booking["decision_cn"] = "建议取消或改期"
            booking["reason"].insert(0, "AI决策已被安全否决机制覆盖，强降雨预警")

        report["booking"] = booking

    # ---- Step 6: Save output + console summary ----
    save_calibration_log(report, risk_scores)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report.pop("llm_generating", None)

    # Stamp next_update_at after ALL processing (incl. LLM)
    if not args.once:
        next_at = datetime.now().astimezone() + timedelta(seconds=args.interval)
        report["next_update_at"] = next_at.isoformat(timespec="seconds")

    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[{ts}] Wrote {output}", flush=True)

    _print_booking_summary(booking)

    # Start the async visual QA thread if triggered
    if radar_visual_qa_trigger_run:
        report_copy = copy.deepcopy(report)
        report_copy["radar_visual_qa_trigger"]["run"] = True
        row_copy = copy.deepcopy(row)
        t = threading.Thread(
            target=run_background_visual_qa_flow,
            args=(report_copy, row_copy, args, now),
            daemon=True
        )
        t.start()
        if args.once:
            print(f"[{ts}] [Async Visual QA] Waiting for background thread to complete (--once is active)...", flush=True)
            t.join()
