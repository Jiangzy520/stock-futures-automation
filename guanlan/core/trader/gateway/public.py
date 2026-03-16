# -*- coding: utf-8 -*-
"""
观澜量化 - 公共行情网关

第一版只做市场数据接入，不支持委托交易。
当前使用 Yahoo Finance 的公开 chart 接口，支持美股、港股、国际期货。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from typing import Any

import requests
from vnpy.trader.constant import Exchange, Product
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    AccountData,
    CancelRequest,
    ContractData,
    HistoryRequest,
    OrderRequest,
    SubscribeRequest,
    TickData,
)


HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}


@dataclass(frozen=True)
class SymbolConfig:
    """公共行情标的配置。"""

    symbol: str
    exchange: Exchange
    provider_symbol: str
    name: str
    product: Product
    pricetick: float
    size: float
    min_volume: float

    @property
    def vt_symbol(self) -> str:
        return f"{self.symbol}.{self.exchange.value}"


class PublicDataGateway(BaseGateway):
    """公共行情网关。"""

    default_name: str = "PUBLIC"
    default_setting: dict[str, Any] = {}

    exchanges: list[Exchange] = [
        Exchange.NASDAQ,
        Exchange.NYSE,
        Exchange.SEHK,
        Exchange.CME,
        Exchange.CBOT,
        Exchange.COMEX,
        Exchange.NYMEX,
        Exchange.ICE,
        Exchange.HKFE,
        Exchange.SGX,
    ]

    def __init__(self, event_engine, gateway_name: str) -> None:
        super().__init__(event_engine, gateway_name)

        self.session: requests.Session = requests.Session()
        self.provider: str = "yahoo"
        self.poll_interval: int = 10
        self.symbols: dict[str, SymbolConfig] = {}
        self.subscribed: set[str] = set()
        self.last_total_volume: dict[str, float] = {}

        self._active: bool = False
        self._thread: Thread | None = None
        self._stop_event: Event = Event()

    def connect(self, setting: dict) -> None:
        """连接公共行情并推送合约清单。"""
        self.provider = str(setting.get("provider", "yahoo")).strip().lower() or "yahoo"
        self.poll_interval = max(int(setting.get("poll_interval_seconds", 10) or 10), 3)
        self.symbols = self._load_symbols(setting.get("symbols", []))

        if self.provider != "yahoo":
            self.write_log(f"暂不支持的数据源: {self.provider}")
            return

        if not self.symbols:
            self.write_log("公共行情配置为空，请填写 public_market_data.json")
            return

        for item in self.symbols.values():
            contract = ContractData(
                gateway_name=self.gateway_name,
                symbol=item.symbol,
                exchange=item.exchange,
                name=item.name,
                product=item.product,
                size=item.size,
                pricetick=item.pricetick,
                min_volume=item.min_volume,
                history_data=False,
            )
            self.on_contract(contract)

        self.query_account()

        if self._active:
            return

        self._active = True
        self._stop_event.clear()
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

        self.write_log(
            f"公共行情已连接，数据源={self.provider}，可用标的={len(self.symbols)}"
        )

    def close(self) -> None:
        """关闭轮询线程。"""
        if not self._active:
            return

        self._active = False
        self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None

        self.write_log("公共行情已断开")

    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅实时行情。"""
        vt_symbol = f"{req.symbol}.{req.exchange.value}"
        if vt_symbol not in self.symbols:
            self.write_log(f"公共行情未配置该标的: {vt_symbol}")
            return

        self.subscribed.add(vt_symbol)
        self.write_log(f"开始订阅行情: {vt_symbol}")

    def send_order(self, req: OrderRequest) -> str:
        """公共行情只支持行情，不支持交易。"""
        self.write_log("公共行情网关只提供行情，不支持下单")
        return ""

    def cancel_order(self, req: CancelRequest) -> None:
        """公共行情只支持行情，不支持撤单。"""
        self.write_log("公共行情网关只提供行情，不支持撤单")

    def query_account(self) -> None:
        """推送一个只读行情账户，方便 UI 识别连接状态。"""
        account = AccountData(
            gateway_name=self.gateway_name,
            accountid=self.gateway_name,
            balance=0,
            frozen=0,
        )
        self.on_account(account)

    def query_position(self) -> None:
        """公共行情无持仓。"""
        return

    def query_history(self, req: HistoryRequest) -> list:
        """历史数据暂不走网关，图表历史仍优先读数据库。"""
        return []

    def _run(self) -> None:
        """后台轮询已订阅标的的最新行情。"""
        while self._active and not self._stop_event.is_set():
            if not self.subscribed:
                self._stop_event.wait(self.poll_interval)
                continue

            for vt_symbol in list(self.subscribed):
                item = self.symbols.get(vt_symbol)
                if not item:
                    continue

                try:
                    tick = self._query_yahoo_tick(item)
                except Exception as exc:
                    self.write_log(f"{vt_symbol} 行情拉取失败: {exc}")
                    continue

                if tick:
                    self.on_tick(tick)

            self._stop_event.wait(self.poll_interval)

    def _load_symbols(self, raw_symbols: list[dict[str, Any]]) -> dict[str, SymbolConfig]:
        """加载并校验标的配置。"""
        result: dict[str, SymbolConfig] = {}

        for raw in raw_symbols:
            try:
                exchange = Exchange(str(raw.get("exchange", "")).strip().upper())
                product = Product[str(raw.get("product", "EQUITY")).strip().upper()]
                symbol = str(raw.get("symbol", "")).strip().upper()
                provider_symbol = str(raw.get("provider_symbol", symbol)).strip()
                name = str(raw.get("name", symbol)).strip() or symbol
                pricetick = float(raw.get("pricetick", 0.01) or 0.01)
                size = float(raw.get("size", 1) or 1)
                min_volume = float(raw.get("min_volume", 1) or 1)
            except Exception as exc:
                self.write_log(f"忽略无效公共行情配置: {raw} ({exc})")
                continue

            if not symbol or not provider_symbol:
                continue

            item = SymbolConfig(
                symbol=symbol,
                exchange=exchange,
                provider_symbol=provider_symbol,
                name=name,
                product=product,
                pricetick=pricetick,
                size=size,
                min_volume=min_volume,
            )
            result[item.vt_symbol] = item

        return result

    def _query_yahoo_tick(self, item: SymbolConfig) -> TickData | None:
        """通过 Yahoo chart 接口获取最新 1 分钟行情。"""
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{item.provider_symbol}?interval=1m&range=1d&includePrePost=false"
        )
        resp = self.session.get(url, headers=HEADERS, timeout=10)
        data = resp.json()

        chart = data.get("chart", {})
        error = chart.get("error")
        if error:
            raise RuntimeError(error.get("description") or error.get("code") or "未知错误")

        result = (chart.get("result") or [None])[0]
        if not result:
            return None

        meta = result.get("meta") or {}
        timestamps = result.get("timestamp") or []
        indicators = result.get("indicators") or {}
        quote = (indicators.get("quote") or [{}])[0]

        closes = quote.get("close") or []
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        volumes = quote.get("volume") or []

        idx = self._last_valid_index(closes)
        if idx is None or idx >= len(timestamps):
            return None

        timestamp = timestamps[idx]
        gmtoffset = int(meta.get("gmtoffset") or 0)
        tz = timezone(timedelta(seconds=gmtoffset))
        dt = datetime.fromtimestamp(timestamp, tz).replace(tzinfo=None)

        total_volume = self._sum_values(volumes, idx)
        prev_total = self.last_total_volume.get(item.vt_symbol, 0.0)
        last_volume = max(total_volume - prev_total, 0.0)
        self.last_total_volume[item.vt_symbol] = total_volume

        tick = TickData(
            gateway_name=self.gateway_name,
            symbol=item.symbol,
            exchange=item.exchange,
            datetime=dt,
            name=meta.get("shortName") or item.name,
            volume=total_volume,
            turnover=0,
            open_interest=0,
            last_price=float(self._value_at(closes, idx)),
            last_volume=last_volume,
            open_price=float(meta.get("regularMarketOpen") or self._value_at(opens, idx)),
            high_price=float(meta.get("regularMarketDayHigh") or self._value_at(highs, idx)),
            low_price=float(meta.get("regularMarketDayLow") or self._value_at(lows, idx)),
            pre_close=float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0),
            localtime=datetime.now(),
        )
        return tick

    @staticmethod
    def _last_valid_index(values: list[Any]) -> int | None:
        """返回最后一个非空 close 的索引。"""
        for i in range(len(values) - 1, -1, -1):
            value = values[i]
            if value is not None:
                return i
        return None

    @staticmethod
    def _value_at(values: list[Any], index: int) -> float:
        """安全读取数组中的数值。"""
        if index >= len(values):
            return 0.0
        value = values[index]
        if value is None:
            return 0.0
        return float(value)

    @staticmethod
    def _sum_values(values: list[Any], index: int) -> float:
        """累计求和到指定索引，用于将分钟量转成当日累计量。"""
        if index < 0:
            return 0.0
        total = 0.0
        for value in values[:index + 1]:
            if value is not None:
                total += float(value)
        return total
