# -*- coding: utf-8 -*-
"""
币安数据获取模块
==============
整合自 bian 目录下的币安数据获取功能，提供：
1. 历史K线数据下载 (download_historical_data.py)
2. WebSocket 实时行情推送 (market.py, market_saver.py, binance_websocket.py)
3. 行情数据持久化 (market_saver.py)
4. 本地 Order Book 维护 (binance_websocket.py)

依赖:
    pip install python-binance
"""

import sys
import io
import time
import json
import logging
import sqlite3
import threading
from typing import Optional, Dict, List, Callable, Any, Union, Tuple
from datetime import datetime
from dataclasses import dataclass, field, asdict
from enum import Enum

from binance import Client as BinanceClient
from binance import ThreadedWebsocketManager

logger = logging.getLogger(__name__)

# 确保 stdout 使用 UTF-8 编码
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


# ============================================================
# K线周期枚举
# ============================================================

class Interval(str, Enum):
    """K线间隔"""
    MINUTE_1 = "1m"
    MINUTE_3 = "3m"
    MINUTE_5 = "5m"
    MINUTE_15 = "15m"
    MINUTE_30 = "30m"
    HOUR_1 = "1h"
    HOUR_2 = "2h"
    HOUR_4 = "4h"
    HOUR_6 = "6h"
    HOUR_8 = "8h"
    HOUR_12 = "12h"
    DAY_1 = "1d"
    DAY_3 = "3d"
    WEEK_1 = "1w"
    MONTH_1 = "1M"


# python-binance K线周期映射
INTERVAL_MAP = {
    "1m": BinanceClient.KLINE_INTERVAL_1MINUTE,
    "3m": BinanceClient.KLINE_INTERVAL_3MINUTE,
    "5m": BinanceClient.KLINE_INTERVAL_5MINUTE,
    "15m": BinanceClient.KLINE_INTERVAL_15MINUTE,
    "30m": BinanceClient.KLINE_INTERVAL_30MINUTE,
    "1h": BinanceClient.KLINE_INTERVAL_1HOUR,
    "2h": BinanceClient.KLINE_INTERVAL_2HOUR,
    "4h": BinanceClient.KLINE_INTERVAL_4HOUR,
    "6h": BinanceClient.KLINE_INTERVAL_6HOUR,
    "8h": BinanceClient.KLINE_INTERVAL_8HOUR,
    "12h": BinanceClient.KLINE_INTERVAL_12HOUR,
    "1d": BinanceClient.KLINE_INTERVAL_1DAY,
    "3d": BinanceClient.KLINE_INTERVAL_3DAY,
    "1w": BinanceClient.KLINE_INTERVAL_1WEEK,
    "1M": BinanceClient.KLINE_INTERVAL_1MONTH,
}


# ============================================================
# Stream 名称生成函数
# ============================================================

def trade_stream(symbol: str) -> str:
    """逐笔交易数据流"""
    return f"{symbol.lower()}@trade"

def agg_trade_stream(symbol: str) -> str:
    """归集交易数据流"""
    return f"{symbol.lower()}@aggTrade"

def kline_stream(symbol: str, interval: Interval) -> str:
    """K线数据流"""
    return f"{symbol.lower()}@kline_{interval.value}"

def mini_ticker_stream(symbol: str) -> str:
    """24hr 精简 Ticker 数据流"""
    return f"{symbol.lower()}@miniTicker"

def full_ticker_stream(symbol: str) -> str:
    """24hr 完整 Ticker 数据流"""
    return f"{symbol.lower()}@ticker"

def window_ticker_stream(symbol: str, window: str) -> str:
    """滚动窗口 Ticker 数据流 (如 1hTicker, 4hTicker)"""
    return f"{symbol.lower()}@{window}Ticker"

def book_ticker_stream(symbol: str) -> str:
    """最优挂单数据流"""
    return f"{symbol.lower()}@bookTicker"

def depth_stream(symbol: str, levels: Optional[str] = None) -> str:
    """深度信息数据流"""
    if levels:
        return f"{symbol.lower()}@depth{levels}"
    return f"{symbol.lower()}@depth"

def avg_price_stream(symbol: str) -> str:
    """加权平均价格数据流"""
    return f"{symbol.lower()}@avgPrice"

def reference_price_stream(symbol: str) -> str:
    """参考价格数据流"""
    return f"{symbol.lower()}@referencePrice"

def all_market_mini_ticker_stream() -> str:
    """全市场精简 Ticker 数据流"""
    return "!miniTicker@arr"

def all_market_ticker_stream() -> str:
    """全市场完整 Ticker 数据流"""
    return "!ticker@arr"

def all_market_book_ticker_stream() -> str:
    """全市场最优挂单数据流"""
    return "!bookTicker"


# ============================================================
# 数据模型 (WebSocket 事件)
# ============================================================

@dataclass
class AggTradeEvent:
    """归集交易事件"""
    event_type: str
    event_time: int
    symbol: str
    agg_trade_id: int
    price: str
    quantity: str
    first_trade_id: int
    last_trade_id: int
    trade_time: int
    is_buyer_maker: bool

@dataclass
class TradeEvent:
    """逐笔交易事件"""
    event_type: str
    event_time: int
    symbol: str
    trade_id: int
    price: str
    quantity: str
    trade_time: int
    is_buyer_maker: bool

@dataclass
class KlineData:
    """K线数据"""
    start_time: int
    close_time: int
    symbol: str
    interval: str
    first_trade_id: int
    last_trade_id: int
    open_price: str
    close_price: str
    high_price: str
    low_price: str
    volume: str
    trade_count: int
    is_closed: bool
    quote_volume: str
    taker_buy_volume: str
    taker_buy_quote_volume: str

@dataclass
class KlineEvent:
    """K线事件"""
    event_type: str
    event_time: int
    symbol: str
    kline: KlineData

@dataclass
class MiniTickerEvent:
    """精简 Ticker 事件"""
    event_type: str
    event_time: int
    symbol: str
    close_price: str
    open_price: str
    high_price: str
    low_price: str
    volume: str
    quote_volume: str

@dataclass
class TickerEvent:
    """完整 Ticker 事件"""
    event_type: str
    event_time: int
    symbol: str
    price_change: str
    price_change_percent: str
    weighted_avg_price: str
    prev_close_price: str
    last_price: str
    last_qty: str
    best_bid_price: str
    best_bid_qty: str
    best_ask_price: str
    best_ask_qty: str
    open_price: str
    high_price: str
    low_price: str
    volume: str
    quote_volume: str
    stat_open_time: int
    stat_close_time: int
    first_trade_id: int
    last_trade_id: int
    trade_count: int

@dataclass
class WindowTickerEvent:
    """滚动窗口 Ticker 事件"""
    event_type: str
    event_time: int
    symbol: str
    price_change: str
    price_change_percent: str
    open_price: str
    high_price: str
    low_price: str
    last_price: str
    weighted_avg_price: str
    volume: str
    quote_volume: str
    stat_open_time: int
    stat_close_time: int
    first_trade_id: int
    last_trade_id: int
    trade_count: int

@dataclass
class AvgPriceEvent:
    """加权平均价格事件"""
    event_type: str
    event_time: int
    symbol: str
    interval: str
    avg_price: str
    last_trade_time: int

@dataclass
class DepthEvent:
    """增量深度事件"""
    event_type: str
    event_time: int
    symbol: str
    first_update_id: int
    final_update_id: int
    bids: List[list]
    asks: List[list]

@dataclass
class BookTickerEvent:
    """最优挂单事件"""
    update_id: int
    symbol: str
    best_bid_price: str
    best_bid_qty: str
    best_ask_price: str
    best_ask_qty: str

@dataclass
class PartialDepthEvent:
    """部分深度事件"""
    last_update_id: int
    bids: List[list]
    asks: List[list]

@dataclass
class ReferencePriceEvent:
    """参考价格事件"""
    event_type: str
    symbol: str
    reference_price: Optional[str]
    trade_time: int


# ============================================================
# 历史K线数据下载
# ============================================================

def parse_time(time_str: str) -> int:
    """解析时间字符串为毫秒时间戳"""
    dt = datetime.strptime(time_str, "%Y-%m-%d")
    return int(dt.timestamp() * 1000)


