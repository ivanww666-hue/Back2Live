# 实盘流程文档

## 1. 整体架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                         LiveEngine                                  │
│  (core/live_engine.py)                                               │
├──────────────────────────────────────────────────────────────────────┤
│  TradingEngine (基类)                                                │
│  core/engine.py                                                      │
├──────────┬──────────┬──────────┬──────────┬───────────┬─────────────┤
│ Account  │BinanceBro│ RiskMgr  │ OrderMgr │ Strategies│ StateStore  │
│ account/ │ core/    │ risk/    │ core/    │ strategy/ │ utils/      │
│ account  │ binance_ │ risk_    │ order_   │ base_ /   │ state_      │
│ .py      │ broker   │ manager  │ manager  │ ma_ / ... │ store.py    │
│          │ .py      │ .py      │ .py      │           │ (Redis)     │
├──────────┴──────────┴──────────┴──────────┴───────────┴─────────────┤
│ BinanceWebSocketClient  │  Binance REST API                         │
│ data/binance_feed.py    │  binance Python SDK                       │
└──────────────────────────────────────────────────────────────────────┘
```

## 2. 启动入口

实盘模式从 `main.py` 的 `main()` 函数进入:

```python
if args.mode == "live":
    run_live(config)
```

### 2.1 配置文件结构

```json
{
    "initial_capital": 10000.0,
    "maker_fee": 0.001,
    "taker_fee": 0.001,
    "interval": "1m",
    "binance": {
        "api_key": "xxx",
        "api_secret": "xxx",
        "testnet": true,
        "quote_asset": "USDT"
    },
    "redis": {
        "host": "localhost",
        "port": 6379,
        "db": 0,
        "ttl_seconds": 604800
    },
    "strategies": [
        {
            "name": "MAStrategy",
            "type": "ma_strategy",
            "symbol": "btcusdt",
            "enabled": true,
            "stop_loss_pct": 2.0,
            "take_profit_pct": 6.0,
            "position_size_pct": 20.0,
            "max_positions": 3
        }
    ]
}
```

### 2.2 命令行参数

| 参数 | 说明 |
|------|------|
| `--mode live` | 实盘模式 |
| `--config config.json` | 配置文件路径 |
| `--testnet` | 使用测试网 |
| `--no-testnet` | 使用主网 |
| `--strategies "MA,EMA"` | 指定运行策略名称（逗号分隔） |

### 2.3 策略过滤

```
配置中所有 enabled=true 的策略
  │
  ├─ 无 --strategies 参数 → 全部启用
  │
  └─ 有 --strategies 参数 → 按名称过滤
       └─ 只保留名称在指定列表中的策略
```

## 3. 引擎初始化 (`run_live`)

### 3.1 创建引擎

```python
engine = LiveEngine(
    initial_capital=initial_capital,
    maker_fee=maker_fee,
    taker_fee=taker_fee,
    config=config,
)
```

`LiveEngine.__init__()` 链式调用执行:

```
LiveEngine.__init__()
│
├── TradingEngine.__init__()
│   ├── self.config = config
│   ├── self.account = Account(initial_capital)
│   ├── self.risk_manager = RiskManager()
│   ├── self.order_manager = OrderManager()
│   └── self.broker = Broker(account, risk_manager, order_manager)
│
├── self.api_key / self.api_secret (从 config["binance"] 读取)
├── self.testnet = config["binance"]["testnet"]
├── self.quote_asset = config["binance"]["quote_asset"]
├── self._binance_broker = None (延迟初始化)
│
├── StateStore (Redis 状态持久化)
│   ├── host/port/db/password (从 config["redis"] 读取)
│   └── ttl_seconds / equity_max_len
│
├── self.equity_curve = [] (权益曲线)
├── self._prices = {} (价格缓存)
├── self._pending_check_lock (线程锁)
└── self._pending_check_interval_sec = 60 (挂单巡检间隔)
```

### 3.2 同步交易所余额

```python
engine.binance_broker.sync_balance()
actual_balance = engine.account.initial_capital
```

`sync_balance()` 调用 Binance API `get_account()`，获取 USDT 余额:

```python
def sync_balance(self):
    info = self.client.get_account()
    for balance in info["balances"]:
        if balance["asset"] == self.quote_asset:
            self.account.initial_capital = float(balance["free"])
