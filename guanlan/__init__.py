# -*- coding: utf-8 -*-
"""
观澜量化交易平台

Author: 海山观澜
"""

__version__ = "2.0.0"
__author__ = "海山观澜"

# 导出核心模块（便于使用）
# 注意：服务器部署的 web 入口允许在未安装 vnpy 时先以 mock 模式启动，
# 因此这里避免强依赖导入失败直接中断整个包初始化。
try:
    from guanlan.core import constants
except Exception:
    constants = None

try:
    from guanlan.core.events import signal_bus
except Exception:
    signal_bus = None

__all__ = ['constants', 'signal_bus']
