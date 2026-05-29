# 功能概述

## 1. 系统概览

本系统是一个**虚拟货币现货交易回测/实盘系统**，支持多策略多品种同时运行，回测与实盘可自由切换。

### 1.1 核心设计理念

| 原则 | 说明 |
|------|------|
| **分层架构** | `core/` 为核心逻辑，历史兼容层在 `engine/` |
| **回测/实盘统一基类** | `TradingEngine` 定义统一接口，`BacktestEngine` 和 `LiveEngine` 分别实现 |
| **多策略支持** | 一个引擎可同时运行多个不同策略，各自独立管理投资组合 |
| **多品种支持** | 每个策略可绑定不同交易对（如 btcusdt、ethusdt） |
| **策略独立风控** | 每个策略-品种对拥有独立的 RiskManager 参数 |

### 1.2 运行模式

| 模式 | 启动命令 | 说明 |
|------|----------|------|
| 回测 | `python main.py --mode backtest` | 使用历史数据验证策略 |
| 实盘 | `python main.py --mode live` | 连接 Binance 交易所实盘/模拟盘 |
| 下载 | `python main.py --download` | 下载历史 K 线数据到本地 SQLite |

### 1.3 命令行参数

| 参数 | 可选值 | 默认值 | 说明 |
|------|--------|--------|------|
| `--mode` | `backtest`, `live` | `backtest` | 运行模式 |
| `--config` | 文件路径 | `config.json` | 配置文件路径 |
| `--download` | - | false | 下载模式 |
| `--testnet` | - | 使用配置值 | 测试网模式 |
| `--no-testnet` | - | 使用配置值 | 主网模式 |
| `--strategies` | 逗号分隔名称 | 全部 | 指定运行的策略 |
| `--report` | JSON 文件路径 | - | 将回测结果 JSON 转 HTML 报告 |

---

## 2. 回测功能

### 2.1 单品种回测

逐 K 线遍历历史数据，模拟交易执行:

```python
engine = BacktestEngine(initial_capital=10000.0)
engine.add_strategy(MAStrategy({"name": "MAStrategy"}))
result = engine.run(symbol="btcusdt", interval="1h", start_date="2025-01-01", end_date="2025-05-25")
```

### 2.2 多品种多策略回测

同时运行多个策略，每个策略可绑定不同品种:

```python
engine.add_strategy(ma_strategy)     # 品种: btcusdt
engine.add_strategy(grid_strategy)   # 品种: ethusdt
result = engine.run_many(symbols=["btcusdt", "ethusdt"], interval="1h")
```

所有 K 线按时间排序后组成统一事件流，跨品种时间对齐。

### 2.3 并行回测

使用 `ProcessPoolExecutor` 多进程并行执行多个回测任务:

```
config.json 中配置:
- max_workers: 并行进程数 (默认: CPU 核心数)
- 每个策略-品种对作为一个独立任务
```

### 2.4 回测 Broker 模拟

`Broker` 类提供本地模拟交易:

| 特性 | 行为 |
|------|------|
| 订单类型 | 仅支持市价单（立即按 bar.close 成交） |
| 手续费 | 支持配置 maker_fee / taker_fee |
| 数量对齐 | 支持 quantity_step 步长对齐 |
| 最小数量 | 支持 min_quantity 检查 |
| 资金校验 | 检查资金是否足够 |
| 最大持仓 | 检查 portfolio.can_open (max_positions) |
| 交易记录 | 所有 OPEN / CLOSE / REJECTED 记录到 trades 列表 |

### 2.5 回测结果指标

| 指标 | 说明 | 计算方式 |
|------|------|----------|
| total_trades | 总交易次数 | 已平仓持仓数 |
| winning_trades | 盈利交易数 | realized_pnl > 0 |
| losing_trades | 亏损交易数 | realized_pnl <= 0 |
| win_rate | 胜率 | winning / total * 100% |
| avg_win | 平均盈利 | 盈利总额 / 盈利次数 |
| avg_loss | 平均亏损 | 亏损总额 / 亏损次数 |
| profit_factor | 盈亏比 | 总盈利 / 总亏损 |
| total_return | 总盈亏 | total_equity - initial_capital |
| total_return_pct | 总收益率 | (total_equity / initial_capital - 1) * 100% |
| total_fee | 总手续费 | 所有交易 fee 之和 |
| net_profit | 净盈亏 | = total_return |
| max_drawdown | 最大回撤 | 从权益曲线计算 (peak - valley) / peak |
| sharpe_ratio | 夏普比率 | 年化收益 / 年化波动 |
| equity_curve | 权益曲线 | 每个(策略,品种,bar) 的 total_equity |
| drawdown_curve | 回撤曲线 | 每个时间点的回撤百分比 |

