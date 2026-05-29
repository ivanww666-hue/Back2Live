# -*- coding: utf-8 -*-
"""
持仓模块
=======
定义持仓数据结构，记录单个持仓的详细信息。
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Position:
    """
    持仓信息

    记录一个持仓的完整生命周期数据，包括开仓、持仓期间和最终平仓信息。
    """

    # ---- 标识 ----
    symbol: str                    # 交易对
    side: str                      # "LONG" 或 "SHORT"
    strategy_name: str = ""        # 所属策略名称

    # ---- 开仓信息 ----
    entry_price: float = 0.0       # 开仓价格
    entry_time: int = 0            # 开仓时间戳 (ms)
    quantity: float = 0.0          # 持仓数量
    entry_order_id: str = ""       # 买入订单ID（用于审计追溯）

    # ---- 风控参数 ----
    stop_loss: float = 0.0         # 止损价
    take_profit: float = 0.0       # 止盈价
    trailing_stop: float = 0.0     # 移动止损价

    # ---- 持仓期间 ----
    highest_price: float = 0.0     # 持仓期间最高价
    lowest_price: float = float('inf')  # 持仓期间最低价
    unrealized_pnl: float = 0.0    # 未实现盈亏

    # ---- 平仓信息 ----
    exit_price: float = 0.0        # 平仓价格
    exit_time: int = 0             # 平仓时间戳 (ms)
    exit_reason: str = ""          # 平仓原因
    realized_pnl: float = 0.0      # 已实现盈亏
    pnl_pct: float = 0.0           # 盈亏百分比

    # ---- 状态 ----
    is_open: bool = True           # 是否仍持有

    def update_high_low(self, current_price: float):
        """更新持仓期间的最高/最低价"""
        if self.side == "LONG":
            if current_price > self.highest_price:
                self.highest_price = current_price
        else:
            if current_price < self.lowest_price:
                self.lowest_price = current_price

    def update_unrealized_pnl(self, current_price: float):
        """更新未实现盈亏"""
        if self.side == "LONG":
            self.unrealized_pnl = (current_price - self.entry_price) * self.quantity
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.quantity

    def close(self, exit_price: float, reason: str = ""):
        """
        平仓

        Args:
            exit_price: 平仓价格
            reason: 平仓原因
        """
        self.exit_price = exit_price
        self.exit_reason = reason

        if self.side == "LONG":
            self.realized_pnl = (exit_price - self.entry_price) * self.quantity
        else:
            self.realized_pnl = (self.entry_price - exit_price) * self.quantity

        self.pnl_pct = (self.realized_pnl / (self.entry_price * self.quantity)) * 100 if self.quantity > 0 else 0
        self.is_open = False

    def to_dict(self) -> dict:
        """转换为字典（用于序列化）"""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "strategy_name": self.strategy_name,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time,
            "entry_order_id": self.entry_order_id,
            "quantity": self.quantity,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "exit_price": self.exit_price,
            "exit_time": self.exit_time,
            "exit_reason": self.exit_reason,
            "realized_pnl": self.realized_pnl,
            "pnl_pct": self.pnl_pct,
            "is_open": self.is_open,
            "highest_price": self.highest_price,
            "lowest_price": self.lowest_price,
            "unrealized_pnl": self.unrealized_pnl,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        """从字典反序列化创建 Position"""
        return cls(
            symbol=data.get("symbol", ""),
            side=data.get("side", "LONG"),
            strategy_name=data.get("strategy_name", ""),
            entry_price=data.get("entry_price", 0.0),
            entry_time=data.get("entry_time", 0),
            quantity=data.get("quantity", 0.0),
            entry_order_id=data.get("entry_order_id", ""),
            stop_loss=data.get("stop_loss", 0.0),
            take_profit=data.get("take_profit", 0.0),
            exit_price=data.get("exit_price", 0.0),
            exit_time=data.get("exit_time", 0),
            exit_reason=data.get("exit_reason", ""),
            realized_pnl=data.get("realized_pnl", 0.0),
            pnl_pct=data.get("pnl_pct", 0.0),
            is_open=data.get("is_open", True),
            highest_price=data.get("highest_price", 0.0),
            lowest_price=data.get("lowest_price", float("inf")),
            unrealized_pnl=data.get("unrealized_pnl", 0.0),
        )
