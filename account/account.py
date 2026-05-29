# -*- coding: utf-8 -*-
"""Account state shared by backtest and live engines."""

from typing import Any, Dict, List, Optional

from .portfolio import Portfolio
from .position import Position


class Account:
    """Manages one portfolio per (strategy, symbol)."""

    def __init__(self, initial_capital: float = 10000.0):
        self.initial_capital = float(initial_capital)
        self._portfolios: Dict[str, Portfolio] = {}

    def _make_key(self, strategy_name: str, symbol: str) -> str:
        return f"{strategy_name}:{symbol}"

    def create_portfolio(
        self,
        strategy_name: str,
        symbol: str,
        capital: Optional[float] = None,
        max_positions: int = 3,
        risk_manager: Optional[Any] = None,
    ) -> Portfolio:
        key = self._make_key(strategy_name, symbol)
        if key in self._portfolios:
            raise ValueError(f"portfolio '{key}' already exists")

        if capital is None:
            allocated = sum(p.initial_capital for p in self._portfolios.values())
            remaining = self.initial_capital - allocated
            count = len(self._portfolios) + 1
            capital = remaining / count if remaining > 0 else self.initial_capital / count

        portfolio = Portfolio(
            initial_capital=capital,
            max_positions=max_positions,
            risk_manager=risk_manager,
        )
        self._portfolios[key] = portfolio
        return portfolio

    def ensure_portfolio(
        self,
        strategy_name: str,
        symbol: str,
        capital: float,
        max_positions: int = 3,
        risk_manager: Optional[Any] = None,
    ) -> Portfolio:
        existing = self.get_portfolio(strategy_name, symbol)
        if existing:
            if risk_manager and existing.risk_manager is None:
                existing.set_risk_manager(risk_manager)
            return existing
        return self.create_portfolio(strategy_name, symbol, capital, max_positions, risk_manager)

    def allocate_equal(
        self,
        strategy_symbols: List[tuple],
        max_positions_by_key: Optional[Dict[str, int]] = None,
        risk_managers_by_key: Optional[Dict[str, Any]] = None,
    ):
        """Create missing portfolios and split account capital across them once."""
        unique = []
        seen = set()
        for strategy_name, symbol in strategy_symbols:
            key = self._make_key(strategy_name, symbol)
            if key not in seen:
                seen.add(key)
                unique.append((strategy_name, symbol))

        if not unique:
            return

        capital = self.initial_capital / len(unique)
        max_positions_by_key = max_positions_by_key or {}
        risk_managers_by_key = risk_managers_by_key or {}
        for strategy_name, symbol in unique:
            key = self._make_key(strategy_name, symbol)
            max_positions = max_positions_by_key.get(key, 3)
            risk_manager = risk_managers_by_key.get(key)
            self.ensure_portfolio(strategy_name, symbol, capital, max_positions, risk_manager)

    def get_portfolio(self, strategy_name: str, symbol: str) -> Optional[Portfolio]:
        return self._portfolios.get(self._make_key(strategy_name, symbol))

    @property
    def portfolios(self) -> Dict[str, Portfolio]:
        return self._portfolios

    @property
    def portfolio_names(self) -> List[str]:
        return list(self._portfolios.keys())

    @property
    def allocated_capital(self) -> float:
        return sum(p.initial_capital for p in self._portfolios.values())

    @property
    def total_equity(self) -> float:
        if not self._portfolios:
            return 0.0
        unallocated = max(self.initial_capital - self.allocated_capital, 0.0)
        return unallocated + sum(p.total_equity for p in self._portfolios.values())

    @property
    def total_realized_pnl(self) -> float:
        return sum(p.total_realized_pnl for p in self._portfolios.values())

    @property
    def total_unrealized_pnl(self) -> float:
        return sum(p.total_unrealized_pnl for p in self._portfolios.values())

    @property
    def total_trades(self) -> int:
        return sum(p.total_trades for p in self._portfolios.values())

    @property
    def total_return_pct(self) -> float:
        if self.initial_capital == 0:
            return 0.0
        return round((self.total_equity / self.initial_capital - 1) * 100, 10)

    def get_all_open_positions(self) -> List[Position]:
        positions = []
        for portfolio in self._portfolios.values():
            positions.extend(portfolio.positions)
        return positions

    def get_all_closed_positions(self) -> List[Position]:
        positions = []
        for portfolio in self._portfolios.values():
            positions.extend(portfolio.closed_positions)
        return positions

    def get_summary(self) -> dict:
        return {
            "initial_capital": self.initial_capital,
            "allocated_capital": self.allocated_capital,
            "total_equity": self.total_equity,
            "total_return_pct": self.total_return_pct,
            "total_realized_pnl": self.total_realized_pnl,
            "total_unrealized_pnl": self.total_unrealized_pnl,
            "total_trades": self.total_trades,
            "portfolio_count": len(self._portfolios),
            "portfolios": {name: p.get_stats() for name, p in self._portfolios.items()},
        }

    def reset(self):
        for portfolio in self._portfolios.values():
            portfolio.reset()
