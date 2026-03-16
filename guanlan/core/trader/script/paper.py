# -*- coding: utf-8 -*-
"""
观澜量化 - 脚本纸面交易簿

为脚本策略提供股票纸面交易能力：
1. 维护模拟账户现金、持仓、成交记录
2. 支持按最新价盯市，计算浮动盈亏
3. 状态持久化到本地 JSON，方便界面查看和重启恢复
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from threading import RLock
from typing import Any

from guanlan.core.utils.common import load_json_file, random_string, save_json_file


PAPER_SETTING_FILENAME = "config/script_paper_trading.json"
EVENT_SCRIPT_PAPER = "eScriptPaper"


@dataclass
class PaperAccount:
    """单个脚本的模拟账户汇总。"""

    script_name: str
    initial_cash: float = 100000.0
    cash: float = 100000.0
    position_value: float = 0.0
    equity: float = 100000.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_trades: int = 0
    closed_trades: int = 0
    win_trades: int = 0
    position_count: int = 0
    last_update: str = ""


@dataclass
class PaperPosition:
    """模拟持仓。"""

    key: str
    script_name: str
    symbol: str
    name: str
    trading_day: str
    volume: int
    avg_price: float
    last_price: float
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pct: float = 0.0
    realized_pnl: float = 0.0
    entry_count: int = 1
    stop_loss: float = 0.0
    invalidation: float = 0.0
    open_time: str = ""
    last_trade_time: str = ""
    update_time: str = ""
    last_signal: str = ""
    last_reason: str = ""


@dataclass
class PaperTrade:
    """模拟成交记录。"""

    trade_id: str
    script_name: str
    symbol: str
    name: str
    direction: str
    offset: str
    trade_time: str
    price: float
    volume: int
    amount: float
    pnl: float = 0.0
    pnl_pct: float = 0.0
    remaining_volume: int = 0
    pattern_type: str = ""
    buy_type: str = ""
    reason: str = ""


def _round2(value: float) -> float:
    return round(float(value or 0.0), 2)


def _timestamp(dt: datetime | None = None) -> str:
    return (dt or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _trading_day(dt: datetime | None = None) -> str:
    return (dt or datetime.now()).strftime("%Y-%m-%d")


def _win_rate_text(account: PaperAccount) -> str:
    if account.closed_trades <= 0:
        return "0.0%"
    return f"{account.win_trades / account.closed_trades * 100:.1f}%"


class ScriptPaperBook:
    """脚本纸面交易状态簿。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self.accounts: dict[str, PaperAccount] = {}
        self.positions: dict[str, PaperPosition] = {}
        self.trades: list[PaperTrade] = []
        self.load()

    @staticmethod
    def _position_key(script_name: str, symbol: str) -> str:
        return f"{script_name}:{symbol}"

    def load(self) -> None:
        """从本地文件恢复状态。"""
        data = load_json_file(PAPER_SETTING_FILENAME)

        accounts: dict[str, PaperAccount] = {}
        for raw in data.get("accounts", []):
            try:
                account = PaperAccount(**raw)
            except TypeError:
                continue
            accounts[account.script_name] = account

        positions: dict[str, PaperPosition] = {}
        for raw in data.get("positions", []):
            try:
                position = PaperPosition(**raw)
            except TypeError:
                continue
            positions[position.key] = position

        trades: list[PaperTrade] = []
        for raw in data.get("trades", []):
            try:
                trades.append(PaperTrade(**raw))
            except TypeError:
                continue

        with self._lock:
            self.accounts = accounts
            self.positions = positions
            self.trades = trades
            self._refresh_all_locked()

    def save(self) -> None:
        """持久化当前状态。"""
        with self._lock:
            payload = {
                "accounts": [asdict(item) for item in self.accounts.values()],
                "positions": [asdict(item) for item in self.positions.values()],
                "trades": [asdict(item) for item in self.trades[-2000:]],
            }
        save_json_file(PAPER_SETTING_FILENAME, payload)

    def get_snapshot(self, script_name: str | None = None, trade_limit: int = 500) -> dict[str, Any]:
        """导出当前快照，供 UI 展示。"""
        with self._lock:
            accounts = list(self.accounts.values())
            positions = list(self.positions.values())
            trades = list(self.trades)

        if script_name:
            accounts = [item for item in accounts if item.script_name == script_name]
            positions = [item for item in positions if item.script_name == script_name]
            trades = [item for item in trades if item.script_name == script_name]

        accounts.sort(key=lambda item: item.script_name)
        positions.sort(key=lambda item: (item.script_name, item.symbol))
        trades.sort(key=lambda item: item.trade_time, reverse=True)
        if trade_limit > 0:
            trades = trades[:trade_limit]

        return {
            "generated_at": _timestamp(),
            "accounts": [
                {
                    **asdict(item),
                    "win_rate": _win_rate_text(item),
                }
                for item in accounts
            ],
            "positions": [asdict(item) for item in positions],
            "trades": [asdict(item) for item in trades],
        }

    def get_position(self, script_name: str, symbol: str) -> dict[str, Any] | None:
        """获取单个持仓。"""
        key = self._position_key(script_name, symbol)
        with self._lock:
            position = self.positions.get(key)
            return asdict(position) if position else None

    def ensure_account(self, script_name: str, initial_cash: float = 100000.0) -> PaperAccount:
        """获取或创建模拟账户。"""
        with self._lock:
            account = self.accounts.get(script_name)
            if account:
                return account

            cash = _round2(initial_cash if initial_cash > 0 else 100000.0)
            account = PaperAccount(
                script_name=script_name,
                initial_cash=cash,
                cash=cash,
                equity=cash,
                last_update=_timestamp(),
            )
            self.accounts[script_name] = account
            return account

    def buy(
        self,
        script_name: str,
        symbol: str,
        name: str,
        price: float,
        volume: int,
        trade_time: datetime | None = None,
        reason: str = "",
        pattern_type: str = "",
        buy_type: str = "",
        stop_loss: float = 0.0,
        invalidation: float = 0.0,
        initial_cash: float = 100000.0,
    ) -> dict[str, Any]:
        """模拟买入。"""
        if price <= 0 or volume <= 0:
            return {"ok": False, "message": "价格或数量无效"}

        dt_text = _timestamp(trade_time)
        day_text = _trading_day(trade_time)
        amount = _round2(price * volume)

        with self._lock:
            account = self.ensure_account(script_name, initial_cash)
            if account.cash + 1e-6 < amount:
                return {
                    "ok": False,
                    "message": f"可用资金不足，当前现金 {account.cash:.2f}，买入金额 {amount:.2f}",
                }

            key = self._position_key(script_name, symbol)
            position = self.positions.get(key)
            offset = "开仓"

            if position:
                total_cost = position.avg_price * position.volume + amount
                position.volume += volume
                position.avg_price = total_cost / position.volume if position.volume > 0 else price
                position.entry_count += 1
                position.last_trade_time = dt_text
                position.update_time = dt_text
                position.last_price = price
                position.name = name or position.name
                position.last_reason = reason or position.last_reason
                position.last_signal = f"{pattern_type}/{buy_type}".strip("/")
                if stop_loss > 0:
                    position.stop_loss = max(position.stop_loss, stop_loss)
                if invalidation > 0:
                    position.invalidation = max(position.invalidation, invalidation)
                offset = "加仓"
            else:
                position = PaperPosition(
                    key=key,
                    script_name=script_name,
                    symbol=symbol,
                    name=name or symbol,
                    trading_day=day_text,
                    volume=volume,
                    avg_price=price,
                    last_price=price,
                    stop_loss=max(stop_loss, 0.0),
                    invalidation=max(invalidation, 0.0),
                    open_time=dt_text,
                    last_trade_time=dt_text,
                    update_time=dt_text,
                    last_signal=f"{pattern_type}/{buy_type}".strip("/"),
                    last_reason=reason,
                )
                self.positions[key] = position

            account.cash = _round2(account.cash - amount)

            trade = PaperTrade(
                trade_id=f"P{datetime.now().strftime('%Y%m%d%H%M%S')}{random_string(4)}",
                script_name=script_name,
                symbol=symbol,
                name=name or symbol,
                direction="买入",
                offset=offset,
                trade_time=dt_text,
                price=_round2(price),
                volume=int(volume),
                amount=amount,
                remaining_volume=position.volume,
                pattern_type=pattern_type,
                buy_type=buy_type,
                reason=reason,
            )
            self.trades.append(trade)

            self._refresh_position_locked(position)
            self._refresh_account_locked(script_name)

        self.save()
        return {
            "ok": True,
            "message": f"{symbol} {name or symbol} 模拟{offset} {volume} 股 @ {price:.3f}",
        }

    def sell(
        self,
        script_name: str,
        symbol: str,
        name: str,
        price: float,
        volume: int,
        trade_time: datetime | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        """模拟卖出平仓。"""
        if price <= 0 or volume <= 0:
            return {"ok": False, "message": "价格或数量无效"}

        dt_text = _timestamp(trade_time)
        amount = _round2(price * volume)

        with self._lock:
            key = self._position_key(script_name, symbol)
            position = self.positions.get(key)
            if not position:
                return {"ok": False, "message": f"{symbol} 当前没有模拟持仓"}

            close_volume = min(int(volume), position.volume)
            amount = _round2(price * close_volume)
            realized = _round2((price - position.avg_price) * close_volume)
            pnl_pct = 0.0
            if position.avg_price > 0:
                pnl_pct = (price / position.avg_price - 1) * 100

            account = self.ensure_account(script_name, 100000.0)
            account.cash = _round2(account.cash + amount)
            account.realized_pnl = _round2(account.realized_pnl + realized)
            account.closed_trades += 1
            if realized > 0:
                account.win_trades += 1

            position.volume -= close_volume
            position.last_price = price
            position.realized_pnl = _round2(position.realized_pnl + realized)
            position.last_trade_time = dt_text
            position.update_time = dt_text
            position.last_reason = reason or position.last_reason
            remaining_volume = position.volume

            if position.volume <= 0:
                remaining_volume = 0
                self.positions.pop(key, None)
            else:
                self._refresh_position_locked(position)

            trade = PaperTrade(
                trade_id=f"P{datetime.now().strftime('%Y%m%d%H%M%S')}{random_string(4)}",
                script_name=script_name,
                symbol=symbol,
                name=name or position.name,
                direction="卖出",
                offset="平仓",
                trade_time=dt_text,
                price=_round2(price),
                volume=close_volume,
                amount=amount,
                pnl=realized,
                pnl_pct=round(pnl_pct, 2),
                remaining_volume=remaining_volume,
                reason=reason,
            )
            self.trades.append(trade)

            self._refresh_account_locked(script_name)

        self.save()
        return {
            "ok": True,
            "message": f"{symbol} {name or symbol} 模拟平仓 {close_volume} 股 @ {price:.3f}，盈亏 {realized:.2f}",
        }

    def mark_price(
        self,
        script_name: str,
        symbol: str,
        price: float,
        mark_time: datetime | None = None,
        name: str = "",
        stop_loss: float | None = None,
        invalidation: float | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        """按最新价更新持仓。"""
        if price <= 0:
            return {"ok": False, "message": "价格无效"}

        key = self._position_key(script_name, symbol)
        dt_text = _timestamp(mark_time)

        with self._lock:
            position = self.positions.get(key)
            if not position:
                return {"ok": False, "message": f"{symbol} 当前没有模拟持仓"}

            position.last_price = price
            position.update_time = dt_text
            if name:
                position.name = name
            if reason:
                position.last_reason = reason
            if stop_loss and stop_loss > 0:
                position.stop_loss = max(position.stop_loss, stop_loss)
            if invalidation and invalidation > 0:
                position.invalidation = max(position.invalidation, invalidation)

            self._refresh_position_locked(position)
            self._refresh_account_locked(script_name)

        self.save()
        return {"ok": True, "message": f"{symbol} 模拟持仓已按最新价更新"}

    def close_all(self, script_name: str | None = None, reason: str = "") -> dict[str, Any]:
        """按最新价平掉全部持仓。"""
        with self._lock:
            targets = [
                item for item in self.positions.values()
                if not script_name or item.script_name == script_name
            ]

        closed = 0
        messages: list[str] = []
        for position in targets:
            price = position.last_price or position.avg_price
            result = self.sell(
                position.script_name,
                position.symbol,
                position.name,
                price,
                position.volume,
                reason=reason or "界面手动全部平仓",
            )
            if result.get("ok"):
                closed += 1
                messages.append(result["message"])

        return {
            "ok": True,
            "closed": closed,
            "message": "；".join(messages) if messages else "没有需要平仓的模拟持仓",
        }

    def clear(self, script_name: str | None = None) -> dict[str, Any]:
        """清空纸面交易记录。"""
        with self._lock:
            if script_name:
                self.accounts.pop(script_name, None)
                self.positions = {
                    key: item
                    for key, item in self.positions.items()
                    if item.script_name != script_name
                }
                self.trades = [item for item in self.trades if item.script_name != script_name]
            else:
                self.accounts.clear()
                self.positions.clear()
                self.trades.clear()

            self._refresh_all_locked()

        self.save()
        return {"ok": True, "message": "模拟交易记录已清空"}

    def _refresh_all_locked(self) -> None:
        scripts = set(self.accounts.keys()) | {item.script_name for item in self.positions.values()}
        for position in self.positions.values():
            self._refresh_position_locked(position)
        for script_name in scripts:
            self._refresh_account_locked(script_name)

    def _refresh_position_locked(self, position: PaperPosition) -> None:
        position.market_value = _round2(position.last_price * position.volume)
        position.unrealized_pnl = _round2((position.last_price - position.avg_price) * position.volume)
        if position.avg_price > 0:
            position.unrealized_pct = round((position.last_price / position.avg_price - 1) * 100, 2)
        else:
            position.unrealized_pct = 0.0

    def _refresh_account_locked(self, script_name: str) -> None:
        account = self.ensure_account(script_name, 100000.0)
        positions = [item for item in self.positions.values() if item.script_name == script_name]
        account.position_value = _round2(sum(item.market_value for item in positions))
        account.unrealized_pnl = _round2(sum(item.unrealized_pnl for item in positions))
        account.position_count = len(positions)
        account.total_trades = len([item for item in self.trades if item.script_name == script_name])
        account.equity = _round2(account.cash + account.position_value)
        account.last_update = _timestamp()

