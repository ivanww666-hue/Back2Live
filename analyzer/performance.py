# -*- coding: utf-8 -*-
"""
绩效分析模块
===========
提供回测结果的绩效指标计算和分析功能。
"""

from typing import List, Dict, Optional
import math


class PerformanceAnalyzer:
    """
    绩效分析器

    计算各种绩效指标:
    - 收益率、年化收益率
    - 最大回撤
    - 夏普比率
    - 胜率、盈亏比
    - 交易频率
    """

    def __init__(self, result_dict: dict):
        """
        Args:
            result_dict: 回测结果字典 (BacktestResult.to_dict())
        """
        self.data = result_dict
        self.equity_curve = result_dict.get("equity_curve", [])
        self.trades = result_dict.get("trades", [])
        self.summary = result_dict.get("summary", {})
        self.config = result_dict.get("config", {})

    # ---- 基础指标 ----

    @property
    def total_return(self) -> float:
        """总收益率 (%)"""
        return self.summary.get("total_return_pct", 0.0)

    @property
    def total_trades(self) -> int:
        """总交易次数"""
        return self.summary.get("total_trades", len(self.trades))

    @property
    def winning_trades(self) -> int:
        """盈利交易次数"""
        return self.summary.get("winning_trades", 0)

    @property
    def losing_trades(self) -> int:
        """亏损交易次数"""
        return self.summary.get("losing_trades", 0)

    @property
    def win_rate(self) -> float:
        """胜率 (%)"""
        return self.summary.get("win_rate", 0.0)

    @property
    def avg_win(self) -> float:
        """平均盈利"""
        return self.summary.get("avg_win", 0.0)

    @property
    def avg_loss(self) -> float:
        """平均亏损"""
        return self.summary.get("avg_loss", 0.0)

    @property
    def profit_factor(self) -> float:
        """盈亏比"""
        return self.summary.get("profit_factor", 0.0)

    # ---- 回撤分析 ----

    @property
    def max_drawdown(self) -> float:
        """最大回撤 (%)"""
        return self.summary.get("max_drawdown", 0.0)

    @property
    def max_drawdown_duration(self) -> int:
        """最大回撤持续时间 (K线数)"""
        if not self.equity_curve:
            return 0

        # 按时间聚合权益
        equity_by_time: Dict[int, float] = {}
        for entry in self.equity_curve:
            t = entry["time"]
            equity_by_time[t] = equity_by_time.get(t, 0) + entry["equity"]

        sorted_times = sorted(equity_by_time.keys())
        equities = [equity_by_time[t] for t in sorted_times]

        peak = equities[0]
        peak_idx = 0
        max_duration = 0
        current_duration = 0

        for i, eq in enumerate(equities):
            if eq > peak:
                peak = eq
                peak_idx = i
                current_duration = 0
            else:
                current_duration = i - peak_idx
                if current_duration > max_duration:
                    max_duration = current_duration

        return max_duration

    # ---- 风险调整收益 ----

    @property
    def sharpe_ratio(self) -> float:
        """夏普比率 (年化)"""
        return self.summary.get("sharpe_ratio", 0.0)

    @property
    def calmar_ratio(self) -> float:
        """卡玛比率"""
        if self.max_drawdown == 0:
            return 0.0
        total_return_val = self.summary.get("total_return_pct", 0.0)
        return abs(total_return_val / self.max_drawdown)

    # ---- 综合报告 ----

    def get_summary(self) -> dict:
        """获取绩效摘要"""
        return {
            "total_return_pct": round(self.total_return, 2),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate_pct": round(self.win_rate, 2),
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "profit_factor": round(self.profit_factor, 2),
            "max_drawdown_pct": round(self.max_drawdown, 2),
            "max_drawdown_duration": self.max_drawdown_duration,
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "calmar_ratio": round(self.calmar_ratio, 2),
        }
