# -*- coding: utf-8 -*-
"""
策略基类模块
===========
定义策略的抽象基类，所有具体策略需继承此类并实现 on_bar 方法。
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from data.datafeed import Bar


@dataclass
class Signal:
    """交易信号"""
    action: str  # BUY, SELL, EXIT_LONG, EXIT_SHORT, NONE
    price: float
    timestamp: int
    reason: str = ""
    confidence: float = 0.0
    indicators: Dict[str, float] = field(default_factory=dict)
    order_type: str = "MARKET"  # MARKET or LIMIT — 策略可指定限价单


class BaseStrategy(ABC):
    """
    策略基类

    所有具体策略需继承此类并实现 on_bar 方法。

    属性:
        name: 策略名称
        symbol: 交易对
        interval: K线周期
        config: 策略配置
        bars_count: 已处理K线数
        signal_count: 信号计数
        last_signal: 最后一个信号
    """

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.name = self.config.get("name", self.__class__.__name__)
        self.symbol: str = ""
        self.interval: str = ""

        # 数据缓存
        self._times_data: List[int] = []
        self._opens_data: List[float] = []
        self._highs_data: List[float] = []
        self._lows_data: List[float] = []
        self._closes_data: List[float] = []
        self._volumes_data: List[float] = []

        # 状态
        self.bars_count = 0
        self.signal_count = 0
        self.last_signal: Optional[Signal] = None

        # 指标缓存
        self._cached_indicators: Dict[str, float] = {}

    @property
    def _times(self):
        return self._times_data

    @_times.setter
    def _times(self, value):
        self._times_data = value

    @property
    def _opens(self):
        return self._opens_data

    @_opens.setter
    def _opens(self, value):
        self._opens_data = value

    @property
    def _highs(self):
        return self._highs_data

    @_highs.setter
    def _highs(self, value):
        self._highs_data = value

    @property
    def _lows(self):
        return self._lows_data

    @_lows.setter
    def _lows(self, value):
        self._lows_data = value

    @property
    def _closes(self):
        return self._closes_data

    @_closes.setter
    def _closes(self, value):
        self._closes_data = value

    @property
    def _volumes(self):
        return self._volumes_data

    @_volumes.setter
    def _volumes(self, value):
        self._volumes_data = value

    def update_bar(self, bar: Bar):
        """更新K线数据"""
        self._times_data.append(bar.time)
        self._opens_data.append(bar.open)
        self._highs_data.append(bar.high)
        self._lows_data.append(bar.low)
        self._closes_data.append(bar.close)
        self._volumes_data.append(bar.volume)
        self.bars_count += 1

    @abstractmethod
    def on_bar(self, bar: Bar) -> Optional[Signal]:
        """
        K线回调 - 生成交易信号

        Args:
            bar: K线数据

        Returns:
            交易信号，None 表示无信号
        """
        pass

    def on_start(self):
        """策略启动回调"""
        pass

    def on_end(self):
        """策略结束回调"""
        pass

    def on_position_open(self, signal: Signal, price: float):
        """开仓回调"""
        pass

    def on_position_close(self, price: float, reason: str, pnl: float):
        """平仓回调"""
        pass

    def get_indicators(self) -> Dict[str, float]:
        """获取当前指标值"""
        return self._cached_indicators.copy()

    def reset(self):
        """重置策略状态"""
        self._times_data.clear()
        self._opens_data.clear()
        self._highs_data.clear()
        self._lows_data.clear()
        self._closes_data.clear()
        self._volumes_data.clear()
        self.bars_count = 0
        self.signal_count = 0
        self.last_signal = None
        self._cached_indicators.clear()
