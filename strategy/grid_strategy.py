# -*- coding: utf-8 -*-
"""
网格交易策略模块
==============
现货网格策略：在设定的价格区间内低买高卖，赚取网格利润。

配置参数:
    grid_low: 网格下限价格
    grid_high: 网格上限价格
    grid_count: 网格数量 (在 [low, high] 之间等分)
    position_per_grid_pct: 每格投入资金比例 (%)
"""

from typing import Optional, Dict, List
from data.datafeed import Bar
from strategy.base_strategy import BaseStrategy, Signal


class GridStrategy(BaseStrategy):
    """
    网格交易策略（现货）

    将 [grid_low, grid_high] 等分为 grid_count 个区间，
    每格投入 position_per_grid_pct% 资金。

    规则:
    - 价格触及网格下限时买入，触及上限时不操作
    - 买入后价格回到上一格时卖出
    - 跟踪每个网格的持仓状态
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)

        # 网格参数
        self.grid_low = self.config.get("grid_low", 10000.0)
        self.grid_high = self.config.get("grid_high", 60000.0)
        self.grid_count = self.config.get("grid_count", 20)
        self.position_per_grid_pct = self.config.get("position_per_grid_pct", 5.0)

        # 网格价格线
        self._grid_prices: List[float] = []

        # 跟踪每个网格级别的持仓状态: grid_index → bool (是否已买入)
        self._grid_filled: Dict[int, bool] = {}
        self._entry_prices: Dict[int, float] = {}  # grid_index → 买入价
        self._pending_buy_level: Optional[int] = None
        self._pending_sell_level: Optional[int] = None

        self._build_grid()

        # 前一根K线的收盘价（用于判断穿越网格线）
        self._prev_close: float = 0.0

        # 最小K线数要求
        self.min_bars = 1

    def _build_grid(self):
        """构建网格价格线"""
        step = (self.grid_high - self.grid_low) / self.grid_count
        self._grid_prices = []
        for i in range(self.grid_count + 1):
            self._grid_prices.append(self.grid_low + i * step)
            self._grid_filled[i] = False

    def _find_grid_level(self, price: float) -> int:
        """找到价格所在的网格区间索引 (0 ~ grid_count-1)"""
        if price <= self.grid_low:
            return 0
        if price >= self.grid_high:
            return self.grid_count - 1
        for i in range(self.grid_count):
            if self._grid_prices[i] <= price < self._grid_prices[i + 1]:
                return i
        return self.grid_count - 1

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        """K线回调 - 生成交易信号"""
        self.update_bar(bar)

        if self.bars_count < self.min_bars:
            return None

        current_price = bar.close
        current_time = self._times[-1]

        # 首次运行：根据当前价格初始化基准，跳过第一个bar
        if self._prev_close == 0:
            self._prev_close = current_price
            return None

        # 计算当前和前一根K线所在的网格级别
        prev_level = self._find_grid_level(self._prev_close)
        curr_level = self._find_grid_level(current_price)

        self._prev_close = current_price

        signal = None

        # ---- 卖出: 价格上升，穿过网格线到更高区间 ----
        if curr_level > prev_level:
            # 从当前级别向下查找有持仓的网格
            for level in range(curr_level - 1, prev_level - 1, -1):
                if self._grid_filled.get(level, False):
                    signal = Signal(
                        action="SELL",
                        price=current_price,
                        timestamp=current_time,
                        reason=f"网格卖出 (level={level}, grid_price={self._grid_prices[level]:.2f})",
                        confidence=1.0,
                        indicators={"grid_level": level, "grid_price": self._grid_prices[level]},
                    )
                    self._pending_sell_level = level
                    break

        # ---- 买入: 价格下跌，穿过网格线到更低区间 ----
        elif curr_level < prev_level:
            # 从 prev_level 向下到 curr_level 逐格买入
            for level in range(prev_level - 1, curr_level - 1, -1):
                if level < 0 or level >= self.grid_count:
                    continue
                if not self._grid_filled.get(level, False):
                    # 不在同一根 bar 产生多个信号，只取第一个
                    if signal is None:
                        signal = Signal(
                            action="BUY",
                            price=current_price,
                            timestamp=current_time,
                            reason=f"网格买入 (level={level}, grid_price={self._grid_prices[level]:.2f})",
                            confidence=1.0,
                            indicators={"grid_level": level, "grid_price": self._grid_prices[level]},
                        )
                        self._pending_buy_level = level

        if signal:
            self.last_signal = signal
            self.signal_count += 1

        return signal

    def on_position_open(self, signal: Signal, price: float):
        """开仓回调"""
        level = int(signal.indicators.get("grid_level", self._pending_buy_level or -1))
        if 0 <= level < self.grid_count:
            self._grid_filled[level] = True
            self._entry_prices[level] = price
        self._pending_buy_level = None

    def on_position_close(self, price: float, reason: str, pnl: float):
        """平仓回调"""
        level = self._pending_sell_level
        if level is not None and 0 <= level < self.grid_count:
            self._grid_filled[level] = False
            self._entry_prices.pop(level, None)
        self._pending_sell_level = None

    def reset(self):
        """重置策略状态"""
        super().reset()
        self._grid_filled = {i: False for i in range(self.grid_count + 1)}
        self._entry_prices.clear()
        self._pending_buy_level = None
        self._pending_sell_level = None
        self._prev_close = 0.0

    def get_indicators(self) -> Dict[str, float]:
        """获取当前指标值"""
        filled_count = sum(1 for v in self._grid_filled.values() if v)
        return {
            "grid_low": self.grid_low,
            "grid_high": self.grid_high,
            "grid_count": self.grid_count,
            "filled_grids": filled_count,
            "fill_pct": filled_count / self.grid_count * 100 if self.grid_count > 0 else 0,
        }
