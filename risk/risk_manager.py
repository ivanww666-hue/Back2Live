# -*- coding: utf-8 -*-
"""
风险管理模块
===========
提供止损、止盈、移动止损等风险控制功能。
"""

from typing import List, Tuple, Optional
from account.position import Position
from account.portfolio import Portfolio


class RiskManager:
    """
    风险管理器

    负责:
    1. 计算仓位大小
    2. 计算止损止盈价格
    3. 检查持仓风控条件
    """

    def __init__(self, stop_loss_pct: float = 2.0,
                 take_profit_pct: float = 6.0,
                 trailing_stop_pct: float = 1.5,
                 position_size_pct: float = 20.0):
        """
        Args:
            stop_loss_pct: 止损百分比
            take_profit_pct: 止盈百分比
            trailing_stop_pct: 移动止损百分比
            position_size_pct: 单次建仓资金比例 (%)
        """
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.trailing_stop_pct = trailing_stop_pct
        self.position_size_pct = position_size_pct

    def calculate_position_size(self, capital: float, price: float,
                                risk_pct: Optional[float] = None) -> float:
        """
        计算仓位大小

        Args:
            capital: 可用资金
            price: 当前价格
            risk_pct: 单笔建仓资金比例 (None 时使用 position_size_pct / 100)

        Returns:
            仓位数量
        """
        if risk_pct is None:
            risk_pct = self.position_size_pct / 100
        if capital <= 0 or price <= 0 or risk_pct <= 0:
            return 0.0
        size = (capital * risk_pct) / price
        return round(size, 4)

    def calculate_stop_loss(self, entry_price: float, side: str = "LONG") -> float:
        """
        计算止损价格

        Args:
            entry_price: 入场价格
            side: 方向（现货只支持 "LONG"）

        Returns:
            止损价格
        """
        return entry_price * (1 - self.stop_loss_pct / 100)

    def calculate_take_profit(self, entry_price: float, side: str = "LONG") -> float:
        """
        计算止盈价格

        Args:
            entry_price: 入场价格
            side: 方向（现货只支持 "LONG"）

        Returns:
            止盈价格
        """
        return entry_price * (1 + self.take_profit_pct / 100)

    def check_positions(self, portfolio: Portfolio, current_price: float,
                        current_time: int) -> List[Tuple[Position, str]]:
        """
        检查所有持仓的风控条件

        Args:
            portfolio: 投资组合
            current_price: 当前价格
            current_time: 当前时间戳

        Returns:
            [(position, reason), ...] 需要平仓的持仓列表
        """
        to_close = []

        for position in portfolio.positions:
            reason = self._check_position(position, current_price)
            if reason:
                to_close.append((position, reason))

        return to_close

    def _check_position(self, position: Position,
                        current_price: float) -> Optional[str]:
        """
        检查单个持仓的风控条件（现货只做多）

        Args:
            position: 持仓
            current_price: 当前价格

        Returns:
            平仓原因，None 表示无需平仓
        """
        return self._check_position_risk(position, current_price)

    def _check_position_risk(self, position: Position, current_price: float) -> Optional[str]:
        """检查持仓风险"""
        # 更新最高价
        if current_price > position.highest_price:
            position.highest_price = current_price

        # 检查止损
        if current_price <= position.stop_loss:
            return f"止损 (价格:{current_price:.2f} <= 止损:{position.stop_loss:.2f})"

        # 检查止盈
        if current_price >= position.take_profit:
            return f"止盈 (价格:{current_price:.2f} >= 止盈:{position.take_profit:.2f})"

        # 检查移动止损
        if position.highest_price > position.entry_price:
            trailing_stop = position.highest_price * (1 - self.trailing_stop_pct / 100)
            if current_price <= trailing_stop:
                return f"移动止损 (价格:{current_price:.2f} <= 移动止损:{trailing_stop:.2f})"

        return None

    @staticmethod
    def round_quantity(quantity: float, precision: int = 4) -> float:
        """
        舍入数量到指定精度

        Args:
            quantity: 原始数量
            precision: 小数位数

        Returns:
            舍入后的数量
        """
        return round(quantity, precision)

    def reset(self):
        """重置风险管理器"""
        pass
