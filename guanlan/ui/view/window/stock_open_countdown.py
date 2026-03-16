# -*- coding: utf-8 -*-
"""
量化 - 股票开启倒计时窗口

显示 A 股交易时段的实时状态与下一关键时间节点倒计时。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone, date

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget, QVBoxLayout, QGridLayout

from qfluentwidgets import (
    FluentWidget,
    BodyLabel,
    SubtitleLabel,
    StrongBodyLabel,
    isDarkTheme,
    qconfig,
)

from guanlan.ui.common.icon import get_icon_path
from guanlan.ui.common.mixin import CursorFixMixin
from guanlan.ui.common.style import StyleSheet, Theme


class StockOpenCountdownWindow(CursorFixMixin, FluentWidget):
    """股票开启倒计时窗口。"""

    _BJT = timezone(timedelta(hours=8))
    _MORNING_OPEN = time(9, 30)
    _MORNING_CLOSE = time(11, 30)
    _AFTERNOON_OPEN = time(13, 0)
    _AFTERNOON_CLOSE = time(15, 0)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)

        self._init_ui()
        self._refresh()
        self._timer.start()

    def _init_ui(self) -> None:
        self.setWindowTitle("股票开启倒计时")
        self.resize(760, 420)
        self.setResizeEnabled(False)

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

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, self.titleBar.height(), 0, 0)
        root_layout.addWidget(self._content_widget)

        content_layout = QVBoxLayout(self._content_widget)
        content_layout.setContentsMargins(20, 16, 20, 16)
        content_layout.setSpacing(12)

        self._state_label = SubtitleLabel("交易状态：--", self)
        content_layout.addWidget(self._state_label)

        self._countdown_label = StrongBodyLabel("--:--:--", self)
        self._countdown_label.setStyleSheet("font-size: 34px;")
        content_layout.addWidget(self._countdown_label)

        self._target_label = BodyLabel("下一节点：--", self)
        content_layout.addWidget(self._target_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)

        self._now_value = BodyLabel("--", self)
        self._session_value = BodyLabel("09:30-11:30，13:00-15:00（工作日）", self)
        self._note_value = BodyLabel("说明：倒计时自动按北京时间刷新", self)

        grid.addWidget(BodyLabel("当前时间", self), 0, 0)
        grid.addWidget(self._now_value, 0, 1)
        grid.addWidget(BodyLabel("交易时段", self), 1, 0)
        grid.addWidget(self._session_value, 1, 1)
        grid.addWidget(BodyLabel("备注", self), 2, 0)
        grid.addWidget(self._note_value, 2, 1)

        content_layout.addLayout(grid)
        content_layout.addStretch(1)

        self._apply_content_style()
        qconfig.themeChanged.connect(self._apply_content_style)

    def _apply_content_style(self) -> None:
        theme = Theme.DARK if isDarkTheme() else Theme.LIGHT
        StyleSheet.apply(self._content_widget, ["common.qss", "window.qss"], theme)

    def _refresh(self) -> None:
        now = datetime.now(self._BJT)
        state, target_dt, target_desc = self._resolve_target(now)
        remain = max(target_dt - now, timedelta(0))

        self._state_label.setText(f"交易状态：{state}")
        self._countdown_label.setText(self._format_timedelta(remain))
        self._target_label.setText(f"下一节点：{target_desc}（{target_dt:%Y-%m-%d %H:%M:%S}）")
        self._now_value.setText(f"{now:%Y-%m-%d %H:%M:%S}")

    def _resolve_target(self, now: datetime) -> tuple[str, datetime, str]:
        if now.weekday() >= 5:
            next_day = self._next_trading_day(now.date())
            return (
                "休市（周末）",
                datetime.combine(next_day, self._MORNING_OPEN, tzinfo=self._BJT),
                "下个交易日开盘",
            )

        today = now.date()
        t = now.time()

        if t < self._MORNING_OPEN:
            return (
                "开盘前",
                datetime.combine(today, self._MORNING_OPEN, tzinfo=self._BJT),
                "上午开盘",
            )

        if self._MORNING_OPEN <= t < self._MORNING_CLOSE:
            return (
                "上午交易中",
                datetime.combine(today, self._MORNING_CLOSE, tzinfo=self._BJT),
                "上午收盘",
            )

        if self._MORNING_CLOSE <= t < self._AFTERNOON_OPEN:
            return (
                "午间休市",
                datetime.combine(today, self._AFTERNOON_OPEN, tzinfo=self._BJT),
                "下午开盘",
            )

        if self._AFTERNOON_OPEN <= t < self._AFTERNOON_CLOSE:
            return (
                "下午交易中",
                datetime.combine(today, self._AFTERNOON_CLOSE, tzinfo=self._BJT),
                "收盘",
            )

        next_day = self._next_trading_day(today)
        return (
            "已收盘",
            datetime.combine(next_day, self._MORNING_OPEN, tzinfo=self._BJT),
            "下个交易日开盘",
        )

    @staticmethod
    def _next_trading_day(base_day: date) -> date:
        day = base_day + timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        return day

    @staticmethod
    def _format_timedelta(delta: timedelta) -> str:
        seconds = int(delta.total_seconds())
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, secs = divmod(rem, 60)

        if days > 0:
            return f"{days}天 {hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)
