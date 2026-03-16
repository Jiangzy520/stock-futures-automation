# -*- coding: utf-8 -*-
"""
观澜量化 - 公共行情配置

用于在没有 CTP 行情账户时，接入公开市场数据源。
当前第一版默认接入 Yahoo Finance，可覆盖美股、港股、国际期货。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from guanlan.core.utils.common import load_json_file, save_json_file


SETTING_FILENAME: str = "config/public_market_data.json"
DEFAULT_GATEWAY_NAME: str = "公共行情"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "gateway_name": DEFAULT_GATEWAY_NAME,
    "provider": "yahoo",
    "poll_interval_seconds": 10,
    "symbols": [
        {
            "symbol": "AAPL",
            "exchange": "NASDAQ",
            "name": "Apple",
            "provider_symbol": "AAPL",
            "product": "EQUITY",
            "pricetick": 0.01,
            "size": 1,
            "min_volume": 1,
        },
        {
            "symbol": "0700",
            "exchange": "SEHK",
            "name": "腾讯控股",
            "provider_symbol": "0700.HK",
            "product": "EQUITY",
            "pricetick": 0.01,
            "size": 1,
            "min_volume": 100,
        },
        {
            "symbol": "ES",
            "exchange": "CME",
            "name": "E-mini S&P 500",
            "provider_symbol": "ES=F",
            "product": "FUTURES",
            "pricetick": 0.25,
            "size": 50,
            "min_volume": 1,
        },
    ],
}


def load_config() -> dict[str, Any]:
    """加载公共行情配置，不存在时自动写入默认示例。"""
    config = load_json_file(SETTING_FILENAME)
    if not config:
        config = deepcopy(DEFAULT_CONFIG)
        save_json_file(SETTING_FILENAME, config)
        return config

    merged = deepcopy(DEFAULT_CONFIG)
    merged.update(config)
    merged["symbols"] = config.get("symbols", deepcopy(DEFAULT_CONFIG["symbols"]))
    return merged


def is_enabled(config: dict[str, Any] | None = None) -> bool:
    """是否启用公共行情。"""
    if config is None:
        config = load_config()
    return bool(config.get("enabled", False))


def to_gateway_setting(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """转换为网关 connect() 所需设置。"""
    if config is None:
        config = load_config()

    return {
        "provider": str(config.get("provider", "yahoo")).strip().lower() or "yahoo",
        "poll_interval_seconds": int(config.get("poll_interval_seconds", 10) or 10),
        "symbols": list(config.get("symbols", [])),
    }
