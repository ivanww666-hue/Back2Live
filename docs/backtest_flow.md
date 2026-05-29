# 回测流程文档

## 1. 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                    BacktestEngine                       │
│  (core/backtest_engine.py)                              │
├─────────────────────────────────────────────────────────┤
│  TradingEngine (基类)                                   │
│  core/engine.py                                         │
├──────────┬──────────┬──────────┬──────────┬─────────────┤
│ Account  │ Broker   │ RiskMgr  │ OrderMgr │ Strategies  │
│ account/ │ core/    │ risk/    │ core/    │ strategy/   │
│ account  │ broker   │ risk_    │ order_   │ base_ /     │
│ .py      │ .py      │ manager  │ manager  │ ma_ / ...   │
│          │          │ .py      │ .py      │             │
├──────────┴──────────┴──────────┴──────────┴─────────────┤
│ DataFeed                                                │
│ data/datafeed.py                                        │
└─────────────────────────────────────────────────────────┘
```

## 2. 启动入口

回测模式从 `main.py` 的 `main()` 函数进入:

```python
# main.py
if args.mode == "backtest":
    merged = run_backtest(config)
```

### 2.1 配置文件加载

1. `main()` 读取 `config.json`
2. 过滤 `enabled=True` 的策略
3. 按策略名称过滤（可选 `--strategies` 参数）
4. 按启用策略数量均分初始资金

### 2.2 任务构建

`run_backtest()` 为每个启用的策略构建任务列表，每个任务包含:

```python
{
    "strategy_config": sc,      # 策略配置
    "symbol": symbol,           # 交易对
    "interval": interval,        # K线周期
    "start_date"/"end_date",    # 时间范围
    "db_path": "market_data.db",# 数据库路径
    "initial_capital": ...,     # 均分后的本金
    "maker_fee"/"taker_fee",    # 费率
    "output_path": "results",   # 输出目录
}
```

### 2.3 并行执行

使用 `ProcessPoolExecutor` 并行执行所有回测任务:

```python
with ProcessPoolExecutor(max_workers=max_workers) as executor:
    future_to_task = {
        executor.submit(_run_single_backtest_task, task): task
        for task in tasks
    }
```

每个子进程独立运行 `_run_single_backtest_task()`，返回结果后主进程合并汇总。

## 3. 引擎初始化 (`_run_single_backtest_task`)

### 3.1 创建引擎

```python
engine = BacktestEngine(
    initial_capital=args["initial_capital"],
    maker_fee=args["maker_fee"],
    taker_fee=args["taker_fee"],
    config=engine_config,
)
```

`BacktestEngine.__init__()` 链式调用 `TradingEngine.__init__()` 创建核心组件:

```
TradingEngine.__init__()
├── self.config = config or {}
├── self.account = Account(initial_capital)
├── self.risk_manager = RiskManager()
├── self.order_manager = OrderManager()
├── self.broker = Broker(account, risk_manager, order_manager, ...)
└── self.strategies = []
```

### 3.2 设置风控

```python
engine.setup_risk_manager(
    stop_loss_pct=stop_loss_pct,     # 止损%
    take_profit_pct=take_profit_pct, # 止盈%
    trailing_stop_pct=trailing_stop_pct, # 移动止损%
    position_size_pct=position_size_pct, # 建仓资金比例%
)
```

### 3.3 添加策略

```python
strategy = _create_strategy(strategy_type, strategy_config)  # 动态加载策略类
engine.add_strategy(strategy)
```

`_create_strategy()` 从 `config.json` 的 `strategy_mapping` 查找策略类型:

| type key | 策略类 | 文件 |
|----------|--------|------|
| `ma_strategy` | `MAStrategy` | `strategy/ma_strategy.py` |
| `dual_ma_strategy` | `DualMAStrategy` | `strategy/dual_ma_strategy.py` |
| `triple_ma_strategy` | `TripleMAStrategy` | `strategy/triple_ma_strategy.py` |
| `slope_ma_strategy` | `SlopeMAStrategy` | `strategy/slope_ma_strategy.py` |
| `grid_strategy` | `GridStrategy` | `strategy/grid_strategy.py` |

### 3.4 创建投资组合

```python
engine.create_portfolio(
    strategy_name=strategy_name,
    symbol=symbol,
    capital=args["initial_capital"],
    max_positions=max_positions,
)
```

创建一个 `Portfolio` 实例，分配资金，关联到 `(strategy_name, symbol)` 键。

## 4. 核心回测循环 (`BacktestEngine.run()`)

### 4.1 数据加载

```python
bars = self.data_feed.load_from_db(
    db_path, symbol, interval,
    start_time=start_date, end_time=end_date,
)
```

从 SQLite 数据库加载历史K线数据。

### 4.2 投资组合自动分配

如果没有预先创建 portfolios，自动为每个策略-品种对均分资金:

```python
keys = [(strategy.name, symbol) for strategy in self.strategies]
self.account.allocate_equal(keys, max_positions, risk_managers)
```

### 4.3 逐K线回测（五大步骤）

```
for bar in bars:
    │
    ├── Step 1: 更新未实现盈亏
    │   └── 用 bar.close 更新每个持仓的 unrealized_pnl 和 highest/lowest
    │
    ├── Step 2: 风控检查（先防守）
    │   ├── 检查每个持仓的止损/止盈/移动止损
    │   └── 触发 → Broker.close_position() 平仓
    │         └── 回调 strategy.on_position_close()
    │
    ├── Step 3: 策略信号（再进攻）
    │   ├── 调用 strategy.on_bar(bar) 生成信号
    │   └── 有信号 → process_signal() → Broker 执行
    │
    ├── Step 4: 记录权益曲线
    │   └── 记录每个(策略,品种)的 total_equity 到 equity_curve
    │
    └── (下一个 bar)
