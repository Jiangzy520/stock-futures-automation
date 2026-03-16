#!/usr/bin/env python3
"""Generate an annotated intraday tick chart for a strategy signal."""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.alltick_variant_double_bottom_core import (  # noqa: E402
    CHINA_TZ,
    StrategySignal,
    TickStore,
    aggregate_ticks,
    china_day_bounds,
    detect_variant_double_bottom,
    load_json_config,
    normalize_symbol,
)


DEFAULT_SIGNAL_FILE = PROJECT_ROOT / ".guanlan" / "alltick" / "multi_token_variant_double_bottom_signals.csv"
DEFAULT_DB_PATH = PROJECT_ROOT / ".guanlan" / "alltick" / "multi_token_ticks.sqlite3"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "tools" / "alltick_multi_token_seconds.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / ".guanlan" / "alltick" / "signal_charts"

MORNING_OPEN = dt_time(9, 30)
MORNING_CLOSE = dt_time(11, 30)
AFTERNOON_OPEN = dt_time(13, 0)
AFTERNOON_CLOSE = dt_time(15, 0)
MORNING_SECONDS = 2 * 60 * 60
AFTERNOON_SECONDS = 2 * 60 * 60
TRADING_SECONDS = MORNING_SECONDS + AFTERNOON_SECONDS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成带 R1/R2/L1/L2/买点 标注的 TICK 图")
    parser.add_argument("--symbol", default="", help="股票代码，例 002181.SZ；默认取最新信号")
    parser.add_argument("--day", default="", help="交易日 YYYY-MM-DD；默认取对应信号日期")
    parser.add_argument("--signal-file", default=str(DEFAULT_SIGNAL_FILE))
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--config-file", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser.parse_args()


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates: list[Path] = []
    if bold:
        candidates.extend(
            [
                Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
                Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
                Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            ]
        )
    else:
        candidates.extend(
            [
                Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
                Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
                Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            ]
        )

    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def _parse_timestamp(text: str) -> datetime:
    raw = _safe_text(text)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=CHINA_TZ)
        except ValueError:
            continue
    raise ValueError(f"Unsupported timestamp: {text}")


def _parse_hms(day: date, text: str) -> datetime:
    raw = _safe_text(text)
    for fmt in ("%H:%M:%S.%f", "%H:%M:%S"):
        try:
            return datetime.combine(day, datetime.strptime(raw, fmt).time(), tzinfo=CHINA_TZ)
        except ValueError:
            continue
    raise ValueError(f"Unsupported HMS: {text}")


def _signal_from_row(row: dict[str, str]) -> StrategySignal:
    trading_day = date.fromisoformat(_safe_text(row.get("trading_day")))
    return StrategySignal(
        symbol=normalize_symbol(_safe_text(row.get("symbol"))),
        name=_safe_text(row.get("name")),
        trading_day=trading_day,
        confirm_mode=_safe_text(row.get("confirm_mode")) or "tick",
        signal_time=_parse_timestamp(_safe_text(row.get("signal_time"))),
        signal_price=float(_safe_text(row.get("signal_price")) or 0),
        open_price=float(_safe_text(row.get("open_price")) or 0),
        r1_time=_parse_hms(trading_day, _safe_text(row.get("r1_time"))),
        r1_price=float(_safe_text(row.get("r1_price")) or 0),
        r2_time=_parse_hms(trading_day, _safe_text(row.get("r2_time"))),
        r2_price=float(_safe_text(row.get("r2_price")) or 0),
        l1_time=_parse_hms(trading_day, _safe_text(row.get("l1_time"))),
        l1_price=float(_safe_text(row.get("l1_price")) or 0),
        l2_time=_parse_hms(trading_day, _safe_text(row.get("l2_time"))),
        l2_price=float(_safe_text(row.get("l2_price")) or 0),
    )


def _load_signal_row(path: Path, symbol: str, trading_day: str) -> dict[str, str] | None:
    if not path.exists():
        return None
    target_symbol = normalize_symbol(symbol) if symbol else ""
    target_day = trading_day.strip()
    latest: dict[str, str] | None = None
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            row_symbol = _safe_text(row.get("symbol"))
            row_day = _safe_text(row.get("trading_day"))
            if target_symbol and normalize_symbol(row_symbol) != target_symbol:
                continue
            if target_day and row_day != target_day:
                continue
            latest = row
    return latest