### 2.6 结果输出

| 输出格式 | 文件 | 说明 |
|----------|------|------|
| JSON | `results/backtest_*.json` | 完整回测数据（摘要/交易/权益曲线/回撤曲线） |
| HTML | 同目录 `.html` 文件 | 人类可读的报告，含图表 |

### 2.7 报告模式

```bash
python main.py --report results/backtest_MA_btcusdt_20250101.json
```

将已有 JSON 结果文件生成为 HTML 报告。

---

## 3. 实盘功能

### 3.1 实时数据流

通过 Binance WebSocket 接收实时 K 线数据:

- 支持所有 Binance K 线周期（1m ~ 1M）
- 自动关闭的 K 线触发交易逻辑
- 支持多品种同时订阅

### 3.2 交易所交互

通过 `BinanceBroker` 与 Binance 交互:

| 功能 | 方法 | 说明 |
|------|------|------|
| 市价单 | `place_order(type=MARKET)` | 立即以市场价成交 |
| 限价单 | `place_order(type=LIMIT, price=...)` | 挂单等待成交 |
| 撤单 | `cancel_order()` | 取消未成交订单 |
| 查单 | `get_order_status()` | 查询订单状态 |
| 余额同步 | `sync_balance()` | 从交易所获取 USDT 余额 |
| 持仓同步 | `sync_positions()` | 从交易所获取当前持仓 |
| 订单同步 | `sync_open_orders()` | 从交易所获取未成交订单 |
| 品种信息 | `get_symbol_info()` | 获取交易对精度/最小数量等信息 |

### 3.3 挂单管理

| 功能 | 说明 |
|------|------|
| 挂单记录 | BUY/SELL 限价单记录为 PENDING 订单 |
| 资金冻结 | BUY 挂单时从 portfolio 冻结资金 |
| K线巡检 | 每根新 K 线时查询交易所所有 PENDING 订单状态 |
| 定时巡检 | 后台线程每 60 秒巡检一次 |
| 成交处理 | FILLED → 创建持仓 / 平仓 |
| 拒绝处理 | REJECTED/EXPIRED/CANCELED → 恢复冻结资金 |
| 超时撤单 | 超过 `pending_timeout_ms` 自动撤单（默认 5 分钟） |
| 部分成交 | PARTIALLY_FILLED → 按增量创建/更新持仓 |
| 重复平仓防护 | 通过 `_has_pending_close()` 检测持仓是否已有 SELL 挂单 |

### 3.4 余额同步

启动时从 Binance 获取真实余额:

```
1. 调用 sync_balance() → 获取 USDT 余额
2. 取 配置金额 和 实际余额 的最小值使用
3. 按启用策略数量均分
```

### 3.5 状态持久化 (Redis)

| 功能 | 说明 |
|------|------|
| 保存时机 | 每根 K 线处理完成后 |
| 保存内容 | Portfolio 数据、挂单列表、权益曲线 |
| 恢复机制 | 引擎启动优先从 Redis 恢复 |
| 回退机制 | Redis 不可用时从 Binance API 同步 |
| TTL | 可配置过期时间（默认 7 天） |
| 清理 | 引擎停止时断开 Redis 连接 |

### 3.6 测试网支持

通过配置文件或命令行 `--testnet` 切换到 Binance 测试网:

```json
"binance": {
    "api_key": "你的测试网KEY",
    "api_secret": "你的测试网SECRET",
    "testnet": true
}
```

---

## 4. 风控系统

共享的 `RiskManager` 适用于回测和实盘。

