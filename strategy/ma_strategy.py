# -*- coding: utf-8 -*-
"""
均线策略模块
===========
基于移动平均线交叉的趋势跟踪策略实现（现货交易）。

关键修复:
1. 入场信号 (BUY) 和出场信号 (SELL) 互斥 - 有持仓时只产生出场信号，无持仓时只产生入场信号
2. 使用持仓状态判断而非 last_signal 判断
3. 现货交易只有买入/卖出，无 SHORT/EXIT_LONG/EXIT_SHORT
"""

from typing import Optional, Dict, List
from data.datafeed import Bar
from indicator.ta import TA
from strategy.base_strategy import BaseStrategy, Signal


class MAStrategy(BaseStrategy):
    """
    均线交叉策略（现货）

    使用 EMA 均线系统判断趋势方向，结合 MACD 和 ADX 过滤信号。

    策略规则:
    - 买入: EMA12 > EMA26 > EMA50 (多头排列) AND MACD > 0 AND ADX > 25
    - 卖出: SuperTrend 反转 或 趋势转弱 或 止损/止盈
    """

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)

        # 策略参数
        self.ema_fast = self.config.get("ema_fast", 12)
        self.ema_medium = self.config.get("ema_medium", 26)
        self.ema_slow = self.config.get("ema_slow", 50)
        self.ema_trend = self.config.get("ema_trend", 200)
        self.macd_fast = self.config.get("macd_fast", 12)
        self.macd_slow = self.config.get("macd_slow", 26)
        self.macd_signal = self.config.get("macd_signal", 9)
        self.adx_period = self.config.get("adx_period", 14)
        self.adx_threshold = self.config.get("adx_threshold", 25)
        self.super_atr_period = self.config.get("super_atr_period", 10)
        self.super_multiplier = self.config.get("super_multiplier", 3.0)

        # 最小K线数要求
        self.min_bars = max(self.ema_slow, self.ema_trend, self.adx_period + 5, 60)

        # 持仓状态 — 支持连续加仓
        self.position_count = 0
        self._max_positions = self.config.get("max_positions", 3)

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        """K线回调 - 生成交易信号"""
        self.update_bar(bar)

        if self.bars_count < self.min_bars:
            return None

        return self._generate_signal()

    def _generate_signal(self) -> Optional[Signal]:
        """生成交易信号"""
        closes = self._closes
        highs = self._highs
        lows = self._lows
        current_price = closes[-1]
        current_time = self._times[-1]

        # 计算指标
        ema12 = TA.ema(closes, self.ema_fast)
        ema26 = TA.ema(closes, self.ema_medium)
        ema50 = TA.ema(closes, self.ema_slow)
        ema200 = TA.ema(closes, self.ema_trend)

        macd_line, signal_line, histogram = TA.macd(
            closes, self.macd_fast, self.macd_slow, self.macd_signal
        )

        adx_values = TA.adx(highs, lows, closes, self.adx_period)
        super_trend, is_uptrend = TA.super_trend(
            highs, lows, closes,
            self.super_atr_period,
            self.super_multiplier
        )

        # 获取最新值
        e12 = ema12[-1]
        e26 = ema26[-1]
        e50 = ema50[-1]
        e200 = ema200[-1]
        macd_val = macd_line[-1]
        macd_hist = histogram[-1]
        macd_prev_hist = histogram[-2] if len(histogram) > 1 else 0
        adx_val = adx_values[-1]
        st_uptrend = is_uptrend[-1]
        st_prev_uptrend = is_uptrend[-2] if len(is_uptrend) > 1 else True

        # 缓存指标
        self._cached_indicators = {
            "ema12": e12, "ema26": e26, "ema50": e50, "ema200": e200,
            "macd": macd_val, "macd_signal": signal_line[-1],
            "macd_histogram": macd_hist, "adx": adx_val,
            "super_trend": super_trend[-1], "is_uptrend": st_uptrend,
        }

        # 趋势判断
        bullish_alignment = e12 > e26 > e50
        macd_bullish = macd_val > 0
        strong_trend = adx_val > self.adx_threshold
        macd_momentum_up = macd_hist > macd_prev_hist
        super_trend_buy = st_uptrend and not st_prev_uptrend
        super_trend_sell = not st_uptrend and st_prev_uptrend

        signal = None
        confidence = 0.0
        reasons = []

        # ---- 出场信号优先 ----
        if self.position_count > 0:
            if super_trend_sell:
                signal = Signal(
                    action="SELL", price=current_price,
                    timestamp=current_time,
                    reason="SuperTrend反转出场", confidence=0.8,
                    indicators=self._cached_indicators.copy(),
                )
            elif not bullish_alignment or not macd_bullish:
                signal = Signal(
                    action="SELL", price=current_price,
                    timestamp=current_time,
                    reason="趋势转弱出场", confidence=0.6,
                    indicators=self._cached_indicators.copy(),
                )

        # ---- 入场信号 ----
        # 未达最大持仓数时，可连续加仓（Broker 层同时校验 portfolio.can_open）
        if self.position_count < self._max_positions:
            if bullish_alignment and macd_bullish and strong_trend:
                confidence = 0.3
                if e50 > e200:
                    confidence += 0.2
                    reasons.append("长期多头")
                if current_price > e12:
                    confidence += 0.15
                    reasons.append("短期强势")
                if macd_momentum_up:
                    confidence += 0.15
                    reasons.append("MACD动能增强")
                if super_trend_buy:
                    confidence += 0.2
                    reasons.append("SuperTrend买入")
                if adx_val > 40:
                    confidence += 0.1
                    reasons.append("强趋势")

                confidence = min(confidence, 1.0)
                if confidence >= 0.5:
                    signal = Signal(
                        action="BUY", price=current_price,
                        timestamp=current_time,
                        reason=" | ".join(reasons) if reasons else "多头信号",
                        confidence=confidence,
                        indicators=self._cached_indicators.copy(),
                    )

        if signal:
            self.last_signal = signal
            self.signal_count += 1

        return signal

    def on_position_open(self, signal: Signal, price: float):
        """开仓回调"""
        if signal.action == "BUY":
            self.position_count += 1

    def on_position_close(self, price: float, reason: str, pnl: float):
        """平仓回调"""
        self.position_count = max(0, self.position_count - 1)

    def reset(self):
        """重置策略状态"""
        super().reset()
        self.position_count = 0