```

### 4.4 信号处理 (`BacktestEngine.process_signal`)

```python
def process_signal(self, signal, strategy, current_time):
    portfolio = self.account.get_portfolio(strategy.name, strategy.symbol)
    self.broker.process_signal(signal, strategy, portfolio, current_time)
```

#### BUY 信号执行流程

```
BUY Signal
│
├── _prepare_buy_plan()
│   ├── 检查 portfolio.can_open（未达最大持仓数）
│   ├── 检查 signal.price > 0
│   ├── 计算仓位大小: RiskManager.calculate_position_size(capital, price)
│   ├── 对齐到 quantity_step
│   ├── 检查 min_quantity
│   ├── 计算总成本 = price * quantity + fee
│   └── 检查资金是否充足
│
└── _apply_buy_fill()
    ├── 创建订单 (OrderManager.create_order)
    ├── 创建持仓 Position(entry_price, quantity, stop_loss, take_profit, ...)
    ├── 扣除资金: portfolio.capital -= total_cost
    ├── 添加到 portfolio.positions
    └── 记录交易: trades.append({"type": "OPEN", ...})
```

#### SELL 信号执行流程

```
SELL Signal
│
├── 检查 signal.price > 0
├── 获取持仓: portfolio.get_position_by_side("LONG")
├── close_position()
│   └── _apply_close_fill()
│       ├── 计算已实现盈亏
│       ├── 回笼资金: portfolio.capital += exit_price * quantity - fee
│       ├── 从 portfolio.positions 移除
│       └── 记录交易: trades.append({"type": "CLOSE", "pnl": ..., ...})
└── 回调 strategy.on_position_close()
```

### 4.5 风控检查 (`Broker.check_risk`)

```python
def check_risk(self, portfolio, current_price, current_time):
    return portfolio.risk_manager.check_positions(portfolio, current_price, current_time)
```

`RiskManager._check_position_risk()` 对每个持仓依次检查:

1. **更新最高价**: `if current_price > position.highest_price: position.highest_price = current_price`
2. **止损检查**: `if current_price <= position.stop_loss → 触发止损`
3. **止盈检查**: `if current_price >= position.take_profit → 触发止盈`
4. **移动止损检查**: `if current_price <= highest_price * (1 - trailing_stop_pct/100) → 触发移动止损`

## 5. 结果计算 (`BacktestEngine._calculate_results`)

### 5.1 交易统计

从 `Account.get_all_closed_positions()` 获取所有已平仓持仓:

| 指标 | 计算方式 |
|------|----------|
| total_trades | 已平仓持仓数 (realized_pnl != 0) |
| winning_trades | realized_pnl > 0 的持仓数 |
| losing_trades | realized_pnl <= 0 的持仓数 |
| win_rate | winning / total * 100 |
| avg_win | 平均盈利金额 |
| avg_loss | 平均亏损金额 |
| profit_factor | 总盈利 / 总亏损 |

### 5.2 资金统计

| 指标 | 计算方式 |
|------|----------|
| total_return | total_equity - initial_capital |
| total_return_pct | (total_equity - initial_capital) / initial_capital * 100 |
| total_fee | 所有交易记录 fee 总和 |
| net_profit | = total_return |

### 5.3 最大回撤

从权益曲线计算:

```python
peak = initial_capital
for eq in total_equities:
    if eq > peak: peak = eq
    dd = (peak - eq) / peak * 100
    max_dd = max(max_dd, dd)
