# -*- coding: utf-8 -*-
"""
回测引擎模块
===========
核心回测引擎，协调数据、策略、账户、风控等模块完成回测流程。

关键修复:
1. 交易记录统一由 Broker 管理，引擎不再重复记录
2. 权益曲线按 (策略, 交易对, bar_index) 维度记录
3. 开平仓资金正确流转（含手续费）
4. 支持多策略多品种同时回测
5. 策略信号逻辑修复（入场/出场信号互斥）
6. 每个 (策略, 品种) 拥有独立投资组合
"""

import json
import logging
import time
from typing import List, Optional, Dict, Tuple, Type, Any
from datetime import datetime

from data.datafeed import DataFeed, Bar
from account.account import Account
from account.portfolio import Portfolio
from account.position import Position
from risk.risk_manager import RiskManager
from strategy.base_strategy import BaseStrategy, Signal
from core.order_manager import OrderManager
from core.broker import Broker
from core.engine import TradingEngine

logger = logging.getLogger(__name__)


class BacktestResult:
    """回测结果"""

    def __init__(self):
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_return = 0.0
        self.total_return_pct = 0.0
        self.max_drawdown = 0.0
        self.sharpe_ratio = 0.0
        self.win_rate = 0.0
        self.avg_win = 0.0
        self.avg_loss = 0.0
        self.profit_factor = 0.0
        self.total_fee = 0.0
        self.net_profit = 0.0
        self.trades: List[dict] = []
        # 权益曲线: 每个元素是 {"time": timestamp, "equity": float, "strategy": str, "symbol": str}
        self.equity_curve: List[dict] = []
        self.drawdown_curve: List[float] = []
        self.start_time: int = 0
        self.end_time: int = 0
        self.duration_seconds: float = 0.0
        self.config: dict = {}

    def to_dict(self) -> dict:
        return {
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "total_return": self.total_return,
            "total_return_pct": self.total_return_pct,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "win_rate": self.win_rate,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "profit_factor": self.profit_factor,
            "total_fee": self.total_fee,
            "net_profit": self.net_profit,
        }

    def summary(self) -> str:
        """生成文本摘要"""
        lines = []
        lines.append("=" * 60)
        lines.append("  回测结果摘要")
        lines.append("=" * 60)
        lines.append(f"  总交易次数: {self.total_trades}")
        lines.append(f"  盈利交易: {self.winning_trades}")
        lines.append(f"  亏损交易: {self.losing_trades}")
        lines.append(f"  胜率: {self.win_rate:.2f}%")
        lines.append(f"  总收益率: {self.total_return_pct:+.2f}%")
        lines.append(f"  总盈亏: {self.total_return:+.2f}")
        lines.append(f"  总手续费: {self.total_fee:.2f}")
        lines.append(f"  净盈亏: {self.net_profit:+.2f}")
        lines.append(f"  最大回撤: {self.max_drawdown:.2f}%")
        lines.append(f"  夏普比率: {self.sharpe_ratio:.2f}")
        lines.append(f"  盈亏比: {self.profit_factor:.2f}")
        lines.append(f"  平均盈利: {self.avg_win:.2f}")
        lines.append(f"  平均亏损: {self.avg_loss:.2f}")
        lines.append("=" * 60)
        return "\n".join(lines)