def _trading_second(dt_value: datetime) -> int:
    local = dt_value.astimezone(CHINA_TZ)
    t = local.timetz().replace(tzinfo=None)
    if t <= MORNING_OPEN:
        return 0
    if MORNING_OPEN <= t <= MORNING_CLOSE:
        return int((datetime.combine(local.date(), t) - datetime.combine(local.date(), MORNING_OPEN)).total_seconds())
    if MORNING_CLOSE < t < AFTERNOON_OPEN:
        return MORNING_SECONDS
    if AFTERNOON_OPEN <= t <= AFTERNOON_CLOSE:
        return MORNING_SECONDS + int((datetime.combine(local.date(), t) - datetime.combine(local.date(), AFTERNOON_OPEN)).total_seconds())
    return TRADING_SECONDS


def _x_for_dt(dt_value: datetime, left: int, width: int) -> float:
    return left + (_trading_second(dt_value) / TRADING_SECONDS) * width


def _y_for_price(price: float, min_price: float, max_price: float, top: int, height: int) -> float:
    if max_price <= min_price:
        return top + height / 2
    ratio = (price - min_price) / (max_price - min_price)
    return top + height - ratio * height


def _draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    start: tuple[float, float],
    end: tuple[float, float],
    fill: str,
    width: int = 1,
    dash: int = 8,
    gap: int = 6,
) -> None:
    x1, y1 = start
    x2, y2 = end
    if x1 == x2:
        total = abs(y2 - y1)
        direction = 1 if y2 >= y1 else -1
        pos = 0.0
        while pos < total:
            y_start = y1 + pos * direction
            y_end = y1 + min(pos + dash, total) * direction
            draw.line([(x1, y_start), (x2, y_end)], fill=fill, width=width)
            pos += dash + gap
        return

    total = abs(x2 - x1)
    direction = 1 if x2 >= x1 else -1
    slope = (y2 - y1) / (x2 - x1) if x2 != x1 else 0
    pos = 0.0
    while pos < total:
        seg_start_x = x1 + pos * direction
        seg_end_x = x1 + min(pos + dash, total) * direction
        seg_start_y = y1 + (seg_start_x - x1) * slope
        seg_end_y = y1 + (seg_end_x - x1) * slope
        draw.line([(seg_start_x, seg_start_y), (seg_end_x, seg_end_y)], fill=fill, width=width)
        pos += dash + gap


def _label_box(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
    border: str,
) -> tuple[int, int, int, int]:
    x, y = xy
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0] + 16
    height = bbox[3] - bbox[1] + 10
    rect = (int(x), int(y), int(x + width), int(y + height))
    draw.rounded_rectangle(rect, radius=10, fill=fill, outline=border, width=2)
    draw.text((x + 8, y + 5), text, fill="#f5fbff", font=font)
    return rect