```

取实际余额与配置金额的最小值作为实盘资金:

```python
initial_capital = min(config_capital, actual_balance)
```

### 3.3 添加策略和投资组合

为每个启用的策略:

```python
for sc in enabled_strategies:
    strategy = _create_strategy(strategy_type, sc)
    engine.add_strategy(strategy)
    risk_mgr = engine.create_risk_manager_from_config(sc)
    engine.create_portfolio(
        strategy_name=strategy_name,
        symbol=symbol,
        capital=capital_per_strategy,
        max_positions=max_positions,
        risk_manager=risk_mgr,
    )
```

## 4. 引擎启动 (`LiveEngine.run`)

```
LiveEngine.run(symbols, interval)
│
├── Step 1: 分配投资组合
│   ├── 为每个(策略,品种)创建 Portfolio
│   └── 均分资金
│
├── Step 2: 尝试从 Redis 恢复状态
│   ├── 对每个策略调用 _restore_state()
│   └── 成功 → 跳过下一阶段
│
├── Step 3: Redis 不可用 → Binance API 同步
│   ├── sync_account_balance() → 同步余额
│   ├── sync_open_orders() → 同步未成交订单
│   └── binance_broker.sync_positions() → 同步持仓
│
├── Step 4: 确保所有 portfolio 有 risk_manager
│
├── Step 5: 策略 on_start()
│
├── Step 6: 保存引擎元信息到 Redis
│
├── Step 7: 预热策略（加载历史K线）
│   └── _warmup_strategy()
│       ├── 从 Binance 获取最近 N 根K线
│       └── 逐根喂给 strategy.on_bar()（忽略信号）
│
├── Step 8: 启动数据流（WebSocket）
│   └── _start_streams()
│       ├── 为每个品种订阅 kline 流
│       └── 回调 = self._on_kline_message
│
└── Step 9: 启动主循环（后台线程）
    └── _main_loop() → 每1秒检查挂单巡检
```

### 4.1 状态恢复流程

```
_restore_state(strategy)
│
├── 从 Redis 加载 portfolio 数据
├── 恢复 RiskManager 参数
├── 恢复或创建 Portfolio
│   ├── capital（可用资金）
│   ├── positions（当前持仓）
│   └── closed_positions（已平仓持仓）
├── 恢复挂单到 OrderManager
├── 恢复权益曲线
└── 返回 True/False
```

如果 `_restore_state` 对所有策略都返回 `False`，则回退到 Binance API 同步:

```
sync_account_balance() → 从交易所获取 USDT 余额
sync_open_orders() → 从交易所获取未成交订单，恢复 OrderManager
sync_positions() → 从交易所获取当前持仓，恢复 Portfolio
```

### 4.2 策略预热

`_warmup_strategy()` 从 Binance 获取历史K线，让策略指标计算达到最小需要:

```python
klines = client.get_klines(
    symbol=symbol_upper,
    interval=interval_binance,
    limit=warmup_count + 5,  # 多取5根确保足够
)
for k in klines:
    bar = Bar(...)
    strategy.on_bar(bar)  # 仅喂数据，不触发交易
