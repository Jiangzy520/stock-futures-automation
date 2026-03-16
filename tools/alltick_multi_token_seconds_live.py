#!/usr/bin/env python3
"""
Multi-token AllTick live tick collector with second-bar persistence.

Features:
1. Uses multiple AllTick tokens in parallel.
2. Persists raw ticks into SQLite.
3. Builds 1-second / 5-second bars locally and persists them into SQLite.
4. Runs the existing variant double bottom second-level scanner.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import queue
import sqlite3
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

import websocket

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.alltick_variant_double_bottom_core import (  # noqa: E402
    ALLTICK_WS_URL,
    CHINA_TZ,
    DEFAULT_CONFIG as CORE_DEFAULT_CONFIG,
    DEFAULT_DB_PATH as CORE_DEFAULT_TICK_DB_PATH,
    DEFAULT_SIGNAL_PATH as CORE_DEFAULT_SIGNAL_PATH,
    StrategySignal,
    TickStore,
    TimeBar,
    TradeTick,
    WatchItem,
    aggregate_ticks,
    append_csv_row,
    detect_variant_double_bottom,
    fetch_latest_http_ticks,
    fetch_today_open_price,
    normalize_symbol,
    parse_trade_tick_message,
)


DEFAULT_MANAGER_DIR = PROJECT_ROOT / ".guanlan" / "alltick_manager"
DEFAULT_API_FILE = DEFAULT_MANAGER_DIR / "apis.txt"
DEFAULT_WATCHLIST_FILE = DEFAULT_MANAGER_DIR / "watchlist.csv"
DEFAULT_ASSIGNMENT_FILE = DEFAULT_MANAGER_DIR / "stock_assignments.csv"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "tools" / "alltick_multi_token_seconds.json"
DEFAULT_TICK_DB_PATH = PROJECT_ROOT / ".guanlan" / "alltick" / "multi_token_ticks.sqlite3"
DEFAULT_BAR_DB_PATH = PROJECT_ROOT / ".guanlan" / "alltick" / "multi_token_seconds.sqlite3"
DEFAULT_SIGNAL_PATH = PROJECT_ROOT / ".guanlan" / "alltick" / "multi_token_variant_double_bottom_signals.csv"

DEFAULT_CONFIG = {
    **CORE_DEFAULT_CONFIG,
    "distribution_mode": "balanced",
    "max_symbols_per_api": 10,
    "persist_intervals": [1, 5],
    "assignment_file": str(DEFAULT_ASSIGNMENT_FILE),
    "api_file": str(DEFAULT_API_FILE),
    "watchlist_file": str(DEFAULT_WATCHLIST_FILE),
    "log_each_tick": False,
}


@dataclass(frozen=True)
class ApiBucket:
    token: str
    watch_items: dict[str, WatchItem]


@dataclass(frozen=True)
class WorkerEvent:
    event_type: str
    worker_name: str
    token_suffix: str
    ticks: tuple[TradeTick, ...] = ()
    message: str = ""


class SecondBarStore:
    """SQLite store for locally aggregated second bars."""

    def __init__(self, path: Path = DEFAULT_BAR_DB_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS second_bars (
                symbol TEXT NOT NULL,
                code TEXT NOT NULL,
                interval_seconds INTEGER NOT NULL,
                bucket_start_ms INTEGER NOT NULL,
                bucket_start_text TEXT NOT NULL,
                bucket_end_ms INTEGER NOT NULL,
                bucket_end_text TEXT NOT NULL,
                open_price REAL NOT NULL,
                high_price REAL NOT NULL,
                low_price REAL NOT NULL,
                close_price REAL NOT NULL,
                volume REAL NOT NULL,
                turnover REAL NOT NULL,
                high_time_text TEXT NOT NULL,
                close_time_text TEXT NOT NULL,
                source_tick_count INTEGER NOT NULL,
                updated_at_text TEXT NOT NULL,
                PRIMARY KEY(symbol, interval_seconds, bucket_start_ms)
            );
            CREATE INDEX IF NOT EXISTS idx_second_bars_symbol_interval_time
            ON second_bars(symbol, interval_seconds, bucket_start_ms);
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def save_bars(self, symbol: str, interval_seconds: int, bars: Sequence[TimeBar]) -> int:
        if not bars:
            return 0
        normalized = normalize_symbol(symbol)
        code = normalized.split(".", 1)[0]
        updated_at_text = datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            (
                normalized,
                code,
                int(interval_seconds),
                int(bar.start_dt.timestamp() * 1000),
                bar.start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                int(bar.end_dt.timestamp() * 1000),
                bar.end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                float(bar.open_price),
                float(bar.high_price),
                float(bar.low_price),
                float(bar.close_price),
                float(bar.volume),
                float(bar.turnover),
                bar.high_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                bar.close_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                0,
                updated_at_text,
            )
            for bar in bars
        ]
        before = self.conn.total_changes
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO second_bars (
                symbol, code, interval_seconds, bucket_start_ms,
                bucket_start_text, bucket_end_ms, bucket_end_text,
                open_price, high_price, low_price, close_price,
                volume, turnover, high_time_text, close_time_text,
                source_tick_count, updated_at_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()
        return self.conn.total_changes - before


def resolve_project_path(raw: str | Path, fallback: Path) -> Path:
    text = str(raw or "").strip()
    if not text:
        return fallback.expanduser().resolve()
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def load_config(path: Path) -> dict:
    config = DEFAULT_CONFIG.copy()
    if not path.exists():
        return config
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return config
    if isinstance(loaded, dict):
        config.update(loaded)
    return config


def load_tokens(path: Path) -> list[str]:
    if not path.exists():
        return []
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        token = raw.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def load_watch_items_csv(path: Path) -> dict[str, WatchItem]:
    if not path.exists():
        raise FileNotFoundError(f"Watchlist file not found: {path}")
    items: dict[str, WatchItem] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw = str(row.get("symbol") or row.get("code") or "").strip()
            if not raw:
                continue
            try:
                symbol = normalize_symbol(raw)
            except Exception:
                continue
            name = str(row.get("name") or row.get("股票名称") or symbol).strip() or symbol
            items[symbol] = WatchItem(symbol=symbol, name=name)
    return items


def load_assignment_buckets(path: Path, max_active_apis: int = 0) -> list[ApiBucket]:
    if not path.exists():
        raise FileNotFoundError(f"Assignment file not found: {path}")
    grouped: dict[str, dict[str, WatchItem]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            token = str(row.get("api") or "").strip()
            raw_symbol = str(row.get("symbol") or row.get("code") or "").strip()
            if not token or not raw_symbol:
                continue
            try:
                symbol = normalize_symbol(raw_symbol)
            except Exception:
                continue
            name = str(row.get("name") or symbol).strip() or symbol
            grouped.setdefault(token, {})[symbol] = WatchItem(symbol=symbol, name=name)
    buckets = [ApiBucket(token=token, watch_items=items) for token, items in grouped.items() if items]
    if max_active_apis > 0:
        return buckets[:max_active_apis]
    return buckets


def build_balanced_buckets(
    api_file: Path,
    watchlist_file: Path,
    max_symbols_per_api: int,
    max_active_apis: int = 0,
    symbol_limit: int = 0,
) -> list[ApiBucket]:
    tokens = load_tokens(api_file)
    watch_items = list(load_watch_items_csv(watchlist_file).values())
    if symbol_limit > 0:
        watch_items = watch_items[:symbol_limit]
    if not tokens:
        raise RuntimeError(f"No API tokens found in {api_file}")
    if not watch_items:
        raise RuntimeError(f"No watchlist symbols found in {watchlist_file}")
    per_api = max(int(max_symbols_per_api), 1)
    needed = math.ceil(len(watch_items) / per_api)
    if max_active_apis > 0:
        needed = min(needed, int(max_active_apis))
    active_count = min(len(tokens), needed)
    if active_count <= 0:
        raise RuntimeError("No active APIs available")
    if len(watch_items) > active_count * per_api:
        raise RuntimeError(
            f"Not enough active APIs: symbols={len(watch_items)} max_per_api={per_api} active={active_count}"
        )
    active_tokens = tokens[:active_count]
    buckets: list[dict[str, WatchItem]] = [dict() for _ in range(active_count)]
    for idx, item in enumerate(watch_items):
        buckets[idx % active_count][item.symbol] = item
    return [
        ApiBucket(token=token, watch_items=watch_map)
        for token, watch_map in zip(active_tokens, buckets)
        if watch_map
    ]


def load_emitted_keys(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    result: set[tuple[str, str]] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            symbol = str(row.get("symbol") or "").strip()
            trading_day = str(row.get("trading_day") or "").strip()
            if symbol and trading_day:
                result.add((symbol, trading_day))
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="多 API AllTick 实时 tick / 秒K / 秒级策略扫描")
    parser.add_argument("--config-file", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--distribution-mode", choices=["balanced", "assignment"], default="")
    parser.add_argument("--assignment-file", default="")
    parser.add_argument("--api-file", default="")
    parser.add_argument("--watchlist-file", default="")
    parser.add_argument("--max-symbols-per-api", type=int, default=0)
    parser.add_argument("--max-active-apis", type=int, default=0)
    parser.add_argument("--symbol-limit", type=int, default=0)
    parser.add_argument("--tick-db-path", default=str(DEFAULT_TICK_DB_PATH))
    parser.add_argument("--bar-db-path", default=str(DEFAULT_BAR_DB_PATH))
    parser.add_argument("--signal-path", default=str(DEFAULT_SIGNAL_PATH))
    parser.add_argument("--run-seconds", type=int, default=0, help="0 表示持续运行")
    parser.add_argument("--seed-http", action="store_true")
    return parser.parse_args()


def token_suffix(token: str) -> str:
    text = token.strip()
    return text[-6:] if len(text) >= 6 else text


def build_buckets(config: dict, args: argparse.Namespace) -> list[ApiBucket]:
    distribution_mode = args.distribution_mode or str(config.get("distribution_mode") or "balanced")
    if distribution_mode == "assignment":
        assignment_path = resolve_project_path(
            args.assignment_file or str(config.get("assignment_file") or DEFAULT_ASSIGNMENT_FILE),
            DEFAULT_ASSIGNMENT_FILE,
        )
        buckets = load_assignment_buckets(assignment_path, max_active_apis=args.max_active_apis)
        if args.symbol_limit > 0:
            remaining = args.symbol_limit
            trimmed: list[ApiBucket] = []
            for bucket in buckets:
                if remaining <= 0:
                    break
                selected_items = dict(list(bucket.watch_items.items())[:remaining])
                if selected_items:
                    trimmed.append(ApiBucket(token=bucket.token, watch_items=selected_items))
                    remaining -= len(selected_items)
            return trimmed
        return buckets
    api_path = resolve_project_path(
        args.api_file or str(config.get("api_file") or DEFAULT_API_FILE),
        DEFAULT_API_FILE,
    )
    watchlist_path = resolve_project_path(
        args.watchlist_file or str(config.get("watchlist_file") or DEFAULT_WATCHLIST_FILE),
        DEFAULT_WATCHLIST_FILE,
    )
    max_symbols_per_api = args.max_symbols_per_api or int(config.get("max_symbols_per_api") or 10)
    return build_balanced_buckets(
        api_file=api_path,
        watchlist_file=watchlist_path,
        max_symbols_per_api=max_symbols_per_api,
        max_active_apis=args.max_active_apis,
        symbol_limit=args.symbol_limit,
    )


def worker_loop(
    worker_name: str,
    token: str,
    watch_items: dict[str, WatchItem],
    event_queue: queue.Queue[WorkerEvent],
    stop_event: threading.Event,
    run_seconds: int,
    seed_http: bool,
    started_at: float,
) -> None:
    heartbeat_seq = 2
    reconnect_count = 0
    subscribe_payload = {
        "cmd_id": 22004,
        "seq_id": 1,
        "trace": f"multi-seconds-sub-{worker_name}-{int(started_at)}",
        "data": {"symbol_list": [{"code": normalize_symbol(symbol)} for symbol in sorted(watch_items)]},
    }
    suffix = token_suffix(token)

    try:
        if seed_http and watch_items:
            try:
                seeded = fetch_latest_http_ticks(token, list(watch_items.keys()), watch_items)
                if seeded:
                    event_queue.put(
                        WorkerEvent(
                            event_type="ticks",
                            worker_name=worker_name,
                            token_suffix=suffix,
                            ticks=tuple(seeded),
                        )
                    )
            except Exception as exc:
                event_queue.put(
                    WorkerEvent(
                        event_type="status",
                        worker_name=worker_name,
                        token_suffix=suffix,
                        message=f"[seed-skip] {exc}",
                    )
                )

        while not stop_event.is_set():
            if run_seconds > 0 and time.time() - started_at >= run_seconds:
                break

            ws = None
            try:
                ws = websocket.create_connection(f"{ALLTICK_WS_URL}?token={token}", timeout=10)
                ws.settimeout(1)
                ws.send(json.dumps(subscribe_payload, ensure_ascii=False, separators=(",", ":")))
                event_queue.put(
                    WorkerEvent(
                        event_type="status",
                        worker_name=worker_name,
                        token_suffix=suffix,
                        message=f"[open] symbols={len(watch_items)}",
                    )
                )
                last_ping_at = time.time()
                last_heartbeat_at = time.time()

                while not stop_event.is_set():
                    now = time.time()
                    if run_seconds > 0 and now - started_at >= run_seconds:
                        break

                    if now - last_ping_at >= 10:
                        ws.ping()
                        last_ping_at = now

                    if now - last_heartbeat_at >= 15:
                        heartbeat = {
                            "cmd_id": 22000,
                            "seq_id": heartbeat_seq,
                            "trace": f"multi-seconds-hb-{worker_name}-{heartbeat_seq}",
                            "data": {},
                        }
                        heartbeat_seq += 1
                        ws.send(json.dumps(heartbeat, ensure_ascii=False, separators=(",", ":")))
                        last_heartbeat_at = now

                    try:
                        raw_message = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue

                    message = json.loads(raw_message)
                    cmd_id = int(message.get("cmd_id", 0) or 0)
                    if cmd_id in {22005, 22001}:
                        continue

                    ticks = parse_trade_tick_message(message, watch_items)
                    if not ticks:
                        continue

                    event_queue.put(
                        WorkerEvent(
                            event_type="ticks",
                            worker_name=worker_name,
                            token_suffix=suffix,
                            ticks=tuple(ticks),
                        )
                    )
            except Exception as exc:
                reconnect_count += 1
                event_queue.put(
                    WorkerEvent(
                        event_type="status",
                        worker_name=worker_name,
                        token_suffix=suffix,
                        message=f"[reconnect:{reconnect_count}] {exc}",
                    )
                )
                time.sleep(min(15, 2 + reconnect_count))
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
    finally:
        event_queue.put(
            WorkerEvent(
                event_type="done",
                worker_name=worker_name,
                token_suffix=suffix,
                message="[done]",
            )
        )


def main() -> int:
    args = parse_args()
    config = load_config(Path(args.config_file).expanduser().resolve())
    buckets = build_buckets(config, args)
    if not buckets:
        raise RuntimeError("No API buckets available")

    tick_db_path = Path(args.tick_db_path).expanduser().resolve()
    bar_db_path = Path(args.bar_db_path).expanduser().resolve()
    signal_path = Path(args.signal_path).expanduser().resolve()

    persist_intervals = [
        max(int(value), 1)
        for value in (config.get("persist_intervals") or [1, 5])
        if str(value).strip()
    ]
    persist_intervals = sorted(set(persist_intervals))

    distribution_mode = args.distribution_mode or str(config.get("distribution_mode") or "balanced")
    total_symbols = sum(len(bucket.watch_items) for bucket in buckets)
    print(
        "[start]"
        f" distribution={distribution_mode}"
        f" apis={len(buckets)}"
        f" symbols={total_symbols}"
        f" intervals={persist_intervals}"
        f" tick_db={tick_db_path}"
        f" bar_db={bar_db_path}"
        f" signal_path={signal_path}"
    )

    tick_store = TickStore(tick_db_path)
    bar_store = SecondBarStore(bar_db_path)
    event_queue: queue.Queue[WorkerEvent] = queue.Queue(maxsize=20000)
    stop_event = threading.Event()
    today = datetime.now(CHINA_TZ).date()

    ticks_by_symbol: dict[str, list[TradeTick]] = {}
    tick_keys_by_symbol: dict[str, set[tuple[int, str]]] = {}
    open_price_map: dict[str, float] = {}
    open_price_attempted: set[str] = set()
    last_scan_at: dict[str, float] = defaultdict(float)
    last_saved_bucket_ms: dict[tuple[str, int], int] = {}
    symbol_token_map = {
        symbol: bucket.token
        for bucket in buckets
        for symbol in bucket.watch_items
    }
    name_map = {
        symbol: item.name
        for bucket in buckets
        for symbol, item in bucket.watch_items.items()
    }
    emitted = load_emitted_keys(signal_path)

    stats = {
        "inserted_ticks": 0,
        "saved_bars": 0,
        "signals": 0,
        "events": 0,
    }

    day_start_ms = int(datetime.combine(today, datetime.min.time(), tzinfo=CHINA_TZ).timestamp() * 1000)
    day_end_ms = day_start_ms + 24 * 60 * 60 * 1000

    def ensure_symbol_loaded(symbol: str) -> None:
        if symbol in ticks_by_symbol:
            return
        history = tick_store.load_ticks(symbol, start_ms=day_start_ms, end_ms=day_end_ms)
        ticks_by_symbol[symbol] = history
        tick_keys_by_symbol[symbol] = {(tick.tick_time_ms, tick.seq) for tick in history}
        if history:
            open_price_map[symbol] = history[0].price

    def persist_bars_for_symbol(symbol: str) -> None:
        ticks = ticks_by_symbol.get(symbol) or []
        if not ticks:
            return
        for interval in persist_intervals:
            bars = aggregate_ticks(ticks, interval)
            if len(bars) > 1:
                bars = bars[:-1]
            if not bars:
                continue
            key = (symbol, interval)
            if key in last_saved_bucket_ms:
                lookback_start = last_saved_bucket_ms[key] - interval * 1000
                subset = [
                    bar
                    for bar in bars
                    if int(bar.start_dt.timestamp() * 1000) >= lookback_start
                ]
            else:
                subset = bars
            stats["saved_bars"] += bar_store.save_bars(symbol, interval, subset)
            last_saved_bucket_ms[key] = int(bars[-1].start_dt.timestamp() * 1000)

    def ensure_open_price(symbol: str) -> float | None:
        if symbol in open_price_map and open_price_map[symbol] > 0:
            return open_price_map[symbol]
        if symbol not in open_price_attempted:
            open_price_attempted.add(symbol)
            token = symbol_token_map.get(symbol, "")
            if token:
                try:
                    open_price = fetch_today_open_price(token, symbol)
                    if open_price and open_price > 0:
                        open_price_map[symbol] = open_price
                        return open_price
                except Exception:
                    pass
        ticks = ticks_by_symbol.get(symbol) or []
        if ticks:
            open_price_map[symbol] = ticks[0].price
            return ticks[0].price
        return None

    def maybe_emit_signal(symbol: str) -> None:
        ticks = ticks_by_symbol.get(symbol) or []
        if not ticks:
            return
        scan_interval = max(float(config.get("scan_interval_seconds") or 1), 0.2)
        now_ts = time.time()
        if now_ts - last_scan_at[symbol] < scan_interval:
            return
        last_scan_at[symbol] = now_ts
        open_price = ensure_open_price(symbol)
        if not open_price:
            return
        signal = detect_variant_double_bottom(ticks, config, open_price)
        if not signal:
            return
        key = (signal.symbol, signal.trading_day.isoformat())
        if key in emitted:
            return
        emitted.add(key)
        row = signal.to_dict()
        append_csv_row(signal_path, row, row.keys())
        stats["signals"] += 1
        print(
            "[signal]"
            f" {signal.symbol} {name_map.get(signal.symbol, signal.name)}"
            f" {signal.signal_time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}"
            f" price={signal.signal_price:.3f}"
            f" R1={signal.r1_time.strftime('%H:%M:%S')}/{signal.r1_price:.3f}"
            f" R2={signal.r2_time.strftime('%H:%M:%S')}/{signal.r2_price:.3f}"
            f" L1={signal.l1_time.strftime('%H:%M:%S')}/{signal.l1_price:.3f}"
            f" L2={signal.l2_time.strftime('%H:%M:%S')}/{signal.l2_price:.3f}"
        )

    threads: list[threading.Thread] = []
    started_at = time.time()
    try:
        for idx, bucket in enumerate(buckets, start=1):
            worker_name = f"api{idx:03d}"
            thread = threading.Thread(
                target=worker_loop,
                args=(
                    worker_name,
                    bucket.token,
                    bucket.watch_items,
                    event_queue,
                    stop_event,
                    args.run_seconds,
                    args.seed_http or bool(config.get("seed_latest_http")),
                    started_at,
                ),
                daemon=True,
            )
            thread.start()
            threads.append(thread)

        active_workers = len(threads)
        while active_workers > 0:
            try:
                event = event_queue.get(timeout=1)
            except queue.Empty:
                if args.run_seconds > 0 and time.time() - started_at >= args.run_seconds:
                    break
                continue

            stats["events"] += 1
            if event.event_type == "done":
                active_workers -= 1
                print(f"[worker] {event.worker_name} token=*{event.token_suffix} {event.message}")
                continue

            if event.event_type == "status":
                print(f"[worker] {event.worker_name} token=*{event.token_suffix} {event.message}")
                continue

            if event.event_type != "ticks":
                continue

            changed_symbols: set[str] = set()
            inserted_batch: list[TradeTick] = []
            for tick in event.ticks:
                symbol = tick.symbol
                ensure_symbol_loaded(symbol)
                tick_key = (tick.tick_time_ms, tick.seq)
                keys = tick_keys_by_symbol.setdefault(symbol, set())
                if tick_key in keys:
                    continue
                keys.add(tick_key)
                ticks_by_symbol.setdefault(symbol, []).append(tick)
                inserted_batch.append(tick)
                changed_symbols.add(symbol)

            if inserted_batch:
                inserted = tick_store.save_ticks(inserted_batch)
                stats["inserted_ticks"] += inserted
                if bool(config.get("log_each_tick")):
                    latest = inserted_batch[-1]
                    print(
                        "[tick]"
                        f" worker={event.worker_name}"
                        f" token=*{event.token_suffix}"
                        f" {latest.symbol}"
                        f" {latest.dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}"
                        f" price={latest.price:.3f}"
                        f" batch={len(inserted_batch)}"
                        f" inserted={inserted}"
                        f" total={stats['inserted_ticks']}"
                    )

            for symbol in changed_symbols:
                ticks_by_symbol[symbol].sort(key=lambda item: (item.tick_time_ms, item.seq))
                persist_bars_for_symbol(symbol)
                maybe_emit_signal(symbol)
    except KeyboardInterrupt:
        print("[stop] keyboard interrupt")
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=3)
        tick_store.close()
        bar_store.close()

    print(
        "[done]"
        f" apis={len(buckets)}"
        f" symbols={total_symbols}"
        f" inserted_ticks={stats['inserted_ticks']}"
        f" saved_bars={stats['saved_bars']}"
        f" signals={stats['signals']}"
        f" events={stats['events']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