### 4.1 风控参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `stop_loss_pct` | 2.0% | 触发止损: 价格 <= entry_price * (1 - sl%) |
| `take_profit_pct` | 6.0% | 触发止盈: 价格 >= entry_price * (1 + tp%) |
| `trailing_stop_pct` | 1.5% | 移动止损: 价格从最高点回落 ts% 时触发 |
| `position_size_pct` | 20.0% | 单次建仓使用资金的百分比 |

### 4.2 仓位计算

```python
quantity = (capital * position_size_pct / 100) / price
```

### 4.3 风控检查顺序（每根 K 线）

```
1. 更新持仓最高价 (current_price > highest_price)
2. 检查止损 (current_price <= stop_loss_price)
3. 检查止盈 (current_price >= take_profit_price)
4. 检查移动止损 (current_price <= highest_price * (1 - ts%))
```

### 4.4 策略独立风控

每个策略-品种对可以配置独立的风控参数:

```json
{
    "name": "策略A",
    "stop_loss_pct": 3.0,
    "take_profit_pct": 10.0,
    "trailing_stop_pct": 4.0,
    "position_size_pct": 15.0,
    "max_positions": 5
}
```

---

## 5. 策略库

### 5.1 双均线交叉策略 (DualMAStrategy)

**文件**: `strategy/dual_ma_strategy.py`

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ema_fast` | 12 | 快速 EMA 周期 |
| `ema_slow` | 26 | 慢速 EMA 周期 |

**规则**:
- **买入**: 快线从下方上穿慢线（金叉）
- **卖出**: 快线从上方下穿慢线（死叉）

### 5.2 三均线趋势策略 (TripleMAStrategy)

**文件**: `strategy/triple_ma_strategy.py`

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ema_fast` | 10 | 快速 EMA 周期 |
| `ema_medium` | 30 | 中速 EMA 周期 |
| `ema_slow` | 60 | 慢速 EMA 周期 |

**规则**:
- **买入**: fast > medium > slow（多头排列）
- **卖出**: 排列关系破坏或死叉

### 5.3 综合均线策略 (MAStrategy)

**文件**: `strategy/ma_strategy.py`

最复杂的趋势跟踪策略，结合多种指标:

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ema_fast` | 12 | 快速 EMA 周期 |
| `ema_medium` | 26 | 中速 EMA 周期 |
| `ema_slow` | 50 | 慢速 EMA 周期 |
| `ema_trend` | 200 | 趋势线周期 |
| `macd_fast` / `macd_slow` / `macd_signal` | 12/26/9 | MACD 参数 |
| `adx_period` / `adx_threshold` | 14/25 | ADX 参数 |
| `super_atr_period` / `super_multiplier` | 10/2.8 | SuperTrend 参数 |

**入场条件**（多头排列 + MACD + ADX 三重过滤）:

```
1. EMA12 > EMA26 > EMA50 (多头排列)
2. MACD > 0 (零轴上方)
3. ADX > 25 (趋势强度足够)
4. confidence >= 0.5 (综合评分)
```

**出场条件**:
```
1. SuperTrend 从上升翻转为下降
2. 多头排列破坏或 MACD 转为负值
3. (Broker 层风控: 止损/止盈/移动止损)
```

**综合评分体系**:
| 加分项 | 加分 |
|--------|------|
| 长期多头 (EMA50 > EMA200) | +0.2 |
| 短期强势 (price > EMA12) | +0.15 |
| MACD 动能增强 | +0.15 |
| SuperTrend 买入信号 | +0.2 |
| 强趋势 (ADX > 40) | +0.1 |

### 5.4 网格交易策略 (GridStrategy)

**文件**: `strategy/grid_strategy.py`

在设定的价格区间内低买高卖，赚取网格利润:

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `grid_low` | 10000 | 网格下限价格 |
| `grid_high` | 60000 | 网格上限价格 |
| `grid_count` | 20 | 网格数量（等分区间） |
| `position_per_grid_pct` | 5.0% | 每格投入资金比例 |

**工作原理**:

```
将 [grid_low, grid_high] 等分为 grid_count 个区间
          ↓
价格下跌穿越网格线 → 买入 (每个格子只买一次)
          ↓