# 缓存最后价格
self._prices[strategy.symbol] = last_bar.close
```

### 4.3 数据流启动

```python
_start_streams(symbols, interval)
│
├── 创建 BinanceWebSocketClient
├── 为每个品种生成 stream_name
│   └── e.g. "btcusdt@kline_1m"
├── 注册回调函数 self._on_kline_message
├── 调用 start_combined() 组合订阅
└── 调用 start() 启动 WebSocket
```

## 5. WebSocket K线回调 (`_on_kline_message`)

```python
_on_kline_message(event)
│
├── 判断 event.kline.is_closed (K线是否关闭)
├── 提取 kline 数据 → Bar
│   ├── time / open / high / low / close / volume
│   └── close_time / quote_volume / trade_count
│
└── 调用 self._on_bar(bar)
```

## 6. 核心 K 线处理循环 (`_on_bar`)

```
_on_bar(bar)
│
├── Step 1: 更新价格缓存
│   └── self._prices[bar.symbol] = bar.close
│
├── Step 2: 巡检挂单
│   └── binance_broker.check_pending_orders(
│           strategies, current_time, pending_timeout_ms)
│
├── Step 3: 更新持仓盈亏（匹配品种）
│   └── position.update_unrealized_pnl(bar.close)
│
├── Step 4: 风控检查（先防守）
│   └── 对每个匹配品种的策略:
│       ├── check_risk(portfolio, bar.close, bar.time)
│       └── 触发 → binance_broker.close_position()
│             └── 回调 strategy.on_position_close()
│
├── Step 5: 策略信号（再进攻）
│   └── 对每个匹配品种的策略:
│       ├── signal = strategy.on_bar(bar)
│       └── 有信号 → process_signal(signal, strategy, bar.time)
│
├── Step 6: 保存状态到 Redis
│   └── _save_state(strategy, bar.time)
│
├── Step 7: 记录权益曲线（每分钟一次）
│   ├── _record_equity(bar)
│   └── 同时写入 Redis
│
└── (等待下一个 K 线)
```

## 7. 实盘信号处理 (`LiveEngine.process_signal`)

与回测不同，实盘信号通过 `BinanceBroker` 发送到交易所:

```python
def process_signal(self, signal, strategy, current_time):
    portfolio = self.account.get_portfolio(strategy.name, strategy.symbol)
    self.binance_broker.process_signal(signal, strategy, portfolio, current_time)
```

### 7.1 BUY 信号执行流程

```
BinanceBroker._buy(signal, strategy, portfolio, current_time)
│
├── 1. _prepare_buy_plan() → 本地校验
│   ├── 检查 max_positions / price / 资金
│   ├── 计算 quantity（含交易所 step_size 对齐）
│   └── 检查 min_qty / min_notional
│
├── 2. place_order() → 发送到 Binance
│   ├── 市价单: 立即成交
│   ├── 限价单: 挂单等待成交
│   └── 失败 → _reject_live_signal() + 记录拒绝原因
│
├── 3. 解读成交结果
│   ├── _extract_fill() → 从 Binance 响应提取均价/数量
│   │
│   ├── 立即成交 (FILLED):
│   │   └── _apply_buy_fill() → 更新本地状态
│   │       ├── 创建持仓 Position
│   │       ├── 扣除资金
│   │       └── 标记订单为 FILLED
│   │
│   ├── 挂单未成交 (NEW / PARTIALLY_FILLED):
│   │   └── _record_pending_order()
│   │       ├── 冻结资金 (portfolio.capital -= total_cost)
│   │       ├── 创建 Order (status=PENDING)
│   │       └── 记录 PENDING trade
│   │
│   └── 完全拒绝 (ERROR):
│       └── _reject_live_signal() → 记录 REJECTED trade
│
└── 4. 立即成交的市价单标记状态
    └── order_manager.update_order_fill(FILLED)
