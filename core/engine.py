# -*- coding: utf-8 -*-
"""
引擎基类
======
回测引擎和实盘引擎的公共基类，定义统一的接口和共享逻辑。
"""

import logging
from typing import List, Optional, Dict, Any
from abc import ABC, abstractmethod

from account.account import Account
from account.portfolio import Portfolio
from risk.risk_manager import RiskManager
from core.order_manager import OrderManager
from core.broker import Broker
from strategy.base_strategy import BaseStrategy, Signal
from data.datafeed import Bar


class TradingEngine(ABC):
    """
    交易引擎基类

    提供回测和实盘引擎的公共功能:
    - 账户管理
    - 策略管理
    - 投资组合管理
    - 风控管理
    - 订单管理
    - 经纪商管理
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
        self.config = config or {}
        self.initial_capital = initial_capital
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee

        # 核心组件
        self.account = Account(initial_capital=initial_capital)
        self.risk_manager = RiskManager()
        self.order_manager = OrderManager()
        # 从 config 中读取交易精度参数
        self.quantity_step = self.config.get("quantity_step", 0.0)
        self.min_quantity = self.config.get("min_quantity", 0.0)
        self.broker = Broker(
            account=self.account,
            risk_manager=self.risk_manager,
            order_manager=self.order_manager,
            maker_fee=maker_fee,
            taker_fee=taker_fee,
            quantity_step=self.quantity_step,
            min_quantity=self.min_quantity,
        )

        # 策略列表
        self.strategies: List[BaseStrategy] = []

        # 运行状态
        self._is_running = False

        # 日志
        self.logger = logging.getLogger(self.__class__.__name__)

    # ---- 策略管理 ----

    def add_strategy(self, strategy: BaseStrategy) -> BaseStrategy:
        """
        添加策略

        Args:
            strategy: 策略实例

        Returns:
            策略实例
        """
        self.strategies.append(strategy)
        return strategy

    def get_strategy(self, name: str) -> Optional[BaseStrategy]:
        """按名称获取策略"""
        for s in self.strategies:
            if s.name == name:
                return s
        return None

    # ---- 投资组合管理 ----

    def create_portfolio(self, strategy_name: str, symbol: str,
                         capital: Optional[float] = None,
                         max_positions: int = 3,
                         risk_manager: Optional[RiskManager] = None) -> Portfolio:
        """
        为策略和品种创建投资组合

        Args:
            strategy_name: 策略名称
            symbol: 交易对
            capital: 分配资金，None 表示自动均分
            max_positions: 最大持仓数

        Returns:
            投资组合
        """
        return self.account.create_portfolio(
            strategy_name=strategy_name,
            symbol=symbol,
            capital=capital,
            max_positions=max_positions,
            risk_manager=risk_manager,
        )

    def get_portfolio(self, strategy_name: str, symbol: str) -> Optional[Portfolio]:
        """获取策略和品种的投资组合"""
        return self.account.get_portfolio(strategy_name, symbol)

    # ---- 风控管理 ----

    def setup_risk_manager(self, stop_loss_pct: float = 2.0,
                           take_profit_pct: float = 6.0,
                           trailing_stop_pct: float = 1.5,
                           position_size_pct: float = 20.0) -> RiskManager:
        """
        设置风险管理器

        Args:
            stop_loss_pct: 止损百分比
            take_profit_pct: 止盈百分比
            trailing_stop_pct: 移动止损百分比
            position_size_pct: 单次建仓资金比例 (%)

        Returns:
            风险管理器
        """
        self.risk_manager = RiskManager(
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            trailing_stop_pct=trailing_stop_pct,
            position_size_pct=position_size_pct,
        )
        # 更新 broker 中的 risk_manager 引用
        self.broker.risk_manager = self.risk_manager
        return self.risk_manager

    def create_risk_manager_from_config(self, config: Optional[dict] = None) -> RiskManager:
        """Create a risk manager using strategy config with engine defaults as fallback."""
        config = config or {}
        position_size_pct = config.get(
            "position_size_pct",
            config.get(
                "position_per_grid_pct",
                self.config.get("position_size_pct", self.risk_manager.position_size_pct),
            ),
        )
        return RiskManager(
            stop_loss_pct=config.get("stop_loss_pct", self.config.get("stop_loss_pct", self.risk_manager.stop_loss_pct)),
            take_profit_pct=config.get("take_profit_pct", self.config.get("take_profit_pct", self.risk_manager.take_profit_pct)),
            trailing_stop_pct=config.get("trailing_stop_pct", self.config.get("trailing_stop_pct", self.risk_manager.trailing_stop_pct)),
            position_size_pct=position_size_pct,
        )

    # ---- 信号处理 ----

    def process_signal(self, signal: Signal, strategy: BaseStrategy,
                       current_time: int):
        """
        处理策略信号

        Args:
            signal: 交易信号
            strategy: 策略实例
            current_time: 当前时间戳
        """
        portfolio = self.account.get_portfolio(strategy.name, strategy.symbol)
        if not portfolio:
            self.logger.warning(f"策略 {strategy.name} 品种 {strategy.symbol} 没有对应的投资组合")
            return

        self.broker.process_signal(signal, strategy, portfolio, current_time)

    # ---- 风控检查 ----

    def check_risk_for_strategy(self, strategy: BaseStrategy,
                                current_price: float,
                                current_time: int) -> List[tuple]:
        """
        检查策略的风控条件

        Args:
            strategy: 策略
            current_price: 当前价格
            current_time: 当前时间

        Returns:
            [(position, reason), ...] 需要平仓的持仓
        """
        portfolio = self.account.get_portfolio(strategy.name, strategy.symbol)
        if not portfolio:
            return []
        return self.broker.check_risk(portfolio, current_price, current_time)

    # ---- 状态查询 ----

    def get_status(self) -> dict:
        """获取引擎状态"""
        return {
            "is_running": self._is_running,
            "strategies": [s.name for s in self.strategies],
            "account": self.account.get_summary(),
            "total_trades": self.account.total_trades,
        }

    # ---- 重置 ----

    def reset(self):
        """重置引擎状态"""
        self.account.reset()
        self.order_manager.clear()
        self.broker.reset()
        for strategy in self.strategies:
            strategy.reset()
        self._is_running = False

    # ---- 抽象方法 ----

    @abstractmethod
    def run(self, *args, **kwargs):
        """运行引擎"""
        pass

    @abstractmethod
    def stop(self):
        """停止引擎"""
        pass
