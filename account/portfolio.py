# -*- coding: utf-8 -*-
"""Portfolio state for one strategy-symbol allocation."""

from typing import Any, List, Optional

from .position import Position


class Portfolio:
    """Holds cash, open positions and closed positions for one strategy-symbol."""

    def __init__(
        self,
        initial_capital: float = 10000.0,
        max_positions: int = 3,
        risk_manager: Optional[Any] = None,
    ):
        self.initial_capital = float(initial_capital)
        self.max_positions = int(max_positions)
        self.risk_manager = risk_manager
        self.capital = float(initial_capital)
        self._positions: List[Position] = []
        self._closed_positions: List[Position] = []

    @property
    def positions(self) -> List[Position]:
        return self._positions

    @property
    def closed_positions(self) -> List[Position]:
        return self._closed_positions

    @property
    def open_count(self) -> int:
        return len(self._positions)

    @property
    def can_open(self) -> bool:
        return self.open_count < self.max_positions

    @property
    def allocated_capital(self) -> float:
        return self.initial_capital

    def set_risk_manager(self, risk_manager: Any):
        self.risk_manager = risk_manager

    def add_position(self, position: Position):
        self._positions.append(position)

    def remove_position(self, position: Position) -> bool:
        if position in self._positions:
            self._positions.remove(position)
            self._closed_positions.append(position)
            return True
        return False

    def get_position_by_side(self, side: str) -> Optional[Position]:
        positions = self.get_open_positions_by_side(side)
        return positions[0] if positions else None

    def get_open_positions_by_side(self, side: str) -> List[Position]:
        return [pos for pos in self._positions if pos.side == side and pos.is_open]

    def get_position_by_entry_id(self, entry_order_id: str) -> Optional[Position]:
        """按建仓订单ID查找持仓（用于平仓挂单成交时精确匹配）。"""
        for pos in self._positions:
            if pos.entry_order_id == entry_order_id and pos.is_open:
                return pos
        return None

    @property
    def total_trades(self) -> int:
        return len(self._closed_positions)

    @property
    def winning_trades(self) -> int:
        return sum(1 for p in self._closed_positions if p.realized_pnl > 0)

    @property
    def losing_trades(self) -> int:
        return sum(1 for p in self._closed_positions if p.realized_pnl <= 0)

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades * 100

    @property
    def total_realized_pnl(self) -> float:
        return sum(p.realized_pnl for p in self._closed_positions)

    @property
    def total_unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self._positions)

    @property
    def total_equity(self) -> float:
        market_value = sum(p.entry_price * p.quantity + p.unrealized_pnl for p in self._positions)
        return self.capital + market_value

    @property
    def total_return_pct(self) -> float:
        if self.initial_capital == 0:
            return 0.0
        return (self.total_equity / self.initial_capital - 1) * 100

    def get_stats(self) -> dict:
        risk_config = {}
        if self.risk_manager:
            risk_config = {
                "stop_loss_pct": self.risk_manager.stop_loss_pct,
                "take_profit_pct": self.risk_manager.take_profit_pct,
                "trailing_stop_pct": self.risk_manager.trailing_stop_pct,
                "position_size_pct": self.risk_manager.position_size_pct,
            }

        return {
            "initial_capital": self.initial_capital,
            "current_capital": self.capital,
            "total_equity": self.total_equity,
            "total_return_pct": self.total_return_pct,
            "total_realized_pnl": self.total_realized_pnl,
            "total_unrealized_pnl": self.total_unrealized_pnl,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": self.win_rate,
            "open_positions": self.open_count,
            "max_positions": self.max_positions,
            "risk": risk_config,
        }

    def reset(self):
        self.capital = self.initial_capital
        self._positions.clear()
        self._closed_positions.clear()