```

### 7.2 SELL 信号执行流程

```
BinanceBroker._sell(signal, strategy, portfolio, current_time)
│
├── 1. 检查 signal.price > 0
├── 2. 获取持仓: portfolio.get_position_by_side("LONG")
│
├── 3. close_position(position, portfolio, exit_price, reason, current_time)
│   ├── _can_close_position() → 本地校验
│   ├── _has_pending_close() → 防止重复平仓
│   │   └── 检查该持仓是否已有 pending SELL 订单
│   ├── place_order() → 发送到 Binance
│   ├── 解读成交结果 (同 BUY)
│   │
│   ├── 立即成交 (FILLED):
│   │   └── _apply_close_fill() → 更新本地状态
│   │       ├── 计算已实现盈亏
│   │       ├── 回笼资金
│   │       ├── 移除持仓
│   │       └── 记录 CLOSE trade
│   │
│   └── 挂单未成交 (NEW):
│       └── _record_pending_close_order()
│           ├── 创建 Order (status=PENDING)
│           └── 记录 PENDING_CLOSE trade
│
└── 4. 回调 strategy.on_position_close()
```

## 8. 挂单巡检 (`BinanceBroker.check_pending_orders`)

独立于 K 线周期的定时巡检，由两种方式触发:

### 8.1 触发方式

| 触发方式 | 频率 | 来源 |
|----------|------|------|
| K 线回调 | 每根新 K 线 | `_on_bar()` 中调用 |
| 定时巡检 | 每 60 秒 | `_main_loop()` 后台线程 |

### 8.2 巡检逻辑

```
check_pending_orders(strategies, current_time, pending_timeout_ms)
│
└── 遍历所有 pending 订单:
    │
    ├── 1. 查交易所最新状态
    │   └── get_order_status(symbol, order_id) → Binance API
    │
    ├── 2. 已成交 (FILLED)
    │   └── _handle_pending_filled()
    │       ├── BUY: 创建持仓 / 追加持仓
    │       │   ├── 有部分成交历史 → 处理增量
    │       │   └── 首次成交 → 解冻资金 → _apply_buy_fill()
    │       │
    │       └── SELL: 平仓
    │           ├── 按 entry_order_id 精确匹配持仓
    │           ├── 有部分成交历史 → 处理增量
    │           └── 首次成交 → _apply_close_fill()
    │
    ├── 3. 拒绝/过期/取消 (REJECTED/EXPIRED/CANCELED)
    │   └── _handle_pending_failed()
    │       ├── 取消本地订单
    │       ├── BUY: 恢复冻结资金
    │       └── 记录 PENDING_FAILED / PENDING_CLOSE_FAILED trade
    │
    ├── 4. 超时检查 (elapsed > pending_timeout_ms)
    │   ├── cancel_order() → 发送撤单到 Binance
    │   └── _handle_pending_failed() → 恢复资金
    │
    └── 5. 部分成交 (PARTIALLY_FILLED)
        └── _handle_partial_fill()
            ├── BUY: 按比例解冻资金 → 创建/追加持仓
            │   ├── 首次: _apply_buy_fill()
            │   └── 已有: 更新均价 + 追加数量
            └── SELL: 部分平仓 → _apply_close_fill() 减少持仓数量
```

## 9. 风控检查

实盘风控与回测共用相同的 `RiskManager.check_positions()` 逻辑:

```
check_positions(portfolio, current_price, current_time)
│
└── 对 portfolio 中每个持仓:
    ├── 止损: current_price <= position.stop_loss
    ├── 止盈: current_price >= position.take_profit
    └── 移动止损: current_price <= highest_price * (1 - trailing_stop_pct/100)
         └── 更新 highest_price (if current_price > highest_price)
