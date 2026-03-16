# -*- coding: utf-8 -*-
"""
股票分时买点监控脚本

目标：
1. 不再看图片，直接使用实时分时数据识别买点。
2. 将“右1/右2/左1/左2”两套分时结构翻译成可执行规则。
3. 作为观澜“脚本策略”运行，输出实时信号提醒。
4. 支持软件内股票纸面交易，自动记录模拟持仓、成交和盈亏。

说明：
1. 当前版本实现两套固定分时结构，只做信号提醒和纸面交易，不自动下单。
2. 该版本更像把你的看图逻辑做成半自动扫描器，不保证和人工判图完全一致。
3. 数据源使用新浪免费股票分时接口，适合盘中监控，稳定性以公开接口为准。

使用方式：
1. 编辑同目录 `stock_intraday_pattern_watch.json`
2. 在软件“脚本策略”页面中添加本脚本
3. 启动脚本，查看脚本日志中的提醒
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, date, time as dtime, timedelta
from pathlib import Path
from time import sleep
from typing import Any

import requests


CONFIG_PATH = Path(__file__).with_suffix(".json")

HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

SINA_FINANCE_URL = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
EASTMONEY_STOCK_GET_URL = "https://push2.eastmoney.com/api/qt/stock/get"
EASTMONEY_YJYG_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
PRELOSS_TYPES = {"首亏", "续亏", "增亏"}

DEFAULT_CONFIG = {
    "symbols": [
        "600519",
        "000001",
    ],
    "poll_interval_seconds": 5,
    "history_sync_interval_seconds": 60,
    "right1_search_minutes": 60,
    "enable_strategy_one": True,
    "enable_strategy_two": True,
    "strategy1_right1_min_pct": 2.5,
    "strategy2_right1_min_pct": 5.0,
    "min_pullback_pct": 0.3,
    "breakout_buffer_pct": 0.10,
    "strategy2_breakout_buffer_pct": None,
    "enable_preloss_filter": True,
    "enable_macd_bearish_filter": True,
    "enable_negative_earnings_filter": True,
    "macd_short_period": 12,
    "macd_long_period": 26,
    "macd_signal_period": 9,
    "signal_cooldown_minutes": 15,
    "enable_sound": True,
    "enable_dingtalk": False,
    "paper_trading_enabled": True,
    "paper_initial_cash": 100000,
    "paper_trade_amount": 20000,
    "paper_lot_size": 100,
    "paper_max_positions": 3,
    "paper_max_entries_per_symbol": 2,
    "paper_require_strong_signal": True,
    "paper_force_exit_time": "14:55",
    "debug": True,
}


@dataclass
class MinuteBar:
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    avg_price: float = 0.0


@dataclass
class Quote:
    code: str
    name: str
    dt: datetime
    open: float
    pre_close: float
    price: float
    high: float
    low: float
    total_volume: float
    total_amount: float


@dataclass
class Signal:
    code: str
    name: str
    signal_time: datetime
    pattern_type: str
    buy_type: str
    strength: str
    entry_price: float
    stop_loss: float
    invalidation: float
    left1_pct: float
    left1_time: str
    right1_volume_ok: bool
    trigger_level: float
    session_vwap: float
    reason: str
    buy_time: str = ""
    right1_time: str = ""
    right1_price: float = 0.0
    right2_time: str = ""
    right2_price: float = 0.0
    left1_point_time: str = ""
    left1_price: float = 0.0
    left2_time: str = ""
    left2_price: float = 0.0
    details: list[str] = field(default_factory=list)


@dataclass
class SymbolState:
    code: str
    name: str = ""
    bars: list[MinuteBar] = field(default_factory=list)
    session_date: date | None = None
    pre_close: float = 0.0
    last_total_volume: float = 0.0
    last_total_amount: float = 0.0
    last_history_sync: datetime | None = None
    last_signal_times: dict[str, datetime] = field(default_factory=dict)


@dataclass
class FundamentalFilterResult:
    code: str
    name: str
    checked_day: date
    eps_basic: float | None
    eps_report_date: str
    pe_ttm: float | None
    forecast_notice_date: str
    forecast_types: str
    reasons: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.reasons)


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return DEFAULT_CONFIG.copy()

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_CONFIG.copy()

    merged = DEFAULT_CONFIG.copy()
    merged.update(data)
    merged["symbols"] = [str(s).strip() for s in merged.get("symbols", []) if str(s).strip()]
    return merged


def to_sina_symbol(code: str) -> str:
    if code.startswith(("6", "9", "5")):
        return f"sh{code}"
    if code.startswith(("0", "2", "3")):
        return f"sz{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return code


def pct(value: float, base: float) -> float:
    if base <= 0:
        return 0.0
    return (value / base - 1) * 100


def floor_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def parse_hhmm(value: str, fallback: dtime) -> dtime:
    try:
        hour_text, minute_text = str(value).split(":", 1)
        return dtime(int(hour_text), int(minute_text))
    except Exception:
        return fallback


def is_stock_trading_time(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False

    current = now.time()
    return (
        dtime(9, 30) <= current <= dtime(11, 30)
        or dtime(13, 0) <= current <= dtime(15, 0)
    )


def minutes_from_open(dt: datetime) -> int:
    morning_open = dt.replace(hour=9, minute=30, second=0, microsecond=0)
    return int((dt - morning_open).total_seconds() // 60)


def local_high_indices(bars: list[MinuteBar], radius: int = 1) -> list[int]:
    result: list[int] = []
    if len(bars) < radius * 2 + 1:
        return result

    for i in range(radius, len(bars) - radius):
        high = bars[i].high
        left = bars[i - radius:i]
        right = bars[i + 1:i + radius + 1]
        if all(high >= bar.high for bar in left + right):
            result.append(i)
    return result


def highest_index_with_threshold(
    bars: list[MinuteBar],
    pre_close: float,
    min_pct: float,
) -> int | None:
    """查找达到涨幅阈值的最高点索引。"""
    candidates = [
        index for index, bar in enumerate(bars)
        if pct(bar.high, pre_close) >= min_pct
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda index: (bars[index].high, -index))


def find_first_secondary_high(
    bars: list[MinuteBar],
    anchor_idx: int,
    min_pullback_pct: float,
    radius: int = 1,
) -> int | None:
    """查找某个高点下跌后的第一个次高点。"""
    if anchor_idx >= len(bars) - 3:
        return None

    highs = set(local_high_indices(bars, radius=radius))
    anchor_high = bars[anchor_idx].high
    pullback_level = anchor_high * (1 - min_pullback_pct / 100)

    for idx in range(anchor_idx + 2, len(bars) - 1):
        if idx not in highs:
            continue
        if bars[idx].high >= anchor_high:
            continue

        pullback_window = bars[anchor_idx + 1:idx]
        if not pullback_window:
            continue
        if min(bar.low for bar in pullback_window) > pullback_level:
            continue

        return idx

    return None


def find_breakout_after_pullback(
    bars: list[MinuteBar],
    pivot_idx: int,
    min_pullback_pct: float,
    breakout_buffer_pct: float,
) -> int | None:
    """查找某个次高点下跌后首次向上突破的位置。"""
    if pivot_idx >= len(bars) - 2:
        return None

    pivot_high = bars[pivot_idx].high
    pullback_level = pivot_high * (1 - min_pullback_pct / 100)
    breakout_level = pivot_high * (1 + breakout_buffer_pct / 100)

    for idx in range(pivot_idx + 2, len(bars)):
        pullback_window = bars[pivot_idx + 1:idx]
        if not pullback_window:
            continue
        if min(bar.low for bar in pullback_window) > pullback_level:
            continue
        if max(bar.high for bar in pullback_window) > breakout_level:
            continue

        if bars[idx].high > breakout_level:
            return idx

    return None


def calc_stop_from_range(
    bars: list[MinuteBar],
    start_idx: int,
    end_idx: int,
) -> float:
    """用突破前后的最低点作为止损参考。"""
    segment = [bar.low for bar in bars[start_idx:end_idx + 1] if bar.low > 0]
    if not segment:
        return 0.0
    return min(segment)


def session_vwap(bars: list[MinuteBar]) -> float:
    total_amount = sum(bar.amount for bar in bars)
    total_volume = sum(bar.volume for bar in bars)
    if total_volume <= 0:
        return 0.0
    return total_amount / total_volume


def infer_suffix_from_code(code: str) -> str:
    if code.startswith(("6", "5", "9")):
        return "SH"
    if code.startswith(("0", "2", "3")):
        return "SZ"
    if code.startswith(("4", "8")):
        return "BJ"
    return ""


def eastmoney_secid_from_code(code: str) -> str:
    if code.startswith(("6", "5", "9")):
        return f"1.{code}"
    return f"0.{code}"


def sina_paper_code_from_code(code: str) -> str:
    suffix = infer_suffix_from_code(code).lower()
    if not suffix:
        return code
    return f"{suffix}{code}"


def recent_report_periods(reference_day: date, count: int = 8) -> list[str]:
    periods: list[str] = []
    year = reference_day.year
    while len(periods) < count:
        for suffix in ("1231", "0930", "0630", "0331"):
            candidate = date(year, int(suffix[:2]), int(suffix[2:]))
            if candidate <= reference_day:
                periods.append(f"{year}{suffix}")
                if len(periods) >= count:
                    break
        year -= 1
    return periods


def next_trading_day_simple(value: date) -> date:
    day = value + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    if period <= 1:
        return values[:]

    alpha = 2 / (period + 1)
    ema_values = [values[0]]
    for value in values[1:]:
        ema_values.append(alpha * value + (1 - alpha) * ema_values[-1])
    return ema_values


def calc_macd_latest(
    closes: list[float],
    short_period: int = 12,
    long_period: int = 26,
    signal_period: int = 9,
) -> tuple[float | None, float | None]:
    values = [float(x) for x in closes if float(x) > 0]
    if len(values) < 2:
        return None, None

    short_ema = ema_series(values, max(int(short_period), 1))
    long_ema = ema_series(values, max(int(long_period), 1))
    dif_list = [s - l for s, l in zip(short_ema, long_ema)]
    dea_list = ema_series(dif_list, max(int(signal_period), 1))
    if not dif_list or not dea_list:
        return None, None
    return dif_list[-1], dea_list[-1]


def fetch_dynamic_pe_ttm(session: requests.Session, code: str) -> float | None:
    response = session.get(
        EASTMONEY_STOCK_GET_URL,
        params={
            "fltt": "2",
            "invt": "2",
            "fields": "f57,f58,f162,f9,f115",
            "secid": eastmoney_secid_from_code(code),
        },
        timeout=20,
    )
    response.raise_for_status()
    data = (response.json().get("data") or {})
    for field in ("f162", "f9", "f115"):
        value = data.get(field)
        if value in (None, "", "-"):
            continue
        return float(value)
    return None


def fetch_latest_basic_eps(session: requests.Session, code: str) -> tuple[str, float | None]:
    response = session.get(
        SINA_FINANCE_URL,
        params={
            "paperCode": sina_paper_code_from_code(code),
            "source": "gjzb",
            "type": "0",
            "page": "1",
            "num": "20",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    report_map = (((payload.get("result") or {}).get("data") or {}).get("report_list") or {})
    if not report_map:
        return "", None

    latest_report = max(report_map.keys())
    rows = report_map.get(latest_report, {}).get("data", [])
    for row in rows:
        if row.get("item_field") == "EPSBASIC" or row.get("item_title") == "基本每股收益":
            value = row.get("item_value")
            if value in (None, "", "-"):
                return latest_report, None
            return latest_report, float(value)
    return latest_report, None


def fetch_latest_forecast_map(
    session: requests.Session,
    codes: list[str],
    reference_day: date,
) -> dict[str, tuple[date, set[str]]]:
    code_set = {str(code).zfill(6) for code in codes}
    result: dict[str, tuple[date, set[str]]] = {}

    for period in recent_report_periods(reference_day):
        params = {
            "sortColumns": "NOTICE_DATE,SECURITY_CODE",
            "sortTypes": "-1,-1",
            "pageSize": "500",
            "pageNumber": "1",
            "reportName": "RPT_PUBLIC_OP_NEWPREDICT",
            "columns": "ALL",
            "filter": f"(REPORT_DATE='{period[:4]}-{period[4:6]}-{period[6:]}')",
        }

        first_response = session.get(EASTMONEY_YJYG_URL, params=params, timeout=20)
        first_response.raise_for_status()
        first_payload = first_response.json()
        first_result = first_payload.get("result") or {}
        total_pages = int(first_result.get("pages") or 0)
        rows = list(first_result.get("data") or [])

        for page in range(2, total_pages + 1):
            params["pageNumber"] = str(page)
            page_response = session.get(EASTMONEY_YJYG_URL, params=params, timeout=20)
            page_response.raise_for_status()
            rows.extend(((page_response.json().get("result") or {}).get("data") or []))

        filtered_rows = [
            row for row in rows
            if str(row.get("SECURITY_CODE", "")).zfill(6) in code_set
        ]
        if not filtered_rows:
            continue

        by_code: dict[str, list[dict[str, Any]]] = {}
        for row in filtered_rows:
            code = str(row.get("SECURITY_CODE", "")).zfill(6)
            by_code.setdefault(code, []).append(row)

        for code, code_rows in by_code.items():
            if code in result:
                continue
            latest_notice = max(
                datetime.fromisoformat(str(row["NOTICE_DATE"]).replace(" ", "T")).date()
                for row in code_rows
            )
            latest_rows = [
                row for row in code_rows
                if datetime.fromisoformat(str(row["NOTICE_DATE"]).replace(" ", "T")).date() == latest_notice
            ]
            result[code] = (
                latest_notice,
                {str(row.get("PREDICT_TYPE", "")).strip() for row in latest_rows if str(row.get("PREDICT_TYPE", "")).strip()},
            )
        if len(result) >= len(code_set):
            break

    return result


def fetch_quotes(session: requests.Session, codes: list[str]) -> dict[str, Quote]:
    if not codes:
        return {}

    sina_symbols = [to_sina_symbol(code) for code in codes]
    url = f"https://hq.sinajs.cn/list={','.join(sina_symbols)}"
    resp = session.get(url, headers=HEADERS, timeout=10)
    resp.encoding = "gbk"

    quotes: dict[str, Quote] = {}

    for line in resp.text.strip().split(";"):
        if '="' not in line:
            continue

        left, _, right = line.partition('="')
        payload = right.rstrip('"').strip()
        if not payload:
            continue

        symbol = left.rsplit("_", 1)[-1]
        code = symbol[2:]
        fields = payload.split(",")
        if len(fields) < 32:
            continue

        try:
            dt = datetime.strptime(
                f"{fields[30]} {fields[31]}",
                "%Y-%m-%d %H:%M:%S",
            )
            quote = Quote(
                code=code,
                name=fields[0].strip() or code,
                dt=dt,
                open=float(fields[1] or 0),
                pre_close=float(fields[2] or 0),
                price=float(fields[3] or 0),
                high=float(fields[4] or 0),
                low=float(fields[5] or 0),
                total_volume=float(fields[8] or 0),
                total_amount=float(fields[9] or 0),
            )
            if quote.price > 0 and quote.pre_close > 0:
                quotes[code] = quote
        except Exception:
            continue

    return quotes


def fetch_intraday_bars(session: requests.Session, code: str, trading_day: date) -> list[MinuteBar]:
    symbol = to_sina_symbol(code)
    url = (
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/"
        "var%20_gl_watch=/CN_MarketData.getKLineData"
        f"?symbol={symbol}&scale=1&ma=no&datalen=240"
    )
    resp = session.get(url, headers=HEADERS, timeout=10)
    resp.encoding = "utf-8"

    match = re.search(r"=\((.*)\);?\s*$", resp.text.strip(), re.S)
    if not match:
        return []

    try:
        raw_rows = json.loads(match.group(1))
    except Exception:
        return []

    bars: list[MinuteBar] = []
    for row in raw_rows:
        try:
            dt = datetime.strptime(row["day"], "%Y-%m-%d %H:%M:%S")
            if dt.date() != trading_day:
                continue
            open_price = float(row.get("open", 0) or 0)
            close_price = float(row.get("close", 0) or 0)
            high_price = float(row.get("high", 0) or 0)
            low_price = float(row.get("low", 0) or 0)
            volume = float(row.get("volume", 0) or 0)
            amount = float(row.get("amount", 0) or 0)
            avg_price = float(row.get("avg_price", 0) or row.get("avgprice", 0) or 0)

            # 新浪部分分钟接口开盘价会返回 0，这里回退到收盘价。
            if open_price <= 0:
                open_price = close_price

            bars.append(
                MinuteBar(
                    dt=floor_minute(dt),
                    open=open_price,
                    high=high_price or close_price,
                    low=low_price or close_price,
                    close=close_price,
                    volume=volume,
                    amount=amount,
                    avg_price=avg_price,
                )
            )
        except Exception:
            continue

    bars.sort(key=lambda bar: bar.dt)
    return bars


def format_price(value: float) -> str:
    return f"{value:.3f}"


class PatternWatcher:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.config = load_config()
        self.session = requests.Session()
        self.states = {
            code: SymbolState(code=code)
            for code in self.config["symbols"]
        }
        self.fund_session = requests.Session()
        self.fund_session.trust_env = False
        self.fund_session.headers.update({"User-Agent": HEADERS["User-Agent"]})
        self._fund_filter_cache: dict[tuple[str, date], FundamentalFilterResult] = {}
        self._forecast_map: dict[str, tuple[date, set[str]]] = {}
        self._forecast_map_day: date | None = None
        self._filter_logged: set[tuple[str, date, str]] = set()

    def log(self, msg: str) -> None:
        self.engine.write_script_log(msg)

    def sync_history_if_needed(self, state: SymbolState, now: datetime) -> None:
        interval_seconds = int(self.config["history_sync_interval_seconds"])
        if state.last_history_sync and (now - state.last_history_sync).total_seconds() < interval_seconds:
            return

        bars = fetch_intraday_bars(self.session, state.code, now.date())
        state.last_history_sync = now

        if not bars:
            return

        # 用分钟历史初始化当天上下文；之后实时 quote 再更新最后一根 bar。
        state.bars = bars
        state.session_date = now.date()
        state.last_total_volume = sum(bar.volume for bar in bars)
        state.last_total_amount = sum(bar.amount for bar in bars)

    def update_from_quote(self, state: SymbolState, quote: Quote) -> None:
        bar_dt = floor_minute(quote.dt)

        if state.session_date != quote.dt.date():
            state.bars = []
            state.last_total_volume = 0
            state.last_total_amount = 0
            state.session_date = quote.dt.date()

        state.name = quote.name
        state.pre_close = quote.pre_close

        delta_volume = max(quote.total_volume - state.last_total_volume, 0.0)
        delta_amount = max(quote.total_amount - state.last_total_amount, 0.0)

        if state.bars and state.bars[-1].dt == bar_dt:
            bar = state.bars[-1]
            if bar.open <= 0:
                bar.open = quote.price
            bar.high = max(bar.high, quote.price)
            bar.low = min(bar.low, quote.price) if bar.low > 0 else quote.price
            bar.close = quote.price
            bar.volume += delta_volume
            bar.amount += delta_amount
        else:
            open_price = state.bars[-1].close if state.bars else quote.open or quote.price
            state.bars.append(
                MinuteBar(
                    dt=bar_dt,
                    open=open_price,
                    high=max(quote.price, open_price),
                    low=min(quote.price, open_price),
                    close=quote.price,
                    volume=delta_volume,
                    amount=delta_amount,
                )
            )

        state.last_total_volume = quote.total_volume
        state.last_total_amount = quote.total_amount

        # 限制缓存长度，只保留当天 bars。
        if len(state.bars) > 260:
            state.bars = state.bars[-260:]

    def can_alert(self, state: SymbolState, key: str, now: datetime) -> bool:
        last_time = state.last_signal_times.get(key)
        if not last_time:
            return True

        cooldown = timedelta(minutes=int(self.config["signal_cooldown_minutes"]))
        return now - last_time >= cooldown

    def mark_alert(self, state: SymbolState, key: str, now: datetime) -> None:
        state.last_signal_times[key] = now

    def strategy_one_enabled(self) -> bool:
        return bool(self.config.get("enable_strategy_one", True))

    def strategy_two_enabled(self) -> bool:
        return bool(self.config.get("enable_strategy_two", True))

    def strategy_two_breakout_buffer_pct(self) -> float:
        override = self.config.get("strategy2_breakout_buffer_pct")
        if override in ("", None):
            return float(self.config["breakout_buffer_pct"])
        return float(override)

    def preloss_filter_enabled(self) -> bool:
        return bool(self.config.get("enable_preloss_filter", False))

    def macd_bearish_filter_enabled(self) -> bool:
        return bool(self.config.get("enable_macd_bearish_filter", False))

    def negative_earnings_filter_enabled(self) -> bool:
        return bool(self.config.get("enable_negative_earnings_filter", False))

    def debug_enabled(self) -> bool:
        return bool(self.config.get("debug", False))

    def ensure_forecast_map(self, reference_day: date) -> None:
        if not self.preloss_filter_enabled():
            return
        if self._forecast_map_day == reference_day:
            return

        self._forecast_map_day = reference_day
        self._forecast_map = {}
        self._fund_filter_cache = {
            key: value for key, value in self._fund_filter_cache.items() if key[1] == reference_day
        }
        try:
            self._forecast_map = fetch_latest_forecast_map(
                session=self.fund_session,
                codes=list(self.states.keys()),
                reference_day=reference_day,
            )
        except Exception as exc:
            if self.debug_enabled():
                self.log(f"[过滤] 预亏公告拉取失败: {exc}")
            self._forecast_map = {}

    def get_fundamental_filter_result(self, state: SymbolState, trading_day: date) -> FundamentalFilterResult:
        cache_key = (state.code, trading_day)
        cached = self._fund_filter_cache.get(cache_key)
        if cached is not None:
            return cached

        reasons: list[str] = []
        eps_basic: float | None = None
        eps_report_date = ""
        pe_ttm: float | None = None
        forecast_notice_date = ""
        forecast_types = ""

        if self.negative_earnings_filter_enabled():
            try:
                eps_report_date, eps_basic = fetch_latest_basic_eps(self.fund_session, state.code)
            except Exception as exc:
                if self.debug_enabled():
                    self.log(f"[过滤] {state.code} EPS 拉取失败: {exc}")
                eps_report_date, eps_basic = "", None

            try:
                pe_ttm = fetch_dynamic_pe_ttm(self.fund_session, state.code)
            except Exception as exc:
                if self.debug_enabled():
                    self.log(f"[过滤] {state.code} PE(TTM) 拉取失败: {exc}")
                pe_ttm = None

            if eps_basic is not None and eps_basic < 0:
                reasons.append("eps_negative")
            if pe_ttm is not None and pe_ttm < 0:
                reasons.append("pe_ttm_negative")

        if self.preloss_filter_enabled():
            self.ensure_forecast_map(trading_day)
            forecast_info = self._forecast_map.get(state.code)
            if forecast_info:
                notice_day, types = forecast_info
                forecast_notice_date = notice_day.isoformat()
                forecast_types = ",".join(sorted(types))
                if types & PRELOSS_TYPES and trading_day in {notice_day, next_trading_day_simple(notice_day)}:
                    reasons.append("recent_preloss_forecast")

        result = FundamentalFilterResult(
            code=state.code,
            name=state.name or state.code,
            checked_day=trading_day,
            eps_basic=eps_basic,
            eps_report_date=eps_report_date,
            pe_ttm=pe_ttm,
            forecast_notice_date=forecast_notice_date,
            forecast_types=forecast_types,
            reasons=reasons,
        )
        self._fund_filter_cache[cache_key] = result
        return result

    def log_filter_once(self, state: SymbolState, trading_day: date, reason_key: str, message: str) -> None:
        key = (state.code, trading_day, reason_key)
        if key in self._filter_logged:
            return
        self._filter_logged.add(key)
        self.log(message)

    def apply_signal_filters(self, state: SymbolState, signal: Signal | None) -> Signal | None:
        if signal is None:
            return None

        if self.macd_bearish_filter_enabled():
            dif, dea = calc_macd_latest(
                [bar.close for bar in state.bars],
                int(self.config.get("macd_short_period", 12)),
                int(self.config.get("macd_long_period", 26)),
                int(self.config.get("macd_signal_period", 9)),
            )
            if dif is not None and dea is not None and dif < dea:
                self.log_filter_once(
                    state=state,
                    trading_day=signal.signal_time.date(),
                    reason_key="macd_dif_lt_dea",
                    message=(
                        f"[过滤] {signal.name} {signal.code} "
                        f"DIF<DEA ({dif:.4f}<{dea:.4f})，跳过 {signal.pattern_type}"
                    ),
                )
                return None

        if self.preloss_filter_enabled() or self.negative_earnings_filter_enabled():
            filter_result = self.get_fundamental_filter_result(state, signal.signal_time.date())
            if filter_result.blocked:
                self.log_filter_once(
                    state=state,
                    trading_day=signal.signal_time.date(),
                    reason_key="fundamental_block",
                    message=(
                        f"[过滤] {signal.name} {signal.code} 因基本面过滤跳过: "
                        f"{','.join(filter_result.reasons)} "
                        f"(EPS={filter_result.eps_basic if filter_result.eps_basic is not None else 'NA'}, "
                        f"PE={filter_result.pe_ttm if filter_result.pe_ttm is not None else 'NA'}, "
                        f"预亏类型={filter_result.forecast_types or 'NA'})"
                    ),
                )
                return None

        return signal

    def evaluate_signals(self, state: SymbolState) -> tuple[Signal | None, Signal | None]:
        signal1 = self.analyze_strategy_one(state) if self.strategy_one_enabled() else None
        signal2 = self.analyze_strategy_two(state) if self.strategy_two_enabled() else None
        signal1 = self.apply_signal_filters(state, signal1)
        signal2 = self.apply_signal_filters(state, signal2)
        return signal1, signal2

    def analyze_strategy_one(self, state: SymbolState) -> Signal | None:
        bars = state.bars
        if len(bars) < 20 or state.pre_close <= 0:
            return None

        completed = bars[:-1] if len(bars) > 1 else bars
        current = bars[-1]
        current_idx = len(bars) - 1
        if len(completed) < 12:
            return None

        scope = completed[:int(self.config["right1_search_minutes"])]
        if not scope:
            return None

        right1_idx = highest_index_with_threshold(
            scope,
            state.pre_close,
            float(self.config["strategy1_right1_min_pct"]),
        )
        if right1_idx is None:
            return None

        right1 = completed[right1_idx]
        right1_pct = pct(right1.high, state.pre_close)

        right2_idx = find_first_secondary_high(
            completed,
            right1_idx,
            float(self.config["min_pullback_pct"]),
        )
        if right2_idx is None:
            return None

        left1_idx = find_breakout_after_pullback(
            completed,
            right2_idx,
            float(self.config["min_pullback_pct"]),
            float(self.config["breakout_buffer_pct"]),
        )
        if left1_idx is None:
            return None

        left2_idx = find_first_secondary_high(
            completed,
            left1_idx,
            float(self.config["min_pullback_pct"]),
        )
        if left2_idx is None:
            return None

        buy_idx = find_breakout_after_pullback(
            bars,
            left2_idx,
            float(self.config["min_pullback_pct"]),
            float(self.config["breakout_buffer_pct"]),
        )
        if buy_idx != current_idx:
            return None

        right2 = completed[right2_idx]
        left1 = completed[left1_idx]
        left2 = completed[left2_idx]
        stop_loss = calc_stop_from_range(bars, left2_idx, current_idx)
        reason = (
            f"右1涨幅 {right1_pct:.2f}% 达到策略一阈值，"
            f"右1→右2→左1→左2 结构成立，当前突破左2 {format_price(left2.high)} 为买点。"
        )
        details = [
            f"右1时间={right1.dt.strftime('%H:%M')} 价格={format_price(right1.high)}",
            f"右2时间={right2.dt.strftime('%H:%M')} 价格={format_price(right2.high)}",
            f"左1时间={left1.dt.strftime('%H:%M')} 价格={format_price(left1.high)}",
            f"左2时间={left2.dt.strftime('%H:%M')} 价格={format_price(left2.high)}",
            f"现价={format_price(current.close)}",
            f"均价线={format_price(session_vwap(bars))}"
        ]
        return Signal(
            code=state.code,
            name=state.name or state.code,
            signal_time=current.dt,
            pattern_type="策略一",
            buy_type="突破左二",
            strength="强" if right1_pct >= float(self.config["strategy2_right1_min_pct"]) else "中",
            entry_price=current.close,
            stop_loss=stop_loss,
            invalidation=stop_loss,
            left1_pct=right1_pct,
            left1_time=right1.dt.strftime("%H:%M"),
            right1_volume_ok=True,
            trigger_level=left2.high,
            session_vwap=session_vwap(bars),
            reason=reason,
            buy_time=current.dt.strftime("%H:%M"),
            right1_time=right1.dt.strftime("%H:%M"),
            right1_price=right1.high,
            right2_time=right2.dt.strftime("%H:%M"),
            right2_price=right2.high,
            left1_point_time=left1.dt.strftime("%H:%M"),
            left1_price=left1.high,
            left2_time=left2.dt.strftime("%H:%M"),
            left2_price=left2.high,
            details=details,
        )

    def analyze_strategy_two(self, state: SymbolState) -> Signal | None:
        bars = state.bars
        if len(bars) < 20 or state.pre_close <= 0:
            return None

        completed = bars[:-1] if len(bars) > 1 else bars
        current = bars[-1]
        current_idx = len(bars) - 1
        if len(completed) < 12:
            return None

        scope = completed[:int(self.config["right1_search_minutes"])]
        if not scope:
            return None

        right1_idx = highest_index_with_threshold(
            scope,
            state.pre_close,
            float(self.config["strategy2_right1_min_pct"]),
        )
        if right1_idx is None:
            return None

        right1 = completed[right1_idx]
        right1_pct = pct(right1.high, state.pre_close)

        right2_idx = find_first_secondary_high(
            completed,
            right1_idx,
            float(self.config["min_pullback_pct"]),
        )
        if right2_idx is None:
            return None

        buy_idx = find_breakout_after_pullback(
            bars,
            right2_idx,
            float(self.config["min_pullback_pct"]),
            self.strategy_two_breakout_buffer_pct(),
        )
        if buy_idx != current_idx:
            return None

        right2 = completed[right2_idx]
        stop_loss = calc_stop_from_range(bars, right2_idx, current_idx)
        reason = (
            f"右1涨幅 {right1_pct:.2f}% 达到策略二阈值，"
            f"右2形成后下跌再上，当前突破右2 {format_price(right2.high)} 为买点。"
        )
        details = [
            f"右1时间={right1.dt.strftime('%H:%M')} 价格={format_price(right1.high)}",
            f"右2时间={right2.dt.strftime('%H:%M')} 价格={format_price(right2.high)}",
            f"现价={format_price(current.close)}",
            f"均价线={format_price(session_vwap(bars))}",
        ]
        return Signal(
            code=state.code,
            name=state.name or state.code,
            signal_time=current.dt,
            pattern_type="策略二",
            buy_type="突破右二",
            strength="强",
            entry_price=current.close,
            stop_loss=stop_loss,
            invalidation=stop_loss,
            left1_pct=right1_pct,
            left1_time=right1.dt.strftime("%H:%M"),
            right1_volume_ok=True,
            trigger_level=right2.high,
            session_vwap=session_vwap(bars),
            reason=reason,
            buy_time=current.dt.strftime("%H:%M"),
            right1_time=right1.dt.strftime("%H:%M"),
            right1_price=right1.high,
            right2_time=right2.dt.strftime("%H:%M"),
            right2_price=right2.high,
            details=details,
        )

    def merge_signals(self, signal1: Signal | None, signal2: Signal | None) -> Signal | None:
        return signal1 or signal2

    def send_signal(self, state: SymbolState, signal: Signal) -> None:
        key = f"{signal.pattern_type}:{signal.buy_type}"
        if not self.can_alert(state, key, signal.signal_time):
            return

        self.mark_alert(state, key, signal.signal_time)

        lines = [
            f"[{signal.name} {signal.code}] {signal.pattern_type} 命中",
            f"买点类型: {signal.buy_type}",
            f"信号强度: {signal.strength}",
            f"右1: {signal.left1_time} 涨幅 {signal.left1_pct:.2f}%",
            f"触发位: {format_price(signal.trigger_level)}",
            f"进场参考: {format_price(signal.entry_price)}",
            f"止损位: {format_price(signal.stop_loss)}",
            f"失效位: {format_price(signal.invalidation)}",
            f"均价线: {format_price(signal.session_vwap)}",
            f"原因: {signal.reason}",
        ]
        lines.extend(signal.details)

        message = " | ".join(lines)
        self.log(message)

        if self.config.get("enable_sound", True):
            try:
                from guanlan.core.services.sound import play as play_sound

                play_sound("alarm")
            except Exception:
                pass

        if self.config.get("enable_dingtalk", False):
            try:
                from guanlan.core.app import AppEngine

                title = f"分时买点提醒 {signal.name} {signal.code}"
                content = "\n".join(f"- {line}" for line in lines)
                AppEngine.instance().main_engine.send_dingtalk(title, content)
            except Exception as exc:
                self.log(f"[{signal.code}] 钉钉提醒失败: {exc}")

        self.handle_paper_signal(state, signal)

    def paper_trading_enabled(self) -> bool:
        return bool(self.config.get("paper_trading_enabled", False))

    def paper_force_exit_time(self) -> dtime:
        return parse_hhmm(
            str(self.config.get("paper_force_exit_time", "14:55")),
            dtime(14, 55),
        )

    def calc_paper_volume(self, price: float) -> int:
        if price <= 0:
            return 0

        trade_amount = float(self.config.get("paper_trade_amount", 0) or 0)
        lot_size = max(int(self.config.get("paper_lot_size", 100) or 100), 1)
        if trade_amount <= 0:
            return 0

        lots = int(trade_amount // (price * lot_size))
        return max(lots, 0) * lot_size

    def handle_paper_signal(self, state: SymbolState, signal: Signal) -> None:
        if not self.paper_trading_enabled():
            return

        if self.config.get("paper_require_strong_signal", True) and signal.strength != "强":
            return

        # 先把保护位同步进现有持仓；如果当前没有持仓，这一步会直接忽略。
        self.engine.paper_mark(
            state.code,
            signal.entry_price,
            signal.signal_time,
            name=signal.name,
            stop_loss=signal.stop_loss,
            invalidation=signal.invalidation,
            reason=signal.reason,
        )

        position = self.engine.get_paper_position(state.code)
        if position:
            max_entries = max(int(self.config.get("paper_max_entries_per_symbol", 2) or 2), 1)
            if int(position.get("entry_count", 1) or 1) >= max_entries:
                return

            if signal.entry_price < float(position.get("avg_price", 0) or 0):
                return
        else:
            snapshot = self.engine.get_paper_snapshot()
            if len(snapshot.get("positions", [])) >= int(self.config.get("paper_max_positions", 3) or 3):
                self.log(f"[模拟交易] {signal.code} 达到持仓上限，跳过新开仓")
                return

        volume = self.calc_paper_volume(signal.entry_price)
        if volume <= 0:
            self.log(
                f"[模拟交易] {signal.code} 下单金额不足以买入 1 手，"
                f"当前金额={self.config.get('paper_trade_amount')} 价格={signal.entry_price:.3f}"
            )
            return

        result = self.engine.paper_buy(
            state.code,
            signal.name,
            signal.entry_price,
            volume,
            signal.signal_time,
            reason=signal.reason,
            pattern_type=signal.pattern_type,
            buy_type=signal.buy_type,
            stop_loss=signal.stop_loss,
            invalidation=signal.invalidation,
            initial_cash=float(self.config.get("paper_initial_cash", 100000) or 100000),
        )
        if result.get("ok"):
            self.log(f"[模拟交易] {result['message']}")
        else:
            self.log(f"[模拟交易] {signal.code} 下单失败: {result.get('message', '未知错误')}")

    def manage_paper_position(self, state: SymbolState, quote: Quote) -> None:
        if not self.paper_trading_enabled():
            return

        position = self.engine.get_paper_position(state.code)
        if not position:
            return

        self.engine.paper_mark(
            state.code,
            quote.price,
            quote.dt,
            name=quote.name,
        )

        position = self.engine.get_paper_position(state.code)
        if not position:
            return

        position_day = str(position.get("trading_day", "") or "")
        current_day = quote.dt.strftime("%Y-%m-%d")
        if position_day and position_day != current_day:
            result = self.engine.paper_sell(
                state.code,
                quote.name,
                quote.price,
                int(position.get("volume", 0) or 0),
                quote.dt,
                reason="隔夜不留仓，次日首个报价自动平仓",
            )
            if result.get("ok"):
                self.log(f"[模拟交易] {result['message']}")
            return

        stop_loss = float(position.get("stop_loss", 0) or 0)
        invalidation = float(position.get("invalidation", 0) or 0)
        risk_level = max(stop_loss, invalidation)
        if risk_level > 0 and quote.price <= risk_level:
            result = self.engine.paper_sell(
                state.code,
                quote.name,
                quote.price,
                int(position.get("volume", 0) or 0),
                quote.dt,
                reason=f"触发止损/失效位 {format_price(risk_level)}",
            )
            if result.get("ok"):
                self.log(f"[模拟交易] {result['message']}")
            return

        if quote.dt.time() >= self.paper_force_exit_time():
            result = self.engine.paper_sell(
                state.code,
                quote.name,
                quote.price,
                int(position.get("volume", 0) or 0),
                quote.dt,
                reason=f"到达日内平仓时间 {self.paper_force_exit_time().strftime('%H:%M')}",
            )
            if result.get("ok"):
                self.log(f"[模拟交易] {result['message']}")

    def run_loop(self) -> None:
        symbols: list[str] = self.config["symbols"]
        if not symbols:
            self.log(f"配置文件未填写股票代码，请编辑: {CONFIG_PATH}")
            return

        self.log(f"脚本启动，监控股票: {', '.join(symbols)}")
        self.log(f"配置文件: {CONFIG_PATH}")
        if self.paper_trading_enabled():
            self.log(
                "[模拟交易] 已启用，"
                f"初始资金={float(self.config.get('paper_initial_cash', 100000)):.2f}，"
                f"单笔金额={float(self.config.get('paper_trade_amount', 20000)):.2f}"
            )
        enabled = []
        if self.strategy_one_enabled():
            enabled.append("策略一")
        if self.strategy_two_enabled():
            enabled.append("策略二")
        self.log(f"已启用策略: {', '.join(enabled) if enabled else '无'}")
        self.log(
            "过滤开关: "
            f"预亏={'开' if self.preloss_filter_enabled() else '关'} | "
            f"MACD DIF<DEA={'开' if self.macd_bearish_filter_enabled() else '关'} | "
            f"收益/EPS负数={'开' if self.negative_earnings_filter_enabled() else '关'}"
        )

        while self.engine.strategy_active:
            now = datetime.now()

            # 每个交易日重新同步一次当天 1 分钟历史，保证中途启动也有左1/右2上下文。
            for state in self.states.values():
                if state.session_date != now.date():
                    self.sync_history_if_needed(state, now)

            if not is_stock_trading_time(now):
                sleep(float(self.config["poll_interval_seconds"]))
                continue

            try:
                quotes = fetch_quotes(self.session, symbols)
            except Exception as exc:
                self.log(f"拉取实时行情失败: {exc}")
                sleep(float(self.config["poll_interval_seconds"]))
                continue

            for code in symbols:
                state = self.states.setdefault(code, SymbolState(code=code))
                quote = quotes.get(code)
                if not quote:
                    continue

                if state.session_date != quote.dt.date() or not state.bars:
                    self.sync_history_if_needed(state, quote.dt)

                self.update_from_quote(state, quote)
                self.manage_paper_position(state, quote)

                signal1, signal2 = self.evaluate_signals(state)
                signal = self.merge_signals(signal1, signal2)
                if signal:
                    self.send_signal(state, signal)

            sleep(float(self.config["poll_interval_seconds"]))


def run(engine) -> None:
    watcher = PatternWatcher(engine)
    watcher.run_loop()
