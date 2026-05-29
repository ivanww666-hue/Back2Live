# -*- coding: utf-8 -*-
"""
三均线趋势策略
=============
使用快、中、慢三条 EMA 判断趋势方向和强度。

规则:
  - 买入: EMA快 > EMA中 > EMA慢 (多头排列) + 价格在快线上方
  - 卖出: EMA快 < EMA中 或 EMA快 < EMA慢 (趋势转弱)
"""

from typing import Optional
from data.datafeed import Bar
from indicator.ta import TA
from strategy.base_strategy import BaseStrategy, Signal


class TripleMAStrategy(BaseStrategy):
    """
    三均线趋势策略（现货）

    配置参数:
        ema_fast:   快速 EMA 周期 (默认 10)
        ema_medium: 中速 EMA 周期 (默认 30)
        ema_slow:   慢速 EMA 周期 (默认 60)
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self.ema_fast = self.config.get("ema_fast", 10)
        self.ema_medium = self.config.get("ema_medium", 30)
        self.ema_slow = self.config.get("ema_slow", 60)
        self.min_bars = max(self.ema_fast, self.ema_medium, self.ema_slow, 20)
        self._position_count = 0

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        self.update_bar(bar)

        if self.bars_count < self.min_bars:
            return None

        closes = self._closes
        current_price = closes[-1]
        current_time = self._times[-1]

        fast = TA.ema(closes, self.ema_fast)
        medium = TA.ema(closes, self.ema_medium)
        slow = TA.ema(closes, self.ema_slow)

        f = fast[-1]
        m = medium[-1]
        s = slow[-1]

        self._cached_indicators = {
            "ema_fast": f,
            "ema_medium": m,
            "ema_slow": s,
        }

        signal = None

        # ---- 出场: 多头排列消失（仅持有仓位时检查） ----
        if self._position_count > 0:
            if not (f > m > s):
                signal = Signal(
                    action="SELL", price=current_price,
                    timestamp=current_time,
                    reason=f"多头排列消失 (EMA{self.ema_fast}={f:.2f} "
                           f"EMA{self.ema_medium}={m:.2f} EMA{self.ema_slow}={s:.2f})",
                    confidence=0.65,
                    indicators=self._cached_indicators.copy(),
                )

        # ---- 入场: 多头排列 + 价格在快线上方 ----
        if signal is None and f > m > s and current_price > f:
            signal = Signal(
                action="BUY", price=current_price,
                timestamp=current_time,
                reason=f"多头排列 (EMA{self.ema_fast}>{self.ema_medium}>{self.ema_slow})",
                confidence=0.65,
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