```

### 5.4 夏普比率

从权益曲线的收益率序列计算（年化）:

```python
returns = [(eq[i] - eq[i-1]) / eq[i-1] for i in range(1, len(equities))]
avg_return = mean(returns)
std_return = std(returns)
sharpe = (avg_return * 252) / (std_return * sqrt(252))
```

## 6. 结果输出

### 6.1 JSON 结果

```python
engine.save_results(output_path)
```

保存内容:
- `summary`: 回测指标汇总
- `config`: 回测配置
- `account`: 账户摘要
- `trades`: 所有交易记录
- `equity_curve`: 权益曲线
- `drawdown_curve`: 回撤曲线
- `start_time` / `end_time`: 时间范围

### 6.2 HTML 报告

```python
from utils.report_html import json_to_html
json_to_html(result_file, strategy_params=strategy_config)
```

## 7. 多品种回测 (`BacktestEngine.run_many`)

与单品种回测的差异:

1. 从数据库加载多个品种的K线数据
2. 所有K线按时间排序后组成统一事件流
3. 遍历事件流，每个品种独立执行风控和策略
4. 权益曲线额外记录 `account_equity`（总账户权益）和 `global_bar_index`

## 8. 回测 vs. 实盘 Broker 差异

| 特性 | 回测 Broker | 实盘 BinanceBroker |
|------|-------------|-------------------|
| 订单执行 | 立即按 bar.close 成交 | 发送到 Binance 交易所 |
| 挂单 | 无（市价单立即成交） | 支持限价单/挂单 + 定时巡检 |
| 部分成交 | 不支持 | 支持（巡检时处理） |
| 订单超时 | 无 | 可配置超时自动撤单 |
| 资金 | 本地模拟 | 从 Binance 同步余额 |

## 9. 核心组件关系图

```
BacktestEngine
│
├── Account (account.account)
│   └── Portfolio (多个, 按 strategy:symbol 键区分)
│       ├── capital (可用资金)
│       ├── positions (当前持仓)
│       ├── closed_positions (已平仓持仓)
│       └── risk_manager (独立的 RiskManager)
│
├── Broker (core.broker)
│   ├── process_signal() → 处理 BUY/SELL 信号
│   ├── check_risk() → 风控检查
│   ├── close_position() → 平仓
│   └── trades (交易记录列表)
│
├── RiskManager (risk.risk_manager)
│   ├── calculate_position_size() → 仓位计算
│   ├── calculate_stop_loss/take_profit() → 价格计算
│   └── check_positions() → 持仓检查
│
├── OrderManager (core.order_manager)
│   ├── create_order() → 创建订单
│   ├── execute_order() → 执行订单
│   └── orders (所有订单记录)
│
├── Strategy (多个, strategy/)
│   ├── on_start() → 策略启动
│   ├── on_bar() → K线回调 → 返回 Signal
│   ├── on_position_open() → 开仓回调
│   ├── on_position_close() → 平仓回调
│   └── on_end() → 策略结束
│
└── DataFeed (data.datafeed)
    └── bars (K线数据列表)
```

## 10. 回测完整时序图

```
main() ──→ run_backtest(config)
              │
              ├─ 加载配置文件
              ├─ 构建任务列表
              └─ ProcessPoolExecutor
                   │
                   └─ _run_single_backtest_task(task)
                        │
                        ├─ 1. 创建 BacktestEngine
                        ├─ 2. 设置风控参数
                        ├─ 3. 创建策略实例
                        ├─ 4. 创建投资组合
                        │
                        ├─ 5. engine.run(symbol, interval, ...)
                        │      │
                        │      ├─ 5.1 加载历史数据
                        │      ├─ 5.2 初始化投资组合
                        │      ├─ 5.3 策略 on_start()
                        │      │
                        │      └─ 5.4 逐K线循环
                        │             │
                        │             ├─ [Step 1] 更新未实现盈亏
                        │             ├─ [Step 2] 风控检查 + 平仓
                        │             ├─ [Step 3] 策略 on_bar → 信号 → Broker 执行
                        │             └─ [Step 4] 记录权益曲线
                        │
                        ├─ 6. 策略 on_end()
                        ├─ 7. _calculate_results()
                        │      ├─ 交易统计
                        │      ├─ 最大回撤
                        │      └─ 夏普比率
                        │
                        ├─ 8. save_results() → JSON
                        └─ 9. json_to_html() → HTML