价格上涨穿越网格线 → 卖出 (已买入的格子)
          ↓
跟踪每个网格的持仓状态 (_grid_filled)
```

### 5.5 均线斜率策略 (SlopeMAStrategy)

**文件**: `strategy/slope_ma_strategy.py`

使用 SMA 斜率判断趋势:

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ma_fast` | 5 | 快速 SMA |
| `ma_medium` | 10 | 中速 SMA |
| `ma_slow` | 20 | 慢速 SMA |
| `trail_drop` | 2% | MA5 回落平仓阈值 |
| `take_profit` | 8% | 止盈阈值 |
| `stop_loss` | 2% | 止损阈值 |

**规则**:
- **买入**: MA5、MA10、MA20 斜率同时向上（当前值 > 前一根值）
- **卖出**:
  1. MA5 从持仓期间最高价回落 trail_drop%
  2. 盈利达到 take_profit 止盈
  3. 亏损达到 stop_loss 止损

---

## 6. 账户与资金管理

### 6.1 Account (账户)

管理所有投资组合，按 `(策略名称, 品种)` 键区分:

```python
account = Account(initial_capital=10000.0)
account.allocate_equal([
    ("MAStrategy", "btcusdt"),
    ("GridStrategy", "ethusdt"),
])
```

**关键属性**:
- `total_equity`: 所有 portfolio 总权益（现金 + 持仓市值）
- `total_realized_pnl`: 所有已平仓持仓的已实现盈亏
- `total_unrealized_pnl`: 所有持仓的浮动盈亏
- `total_trades`: 所有 portfolio 的交易次数

### 6.2 Portfolio (投资组合)

每个 `(策略, 品种)` 对拥有独立的投资组合:

```
Portfolio:
├── initial_capital: 分配资金 (均分)
├── capital: 当前可用资金
├── positions: 当前持仓列表
├── closed_positions: 已平仓持仓列表
├── max_positions: 最大持仓数
└── risk_manager: 风控实例
```

**资金流转**:
```
     初始资金 → Portfolio.capital
         ↓
  开仓: capital -= 买入金额 + 手续费
         ↓
  持仓期间: 浮动盈亏 = (当前价 - 入场价) × 数量
         ↓
  平仓: capital += 卖出金额 - 手续费
         ↓
  最终: total_equity = capital + 持仓市值
```

### 6.3 Position (持仓)

```
Position:
├── symbol: 交易对
├── side: "LONG" (现货只做多)
├── strategy_name: 所属策略
├── entry_price / entry_time: 入场价格和时间
├── quantity: 持仓数量
├── entry_order_id: 建仓订单 ID
├── stop_loss / take_profit: 止损止盈价格
├── highest_price: 持仓期间最高价 (用于移动止损)
├── realized_pnl / unrealized_pnl: 已实现/未实现盈亏
└── is_open: 是否仍持有
```

---

## 7. 技术指标 (TA)

**文件**: `indicator/ta.py`

基于 numpy 实现的技术指标:

| 指标 | 方法 | 说明 |
|------|------|------|
| SMA | `TA.sma(data, period)` | 简单移动平均线 |
| EMA | `TA.ema(data, period)` | 指数移动平均线 |
| MACD | `TA.macd(data, fast, slow, signal)` | 指数平滑异同移动平均线 |
| RSI | `TA.rsi(data, period)` | 相对强弱指标 |
| ATR | `TA.atr(high, low, close, period)` | 平均真实波幅 |
| 布林带 | `TA.bollinger(data, period, std_dev)` | 布林带 (上/中/下轨) |
| ADX | `TA.adx(high, low, close, period)` | 平均趋向指数 |
| SuperTrend | `TA.super_trend(high, low, close, period, multiplier)` | 超级趋势指标 |

---

## 8. 数据模块

### 8.1 历史数据下载

```bash
python main.py --download
```

从 Binance 下载历史 K 线数据到本地 SQLite 数据库。

| 配置 | 说明 |
|------|------|
| `db_path` | SQLite 数据库文件路径 |
| `start_date` / `end_date` | 下载时间范围 |
| 品种 | 从配置中所有策略的 symbol 字段自动收集 |
| 去重 | 同一品种不会重复下载 |