class BacktestEngine(TradingEngine):
    """
    回测引擎

    负责:
    1. 加载历史数据
    2. 驱动策略运行
    3. 执行交易
    4. 计算回测指标
    5. 记录详细的权益曲线
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

        # 数据源
        self.data_feed = DataFeed()

        # 回测状态
        self._current_bar: Optional[Bar] = None
        self._bar_index = 0
        self._total_bars = 0
        self._current_symbol = ""

        # 结果
        self.result = BacktestResult()
        self._start_time: float = 0.0

    def run(self, symbol: str, interval: str = "1h",
            start_time: Optional[int] = None,
            end_time: Optional[int] = None,
            start_date: Optional[str] = None,
            end_date: Optional[str] = None,
            db_path: str = "market_data.db") -> BacktestResult:
        """
        运行回测

        Args:
            symbol: 交易对
            interval: K线周期
            start_time: 开始时间戳 (毫秒)
            end_time: 结束时间戳 (毫秒)
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            db_path: 数据库路径

        Returns:
            回测结果
        """
        self._is_running = True
        self._bar_index = 0
        self._current_symbol = symbol
        self._start_time = time.time()

        # 加载数据
        loaded = False
        if self.data_feed.loaded and self.data_feed.symbol.lower() == symbol.lower():
            bars = self.data_feed.bars
            loaded = len(bars) > 0
        elif start_time is not None and end_time is not None:
            bars = self.data_feed.load(symbol, start_time, end_time)
            loaded = len(bars) > 0
        else:
            loaded = self.data_feed.load_from_db(
                db_path, symbol, interval,
                start_time=start_date, end_time=end_date,
            )
            bars = self.data_feed.bars

        self._total_bars = len(bars)

        if self._total_bars == 0:
            self.logger.warning("没有数据可回测")
            self._is_running = False
            return self.result

        self.logger.info(f"开始回测: {symbol} {interval} ({self._total_bars} 根K线)")

        # 记录配置
        self.result.config = {
            "symbol": symbol,
            "interval": interval,
            "initial_capital": self.initial_capital,
            "strategies": [s.name for s in self.strategies],
            "data_count": self._total_bars,
        }

        # 如果没有策略，自动创建默认策略
        if not self.strategies:
            from strategy.ma_strategy import MAStrategy
            self.add_strategy(MAStrategy({"name": "MAStrategy"}))

        # 为每个策略设置品种信息并启动（投资组合由调用方提前创建）
        if not self.account.portfolios:
            keys = [(strategy.name, symbol) for strategy in self.strategies]
            max_positions = {
                self.account._make_key(strategy.name, symbol): strategy.config.get("max_positions", 3)
                for strategy in self.strategies
            }
            risk_managers = {
                self.account._make_key(strategy.name, symbol): self.create_risk_manager_from_config(strategy.config)
                for strategy in self.strategies
            }
            self.account.allocate_equal(keys, max_positions, risk_managers)

        for strategy in self.strategies:
            strategy.symbol = symbol
            strategy.interval = interval
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

        # 记录开始时间
        if bars:
            self.result.start_time = bars[0].time
            self.result.end_time = bars[-1].time

        # 逐K线回测
        for bar in bars:
            self._current_bar = bar
            self._bar_index += 1

            # === 第一步：更新所有持仓的未实现盈亏（用当前 bar 价格） ===
            for strategy in self.strategies:
                portfolio = self.account.get_portfolio(strategy.name, symbol)
                if portfolio:
                    for position in portfolio.positions:
                        position.update_unrealized_pnl(bar.close)
                        position.update_high_low(bar.close)

            # === 第二步：检查风控（先防守 — 处理已有持仓的止损止盈） ===
            for strategy in self.strategies:
                portfolio = self.account.get_portfolio(strategy.name, symbol)
                if portfolio:
                    try:
                        to_close = self.broker.check_risk(
                            portfolio, bar.close, bar.time
                        )
                        for position, reason in to_close:
                            self.broker.close_position(
                                position, portfolio, bar.close, reason, bar.time
                            )
                            strategy.on_position_close(
                                bar.close, reason, position.realized_pnl
                            )
                    except Exception as e:
                        self.logger.error(f"风控检查错误: {e}")

            # === 第三步：运行策略生成信号（再进攻 — 基于最新状态决策） ===
            for strategy in self.strategies:
                try:
                    signal = strategy.on_bar(bar)
                    if signal and signal.action != "NONE":
                        self.process_signal(signal, strategy, bar.time)
                except Exception as e:
                    self.logger.error(f"策略 {strategy.name} 错误: {e}", exc_info=True)

            # === 第四步：记录权益曲线（每个策略每个品种每个bar） ===
            # 资金总额 = 现金 + 持仓数量 × 当前价格
            for strategy in self.strategies:
                portfolio = self.account.get_portfolio(strategy.name, symbol)
                if portfolio:
                    self.result.equity_curve.append({
                        "time": bar.time,
                        "equity": portfolio.total_equity,
                        "strategy": strategy.name,
                        "symbol": symbol,
                        "bar_index": self._bar_index,
                    })

        # 策略结束
        for strategy in self.strategies:
            strategy.on_end()

        # 计算回测指标
        self._calculate_results()

        self._is_running = False
        self.result.duration_seconds = time.time() - self._start_time
        self.logger.info(f"回测完成, 耗时: {self.result.duration_seconds:.2f}秒")

        return self.result

    def run_many(self, symbols: List[str], interval: str = "1h",
                 start_date: Optional[str] = None,
                 end_date: Optional[str] = None,
                 db_path: str = "market_data.db") -> BacktestResult:
        """Run multiple strategy-symbol portfolios in one account event stream."""
        self._is_running = True
        self._bar_index = 0
        self._start_time = time.time()

        if not self.strategies:
            from strategy.ma_strategy import MAStrategy
            self.add_strategy(MAStrategy({"name": "MAStrategy", "symbol": symbols[0]}))

        strategy_symbols = []
        max_positions = {}
        risk_managers = {}
        for strategy in self.strategies:
            symbol = strategy.config.get("symbol") or strategy.symbol or (symbols[0] if symbols else "")
            strategy.symbol = symbol.lower()
            strategy.interval = interval
            strategy_symbols.append((strategy.name, strategy.symbol))
            key = self.account._make_key(strategy.name, strategy.symbol)
            max_positions[key] = strategy.config.get("max_positions", 3)
            risk_managers[key] = self.create_risk_manager_from_config(strategy.config)

        if not self.account.portfolios:
            self.account.allocate_equal(strategy_symbols, max_positions, risk_managers)
        else:
            for strategy_name, symbol in strategy_symbols:
                portfolio = self.account.get_portfolio(strategy_name, symbol)
                if portfolio and portfolio.risk_manager is None:
                    key = self.account._make_key(strategy_name, symbol)
                    portfolio.set_risk_manager(risk_managers[key])

        bars_by_symbol: Dict[str, List[Bar]] = {}
        for symbol in sorted(set(s.lower() for s in symbols)):
            feed = DataFeed()
            if feed.load_from_db(db_path, symbol, interval, start_time=start_date, end_time=end_date):
                bars_by_symbol[symbol] = feed.bars

        if not bars_by_symbol:
            self.logger.warning("no data loaded for multi-symbol backtest")
            self._is_running = False
            return self.result

        events = []
        for symbol, bars in bars_by_symbol.items():
            for idx, bar in enumerate(bars, start=1):
                events.append((bar.time, symbol, idx, bar))
        events.sort(key=lambda item: (item[0], item[1]))

        self.result.start_time = events[0][0]
        self.result.end_time = events[-1][0]
        self.result.config = {
            "symbols": sorted(bars_by_symbol.keys()),
            "interval": interval,
            "initial_capital": self.initial_capital,
            "strategies": [s.name for s in self.strategies],
            "data_count": len(events),
            "mode": "multi",
        }

        for strategy in self.strategies:
            strategy.on_start()

        strategies_by_symbol: Dict[str, List[BaseStrategy]] = {}
        for strategy in self.strategies:
            strategies_by_symbol.setdefault(strategy.symbol.lower(), []).append(strategy)

        for _, symbol, symbol_bar_index, bar in events:
            self._current_bar = bar
            self._current_symbol = symbol
            self._bar_index += 1

            for strategy in strategies_by_symbol.get(symbol, []):
                portfolio = self.account.get_portfolio(strategy.name, symbol)
                if not portfolio:
                    continue

                for position in portfolio.positions:
                    position.update_unrealized_pnl(bar.close)
                    position.update_high_low(bar.close)

                # 先风控（防守）
                try:
                    for position, reason in self.broker.check_risk(portfolio, bar.close, bar.time):
                        if self.broker.close_position(position, portfolio, bar.close, reason, bar.time):
                            strategy.on_position_close(bar.close, reason, position.realized_pnl)
                except Exception as exc:
                    self.logger.error("risk check failed: %s", exc, exc_info=True)

                # 再策略信号（进攻）
                try:
                    signal = strategy.on_bar(bar)
                    if signal and signal.action != "NONE":
                        self.process_signal(signal, strategy, bar.time)
                except Exception as exc:
                    self.logger.error("strategy %s failed: %s", strategy.name, exc, exc_info=True)

                self.result.equity_curve.append({
                    "time": bar.time,
                    "equity": portfolio.total_equity,
                    "account_equity": self.account.total_equity,
                    "cash": portfolio.capital,
                    "strategy": strategy.name,
                    "symbol": symbol,
                    "bar_index": symbol_bar_index,
                    "global_bar_index": self._bar_index,
                })

        for strategy in self.strategies:
            strategy.on_end()

        self._calculate_results()
        self._is_running = False
        self.result.duration_seconds = time.time() - self._start_time
        return self.result

    def _calculate_results(self):
        """计算回测结果"""
        result = self.result

        # 从 Broker 获取交易记录（单次操作记录）
        all_trades = self.broker.trades
        result.trades = all_trades

        # 手续费统计（从所有交易记录中汇总）
        result.total_fee = sum(t.get("fee", 0) for t in all_trades)

        # 收益率（已含手续费，因为 Broker 在交易中已从 capital 扣除 fee）
        result.total_return = self.account.total_equity - self.initial_capital
        if self.initial_capital != 0:
            result.total_return_pct = (
                (self.account.total_equity - self.initial_capital) / self.initial_capital * 100
            )
        else:
            result.total_return_pct = 0.0
        result.net_profit = result.total_return

        # 交易统计（从各策略各品种的已平仓持仓中计算）
        all_closed_positions = self.account.get_all_closed_positions()

        closed_positions = [p for p in all_closed_positions if p.realized_pnl != 0]
        result.total_trades = len(closed_positions)
        result.winning_trades = len([p for p in closed_positions if p.realized_pnl > 0])
        result.losing_trades = len([p for p in closed_positions if p.realized_pnl <= 0])

        if result.total_trades > 0:
            result.win_rate = result.winning_trades / result.total_trades * 100
            result.avg_win = sum(p.realized_pnl for p in closed_positions if p.realized_pnl > 0) / max(result.winning_trades, 1)
            result.avg_loss = abs(sum(p.realized_pnl for p in closed_positions if p.realized_pnl <= 0)) / max(result.losing_trades, 1)
            total_win = sum(p.realized_pnl for p in closed_positions if p.realized_pnl > 0)
            total_loss = abs(sum(p.realized_pnl for p in closed_positions if p.realized_pnl <= 0))
            result.profit_factor = total_win / total_loss if total_loss != 0 else float("inf")

        # 从权益曲线计算最大回撤
        if self.result.equity_curve:
            # 使用总权益曲线（按时间聚合）
            equity_by_time: Dict[int, float] = {}
            for entry in self.result.equity_curve:
                t = entry["time"]
                if "account_equity" in entry:
                    equity_by_time[t] = entry["account_equity"]
                else:
                    equity_by_time[t] = equity_by_time.get(t, 0) + entry["equity"]

            sorted_times = sorted(equity_by_time.keys())
            total_equities = [equity_by_time[t] for t in sorted_times]

            peak = self.initial_capital
            max_dd = 0
            for eq in total_equities:
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak * 100 if peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd
                self.result.drawdown_curve.append(dd)
            result.max_drawdown = max_dd

            # Sharpe Ratio
            if len(total_equities) > 1:
                returns = [
                    (total_equities[i] - total_equities[i-1]) / total_equities[i-1]
                    for i in range(1, len(total_equities))
                    if total_equities[i-1] != 0
                ]
                avg_return = sum(returns) / len(returns) if returns else 0
                std_return = (
                    (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5
                    if returns else 1
                )
                result.sharpe_ratio = (
                    (avg_return * 252) / (std_return * (252 ** 0.5))
                    if std_return > 0 else 0
                )

    def save_results(self, filepath: str):
        """保存回测结果到 JSON 文件"""
        import os
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump({
                "summary": self.result.to_dict(),
                "config": self.result.config,
                "account": self.account.get_summary(),
                "trades": self.result.trades,
                "equity_curve": self.result.equity_curve,
                "drawdown_curve": self.result.drawdown_curve,
                "start_time": self.result.start_time,
                "end_time": self.result.end_time,
                "duration_seconds": self.result.duration_seconds,
            }, f, ensure_ascii=False, indent=2)
        self.logger.info(f"回测结果已保存: {filepath}")
        return filepath

    def stop(self):
        """停止回测"""
        self._is_running = False
        self.logger.info("回测已停止")