```

触发风控后调用 `binance_broker.close_position()` 发送平仓到交易所。

## 10. 状态持久化 (Redis)

### 10.1 每次 K 线保存的内容

保存时机: 每根新 K 线处理完成后 (`_save_state`)

Redis Key 结构:
- `{strategy_name}:{symbol}:portfolio` → 投资组合数据
- `{strategy_name}:{symbol}:pending_orders` → 挂单列表
- `{strategy_name}:{symbol}:equity` → 权益曲线
- `meta` → 引擎元信息

Portfolio 保存内容:
```python
{
    "initial_capital": portfolio.initial_capital,
    "max_positions": portfolio.max_positions,
    "capital": portfolio.capital,           # 可用资金
    "positions": [p.to_dict() for p in positions],   # 当前持仓
    "closed_positions": [p.to_dict() for p in closed_positions],  # 已平仓持仓
    "risk": {                               # 风控参数
        "stop_loss_pct": ...,
        "take_profit_pct": ...,
        "trailing_stop_pct": ...,
        "position_size_pct": ...,
    },
    "last_save_time": bar_time,
}
```

### 10.2 引擎停止时

```
LiveEngine.stop()
│
├── 1. 对所有策略最终保存状态 (_save_state)
├── 2. 断开 Redis (state_store.disconnect())
├── 3. 停止所有数据流
├── 4. 所有策略 on_end()
└── 5. 标记 _running = False
```

## 11. 权益曲线记录

每分钟记录一次（防止数据量过大）:

```python
_record_equity(bar)
│
└── 对每个策略:
    └── 记录到 self.equity_curve:
        {
            "time": bar.time,
            "equity": portfolio.total_equity,
            "strategy": strategy.name,
            "symbol": bar.symbol,
        }
    └── 同时写入 Redis:
        state_store.append_equity(strategy.name, bar.symbol, entry)
```

引擎停止时保存到文件:
```python
with open("results/live_equity_curve.json", "w") as f:
    json.dump(engine.equity_curve, f, ensure_ascii=False, indent=2)
```

## 12. 实盘 vs. 回测 关键差异总结

| 特性 | 回测 (BacktestEngine) | 实盘 (LiveEngine) |
|------|-----------------------|-------------------|
| 数据源 | 本地 SQLite 数据库 | Binance WebSocket 实时流 |
| 数据加载 | 一次性加载所有历史K线 | 逐根接收新K线 |
| 策略预热 | 不需要（数据全量） | 需要 `_warmup_strategy()` |
| 订单执行 | 立即按 bar.close 成交 | 发送到 Binance 交易所 |
| Broker | `Broker` (本地模拟) | `BinanceBroker` (交易所适配) |
| 挂单 | 不支持 | 支持限价单/挂单 |
| 部分成交 | 不支持 | 支持（巡检处理） |
| 订单超时 | 不适用 | 可配置超时自动撤单 |
| 状态持久化 | 不需要 | Redis 状态恢复 |
| 重启恢复 | 从头开始 | 从 Redis 或 Binance API 恢复 |
| 余额 | 固定配置值 | 从交易所实时同步 |
| 并发 | 多进程并行 | 单进程 + 后台线程 |
| 权益曲线 | 每根K线记录 | 每分钟记录一次 |
| 风控触发 | 按K线时间戳 | 按每根新K线 |
| 结果输出 | JSON + HTML 报告 | JSON 权益曲线文件 |

## 13. 完整时序图

```
main() ──→ run_live(config)
              │
              ├─ 1. 创建 LiveEngine
              ├─ 2. 同步交易所余额
              ├─ 3. 过滤策略 + 创建策略实例
              ├─ 4. 创建投资组合 (均分资金)
              │
              └─ 5. engine.run(symbols, interval)
                     │
                     ├─ 6. 分配投资组合
                     ├─ 7. 尝试 Redis 状态恢复
                     │     ├─ 成功 → 跳过同步
                     │     └─ 失败 → 从 Binance 同步
                     │
                     ├─ 8. 策略 on_start()
                     ├─ 9. 预热策略 (Binance API)
                     │
                     ├─ 10. 启动 WebSocket 数据流
                     │      └─ 订阅 kline 流
                     │
                     ├─ 11. 启动主循环线程 (_main_loop)
                     │      └─ 每 60 秒巡检挂单
                     │
                     └─ 12. [持续] 每根K线回调
                            │
                            └─ _on_kline_message
                                 └─ _on_bar
                                      ├─ 更新价格缓存
                                      ├─ 巡检挂单
                                      ├─ 更新持仓盈亏
                                      ├─ 风控检查 + 平仓
                                      ├─ 策略 on_bar → 信号
                                      ├─ BinanceBroker 执行
                                      ├─ 保存状态到 Redis
                                      └─ 记录权益曲线
                                      │
                             (等待下一根K线...)