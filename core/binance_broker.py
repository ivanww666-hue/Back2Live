# -*- coding: utf-8 -*-
"""Binance spot broker adapter.

The exchange adapter intentionally reuses ``Broker``'s local invariants:
- local BUY validation happens before an exchange order is submitted;
- local SELL validation happens before an exchange order is submitted;
- portfolio state is updated only from normalized exchange fills;
- rejected exchange responses are recorded in the same trade audit stream.
"""

import logging
import time
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional

from binance import Client as BinanceClient
from binance.exceptions import BinanceAPIException, BinanceOrderException

from account.account import Account
from account.portfolio import Portfolio
from account.position import Position
from core.broker import Broker
from core.order_manager import Order, OrderManager, OrderStatus
from risk.risk_manager import RiskManager
from strategy.base_strategy import BaseStrategy, Signal


logger = logging.getLogger(__name__)


class BinanceBroker(Broker):
    """Live Binance implementation of the shared spot broker model."""

    def __init__(
        self,
        account: Account,
        risk_manager: RiskManager,
        order_manager: OrderManager,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
        maker_fee: float = 0.001,
        taker_fee: float = 0.001,
        order_type: str = "MARKET",
        quote_asset: str = "USDT",
    ):
        super().__init__(account, risk_manager, order_manager, maker_fee, taker_fee)
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.order_type = order_type.upper()
        self.quote_asset = quote_asset.upper()
        self.client = BinanceClient(
            api_key=api_key,
            api_secret=api_secret,
            requests_params={"timeout": 120},
            testnet=testnet,
        )
        self._symbol_info_cache = {}
        logger.info("BinanceBroker initialized testnet=%s order_type=%s", testnet, self.order_type)

    def get_account_info(self) -> dict:
        try:
            info = self.client.get_account()
            balances = info.get("balances", [])
            total_usdt = 0.0

            for balance in balances:
                asset = balance["asset"]
                free = float(balance["free"])
                locked = float(balance["locked"])
                total = free + locked
                if asset == self.quote_asset and free > 0:
                    total_usdt = free
                # 其他资产不换算，仅记录日志（资金分配只使用配置的计价资产）
                elif total > 0:
                    logger.debug("非计价资产跳过: %s free=%.4f locked=%.4f", asset, free, locked)

            return {
                "can_trade": info.get("canTrade", False),
                "can_withdraw": info.get("canWithdraw", False),
                "balances": balances,
                "total_usdt": total_usdt,
            }
        except BinanceAPIException as exc:
            logger.error("get_account_info failed: %s", exc)
            return {"error": str(exc)}

    def get_symbol_info(self, symbol: str) -> dict:
        symbol_upper = symbol.upper()
        if symbol_upper in self._symbol_info_cache:
            return self._symbol_info_cache[symbol_upper]

        info = self.client.get_symbol_info(symbol_upper)
        if not info:
            raise ValueError(f"symbol {symbol_upper} does not exist")

        filters = {item["filterType"]: item for item in info.get("filters", [])}
        lot_size = filters.get("LOT_SIZE", {})
        min_notional = filters.get("MIN_NOTIONAL", filters.get("NOTIONAL", {}))
        result = {
            "symbol": info["symbol"],
            "status": info["status"],
            "base_asset": info["baseAsset"],
            "quote_asset": info["quoteAsset"],
            "price_precision": info.get("quotePrecision", 8),
            "quantity_precision": info.get("baseAssetPrecision", 8),
            "min_qty": float(lot_size.get("minQty", 0)),
            "max_qty": float(lot_size.get("maxQty", 0)),
            "step_size": float(lot_size.get("stepSize", 0)),
            "min_notional": float(min_notional.get("minNotional", 0)),
        }
        self._symbol_info_cache[symbol_upper] = result
        return result

    def _round_step(self, value: float, step_size: float) -> float:
        if step_size <= 0:
            return value
        step = Decimal(str(step_size))
        value_dec = Decimal(str(value))
        rounded = (value_dec / step).to_integral_value(rounding=ROUND_DOWN) * step
        return float(rounded)

    def _round_price(self, price: float, symbol_info: dict) -> float:
        return round(price, int(symbol_info["price_precision"]))

    def _round_quantity(self, quantity: float, symbol_info: dict) -> float:
        rounded = self._round_step(quantity, symbol_info["step_size"])
        if rounded < symbol_info["min_qty"]:
            return 0.0
        return rounded

    def _strategy_symbol_for_asset(
        self,
        asset: str,
        strategies_by_symbol: Dict[str, List[BaseStrategy]],
    ) -> Optional[str]:
        """Map an account asset like ETH back to a configured pair like ethusdt."""
        asset_lower = asset.lower()
        quote_lower = self.quote_asset.lower()
        direct_symbol = f"{asset_lower}{quote_lower}"
        if direct_symbol in strategies_by_symbol:
            return direct_symbol

        matches = [
            symbol for symbol in strategies_by_symbol
            if symbol.startswith(asset_lower) and symbol.endswith(quote_lower)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            logger.warning("asset %s matches multiple strategy symbols: %s", asset, matches)
        return None

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: Optional[float] = None,
        order_type: Optional[str] = None,
    ) -> dict:
        symbol_upper = symbol.upper()
        symbol_info = self.get_symbol_info(symbol_upper)
        qty = self._round_quantity(quantity, symbol_info)
        if qty <= 0:
            return {"error": "quantity_too_small"}

        # 检查最低名义价值（交易所 min_notional）
        use_price = price or 0.0
        min_notional = symbol_info.get("min_notional", 0)
        if min_notional > 0 and use_price > 0:
            notional = use_price * qty
            if notional < min_notional:
                logger.warning(
                    f"[{symbol_upper}] 订单价值 {notional:.2f} < min_notional {min_notional}, 拒绝下单"
                )
                return {"error": f"min_notional ({notional:.2f} < {min_notional})"}

        effective_type = (order_type or self.order_type).upper()
        try:
            if effective_type == "LIMIT" and price:
                limit_price = self._round_price(price, symbol_info)
                return self.client.create_order(
                    symbol=symbol_upper,
                    side=side,
                    type=BinanceClient.ORDER_TYPE_LIMIT,
                    timeInForce=BinanceClient.TIME_IN_FORCE_GTC,
                    quantity=qty,
                    price=limit_price,
                )

            return self.client.create_order(
                symbol=symbol_upper,
                side=side,
                type=BinanceClient.ORDER_TYPE_MARKET,
                quantity=qty,
            )
        except (BinanceAPIException, BinanceOrderException) as exc:
            logger.error("place_order failed: %s %s %s", side, symbol_upper, exc)
            return {"error": str(exc)}

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        try:
            self.client.cancel_order(symbol=symbol.upper(), orderId=order_id)
            return True
        except BinanceAPIException as exc:
            logger.error("cancel_order failed: %s", exc)
            return False

    def get_order_status(self, symbol: str, order_id: str) -> dict:
        try:
            return self.client.get_order(symbol=symbol.upper(), orderId=order_id)
        except BinanceAPIException as exc:
            logger.error("get_order_status failed: %s", exc)
            return {"error": str(exc)}

    def _extract_fill(
        self,
        order_result: dict,
        fallback_price: float,
        fallback_quantity: float,
    ) -> dict:
        executed_qty = float(order_result.get("executedQty") or 0)
        fills = order_result.get("fills") or []
        quote_qty = float(order_result.get("cummulativeQuoteQty") or 0)
        avg_price = float(order_result.get("price") or 0)

        if fills:
            total_qty = sum(float(fill.get("qty", 0)) for fill in fills)
            total_quote = sum(
                float(fill.get("qty", 0)) * float(fill.get("price", 0))
                for fill in fills
            )
            if total_qty > 0:
                executed_qty = total_qty
                avg_price = total_quote / total_qty
        elif quote_qty > 0 and executed_qty > 0:
            avg_price = quote_qty / executed_qty

        if executed_qty <= 0 and order_result.get("status") == "FILLED":
            executed_qty = fallback_quantity
        if avg_price <= 0:
            avg_price = fallback_price

        return {
            "filled": order_result.get("status") == "FILLED",
            "quantity": executed_qty,
            "price": avg_price,
            "fee": avg_price * executed_qty * self.taker_fee,
            "order_id": str(order_result.get("orderId", "")),
            "status": order_result.get("status", ""),
        }

    def _annotate_last_rejection(self, order_result: Optional[dict]):
        if not order_result or not self.trades:
            return
        self.trades[-1]["order_id"] = str(order_result.get("orderId", ""))
        self.trades[-1]["exchange_error"] = order_result.get("error", "")
        self.trades[-1]["exchange_status"] = order_result.get("status", "")

    def _reject_live_signal(
        self,
        signal: Signal,
        strategy: BaseStrategy,
        current_time: int,
        reason: str,
        order_result: Optional[dict] = None,
    ):
        self._reject_trade(signal, strategy, current_time, reason)
        self._annotate_last_rejection(order_result)

    def _reject_live_close(
        self,
        position: Position,
        exit_price: float,
        current_time: int,
        reason: str,
        order_result: Optional[dict] = None,
    ):
        trade = {
            "type": "REJECTED",
            "symbol": position.symbol,
            "side": "SELL",
            "price": exit_price,
            "quantity": 0.0,
            "fee": 0.0,
            "time": current_time,
            "reason": reason,
            "strategy": position.strategy_name,
        }
        if order_result:
            trade["order_id"] = str(order_result.get("orderId", ""))
            trade["exchange_error"] = order_result.get("error", "")
            trade["exchange_status"] = order_result.get("status", "")
        self.trades.append(trade)

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

        signal_order_type = getattr(signal, 'order_type', 'MARKET').upper()
        use_limit = (signal_order_type == "LIMIT")
        order_result = self.place_order(
            symbol=strategy.symbol,
            side="BUY",
            quantity=plan["quantity"],
            price=plan["price"] if use_limit else None,
            order_type=signal_order_type,
        )
        if "error" in order_result:
            self._reject_live_signal(
                signal,
                strategy,
                current_time,
                f"exchange_rejected: {order_result['error']}",
                order_result,
            )
            return

        fill = self._extract_fill(order_result, plan["price"], plan["quantity"])
        if not fill["filled"]:
            # 挂单未成交 — 记录为 pending，后续巡检处理
            self._record_pending_order(
                order_result=order_result,
                symbol=strategy.symbol,
                side="BUY",
                strategy_name=strategy.name,
                reason=signal.reason,
                current_time=current_time,
                plan=plan,
            )
            logger.info(
                f"[{strategy.symbol.upper()}] BUY 挂单: "
                f"order_id={fill['order_id']} 数量={plan['quantity']:.8f} "
                f"状态={fill['status']}, 等待成交..."
            )
            return

        logger.info(
            f"[{strategy.symbol.upper()}] BUY 成交: "
            f"数量={fill['quantity']:.8f} 价格={fill['price']:.2f} "
            f"手续费={fill['fee']:.4f}"
        )
        self._apply_buy_fill(
            signal=signal,
            strategy=strategy,
            portfolio=portfolio,
            current_time=current_time,
            price=fill["price"],
            quantity=fill["quantity"],
            fee=fill["fee"],
            order_id=fill["order_id"],
        )
        # 立即成交的市价单，标记订单为 FILLED，防止 check_pending_orders 重复处理
        self.order_manager.update_order_fill(
            order_id=fill["order_id"],
            status=OrderStatus.FILLED,
            filled_quantity=fill["quantity"],
            filled_price=fill["price"],
            filled_time=current_time,
        )

    def close_position(
        self,
        position: Position,
        portfolio: Portfolio,
        exit_price: float,
        reason: str,
        current_time: int,
        order_type: str = "MARKET",
    ) -> bool:
        if not self._can_close_position(position, portfolio):
            return False
        if exit_price <= 0:
            self._reject_live_close(position, exit_price, current_time, "invalid_price")
            return False

        # 防止重复平仓：检查该持仓是否已有 pending 的 SELL 订单
        if self._has_pending_close(position):
            logger.warning(
                f"[{position.symbol.upper()}] 平仓被跳过: "
                f"持仓 {position.entry_order_id} 已有挂单中"
            )
            return False

        requested_quantity = position.quantity
        effective_type = order_type.upper()
        use_limit = (effective_type == "LIMIT")
        order_result = self.place_order(
            symbol=position.symbol,
            side="SELL",
            quantity=requested_quantity,
            price=exit_price if use_limit else None,
            order_type=effective_type,
        )
        if "error" in order_result:
            self._reject_live_close(
                position,
                exit_price,
                current_time,
                f"exchange_rejected: {order_result['error']}",
                order_result,
            )
            return False

        fill = self._extract_fill(order_result, exit_price, requested_quantity)
        if not fill["filled"]:
            # 挂单未成交 — 记录为 pending，后续巡检处理
            self._record_pending_close_order(
                order_result=order_result,
                position=position,
                reason=reason,
                current_time=current_time,
            )
            logger.info(
                f"[{position.symbol.upper()}] SELL 挂单: "
                f"order_id={fill['order_id']} 仓位={position.entry_order_id} "
                f"数量={requested_quantity:.8f} 状态={fill['status']}, 等待成交..."
            )
            return False

        logger.info(
            f"[{position.symbol.upper()}] SELL 成交: "
            f"数量={fill['quantity']:.8f} 价格={fill['price']:.2f} "
            f"手续费={fill['fee']:.4f} 原因={reason}"
        )
        # 传入 symbol 级别的 min_qty 用于残渣仓位判断
        symbol_info = self.get_symbol_info(position.symbol)
        return self._apply_close_fill(
            position=position,
            portfolio=portfolio,
            exit_price=fill["price"],
            quantity=fill["quantity"],
            fee=fill["fee"],
            reason=reason,
            current_time=current_time,
            order_id=fill["order_id"],
            min_quantity=symbol_info.get("min_qty", 0),
        )

    # ─── 挂单管理 ────────────────────────────────────────────

    def _record_pending_order(
        self,
        order_result: dict,
        symbol: str,
        side: str,
        strategy_name: str,
        reason: str,
        current_time: int,
        plan: dict,
    ):
        """记录一笔未成交的挂单到 OrderManager，并冻结资金。"""
        order_id = str(order_result.get("orderId", ""))
        quantity = plan["quantity"]
        price = plan["price"]
        total_cost = plan["total_cost"]

        # 冻结买入所需资金（从 portfolio 暂扣，成交后按订单精确解冻）
        portfolio = self.account.get_portfolio(strategy_name, symbol)
        if portfolio and portfolio.capital >= total_cost:
            portfolio.capital -= total_cost

        self.order_manager.create_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            strategy_name=strategy_name,
            reason=reason,
            order_id=order_id,
            created_time=current_time,
        )

        self.trades.append({
            "type": "PENDING",
            "symbol": symbol,
            "side": side,
            "price": price,
            "quantity": quantity,
            "fee": 0.0,
            "time": current_time,
            "reason": reason,
            "strategy": strategy_name,
            "order_id": order_id,
            "exchange_status": order_result.get("status", ""),
        })

    def _record_pending_close_order(
        self,
        order_result: dict,
        position: Position,
        reason: str,
        current_time: int,
    ):
        """记录一笔未成交的平仓挂单到 OrderManager。"""
        order_id = str(order_result.get("orderId", ""))
        self.order_manager.create_order(
            symbol=position.symbol,
            side="SELL",
            quantity=position.quantity,
            price=position.entry_price,
            strategy_name=position.strategy_name,
            reason=reason,
            order_id=order_id,
            entry_order_id=position.entry_order_id,
            created_time=current_time,
        )

        self.trades.append({
            "type": "PENDING_CLOSE",
            "symbol": position.symbol,
            "side": "SELL",
            "price": 0.0,
            "quantity": position.quantity,
            "fee": 0.0,
            "time": current_time,
            "reason": reason,
            "strategy": position.strategy_name,
            "order_id": order_id,
            "entry_order_id": position.entry_order_id,
            "exchange_status": order_result.get("status", ""),
        })

    def check_pending_orders(
        self,
        strategies: List[BaseStrategy],
        current_time: int,
        pending_timeout_ms: int = 300000,
    ):
        """巡检所有挂单 — 每根K线调用一次。

        对每个 PENDING 订单：
        1. 查 Binance 最新状态
        2. FILLED → 执行成交逻辑（建仓/平仓）
        3. REJECTED/EXPIRED/CANCELED → 恢复冻结资金/取消订单
        4. NEW/PARTIALLY_FILLED 超时 → 撤单 + 恢复资金

        Args:
            strategies: 策略列表（用于调用 on_position_open/close 回调）
            current_time: 当前时间戳
            pending_timeout_ms: 挂单超时（毫秒），默认 300000 (5分钟)
        """
        pending = self.order_manager.pending_orders
        if not pending:
            return

        for order in pending:
            symbol = order.symbol

            # 查交易所最新状态
            status_result = self.get_order_status(symbol, order.order_id)
            if "error" in status_result:
                logger.warning(
                    f"[{symbol.upper()}] 查单失败 order_id={order.order_id}: "
                    f"{status_result['error']}"
                )
                continue

            exchange_status = status_result.get("status", "")
            executed_qty = float(status_result.get("executedQty", 0))
            cummulative_quote = float(status_result.get("cummulativeQuoteQty", 0))
            avg_price = cummulative_quote / executed_qty if executed_qty > 0 else 0.0

            # ── 已成交 ──
            if exchange_status == "FILLED":
                self._handle_pending_filled(
                    order=order,
                    executed_qty=executed_qty,
                    avg_price=avg_price,
                    current_time=current_time,
                    strategies=strategies,
                )
                continue

            # ── 被拒绝 / 过期 / 已取消 ──
            if exchange_status in ("REJECTED", "EXPIRED", "CANCELED"):
                self._handle_pending_failed(
                    order=order,
                    exchange_status=exchange_status,
                )
                continue

            # ── 超时检查 ──
            elapsed = current_time - order.created_time
            if pending_timeout_ms > 0 and elapsed > pending_timeout_ms:
                logger.warning(
                    f"[{symbol.upper()}] 挂单超时 order_id={order.order_id} "
                    f"elapsed={elapsed}ms, 尝试撤单..."
                )
                if self.cancel_order(symbol, order.order_id):
                    self._handle_pending_failed(
                        order=order,
                        exchange_status="CANCELED_BY_TIMEOUT",
                    )
                # 撤单失败也不阻塞，下次巡检继续尝试

            # ── 部分成交 — 增量创建持仓 ──
            elif exchange_status == "PARTIALLY_FILLED":
                if executed_qty > 0 and executed_qty > order.filled_quantity:
                    delta_qty = executed_qty - order.filled_quantity
                    self._handle_partial_fill(
                        order=order,
                        delta_qty=delta_qty,
                        avg_price=avg_price,
                        current_time=current_time,
                        strategies=strategies,
                    )
                    self.order_manager.update_order_fill(
                        order_id=order.order_id,
                        status=OrderStatus.PENDING,
                        filled_quantity=executed_qty,
                        filled_price=avg_price,
                    )

    def _handle_pending_filled(
        self,
        order: Order,
        executed_qty: float,
        avg_price: float,
        current_time: int,
        strategies: List[BaseStrategy],
    ):
        """处理已成交的挂单 — 建仓或平仓。"""
        order_id = order.order_id
        symbol = order.symbol

        # 防止多线程并发：定时巡检与 K 线回调可能同时处理同一笔订单
        if order.status != OrderStatus.PENDING:
            logger.debug(
                f"[{symbol.upper()}] 订单 {order_id} 已被其他线程处理, 跳过"
            )
            return

        previous_filled_qty = order.filled_quantity
        previous_filled_price = order.filled_price

        portfolio = self.account.get_portfolio(order.strategy_name, symbol)
        if not portfolio:
            logger.warning(f"[{symbol.upper()}] 挂单成交但无 portfolio: {order.strategy_name}")
            self.order_manager.update_order_fill(
                order_id=order_id,
                status=OrderStatus.FILLED,
                filled_quantity=executed_qty,
                filled_price=avg_price,
                filled_time=current_time,
            )
            return

        fee = avg_price * executed_qty * self.taker_fee

        if order.side == "BUY":
            # 匹配策略实例
            strategy = next(
                (s for s in strategies if s.name == order.strategy_name and s.symbol == symbol),
                None,
            )
            if strategy is None:
                logger.warning(
                    f"[{symbol.upper()}] BUY 挂单成交但找不到策略: {order.strategy_name}"
                )
                self.order_manager.update_order_fill(
                    order_id=order_id,
                    status=OrderStatus.FILLED,
                    filled_quantity=executed_qty,
                    filled_price=avg_price,
                    filled_time=current_time,
                )
                return

            # 如果之前有部分成交，只处理剩余增量
            if previous_filled_qty > 0:
                delta_qty = executed_qty - previous_filled_qty
                if delta_qty > 0:
                    # 逆推边际成交价（避免全局均价导致的持仓均价错误）
                    total_fill_cost = avg_price * executed_qty
                    prev_fill_cost = previous_filled_price * previous_filled_qty
                    marginal_price = (total_fill_cost - prev_fill_cost) / delta_qty if delta_qty > 0 else avg_price
                    self._handle_partial_fill(
                        order=order,
                        delta_qty=delta_qty,
                        avg_price=marginal_price,
                        current_time=current_time,
                        strategies=strategies,
                    )
                logger.info(
                    f"[{symbol.upper()}] 挂单全部成交 (增量={delta_qty:.8f}): "
                    f"order_id={order_id} 总={executed_qty:.8f} 均价={avg_price:.2f}"
                )
            else:
                # 首次直接全部成交（无历史部分成交）
                unfreeze = order.price * order.quantity * (1 + self.taker_fee)
                portfolio.capital += unfreeze

                signal = Signal(
                    action="BUY",
                    price=avg_price,
                    timestamp=current_time,
                    reason=order.reason,
                )
                self._apply_buy_fill(
                    signal=signal,
                    strategy=strategy,
                    portfolio=portfolio,
                    current_time=current_time,
                    price=avg_price,
                    quantity=executed_qty,
                    fee=fee,
                    order_id=order_id,
                )
                logger.info(
                    f"[{symbol.upper()}] 挂单成交 → BUY: "
                    f"order_id={order_id} 数量={executed_qty:.8f} 价格={avg_price:.2f}"
                )

        elif order.side == "SELL":
            # 按 entry_order_id 精确匹配持仓，避免多持仓时平错仓
            position = None
            if order.entry_order_id:
                position = portfolio.get_position_by_entry_id(order.entry_order_id)
            if position is None:
                # 回退：如果挂单没有关联 entry_order_id（旧数据兼容），取任意 LONG
                position = portfolio.get_position_by_side("LONG")
            strategy = next(
                (s for s in strategies if s.name == order.strategy_name and s.symbol == symbol),
                None,
            )
            if position:
                # 如果之前有部分成交，只平仓剩余增量
                if previous_filled_qty > 0:
                    delta_qty = executed_qty - previous_filled_qty
                    if delta_qty > 0:
                        # 逆推边际成交价
                        total_fill_cost = avg_price * executed_qty
                        prev_fill_cost = previous_filled_price * previous_filled_qty
                        marginal_price = (total_fill_cost - prev_fill_cost) / delta_qty if delta_qty > 0 else avg_price
                        fee_delta = marginal_price * delta_qty * self.taker_fee
                        self._apply_close_fill(
                            position=position,
                            portfolio=portfolio,
                            exit_price=marginal_price,
                            quantity=delta_qty,
                            fee=fee_delta,
                            reason=order.reason,
                            current_time=current_time,
                            order_id=order_id,
                        )
                    if strategy:
                        strategy.on_position_close(
                            avg_price, order.reason, position.realized_pnl
                        )
                    logger.info(
                        f"[{symbol.upper()}] 挂单全部成交 (增量={delta_qty:.8f}): "
                        f"order_id={order_id} 总={executed_qty:.8f} 均价={avg_price:.2f}"
                    )
                else:
                    symbol_info = self.get_symbol_info(symbol)
                    self._apply_close_fill(
                        position=position,
                        portfolio=portfolio,
                        exit_price=avg_price,
                        quantity=executed_qty,
                        fee=fee,
                        reason=order.reason,
                        current_time=current_time,
                        order_id=order_id,
                        min_quantity=symbol_info.get("min_qty", 0),
                    )
                    if strategy:
                        strategy.on_position_close(
                            avg_price, order.reason, position.realized_pnl
                        )
                    logger.info(
                        f"[{symbol.upper()}] 挂单成交 → SELL: "
                        f"order_id={order_id} 数量={executed_qty:.8f} 价格={avg_price:.2f}"
                    )
            else:
                logger.warning(
                    f"[{symbol.upper()}] 挂单成交 SELL 但找不到对应持仓: "
                    f"order_id={order_id} entry_order_id={order.entry_order_id}"
                )

        self.order_manager.update_order_fill(
            order_id=order_id,
            status=OrderStatus.FILLED,
            filled_quantity=executed_qty,
            filled_price=avg_price,
            filled_time=current_time,
        )

    def _handle_pending_failed(
        self,
        order: Order,
        exchange_status: str,
    ):
        """处理挂单失败（拒绝/过期/取消/超时撤单）— 恢复冻结资金。"""
        order_id = order.order_id
        symbol = order.symbol

        # 防止多线程并发重复处理
        if order.status != OrderStatus.PENDING:
            return

        self.order_manager.cancel_order(order_id)

        if order.side == "BUY":
            portfolio = self.account.get_portfolio(order.strategy_name, symbol)
            if portfolio:
                # 只退还未成交部分的冻结资金（部分成交已解冻对应比例）
                unfilled_ratio = (order.quantity - order.filled_quantity) / order.quantity if order.quantity > 0 else 1.0
                refund = order.price * order.quantity * (1 + self.taker_fee) * unfilled_ratio
                portfolio.capital += refund

            self.trades.append({
                "type": "PENDING_FAILED",
                "symbol": symbol,
                "side": order.side,
                "price": order.price,
                "quantity": order.quantity,
                "fee": 0.0,
                "time": int(time.time() * 1000),
                "reason": f"pending_failed: {exchange_status}",
                "strategy": order.strategy_name,
                "order_id": order_id,
                "exchange_status": exchange_status,
            })
            logger.warning(
                f"[{symbol.upper()}] 挂单失败 order_id={order_id} "
                f"status={exchange_status}, 已恢复冻结资金"
            )

        else:
            self.trades.append({
                "type": "PENDING_CLOSE_FAILED",
                "symbol": symbol,
                "side": order.side,
                "price": 0.0,
                "quantity": order.quantity,
                "fee": 0.0,
                "time": int(time.time() * 1000),
                "reason": f"pending_close_failed: {exchange_status}",
                "strategy": order.strategy_name,
                "order_id": order_id,
                "exchange_status": exchange_status,
            })
            logger.warning(
                f"[{symbol.upper()}] 平仓挂单失败 order_id={order_id} "
                f"status={exchange_status}"
            )

    def _handle_partial_fill(
        self,
        order: Order,
        delta_qty: float,
        avg_price: float,
        current_time: int,
        strategies: List[BaseStrategy],
    ):
        """处理部分成交 — 按增量创建/更新持仓。

        BUY 部分成交：解冻对应比例资金，创建或追加持仓。
        SELL 部分成交：部分平仓，减少持仓数量。
        """
        if delta_qty <= 0:
            return

        order_id = order.order_id
        symbol = order.symbol
        portfolio = self.account.get_portfolio(order.strategy_name, symbol)
        if not portfolio:
            return

        if order.side == "BUY":
            strategy = next(
                (s for s in strategies if s.name == order.strategy_name and s.symbol == symbol),
                None,
            )
            if strategy is None:
                logger.warning(
                    f"[{symbol.upper()}] BUY 部分成交但找不到策略: {order.strategy_name}"
                )
                return

            # 按比例解冻资金
            frozen_ratio = delta_qty / order.quantity if order.quantity > 0 else 0.0
            planned_cost = order.price * order.quantity * (1 + self.taker_fee)
            unfreeze = planned_cost * frozen_ratio
            portfolio.capital += unfreeze

            fee = avg_price * delta_qty * self.taker_fee

            # 查找同一订单已有的部分持仓
            existing_position = next(
                (p for p in portfolio.positions if p.entry_order_id == order_id), None
            )
            if existing_position:
                # 追加数量，更新均价，扣除实际成交资金
                old_qty = existing_position.quantity
                old_cost = existing_position.entry_price * old_qty
                new_total_qty = old_qty + delta_qty
                new_avg_price = (old_cost + avg_price * delta_qty) / new_total_qty
                existing_position.quantity = new_total_qty
                existing_position.entry_price = new_avg_price
                existing_position.stop_loss = self._risk_manager_for(portfolio).calculate_stop_loss(new_avg_price, "LONG")
                existing_position.take_profit = self._risk_manager_for(portfolio).calculate_take_profit(new_avg_price, "LONG")
                existing_position.update_unrealized_pnl(avg_price)
                existing_position.update_high_low(avg_price)
                # 扣除增量资金（先解冻再扣款，final capital 正确）
                portfolio.capital -= avg_price * delta_qty + fee

                self.trades.append({
                    "type": "PARTIAL_ADD",
                    "symbol": symbol,
                    "side": "BUY",
                    "price": avg_price,
                    "quantity": delta_qty,
                    "fee": fee,
                    "time": current_time,
                    "reason": order.reason,
                    "strategy": order.strategy_name,
                    "order_id": order_id,
                    "new_total_qty": new_total_qty,
                    "new_avg_price": new_avg_price,
                })
                logger.info(
                    f"[{symbol.upper()}] 部分成交追加: "
                    f"order_id={order_id} +{delta_qty:.8f} "
                    f"总={new_total_qty:.8f} 均价={new_avg_price:.2f}"
                )
            else:
                # 首次部分成交：创建新持仓
                signal = Signal(
                    action="BUY",
                    price=avg_price,
                    timestamp=current_time,
                    reason=order.reason,
                )
                self._apply_buy_fill(
                    signal=signal,
                    strategy=strategy,
                    portfolio=portfolio,
                    current_time=current_time,
                    price=avg_price,
                    quantity=delta_qty,
                    fee=fee,
                    order_id=order_id,
                )
                logger.info(
                    f"[{symbol.upper()}] 部分成交建仓: "
                    f"order_id={order_id} 数量={delta_qty:.8f} 价格={avg_price:.2f}"
                )

        elif order.side == "SELL":
            position = None
            if order.entry_order_id:
                position = portfolio.get_position_by_entry_id(order.entry_order_id)
            if position is None:
                position = portfolio.get_position_by_side("LONG")
            if position:
                fee = avg_price * delta_qty * self.taker_fee
                self._apply_close_fill(
                    position=position,
                    portfolio=portfolio,
                    exit_price=avg_price,
                    quantity=delta_qty,
                    fee=fee,
                    reason=order.reason,
                    current_time=current_time,
                    order_id=order_id,
                )
                logger.info(
                    f"[{symbol.upper()}] 部分成交平仓: "
                    f"order_id={order_id} 数量={delta_qty:.8f} 价格={avg_price:.2f}"
                )

    def _has_pending_close(self, position: Position) -> bool:
        """检查该持仓是否已有 pending 的 SELL 订单，防止重复平仓。
        
        通过 entry_order_id 精确匹配，而非仅按 symbol，避免多持仓时误拦截。
        """
        for order in self.order_manager.pending_orders:
            if (
                order.side == "SELL"
                and order.symbol == position.symbol
                and order.entry_order_id == position.entry_order_id
            ):
                return True
        return False

    def get_open_orders(self, symbol: Optional[str] = None) -> List[dict]:
        try:
            if symbol:
                return self.client.get_open_orders(symbol=symbol.upper())
            return self.client.get_open_orders()
        except BinanceAPIException as exc:
            logger.error("get_open_orders failed: %s", exc)
            return []

    def get_balance(self, asset: str = "USDT") -> float:
        try:
            balance = self.client.get_asset_balance(asset=asset)
            return float(balance["free"]) if balance else 0.0
        except BinanceAPIException as exc:
            logger.error("get_balance failed: %s", exc)
            return 0.0

    def get_current_price(self, symbol: str) -> float:
        try:
            ticker = self.client.get_symbol_ticker(symbol=symbol.upper())
            return float(ticker["price"])
        except BinanceAPIException as exc:
            logger.error("get_current_price failed: %s", exc)
            return 0.0

    def sync_balance(self):
        info = self.get_account_info()
        if "error" not in info:
            self.account.initial_capital = info.get("total_usdt", 0.0)
            logger.info("account balance synced: %.2f USDT", self.account.initial_capital)

    def sync_positions(self, strategies: List[BaseStrategy]):
        """从 Binance 同步当前持仓，重建本地 Portfolio 状态。

        遍历交易所的当前持仓（通过 account info 中的 non-zero balances），
        对每个有余额的资产查找所有匹配策略，按资金比例分配持仓数量。

        多策略同 symbol 场景：交易所只有一份资产余额，按各 portfolio 的
        initial_capital 占比分配，确保每个 portfolio 的权益计算一致。

        Args:
            strategies: 策略列表（用于匹配 symbol 和创建持仓回调）
        """
        try:
            info = self.client.get_account()
            balances = info.get("balances", [])
        except BinanceAPIException as exc:
            logger.error("sync_positions failed to get account: %s", exc)
            return

        # 按 symbol 分组策略（支持多策略同 symbol）
        strategies_by_symbol: Dict[str, List[BaseStrategy]] = {}
        for s in strategies:
            strategies_by_symbol.setdefault(s.symbol.lower(), []).append(s)

        synced_count = 0
        for balance in balances:
            asset = balance["asset"]
            free = float(balance["free"])
            locked = float(balance["locked"])
            total = free + locked
            # 跳过配置的计价资产（已在 sync_balance 中作为现金处理）
            if total <= 0 or asset == self.quote_asset:
                continue

            symbol = self._strategy_symbol_for_asset(asset, strategies_by_symbol)
            if not symbol:
                logger.debug("sync_positions: no strategy symbol matches asset %s", asset)
                continue
            matched_strategies = strategies_by_symbol.get(symbol, [])
            if not matched_strategies:
                continue

            # 获取当前价格（共享给所有匹配策略）
            try:
                ticker = self.client.get_symbol_ticker(symbol=symbol.upper())
                current_price = float(ticker["price"])
            except Exception:
                logger.warning(f"sync_positions: cannot get price for {symbol.upper()}, skip")
                continue

            # 估算入场均价
            avg_buy_price = current_price
            try:
                trades_list = self.client.get_my_trades(symbol=symbol.upper(), limit=50)
                buy_trades = [t for t in trades_list if not t["isBuyer"] is False]
                bought_qty = 0.0
                bought_cost = 0.0
                for t in buy_trades:
                    qty = float(t["qty"])
                    price = float(t["price"])
                    bought_qty += qty
                    bought_cost += qty * price
                if bought_qty > 0:
                    avg_buy_price = bought_cost / bought_qty
            except Exception:
                pass

            # 按资金比例分配持仓数量
            total_allocated = sum(
                self.account.get_portfolio(s.name, s.symbol).initial_capital
                for s in matched_strategies
                if self.account.get_portfolio(s.name, s.symbol)
            )
            if total_allocated <= 0:
                total_allocated = len(matched_strategies)

            locked_per_strategy = locked / len(matched_strategies) if matched_strategies else 0

            for strategy in matched_strategies:
                portfolio = self.account.get_portfolio(strategy.name, symbol)
                if not portfolio:
                    continue

                # 检查是否已有此资产持仓
                existing = portfolio.get_position_by_side("LONG")
                if existing:
                    logger.info(f"sync_positions: {strategy.name}/{symbol} already has position, skip")
                    continue

                # 按比例分配数量
                ratio = portfolio.initial_capital / total_allocated if total_allocated > 0 else 1.0 / len(matched_strategies)
                position_qty = total * ratio

                position = Position(
                    symbol=symbol,
                    side="LONG",
                    strategy_name=strategy.name,
                    entry_price=avg_buy_price,
                    entry_time=int(time.time() * 1000),
                    quantity=position_qty,
                    entry_order_id=f"sync_{strategy.name}_{int(time.time())}",
                    stop_loss=self._risk_manager_for(portfolio).calculate_stop_loss(avg_buy_price, "LONG"),
                    take_profit=self._risk_manager_for(portfolio).calculate_take_profit(avg_buy_price, "LONG"),
                    highest_price=max(avg_buy_price, current_price),
                )
                position.update_unrealized_pnl(current_price)
                portfolio.add_position(position)

                # 锁定部分的资金计入 capital 扣减
                if locked_per_strategy > 0 and portfolio.capital >= avg_buy_price * locked_per_strategy:
                    portfolio.capital -= avg_buy_price * locked_per_strategy

                synced_count += 1
                logger.info(
                    f"sync_positions: {strategy.name}/{symbol} "
                    f"qty={position_qty:.8f} (总={total:.8f} × {ratio:.2%}) "
                    f"avg_price={avg_buy_price:.2f}"
                )

        if synced_count > 0:
            logger.info(f"sync_positions: synced {synced_count} positions from Binance")
        else:
            logger.info("sync_positions: no positions to sync")

    def sync_open_orders(self, strategies: Optional[List[BaseStrategy]] = None):
        strategies_by_symbol: Dict[str, List[BaseStrategy]] = {}
        for strategy in strategies or []:
            strategies_by_symbol.setdefault(strategy.symbol.lower(), []).append(strategy)

        orders = self.get_open_orders()
        synced_count = 0
        for order_data in orders:
            symbol = order_data.get("symbol", "").lower()
            matched = strategies_by_symbol.get(symbol, [])
            if len(matched) != 1:
                logger.warning(
                    "skip synced open order %s/%s: cannot uniquely map to strategy",
                    order_data.get("symbol", ""),
                    order_data.get("orderId", ""),
                )
                continue
            strategy = matched[0]
            self.order_manager.create_order(
                symbol=symbol,
                side=order_data.get("side", ""),
                quantity=float(order_data.get("origQty", 0)),
                price=float(order_data.get("price", 0)),
                strategy_name=strategy.name,
                reason="synced_from_binance",
                order_id=str(order_data.get("orderId", "")),
            )
            synced_count += 1
        logger.info("synced %d/%d open orders", synced_count, len(orders))