def render_chart(
    output_path: Path,
    signal: StrategySignal,
    ticks: list[Any],
    shape_bars: list[Any],
    volume_bars: list[Any],
    config: dict[str, Any],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    width = 1680
    height = 980
    margin_left = 110
    margin_right = 40
    margin_top = 36
    margin_bottom = 52
    header_h = 120
    gap = 22
    price_h = 560
    volume_h = 180
    chart_w = width - margin_left - margin_right
    price_top = margin_top + header_h
    volume_top = price_top + price_h + gap

    image = Image.new("RGB", (width, height), "#0f141c")
    draw = ImageDraw.Draw(image)

    title_font = _load_font(40, bold=True)
    header_font = _load_font(22, bold=True)
    text_font = _load_font(20, bold=False)
    label_font = _load_font(18, bold=True)
    small_font = _load_font(16, bold=False)

    draw.rounded_rectangle([22, 18, width - 22, height - 18], radius=20, fill="#111926", outline="#20314a", width=2)
    draw.text((margin_left, 34), f"{signal.symbol} {signal.name}", fill="#ffffff", font=title_font)
    draw.text(
        (margin_left, 82),
        f"交易日 {signal.trading_day.isoformat()}  |  5秒形态K  |  确认模式 {signal.confirm_mode}  |  开盘价 {signal.open_price:.3f}",
        fill="#88a4c6",
        font=text_font,
    )

    summary = [
        f"右1 {signal.r1_time.strftime('%H:%M:%S')} @ {signal.r1_price:.3f}",
        f"右2 {signal.r2_time.strftime('%H:%M:%S')} @ {signal.r2_price:.3f}",
        f"左1 {signal.l1_time.strftime('%H:%M:%S')} @ {signal.l1_price:.3f}",
        f"左2 {signal.l2_time.strftime('%H:%M:%S')} @ {signal.l2_price:.3f}",
        f"确认买点 {signal.signal_time.strftime('%H:%M:%S')} @ {signal.signal_price:.3f}",
    ]
    sx = margin_left
    sy = price_top - 48
    for item in summary:
        box = _label_box(draw, (sx, sy), item, small_font, "#162333", "#2b4568")
        sx = box[2] + 12

    draw.rounded_rectangle(
        [margin_left, price_top, margin_left + chart_w, price_top + price_h],
        radius=18,
        fill="#101722",
        outline="#1d2c42",
        width=2,
    )
    draw.rounded_rectangle(
        [margin_left, volume_top, margin_left + chart_w, volume_top + volume_h],
        radius=18,
        fill="#101722",
        outline="#1d2c42",
        width=2,
    )

    if not ticks:
        draw.text((margin_left + 20, price_top + 20), "无 tick 数据", fill="#ff8585", font=header_font)
        image.save(output_path)
        return

    min_price = min(tick.price for tick in ticks)
    max_price = max(tick.price for tick in ticks)
    pad = max((max_price - min_price) * 0.08, signal.open_price * 0.003)
    min_price -= pad
    max_price += pad

    for idx in range(7):
        y = price_top + idx * price_h / 6
        price = max_price - (max_price - min_price) * idx / 6
        draw.line([(margin_left, y), (margin_left + chart_w, y)], fill="#1d2a3d", width=1)
        draw.text((22, y - 10), f"{price:.2f}", fill="#89a7ca", font=text_font)

    timeline = [
        ("09:30", datetime.combine(signal.trading_day, MORNING_OPEN, tzinfo=CHINA_TZ)),
        ("10:30", datetime.combine(signal.trading_day, dt_time(10, 30), tzinfo=CHINA_TZ)),
        ("11:30", datetime.combine(signal.trading_day, MORNING_CLOSE, tzinfo=CHINA_TZ)),
        ("13:00", datetime.combine(signal.trading_day, AFTERNOON_OPEN, tzinfo=CHINA_TZ)),
        ("14:00", datetime.combine(signal.trading_day, dt_time(14, 0), tzinfo=CHINA_TZ)),
        ("15:00", datetime.combine(signal.trading_day, AFTERNOON_CLOSE, tzinfo=CHINA_TZ)),
    ]
    for label, dt_value in timeline:
        x = _x_for_dt(dt_value, margin_left, chart_w)
        draw.line([(x, price_top), (x, volume_top + volume_h)], fill="#1a2737", width=1)
        draw.text((x - 24, volume_top + volume_h + 10), label, fill="#89a7ca", font=text_font)

    open_y = _y_for_price(signal.open_price, min_price, max_price, price_top, price_h)
    draw.line([(margin_left, open_y), (margin_left + chart_w, open_y)], fill="#6d8fb4", width=1)
    draw.text((margin_left + 8, open_y - 24), f"开盘 {signal.open_price:.3f}", fill="#6d8fb4", font=small_font)

    threshold_price = signal.open_price * (1 + float(config.get("r1_open_gain_pct") or 2.5) / 100)
    threshold_y = _y_for_price(threshold_price, min_price, max_price, price_top, price_h)
    _draw_dashed_line(draw, (margin_left, threshold_y), (margin_left + chart_w, threshold_y), fill="#e8b454", width=2)
    draw.text((margin_left + 8, threshold_y - 24), f"开盘+2.5% {threshold_price:.3f}", fill="#e8b454", font=small_font)

    breakout_y = _y_for_price(signal.l2_price, min_price, max_price, price_top, price_h)
    breakout_x1 = _x_for_dt(signal.l2_time, margin_left, chart_w)
    breakout_x2 = _x_for_dt(signal.signal_time, margin_left, chart_w)
    _draw_dashed_line(draw, (breakout_x1, breakout_y), (breakout_x2, breakout_y), fill="#4fd1c5", width=2)
    draw.text((breakout_x1 + 8, breakout_y + 8), f"L2突破线 {signal.l2_price:.3f}", fill="#4fd1c5", font=small_font)

    line_points = [
        (
            _x_for_dt(tick.dt, margin_left, chart_w),
            _y_for_price(tick.price, min_price, max_price, price_top, price_h),
        )
        for tick in ticks
    ]
    if len(line_points) >= 2:
        draw.line(line_points, fill="#f5f9ff", width=2)

    buy_x = _x_for_dt(signal.signal_time, margin_left, chart_w)
    _draw_dashed_line(draw, (buy_x, price_top), (buy_x, volume_top + volume_h), fill="#7cffc5", width=2)

    markers = [
        ("右1", signal.r1_time, signal.r1_price, "#ffae57", (18, -72)),
        ("右2", signal.r2_time, signal.r2_price, "#ff6b6b", (18, 18)),
        ("左1", signal.l1_time, signal.l1_price, "#4fd1ff", (18, -72)),
        ("左2", signal.l2_time, signal.l2_price, "#a78bfa", (18, 18)),
        ("买点", signal.signal_time, signal.signal_price, "#6bff95", (18, -92)),
    ]
    for label, point_dt, price, color, offset in markers:
        x = _x_for_dt(point_dt, margin_left, chart_w)
        y = _y_for_price(price, min_price, max_price, price_top, price_h)
        draw.ellipse([x - 6, y - 6, x + 6, y + 6], fill=color, outline="#ffffff", width=2)
        target_x = x + offset[0]
        target_y = y + offset[1]
        draw.line([(x, y), (target_x, target_y + 14)], fill=color, width=2)
        text = f"{label} {point_dt.strftime('%H:%M:%S')} @ {price:.3f}"
        _label_box(draw, (target_x, target_y), text, label_font, "#172335", color)

    volume_values = [bar.volume for bar in volume_bars] or [0.0]
    max_volume = max(volume_values) or 1.0
    for bar in volume_bars:
        x = _x_for_dt(bar.start_dt, margin_left, chart_w)
        y = volume_top + volume_h - (bar.volume / max_volume) * (volume_h - 24)
        y = max(volume_top + 18, min(y, volume_top + volume_h - 2))
        color = "#45caff" if bar.close_price >= bar.open_price else "#ff7c8b"
        draw.rectangle([x - 2, y, x + 2, volume_top + volume_h - 2], fill=color)

    draw.text((margin_left + 16, price_top + 16), "Tick 分时线", fill="#dbe8f8", font=header_font)
    draw.text((margin_left + 16, volume_top + 14), "1分钟成交量", fill="#dbe8f8", font=header_font)
    draw.text(
        (margin_left, height - margin_bottom + 8),
        "说明：白线=逐笔价格，黄虚线=开盘+2.5%，青虚线=L2突破线，绿虚线=确认买点时间。",
        fill="#7d96b8",
        font=text_font,
    )

    image.save(output_path)


def main() -> int:
    args = parse_args()
    signal_file = Path(args.signal_file).expanduser().resolve()
    db_path = Path(args.db_path).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    config = load_json_config(Path(args.config_file).expanduser().resolve())

    row = _load_signal_row(signal_file, args.symbol, args.day)
    signal = _signal_from_row(row) if row else None

    symbol = normalize_symbol(args.symbol) if args.symbol else (signal.symbol if signal else "")
    if not symbol:
        raise SystemExit("未找到信号，请提供 --symbol 或先生成信号 CSV")

    trading_day = date.fromisoformat(args.day) if args.day else (signal.trading_day if signal else None)
    if trading_day is None:
        raise SystemExit("未指定交易日")

    store = TickStore(db_path)
    try:
        start_ms, end_ms = china_day_bounds(trading_day)
        ticks = store.load_ticks(symbol, start_ms=start_ms, end_ms=end_ms)
    finally:
        store.close()

    if not ticks:
        raise SystemExit(f"未找到 {symbol} {trading_day.isoformat()} 的 tick 数据")

    if signal is None:
        open_price = ticks[0].price
        signal = detect_variant_double_bottom(ticks, config, open_price)
        if signal is None:
            raise SystemExit(f"{symbol} {trading_day.isoformat()} 未识别到策略信号")

    shape_seconds = int(config.get("shape_bar_seconds") or 5)
    shape_bars = aggregate_ticks(ticks, shape_seconds)
    volume_bars = aggregate_ticks(ticks, 60)

    filename = f"{signal.symbol.replace('.', '_')}_{signal.trading_day.isoformat()}_signal_chart.png"
    output_path = output_dir / filename
    render_chart(output_path, signal, ticks, shape_bars, volume_bars, config)

    print(f"[chart] {output_path}")
    print(
        "[signal]"
        f" {signal.symbol} {signal.name}"
        f" R1={signal.r1_time.strftime('%H:%M:%S')}@{signal.r1_price:.3f}"
        f" R2={signal.r2_time.strftime('%H:%M:%S')}@{signal.r2_price:.3f}"
        f" L1={signal.l1_time.strftime('%H:%M:%S')}@{signal.l1_price:.3f}"
        f" L2={signal.l2_time.strftime('%H:%M:%S')}@{signal.l2_price:.3f}"
        f" BUY={signal.signal_time.strftime('%H:%M:%S')}@{signal.signal_price:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
