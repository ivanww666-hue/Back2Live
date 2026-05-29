# -*- coding: utf-8 -*-
"""
均线斜率策略
============
使用 5、10、20 SMA 均线斜率判断趋势方向。

规则:
  - 买入: MA5、MA10、MA20 斜率同时向上（即当前值 > 前一根值）
  - 卖出:
    (1) MA5 从持仓期间最高价下跌 2%
    (2) 盈利达到 8% 止盈
    (3) 亏损达到 2% 止损
"""

from typing import Optional
from data.datafeed import Bar
from indicator.ta import TA
from strategy.base_strategy import BaseStrategy, Signal


class SlopeMAStrategy(BaseStrategy):
    """
    均线斜率策略（现货）

    配置参数:
        ma_fast:     快速 SMA 周期 (默认 5)
        ma_medium:   中速 SMA 周期 (默认 10)
        ma_slow:     慢速 SMA 周期 (默认 20)
        trail_drop:  MA5 回落平仓百分比 (默认 0.02 = 2%)
        take_profit: 止盈百分比 (默认 0.08 = 8%)
        stop_loss:   止损百分比 (默认 0.02 = 2%)
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.ma_fast = self.config.get("ma_fast", 5)
        self.ma_medium = self.config.get("ma_medium", 10)
        self.ma_slow = self.config.get("ma_slow", 20)
        self.trail_drop = self.config.get("trail_drop", 0.02)
        self.take_profit = self.config.get("take_profit", 0.08)
        self.stop_loss = self.config.get("stop_loss", 0.02)

        self.min_bars = max(self.ma_fast, self.ma_medium, self.ma_slow) + 1

        # 持仓状态跟踪
        self._in_position = False
        self._entry_price = 0.0
        self._highest_since_entry = 0.0

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        self.update_bar(bar)

        if self.bars_count < self.min_bars:
            return None

        closes = self._closes
        current_price = closes[-1]
        current_time = self._times[-1]

        # 计算 SMA
        fast = TA.sma(closes, self.ma_fast)
        medium = TA.sma(closes, self.ma_medium)
        slow = TA.sma(closes, self.ma_slow)

        f = fast[-1]
        m = medium[-1]
        s = slow[-1]

        # 前一根值（用于判断斜率方向）
        f_prev = fast[-2] if len(fast) >= 2 else f
        m_prev = medium[-2] if len(medium) >= 2 else m
        s_prev = slow[-2] if len(slow) >= 2 else s

        # 三均线斜率向上（当前值 > 前一根值）
        slopes_up = (f > f_prev) and (m > m_prev) and (s > s_prev)

        self._cached_indicators = {
            f"SMA{self.ma_fast}": f,
            f"SMA{self.ma_medium}": m,
            f"SMA{self.ma_slow}": s,
            f"SMA{self.ma_fast}_slope_up": 1.0 if f > f_prev else 0.0,
            f"SMA{self.ma_medium}_slope_up": 1.0 if m > m_prev else 0.0,
            f"SMA{self.ma_slow}_slope_up": 1.0 if s > s_prev else 0.0,
        }

        signal = None

        # ========== 出场判断（持仓时） ==========
        if self._in_position:
            # 更新持仓期间最高价（使用 MA5 值）
            if f > self._highest_since_entry:
                self._highest_since_entry = f

            # 止损: 价格从入场价下跌 stop_loss%
            if current_price <= self._entry_price * (1 - self.stop_loss):
                signal = Signal(
                    action="SELL", price=current_price,
                    timestamp=current_time,
                    reason=f"止损 {self.stop_loss*100:.0f}% "
                           f"(入场={self._entry_price:.2f}, 当前={current_price:.2f})",
                    confidence=0.9,
                    indicators=self._cached_indicators.copy(),
                )

            # 止盈: 价格从入场价上涨 take_profit%
            elif current_price >= self._entry_price * (1 + self.take_profit):
                signal = Signal(
                    action="SELL", price=current_price,
                    timestamp=current_time,
                    reason=f"止盈 {self.take_profit*100:.0f}% "
                           f"(入场={self._entry_price:.2f}, 当前={current_price:.2f})",
                    confidence=0.9,
                    indicators=self._cached_indicators.copy(),
                )

            # MA5 从持仓期间最高价回落 trail_drop%
            elif f <= self._highest_since_entry * (1 - self.trail_drop):
                signal = Signal(
                    action="SELL", price=current_price,
                    timestamp=current_time,
                    reason=f"MA{self.ma_fast}回落 {self.trail_drop*100:.0f}% "
                           f"(最高MA={self._highest_since_entry:.2f}, 当前MA={f:.2f})",
                    confidence=0.7,
                    indicators=self._cached_indicators.copy(),
                )

        # ========== 入场判断（空仓时） ==========
        if signal is None and not self._in_position:
            if slopes_up:
                signal = Signal(
                    action="BUY", price=current_price,
                    timestamp=current_time,
                    reason=f"三均线斜率向上 "
                           f"(SMA{self.ma_fast}={f:.2f} SMA{self.ma_medium}={m:.2f} "
                           f"SMA{self.ma_slow}={s:.2f})",
                    confidence=0.65,
                    indicators=self._cached_indicators.copy(),
                )

        if signal:
            self.last_signal = signal
            self.signal_count += 1
        return signal

    def on_position_open(self, signal: Signal, price: float):
        if signal.action == "BUY" and not self._in_position:
            self._in_position = True
            self._entry_price = price
            self._highest_since_entry = price

    def on_position_close(self, price: float, reason: str, pnl: float):
        self._in_position = False
        self._entry_price = 0.0
        self._highest_since_entry = 0.0

    def reset(self):
        super().reset()
        self._in_position = False
        self._entry_price = 0.0
        self._highest_since_entry = 0.0