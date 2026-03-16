# -*- coding: utf-8 -*-
"""
观澜量化 - 运行时引擎启动工具

统一桌面端和网页版的默认引擎初始化逻辑，避免多处重复维护。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_DEFAULT_ENGINES: tuple[tuple[str, str, str], ...] = (
    ("portfolio", "guanlan.core.trader.pnl", "PortfolioEngine"),
    ("PortfolioStrategy", "guanlan.core.trader.portfolio", "PortfolioStrategyEngine"),
    ("CtaStrategy", "guanlan.core.trader.cta", "CtaEngine"),
    ("ScriptTrader", "guanlan.core.trader.script", "ScriptEngine"),
    ("RiskManager", "guanlan.core.trader.risk", "RiskEngine"),
)


def ensure_default_engines(main_engine: Any) -> dict[str, Any]:
    """
    确保观澜默认业务引擎已完成注册。

    Parameters
    ----------
    main_engine:
        已初始化的 MainEngine 实例。

    Returns
    -------
    dict[str, Any]
        当前已注册的默认引擎映射。
    """
    registered = getattr(main_engine, "engines", {})

    for engine_name, module_name, class_name in _DEFAULT_ENGINES:
        if engine_name in registered:
            continue

        module = import_module(module_name)
        engine_class = getattr(module, class_name)
        main_engine.add_engine(engine_class)

    return {
        engine_name: getattr(main_engine, "engines", {}).get(engine_name)
        for engine_name, _, _ in _DEFAULT_ENGINES
    }

