# -*- coding: utf-8 -*-
"""
技术指标计算模块
===============
提供常用的技术指标计算函数，基于 numpy 实现。
"""

from typing import List, Tuple, Optional
import numpy as np


class TA:
    """技术指标计算工具类"""

    @staticmethod
    def sma(data: List[float], period: int) -> List[float]:
        """简单移动平均线"""
        arr = np.array(data, dtype=float)
        result = np.copy(arr)
        for i in range(len(arr)):
            if i < period - 1:
                result[i] = arr[i]
            else:
                result[i] = np.mean(arr[i - period + 1:i + 1])
        return result.tolist()

    @staticmethod
    def ema(data: List[float], period: int) -> List[float]:
        """指数移动平均线"""
        arr = np.array(data, dtype=float)
        result = np.zeros_like(arr)
        multiplier = 2.0 / (period + 1)
        result[0] = arr[0]
        for i in range(1, len(arr)):
            result[i] = (arr[i] - result[i - 1]) * multiplier + result[i - 1]
        return result.tolist()

    @staticmethod
    def macd(data: List[float], fast: int = 12, slow: int = 26, signal_period: int = 9) -> Tuple[List[float], List[float], List[float]]:
        """
        MACD 指标
        Returns: (macd_line, signal_line, histogram)
        """
        ema_fast = TA.ema(data, fast)
        ema_slow = TA.ema(data, slow)
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_line = TA.ema(macd_line, signal_period)
        histogram = [m - s for m, s in zip(macd_line, signal_line)]
        return macd_line, signal_line, histogram

    @staticmethod
    def rsi(data: List[float], period: int = 14) -> List[float]:
        """相对强弱指标 RSI"""
        arr = np.array(data, dtype=float)
        deltas = np.diff(arr)
        seed = deltas[:period]
        up = seed[seed >= 0].sum() / period
        down = -seed[seed < 0].sum() / period
        rs = up / down if down != 0 else 0
        rsi = np.zeros_like(arr)
        rsi[:period] = 100 - 100 / (1 + rs)

        for i in range(period, len(arr)):
            delta = deltas[i - 1]
            if delta > 0:
                upval = delta
                downval = 0
            else:
                upval = 0
                downval = -delta
            up = (up * (period - 1) + upval) / period
            down = (down * (period - 1) + downval) / period
            rs = up / down if down != 0 else 0
            rsi[i] = 100 - 100 / (1 + rs)

        return rsi.tolist()

    @staticmethod
    def atr(high: List[float], low: List[float], close: List[float], period: int = 14) -> List[float]:
        """平均真实波幅 (Average True Range)"""
        high_arr = np.array(high, dtype=float)
        low_arr = np.array(low, dtype=float)
        close_arr = np.array(close, dtype=float)

        tr = np.zeros_like(close_arr)
        tr[0] = high_arr[0] - low_arr[0]
        for i in range(1, len(close_arr)):
            tr[i] = max(
                high_arr[i] - low_arr[i],
                abs(high_arr[i] - close_arr[i - 1]),
                abs(low_arr[i] - close_arr[i - 1])
            )
        return TA.ema(tr.tolist(), period)

    @staticmethod
    def bollinger(data: List[float], period: int = 20, std_dev: float = 2.0) -> Tuple[List[float], List[float], List[float]]:
        """
        布林带
        Returns: (upper_band, middle_band, lower_band)
        """
        arr = np.array(data, dtype=float)
        middle = np.zeros_like(arr)
        upper = np.zeros_like(arr)
        lower = np.zeros_like(arr)

        for i in range(len(arr)):
            if i < period - 1:
                middle[i] = arr[i]
                upper[i] = arr[i]
                lower[i] = arr[i]
            else:
                window = arr[i - period + 1:i + 1]
                middle[i] = np.mean(window)
                std = np.std(window)
                upper[i] = middle[i] + std_dev * std
                lower[i] = middle[i] - std_dev * std

        return upper.tolist(), middle.tolist(), lower.tolist()

    @staticmethod
    def adx(high: List[float], low: List[float], close: List[float], period: int = 14) -> List[float]:
        """平均趋向指数 (Average Directional Index)"""
        length = len(close)
        if length < period + 1:
            return [0.0] * length

        high_arr = np.array(high, dtype=float)
        low_arr = np.array(low, dtype=float)
        close_arr = np.array(close, dtype=float)

        plus_dm = np.zeros(length)
        minus_dm = np.zeros(length)
        tr = np.zeros(length)

        for i in range(1, length):
            up_move = high_arr[i] - high_arr[i - 1]
            down_move = low_arr[i - 1] - low_arr[i]

            if up_move > down_move and up_move > 0:
                plus_dm[i] = up_move
            if down_move > up_move and down_move > 0:
                minus_dm[i] = down_move

            tr[i] = max(
                high_arr[i] - low_arr[i],
                abs(high_arr[i] - close_arr[i - 1]),
                abs(low_arr[i] - close_arr[i - 1])
            )

        atr_values = TA.ema(tr.tolist(), period)
        plus_dm_smooth = TA.ema(plus_dm.tolist(), period)
        minus_dm_smooth = TA.ema(minus_dm.tolist(), period)

        plus_di = np.zeros(length)
        minus_di = np.zeros(length)
        dx = np.zeros(length)

        for i in range(length):
            if atr_values[i] != 0:
                plus_di[i] = (plus_dm_smooth[i] / atr_values[i]) * 100
                minus_di[i] = (minus_dm_smooth[i] / atr_values[i]) * 100
                di_diff = abs(plus_di[i] - minus_di[i])
                di_sum = plus_di[i] + minus_di[i]
                dx[i] = (di_diff / di_sum) * 100 if di_sum != 0 else 0

        adx_values = TA.ema(dx.tolist(), period)
        return adx_values

    @staticmethod
    def super_trend(high: List[float], low: List[float], close: List[float],
                    period: int = 10, multiplier: float = 3.0) -> Tuple[List[float], List[bool]]:
        """
        超级趋势指标 (SuperTrend)
        Returns: (super_trend_line, is_uptrend)
        """
        length = len(close)
        atr_values = TA.atr(high, low, close, period)

        upper_band = [0.0] * length
        lower_band = [0.0] * length
        super_trend = [0.0] * length
        is_uptrend = [True] * length

        for i in range(length):
            if i < period:
                continue

            hl2 = (high[i] + low[i]) / 2
            upper_band[i] = hl2 + multiplier * atr_values[i]
            lower_band[i] = hl2 - multiplier * atr_values[i]

            if i == period:
                super_trend[i] = upper_band[i]
                is_uptrend[i] = True
            else:
                if close[i] > upper_band[i - 1]:
                    is_uptrend[i] = True
                elif close[i] < lower_band[i - 1]:
                    is_uptrend[i] = False
                else:
                    is_uptrend[i] = is_uptrend[i - 1]

                if is_uptrend[i] and lower_band[i] < lower_band[i - 1]:
                    lower_band[i] = lower_band[i - 1]
                if not is_uptrend[i] and upper_band[i] > upper_band[i - 1]:
                    upper_band[i] = upper_band[i - 1]

                super_trend[i] = lower_band[i] if is_uptrend[i] else upper_band[i]

        return super_trend, is_uptrend
