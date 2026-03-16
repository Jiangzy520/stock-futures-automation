# -*- coding: utf-8 -*-
"""
量化 - 脚本纸面交易窗口

展示脚本策略的股票纸面交易结果，包括：
1. 模拟账户汇总
2. 当前持仓
3. 成交记录
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout

from qfluentwidgets import (
    FluentWidget,
    PushButton,
    BodyLabel,
    SubtitleLabel,
    InfoBar,
    InfoBarPosition,
    isDarkTheme,
    qconfig,
)

from guanlan.core.trader.script import EVENT_SCRIPT_PAPER
from guanlan.ui.common.icon import get_icon_path
from guanlan.ui.common.mixin import CursorFixMixin
from guanlan.ui.common.style import StyleSheet, Theme
from guanlan.ui.view.panel.base import BaseMonitor


class PaperAccountTable(BaseMonitor):
    """模拟账户汇总表。"""

    headers = {
        "script_name": {"display": "脚本", "width": 140},
        "initial_cash": {"display": "初始资金", "format": ".2f", "width": 100},
        "cash": {"display": "可用现金", "format": ".2f", "width": 100},
        "position_value": {"display": "持仓市值", "format": ".2f", "width": 100},
        "equity": {"display": "总权益", "format": ".2f", "width": 100},
        "realized_pnl": {"display": "已实现", "format": ".2f", "color": "pnl", "width": 90},
        "unrealized_pnl": {"display": "浮动盈亏", "format": ".2f", "color": "pnl", "width": 90},
        "position_count": {"display": "持仓数", "format": "int", "width": 70},
        "total_trades": {"display": "成交数", "format": "int", "width": 70},
        "win_rate": {"display": "胜率", "width": 70},
        "last_update": {"display": "更新时间", "width": 140},
    }
    data_key = "script_name"


class PaperPositionTable(BaseMonitor):
    """模拟持仓表。"""

    headers = {
        "key": {"display": "键", "width": 60},
        "script_name": {"display": "脚本", "width": 120},
        "symbol": {"display": "代码", "width": 80},
        "name": {"display": "名称", "width": 100},
        "trading_day": {"display": "交易日", "width": 95},
        "volume": {"display": "持仓", "format": "int", "width": 70},
        "entry_count": {"display": "建仓次数", "format": "int", "width": 80},
        "avg_price": {"display": "持仓均价", "format": ".3f", "width": 80},
        "last_price": {"display": "最新价", "format": ".3f", "width": 80},
        "market_value": {"display": "市值", "format": ".2f", "width": 90},
        "unrealized_pnl": {"display": "浮盈", "format": ".2f", "color": "pnl", "width": 90},
        "unrealized_pct": {"display": "浮盈%", "format": ".2f", "color": "pnl", "width": 80},
        "stop_loss": {"display": "止损位", "format": ".3f", "width": 80},
        "invalidation": {"display": "失效位", "format": ".3f", "width": 80},
        "last_signal": {"display": "信号", "width": 90},
        "last_reason": {"display": "原因", "align": "left", "width": 260},
        "update_time": {"display": "更新时间", "width": 140},
    }
    data_key = "key"

    def _init_table(self) -> None:
        super()._init_table()
        self.hideColumn(0)


class PaperTradeTable(BaseMonitor):
    """模拟成交表。"""

    headers = {
        "trade_id": {"display": "成交号", "width": 120},
        "script_name": {"display": "脚本", "width": 120},
        "trade_time": {"display": "时间", "width": 140},
        "symbol": {"display": "代码", "width": 80},
        "name": {"display": "名称", "width": 100},
        "direction": {"display": "方向", "width": 60},
        "offset": {"display": "开平", "width": 60},
        "price": {"display": "成交价", "format": ".3f", "width": 80},
        "volume": {"display": "数量", "format": "int", "width": 70},
        "amount": {"display": "金额", "format": ".2f", "width": 90},
        "pnl": {"display": "盈亏", "format": ".2f", "color": "pnl", "width": 90},
        "pnl_pct": {"display": "盈亏%", "format": ".2f", "color": "pnl", "width": 80},
        "remaining_volume": {"display": "剩余仓位", "format": "int", "width": 80},
        "pattern_type": {"display": "形态", "width": 90},
        "buy_type": {"display": "买点", "width": 80},
        "reason": {"display": "原因", "align": "left", "width": 300},
    }
    data_key = "trade_id"


class ScriptPaperWindow(CursorFixMixin, FluentWidget):
    """脚本纸面交易窗口。"""

    _signal_paper = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._init_ui()
        self._register_events()
        self._refresh_snapshot()

    def _get_script_engine(self):
        from guanlan.core.app import AppEngine

        return AppEngine.instance().main_engine.get_engine("ScriptTrader")

    def _init_ui(self) -> None:
        self.setWindowTitle("模拟持仓")
        self.resize(1480, 920)
        self.setResizeEnabled(False)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        self.titleBar.setFixedHeight(48)
        self.titleBar.vBoxLayout.insertStretch(0, 1)
        self.titleBar.minBtn.hide()
        self.titleBar.maxBtn.hide()
        self.titleBar.closeBtn.show()

        icon_path = get_icon_path()
        if icon_path:
            icon = QIcon(icon_path)
            self.setWindowIcon(icon)
            self.titleBar.setIcon(icon)

        self._content_widget = QWidget(self)
        self._content_widget.setObjectName("dialogContent")

        content_layout = QVBoxLayout(self._content_widget)
        content_layout.setContentsMargins(16, 12, 16, 12)
        content_layout.setSpacing(8)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, self.titleBar.height(), 0, 0)
        layout.addWidget(self._content_widget)

        toolbar = QHBoxLayout()

        self._status_label = BodyLabel("暂无模拟交易数据", self)
        toolbar.addWidget(self._status_label)
        toolbar.addStretch(1)

        btn_refresh = PushButton("刷新", self)
        btn_refresh.clicked.connect(self._refresh_snapshot)
        toolbar.addWidget(btn_refresh)

        btn_flatten = PushButton("全部平仓", self)
        btn_flatten.clicked.connect(self._flatten_all)
        toolbar.addWidget(btn_flatten)

        btn_clear = PushButton("清空记录", self)
        btn_clear.clicked.connect(self._clear_all)
        toolbar.addWidget(btn_clear)

        content_layout.addLayout(toolbar)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        account_widget = QWidget(self)
        account_layout = QVBoxLayout(account_widget)
        account_layout.setContentsMargins(0, 0, 0, 0)
        account_layout.setSpacing(4)
        account_layout.addWidget(SubtitleLabel("模拟账户", self))
        self._account_table = PaperAccountTable(self)
        account_layout.addWidget(self._account_table)
        grid.addWidget(account_widget, 0, 0, 1, 2)

        position_widget = QWidget(self)
        position_layout = QVBoxLayout(position_widget)
        position_layout.setContentsMargins(0, 0, 0, 0)
        position_layout.setSpacing(4)
        position_layout.addWidget(SubtitleLabel("当前持仓", self))
        self._position_table = PaperPositionTable(self)
        position_layout.addWidget(self._position_table)
        grid.addWidget(position_widget, 1, 0)

        trade_widget = QWidget(self)
        trade_layout = QVBoxLayout(trade_widget)
        trade_layout.setContentsMargins(0, 0, 0, 0)
        trade_layout.setSpacing(4)
        trade_layout.addWidget(SubtitleLabel("成交记录", self))
        self._trade_table = PaperTradeTable(self)
        trade_layout.addWidget(self._trade_table)
        grid.addWidget(trade_widget, 1, 1)

        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 3)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        content_layout.addLayout(grid, 1)

        self._apply_content_style()
        qconfig.themeChanged.connect(self._apply_content_style)

    def _apply_content_style(self) -> None:
        theme = Theme.DARK if isDarkTheme() else Theme.LIGHT
        StyleSheet.apply(self._content_widget, ["common.qss", "window.qss"], theme)

    def _register_events(self) -> None:
        from guanlan.core.app import AppEngine

        event_engine = AppEngine.instance().event_engine
        self._signal_paper.connect(self._process_paper_event)
        self._paper_handler = self._signal_paper.emit
        event_engine.register(EVENT_SCRIPT_PAPER, self._paper_handler)

    def _unregister_events(self) -> None:
        try:
            from guanlan.core.app import AppEngine

            event_engine = AppEngine.instance().event_engine
            event_engine.unregister(EVENT_SCRIPT_PAPER, self._paper_handler)
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        self._unregister_events()
        super().closeEvent(event)

    def _process_paper_event(self, event) -> None:
        data = event.data if hasattr(event, "data") else event
        if isinstance(data, dict):
            self._apply_snapshot(data.get("snapshot", {}))

    def _refresh_snapshot(self) -> None:
        engine = self._get_script_engine()
        if not engine:
            return
        self._apply_snapshot(engine.get_paper_snapshot())

    def _flatten_all(self) -> None:
        engine = self._get_script_engine()
        if not engine:
            return

        result = engine.close_all_paper_positions(reason="界面手动全部平仓")
        self._refresh_snapshot()
        InfoBar.success(
            "模拟交易",
            result.get("message", "已执行全部平仓"),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=2500,
        )

    def _clear_all(self) -> None:
        engine = self._get_script_engine()
        if not engine:
            return

        result = engine.clear_paper_data()
        self._refresh_snapshot()
        InfoBar.success(
            "模拟交易",
            result.get("message", "已清空记录"),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=2500,
        )

    def _apply_snapshot(self, snapshot: dict) -> None:
        accounts = snapshot.get("accounts", [])
        positions = snapshot.get("positions", [])
        trades = snapshot.get("trades", [])
        generated_at = snapshot.get("generated_at", "")

        self._account_table.clear_data()
        self._position_table.clear_data()
        self._trade_table.clear_data()

        if accounts:
            self._account_table.process_batch(accounts)
        if positions:
            self._position_table.process_batch(positions)
        if trades:
            self._trade_table.process_batch(trades)

        self._status_label.setText(
            f"账户 {len(accounts)} 个 | 持仓 {len(positions)} 条 | 成交 {len(trades)} 条 | 更新时间 {generated_at or '-'}"
        )
