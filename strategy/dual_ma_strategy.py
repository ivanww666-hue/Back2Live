# -*- coding: utf-8 -*-
"""
双均线交叉策略
=============
基于快慢两条 EMA 的金叉/死叉判断趋势方向。

规则:
  - 买入: 快线从下方上穿慢线 (金叉)
  - 卖出: 快线从上方下穿慢线 (死叉)
"""

from typing import Optional
from data.datafeed import Bar
from indicator.ta import TA
from strategy.base_strategy import BaseStrategy, Signal


class DualMAStrategy(BaseStrategy):
    """
    双均线交叉策略（现货）

    配置参数:
        ema_fast: 快速 EMA 周期 (默认 12)
        ema_slow: 慢速 EMA 周期 (默认 26)
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.ema_fast = self.config.get("ema_fast", 12)
        self.ema_slow = self.config.get("ema_slow", 26)
        self.min_bars = max(self.ema_fast, self.ema_slow, 20)
        self._position_count = 0

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        self.update_bar(bar)

        if self.bars_count < self.min_bars:
            return None

        closes = self._closes
        current_price = closes[-1]
        current_time = self._times[-1]

        fast = TA.ema(closes, self.ema_fast)
        slow = TA.ema(closes, self.ema_slow)

        fast_now = fast[-1]
        slow_now = slow[-1]
        fast_prev = fast[-2] if len(fast) > 1 else fast_now
        slow_prev = slow[-2] if len(slow) > 1 else slow_now

        self._cached_indicators = {
            "ema_fast": fast_now,
            "ema_slow": slow_now,
        }

        signal = None

        # ---- 死叉出场（仅持有仓位时检查） ----
        if self._position_count > 0:
            if fast_prev > slow_prev and fast_now < slow_now:
                signal = Signal(
                    action="SELL", price=current_price,
                    timestamp=current_time,
                    reason=f"死叉 (EMA{self.ema_fast}<EMA{self.ema_slow})",
                    confidence=0.7,
                    indicators=self._cached_indicators.copy(),
                )

        # ---- 金叉入场 ----
        if signal is None and fast_prev < slow_prev and fast_now > slow_now:
            signal = Signal(
                action="BUY", price=current_price,
                timestamp=current_time,
                reason=f"金叉 (EMA{self.ema_fast}>EMA{self.ema_slow})",
                confidence=0.7,
                indicators=self._cached_indicators.copy(),
            )

        if signal:
            self.last_signal = signal
            self.signal_count += 1
        return signal

    def on_position_open(self, signal: Signal, price: float):
        if signal.action == "BUY":
            self._position_count += 1

    def on_position_close(self, price: float, reason: str, pnl: float):
        self._position_count = max(0, self._position_count - 1)

    def reset(self):
        super().reset()
        self._position_count = 0
