# -*- coding: utf-8 -*-
"""Execution layer shared by backtest and paper/live adapters."""

import logging
from typing import List, Optional, Tuple

from account.account import Account
from account.portfolio import Portfolio
from account.position import Position
from core.order_manager import OrderManager
from risk.risk_manager import RiskManager
from strategy.base_strategy import BaseStrategy, Signal


logger = logging.getLogger(__name__)

# 最小残渣数量阈值：低于此值的持仓残渣将被视为0（防止浮点误差产生无法平仓的幽灵仓位）
_MIN_POSITION_THRESHOLD = 1e-8
# 通用浮点容差
_FLOAT_EPSILON = 1e-9
# capital 取整精度
_CAPITAL_DECIMALS = 8


class Broker:
    """Spot broker simulator.

    Invariants:
    - BUY opens long spot inventory only after cash is available.
    - SELL can only close an existing long position.
    - Close quantity always equals the selected open position quantity.
    - All accepted and rejected decisions are auditable in ``trades``.
    """

    def __init__(
        self,
        account: Account,
        risk_manager: RiskManager,
        order_manager: OrderManager,
        maker_fee: float = 0.001,
        taker_fee: float = 0.001,
        quantity_step: float = 0.0,
        min_quantity: float = 0.0,
    ):
        self.account = account
        self.risk_manager = risk_manager
        self.order_manager = order_manager
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.quantity_step = quantity_step  # 数量步长 (如 BTC 的 0.00001)
        self.min_quantity = min_quantity    # 最小数量 (交易所 min_qty)
        self.trades: List[dict] = []

    def _round_qty(self, quantity: float) -> float:
        """将数量对齐到 quantity_step 的整数倍。如果 step 未设置则不做对齐。"""
        if self.quantity_step <= 0:
            return quantity
        return round(quantity / self.quantity_step) * self.quantity_step

    def _round_capital(self, value: float) -> float:
        """将资金取整到 _CAPITAL_DECIMALS 位，防止浮点累积误差。"""
        return round(value, _CAPITAL_DECIMALS)

    def process_signal(
        self,
        signal: Signal,
        strategy: BaseStrategy,
        portfolio: Portfolio,
        current_time: int,
    ):
        action = signal.action.upper()
        if action == "BUY":
            self._buy(signal, strategy, portfolio, current_time)
        elif action in ("SELL", "EXIT_LONG"):
            self._sell(signal, strategy, portfolio, current_time)
        elif action != "NONE":
            self._reject_trade(signal, strategy, current_time, "unsupported_spot_action")

    def check_risk(
        self,
        portfolio: Portfolio,
        current_price: float,
        current_time: int,
    ) -> List[Tuple[Position, str]]:
        return self._risk_manager_for(portfolio).check_positions(portfolio, current_price, current_time)

    def _risk_manager_for(self, portfolio: Portfolio) -> RiskManager:
        return portfolio.risk_manager or self.risk_manager

    def close_position(
        self,
        position: Position,
        portfolio: Portfolio,
        exit_price: float,
        reason: str,
        current_time: int,
    ) -> bool:
        if not self._can_close_position(position, portfolio):
            return False

        return self._apply_close_fill(
            position=position,
            portfolio=portfolio,
            exit_price=exit_price,
            quantity=position.quantity,
            fee=exit_price * position.quantity * self.taker_fee,
            reason=reason,
            current_time=current_time,
        )

    def _prepare_buy_plan(
        self,
        signal: Signal,
        strategy: BaseStrategy,
        portfolio: Portfolio,
        current_time: int,
    ) -> Optional[dict]:
        """Validate a BUY signal and return an executable local plan."""
        if signal.price <= 0:
            self._reject_trade(signal, strategy, current_time, "invalid_price")
            return None
        if not portfolio.can_open:
            self._reject_trade(signal, strategy, current_time, "max_positions_reached")
            return None

        price = signal.price
        risk_manager = self._risk_manager_for(portfolio)
        quantity = risk_manager.calculate_position_size(portfolio.capital, price)

        # 对齐到交易所步长
        quantity = self._round_qty(quantity)
        if quantity <= 0:
            self._reject_trade(signal, strategy, current_time, "quantity_too_small")
            return None

        # 检查最低数量（交易所 min_qty）
        if self.min_quantity > 0 and quantity < self.min_quantity:
            self._reject_trade(signal, strategy, current_time,
                               f"quantity_below_min_qty ({quantity} < {self.min_quantity})")
            return None

        cost = price * quantity
        fee = cost * self.taker_fee
        total_cost = cost + fee

        if total_cost > portfolio.capital:
            affordable_qty = portfolio.capital / (price * (1 + self.taker_fee))
            quantity = self._round_qty(risk_manager.round_quantity(affordable_qty))
            if self.min_quantity > 0 and quantity < self.min_quantity:
                quantity = 0.0
            if quantity <= 0:
                self._reject_trade(signal, strategy, current_time, "insufficient_cash")
                return None
            cost = price * quantity
            fee = cost * self.taker_fee
            total_cost = cost + fee

        if quantity <= 0 or total_cost > portfolio.capital + _FLOAT_EPSILON:
            self._reject_trade(signal, strategy, current_time, "insufficient_cash")
            return None

        return {
            "price": price,
            "quantity": quantity,
            "fee": fee,
            "total_cost": total_cost,
        }

    def _apply_buy_fill(
        self,
        signal: Signal,
        strategy: BaseStrategy,
        portfolio: Portfolio,
        current_time: int,
        price: float,
        quantity: float,
        fee: float,
        order_id: str = "",
    ) -> bool:
        """Apply a confirmed BUY fill to local portfolio state."""
        if price <= 0 or quantity <= 0:
            self._reject_trade(signal, strategy, current_time, "invalid_fill")
            return False

        total_cost = price * quantity + fee
        if total_cost > portfolio.capital + _FLOAT_EPSILON:
            self._reject_trade(signal, strategy, current_time, "fill_exceeds_local_cash")
            return False

        if not order_id:
            order = self.order_manager.create_order(
                symbol=strategy.symbol,
                side="BUY",
                quantity=quantity,
                price=price,
                strategy_name=strategy.name,
                reason=signal.reason,
            )
            order_id = order.order_id
        else:
            self.order_manager.create_order(
                symbol=strategy.symbol,
                side="BUY",
                quantity=quantity,
                price=price,
                strategy_name=strategy.name,
                reason=signal.reason,
                order_id=order_id,
            )

        position = Position(
            symbol=strategy.symbol,
            side="LONG",
            strategy_name=strategy.name,
            entry_price=price,
            entry_time=current_time,
            quantity=quantity,
            entry_order_id=order_id,
            stop_loss=self._risk_manager_for(portfolio).calculate_stop_loss(price, "LONG"),
            take_profit=self._risk_manager_for(portfolio).calculate_take_profit(price, "LONG"),
            highest_price=price,
        )

        portfolio.capital = self._round_capital(portfolio.capital - total_cost)
        portfolio.add_position(position)

        trade = {
            "type": "OPEN",
            "symbol": strategy.symbol,
            "side": "BUY",
            "price": price,
            "quantity": quantity,
            "fee": fee,
            "time": current_time,
            "reason": signal.reason,
            "strategy": strategy.name,
            "order_id": order_id,
            "cash": round(portfolio.capital, _CAPITAL_DECIMALS),
        }
        self.trades.append(trade)
        strategy.on_position_open(signal, price)
        return True

    def _can_close_position(self, position: Position, portfolio: Portfolio) -> bool:
        if position not in portfolio.positions or not position.is_open:
            logger.warning("close rejected: position is not open in portfolio")
            return False
        if position.quantity <= 0:
            logger.warning("close rejected: position quantity is not positive")
            return False
        return True

    def _apply_close_fill(
        self,
        position: Position,
        portfolio: Portfolio,
        exit_price: float,
        quantity: float,
        fee: float,
        reason: str,
        current_time: int,
        order_id: str = "",
        min_quantity: float = 0.0,
    ) -> bool:
        """Apply a confirmed SELL fill without allowing local oversell.

        Args:
            min_quantity: 交易所最低数量，用于判断残渣仓位是否可忽略。
                          传入 0 时使用实例级别的 self.min_quantity 兜底。
        """
        if not self._can_close_position(position, portfolio):
            return False
        if exit_price <= 0 or quantity <= 0:
            logger.warning("close rejected: invalid fill")
            return False
        if quantity > position.quantity + _FLOAT_EPSILON:
            logger.warning("close rejected: fill quantity exceeds local position")
            return False

        original_quantity = position.quantity
        closed_quantity = quantity

        # 计算剩余数量，用 min_quantity 过滤浮点残渣和 step 截断残渣
        residual = original_quantity - closed_quantity
        residual_threshold = max(_MIN_POSITION_THRESHOLD, min_quantity or self.min_quantity)
        if residual < residual_threshold:
            residual = 0.0

        if residual <= 0:
            # ── 全部平仓 ──
            position.close(exit_price, reason)
            position.exit_time = current_time
            realized_pnl = position.realized_pnl
            pnl_pct = position.pnl_pct
            portfolio.capital = self._round_capital(
                portfolio.capital + exit_price * original_quantity - fee
            )
            portfolio.remove_position(position)
            trade_type = "CLOSE"
        else:
            # ── 部分平仓 ──
            realized_pnl = (
                (exit_price - position.entry_price) * closed_quantity
                if position.side == "LONG"
                else (position.entry_price - exit_price) * closed_quantity
            )
            portfolio.capital = self._round_capital(
                portfolio.capital + exit_price * closed_quantity - fee
            )
            position.quantity = residual
            position.update_unrealized_pnl(exit_price)
            pnl_pct = realized_pnl / (position.entry_price * closed_quantity) * 100 if closed_quantity > 0 else 0
            trade_type = "PARTIAL_CLOSE"

        trade = {
            "type": trade_type,
            "symbol": position.symbol,
            "side": "SELL",
            "price": exit_price,
            "quantity": closed_quantity,
            "fee": fee,
            "time": current_time,
            "reason": reason,
            "strategy": position.strategy_name,
            "pnl": realized_pnl,
            "pnl_pct": pnl_pct,
            "cash": round(portfolio.capital, _CAPITAL_DECIMALS),
        }
        if order_id:
            trade["order_id"] = order_id
        self.trades.append(trade)
        return trade_type == "CLOSE"

    def _buy(
        self,
        signal: Signal,
        strategy: BaseStrategy,
        portfolio: Portfolio,
        current_time: int,
    ):
        plan = self._prepare_buy_plan(signal, strategy, portfolio, current_time)
        if not plan:
            return
        self._apply_buy_fill(
            signal=signal,
            strategy=strategy,
            portfolio=portfolio,
            current_time=current_time,
            price=plan["price"],
            quantity=plan["quantity"],
            fee=plan["fee"],
        )

    def _sell(
        self,
        signal: Signal,
        strategy: BaseStrategy,
        portfolio: Portfolio,
        current_time: int,
    ):
        if signal.price <= 0:
            self._reject_trade(signal, strategy, current_time, "invalid_price")
            return

        position = portfolio.get_position_by_side("LONG")
        if position is None:
            self._reject_trade(signal, strategy, current_time, "no_long_position")
            return

        if self.close_position(position, portfolio, signal.price, signal.reason, current_time):
            strategy.on_position_close(signal.price, signal.reason, position.realized_pnl)

    def _reject_trade(
        self,
        signal: Signal,
        strategy: BaseStrategy,
        current_time: int,
        reason: str,
    ):
        self.trades.append({
            "type": "REJECTED",
            "symbol": strategy.symbol,
            "side": signal.action.upper(),
            "price": signal.price,
            "quantity": 0.0,
            "fee": 0.0,
            "time": current_time,
            "reason": reason if not signal.reason else f"{reason}: {signal.reason}",
            "strategy": strategy.name,
        })
        logger.warning("trade rejected: %s %s %s", strategy.name, strategy.symbol, reason)

    def get_trade_summary(self) -> dict:
        total_fee = sum(t.get("fee", 0.0) for t in self.trades)
        return {
            "total_trades": len(self.trades),
            "open_trades": len([t for t in self.trades if t.get("type") == "OPEN"]),
            "close_trades": len([t for t in self.trades if t.get("type") == "CLOSE"]),
            "rejected_trades": len([t for t in self.trades if t.get("type") == "REJECTED"]),
            "total_fee": total_fee,
        }

    def reset(self):
        self.trades.clear()
        self.order_manager.clear()