# Back2Live - 虚拟货币现货交易回测/实盘系统

支持多品种多策略同时回测或实盘运行，回测与实盘可自由切换。

## 项目结构

```
backtest/
├── core/                      # 核心模块（新架构）
│   ├── __init__.py
│   ├── engine.py              # 引擎基类（回测/实盘共用逻辑）
│   ├── backtest_engine.py     # 回测引擎
│   ├── live_engine.py         # 实盘引擎
│   ├── broker.py              # 模拟经纪商（回测用）
│   └── order_manager.py       # 订单管理器
├── engine/                    # 向后兼容层（旧引用自动重定向到 core/）
│   ├── __init__.py
│   ├── backtest_engine.py     # 重定向到 core.backtest_engine
│   ├── live_engine.py         # 重定向到 core.live_engine
│   ├── broker.py              # 重定向到 core.broker
│   ├── order_manager.py       # 重定向到 core.order_manager
│   └── binance_broker.py      # 币安实盘经纪商
├── account/                   # 账户模块
│   ├── __init__.py
│   ├── account.py             # 账户
│   ├── portfolio.py           # 投资组合
│   └── position.py            # 持仓
├── strategy/                  # 策略模块
│   ├── __init__.py
│   ├── base_strategy.py       # 策略基类
│   └── ma_strategy.py         # 均线策略
├── data/                      # 数据模块
│   ├── __init__.py
│   ├── datafeed.py            # 数据馈送
│   └── binance_feed.py        # 币安数据
├── risk/                      # 风控模块
│   ├── __init__.py
│   └── risk_manager.py        # 风险管理
├── indicator/                 # 指标模块
│   ├── __init__.py
│   └── ta.py                  # 技术指标
├── analyzer/                  # 分析模块
│   ├── __init__.py
│   ├── performance.py         # 绩效分析
│   └── report.py              # 报告生成
├── results/                   # 结果输出目录
├── main.py                    # 主入口
└── requirements.txt           # 依赖
```

## 核心架构

### 设计原则

1. **分层架构**: core/ 为核心逻辑，engine/ 为向后兼容层
2. **回测/实盘统一**: 通过 TradingEngine 基类统一接口
3. **多策略支持**: 一个引擎可同时运行多个策略
4. **多品种支持**: 每个策略可绑定不同交易对

### 核心类

| 类 | 文件 | 说明 |
|---|---|---|
| `TradingEngine` | `core/engine.py` | 引擎基类，提供通用逻辑 |
| `BacktestEngine` | `core/backtest_engine.py` | 回测引擎，使用历史数据 |
| `LiveEngine` | `core/live_engine.py` | 实盘引擎，连接交易所 |
| `Broker` | `core/broker.py` | 模拟经纪商（回测用） |
| `BinanceBroker` | `engine/binance_broker.py` | 币安实盘经纪商 |
| `OrderManager` | `core/order_manager.py` | 订单管理器 |
| `Portfolio` | `account/portfolio.py` | 投资组合（资金管理） |
| `Position` | `account/position.py` | 持仓管理 |
| `RiskManager` | `risk/risk_manager.py` | 风险管理 |
| `BaseStrategy` | `strategy/base_strategy.py` | 策略基类 |
| `DataFeed` | `data/datafeed.py` | 数据馈送 |

### 资金流转

```
初始资金 → Portfolio.capital
    ↓
开仓: capital -= 买入金额 + 手续费
    ↓
持仓期间: 浮动盈亏 = (当前价 - 入场价) × 数量
    ↓
平仓: capital += 卖出金额 - 手续费
    ↓
最终: total_equity = capital + 浮动盈亏
```

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 回测模式

```python
from core.backtest_engine import BacktestEngine
from strategy.ma_strategy import MAStrategy

engine = BacktestEngine(initial_capital=10000.0)
engine.add_strategy(MAStrategy({"name": "MAStrategy"}))
result = engine.run(
    symbol="btcusdt",
    interval="1h",
    start_date="2025-01-01",
    end_date="2025-05-25",
)
print(result.summary())
```

### 下载历史数据 命令行回测

```bash
python main.py --download
python main.py --mode backtest --strategies "ETH网格"
python main.py --mode backtest --symbol btcusdt --interval 1h --capital 10000
```

### 实盘模式

```python
from core.live_engine import LiveEngine
from strategy.ma_strategy import MAStrategy

engine = LiveEngine(
    api_key="your_key",
    api_secret="your_secret",
    testnet=True,
)
engine.setup_account(initial_capital=10000.0)
engine.add_strategy(MAStrategy({"name": "MAStrategy"}))
engine.run(symbols=["btcusdt"], interval="1m")
```

### 命令行实盘

```bash
python main.py --mode live --symbols btcusdt ethusdt --interval 1m --testnet
```

## 配置说明

### 回测配置

| 参数 | 默认值 | 说明 |
|---|---|---|
| `initial_capital` | 10000.0 | 初始资金 |
| `maker_fee` | 0.001 | Maker 费率 |
| `taker_fee` | 0.001 | Taker 费率 |
| `stop_loss_pct` | 2.0 | 止损百分比 |
| `take_profit_pct` | 6.0 | 止盈百分比 |
| `trailing_stop_pct` | 1.5 | 移动止损百分比 |

### 策略配置

| 参数 | 默认值 | 说明 |
|---|---|---|
| `name` | MAStrategy | 策略名称 |
| `ema_fast` | 12 | 快线周期 |
| `ema_medium` | 26 | 中线周期 |
| `ema_slow` | 50 | 慢线周期 |
| `ema_trend` | 200 | 趋势线周期 |

## 编写自定义策略

继承 `BaseStrategy` 并实现 `on_bar` 方法：

```python
from strategy.base_strategy import BaseStrategy, Signal

class MyStrategy(BaseStrategy):
    def on_start(self):
        # 初始化
        pass

    def on_bar(self, bar):
        # 处理每个K线
        if bar.close > self.sma:
            return Signal(action="BUY", price=bar.close, reason="突破")
        return Signal(action="NONE", price=bar.close)

    def on_end(self):
        # 清理
        pass
```

## 常见问题

### Q: 回测结果与实盘不一致？
A: 回测使用收盘价模拟，实盘使用实时价格。建议在回测中设置合理的滑点和手续费。

### Q: 如何添加多个策略？
A: 多次调用 `engine.add_strategy()` 即可，每个策略会独立管理自己的投资组合。

### Q: 如何切换回测/实盘？
A: 使用 `BacktestEngine` 进行回测，`LiveEngine` 进行实盘，两者接口一致。
