# -*- coding: utf-8 -*-
"""
观澜量化 - AI 聊天面板

嵌入首页右侧，提供 AI 交易助手对话功能。
支持流式输出、Markdown 渲染、多模型切换。
每条消息为独立 Widget 气泡，外层 SmoothScrollArea 包裹。

Author: 海山观澜
"""

import asyncio
import csv
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Qt, QEvent, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPalette
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextBrowser, QFileDialog,
    QSizePolicy, QLabel,
)

from qfluentwidgets import (
    ComboBox, TextEdit, ToolButton, PrimaryToolButton, FluentIcon,
    InfoBar, InfoBarPosition, isDarkTheme, SmoothScrollArea,
)

from guanlan.core.constants import CONFIG_DIR
from guanlan.core.services.ai.prompts.market import TRADING_ASSISTANT_SYSTEM
from guanlan.core.utils.logger import get_logger


logger = get_logger("ai_chat")
ALLTICK_MANAGER_DIR = CONFIG_DIR / "alltick_manager"
ALLTICK_SIGNAL_CSV = CONFIG_DIR / "alltick" / "multi_token_variant_double_bottom_signals.csv"
AI_PUSH_DEBUG_LOG = CONFIG_DIR / "ai_push_debug.log"


class _MessageBubble(QWidget):
    """单条消息气泡 widget

    每条消息独立渲染，包含角色标签和内容区域。
    QTextBrowser 单条使用时透明背景生效，避免灰底问题。
    """

    def __init__(self, role: str, parent=None) -> None:
        """
        Args:
            role: 消息角色，"user" 或 "ai"
        """
        super().__init__(parent)
        self._role = role

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)

        # 角色标签
        self._label = QLabel("你" if role == "user" else "AI", self)
        self._label.setFixedHeight(16)

        # 内容区域
        self._browser = QTextBrowser(self)
        self._browser.setOpenExternalLinks(True)
        self._browser.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._browser.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        # 透明背景（单条 QTextBrowser 有效）
        self._browser.setFrameShape(QTextBrowser.Shape.NoFrame)
        palette = self._browser.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
        self._browser.setPalette(palette)

        # 对齐和气泡样式
        if role == "user":
            layout.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._label.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._browser.setMaximumWidth(500)
        else:
            layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
            self._label.setAlignment(Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(self._label)
        layout.addWidget(self._browser)

        self._apply_theme()

    def _apply_theme(self) -> None:
        """根据当前主题应用样式"""
        dark = isDarkTheme()

        if self._role == "user":
            label_color = "#7eb8f0" if dark else "#0066cc"
            bubble_bg = "#264f78" if dark else "#dcedfc"
            text_color = "#e0e0e0" if dark else "#1e1e1e"
        else:
            label_color = "#6dd4b8" if dark else "#008060"
            bubble_bg = "#2d2d2d" if dark else "#f0f0f0"
            text_color = "#d4d4d4" if dark else "#1e1e1e"

        code_bg = "#383838" if dark else "#f5f5f5"
        hr_color = "#333" if dark else "#e0e0e0"
        blockquote_border = "#555" if dark else "#ccc"

        self._label.setStyleSheet(
            f"QLabel {{ color: {label_color}; font-size: 11px; "
            f"background: transparent; }}"
        )

        self._browser.setStyleSheet(
            f"QTextBrowser {{"
            f"  background-color: {bubble_bg};"
            f"  color: {text_color};"
            f"  border-radius: 10px;"
            f"  padding: 8px;"
            f"  font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif;"
            f"  font-size: 13px;"
            f"}}"
        )

        self._browser.document().setDefaultStyleSheet(
            f"body {{ line-height: 1.6; }}"
            f"p {{ margin: 4px 0; }}"
            f"code {{ background-color: {code_bg}; padding: 1px 5px; "
            f"  border-radius: 3px; font-family: 'Consolas', 'Courier New', monospace; "
            f"  font-size: 12px; }}"
            f"pre {{ background-color: {code_bg}; padding: 10px; "
            f"  border-radius: 6px; overflow-x: auto; margin: 6px 0; }}"
            f"pre code {{ padding: 0; background: none; }}"
            f"table {{ border-collapse: collapse; margin: 8px 0; width: 100%; }}"
            f"th, td {{ border: 1px solid {hr_color}; padding: 5px 8px; text-align: left; }}"
            f"th {{ background-color: {code_bg}; }}"
            f"blockquote {{ border-left: 3px solid {blockquote_border}; "
            f"  margin: 8px 0; padding: 4px 12px; }}"
        )

    def set_content(self, text: str, is_html: bool = False) -> None:
        """设置消息内容

        Args:
            text: 消息文本（纯文本或 HTML）
            is_html: True 表示 HTML，False 表示 Markdown
        """
        if is_html:
            self._browser.setHtml(text)
        else:
            self._browser.setMarkdown(text)
        self._adjust_height()

    def _adjust_height(self) -> None:
        """根据文档内容自适应尺寸"""
        doc = self._browser.document()

        if self._role == "user":
            # 用户气泡：用 QFontMetrics 测量实际文本像素宽度
            # 自行构造字体，因为 QSS 字体在 widget 未显示时不生效
            font = QFont("Microsoft YaHei", 13)
            fm = QFontMetrics(font)
            # 多行取最宽行
            plain = doc.toPlainText()
            lines = plain.split("\n")
            text_px = max(fm.horizontalAdvance(line) for line in lines)
            # padding(8×2) + documentMargin(4×2) + 余量
            browser_w = min(text_px + 32, 500)
            browser_w = max(browser_w, 80)
            self._browser.setFixedWidth(browser_w)
            doc.setTextWidth(browser_w - 16)
        else:
            # AI 气泡：按视口宽度排版
            vw = self._browser.viewport().width()
            if vw <= 0:
                parent_w = self.parentWidget().width() if self.parentWidget() else 0
                vw = parent_w - 36 if parent_w > 0 else 560
            # 兜底宽度，避免 textWidth 未设置导致文档高度异常放大
            vw = max(vw, 280)
            doc.setTextWidth(vw)

        doc_height = doc.size().height()
        # QSS padding 上下各 8px，用户气泡需额外补偿
        pad = 20 if self._role == "user" else 20
        self._browser.setFixedHeight(max(int(doc_height) + pad, 36))


class _StreamWorker(QThread):
    """AI 流式对话工作线程"""

    chunk_received = Signal(str)
    stream_finished = Signal(str)
    stream_error = Signal(str)

    def __init__(
        self,
        message: str,
        model: str,
        history: list[dict],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._message = message
        self._model = model
        self._history = history

    def run(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._stream())
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    async def _stream(self) -> None:
        from guanlan.core.services.ai import get_ai_client

        full = ""
        try:
            ai = get_ai_client()
            async for text in ai.chat_stream(
                self._message,
                model=self._model or None,
                system_prompt=TRADING_ASSISTANT_SYSTEM,
                history=self._history,
            ):
                full += text
                self.chunk_received.emit(text)
            self.stream_finished.emit(full)
        except Exception as e:
            logger.error(f"AI 对话失败: {e}")
            self.stream_error.emit(str(e))


class AIChatPanel(QWidget):
    """AI 聊天面板

    嵌入首页右侧，提供交易助手对话功能。
    每条消息为独立 _MessageBubble widget，外层 SmoothScrollArea 包裹。
    支持流式输出、Markdown 渲染、多模型切换。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._history: list[dict] = []
        self._current_response: str = ""
        self._is_streaming: bool = False
        self._worker: _StreamWorker | None = None
        self._streaming_bubble: _MessageBubble | None = None
        self._push_timer: QTimer | None = None
        self._push_last_signal_key: str = ""
        self._push_initialized: bool = False
        self._push_error_notice_shown: bool = False

        self._init_ui()
        self._connect_signals()
        self._init_realtime_push()

    def _connect_signals(self) -> None:
        """连接信号"""
        from guanlan.core.events import signal_bus
        signal_bus.ai_models_changed.connect(self._reload_models)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # 工具栏
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)

        self._model_combo = ComboBox(self)
        self._model_combo.setFixedWidth(140)
        self._load_models()
        toolbar.addWidget(self._model_combo)

        toolbar.addStretch()

        clear_btn = ToolButton(FluentIcon.DELETE, self)
        clear_btn.setToolTip("清空对话")
        clear_btn.clicked.connect(self._clear_history)
        toolbar.addWidget(clear_btn)

        export_btn = ToolButton(FluentIcon.SAVE, self)
        export_btn.setToolTip("导出对话")
        export_btn.clicked.connect(self._export_chat)
        toolbar.addWidget(export_btn)

        layout.addLayout(toolbar)

        self._push_meta = QLabel("实时推送未连接", self)
        self._push_meta.setStyleSheet(
            "QLabel { color: #63d5ff; font-size: 13px; font-weight: 600; padding: 2px 2px 4px 2px; }"
        )
        layout.addWidget(self._push_meta)

        # 消息显示区：SmoothScrollArea + 消息容器
        self._scroll = SmoothScrollArea(self)
        self._scroll.setObjectName("chatScroll")
        self._msg_container = QWidget()
        self._msg_container.setObjectName("chatMsgContainer")
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(4, 4, 12, 4)
        self._msg_layout.setSpacing(4)
        self._msg_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(self._msg_container)
        self._scroll.setWidgetResizable(True)
        layout.addWidget(self._scroll, 1)

        # 输入区
        input_layout = QHBoxLayout()
        input_layout.setSpacing(6)

        self._input = TextEdit(self)
        self._input.setObjectName("chatInput")
        self._input.setPlaceholderText("输入问题，Enter 发送，Shift+Enter 换行")
        self._input.setMinimumHeight(60)
        self._input.setMaximumHeight(100)
        self._input.installEventFilter(self)
        input_layout.addWidget(self._input, 1)

        self._send_btn = PrimaryToolButton(FluentIcon.SEND, self)
        self._send_btn.setObjectName("chatSendBtn")
        self._send_btn.setToolTip("发送")
        self._send_btn.setFixedWidth(60)
        sp = self._send_btn.sizePolicy()
        sp.setVerticalPolicy(QSizePolicy.Policy.Expanding)
        self._send_btn.setSizePolicy(sp)
        self._send_btn.setMinimumHeight(60)
        self._send_btn.setMaximumHeight(100)
        self._send_btn.clicked.connect(self._on_send)
        input_layout.addWidget(self._send_btn)

        layout.addLayout(input_layout)

    def _load_models(self) -> None:
        """加载可用模型列表"""
        try:
            from guanlan.core.services.ai import get_ai_client
            ai = get_ai_client()
            models = ai.list_models()
            self._model_combo.addItems(models)

            default = ai.get_default_model()
            idx = self._model_combo.findText(default)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
        except Exception:
            self._model_combo.addItem("未配置")

    def _reload_models(self) -> None:
        """重新加载模型列表（响应配置变更）"""
        # 保存当前选中的模型
        current = self._model_combo.currentText()

        # 清空并重新加载
        self._model_combo.clear()
        try:
            from guanlan.core.services.ai import get_ai_client, reset_ai_client
            # 重置 AI 客户端以重新加载配置
            reset_ai_client()
            ai = get_ai_client()
            models = ai.list_models()
            self._model_combo.addItems(models)

            # 尝试恢复之前的选择，如果不存在则使用默认模型
            idx = self._model_combo.findText(current)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
            else:
                default = ai.get_default_model()
                idx = self._model_combo.findText(default)
                if idx >= 0:
                    self._model_combo.setCurrentIndex(idx)
        except Exception:
            self._model_combo.addItem("未配置")

    # ── 事件过滤（Enter 发送） ──

    def eventFilter(self, obj, event) -> bool:
        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not shift:
                self._on_send()
                return True
        return super().eventFilter(obj, event)

    # ── 发送消息 ──

    def _on_send(self) -> None:
        """发送消息"""
        if self._is_streaming:
            return

        text = self._input.toPlainText().strip()
        if not text:
            return

        model = self._model_combo.currentText()
        if model == "未配置":
            InfoBar.error(
                "AI 未配置", "请先配置 AI 服务的 API Key",
                parent=self.window(), position=InfoBarPosition.TOP,
            )
            return

        self._input.clear()

        # 添加用户消息气泡
        user_bubble = _MessageBubble("user", self._msg_container)
        escaped = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )
        user_bubble.set_content(escaped, is_html=True)
        self._msg_layout.addWidget(user_bubble)

        # 创建 AI 流式占位气泡
        self._streaming_bubble = _MessageBubble("ai", self._msg_container)
        self._streaming_bubble.set_content("▌", is_html=True)
        self._msg_layout.addWidget(self._streaming_bubble)
        self._scroll_to_bottom()

        # 开始流式请求
        self._is_streaming = True
        self._current_response = ""
        self._send_btn.setEnabled(False)
        self._send_btn.setIcon(FluentIcon.SYNC)

        self._worker = _StreamWorker(text, model, self._history, self)
        self._worker.chunk_received.connect(self._on_chunk)
        self._worker.stream_finished.connect(
            lambda resp, msg=text: self._on_finished(msg, resp)
        )
        self._worker.stream_error.connect(self._on_error)
        self._worker.start()

    def _on_chunk(self, chunk: str) -> None:
        """收到流式片段"""
        self._current_response += chunk
        if self._streaming_bubble:
            self._streaming_bubble.set_content(
                self._current_response + "▌"
            )
            self._scroll_to_bottom()

    def _on_finished(self, user_msg: str, full_response: str) -> None:
        """流式完成"""
        self._history.append({"role": "user", "content": user_msg})
        self._history.append({"role": "assistant", "content": full_response})

        if self._streaming_bubble:
            self._streaming_bubble.set_content(full_response)
            self._streaming_bubble = None

        self._current_response = ""
        self._is_streaming = False
        self._send_btn.setEnabled(True)
        self._send_btn.setIcon(FluentIcon.SEND)
        self._worker = None
        self._scroll_to_bottom()

    def _on_error(self, error_msg: str) -> None:
        """流式错误"""
        # 移除流式占位气泡
        if self._streaming_bubble:
            self._msg_layout.removeWidget(self._streaming_bubble)
            self._streaming_bubble.deleteLater()
            self._streaming_bubble = None

        self._is_streaming = False
        self._send_btn.setEnabled(True)
        self._send_btn.setIcon(FluentIcon.SEND)
        self._current_response = ""
        self._worker = None

        InfoBar.error(
            "AI 对话失败", error_msg,
            parent=self.window(), position=InfoBarPosition.TOP,
            duration=5000,
        )

    # ── 滚动控制 ──

    def _scroll_to_bottom(self) -> None:
        """滚动到底部"""
        QTimer.singleShot(10, self._do_scroll_to_bottom)

    def _do_scroll_to_bottom(self) -> None:
        scrollbar = self._scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ── 工具栏操作 ──

    def _clear_history(self) -> None:
        """清空对话历史"""
        if self._is_streaming:
            return
        self._history.clear()
        self._current_response = ""
        # 移除所有消息气泡
        while self._msg_layout.count():
            item = self._msg_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        # 清空后重置推送游标并立即回填最近信号
        self._push_initialized = False
        self._push_last_signal_key = ""
        self._refresh_realtime_push()

    def _init_realtime_push(self) -> None:
        """初始化实时信号推送（每 2 秒轮询本地扫描结果文件）"""
        self._debug_push("init realtime push")
        self._append_ai_text("实时推送已启动，正在读取策略信号...")
        self._push_timer = QTimer(self)
        self._push_timer.setInterval(2000)
        self._push_timer.timeout.connect(self._refresh_realtime_push)
        self._push_timer.start()
        self._refresh_realtime_push()

    def _refresh_realtime_push(self) -> None:
        """将实时扫描信号推送到 AI 聊天窗口"""
        payload = self._collect_realtime_payload(limit=0)
        if not payload:
            self._set_push_meta("实时推送未连接")
            self._debug_push("payload none")
            if not self._push_error_notice_shown:
                self._append_ai_text("实时推送未连接：未读取到策略结果文件，请检查扫描脚本是否正在输出信号。")
                self._push_error_notice_shown = True
            return
        self._push_error_notice_shown = False

        self._set_push_meta(
            f"推送中 自选:{payload.get('watchlist_count', 0)}"
            f" API总:{payload.get('api_total_count', 0)}"
            f" 已分配API:{payload.get('assigned_api_count', 0)}"
            f" 信号:{payload.get('signal_total_count', 0)}"
            f" 更新:{payload.get('updated_at', '--')}"
        )
        self._debug_push(
            "refresh ok "
            f"msg_count={self._msg_layout.count()} "
            f"signal_total={payload.get('signal_total_count', 0)} "
            f"watch={payload.get('watchlist_count', 0)}"
        )

        rows = payload.get("signals", [])
        push_time = payload.get("updated_at", "--")
        if not self._push_initialized:
            self._push_initialized = True
            if not rows:
                self._append_ai_text("实时推送已连接，当前暂无策略信号。")
                return

            warm_rows = rows
            self._append_ai_text(
                f"实时推送已连接，当前累计信号 {payload.get('signal_total_count', len(rows))} 条，已全量展示 {len(warm_rows)} 条。"
            )
            for row in warm_rows:
                self._append_ai_text(self._format_signal_text(row, push_time))
            self._push_last_signal_key = self._signal_key(warm_rows[-1])
            return

        # 若聊天区为空（例如用户手动清空），自动回填最近信号，避免只有状态行没有内容
        if self._msg_layout.count() == 0:
            if not rows:
                self._append_ai_text("实时推送已连接，当前暂无策略信号。")
                self._push_last_signal_key = ""
                return
            warm_rows = rows
            self._append_ai_text(
                f"实时推送回填：当前累计信号 {payload.get('signal_total_count', len(rows))} 条，已全量展示 {len(warm_rows)} 条。"
            )
            for row in warm_rows:
                self._append_ai_text(self._format_signal_text(row, push_time))
            self._push_last_signal_key = self._signal_key(warm_rows[-1])
            return

        if not rows:
            return

        last_key = self._push_last_signal_key
        if not last_key:
            new_rows = rows
        else:
            idx = -1
            for i, row in enumerate(rows):
                if self._signal_key(row) == last_key:
                    idx = i
                    break
            if idx >= 0:
                new_rows = rows[idx + 1:]
            else:
                new_rows = rows

        if not new_rows:
            return

        for row in new_rows:
            self._append_ai_text(self._format_signal_text(row, push_time))
        self._push_last_signal_key = self._signal_key(new_rows[-1])

    def _set_push_meta(self, text: str) -> None:
        if hasattr(self, "_push_meta") and self._push_meta:
            self._push_meta.setText(text)

    def _append_ai_text(self, text: str) -> None:
        label = QLabel(str(text), self._msg_container)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if isDarkTheme():
            label.setStyleSheet(
                "QLabel {"
                " color: #e6e6e6;"
                " background-color: rgba(255, 255, 255, 0.06);"
                " border: 1px solid rgba(255, 255, 255, 0.08);"
                " border-radius: 8px;"
                " padding: 6px 8px;"
                "}"
            )
        else:
            label.setStyleSheet(
                "QLabel {"
                " color: #1f1f1f;"
                " background-color: #f4f6f8;"
                " border: 1px solid #e0e4e8;"
                " border-radius: 8px;"
                " padding: 6px 8px;"
                "}"
            )
        self._msg_layout.addWidget(label)
        self._scroll_to_bottom()

    @staticmethod
    def _signal_key(row: dict) -> str:
        return (
            str(row.get("key", "")).strip()
            or f"{row.get('symbol', '')}|{row.get('trading_day', '')}|{row.get('signal_time', '')}"
        )

    @staticmethod
    def _format_signal_text(row: dict, push_time: str = "--") -> str:
        symbol = str(row.get("symbol", "")).strip() or "-"
        name = str(row.get("name", "")).strip()
        buy_time = AIChatPanel._to_hms(row.get("signal_time", ""))
        buy_price = str(row.get("signal_price", "")).strip() or "-"
        l1 = f"{AIChatPanel._to_hms(row.get('l1_time', '--'))}@{row.get('l1_price', '--')}"
        l2 = f"{AIChatPanel._to_hms(row.get('l2_time', '--'))}@{row.get('l2_price', '--')}"
        r1 = f"{AIChatPanel._to_hms(row.get('r1_time', '--'))}@{row.get('r1_price', '--')}"
        r2 = f"{AIChatPanel._to_hms(row.get('r2_time', '--'))}@{row.get('r2_price', '--')}"
        return (
            f"【推送:{AIChatPanel._to_hms(push_time)}】{symbol} {name} | 买入:{buy_time} @ {buy_price} | "
            f"左1:{l1} 左2:{l2} | 右1:{r1} 右2:{r2}"
        )

    @staticmethod
    def _to_hms(value: object) -> str:
        """规范化时间显示为 HH:MM:SS。"""
        text = str(value or "").strip()
        if not text or text in {"-", "--"}:
            return "--:--:--"
        # 兼容 "2026-03-16 09:55:39.000" / "09:55:39" / "09:55"
        token = text.split()[-1]
        token = token.split(".")[0]
        if len(token) >= 8 and token[2] == ":" and token[5] == ":":
            return token[:8]
        if len(token) == 5 and token[2] == ":":
            return f"{token}:00"
        return token

    def _collect_realtime_payload(self, limit: int = 300) -> dict | None:
        try:
            signal_total, signals = self._load_realtime_signals(ALLTICK_SIGNAL_CSV, limit=limit)
            watchlist_count = len(self._read_csv_rows(ALLTICK_MANAGER_DIR / "watchlist.csv"))
            api_total_count = len(set(self._read_non_empty_lines(ALLTICK_MANAGER_DIR / "apis.txt")))
            assigned_symbol_count, assigned_api_count = self._load_assignment_counts(
                ALLTICK_MANAGER_DIR / "stock_assignments.csv"
            )
            return {
                "watchlist_count": watchlist_count,
                "api_total_count": api_total_count,
                "assigned_symbol_count": assigned_symbol_count,
                "assigned_api_count": assigned_api_count,
                "api_remaining_count": max(0, api_total_count - assigned_api_count),
                "signal_total_count": signal_total,
                "signals": signals,
                "updated_at": datetime.now().strftime("%H:%M:%S"),
            }
        except Exception as e:
            logger.error(f"刷新实时推送失败: {e}")
            self._debug_push(f"collect payload failed: {e!r}")
            return None

    def _debug_push(self, text: str) -> None:
        """写入本地推送调试日志，便于定位界面未显示问题。"""
        try:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            AI_PUSH_DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
            with AI_PUSH_DEBUG_LOG.open("a", encoding="utf-8") as f:
                f.write(f"[{stamp}] {text}\n")
        except Exception:
            pass

    @staticmethod
    def _load_realtime_signals(path: Path, limit: int = 300) -> tuple[int, list[dict]]:
        rows = AIChatPanel._read_csv_rows(path)
        total = len(rows)
        if not rows:
            return 0, []

        result: list[dict] = []
        selected_rows = rows if limit <= 0 else rows[-limit:]
        for row in selected_rows:
            item = {
                "symbol": row.get("symbol", ""),
                "name": row.get("name", ""),
                "trading_day": row.get("trading_day", ""),
                "signal_time": row.get("signal_time", ""),
                "signal_price": row.get("signal_price", ""),
                "r1_time": row.get("r1_time", ""),
                "r1_price": row.get("r1_price", ""),
                "r2_time": row.get("r2_time", ""),
                "r2_price": row.get("r2_price", ""),
                "l1_time": row.get("l1_time", ""),
                "l1_price": row.get("l1_price", ""),
                "l2_time": row.get("l2_time", ""),
                "l2_price": row.get("l2_price", ""),
            }
            item["key"] = row.get("key", "") or AIChatPanel._signal_key(item)
            result.append(item)
        return total, result

    @staticmethod
    def _load_assignment_counts(path: Path) -> tuple[int, int]:
        rows = AIChatPanel._read_csv_rows(path)
        assigned_symbols = 0
        assigned_apis: set[str] = set()
        for row in rows:
            api = str(row.get("api", "")).strip()
            symbol = str(row.get("symbol", "")).strip()
            if symbol and api:
                assigned_symbols += 1
            if api:
                assigned_apis.add(api)
        return assigned_symbols, len(assigned_apis)

    @staticmethod
    def _read_non_empty_lines(path: Path) -> list[str]:
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
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                rows: list[dict[str, str]] = []
                for row in reader:
                    if not row:
                        continue
                    rows.append({k: (v or "").strip() for k, v in row.items()})
                return rows
        except OSError:
            return []

    def _export_chat(self) -> None:
        """导出对话为 Markdown 文件"""
        if not self._history:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "导出对话", "ai_chat.md", "Markdown (*.md)",
        )
        if not path:
            return

        lines = ["# AI 对话记录\n"]
        for msg in self._history:
            role = "你" if msg["role"] == "user" else "AI"
            lines.append(f"### {role}\n")
            lines.append(msg["content"])
            lines.append("\n---\n")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        InfoBar.success(
            "导出成功", f"已保存到 {path}",
            parent=self.window(), position=InfoBarPosition.TOP,
        )

    def cleanup(self) -> None:
        """清理资源（窗口关闭时调用）"""
        if self._push_timer and self._push_timer.isActive():
            self._push_timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.wait(3000)
