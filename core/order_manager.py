# -*- coding: utf-8 -*-
"""
订单管理模块
===========
管理订单的创建、执行和跟踪。
"""

from typing import List, Optional, Dict
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime


class OrderType(Enum):
    """订单类型"""
    MARKET = "MARKET"       # 市价单
    LIMIT = "LIMIT"         # 限价单
    STOP = "STOP"           # 止损单


class OrderStatus(Enum):
    """订单状态"""
    PENDING = "PENDING"     # 待成交
    FILLED = "FILLED"       # 已成交
    PARTIAL = "PARTIAL"     # 部分成交
    CANCELLED = "CANCELLED" # 已取消
    REJECTED = "REJECTED"   # 已拒绝


@dataclass
class Order:
    """订单"""
    order_id: str
    symbol: str
    side: str                # "BUY" or "SELL"
    order_type: OrderType = OrderType.MARKET
    quantity: float = 0.0
    price: float = 0.0
    stop_price: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    filled_price: float = 0.0
    created_time: int = 0
    filled_time: int = 0
    strategy_name: str = ""
    reason: str = ""
    entry_order_id: str = ""   # 平仓挂单关联的建仓订单ID


class OrderManager:
    """
    订单管理器

    负责订单的创建、执行、跟踪和记录。
    在回测中，订单立即以当前价格成交（市价单）。
    """

    def __init__(self):
        self._orders: List[Order] = []
        self._order_counter = 0

    @property
    def orders(self) -> List[Order]:
        return self._orders

    @property
    def pending_orders(self) -> List[Order]:
        return [o for o in self._orders if o.status == OrderStatus.PENDING]

    @property
    def filled_orders(self) -> List[Order]:
        return [o for o in self._orders if o.status == OrderStatus.FILLED]

    def create_order(self, symbol: str, side: str, quantity: float,
                     order_type: OrderType = OrderType.MARKET,
                     price: float = 0.0, stop_price: float = 0.0,
                     strategy_name: str = "", reason: str = "",
                     order_id: str = "", created_time: int = 0,
                     entry_order_id: str = "") -> Order:
        """
        创建订单

        Args:
            symbol: 交易对
            side: "BUY" or "SELL"
            quantity: 数量
            order_type: 订单类型
            price: 价格（限价单）
            stop_price: 触发价格（止损单）
            strategy_name: 策略名称
            reason: 订单原因
            order_id: 外部订单ID (如 Binance 订单ID)
            created_time: 订单创建时间戳（ms），0 表示用当前时间
            entry_order_id: 关联的建仓订单ID（平仓挂单使用）

        Returns:
            创建的订单
        """
        # 如果 order_id 已存在，直接返回已有订单（避免挂单成交后重复创建）
        if order_id:
            existing = self.get_order(order_id)
            if existing:
                return existing
        self._order_counter += 1
        if not order_id:
            order_id = f"ORD_{self._order_counter:06d}"
        if not created_time:
            created_time = int(datetime.now().timestamp() * 1000)
        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            created_time=created_time,
            strategy_name=strategy_name,
            reason=reason,
            entry_order_id=entry_order_id,
        )
        self._orders.append(order)
        return order

    def execute_order(self, order: Order, current_price: float,
                      current_time: int) -> bool:
        """
        执行订单（回测中市价单立即成交）

        Args:
            order: 待执行订单
            current_price: 当前市场价格
            current_time: 当前时间戳

        Returns:
            是否成功执行
        """
        if order.status != OrderStatus.PENDING:
            return False

        # 市价单立即成交
        if order.order_type == OrderType.MARKET:
            order.status = OrderStatus.FILLED
            order.filled_quantity = order.quantity
            order.filled_price = current_price
            order.filled_time = current_time
            return True

        # 限价单检查价格
        elif order.order_type == OrderType.LIMIT:
            if (order.side == "BUY" and current_price <= order.price) or \
               (order.side == "SELL" and current_price >= order.price):
                order.status = OrderStatus.FILLED
                order.filled_quantity = order.quantity
                order.filled_price = current_price
                order.filled_time = current_time
                return True

        # 止损单检查触发价格
        elif order.order_type == OrderType.STOP:
            if (order.side == "BUY" and current_price >= order.stop_price) or \
               (order.side == "SELL" and current_price <= order.stop_price):
                order.status = OrderStatus.FILLED
                order.filled_quantity = order.quantity
                order.filled_price = current_price
                order.filled_time = current_time
                return True

        return False

    def cancel_order(self, order_id: str) -> bool:
        """取消订单"""
        for order in self._orders:
            if order.order_id == order_id and order.status == OrderStatus.PENDING:
                order.status = OrderStatus.CANCELLED
                return True
        return False

    def get_order(self, order_id: str) -> Optional[Order]:
        """根据订单ID查找订单"""
        for order in self._orders:
            if order.order_id == order_id:
                return order
        return None

    def update_order_fill(self, order_id: str, status: OrderStatus,
                          filled_quantity: float = 0.0,
                          filled_price: float = 0.0,
                          filled_time: int = 0) -> bool:
        """更新订单成交状态"""
        order = self.get_order(order_id)
        if not order:
            return False
        order.status = status
        order.filled_quantity = filled_quantity
        order.filled_price = filled_price
        if filled_time:
            order.filled_time = filled_time
        return True

    def get_orders_by_strategy(self, strategy_name: str) -> List[Order]:
        """获取指定策略的所有订单"""
        return [o for o in self._orders if o.strategy_name == strategy_name]

    def clear(self):
        """清空所有订单"""
        self._orders.clear()
        self._order_counter = 0

    def get_stats(self) -> dict:
        """获取订单统计"""
        total = len(self._orders)
        filled = len(self.filled_orders)
        pending = len(self.pending_orders)
        return {
            "total_orders": total,
            "filled_orders": filled,
            "pending_orders": pending,
            "fill_rate": (filled / total * 100) if total > 0 else 0,
        }

    def to_dict_list(self, strategy_name: str = "") -> list:
        """将待保存的订单序列化为字典列表（仅 pending 订单需要持久化）

        Args:
            strategy_name: 可选策略名过滤，为空则返回全部
        """
        return [
            {
                "order_id": o.order_id,
                "symbol": o.symbol,
                "side": o.side,
                "quantity": o.quantity,
                "price": o.price,
                "strategy_name": o.strategy_name,
                "reason": o.reason,
                "created_time": o.created_time,
                "entry_order_id": o.entry_order_id,
                "filled_quantity": o.filled_quantity,
                "filled_price": o.filled_price,
            }
            for o in self.pending_orders
            if not strategy_name or o.strategy_name == strategy_name
        ]

    def load_from_dict_list(self, data: list):
        """从字典列表恢复 pending 订单（用于从 Redis 恢复）"""
        for item in data:
            order = Order(
                order_id=item.get("order_id", ""),
                symbol=item.get("symbol", ""),
                side=item.get("side", ""),
                quantity=item.get("quantity", 0.0),
                price=item.get("price", 0.0),
                strategy_name=item.get("strategy_name", ""),
                reason=item.get("reason", ""),
                created_time=item.get("created_time", 0),
                entry_order_id=item.get("entry_order_id", ""),
            )
            order.status = OrderStatus.PENDING
            order.filled_quantity = item.get("filled_quantity", 0.0)
            order.filled_price = item.get("filled_price", 0.0)
            self._orders.append(order)