### 8.2 本地数据源

`DataFeed` 从 SQLite 数据库加载数据，支持:
- 按品种、周期、时间范围查询
- 返回 `Bar` 对象列表
- Bar 包含: time, open, high, low, close, volume, close_time, quote_volume, trade_count

### 8.3 实时数据源

`BinanceWebSocketClient` 通过 WebSocket 接收实时 K 线:
- 订阅多个品种的 kline 流
- 组合流模式 (`start_combined`)
- K 线关闭时回调 `_on_kline_message`

---

## 9. 策略开发框架

### 9.1 策略基类 BaseStrategy

所有策略继承 `BaseStrategy` 并实现抽象方法:

```python
class MyStrategy(BaseStrategy):
    def on_start(self):
        """策略启动时调用"""
        pass

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        """每根K线调用一次，返回交易信号"""
        pass

    def on_end(self):
        """策略结束时调用"""
        pass

    def on_position_open(self, signal: Signal, price: float):
        """开仓回调"""
        pass

    def on_position_close(self, price: float, reason: str, pnl: float):
        """平仓回调"""
        pass
```

### 9.2 Signal 信号

```python
@dataclass
class Signal:
    action: str      # BUY, SELL, NONE
    price: float     # 交易价格
    timestamp: int   # 时间戳
    reason: str      # 原因描述
    confidence: float  # 置信度 0.0~1.0
    indicators: Dict[str, float]  # 指标值(用于日志/分析)
    order_type: str  # MARKET 或 LIMIT
```

### 9.3 策略类型注册

在 `config.json` 的 `strategy_mapping` 中注册:

```json
{
    "strategy_mapping": {
        "ma_strategy": "MAStrategy",
        "grid_strategy": "GridStrategy",
        "dual_ma_strategy": "DualMAStrategy",
        "triple_ma_strategy": "TripleMAStrategy",
        "slope_ma_strategy": "SlopeMAStrategy"
    }
}
```

策略类自动发现: 系统会在 `strategy/` 目录下自动扫描所有继承 `BaseStrategy` 的类。

---

## 10. 配置文件

### 10.1 全局配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `mode` | string | `backtest` | 运行模式 |
| `interval` | string | `1h` | K线周期 |
| `initial_capital` | float | 10000.0 | 初始资金 |
| `db_path` | string | `market_data.db` | 历史数据数据库路径 |
| `output_path` | string | `results` | 结果输出目录 |
| `start_date` | string | - | 回测开始日期 |
| `end_date` | string | - | 回测结束日期 |
| `max_workers` | int | auto | 并行回测最大进程数 |
| `pending_timeout_ms` | int | 300000 | 挂单超时毫秒 |
| `pending_check_interval_sec` | int | 60 | 挂单巡检间隔秒数 |

### 10.2 Binance 配置

| 参数 | 说明 |
|------|------|
| `binance.api_key` | API Key（支持 `${ENV_VAR}` 环境变量注入） |
| `binance.api_secret` | API Secret |
| `binance.testnet` | 是否使用测试网 |
| `binance.quote_asset` | 计价资产 (默认 USDT) |

### 10.3 Redis 配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `redis.host` | localhost | Redis 主机 |
| `redis.port` | 6379 | Redis 端口 |
| `redis.db` | 0 | Redis 数据库编号 |
| `redis.password` | null | Redis 密码 |
| `redis.ttl_seconds` | 604800 | Key 过期时间 (7天) |
| `redis.equity_max_len` | 2000 | 权益曲线最大记录数 |

### 10.4 策略配置

每个策略条目:

| 参数 | 说明 |
|------|------|
| `name` | 策略名称（显示用） |
| `type` | 策略类型（对应 strategy_mapping） |
| `enabled` | 是否启用 |
| `symbol` | 交易对 |
| `stop_loss_pct` | 止损百分比（覆盖全局） |
| `take_profit_pct` | 止盈百分比 |
| `trailing_stop_pct` | 移动止损百分比 |
| `position_size_pct` | 单次建仓资金比例 |
| `max_positions` | 最大持仓数 |
| (策略特有参数) | 见各策略文档 |