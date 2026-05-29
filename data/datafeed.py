# -*- coding: utf-8 -*-
"""
数据馈送模块
===========
负责从各种数据源（本地数据库、CSV、API）加载K线数据，
并提供统一的数据访问接口。
"""

import os
import csv
import json
import sqlite3
import logging
from typing import List, Optional, Dict, Tuple
from datetime import datetime
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class Bar:
    """单根K线数据"""
    time: int          # 开盘时间戳 (ms)
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int    # 收盘时间戳 (ms)
    quote_volume: float = 0.0
    trade_count: int = 0


class DataFeed:
    """
    数据馈送器

    支持多种数据源:
    1. SQLite 数据库 (market_data.db)
    2. CSV 文件
    3. JSON 文件
    4. 模拟生成数据

    提供统一的 K线数据访问接口。
    """

    def __init__(self):
        self._bars: List[Bar] = []
        self._symbol: str = ""
        self._interval: str = ""
        self._loaded = False

    @property
    def bars(self) -> List[Bar]:
        return self._bars

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def interval(self) -> str:
        return self._interval

    @property
    def loaded(self) -> bool:
        return self._loaded

    def __len__(self) -> int:
        return len(self._bars)

    # ---- 数据加载方法 ----

    def load_from_db(self, db_path: str, symbol: str, interval: str,
                     start_time: Optional[str] = None,
                     end_time: Optional[str] = None) -> bool:
        """
        从 SQLite 数据库加载K线数据

        Args:
            db_path: 数据库文件路径
            symbol: 交易对
            interval: K线周期
            start_time: 开始日期 (YYYY-MM-DD)
            end_time: 结束日期 (YYYY-MM-DD)

        Returns:
            是否成功加载
        """
        if not os.path.exists(db_path):
            logger.error(f"数据库文件不存在: {db_path}")
            return False

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # 查询表结构
            cursor.execute("PRAGMA table_info(market_klines)")
            columns = [col[1] for col in cursor.fetchall()]
            if not columns:
                logger.warning("数据库中没有 market_klines 表")
                conn.close()
                return False

            # 构建查询
            query = "SELECT * FROM market_klines WHERE symbol=? AND interval_type=?"
            params = [symbol.upper(), interval]

            if start_time:
                try:
                    dt = datetime.strptime(start_time, "%Y-%m-%d")
                    start_ms = int(dt.timestamp() * 1000)
                    query += " AND start_time>=?"
                    params.append(start_ms)
                except ValueError:
                    pass

            if end_time:
                try:
                    dt = datetime.strptime(end_time, "%Y-%m-%d")
                    end_ms = int(dt.timestamp() * 1000) + 24 * 60 * 60 * 1000 - 1
                    query += " AND start_time<=?"
                    params.append(end_ms)
                except ValueError:
                    pass

            query += " ORDER BY start_time ASC"
            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                logger.warning(f"数据库中没有匹配的K线数据: {symbol} {interval}")
                return False

            # 转换为 Bar 对象
            self._bars = []
            for row in rows:
                row_dict = dict(zip(columns, row))
                self._bars.append(Bar(
                    time=row_dict.get("start_time", 0),
                    open=float(row_dict.get("open_price", 0)),
                    high=float(row_dict.get("high_price", 0)),
                    low=float(row_dict.get("low_price", 0)),
                    close=float(row_dict.get("close_price", 0)),
                    volume=float(row_dict.get("volume", 0)),
                    close_time=row_dict.get("close_time", 0),
                    quote_volume=float(row_dict.get("quote_volume", 0)),
                    trade_count=row_dict.get("trade_count", 0),
                ))

            self._symbol = symbol.lower()
            self._interval = interval
            self._loaded = True
            logger.info(f"从数据库加载了 {len(self._bars)} 根K线 ({symbol} {interval})")
            return True

        except Exception as e:
            logger.error(f"从数据库加载K线失败: {e}")
            return False

    def load_from_csv(self, filepath: str, symbol: str = "", interval: str = "") -> bool:
        """
        从 CSV 文件加载K线数据

        CSV 格式: time,open,high,low,close,volume
        """
        if not os.path.exists(filepath):
            logger.error(f"CSV文件不存在: {filepath}")
            return False

        try:
            self._bars = []
            with open(filepath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    self._bars.append(Bar(
                        time=int(row.get("time", 0)),
                        open=float(row.get("open", 0)),
                        high=float(row.get("high", 0)),
                        low=float(row.get("low", 0)),
                        close=float(row.get("close", 0)),
                        volume=float(row.get("volume", 0)),
                        close_time=int(row.get("close_time", 0)),
                    ))

            self._symbol = symbol
            self._interval = interval
            self._loaded = True
            logger.info(f"从CSV加载了 {len(self._bars)} 根K线")
            return True

        except Exception as e:
            logger.error(f"从CSV加载K线失败: {e}")
            return False

    def load_from_json(self, filepath: str, symbol: str = "", interval: str = "") -> bool:
        """从 JSON 文件加载K线数据"""
        if not os.path.exists(filepath):
            logger.error(f"JSON文件不存在: {filepath}")
            return False

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._bars = []
            for item in data:
                self._bars.append(Bar(
                    time=int(item.get("time", 0)),
                    open=float(item.get("open", 0)),
                    high=float(item.get("high", 0)),
                    low=float(item.get("low", 0)),
                    close=float(item.get("close", 0)),
                    volume=float(item.get("volume", 0)),
                    close_time=int(item.get("close_time", 0)),
                ))

            self._symbol = symbol
            self._interval = interval
            self._loaded = True
            logger.info(f"从JSON加载了 {len(self._bars)} 根K线")
            return True

        except Exception as e:
            logger.error(f"从JSON加载K线失败: {e}")
            return False

    def generate_mock_data(self, symbol: str, interval: str, count: int = 500,
                           start_price: float = 50000.0, volatility: float = 0.005) -> bool:
        """
        生成模拟K线数据（用于测试）

        Args:
            symbol: 交易对
            interval: K线周期
            count: K线数量
            start_price: 起始价格
            volatility: 波动率
        """
        import random
        import time
        random.seed(42)

        price = start_price
        now = int(time.time() * 1000)
        interval_ms = self._interval_to_ms(interval)

        self._bars = []
        for i in range(count):
            price *= (1 + random.gauss(0, volatility))
            high = price * (1 + abs(random.gauss(0, volatility * 0.6)))
            low = price * (1 - abs(random.gauss(0, volatility * 0.6)))
            self._bars.append(Bar(
                time=now - (count - i) * interval_ms,
                open=price * 0.99,
                high=high,
                low=low,
                close=price,
                volume=100.0 + random.random() * 50,
                close_time=now - (count - i) * interval_ms + interval_ms,
            ))

        self._symbol = symbol.lower()
        self._interval = interval
        self._loaded = True
        logger.info(f"生成了 {len(self._bars)} 根模拟K线 ({symbol} {interval})")
        return True

    # ---- 数据访问方法 ----

    def get_opens(self) -> List[float]:
        return [b.open for b in self._bars]

    def get_highs(self) -> List[float]:
        return [b.high for b in self._bars]

    def get_lows(self) -> List[float]:
        return [b.low for b in self._bars]

    def get_closes(self) -> List[float]:
        return [b.close for b in self._bars]

    def get_volumes(self) -> List[float]:
        return [b.volume for b in self._bars]

    def get_times(self) -> List[int]:
        return [b.time for b in self._bars]

    def get_slice(self, start: int, end: int) -> List[Bar]:
        """获取K线切片"""
        return self._bars[start:end]

    def get_range(self, start_time: int, end_time: int) -> List[Bar]:
        """获取时间范围内的K线"""
        return [b for b in self._bars if start_time <= b.time <= end_time]

    # ---- 辅助方法 ----

    @staticmethod
    def _interval_to_ms(interval: str) -> int:
        """将K线周期转换为毫秒"""
        unit = interval[-1]
        value = int(interval[:-1]) if len(interval) > 1 else 1
        multipliers = {
            "m": 60 * 1000,
            "h": 60 * 60 * 1000,
            "d": 24 * 60 * 60 * 1000,
            "w": 7 * 24 * 60 * 60 * 1000,
        }
        return value * multipliers.get(unit, 60 * 1000)

    def clear(self):
        """清空数据"""
        self._bars.clear()
        self._symbol = ""
        self._interval = ""
        self._loaded = False