class HistoricalDataDownloader:
    """
    历史K线数据下载器

    从 Binance API 下载指定时间范围的历史K线数据，
    保存到本地 SQLite 数据库供回测使用。

    用法:
        downloader = HistoricalDataDownloader("market_data.db")
        downloader.download("btcusdt", "1h", "2023-01-01", "2026-05-20")
    """

    def __init__(self, db_path: str = "market_data.db"):
        """
        Args:
            db_path: SQLite 数据库文件路径
        """
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS market_klines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                interval_type TEXT NOT NULL,
                start_time INTEGER,
                close_time INTEGER,
                open_price REAL,
                close_price REAL,
                high_price REAL,
                low_price REAL,
                volume REAL,
                quote_volume REAL,
                trade_count INTEGER,
                is_closed INTEGER,
                event_time INTEGER
            )
        """)
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_klines_unique
            ON market_klines(symbol, interval_type, start_time)
        """)
        # 迁移：删除旧的非唯一索引
        cursor.execute("DROP INDEX IF EXISTS idx_klines_lookup")
        conn.commit()
        conn.close()
        logger.info(f"数据库表初始化完成: {self.db_path}")

    def get_existing_range(self, symbol: str, interval: str) -> Tuple[Optional[int], Optional[int]]:
        """获取数据库中已有的数据时间范围"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT MIN(start_time), MAX(start_time) FROM market_klines WHERE symbol=? AND interval_type=?",
            (symbol.upper(), interval)
        )
        result = cursor.fetchone()
        conn.close()
        return result

    def download(self, symbol: str, interval: str,
                 start_time: str, end_time: Optional[str] = None,
                 force: bool = False):
        """
        下载历史K线数据并保存到数据库

        Args:
            symbol: 交易对 (如 "btcusdt")
            interval: K线周期 (如 "1h")
            start_time: 开始日期 (YYYY-MM-DD)
            end_time: 结束日期 (YYYY-MM-DD), 默认当前时间
            force: 是否强制重新下载已存在的数据
        """
        symbol_upper = symbol.upper()
        interval_binance = INTERVAL_MAP.get(interval)
        if not interval_binance:
            logger.error(f"不支持的K线周期: {interval}")
            return

        start_ms = parse_time(start_time)
        end_ms = parse_time(end_time) + 24 * 60 * 60 * 1000 - 1 if end_time else int(time.time() * 1000)

        # 检查数据库中已有数据
        if not force:
            existing_min, existing_max = self.get_existing_range(symbol, interval)
            if existing_min is not None and existing_max is not None:
                logger.info(f"数据库中已有 {symbol} {interval} 数据: "
                            f"{datetime.fromtimestamp(existing_min/1000).strftime('%Y-%m-%d')} ~ "
                            f"{datetime.fromtimestamp(existing_max/1000).strftime('%Y-%m-%d')}")

                if existing_min <= start_ms and existing_max >= end_ms:
                    logger.info("数据库已有完整数据，跳过下载")
                    return

                if existing_min > start_ms:
                    logger.info("下载缺失的早期数据...")
                    self._download_range(symbol, interval, interval_binance, start_ms, existing_min - 1)
                if existing_max < end_ms:
                    logger.info("下载缺失的近期数据...")
                    self._download_range(symbol, interval, interval_binance, existing_max + 1, end_ms)
                return

        # 全新下载
        self._download_range(symbol, interval, interval_binance, start_ms, end_ms)

    def _download_range(self, symbol: str, interval: str, interval_binance: str,
                        start_ms: int, end_ms: int):
        """下载指定时间范围的K线数据"""
        symbol_upper = symbol.upper()
        client = BinanceClient()
        all_klines = []
        current_start = start_ms
        batch_count = 0

        logger.info(f"开始下载 {symbol_upper} {interval} 历史数据...")
        logger.info(f"  时间范围: {datetime.fromtimestamp(start_ms/1000).strftime('%Y-%m-%d')} ~ "
                    f"{datetime.fromtimestamp(end_ms/1000).strftime('%Y-%m-%d')}")

        while current_start < end_ms:
            try:
                params = {
                    "symbol": symbol_upper,
                    "interval": interval_binance,
                    "limit": 1000,
                    "startTime": current_start,
                    "endTime": end_ms,
                }
                batch = client.get_klines(**params)

                if not batch:
                    break

                all_klines.extend(batch)
                batch_count += 1
                current_start = batch[-1][0] + 1

                last_time = datetime.fromtimestamp(batch[-1][0] / 1000)
                pct = (batch[-1][0] - start_ms) / (end_ms - start_ms) * 100 if end_ms > start_ms else 0
                logger.info(f"  批次 {batch_count}: 已获取 {len(all_klines)} 根K线 "
                            f"({pct:.1f}%) - 截至 {last_time.strftime('%Y-%m-%d')}")

                time.sleep(0.2)

            except Exception as e:
                logger.error(f"下载失败 (批次 {batch_count + 1}): {e}")
                time.sleep(2)
                continue

        logger.info(f"下载完成: 共 {len(all_klines)} 根K线")
        self._save_to_db(symbol, interval, all_klines)

    def _save_to_db(self, symbol: str, interval: str, klines: List[list]):
        """将K线数据批量保存到 SQLite 数据库（支持百万级）"""
        if not klines:
            return

        symbol_upper = symbol.upper()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 提速设置
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=OFF")
        cursor.execute("PRAGMA cache_size=100000")

        batch = []
        skipped = 0
        total = len(klines)
        BATCH_SIZE = 5000  # 每批 5000 条

        for kline in klines:
            start_time = kline[0]
            close_time = kline[6]
            open_price = float(kline[1])
            high_price = float(kline[2])
            low_price = float(kline[3])
            close_price = float(kline[4])
            volume = float(kline[5])
            quote_volume = float(kline[7]) if len(kline) > 7 else 0.0
            trade_count = int(kline[8]) if len(kline) > 8 else 0

            batch.append((
                symbol_upper, interval, start_time, close_time,
                open_price, close_price, high_price, low_price,
                volume, quote_volume, trade_count, 1, close_time,
            ))

            if len(batch) >= BATCH_SIZE:
                inserted = self._batch_insert(cursor, batch)
                skipped += (len(batch) - inserted)
                batch.clear()
                conn.commit()

        # 处理剩余数据
        if batch:
            inserted = self._batch_insert(cursor, batch)
            skipped += (len(batch) - inserted)

        conn.commit()
        conn.close()
        logger.info(f"数据库保存完成: 新增 {total - skipped} 条, 跳过 {skipped} 条 (已存在)")

    @staticmethod
    def _batch_insert(cursor, batch: List[tuple]) -> int:
        """批量 INSERT OR IGNORE，返回实际插入行数"""
        cursor.executemany(
            """INSERT OR IGNORE INTO market_klines
               (symbol, interval_type, start_time, close_time,
                open_price, close_price, high_price, low_price,
                volume, quote_volume, trade_count, is_closed, event_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            batch
        )
        return cursor.rowcount

    def verify(self, symbol: str, interval: str) -> dict:
        """验证数据库中的数据"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*), MIN(start_time), MAX(start_time) FROM market_klines WHERE symbol=? AND interval_type=?",
            (symbol.upper(), interval)
        )
        count, min_ts, max_ts = cursor.fetchone()
        conn.close()

        result = {"count": count, "min_time": min_ts, "max_time": max_ts}
        if count:
            min_dt = datetime.fromtimestamp(min_ts / 1000).strftime("%Y-%m-%d %H:%M")
            max_dt = datetime.fromtimestamp(max_ts / 1000).strftime("%Y-%m-%d %H:%M")
            logger.info(f"数据库验证: {symbol} {interval} 共 {count} 根K线")
            logger.info(f"  时间范围: {min_dt} ~ {max_dt}")
        else:
            logger.warning("数据库中没有数据")
        return result


# ============================================================
# WebSocket 实时行情客户端
# ============================================================

class BinanceWebSocketClient:
    """
    Binance WebSocket 实时行情客户端

    基于 python-binance 的 ThreadedWebsocketManager 实现。
    支持单个数据流和组合数据流订阅。

    用法:
        client = BinanceWebSocketClient()

        # 订阅单个数据流
        client.subscribe(trade_stream("btcusdt"), on_trade)
        client.start()

        # 或订阅组合数据流
        client.start_combined([trade_stream("btcusdt"), kline_stream("btcusdt", Interval.MINUTE_1)], callbacks)
        client.start()
    """

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None,
                 testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.twm: Optional[ThreadedWebsocketManager] = None
        self.is_connected = False
        self._should_stop = False
        self._lock = threading.Lock()
        self._subscribed_streams: Dict[str, List[Callable]] = {}
        self._callbacks: Dict[str, List[Callable]] = {}
        self._is_combined = False
        self._combined_streams: List[str] = []

        self.on_open_callback: Optional[Callable] = None
        self.on_close_callback: Optional[Callable] = None
        self.on_error_callback: Optional[Callable] = None

        self.auto_reconnect = True
        self.reconnect_interval = 5
        self.max_reconnect_retries = 0
        self._reconnect_count = 0
        self._socket_name_map: Dict[str, str] = {}

    def subscribe(self, stream_name: str, callback: Callable):
        """订阅一个数据流 (用于单个数据流连接)"""
        with self._lock:
            if stream_name not in self._subscribed_streams:
                self._subscribed_streams[stream_name] = []
            self._subscribed_streams[stream_name].append(callback)

    def unsubscribe(self, stream_name: str, callback: Optional[Callable] = None):
        """取消订阅"""
        with self._lock:
            if stream_name in self._subscribed_streams:
                if callback:
                    self._subscribed_streams[stream_name].remove(callback)
                    if not self._subscribed_streams[stream_name]:
                        del self._subscribed_streams[stream_name]
                else:
                    del self._subscribed_streams[stream_name]

    def start_combined(self, streams: List[str],
                       callbacks: Optional[Dict[str, Union[Callable, List[Callable]]]] = None):
        """启动组合数据流连接"""
        self._is_combined = True
        self._combined_streams = streams
        if callbacks:
            for stream_name, cb in callbacks.items():
                if isinstance(cb, list):
                    self._callbacks[stream_name] = cb
                else:
                    self._callbacks[stream_name] = [cb]

    def subscribe_combined(self, stream_name: str, callback: Callable):
        """在组合数据流连接中动态订阅"""
        with self._lock:
            if stream_name not in self._callbacks:
                self._callbacks[stream_name] = []
            self._callbacks[stream_name].append(callback)
        if self.is_connected and self.twm:
            self._start_socket_for_stream(stream_name)

    def unsubscribe_combined(self, stream_name: str, callback: Optional[Callable] = None):
        """在组合数据流连接中动态取消订阅"""
        with self._lock:
            if stream_name in self._callbacks:
                if callback:
                    self._callbacks[stream_name].remove(callback)
                    if not self._callbacks[stream_name]:
                        del self._callbacks[stream_name]
                else:
                    del self._callbacks[stream_name]
        if self.is_connected and self.twm and stream_name not in self._callbacks:
            self._stop_socket_for_stream(stream_name)

    def _parse_stream_name(self, stream_name: str) -> tuple:
        """解析 stream 名称"""
        parts = stream_name.split("@")
        symbol = parts[0]
        rest = "@".join(parts[1:]) if len(parts) > 1 else ""

        if rest.startswith("kline_"):
            return symbol, "kline", rest[6:]
        elif rest.startswith("depth"):
            levels = rest[5:]
            return symbol, "depth", levels if levels else None
        elif rest == "miniTicker":
            return symbol, "miniTicker", None
        elif rest == "ticker":
            return symbol, "ticker", None
        elif rest.endswith("Ticker"):
            return symbol, rest, None
        elif rest == "bookTicker":
            return symbol, "bookTicker", None
        elif rest == "trade":
            return symbol, "trade", None
        elif rest == "aggTrade":
            return symbol, "aggTrade", None
        elif rest == "avgPrice":
            return symbol, "avgPrice", None
        elif rest == "referencePrice":
            return symbol, "referencePrice", None
        elif rest == "!miniTicker@arr":
            return "!miniTicker", "!miniTicker@arr", None
        elif rest == "!ticker@arr":
            return "!ticker", "!ticker@arr", None
        elif rest == "!bookTicker":
            return "!bookTicker", "!bookTicker", None
        else:
            return symbol, rest, None

    def _start_socket_for_stream(self, stream_name: str):
        """通过 TWM 启动一个 socket"""
        if not self.twm:
            return

        symbol, stream_type, params = self._parse_stream_name(stream_name)
        if stream_name in self._socket_name_map:
            return

        socket_name = None
        try:
            if stream_type == "trade":
                socket_name = self.twm.start_trade_socket(
                    symbol=symbol, callback=self._make_callback(stream_name))
            elif stream_type == "aggTrade":
                socket_name = self.twm.start_agg_trade_socket(
                    symbol=symbol, callback=self._make_callback(stream_name))
            elif stream_type == "kline":
                socket_name = self.twm.start_kline_socket(
                    symbol=symbol, interval=params, callback=self._make_callback(stream_name))
            elif stream_type == "miniTicker":
                socket_name = self.twm.start_symbol_miniticker_socket(
                    symbol=symbol, callback=self._make_callback(stream_name))
            elif stream_type == "ticker":
                socket_name = self.twm.start_symbol_ticker_socket(
                    symbol=symbol, callback=self._make_callback(stream_name))
            elif stream_type == "bookTicker":
                socket_name = self.twm.start_symbol_book_ticker_socket(
                    symbol=symbol, callback=self._make_callback(stream_name))
            elif stream_type == "depth":
                if params:
                    socket_name = self.twm.start_depth_socket(
                        symbol=symbol, depth=params, callback=self._make_callback(stream_name))
                else:
                    socket_name = self.twm.start_depth_socket(
                        symbol=symbol, callback=self._make_callback(stream_name))
            elif stream_type == "!miniTicker@arr":
                socket_name = self.twm.start_miniticker_socket(
                    callback=self._make_callback(stream_name))
            elif stream_type == "!ticker@arr":
                socket_name = self.twm.start_ticker_socket(
                    callback=self._make_callback(stream_name))
            elif stream_type == "!bookTicker":
                socket_name = self.twm.start_book_ticker_socket(
                    callback=self._make_callback(stream_name))
            elif stream_type == "avgPrice":
                socket_name = self.twm.start_avg_price_socket(
                    symbol=symbol, callback=self._make_callback(stream_name))
            else:
                logger.warning(f"不支持的 stream 类型: {stream_type} for {stream_name}")
                return

            if socket_name:
                self._socket_name_map[stream_name] = socket_name
        except Exception as e:
            logger.error(f"启动 socket 失败 {stream_name}: {e}")

    def _stop_socket_for_stream(self, stream_name: str):
        """停止指定 stream 的 socket"""
        if not self.twm:
            return
        socket_name = self._socket_name_map.pop(stream_name, None)
        if socket_name:
            try:
                self.twm.stop_socket(socket_name)
            except Exception as e:
                logger.error(f"停止 socket 失败 {stream_name}: {e}")

    def _make_callback(self, stream_name: str) -> Callable:
        """创建包装回调"""
        def wrapped_callback(msg):
            self._on_message(stream_name, msg)
        return wrapped_callback

    def _on_message(self, stream_name: str, msg: dict):
        """处理收到的消息"""
        if isinstance(msg, dict) and msg.get("e") == "serverShutdown":
            logger.warning("收到服务器关闭事件")
            return
        self._dispatch_event(stream_name, msg)

    def _dispatch_event(self, stream_name: str, data: Union[dict, list]):
        """分发事件到对应的回调"""
        callbacks = []
        with self._lock:
            if self._is_combined:
                if stream_name in self._callbacks:
                    callbacks = self._callbacks[stream_name].copy()
            else:
                if stream_name in self._subscribed_streams:
                    callbacks = self._subscribed_streams[stream_name].copy()

        if isinstance(data, dict):
            parsed = self._parse_event(data)
        else:
            parsed = data

        for callback in callbacks:
            try:
                callback(parsed if parsed else data)
            except Exception as e:
                logger.error(f"回调错误 {stream_name}: {e}")

    def _parse_event(self, data: dict) -> Optional[Any]:
        """根据事件类型解析数据"""
        event_type = data.get("e", "")

        if event_type == "aggTrade":
            return AggTradeEvent(
                event_type=data["e"], event_time=data["E"], symbol=data["s"],
                agg_trade_id=data["a"], price=data["p"], quantity=data["q"],
                first_trade_id=data["f"], last_trade_id=data["l"],
                trade_time=data["T"], is_buyer_maker=data["m"])
        elif event_type == "trade":
            return TradeEvent(
                event_type=data["e"], event_time=data["E"], symbol=data["s"],
                trade_id=data["t"], price=data["p"], quantity=data["q"],
                trade_time=data["T"], is_buyer_maker=data["m"])
        elif event_type == "kline":
            k = data["k"]
            return KlineEvent(
                event_type=data["e"], event_time=data["E"], symbol=data["s"],
                kline=KlineData(
                    start_time=k["t"], close_time=k["T"], symbol=k["s"],
                    interval=k["i"], first_trade_id=k["f"], last_trade_id=k["L"],
                    open_price=k["o"], close_price=k["c"], high_price=k["h"],
                    low_price=k["l"], volume=k["v"], trade_count=k["n"],
                    is_closed=k["x"], quote_volume=k["q"],
                    taker_buy_volume=k["V"], taker_buy_quote_volume=k["Q"]))
        elif event_type == "24hrMiniTicker":
            return MiniTickerEvent(
                event_type=data["e"], event_time=data["E"], symbol=data["s"],
                close_price=data["c"], open_price=data["o"], high_price=data["h"],
                low_price=data["l"], volume=data["v"], quote_volume=data["q"])
        elif event_type == "24hrTicker":
            return TickerEvent(
                event_type=data["e"], event_time=data["E"], symbol=data["s"],
                price_change=data["p"], price_change_percent=data["P"],
                weighted_avg_price=data["w"], prev_close_price=data["x"],
                last_price=data["c"], last_qty=data["Q"],
                best_bid_price=data["b"], best_bid_qty=data["B"],
                best_ask_price=data["a"], best_ask_qty=data["A"],
                open_price=data["o"], high_price=data["h"], low_price=data["l"],
                volume=data["v"], quote_volume=data["q"],
                stat_open_time=data["O"], stat_close_time=data["C"],
                first_trade_id=data["F"], last_trade_id=data["L"],
                trade_count=data["n"])
        elif event_type == "avgPrice":
            return AvgPriceEvent(
                event_type=data["e"], event_time=data["E"], symbol=data["s"],
                interval=data["i"], avg_price=data["w"], last_trade_time=data["T"])
        elif event_type == "depthUpdate":
            return DepthEvent(
                event_type=data["e"], event_time=data["E"], symbol=data["s"],
                first_update_id=data["U"], final_update_id=data["u"],
                bids=data["b"], asks=data["a"])
        elif event_type == "referencePrice":
            return ReferencePriceEvent(
                event_type=data["e"], symbol=data["s"],
                reference_price=data.get("r"), trade_time=data["t"])
        elif event_type.endswith("Ticker"):
            return WindowTickerEvent(
                event_type=data["e"], event_time=data["E"], symbol=data["s"],
                price_change=data["p"], price_change_percent=data["P"],
                open_price=data["o"], high_price=data["h"], low_price=data["l"],
                last_price=data["c"], weighted_avg_price=data["w"],
                volume=data["v"], quote_volume=data["q"],
                stat_open_time=data["O"], stat_close_time=data["C"],
                first_trade_id=data["F"], last_trade_id=data["L"],
                trade_count=data["n"])

        if "u" in data and "b" in data and "a" in data and "s" in data:
            if "B" in data and "A" in data:
                return BookTickerEvent(
                    update_id=data["u"], symbol=data["s"],
                    best_bid_price=data["b"], best_bid_qty=data["B"],
                    best_ask_price=data["a"], best_ask_qty=data["A"])

        if "lastUpdateId" in data and "bids" in data and "asks" in data:
            return PartialDepthEvent(
                last_update_id=data["lastUpdateId"],
                bids=data["bids"], asks=data["asks"])

        return data

    def start(self):
        """启动 WebSocket 连接"""
        self._should_stop = False

        if self.api_key and self.api_secret:
            self.twm = ThreadedWebsocketManager(
                api_key=self.api_key, api_secret=self.api_secret,
                testnet=self.testnet)
        else:
            self.twm = ThreadedWebsocketManager(testnet=self.testnet)

        self.twm.start()

        if self._is_combined:
            for stream_name in self._combined_streams:
                self._start_socket_for_stream(stream_name)
        else:
            with self._lock:
                stream_names = list(self._subscribed_streams.keys())
            for stream_name in stream_names:
                self._start_socket_for_stream(stream_name)

        self.is_connected = True
        self._reconnect_count = 0
        if self.on_open_callback:
            self.on_open_callback()
        logger.info("WebSocket 客户端已启动")

    def stop(self):
        """停止 WebSocket 连接"""
        self._should_stop = True
        if self.twm:
            try:
                self.twm.stop()
                logger.info("WebSocket 客户端已停止")
            except Exception as e:
                logger.error(f"停止 WebSocket 客户端出错: {e}")
        self.is_connected = False
        self._socket_name_map.clear()

    def wait(self, timeout: Optional[float] = None):
        """等待连接结束"""
        if self.twm and hasattr(self.twm, 'join'):
            self.twm.join(timeout=timeout)


# ============================================================
# 本地 Order Book 维护
# ============================================================

class OrderBook:
    """
    本地 Order Book 副本

    根据增量深度信息流维护本地订单簿。

    用法:
        book = OrderBook("bnbbtc")
        book.set_snapshot(snapshot)
        book.apply_depth(event)
    """

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.last_update_id: int = 0
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self._initialized = False
        self._pending_events: List[DepthEvent] = []
        self._first_event_u: Optional[int] = None

    def set_snapshot(self, snapshot: dict):
        """
        设置深度快照

        Args:
            snapshot: GET /api/v3/depth?symbol=XXX&limit=5000 的响应
        """
        snapshot_last_update_id = snapshot["lastUpdateId"]

        if self._first_event_u is not None:
            if snapshot_last_update_id <= self._first_event_u:
                logger.warning("Snapshot lastUpdateId <= first event U, need to fetch again")
                return

        # 清空 bids/asks
        self.bids.clear()
        self.asks.clear()

        # 设置快照
        for bid in snapshot["bids"]:
            price = float(bid[0])
            qty = float(bid[1])
            if qty > 0:
                self.bids[price] = qty

        for ask in snapshot["asks"]:
            price = float(ask[0])
            qty = float(ask[1])
            if qty > 0:
                self.asks[price] = qty

        self.last_update_id = snapshot_last_update_id
        self._initialized = True

        # 处理待处理的 events
        self._process_pending_events()

    def apply_depth(self, event: DepthEvent):
        """
        应用增量深度更新

        Args:
            event: 增量深度事件
        """
        if not self._initialized:
            if self._first_event_u is None:
                self._first_event_u = event.first_update_id
            self._pending_events.append(event)
            return

        if event.final_update_id < self.last_update_id:
            return

        if event.first_update_id > self.last_update_id + 1:
            logger.warning(f"Missing events: first_update_id={event.first_update_id} > last_update_id={self.last_update_id} + 1")
            self._initialized = False
            self._pending_events.clear()
            self._first_event_u = event.first_update_id
            self._pending_events.append(event)
            return

        # 更新 bids
        for bid in event.bids:
            price = float(bid[0])
            qty = float(bid[1])
            if qty == 0:
                self.bids.pop(price, None)
            else:
                self.bids[price] = qty

        # 更新 asks
        for ask in event.asks:
            price = float(ask[0])
            qty = float(ask[1])
            if qty == 0:
                self.asks.pop(price, None)
            else:
                self.asks[price] = qty

        self.last_update_id = event.final_update_id

    def _process_pending_events(self):
        """处理缓存的 events"""
        for event in self._pending_events:
            self.apply_depth(event)
        self._pending_events.clear()

    def get_bids(self, limit: int = 10) -> List[tuple]:
        """获取买单列表 (从高到低)"""
        sorted_bids = sorted(self.bids.items(), key=lambda x: -x[0])
        return [(f"{p:.8f}", f"{q:.8f}") for p, q in sorted_bids[:limit]]

    def get_asks(self, limit: int = 10) -> List[tuple]:
        """获取卖单列表 (从低到高)"""
        sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])
        return [(f"{p:.8f}", f"{q:.8f}") for p, q in sorted_asks[:limit]]

    def get_best_bid(self) -> Optional[tuple]:
        """获取最优买单 (最高价)"""
        if not self.bids:
            return None
        best_price = max(self.bids.keys())
        return (f"{best_price:.8f}", f"{self.bids[best_price]:.8f}")

    def get_best_ask(self) -> Optional[tuple]:
        """获取最优卖单 (最低价)"""
        if not self.asks:
            return None
        best_price = min(self.asks.keys())
        return (f"{best_price:.8f}", f"{self.asks[best_price]:.8f}")

    def get_spread(self) -> Optional[float]:
        """获取买卖价差"""
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if best_bid and best_ask:
            return float(best_ask[0]) - float(best_bid[0])
        return None

    def get_mid_price(self) -> Optional[float]:
        """获取中间价"""
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if best_bid and best_ask:
            return (float(best_bid[0]) + float(best_ask[0])) / 2
        return None

    def reset(self):
        """重置 Order Book"""
        self.bids.clear()
        self.asks.clear()
        self.last_update_id = 0
        self._initialized = False
        self._pending_events.clear()
        self._first_event_u = None


# ============================================================
# 行情数据持久化 (Redis + 数据库)
# ============================================================

# 默认配置
DEFAULT_CONFIG = {
    "redis_host": "localhost",
    "redis_port": 6379,
    "redis_db": 5,
    "redis_password": None,
    "redis_key_prefix": "market:",
    "db_type": "sqlite",
    "sqlite_path": "market_data.db",
    "mysql_host": "localhost",
    "mysql_port": 3306,
    "mysql_user": "root",
    "mysql_password": "",
    "mysql_database": "market_data",
    "save_to_redis": True,
    "save_to_db": True,
    "batch_size": 100,
    "flush_interval": 5,
}


def event_to_dict(msg: Any) -> Optional[Dict]:
    """
    将行情消息转为可序列化的字典

    Args:
        msg: WebSocket 消息 (dict 或 list)

    Returns:
        序列化后的字典, 或 None 如果无法识别
    """
    if not isinstance(msg, dict):
        if isinstance(msg, list):
            return {
                "_type": "all_market_mini_ticker",
                "count": len(msg),
                "data": msg,
                "save_time": int(time.time() * 1000),
            }
        return None

    event_type = msg.get("e", "")

    if event_type == "trade":
        return {
            "_type": "trade",
            "symbol": msg["s"],
            "trade_id": msg["t"],
            "price": msg["p"],
            "quantity": msg["q"],
            "trade_time": msg["T"],
            "event_time": msg["E"],
            "is_buyer_maker": msg["m"],
        }
    elif event_type == "aggTrade":
        return {
            "_type": "aggTrade",
            "symbol": msg["s"],
            "agg_trade_id": msg["a"],
            "price": msg["p"],
            "quantity": msg["q"],
            "trade_time": msg["T"],
            "event_time": msg["E"],
            "is_buyer_maker": msg["m"],
        }
    elif event_type == "kline":
        k = msg["k"]
        return {
            "_type": "kline",
            "symbol": k["s"],
            "interval": k["i"],
            "start_time": k["t"],
            "close_time": k["T"],
            "open_price": k["o"],
            "close_price": k["c"],
            "high_price": k["h"],
            "low_price": k["l"],
            "volume": k["v"],
            "quote_volume": k["q"],
            "trade_count": k["n"],
            "is_closed": k["x"],
            "taker_buy_volume": k["V"],
            "taker_buy_quote_volume": k["Q"],
            "event_time": msg["E"],
        }
    elif event_type == "24hrMiniTicker":
        return {
            "_type": "miniTicker",
            "symbol": msg["s"],
            "close_price": msg["c"],
            "open_price": msg["o"],
            "high_price": msg["h"],
            "low_price": msg["l"],
            "volume": msg["v"],
            "quote_volume": msg["q"],
            "event_time": msg["E"],
        }
    elif event_type == "24hrTicker":
        return {
            "_type": "ticker",
            "symbol": msg["s"],
            "price_change": msg["p"],
            "price_change_percent": msg["P"],
            "weighted_avg_price": msg["w"],
            "last_price": msg["c"],
            "last_qty": msg["Q"],
            "best_bid_price": msg["b"],
            "best_bid_qty": msg["B"],
            "best_ask_price": msg["a"],
            "best_ask_qty": msg["A"],
            "open_price": msg["o"],
            "high_price": msg["h"],
            "low_price": msg["l"],
            "volume": msg["v"],
            "quote_volume": msg["q"],
            "trade_count": msg["n"],
            "event_time": msg["E"],
        }
    elif event_type == "avgPrice":
        return {
            "_type": "avgPrice",
            "symbol": msg["s"],
            "interval": msg["i"],
            "avg_price": msg["w"],
            "last_trade_time": msg["T"],
            "event_time": msg["E"],
        }
    elif event_type == "referencePrice":
        return {
            "_type": "referencePrice",
            "symbol": msg["s"],
            "reference_price": msg["p"],
            "trade_time": msg["T"],
            "event_time": msg["E"],
        }
    elif event_type == "depthUpdate":
        return {
            "_type": "depth",
            "symbol": msg["s"],
            "first_update_id": msg["U"],
            "final_update_id": msg["u"],
            "bids": msg["b"][:10],
            "asks": msg["a"][:10],
            "event_time": msg["E"],
        }
    elif event_type.endswith("Ticker"):
        return {
            "_type": "windowTicker",
            "symbol": msg["s"],
            "event_type": event_type,
            "price_change": msg["p"],
            "price_change_percent": msg["P"],
            "open_price": msg["o"],
            "high_price": msg["h"],
            "low_price": msg["l"],
            "last_price": msg["c"],
            "volume": msg["v"],
            "quote_volume": msg["q"],
            "trade_count": msg["n"],
            "event_time": msg["E"],
        }

    if "u" in msg and "b" in msg and "a" in msg and "s" in msg and "B" in msg and "A" in msg:
        return {
            "_type": "bookTicker",
            "symbol": msg["s"],
            "update_id": msg["u"],
            "best_bid_price": msg["b"],
            "best_bid_qty": msg["B"],
            "best_ask_price": msg["a"],
            "best_ask_qty": msg["A"],
        }

    if "lastUpdateId" in msg and "bids" in msg and "asks" in msg:
        return {
            "_type": "partialDepth",
            "symbol": msg.get("s", ""),
            "last_update_id": msg["lastUpdateId"],
            "bids": msg["bids"],
            "asks": msg["asks"],
        }

    return None


class RedisStore:
    """Redis 行情缓存存储"""

    def __init__(self, config: dict):
        self.config = config
        self.prefix = config.get("redis_key_prefix", "market:")
        self.client = None
        self._connect()

    def _connect(self):
        """连接 Redis"""
        try:
            import redis
            self.client = redis.Redis(
                host=self.config.get("redis_host", "localhost"),
                port=self.config.get("redis_port", 6379),
                db=self.config.get("redis_db", 0),
                password=self.config.get("redis_password"),
                decode_responses=True,
            )
            self.client.ping()
            logger.info("Redis 连接成功")
        except ImportError:
            logger.warning("redis 库未安装, 请执行: pip install redis")
            self.client = None
        except Exception as e:
            logger.warning(f"Redis 连接失败: {e}")
            self.client = None

    def save(self, data: dict):
        """保存行情数据到 Redis"""
        if not self.client:
            return

        try:
            market_type = data.get("_type", "unknown")
            symbol = data.get("symbol", "unknown")

            if market_type == "all_market_mini_ticker":
                for ticker in data.get("data", []):
                    if isinstance(ticker, dict):
                        sym = ticker.get("s", "")
                        key = f"{self.prefix}{sym}:miniTicker"
                        self.client.setex(key, 60, json.dumps(ticker))
                key = f"{self.prefix}all:snapshot"
                self.client.setex(key, 60, json.dumps(data))
                return

            key = f"{self.prefix}{symbol}:{market_type}"
            self.client.setex(key, 60, json.dumps(data))

            latest_key = f"{self.prefix}{symbol}:latest"
            latest_data = {
                "type": market_type,
                "symbol": symbol,
                "time": data.get("event_time") or data.get("trade_time") or int(time.time() * 1000),
                "price": data.get("close_price") or data.get("last_price") or data.get("avg_price") or data.get("price", ""),
            }
            self.client.setex(latest_key, 60, json.dumps(latest_data))

        except Exception as e:
            logger.error(f"Redis 保存失败: {e}")

    def close(self):
        """关闭 Redis 连接"""
        if self.client:
            self.client.close()


class DatabaseStore:
    """数据库行情持久化存储 (支持 SQLite / MySQL)"""

    def __init__(self, config: dict):
        self.config = config
        self.db_type = config.get("db_type", "sqlite")
        self.conn = None
        self._connect()
        self._init_tables()

    def _connect(self):
        """连接数据库"""
        try:
            if self.db_type == "mysql":
                import pymysql
                self.conn = pymysql.connect(
                    host=self.config.get("mysql_host", "localhost"),
                    port=self.config.get("mysql_port", 3306),
                    user=self.config.get("mysql_user", "root"),
                    password=self.config.get("mysql_password", ""),
                    database=self.config.get("mysql_database", "market_data"),
                    charset="utf8mb4",
                )
            else:
                import sqlite3
                db_path = self.config.get("sqlite_path", "market_data.db")
                self.conn = sqlite3.connect(db_path, check_same_thread=False)

            logger.info(f"数据库连接成功 ({self.db_type})")
        except ImportError as e:
            logger.warning(f"数据库驱动未安装: {e}")
            self.conn = None
        except Exception as e:
            logger.warning(f"数据库连接失败: {e}")
            self.conn = None

    def _init_tables(self):
        """初始化数据库表"""
        if not self.conn:
            return

        cursor = self.conn.cursor()

        if self.db_type == "mysql":
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_trades (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    trade_type VARCHAR(20) NOT NULL,
                    trade_id BIGINT,
                    price DECIMAL(30,10),
                    quantity DECIMAL(30,10),
                    trade_time BIGINT,
                    event_time BIGINT,
                    is_buyer_maker TINYINT(1),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_symbol_time (symbol, trade_time),
                    INDEX idx_trade_type (trade_type)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_klines (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    interval_type VARCHAR(10) NOT NULL,
                    start_time BIGINT,
                    close_time BIGINT,
                    open_price DECIMAL(30,10),
                    close_price DECIMAL(30,10),
                    high_price DECIMAL(30,10),
                    low_price DECIMAL(30,10),
                    volume DECIMAL(30,10),
                    quote_volume DECIMAL(30,10),
                    trade_count INT,
                    is_closed TINYINT(1),
                    event_time BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_symbol_interval (symbol, interval_type, start_time)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_tickers (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    ticker_type VARCHAR(20) NOT NULL,
                    last_price DECIMAL(30,10),
                    open_price DECIMAL(30,10),
                    high_price DECIMAL(30,10),
                    low_price DECIMAL(30,10),
                    volume DECIMAL(30,10),
                    quote_volume DECIMAL(30,10),
                    price_change DECIMAL(30,10),
                    price_change_percent VARCHAR(20),
                    trade_count INT,
                    event_time BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_symbol_type_time (symbol, ticker_type, event_time)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_book_tickers (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    update_id BIGINT,
                    best_bid_price DECIMAL(30,10),
                    best_bid_qty DECIMAL(30,10),
                    best_ask_price DECIMAL(30,10),
                    best_ask_qty DECIMAL(30,10),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_symbol_time (symbol, created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_avg_prices (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    interval_type VARCHAR(10),
                    avg_price DECIMAL(30,10),
                    event_time BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_symbol_time (symbol, event_time)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_depths (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    depth_type VARCHAR(20),
                    update_id BIGINT,
                    best_bid_price DECIMAL(30,10),
                    best_ask_price DECIMAL(30,10),
                    bid_count INT,
                    ask_count INT,
                    bids JSON,
                    asks JSON,
                    event_time BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_symbol_time (symbol, event_time)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_reference_prices (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    reference_price DECIMAL(30,10),
                    trade_time BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_symbol_time (symbol, trade_time)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    trade_type TEXT NOT NULL,
                    trade_id INTEGER,
                    price REAL,
                    quantity REAL,
                    trade_time INTEGER,
                    event_time INTEGER,
                    is_buyer_maker INTEGER
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_klines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    interval_type TEXT NOT NULL,
                    start_time INTEGER,
                    close_time INTEGER,
                    open_price REAL,
                    close_price REAL,
                    high_price REAL,
                    low_price REAL,
                    volume REAL,
                    quote_volume REAL,
                    trade_count INTEGER,
                    is_closed INTEGER,
                    event_time INTEGER
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_tickers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    ticker_type TEXT NOT NULL,
                    last_price REAL,
                    open_price REAL,
                    high_price REAL,
                    low_price REAL,
                    volume REAL,
                    quote_volume REAL,
                    price_change REAL,
                    price_change_percent TEXT,
                    trade_count INTEGER,
                    event_time INTEGER
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_book_tickers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    update_id INTEGER,
                    best_bid_price REAL,
                    best_bid_qty REAL,
                    best_ask_price REAL,
                    best_ask_qty REAL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_avg_prices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    interval_type TEXT,
                    avg_price REAL,
                    event_time INTEGER
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_depths (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    depth_type TEXT,
                    update_id INTEGER,
                    best_bid_price REAL,
                    best_ask_price REAL,
                    bid_count INTEGER,
                    ask_count INTEGER,
                    bids TEXT,
                    asks TEXT,
                    event_time INTEGER
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_reference_prices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    reference_price REAL,
                    trade_time INTEGER
                )
            """)

        self.conn.commit()
        logger.info("数据库表初始化完成")

    def save(self, data: dict):
        """保存行情数据到数据库"""
        if not self.conn or not data:
            return

        try:
            market_type = data.get("_type", "")
            if market_type == "trade":
                self._save_trade(data)
            elif market_type == "aggTrade":
                self._save_trade(data)
            elif market_type == "kline":
                self._save_kline(data)
            elif market_type in ("miniTicker", "ticker", "windowTicker"):
                self._save_ticker(data)
            elif market_type == "bookTicker":
                self._save_book_ticker(data)
            elif market_type == "avgPrice":
                self._save_avg_price(data)
            elif market_type in ("depth", "partialDepth"):
                self._save_depth(data)
            elif market_type == "referencePrice":
                self._save_reference_price(data)
        except Exception as e:
            logger.error(f"数据库保存失败 ({data.get('_type', 'unknown')}): {e}")

    def _save_trade(self, data: dict):
        cursor = self.conn.cursor()
        cursor.execute(
            """INSERT INTO market_trades
               (symbol, trade_type, trade_id, price, quantity, trade_time, event_time, is_buyer_maker)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (data.get("symbol", ""), data.get("_type", "trade"),
             data.get("trade_id") or data.get("agg_trade_id"),
             data.get("price"), data.get("quantity"),
             data.get("trade_time"), data.get("event_time"),
             1 if data.get("is_buyer_maker") else 0))
        self.conn.commit()

    def _save_kline(self, data: dict):
        cursor = self.conn.cursor()
        cursor.execute(
            """INSERT INTO market_klines
               (symbol, interval_type, start_time, close_time,
                open_price, close_price, high_price, low_price,
                volume, quote_volume, trade_count, is_closed, event_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data.get("symbol", ""), data.get("interval", ""),
             data.get("start_time"), data.get("close_time"),
             data.get("open_price"), data.get("close_price"),
             data.get("high_price"), data.get("low_price"),
             data.get("volume"), data.get("quote_volume"),
             data.get("trade_count"), 1 if data.get("is_closed") else 0,
             data.get("event_time")))
        self.conn.commit()

    def _save_ticker(self, data: dict):
        cursor = self.conn.cursor()
        cursor.execute(
            """INSERT INTO market_tickers
               (symbol, ticker_type, last_price, open_price, high_price, low_price,
                volume, quote_volume, price_change, price_change_percent, trade_count, event_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data.get("symbol", ""), data.get("_type", "ticker"),
             data.get("last_price") or data.get("close_price"),
             data.get("open_price"), data.get("high_price"),
             data.get("low_price"), data.get("volume"),
             data.get("quote_volume"), data.get("price_change"),
             data.get("price_change_percent"), data.get("trade_count"),
             data.get("event_time")))
        self.conn.commit()

    def _save_book_ticker(self, data: dict):
        cursor = self.conn.cursor()
        cursor.execute(
            """INSERT INTO market_book_tickers
               (symbol, update_id, best_bid_price, best_bid_qty, best_ask_price, best_ask_qty)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (data.get("symbol", ""), data.get("update_id"),
             data.get("best_bid_price"), data.get("best_bid_qty"),
             data.get("best_ask_price"), data.get("best_ask_qty")))
        self.conn.commit()

    def _save_avg_price(self, data: dict):
        cursor = self.conn.cursor()
        cursor.execute(
            """INSERT INTO market_avg_prices
               (symbol, interval_type, avg_price, event_time)
               VALUES (?, ?, ?, ?)""",
            (data.get("symbol", ""), data.get("interval", ""),
             data.get("avg_price"), data.get("event_time")))
        self.conn.commit()

    def _save_depth(self, data: dict):
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        best_bid = float(bids[0][0]) if bids else 0
        best_ask = float(asks[0][0]) if asks else 0
        cursor = self.conn.cursor()
        cursor.execute(
            """INSERT INTO market_depths
               (symbol, depth_type, update_id, best_bid_price, best_ask_price,
                bid_count, ask_count, bids, asks, event_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data.get("symbol", ""), data.get("_type", "depth"),
             data.get("final_update_id") or data.get("last_update_id") or data.get("first_update_id"),
             best_bid, best_ask, len(bids), len(asks),
             json.dumps(bids), json.dumps(asks), data.get("event_time")))
        self.conn.commit()

    def _save_reference_price(self, data: dict):
        cursor = self.conn.cursor()
        cursor.execute(
            """INSERT INTO market_reference_prices
               (symbol, reference_price, trade_time)
               VALUES (?, ?, ?)""",
            (data.get("symbol", ""), data.get("reference_price"), data.get("trade_time")))
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()


class MarketSaver:
    """
    行情数据保存器

    同时将行情数据保存到 Redis (缓存) 和 数据库 (持久化)

    用法:
        saver = MarketSaver()
        saver.on_trade(msg)
        saver.on_kline(msg)
        saver.close()
    """

    def __init__(self, config: Optional[dict] = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.save_count = 0
        self.last_flush_time = time.time()

        self.redis_store = None
        self.db_store = None

        if self.config.get("save_to_redis"):
            self.redis_store = RedisStore(self.config)
        if self.config.get("save_to_db"):
            self.db_store = DatabaseStore(self.config)

        logger.info(f"MarketSaver 初始化完成 (Redis={'是' if self.redis_store else '否'}, DB={'是' if self.db_store else '否'})")

    def save(self, msg):
        """保存行情数据 (通用入口)"""
        serialized = event_to_dict(msg)
        if serialized is None:
            return

        if self.redis_store:
            self.redis_store.save(serialized)
        if self.db_store:
            self.db_store.save(serialized)
        self.save_count += 1

    def on_trade(self, msg):
        if isinstance(msg, dict) and msg.get("e") == "trade":
            self.save(msg)

    def on_agg_trade(self, msg):
        if isinstance(msg, dict) and msg.get("e") == "aggTrade":
            self.save(msg)

    def on_kline(self, msg):
        if isinstance(msg, dict) and msg.get("e") == "kline":
            self.save(msg)

    def on_mini_ticker(self, msg):
        if isinstance(msg, dict) and msg.get("e") == "24hrMiniTicker":
            self.save(msg)

    def on_ticker(self, msg):
        if isinstance(msg, dict) and msg.get("e") == "24hrTicker":
            self.save(msg)

    def on_book_ticker(self, msg):
        if isinstance(msg, dict) and "u" in msg and "b" in msg and "a" in msg:
            self.save(msg)

    def on_avg_price(self, msg):
        if isinstance(msg, dict) and msg.get("e") == "avgPrice":
            self.save(msg)

    def on_depth(self, msg):
        if isinstance(msg, dict) and msg.get("e") == "depthUpdate":
            self.save(msg)

    def on_partial_depth(self, msg):
        if isinstance(msg, dict) and "lastUpdateId" in msg and "bids" in msg:
            self.save(msg)

    def on_window_ticker(self, msg):
        if isinstance(msg, dict) and msg.get("e", "").endswith("Ticker"):
            self.save(msg)

    def on_ref_price(self, msg):
        if isinstance(msg, dict) and msg.get("e") == "referencePrice":
            self.save(msg)

    def on_all_mini_ticker(self, msg):
        if isinstance(msg, list):
            self.save(msg)

    def close(self):
        if self.redis_store:
            self.redis_store.close()
        if self.db_store:
            self.db_store.close()
        logger.info(f"MarketSaver 已关闭, 共保存 {self.save_count} 条数据")


# ============================================================
# 行情类型定义 (用于交互式工具)
# ============================================================

MARKET_TYPES = {
    "1":  {"name": "逐笔交易",         "key": "trade",          "desc": "实时推送每一笔成交"},
    "2":  {"name": "归集交易",         "key": "aggTrade",       "desc": "同一价格的多笔成交归集"},
    "3":  {"name": "K线(1分钟)",       "key": "kline_1m",       "desc": "每秒推送1分钟K线更新"},
    "4":  {"name": "K线(5分钟)",       "key": "kline_5m",       "desc": "每秒推送5分钟K线更新"},
    "5":  {"name": "K线(1小时)",       "key": "kline_1h",       "desc": "每2秒推送1小时K线更新"},
    "6":  {"name": "K线(1天)",         "key": "kline_1d",       "desc": "每2秒推送日K线更新"},
    "7":  {"name": "精简Ticker",       "key": "miniTicker",     "desc": "24小时精简行情(1秒)"},
    "8":  {"name": "完整Ticker",       "key": "ticker",         "desc": "24小时完整行情(1秒)"},
    "9":  {"name": "最优挂单",         "key": "bookTicker",     "desc": "实时最优买卖挂单"},
    "10": {"name": "平均价格",         "key": "avgPrice",       "desc": "5分钟平均价格(1秒)"},
    "11": {"name": "增量深度",         "key": "depth",          "desc": "订单簿增量变化(1秒)"},
    "12": {"name": "增量深度(100ms)",  "key": "depth_100ms",    "desc": "订单簿增量变化(100ms)"},
    "13": {"name": "有限深度(5档)",    "key": "depth5",         "desc": "5档买卖盘口(1秒)"},
    "14": {"name": "有限深度(20档)",   "key": "depth20",        "desc": "20档买卖盘口(1秒)"},
    "15": {"name": "滚动统计(1小时)",  "key": "ticker_1h",      "desc": "1小时滚动窗口统计"},
    "16": {"name": "滚动统计(1天)",    "key": "ticker_1d",      "desc": "1天滚动窗口统计"},
    "17": {"name": "参考价格",         "key": "referencePrice", "desc": "参考价格(1秒)"},
    "18": {"name": "全市场行情",       "key": "all_market",     "desc": "所有交易对精简行情"},
    "19": {"name": "全部行情",         "key": "all",            "desc": "订阅所有行情类型"},
}

SOCKET_MAP = {
    "trade":          "trade",
    "aggTrade":       "aggTrade",
    "kline_1m":       "kline_1m",
    "kline_5m":       "kline_5m",
    "kline_1h":       "kline_1h",
    "kline_1d":       "kline_1d",
    "miniTicker":     "miniTicker",
    "ticker":         "ticker",
    "bookTicker":     "bookTicker",
    "avgPrice":       "avgPrice",
    "depth":          "depth",
    "depth_100ms":    "depth@100ms",
    "depth5":         "depth5",
    "depth20":        "depth20",
    "ticker_1h":      "ticker_1h",
    "ticker_1d":      "ticker_1d",
    "referencePrice": "referencePrice",
}


def get_stream_and_callback(symbol: str, market_key: str, saver: MarketSaver):
    """根据行情类型获取 stream 名称和回调函数"""
    callback_map = {
        "trade":          saver.on_trade,
        "aggTrade":       saver.on_agg_trade,
        "kline_1m":       saver.on_kline,
        "kline_5m":       saver.on_kline,
        "kline_1h":       saver.on_kline,
        "kline_1d":       saver.on_kline,
        "miniTicker":     saver.on_mini_ticker,
        "ticker":         saver.on_ticker,
        "bookTicker":     saver.on_book_ticker,
        "avgPrice":       saver.on_avg_price,
        "depth":          saver.on_depth,
        "depth_100ms":    saver.on_depth,
        "depth5":         saver.on_partial_depth,
        "depth20":        saver.on_partial_depth,
        "ticker_1h":      saver.on_window_ticker,
        "ticker_1d":      saver.on_window_ticker,
        "referencePrice": saver.on_ref_price,
    }

    socket_type = SOCKET_MAP.get(market_key)
    callback = callback_map.get(market_key)
    return (socket_type, callback) if socket_type and callback else (None, None)


def get_all_streams_and_callbacks(symbol: str, saver: MarketSaver):
    """获取所有行情类型的 streams 和 callbacks"""
    streams = []
    callbacks = {}

    items = [
        ("trade", saver.on_trade),
        ("aggTrade", saver.on_agg_trade),
        ("kline_1m", saver.on_kline),
        ("miniTicker", saver.on_mini_ticker),
        ("ticker", saver.on_ticker),
        ("bookTicker", saver.on_book_ticker),
        ("avgPrice", saver.on_avg_price),
        ("depth", saver.on_depth),
        ("depth5", saver.on_partial_depth),
        ("ticker_1h", saver.on_window_ticker),
        ("referencePrice", saver.on_ref_price),
    ]

    for key, cb in items:
        stream, _ = get_stream_and_callback(symbol, key, saver)
        if stream:
            streams.append(stream)
            callbacks[stream] = cb

    return streams, callbacks


# ============================================================
# 便捷函数: 运行实时行情显示
# ============================================================

def fmt_time(ts: int) -> str:
    """格式化时间戳"""
    return datetime.fromtimestamp(ts / 1000).strftime("%H:%M:%S.%f")[:-3]


def fmt_price(val: str) -> str:
    """格式化价格"""
    f = float(val)
    if f >= 1000:
        return f"{f:.2f}"
    elif f >= 1:
        return f"{f:.4f}"
    else:
        return f"{f:.8f}"


def fmt_qty(val: str) -> str:
    """格式化数量"""
    f = float(val)
    if f >= 1000:
        return f"{f:.2f}"
    elif f >= 1:
        return f"{f:.4f}"
    elif f >= 0.001:
        return f"{f:.6f}"
    else:
        return f"{f:.8f}"


class MarketPrinter:
    """行情数据格式化打印"""

    @staticmethod
    def on_trade(msg):
        sp(f"[{fmt_time(msg['T'])}] [{msg['s']}] 逐笔交易 #{msg['t']} "
           f"价格:{fmt_price(msg['p'])} 数量:{fmt_qty(msg['q'])} "
           f"{'🟢买入' if not msg['m'] else '🔴卖出'}")

    @staticmethod
    def on_agg_trade(msg):
        sp(f"[{fmt_time(msg['T'])}] [{msg['s']}] 归集交易 #{msg['a']} "
           f"价格:{fmt_price(msg['p'])} 数量:{fmt_qty(msg['q'])} "
           f"{'🟢买入' if not msg['m'] else '🔴卖出'}")

    @staticmethod
    def on_kline(msg):
        k = msg["k"]
        status = "✅" if k["x"] else "⏳"
        sp(f"[{fmt_time(msg['E'])}] [{k['s']}] K线({k['i']}) {status} "
           f"开:{fmt_price(k['o'])} 高:{fmt_price(k['h'])} "
           f"低:{fmt_price(k['l'])} 收:{fmt_price(k['c'])} "
           f"量:{fmt_qty(k['v'])} 笔:{k['n']}")

    @staticmethod
    def on_mini_ticker(msg):
        change = ((float(msg['c']) - float(msg['o'])) / float(msg['o']) * 100
                  if float(msg['o']) > 0 else 0)
        arrow = "🟢" if change >= 0 else "🔴"
        sp(f"[{fmt_time(msg['E'])}] [{msg['s']}] 精简Ticker "
           f"最新:{fmt_price(msg['c'])} "
           f"24h高:{fmt_price(msg['h'])} 24h低:{fmt_price(msg['l'])} "
           f"24h涨幅:{change:+.2f}% {arrow}")

    @staticmethod
    def on_ticker(msg):
        sp(f"[{fmt_time(msg['E'])}] [{msg['s']}] 完整Ticker "
           f"最新:{fmt_price(msg['c'])} "
           f"涨跌:{msg['p']} 涨幅:{msg['P']}% "
           f"最高:{fmt_price(msg['h'])} 最低:{fmt_price(msg['l'])} "
           f"成交量:{fmt_qty(msg['v'])} 成交额:{fmt_qty(msg['q'])}")

    @staticmethod
    def on_book_ticker(msg):
        spread = float(msg['a']) - float(msg['b'])
        spread_pct = spread / float(msg['b']) * 100 if float(msg['b']) > 0 else 0
        sp(f"[{msg['s']}] 最优挂单 "
           f"买一:{fmt_price(msg['b'])} ({fmt_qty(msg['B'])}) "
           f"卖一:{fmt_price(msg['a'])} ({fmt_qty(msg['A'])}) "
           f"价差:{fmt_price(str(spread))} ({spread_pct:.4f}%)")

    @staticmethod
    def on_avg_price(msg):
        sp(f"[{fmt_time(msg['E'])}] [{msg['s']}] 平均价格({msg['i']}) "
           f"{fmt_price(msg['w'])}")

    @staticmethod
    def on_depth(msg):
        bids = msg.get("b", [])
        asks = msg.get("a", [])
        best_bid = float(bids[0][0]) if bids else 0
        best_ask = float(asks[0][0]) if asks else 0
        spread = best_ask - best_bid
        spread_pct = spread / best_bid * 100 if best_bid > 0 else 0
        sp(f"[{fmt_time(msg['E'])}] [{msg['s']}] 深度更新 "
           f"买一:{fmt_price(str(best_bid))} 卖一:{fmt_price(str(best_ask))} "
           f"价差:{fmt_price(str(spread))} ({spread_pct:.4f}%) "
           f"变动:买{len(bids)}档 卖{len(asks)}档 "
           f"更新ID:{msg['u']}")

    @staticmethod
    def on_partial_depth(msg):
        bids = msg.get("bids", [])
        asks = msg.get("asks", [])
        best_bid = float(bids[0][0]) if bids else 0
        best_ask = float(asks[0][0]) if asks else 0
        spread = best_ask - best_bid
        sp(f"[有限深度] 买一:{fmt_price(str(best_bid))} 卖一:{fmt_price(str(best_ask))} "
           f"价差:{fmt_price(str(spread))} "
           f"买{len(bids)}档 卖{len(asks)}档 "
           f"更新ID:{msg.get('lastUpdateId', '')}")

    @staticmethod
    def on_window_ticker(msg):
        sp(f"[{fmt_time(msg['E'])}] [{msg['s']}] 滚动统计({msg['e']}) "
           f"最新:{fmt_price(msg['c'])} 涨幅:{msg['P']}% "
           f"最高:{fmt_price(msg['h'])} 最低:{fmt_price(msg['l'])}")

    @staticmethod
    def on_ref_price(msg):
        sp(f"[{fmt_time(msg['t'])}] [{msg['s']}] 参考价格 "
           f"{msg.get('r', '无')}")

    @staticmethod
    def on_all_mini_ticker(msg):
        if isinstance(msg, list):
            sp(f"\n=== 全市场行情 ({len(msg)}个交易对) ===")
            sorted_data = sorted(
                [t for t in msg if isinstance(t, dict)],
                key=lambda x: float(x.get('q', '0') or '0'),
                reverse=True
            )
            for t in sorted_data[:20]:
                symbol = t.get('s', '')
                price = t.get('c', '')
                vol = t.get('q', '')
                sp(f"  {symbol:12s} 最新:{fmt_price(price):>12s}  成交额:{fmt_qty(vol):>12s}")
            sp(f"================================")


# 全局 print 别名
sp = print


def run_market(symbol: str, market_key: str, duration: int = 30):
    """
    运行实时行情显示

    Args:
        symbol: 交易对 (如 "btcusdt")
        market_key: 行情类型 key
        duration: 运行时长(秒)
    """
    symbol_upper = symbol.upper()

    sp(f"\n{'='*60}")
    sp(f"  币安行情 - {symbol_upper}")
    sp(f"{'='*60}")

    twm = ThreadedWebsocketManager()
    twm.start()

    try:
        if market_key == "all_market":
            sp(f"  订阅: 全市场行情")
            sp(f"  运行: {duration}秒")
            sp(f"{'='*60}\n")
            twm.start_miniticker_socket(callback=MarketPrinter.on_all_mini_ticker)

        elif market_key == "all":
            market_name = "全部行情"
            sp(f"  订阅: {market_name}")
            sp(f"  交易对: {symbol_upper}")
            sp(f"  运行: {duration}秒")
            sp(f"  Ctrl+C 提前结束")
            sp(f"{'='*60}\n")

            for key in ["trade", "aggTrade", "kline_1m", "miniTicker", "ticker",
                        "bookTicker", "avgPrice", "depth", "depth5",
                        "ticker_1h", "referencePrice"]:
                socket_name = SOCKET_MAP.get(key)
                callback = CALLBACK_MAP.get(key)
                if socket_name and callback:
                    twm.start_symbol_ticker_socket(
                        callback=callback,
                        symbol=symbol_upper,
                    ) if key == "ticker" else \
                    twm.start_symbol_miniticker_socket(
                        callback=callback,
                        symbol=symbol_upper,
                    ) if key == "miniTicker" else \
                    twm.start_kline_socket(
                        callback=callback,
                        symbol=symbol_upper,
                        interval=BinanceClient.KLINE_INTERVAL_1MINUTE,
                    ) if key == "kline_1m" else \
                    twm.start_trade_socket(
                        callback=callback,
                        symbol=symbol_upper,
                    ) if key == "trade" else \
                    twm.start_aggtrade_socket(
                        callback=callback,
                        symbol=symbol_upper,
                    ) if key == "aggTrade" else \
                    twm.start_book_ticker_socket(
                        callback=callback,
                        symbol=symbol_upper,
                    ) if key == "bookTicker" else \
                    twm.start_depth_socket(
                        callback=callback,
                        symbol=symbol_upper,
                    ) if key == "depth" else \
                    twm.start_depth_socket(
                        callback=callback,
                        symbol=symbol_upper,
                        depth=BinanceClient.DEPTH_5,
                    ) if key == "depth5" else \
                    twm.start_symbol_ticker_socket(
                        callback=callback,
                        symbol=symbol_upper,
                    )

        else:
            socket_name = SOCKET_MAP.get(market_key)
            callback = CALLBACK_MAP.get(market_key)
            if not socket_name or not callback:
                sp(f"❌ 不支持的行情类型: {market_key}")
                return

            market_name = MARKET_TYPES.get(
                next((k for k, v in MARKET_TYPES.items() if v["key"] == market_key), ""),
                {}
            ).get("name", market_key)

            sp(f"  订阅: {market_name}")
            sp(f"  交易对: {symbol_upper}")
            sp(f"  运行: {duration}秒")
            sp(f"  Ctrl+C 提前结束")
            sp(f"{'='*60}\n")

            if market_key == "trade":
                twm.start_trade_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "aggTrade":
                twm.start_aggtrade_socket(callback=callback, symbol=symbol_upper)
            elif market_key.startswith("kline_"):
                interval_map = {
                    "kline_1m": BinanceClient.KLINE_INTERVAL_1MINUTE,
                    "kline_5m": BinanceClient.KLINE_INTERVAL_5MINUTE,
                    "kline_1h": BinanceClient.KLINE_INTERVAL_1HOUR,
                    "kline_1d": BinanceClient.KLINE_INTERVAL_1DAY,
                }
                twm.start_kline_socket(
                    callback=callback,
                    symbol=symbol_upper,
                    interval=interval_map.get(market_key, BinanceClient.KLINE_INTERVAL_1MINUTE),
                )
            elif market_key == "miniTicker":
                twm.start_symbol_miniticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "ticker":
                twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "bookTicker":
                twm.start_book_ticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "avgPrice":
                twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "depth":
                twm.start_depth_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "depth_100ms":
                twm.start_depth_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "depth5":
                twm.start_depth_socket(callback=callback, symbol=symbol_upper, depth=BinanceClient.DEPTH_5)
            elif market_key == "depth20":
                twm.start_depth_socket(callback=callback, symbol=symbol_upper, depth=BinanceClient.DEPTH_20)
            elif market_key in ("ticker_1h", "ticker_1d"):
                twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "referencePrice":
                twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)

        time.sleep(duration)

    except KeyboardInterrupt:
        sp("\n\n⏹ 用户中断")
    finally:
        twm.stop()
        sp(f"\n{'='*60}")
        sp(f"  行情获取结束")
        sp(f"{'='*60}")


# 回调映射 (用于 run_market)
CALLBACK_MAP = {
    "trade":          MarketPrinter.on_trade,
    "aggTrade":       MarketPrinter.on_agg_trade,
    "kline_1m":       MarketPrinter.on_kline,
    "kline_5m":       MarketPrinter.on_kline,
    "kline_1h":       MarketPrinter.on_kline,
    "kline_1d":       MarketPrinter.on_kline,
    "miniTicker":     MarketPrinter.on_mini_ticker,
    "ticker":         MarketPrinter.on_ticker,
    "bookTicker":     MarketPrinter.on_book_ticker,
    "avgPrice":       MarketPrinter.on_avg_price,
    "depth":          MarketPrinter.on_depth,
    "depth_100ms":    MarketPrinter.on_depth,
    "depth5":         MarketPrinter.on_partial_depth,
    "depth20":        MarketPrinter.on_partial_depth,
    "ticker_1h":      MarketPrinter.on_window_ticker,
    "ticker_1d":      MarketPrinter.on_window_ticker,
    "referencePrice": MarketPrinter.on_ref_price,
}


def run_saver(symbol: str, market_key: str, duration: int = 30):
    """
    运行行情数据持久化

    Args:
        symbol: 交易对 (如 "btcusdt")
        market_key: 行情类型 key
        duration: 运行时长(秒)
    """
    symbol_upper = symbol.upper()
    saver = MarketSaver()

    print(f"\n{'='*60}")
    print(f"  币安行情持久化 - {symbol_upper}")
    print(f"{'='*60}")

    twm = ThreadedWebsocketManager()
    twm.start()

    try:
        if market_key == "all_market":
            print(f"  订阅: 全市场行情")
            print(f"  运行: {duration}秒")
            print(f"{'='*60}\n")
            twm.start_miniticker_socket(callback=saver.on_all_mini_ticker)

        elif market_key == "all":
            print(f"  订阅: 全部行情")
            print(f"  交易对: {symbol_upper}")
            print(f"  运行: {duration}秒")
            print(f"  Ctrl+C 提前结束")
            print(f"{'='*60}\n")

            for key in ["trade", "aggTrade", "kline_1m", "miniTicker", "ticker",
                        "bookTicker", "avgPrice", "depth", "depth5",
                        "ticker_1h", "referencePrice"]:
                socket_type, callback = get_stream_and_callback(symbol, key, saver)
                if socket_type and callback:
                    if key == "trade":
                        twm.start_trade_socket(callback=callback, symbol=symbol_upper)
                    elif key == "aggTrade":
                        twm.start_aggtrade_socket(callback=callback, symbol=symbol_upper)
                    elif key == "kline_1m":
                        twm.start_kline_socket(callback=callback, symbol=symbol_upper, interval=BinanceClient.KLINE_INTERVAL_1MINUTE)
                    elif key == "miniTicker":
                        twm.start_symbol_miniticker_socket(callback=callback, symbol=symbol_upper)
                    elif key == "ticker":
                        twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)
                    elif key == "bookTicker":
                        twm.start_book_ticker_socket(callback=callback, symbol=symbol_upper)
                    elif key == "avgPrice":
                        twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)
                    elif key == "depth":
                        twm.start_depth_socket(callback=callback, symbol=symbol_upper)
                    elif key == "depth5":
                        twm.start_depth_socket(callback=callback, symbol=symbol_upper, depth=BinanceClient.DEPTH_5)
                    elif key == "ticker_1h":
                        twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)
                    elif key == "referencePrice":
                        twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)

        else:
            socket_type, callback = get_stream_and_callback(symbol, market_key, saver)
            if not socket_type or not callback:
                print(f"❌ 不支持的行情类型: {market_key}")
                return

            market_name = MARKET_TYPES.get(
                next((k for k, v in MARKET_TYPES.items() if v["key"] == market_key), ""),
                {}
            ).get("name", market_key)

            print(f"  订阅: {market_name}")
            print(f"  交易对: {symbol_upper}")
            print(f"  运行: {duration}秒")
            print(f"  Ctrl+C 提前结束")
            print(f"{'='*60}\n")

            if market_key == "trade":
                twm.start_trade_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "aggTrade":
                twm.start_aggtrade_socket(callback=callback, symbol=symbol_upper)
            elif market_key.startswith("kline_"):
                interval_map = {
                    "kline_1m": BinanceClient.KLINE_INTERVAL_1MINUTE,
                    "kline_5m": BinanceClient.KLINE_INTERVAL_5MINUTE,
                    "kline_1h": BinanceClient.KLINE_INTERVAL_1HOUR,
                    "kline_1d": BinanceClient.KLINE_INTERVAL_1DAY,
                }
                twm.start_kline_socket(
                    callback=callback,
                    symbol=symbol_upper,
                    interval=interval_map.get(market_key, BinanceClient.KLINE_INTERVAL_1MINUTE),
                )
            elif market_key == "miniTicker":
                twm.start_symbol_miniticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "ticker":
                twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "bookTicker":
                twm.start_book_ticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "avgPrice":
                twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "depth":
                twm.start_depth_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "depth_100ms":
                twm.start_depth_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "depth5":
                twm.start_depth_socket(callback=callback, symbol=symbol_upper, depth=BinanceClient.DEPTH_5)
            elif market_key == "depth20":
                twm.start_depth_socket(callback=callback, symbol=symbol_upper, depth=BinanceClient.DEPTH_20)
            elif market_key in ("ticker_1h", "ticker_1d"):
                twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "referencePrice":
                twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)

        time.sleep(duration)

    except KeyboardInterrupt:
        print("\n\n⏹ 用户中断")
    finally:
        twm.stop()
        saver.close()
        print(f"\n{'='*60}")
        print(f"  行情持久化结束")
        print(f"{'='*60}")
