# -*- coding: utf-8 -*-
"""
观澜量化 - 资金监控面板

Author: 海山观澜
"""

import csv
import json
from pathlib import Path

from PySide6.QtCore import QTimer

from guanlan.core.constants import CONFIG_DIR
from vnpy.trader.event import EVENT_ACCOUNT
from vnpy.trader.object import AccountData

from .base import BaseMonitor, MonitorPanel

ALLTICK_MANAGER_DIR = CONFIG_DIR / "alltick_manager"
REPLAY_RESULTS_DIR = CONFIG_DIR / "replay_results"
SCAN_VT_ACCOUNTID = "__scan_status__.公告行情"
BUY_DETAIL_PREFIX = "__scan_buy__."
MAX_BUY_PREVIEW = 12


class _AccountTable(BaseMonitor):
    """资金表格"""

    headers = {
        "accountid": {"display": "脚本策略信息"},
        "balance": {"display": "左1/左2"},
        "frozen": {"display": "右1/右2"},
        "available": {"display": "确认购买时间"},
        "gateway_name": {"display": "具体时间/买入价"},
    }
    data_key = "vt_accountid"


class AccountMonitor(MonitorPanel):
    """资金监控面板"""

    table_class = _AccountTable
    filter_fields = {}
    event_type = EVENT_ACCOUNT

    def __init__(self, parent=None) -> None:
        self._alltick_dir = ALLTICK_MANAGER_DIR
        self._replay_result_dir = REPLAY_RESULTS_DIR
        self._tick_scan_label = None
        self._tick_scan_timer: QTimer | None = None
        self._detail_row_keys: set[str] = set()
        super().__init__(parent)
        self._init_tick_scan_status()

    def _convert_data(self, acc: AccountData) -> dict | None:
        # 清除公共行情账户在资金表中的占位文字行（如：公共行情/公告行情）
        gateway_name = str(acc.gateway_name).strip()
        accountid = str(acc.accountid).strip()
        if (
            gateway_name in {"公共行情", "公告行情"}
            and accountid in {"", "公共行情", "公告行情"}
            and float(acc.balance or 0) == 0.0
            and float(acc.frozen or 0) == 0.0
            and float(acc.available or 0) == 0.0
        ):
            return None
        return {
            "accountid": acc.accountid,
            "balance": acc.balance,
            "frozen": acc.frozen,
            "available": acc.available,
            "gateway_name": acc.gateway_name,
            "vt_accountid": acc.vt_accountid,
        }

    def _flush(self) -> None:
        """重载批量刷新：允许跳过被过滤的占位账户行。"""
        if not self._buffer:
            return

        batch = self._buffer
        self._buffer = []

        self._table.setUpdatesEnabled(False)
        for raw_data in batch:
            converted = self._convert_data(raw_data)
            if not converted:
                continue
            self._table.process_data(converted)
            self._update_options(converted)
        self._table.setUpdatesEnabled(True)

        self._apply_filters()
        if self.auto_scroll and self._scroll_check.isChecked():
            self._table.scrollToBottom()

    def _init_tick_scan_status(self) -> None:
        """初始化扫描状态定时器（顶部蓝色状态栏已按需隐藏）"""

        self._tick_scan_timer = QTimer(self)
        self._tick_scan_timer.setInterval(3000)
        self._tick_scan_timer.timeout.connect(self._refresh_tick_scan_status)
        self._tick_scan_timer.start()

        self._refresh_tick_scan_status()

    def _refresh_tick_scan_status(self) -> None:
        """刷新扫描统计，并同步到资金表的“公告行情”行"""
        stats = self._collect_panel_stats()
        if not stats:
            if self._tick_scan_label:
                self._tick_scan_label.setText("脚本策略：未检测到可用扫描结果")
            self._upsert_scan_account_row(None)
            return

        text = (
            "脚本策略  "
            f"自:{stats['watch_total']}  "
            f"扫:{stats['scanned_total']}  "
            f"过:{stats['filtered_total']}  "
            f"买:{stats['buyable_total']}"
        )
        if stats.get("api_total", 0):
            text += (
                f"  已用API:{stats.get('api_used', 0)}"
                f"  剩余API:{stats.get('api_remaining', 0)}"
            )
        if self._tick_scan_label:
            self._tick_scan_label.setText(text)
        self._upsert_scan_account_row(stats)

    def _upsert_scan_account_row(self, stats: dict | None) -> None:
        """将扫描统计写入资金表，显示在“公告行情”行。"""
        if stats:
            accountid = (
                f"自选:{stats['watch_total']}  "
                f"扫描:{stats['scanned_total']}  "
                f"过滤:{stats['filtered_total']}  "
                f"可买:{stats['buyable_total']}"
            )
            balance = f"已用API:{stats.get('api_used', 0)}"
            frozen = f"剩余API:{stats.get('api_remaining', 0)}"
            available = f"总API:{stats.get('api_total', 0)}"
            gateway_name = stats.get("buy_preview", "")
        else:
            accountid = ""
            balance = ""
            frozen = ""
            available = ""
            gateway_name = ""

        row = {
            "accountid": accountid,
            "balance": balance,
            "frozen": frozen,
            "available": available,
            "gateway_name": gateway_name,
            "vt_accountid": SCAN_VT_ACCOUNTID,
        }
        self._table.process_data(row)
        self._update_options(row)
        self._sync_buy_detail_rows((stats or {}).get("buyable_rows", []))
        self._apply_filters()

    def _collect_panel_stats(self) -> dict | None:
        """整合 API 配对状态 + 脚本策略扫描结果。"""
        tick_stats = self._collect_tick_scan_stats()
        if not tick_stats:
            return None

        watch_total = int(tick_stats.get("watch_total", 0))
        strategy_stats = self._collect_latest_strategy_stats(watch_total)
        if strategy_stats:
            merged = dict(tick_stats)
            merged.update(strategy_stats)
            return merged

        # 无脚本回放结果时，保持基础数字可读
        merged = dict(tick_stats)
        merged.update(
            {
                "scanned_total": watch_total,
                "filtered_total": watch_total,
                "buyable_total": 0,
                "buy_preview": "",
                "strategy_file": "",
                "buyable_rows": [],
            }
        )
        return merged

    def _collect_latest_strategy_stats(self, watch_total: int) -> dict | None:
        """读取最新且最匹配当前自选池规模的历史回放结果。"""
        if not self._replay_result_dir.exists():
            return None

        candidates: list[tuple[int, float, Path, dict]] = []
        for json_file in self._replay_result_dir.glob("history_signal_result_*.json"):
            payload = self._read_json_dict(json_file)
            if not payload:
                continue

            symbols = payload.get("symbols")
            if not isinstance(symbols, list):
                continue
            symbol_count = len(symbols)
            if symbol_count <= 0:
                continue

            try:
                mtime = json_file.stat().st_mtime
            except OSError:
                mtime = 0.0
            candidates.append((symbol_count, mtime, json_file, payload))

        if not candidates:
            return None

        selected: tuple[int, float, Path, dict] | None = None
        if watch_total > 0:
            exact = [item for item in candidates if item[0] == watch_total]
            if exact:
                selected = max(exact, key=lambda item: item[1])
        if selected is None:
            selected = max(candidates, key=lambda item: (item[0], item[1]))

        _, _, result_file, payload = selected
        day_results = payload.get("day_results") or []
        signal_records = payload.get("signal_records") or []

        scanned_total = 0
        for row in day_results:
            if not isinstance(row, dict):
                continue
            bar_count = self._safe_int(row.get("bar_count"))
            if bar_count > 0:
                scanned_total += 1
        if scanned_total <= 0:
            scanned_total = int(payload.get("active_api_count") or 0) or len(payload.get("symbols") or [])

        buy_map: dict[str, tuple[str, float]] = {}
        buy_rows: dict[str, dict] = {}
        for row in signal_records:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol", "")).strip()
            if not symbol:
                continue
            buy_time = str(row.get("buy_time", "")).strip()
            entry_price = self._safe_float(row.get("entry_price"))
            signal_time = str(row.get("signal_time", "")).strip()

            old = buy_rows.get(symbol)
            if old:
                old_time = str(old.get("signal_time", "")).strip()
                if old_time and signal_time and signal_time >= old_time:
                    continue

            buy_map[symbol] = (buy_time, entry_price)
            buy_rows[symbol] = {
                "symbol": symbol,
                "name": str(row.get("name", "")).strip(),
                "trading_day": str(row.get("trading_day", "")).strip(),
                "signal_time": signal_time,
                "buy_time": buy_time,
                "entry_price": entry_price,
                "left1_time": (
                    str(row.get("left1_point_time", "")).strip()
                    or str(row.get("left1_time", "")).strip()
                ),
                "left2_time": str(row.get("left2_time", "")).strip(),
                "right1_time": str(row.get("right1_time", "")).strip(),
                "right2_time": str(row.get("right2_time", "")).strip(),
                "pattern_type": str(row.get("pattern_type", "")).strip(),
                "buy_type": str(row.get("buy_type", "")).strip(),
            }

        buyable_total = len(buy_map)
        filtered_total = max(scanned_total - buyable_total, 0)
        buyable_rows = sorted(
            buy_rows.values(),
            key=lambda item: (str(item.get("signal_time", "")), str(item.get("symbol", ""))),
        )

        return {
            "scanned_total": scanned_total,
            "filtered_total": filtered_total,
            "buyable_total": buyable_total,
            "buy_preview": self._build_buy_preview(buy_map),
            "buyable_rows": buyable_rows,
            "strategy_file": result_file.name,
        }

    def _sync_buy_detail_rows(self, buyable_rows: list[dict]) -> None:
        """在汇总行下方同步逐股可买明细行。"""
        next_keys: set[str] = set()

        for item in buyable_rows:
            symbol = str(item.get("symbol", "")).strip()
            if not symbol:
                continue

            key = f"{BUY_DETAIL_PREFIX}{symbol}"
            next_keys.add(key)
            row = self._build_buy_detail_row(item, key)
            self._table.process_data(row)
            self._update_options(row)

        stale_keys = self._detail_row_keys - next_keys
        if stale_keys:
            self._remove_rows_by_keys(stale_keys)

        self._detail_row_keys = next_keys

    def _remove_rows_by_keys(self, keys: set[str]) -> None:
        """从表格中删除指定 key 的行，并重建行索引。"""
        table = self._table
        row_indices = [
            table._rows.get(key)
            for key in keys
            if key in table._rows
        ]
        valid_indices = sorted({idx for idx in row_indices if idx is not None}, reverse=True)
        if not valid_indices:
            return

        for row in valid_indices:
            if 0 <= row < table.rowCount():
                table.removeRow(row)
            if 0 <= row < len(table._data_by_row):
                table._data_by_row.pop(row)

        rebuilt_rows: dict[str, int] = {}
        rebuilt_row_data: dict[str, dict] = {}
        data_key = table.data_key
        if data_key:
            for idx, data in enumerate(table._data_by_row):
                key = str(data.get(data_key, ""))
                if key:
                    rebuilt_rows[key] = idx
                    rebuilt_row_data[key] = data
        table._rows = rebuilt_rows
        table._row_data = rebuilt_row_data

    @staticmethod
    def _build_buy_detail_row(item: dict, key: str) -> dict:
        symbol = str(item.get("symbol", "")).strip()
        name = str(item.get("name", "")).strip()

        left1 = str(item.get("left1_time", "")).strip() or "-"
        left2 = str(item.get("left2_time", "")).strip() or "-"
        right1 = str(item.get("right1_time", "")).strip() or "-"
        right2 = str(item.get("right2_time", "")).strip() or "-"
        buy_time = str(item.get("buy_time", "")).strip() or "-"
        trading_day = str(item.get("trading_day", "")).strip()
        signal_time = str(item.get("signal_time", "")).strip()
        entry_price = float(item.get("entry_price", 0.0) or 0.0)
        pattern_type = str(item.get("pattern_type", "")).strip()
        buy_type = str(item.get("buy_type", "")).strip()

        if signal_time:
            confirm_buy_time = signal_time
        elif trading_day and buy_time != "-":
            confirm_buy_time = f"{trading_day} {buy_time}"
        else:
            confirm_buy_time = buy_time

        detail_parts = []
        detail_parts.append(f"买入价:{entry_price:.3f}")
        if buy_time != "-":
            detail_parts.append(f"买点:{buy_time}")
        if pattern_type or buy_type:
            detail_parts.append(f"{pattern_type}/{buy_type}".strip("/"))

        return {
            "accountid": f"{symbol} {name}".strip(),
            "balance": f"左1:{left1} 左2:{left2}",
            "frozen": f"右1:{right1} 右2:{right2}",
            "available": confirm_buy_time,
            "gateway_name": " | ".join(detail_parts),
            "vt_accountid": key,
        }

    def _collect_tick_scan_stats(self) -> dict | None:
        """读取 alltick_manager 文件，计算自选/API使用情况"""
        if not self._alltick_dir.exists():
            return None

        api_file = self._alltick_dir / "apis.txt"
        watchlist_file = self._alltick_dir / "watchlist.csv"
        api_assign_file = self._alltick_dir / "api_assignments.csv"
        stock_assign_file = self._alltick_dir / "stock_assignments.csv"
        settings_file = self._alltick_dir / "settings.json"

        api_total = len(dict.fromkeys(self._read_non_empty_lines(api_file)))
        watch_total = len(self._read_csv_rows(watchlist_file))

        api_rows = self._read_csv_rows(api_assign_file)
        if api_rows:
            api_used = 0
            assigned_stock = 0
            for row in api_rows:
                stock_count_raw = row.get("stock_count", "")
                try:
                    stock_count = int(float(stock_count_raw)) if stock_count_raw else 0
                except (ValueError, TypeError):
                    stock_count = 0

                status = row.get("status", "").lower()
                has_stocks = bool(row.get("stocks", ""))

                if stock_count > 0 or status == "used" or has_stocks:
                    api_used += 1
                assigned_stock += max(stock_count, 0)
        else:
            max_stocks_per_api = self._read_max_stocks_per_api(settings_file)
            api_used = min(api_total, (watch_total + max_stocks_per_api - 1) // max_stocks_per_api)
            assigned_stock = min(watch_total, api_used * max_stocks_per_api)

        stock_rows = self._read_csv_rows(stock_assign_file)
        if stock_rows:
            assigned_stock = sum(1 for row in stock_rows if row.get("api", ""))

        api_remaining = max(0, api_total - api_used)
        watch_unassigned = max(0, watch_total - assigned_stock)

        return {
            "api_total": api_total,
            "watch_total": watch_total,
            "api_used": api_used,
            "api_remaining": api_remaining,
            "watch_unassigned": watch_unassigned,
        }

    @staticmethod
    def _safe_int(value: object) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _safe_float(value: object) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _read_json_dict(path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(data, dict):
            return data
        return None

    @staticmethod
    def _build_buy_preview(buy_map: dict[str, tuple[str, float]]) -> str:
        if not buy_map:
            return ""

        pairs = []
        for symbol in sorted(buy_map.keys())[:MAX_BUY_PREVIEW]:
            buy_time, price = buy_map[symbol]
            if buy_time:
                pairs.append(f"{symbol} {buy_time}@{price:.3f}")
            else:
                pairs.append(f"{symbol}@{price:.3f}")

        hidden = max(0, len(buy_map) - MAX_BUY_PREVIEW)
        if hidden:
            pairs.append(f"...+{hidden}")
        return " | ".join(pairs)

    @staticmethod
    def _read_non_empty_lines(path: Path) -> list[str]:
        """读取非空行"""
        if not path.exists():
            return []
        try:
            return [
                line.strip()
                for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
                if line.strip()
            ]
        except OSError:
            return []

    @staticmethod
    def _read_csv_rows(path: Path) -> list[dict[str, str]]:
        """读取 CSV 行（去空白）"""
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                rows: list[dict[str, str]] = []
                for row in reader:
                    if not row:
                        continue
                    rows.append({k: (v or "").strip() for k, v in row.items()})
                return rows
        except OSError:
            return []

    @staticmethod
    def _read_max_stocks_per_api(path: Path) -> int:
        """从 settings.json 读取单个 API 最大股票数"""
        if not path.exists():
            return 1
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            value = int(data.get("max_stocks_per_api", 1))
            return max(1, value)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return 1
