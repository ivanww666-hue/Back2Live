# -*- coding: utf-8 -*-
"""
实盘引擎模块
===========
实盘交易引擎，连接 Binance 交易所进行实盘或模拟盘交易。

与回测引擎共享相同的核心逻辑（账户、风控、策略接口），
但使用实时数据流和真实/模拟经纪商执行交易。

状态持久化:
- 每根 K 线处理后自动保存 portfolio + pending orders 到 Redis
- 引擎重启时优先从 Redis 恢复，Redis 不可用时回退到 Binance API 同步
"""

import json
import logging
import time
import threading
from typing import Optional, Dict, Any, List
from datetime import datetime

from data.datafeed import Bar
from account.account import Account
from account.portfolio import Portfolio
from account.position import Position
from risk.risk_manager import RiskManager
from strategy.base_strategy import BaseStrategy, Signal
from core.order_manager import OrderManager, Order, OrderType, OrderStatus
from core.broker import Broker
from core.engine import TradingEngine

logger = logging.getLogger(__name__)


class LiveEngine(TradingEngine):
    """
    实盘引擎

    负责:
    1. 连接 Binance 实时数据流
    2. 驱动策略实时运行
    3. 通过 BinanceBroker 执行真实/模拟交易
    4. 实时风控检查
    5. 记录实时权益曲线
    6. Redis 状态持久化（重启恢复）
    """

    def __init__(self, initial_capital: float = 10000.0,
                 maker_fee: float = 0.001, taker_fee: float = 0.001,
                 config: Optional[dict] = None):
        """
        Args:
            initial_capital: 初始资金
            maker_fee: Maker 费率
            taker_fee: Taker 费率
            config: 配置字典
        """
        super().__init__(
            initial_capital=initial_capital,
            maker_fee=maker_fee,
            taker_fee=taker_fee,
            config=config,
        )

        # Binance 相关（从 config["binance"] 下读取）
        binance_cfg = self.config.get("binance", {})
        self.api_key = binance_cfg.get("api_key", "")
        self.api_secret = binance_cfg.get("api_secret", "")
        self.testnet = binance_cfg.get("testnet", True)
        self.pending_timeout_ms = self.config.get("pending_timeout_ms", 300000)

        # 计价资产（从 config["binance"]["quote_asset"] 读取，默认 USDT）
        self.quote_asset = binance_cfg.get("quote_asset", "USDT").upper()

        # 实盘经纪商（延迟初始化）
        self._binance_broker = None

        # Redis 状态持久化
        redis_cfg = self.config.get("redis", {}) or {}
        if redis_cfg:
            from utils.state_store import StateStore
            self.state_store = StateStore(
                host=redis_cfg.get("host", "localhost"),
                port=redis_cfg.get("port", 6379),
                db=redis_cfg.get("db", 0),
                password=redis_cfg.get("password"),
                ttl_seconds=redis_cfg.get("ttl_seconds", 86400 * 7),
                equity_max_len=redis_cfg.get("equity_max_len", 2000),
            )
        else:
            self.state_store = None

        # 运行状态
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._streams: List[Any] = []

        # 权益曲线记录
        self.equity_curve: List[dict] = []
        self._last_record_time = 0

        # 当前价格缓存
        self._prices: Dict[str, float] = {}

        # 定时巡检（独立于 K 线周期）
        self._pending_check_interval_sec = self.config.get("pending_check_interval_sec", 60)
        self._last_pending_check_time = 0

        # 线程锁（_main_loop 定时器 与 WebSocket 回调可能并发）
        self._pending_check_lock = threading.Lock()

    @property
    def binance_broker(self):
        """延迟初始化 BinanceBroker"""
        if self._binance_broker is None:
            from core.binance_broker import BinanceBroker
            self._binance_broker = BinanceBroker(
                api_key=self.api_key,
                api_secret=self.api_secret,
                testnet=self.testnet,
                account=self.account,
                risk_manager=self.risk_manager,
                order_manager=self.order_manager,
                maker_fee=self.maker_fee,
                taker_fee=self.taker_fee,
                quote_asset=self.quote_asset,
            )
        return self._binance_broker

    # ── Redis 状态保存/恢复 ────────────────────────────

    def _save_state(self, strategy: BaseStrategy, bar_time: int = 0):
        """保存单个策略-品种的完整状态到 Redis。"""
        if not self.state_store or not self.state_store.available:
            return
        portfolio = self.account.get_portfolio(strategy.name, strategy.symbol)
        if not portfolio:
            return
        try:
            portfolio_data = {
                "initial_capital": portfolio.initial_capital,
                "max_positions": portfolio.max_positions,
                "capital": portfolio.capital,
                "positions": [p.to_dict() for p in portfolio.positions],
                "closed_positions": [p.to_dict() for p in portfolio.closed_positions],
                "risk": {
                    "stop_loss_pct": portfolio.risk_manager.stop_loss_pct if portfolio.risk_manager else 0,
                    "take_profit_pct": portfolio.risk_manager.take_profit_pct if portfolio.risk_manager else 0,
                    "trailing_stop_pct": portfolio.risk_manager.trailing_stop_pct if portfolio.risk_manager else 0,
                    "position_size_pct": portfolio.risk_manager.position_size_pct if portfolio.risk_manager else 0,
                },
                "last_save_time": bar_time or int(time.time() * 1000),
            }
            pending_orders = self.order_manager.to_dict_list(strategy_name=strategy.name)
            self.state_store.save_all(
                strategy_name=strategy.name,
                symbol=strategy.symbol,
                portfolio_data=portfolio_data,
                pending_orders=pending_orders,
            )
        except Exception as e:
            logger.error(f"[{strategy.name}] 保存状态失败: {e}")

    def _restore_state(self, strategy: BaseStrategy) -> bool:
        """从 Redis 恢复单个策略-品种的状态。返回 True 表示恢复成功。"""
        if not self.state_store or not self.state_store.available:
            return False
        try:
            data = self.state_store.load_portfolio(strategy.name, strategy.symbol)
            if not data:
                return False

            # 恢复 risk manager
            risk_cfg = data.get("risk", {})
            risk_manager = RiskManager(
                stop_loss_pct=risk_cfg.get("stop_loss_pct", 2.0),
                take_profit_pct=risk_cfg.get("take_profit_pct", 6.0),
                trailing_stop_pct=risk_cfg.get("trailing_stop_pct", 1.5),
                position_size_pct=risk_cfg.get("position_size_pct", 20.0),
            )

            # 恢复或创建 portfolio
            portfolio = self.account.ensure_portfolio(
                strategy_name=strategy.name,
                symbol=strategy.symbol,
                capital=data.get("initial_capital", self.initial_capital),
                max_positions=data.get("max_positions", 3),
                risk_manager=risk_manager,
            )

            # 恢复资金
            portfolio.capital = data.get("capital", portfolio.initial_capital)

            # 恢复持仓
            portfolio._positions.clear()
            for pos_dict in data.get("positions", []):
                pos = Position.from_dict(pos_dict)
                portfolio._positions.append(pos)

            # 恢复已平仓持仓
            portfolio._closed_positions.clear()
            for pos_dict in data.get("closed_positions", []):
                pos = Position.from_dict(pos_dict)
                portfolio._closed_positions.append(pos)

            # 恢复挂单
            orders_data = self.state_store.load_pending_orders(strategy.name, strategy.symbol)
            if orders_data:
                self.order_manager.load_from_dict_list(orders_data)

            # 恢复权益曲线
            equity = self.state_store.load_equity(strategy.name, strategy.symbol)
            if equity:
                self.equity_curve = equity

            logger.info(
                f"[{strategy.name}] 从 Redis 恢复状态: "
                f"capital={portfolio.capital:.2f}, "
                f"positions={len(portfolio.positions)}, "
                f"pending_orders={len(orders_data)}, "
                f"equity={len(equity)}"
            )
            return True
        except Exception as e:
            logger.error(f"[{strategy.name}] 从 Redis 恢复状态失败: {e}", exc_info=True)
            return False

    def run(self, symbols: list, interval: str = "1m"):
        """
        启动实盘运行

        Args:
            symbols: 交易对列表
            interval: K线周期
        """
        if self._running:
            logger.warning("引擎已在运行中")
            return

        self._running = True
        self._is_running = True

        # 为每个 (策略, 品种) 创建投资组合
        strategy_symbols = []
        for strategy in self.strategies:
            target_symbol = (strategy.config.get("symbol") or strategy.symbol or (symbols[0] if symbols else "")).lower()
            strategy.symbol = target_symbol
            strategy.interval = interval
            strategy_symbols.append((strategy.name, target_symbol))

        # 先创建 portfolios（如果还未分配）
        if not self.account.portfolios:
            keys = strategy_symbols
            max_positions = {
                self.account._make_key(strategy.name, symbol): strategy.config.get("max_positions", 3)
                for strategy, (_, symbol) in zip(self.strategies, strategy_symbols)
            }
            risk_managers = {
                self.account._make_key(strategy.name, symbol): self.create_risk_manager_from_config(strategy.config)
                for strategy, (_, symbol) in zip(self.strategies, strategy_symbols)
            }
            self.account.allocate_equal(keys, max_positions, risk_managers)

        # ── 第一步：尝试从 Redis 恢复状态 ──
        restored_count = 0
        for strategy in self.strategies:
            if self._restore_state(strategy):
                restored_count += 1

        if restored_count == 0:
            # ── Redis 不可用或无数据 → 回退到 Binance API 同步 ──
            logger.info("Redis 状态不可用，回退到 Binance API 同步...")
            self.sync_account_balance()
            self.sync_open_orders()
            self.binance_broker.sync_positions(self.strategies)

        # 确保所有 portfolio 都有 risk_manager
        for strategy in self.strategies:
            symbol = strategy.symbol
            portfolio = self.account.get_portfolio(strategy.name, symbol)
            if not portfolio:
                portfolio = self.account.create_portfolio(
                    strategy_name=strategy.name,
                    symbol=symbol,
                    capital=self.initial_capital,
                    max_positions=strategy.config.get("max_positions", 3),
                    risk_manager=self.create_risk_manager_from_config(strategy.config),
                )
            elif portfolio.risk_manager is None:
                portfolio.set_risk_manager(self.create_risk_manager_from_config(strategy.config))
            strategy.on_start()
            logger.info(
                f"策略 [{strategy.name}] 已启动 | 品种={symbol} | "
                f"最小K线数={strategy.min_bars}"
            )
            # 首次保存当前状态到 Redis
            self._save_state(strategy)

        # ── 保存引擎元信息 ──
        if self.state_store and self.state_store.available:
            self.state_store.save_meta({
                "interval": interval,
                "symbols": [s.lower() for s in symbols],
                "strategies": [s.name for s in self.strategies],
                "testnet": self.testnet,
                "start_time": int(time.time() * 1000),
            })

        # 预热策略（加载历史K线）
        for strategy in self.strategies:
            self._warmup_strategy(strategy, interval)

        # 启动数据流
        self._start_streams(symbols, interval)

        # 启动主循环
        self._thread = threading.Thread(target=self._main_loop, daemon=True)
        self._thread.start()

        logger.info(f"实盘引擎已启动: {symbols} {interval}")

    def _warmup_strategy(self, strategy: BaseStrategy, interval: str):
        """预热策略 — 加载历史K线让指标到达最小要求"""
        try:
            warmup_count = strategy.min_bars
            interval_binance = {
                "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m",
                "30m": "30m", "1h": "1h", "2h": "2h", "4h": "4h",
                "6h": "6h", "8h": "8h", "12h": "12h", "1d": "1d",
                "3d": "3d", "1w": "1w", "1M": "1M",
            }.get(interval, "1h")

            from binance import Client as BinanceClient
            client = BinanceClient()
            symbol_upper = strategy.symbol.upper()
            klines = client.get_klines(
                symbol=symbol_upper, interval=interval_binance,
                limit=warmup_count + 5,
            )

            if not klines:
                logger.warning(f"[{strategy.name}] 预热失败: 未获取到历史K线")
                return

            logger.info(
                f"[{strategy.name}] 开始预热 ({strategy.symbol}): "
                f"需 {warmup_count} 根, 已获取 {len(klines)} 根"
            )

            count = 0
            for k in klines:
                bar = Bar(
                    time=k[0],
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                    close_time=k[6],
                    quote_volume=float(k[7]) if len(k) > 7 else 0.0,
                    trade_count=k[8] if len(k) > 8 else 0,
                )
                bar.symbol = strategy.symbol
                strategy.on_bar(bar)  # 喂数据，忽略信号
                count += 1

            # 缓存最后一根K线的价格
            if count > 0:
                last_bar = Bar(
                    time=klines[-1][0],
                    open=float(klines[-1][1]),
                    high=float(klines[-1][2]),
                    low=float(klines[-1][3]),
                    close=float(klines[-1][4]),
                    volume=float(klines[-1][5]),
                    close_time=klines[-1][6],
                )
                last_bar.symbol = strategy.symbol
                self._prices[strategy.symbol] = last_bar.close

            logger.info(
                f"[{strategy.name}] 预热完成: {count}/{warmup_count} 根, "
                f"当前价格: ${self._prices.get(strategy.symbol, 0):,.2f}"
            )
        except Exception as e:
            logger.error(f"[{strategy.name}] 预热失败: {e}", exc_info=True)

    def _start_streams(self, symbols: list, interval: str):
        """启动数据流"""
        try:
            from data.binance_feed import BinanceWebSocketClient, kline_stream, Interval

            # map interval string to Interval enum
            _interval_map = {
                "1m": Interval.MINUTE_1, "3m": Interval.MINUTE_3, "5m": Interval.MINUTE_5,
                "15m": Interval.MINUTE_15, "30m": Interval.MINUTE_30, "1h": Interval.HOUR_1,
                "2h": Interval.HOUR_2, "4h": Interval.HOUR_4, "6h": Interval.HOUR_6,
                "8h": Interval.HOUR_8, "12h": Interval.HOUR_12, "1d": Interval.DAY_1,
                "3d": Interval.DAY_3, "1w": Interval.WEEK_1, "1M": Interval.MONTH_1,
            }
            interval_enum = _interval_map.get(interval, Interval.HOUR_1)

            client = BinanceWebSocketClient(testnet=self.testnet)
            stream_names = []
            callbacks = {}
            for symbol in symbols:
                stream_name = kline_stream(symbol, interval_enum)
                stream_names.append(stream_name)
                callbacks[stream_name] = self._on_kline_message
                logger.info(f"订阅: {symbol} {interval} -> {stream_name}")
            client.start_combined(stream_names, callbacks)
            client.start()
            self._streams.append(client)
        except Exception as e:
            logger.error(f"启动数据流失败: {e}", exc_info=True)

    def process_signal(self, signal: Signal, strategy: BaseStrategy, current_time: int):
        """Override: use BinanceBroker for live trading"""
        portfolio = self.account.get_portfolio(strategy.name, strategy.symbol)
        if not portfolio:
            self.logger.warning(f"策略 {strategy.name} 品种 {strategy.symbol} 没有对应的投资组合")
            return
        self.binance_broker.process_signal(signal, strategy, portfolio, current_time)

    def _on_kline_message(self, event):
        """WebSocket K线消息回调 — 转换为 Bar 并调用 _on_bar"""
        from data.binance_feed import KlineEvent
        if isinstance(event, KlineEvent) and event.kline.is_closed:
            k = event.kline
            symbol = k.symbol.lower()
            bar = Bar(
                time=k.start_time,
                open=float(k.open_price),
                high=float(k.high_price),
                low=float(k.low_price),
                close=float(k.close_price),
                volume=float(k.volume),
                close_time=k.close_time,
                quote_volume=float(k.quote_volume),
                trade_count=k.trade_count,
            )
            bar.symbol = symbol
            logger.info(
                f"[{symbol.upper()}] K线: O={bar.open:>.2f} H={bar.high:>.2f} "
                f"L={bar.low:>.2f} C={bar.close:>.2f} V={bar.volume:.8f}"
            )
            self._on_bar(bar)

    def _on_bar(self, bar: Bar):
        """收到新K线回调"""
        if not self._running:
            return

        try:
            # 更新价格缓存
            self._prices[bar.symbol] = bar.close

            # 巡检挂单（每次新K线检查所有 pending 订单）
            self.binance_broker.check_pending_orders(
                strategies=self.strategies,
                current_time=bar.time,
                pending_timeout_ms=self.pending_timeout_ms,
            )

            # 更新持仓盈亏
            for strategy in self.strategies:
                if strategy.symbol == bar.symbol:
                    portfolio = self.account.get_portfolio(strategy.name, bar.symbol)
                    if portfolio:
                        for position in portfolio.positions:
                            position.update_unrealized_pnl(bar.close)
                            position.update_high_low(bar.close)

            # 检查风控（先防守 — 处理已有持仓的止损止盈）
            for strategy in self.strategies:
                portfolio = self.account.get_portfolio(strategy.name, bar.symbol)
                if portfolio:
                    to_close = self.binance_broker.check_risk(
                        portfolio, bar.close, bar.time
                    )
                    for position, reason in to_close:
                        closed = self.binance_broker.close_position(
                            position, portfolio, bar.close, reason, bar.time
                        )
                        if closed:
                            strategy.on_position_close(
                                bar.close, reason, position.realized_pnl
                            )

            # 运行策略（再进攻 — 基于最新状态决策）
            for strategy in self.strategies:
                if strategy.symbol == bar.symbol:
                    signal = strategy.on_bar(bar)
                    if signal and signal.action != "NONE":
                        logger.info(
                            f"[{bar.symbol.upper()}] 信号: {signal.action} "
                            f"价格={signal.price:.2f} 信心={signal.confidence:.2f} "
                            f"原因={signal.reason}"
                        )
                        self.process_signal(signal, strategy, bar.time)

            # ── 每根K线后保存状态到 Redis ──
            for strategy in self.strategies:
                if strategy.symbol == bar.symbol:
                    self._save_state(strategy, bar.time)

            # 记录权益曲线（每分钟记录一次）
            now = bar.time
            if now - self._last_record_time >= 60000:  # 至少间隔1分钟
                self._record_equity(bar)
                self._last_record_time = now

        except Exception as e:
            logger.error(f"处理K线回调错误: {e}", exc_info=True)

    def _record_equity(self, bar: Bar):
        """记录权益曲线"""
        for strategy in self.strategies:
            portfolio = self.account.get_portfolio(strategy.name, bar.symbol)
            if portfolio:
                entry = {
                    "time": bar.time,
                    "equity": portfolio.total_equity,
                    "strategy": strategy.name,
                    "symbol": bar.symbol,
                }
                self.equity_curve.append(entry)
                # 同时写入 Redis
                if self.state_store:
                    self.state_store.append_equity(strategy.name, bar.symbol, entry)

    def _main_loop(self):
        """主循环 — 驱动定时巡检，与 WebSocket 回调并行运行。"""
        try:
            while self._running:
                now_ms = int(time.time() * 1000)
                if now_ms - self._last_pending_check_time >= self._pending_check_interval_sec * 1000:
                    self._last_pending_check_time = now_ms
                    self._pending_check()
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def _pending_check(self):
        """定时巡检挂单（独立于 K 线周期，默认每60秒执行一次）。"""
        if not self.order_manager.pending_orders:
            return
        if not self._pending_check_lock.acquire(blocking=False):
            return  # 上一轮巡检尚未完成，跳过
        try:
            self.binance_broker.check_pending_orders(
                strategies=self.strategies,
                current_time=int(time.time() * 1000),
                pending_timeout_ms=self.pending_timeout_ms,
            )
        except Exception as e:
            logger.error(f"定时巡检挂单错误: {e}", exc_info=True)
        finally:
            self._pending_check_lock.release()

    def stop(self):
        """停止实盘引擎"""
        self._running = False
        self._is_running = False

        # 最终保存状态
        for strategy in self.strategies:
            try:
                self._save_state(strategy)
            except Exception:
                pass

        # 断开 Redis
        if self.state_store:
            self.state_store.disconnect()

        # 停止数据流
        for stream in self._streams:
            try:
                stream.stop()
            except Exception:
                pass
        self._streams.clear()

        # 策略结束
        for strategy in self.strategies:
            try:
                strategy.on_end()
            except Exception:
                pass

        logger.info("实盘引擎已停止")

    def get_status(self) -> dict:
        """获取引擎状态"""
        status = super().get_status()
        status.update({
            "testnet": self.testnet,
            "symbols": list(self._prices.keys()),
            "prices": self._prices,
            "equity_records": len(self.equity_curve),
            "redis_available": self.state_store is not None and self.state_store.available,
        })
        return status

    def sync_account_balance(self):
        """从 Binance 同步账户余额"""
        try:
            self.binance_broker.sync_balance()
            logger.info("账户余额已同步")
        except Exception as e:
            logger.error(f"同步账户余额失败: {e}")

    def sync_open_orders(self):
        """从 Binance 同步未成交订单"""
        try:
            self.binance_broker.sync_open_orders(self.strategies)
            logger.info("未成交订单已同步")
        except Exception as e:
            logger.error(f"同步未成交订单失败: {e}")
