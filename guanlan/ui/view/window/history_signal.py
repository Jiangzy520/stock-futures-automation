# -*- coding: utf-8 -*-
"""
量化 - 历史信号结果窗口

用于运行股票分时买点脚本的历史回放，并在软件中查看/导出结果。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QDate, QThread, QUrl
from PySide6.QtGui import QIcon, QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFileDialog,
)

from qfluentwidgets import (
    FluentWidget,
    PushButton, PrimaryPushButton,
    BodyLabel, SubtitleLabel,
    LineEdit, DateEdit, TextEdit,
    InfoBar, InfoBarPosition,
    isDarkTheme, qconfig,
)

from guanlan.ui.common.icon import get_icon_path
from guanlan.ui.common.mixin import CursorFixMixin
from guanlan.ui.common.style import StyleSheet, Theme
from guanlan.ui.view.panel.base import BaseMonitor
from tools.replay_stock_intraday_pattern import (
    DEFAULT_MANAGER_WATCHLIST_PATH,
    DEFAULT_OUTPUT_DIR,
    ReplayRunResult,
    load_symbols_from_watchlist,
    save_replay_result,
    run_replay,
)


class HistorySignalTable(BaseMonitor):
    """历史信号结果表。"""

    headers = {
        "key": {"display": "键", "width": 60},
        "symbol": {"display": "代码", "width": 80},
        "name": {"display": "名称", "width": 100},
        "trading_day": {"display": "交易日", "width": 100},
        "buy_time": {"display": "买入时间", "width": 90},
        "signal_time": {"display": "买入日期时间", "width": 130},
        "entry_price": {"display": "买入价", "format": ".3f", "width": 80},
        "right1_time": {"display": "右1时间", "width": 85},
        "right1_price": {"display": "右1价", "format": ".3f", "width": 75},
        "right2_time": {"display": "右2时间", "width": 85},
        "right2_price": {"display": "右2价", "format": ".3f", "width": 75},
        "left1_point_time": {"display": "左1时间", "width": 85},
        "left1_price": {"display": "左1价", "format": ".3f", "width": 75},
        "left2_time": {"display": "左2时间", "width": 85},
        "left2_price": {"display": "左2价", "format": ".3f", "width": 75},
        "signal_pct": {"display": "涨幅%", "format": ".2f", "width": 75},
        "pattern_type": {"display": "形态", "width": 90},
        "buy_type": {"display": "买点", "width": 70},
        "strength": {"display": "强度", "width": 60},
        "stop_loss": {"display": "止损", "format": ".3f", "width": 80},
        "trigger_level": {"display": "触发位", "format": ".3f", "width": 80},
        "left1_pct": {"display": "右1涨幅%", "format": ".2f", "width": 90},
        "reason": {"display": "原因", "align": "left", "width": 360},
    }
    data_key = "key"

    def _init_table(self) -> None:
        super()._init_table()
        self.hideColumn(0)


class ReplayWorker(QThread):
    """历史回放线程。"""

    progress = Signal(str)
    finished = Signal(object, object, object)
    failed = Signal(str)

    def __init__(
        self,
        symbols: list[str],
        start_date: datetime.date,
        end_date: datetime.date,
        config_override: dict | None,
        output_dir: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._symbols = symbols
        self._start_date = start_date
        self._end_date = end_date
        self._config_override = config_override
        self._output_dir = output_dir

    def run(self) -> None:
        try:
            result = run_replay(
                self._symbols,
                self._start_date,
                self._end_date,
                self._config_override,
                progress_callback=self.progress.emit,
            )
            json_path, csv_path = save_replay_result(result, self._output_dir)
            self.finished.emit(result, json_path, csv_path)
        except Exception as exc:
            import traceback
            self.failed.emit(f"{exc}\n{traceback.format_exc()}")


class HistorySignalResultWindow(CursorFixMixin, FluentWidget):
    """历史信号结果窗口。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._worker: ReplayWorker | None = None
        self._latest_result: ReplayRunResult | None = None
        self._latest_json_path: Path | None = None
        self._latest_csv_path: Path | None = None
        self._output_dir: Path = DEFAULT_OUTPUT_DIR

        self._init_ui()

    def _init_ui(self) -> None:
        self.setWindowTitle("历史信号结果")
        self.resize(1400, 860)
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

        content_layout = QVBoxLayout(self._content_widget)
        content_layout.setContentsMargins(16, 12, 16, 12)
        content_layout.setSpacing(8)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, self.titleBar.height(), 0, 0)
        layout.addWidget(self._content_widget)

        toolbar = QHBoxLayout()
        toolbar.addWidget(BodyLabel("股票代码", self))

        self._symbols_edit = LineEdit(self)
        self._symbols_edit.setPlaceholderText("留空则自动使用当前自选股文件；也可输入逗号分隔代码")
        self._symbols_edit.setText("")
        toolbar.addWidget(self._symbols_edit, 2)

        toolbar.addWidget(BodyLabel("开始日期", self))
        self._start_date = DateEdit(self)
        start_dt = datetime.now() - timedelta(days=5)
        self._start_date.setDate(QDate(start_dt.year, start_dt.month, start_dt.day))
        self._start_date.setDisplayFormat("yyyy-MM-dd")
        toolbar.addWidget(self._start_date)

        toolbar.addWidget(BodyLabel("结束日期", self))
        self._end_date = DateEdit(self)
        now = datetime.now()
        self._end_date.setDate(QDate(now.year, now.month, now.day))
        self._end_date.setDisplayFormat("yyyy-MM-dd")
        toolbar.addWidget(self._end_date)

        self._config_edit = LineEdit(self)
        self._config_edit.setPlaceholderText(
            "可选参数覆盖 JSON，例如 {\"enable_strategy_one\":false,\"enable_strategy_two\":true,\"strategy2_breakout_buffer_pct\":0}"
        )
        toolbar.addWidget(self._config_edit, 2)

        self._run_btn = PrimaryPushButton("开始回放", self)
        self._run_btn.clicked.connect(self._on_run)
        toolbar.addWidget(self._run_btn)

        self._export_json_btn = PushButton("导出 JSON", self)
        self._export_json_btn.clicked.connect(self._export_json)
        self._export_json_btn.setEnabled(False)
        toolbar.addWidget(self._export_json_btn)

        self._export_csv_btn = PushButton("导出 CSV", self)
        self._export_csv_btn.clicked.connect(self._export_csv)
        self._export_csv_btn.setEnabled(False)
        toolbar.addWidget(self._export_csv_btn)

        self._open_dir_btn = PushButton("打开目录", self)
        self._open_dir_btn.clicked.connect(self._open_output_dir)
        toolbar.addWidget(self._open_dir_btn)

        self._clear_btn = PushButton("清空结果", self)
        self._clear_btn.clicked.connect(self._clear)
        toolbar.addWidget(self._clear_btn)

        content_layout.addLayout(toolbar)

        grid = QGridLayout()

        left_widget = QWidget(self)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        left_layout.addWidget(SubtitleLabel("历史信号表", self))
        self._table = HistorySignalTable(self)
        left_layout.addWidget(self._table, 1)

        right_widget = QWidget(self)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(6)

        right_layout.addWidget(SubtitleLabel("回放摘要", self))
        self._summary_edit = TextEdit(self)
        self._summary_edit.setReadOnly(True)
        right_layout.addWidget(self._summary_edit, 1)

        right_layout.addWidget(SubtitleLabel("运行日志", self))
        self._log_edit = TextEdit(self)
        self._log_edit.setReadOnly(True)
        right_layout.addWidget(self._log_edit, 2)

        grid.addWidget(left_widget, 0, 0)
        grid.addWidget(right_widget, 0, 1)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)

        content_layout.addLayout(grid, 1)

        self._apply_content_style()
        qconfig.themeChanged.connect(self._apply_content_style)
        self._set_summary("尚未运行历史回放。")

    def _apply_content_style(self) -> None:
        theme = Theme.DARK if isDarkTheme() else Theme.LIGHT
        StyleSheet.apply(self._content_widget, ["common.qss", "window.qss"], theme)

    def _append_log(self, text: str) -> None:
        self._log_edit.append(text)

    def _set_summary(self, text: str) -> None:
        self._summary_edit.setPlainText(text)

    def _on_run(self) -> None:
        if self._worker and self._worker.isRunning():
            InfoBar.warning(
                "提示", "历史回放正在运行，请稍候",
                parent=self, position=InfoBarPosition.TOP, duration=2500,
            )
            return

        symbols = [item.strip() for item in self._symbols_edit.text().split(",") if item.strip()]
        if not symbols:
            symbols = load_symbols_from_watchlist(DEFAULT_MANAGER_WATCHLIST_PATH)
            if not symbols:
                InfoBar.warning(
                    "提示", "请输入股票代码，或先准备当前自选股文件",
                    parent=self, position=InfoBarPosition.TOP, duration=2500,
                )
                return

        start_date = self._start_date.date().toPython()
        end_date = self._end_date.date().toPython()
        if end_date < start_date:
            InfoBar.warning(
                "提示", "结束日期不能早于开始日期",
                parent=self, position=InfoBarPosition.TOP, duration=2500,
            )
            return

        config_override = None
        config_text = self._config_edit.text().strip()
        if config_text:
            try:
                config_override = json.loads(config_text)
            except Exception as exc:
                InfoBar.error(
                    "参数错误", f"配置 JSON 解析失败: {exc}",
                    parent=self, position=InfoBarPosition.TOP, duration=3500,
                )
                return

        self._table.clear_data()
        self._log_edit.clear()
        self._latest_result = None
        self._latest_json_path = None
        self._latest_csv_path = None
        self._export_json_btn.setEnabled(False)
        self._export_csv_btn.setEnabled(False)
        self._run_btn.setEnabled(False)
        self._set_summary("正在回放，请稍候...")
        source_text = "当前自选股文件" if not self._symbols_edit.text().strip() else "手工输入"
        self._append_log(f"开始回放: source={source_text} | symbols={len(symbols)} | {start_date} ~ {end_date}")

        self._worker = ReplayWorker(
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            config_override=config_override,
            output_dir=self._output_dir,
            parent=self,
        )
        self._worker.progress.connect(self._append_log)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_finished(self, result: ReplayRunResult, json_path: Path, csv_path: Path) -> None:
        self._worker = None
        self._run_btn.setEnabled(True)

        self._latest_result = result
        self._latest_json_path = Path(json_path)
        self._latest_csv_path = Path(csv_path)
        self._export_json_btn.setEnabled(True)
        self._export_csv_btn.setEnabled(True)

        rows = []
        for index, record in enumerate(result.signal_records):
            row = asdict(record)
            row["key"] = f"{record.symbol}_{record.signal_time}_{record.buy_type}_{index}"
            rows.append(row)
        self._table.process_batch(rows)

        day_count = len(result.day_results)
        zero_days = sum(1 for item in result.day_results if item.signal_count == 0)
        skipped = [item for item in result.day_results if item.skipped_reason]
        summary_lines = [
            f"生成时间: {result.generated_at}",
            f"数据源: {result.data_source}",
            f"缓存库: {result.cache_db}",
            f"API 池: 总数 {result.total_api_count} / 本次使用 {result.active_api_count} / 拉取错误 {result.fetch_error_count}",
            f"股票数量: {len(result.symbols)}",
            f"回放区间: {result.start_date} ~ {result.end_date}",
            f"交易日记录数: {day_count}",
            f"总信号数: {result.total_signals}",
            f"零信号交易日: {zero_days}",
            f"JSON: {self._latest_json_path}",
            f"CSV: {self._latest_csv_path}",
        ]
        if skipped:
            summary_lines.append("跳过记录:")
            summary_lines.extend(
                f"- {item.symbol} {item.trading_day}: {item.skipped_reason}" for item in skipped[:10]
            )
        self._set_summary("\n".join(summary_lines))
        self._append_log(f"导出完成: {self._latest_json_path}")
        self._append_log(f"导出完成: {self._latest_csv_path}")
        self._append_log(f"回放完成: total_signals={result.total_signals}")

        InfoBar.success(
            "完成",
            f"历史回放完成，共 {result.total_signals} 个信号",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=3000,
        )

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self._run_btn.setEnabled(True)
        self._append_log(message)
        self._set_summary("历史回放失败，请查看运行日志。")
        InfoBar.error(
            "失败",
            "历史回放执行失败，请查看运行日志",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=4000,
        )

    def _export_json(self) -> None:
        if not self._latest_result:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 JSON", "", "JSON (*.json)")
        if not path:
            return
        Path(path).write_text(
            json.dumps(asdict(self._latest_result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _export_csv(self) -> None:
        if not self._latest_result:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 CSV", "", "CSV (*.csv)")
        if not path:
            return
        headers = [
            "symbol", "name", "trading_day", "buy_time", "signal_time", "entry_price", "signal_pct",
            "right1_time", "right1_price", "right2_time", "right2_price",
            "left1_point_time", "left1_price", "left2_time", "left2_price",
            "pattern_type", "buy_type", "strength", "stop_loss", "invalidation", "trigger_level",
            "left1_pct", "left1_time", "right1_volume_ok", "session_vwap", "reason",
        ]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            import csv
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in self._latest_result.signal_records:
                writer.writerow(asdict(row))

    def _open_output_dir(self) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output_dir)))

    def _clear(self) -> None:
        self._table.clear_data()
        self._summary_edit.clear()
        self._log_edit.clear()
        self._latest_result = None
        self._latest_json_path = None
        self._latest_csv_path = None
        self._export_json_btn.setEnabled(False)
        self._export_csv_btn.setEnabled(False)
        self._set_summary("尚未运行历史回放。")
