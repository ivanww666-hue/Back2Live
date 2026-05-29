# Back2Live — 虚拟货币现货交易回测/实盘系统

支持多品种多策略同时回测或实盘运行，回测与实盘可自由切换。  
针对 Binance 交易所现货市场设计，内置网格、趋势跟踪、均线斜率等多种策略。

---

## 项目结构

```
Back2Live/
├── core/                      # 核心引擎
│   ├── engine.py              # TradingEngine 基类
│   ├── backtest_engine.py     # 回测引擎
│   ├── live_engine.py         # 实盘引擎（WebSocket + Redis 持久化）
│   ├── broker.py              # 模拟经纪商（回测用）
│   ├── binance_broker.py      # 币安实盘经纪商（订单执行/挂单管理）
│   └── order_manager.py       # 订单管理器
├── account/                   # 账户模块
│   ├── account.py             # 账户（多投资组合管理）
│   ├── portfolio.py           # 投资组合（资金/持仓）
│   └── position.py            # 持仓
├── strategy/                  # 策略模块
│   ├── base_strategy.py       # 策略基类
│   ├── ma_strategy.py         # 综合均线策略（EMA+MACD+ADX+SuperTrend）
│   ├── dual_ma_strategy.py    # 双均线交叉策略
│   ├── triple_ma_strategy.py  # 三均线趋势策略
│   ├── grid_strategy.py       # 网格交易策略
│   └── slope_ma_strategy.py   # 均线斜率策略
├── data/                      # 数据模块
│   ├── datafeed.py            # 数据馈送（从SQLite/CSV/JSON加载）
│   └── binance_feed.py        # 币安数据（历史下载 + WebSocket + OrderBook）
├── risk/                      # 风控模块
│   └── risk_manager.py        # 风险管理（止损/止盈/移动止损）
├── indicator/                 # 指标模块
│   └── ta.py                  # 技术指标（SMA/EMA/MACD/RSI/ATR/ADX/SuperTrend）
├── analyzer/                  # 分析模块
│   ├── performance.py         # 绩效分析
│   └── report.py              # 报告生成
├── utils/                     # 工具模块
│   ├── json_util.py           # JSON 配置读取（支持 // 注释 + 环境变量）
│   ├── logger.py              # 日志配置（文件轮转 + 信号专用日志）
│   ├── report_html.py         # 回测报告 JSON → 独立 HTML 页面
│   └── state_store.py         # Redis 状态持久化（实盘重启恢复）
├── configs/                   # 预置配置文件
│   ├── config_grid.json       # 网格策略专用
│   ├── config_trend.json      # 趋势跟踪策略专用
│   ├── config_hybrid.json     # 混合策略配置
│   └── config_slope.json      # 均线斜率策略专用
├── docs/                      # 文档
│   ├── features.md            # 完整功能文档
│   ├── backtest_flow.md       # 回测流程详解
│   ├── live_flow.md           # 实盘流程详解
│   └── cli.md                 # 命令行完整参考
├── config_app.py              # 策略配置 GUI 工具（tkinter）
├── main.py                    # 主入口（CLI）
├── config.json                # 默认配置文件
├── requirements.txt           # Python 依赖
├── cli.md                     # 命令行速查
├── tmux.md                    # tmux 操作速查
└── README.md                  # 本文件
```

---

## 核心架构

### 设计原则

1. **分层架构**: `core/` 为核心逻辑，回测/实盘共用基类
2. **回测/实盘统一**: 通过 `TradingEngine` 基类统一接口
3. **多策略支持**: 一个引擎可同时运行多个不同策略，各自独立管理投资组合
4. **多品种支持**: 每个策略可绑定不同交易对（如 btcusdt、ethusdt）
5. **策略独立风控**: 每个策略-品种对拥有独立的 RiskManager 参数

### 核心类

| 类 | 文件 | 说明 |
|---|---|---|
| `TradingEngine` | `core/engine.py` | 引擎基类，提供通用逻辑 |
| `BacktestEngine` | `core/backtest_engine.py` | 回测引擎，使用历史数据 |
| `LiveEngine` | `core/live_engine.py` | 实盘引擎，连接 Binance WebSocket |
| `Broker` | `core/broker.py` | 模拟经纪商（回测用） |
| `BinanceBroker` | `core/binance_broker.py` | 币安实盘经纪商（支持挂单/部分成交） |
| `OrderManager` | `core/order_manager.py` | 订单管理器 |
| `Account` | `account/account.py` | 账户（多投资组合） |
| `Portfolio` | `account/portfolio.py` | 投资组合（资金管理） |
| `Position` | `account/position.py` | 持仓管理 |
| `RiskManager` | `risk/risk_manager.py` | 风险管理 |
| `BaseStrategy` | `strategy/base_strategy.py` | 策略基类 |
| `DataFeed` | `data/datafeed.py` | 数据馈送（SQLite/CSV/JSON） |
| `PerformanceAnalyzer` | `analyzer/performance.py` | 绩效分析 |
| `StateStore` | `utils/state_store.py` | Redis 状态持久化 |

### 资金流转

```
初始资金 → Account.allocate_equal() 均分到每个策略-品种对
    ↓
Portfolio.capital (各策略独立)
    ↓
开仓: capital -= 买入金额 + 手续费
    ↓
持仓期间: 浮动盈亏 = (当前价 - 入场价) × 数量
    ↓
平仓: capital += 卖出金额 - 手续费
    ↓
最终: total_equity = capital + 持仓市值
```

---

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt

# 可选：Redis 实盘状态持久化
pip install redis
```

### 运行模式

```bash
# 回测（默认模式）
python main.py

# 指定配置文件
python main.py --config my_config.json

# 实盘模式
python main.py --mode live

# 下载历史K线数据
python main.py --download

# 生成 HTML 报告（从已有 JSON 结果）
python main.py --report results/backtest_*.json
```

### 策略控制

```bash
# 回测指定策略（逗号分隔名称）
python main.py --strategies "ETH网格"
python main.py --strategies "ETH网格,趋势跟踪型,双均线交叉"

# 实盘指定策略
python main.py --mode live --strategies "均线斜率策略"

# 测试网 / 主网
python main.py --mode live --testnet      # 测试网
python main.py --mode live --no-testnet   # 主网
```

### 命令行参数

| 参数 | 默认值 | 可选值 | 说明 |
|------|--------|--------|------|
| `--mode` | `backtest` | `backtest`, `live` | 运行模式 |
| `--config` | `config.json` | 任意 .json 文件 | 配置文件路径 |
| `--download` | - | - | 下载历史K线后退出 |
| `--testnet` | 使用配置值 | - | 币安测试网 |
| `--no-testnet` | 使用配置值 | - | 币安主网 |
| `--strategies` | 全部启用 | 逗号分隔名称 | 指定运行的策略 |
| `--report` | - | JSON 文件路径 | 从 JSON 生成 HTML 报告 |

---

## 策略库

### 1. 综合均线策略 (MAStrategy)
**文件**: `strategy/ma_strategy.py`

结合 EMA 均线系统、MACD、ADX 和 SuperTrend 的多重过滤趋势跟踪策略。

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ema_fast` / `ema_medium` / `ema_slow` | 12 / 26 / 50 | EMA 周期 |
| `ema_trend` | 200 | 长期趋势线 |
| `macd_fast` / `macd_slow` / `macd_signal` | 12 / 26 / 9 | MACD 参数 |
| `adx_period` / `adx_threshold` | 14 / 25 | ADX 趋势强度 |
| `super_atr_period` / `super_multiplier` | 10 / 2.8 | SuperTrend 参数 |

**入场**: EMA12 > EMA26 > EMA50（多头排列）+ MACD > 0 + ADX > 25，confidence >= 0.5  
**出场**: SuperTrend 反转 或 趋势转弱（排列破坏 / MACD 转负）  
**评分加分项**: 长期多头、短期强势、MACD 动能增强、SuperTrend 买入、强趋势 (ADX>40)

### 2. 双均线交叉策略 (DualMAStrategy)
**文件**: `strategy/dual_ma_strategy.py`

经典的快慢 EMA 金叉/死叉策略。

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ema_fast` | 12 | 快速 EMA |
| `ema_slow` | 26 | 慢速 EMA |

**买入**: 快线上穿慢线（金叉）  
**卖出**: 快线下穿慢线（死叉）

### 3. 三均线趋势策略 (TripleMAStrategy)
**文件**: `strategy/triple_ma_strategy.py`

使用三条 EMA 判断多头排列。

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ema_fast` | 10 | 快速 EMA |
| `ema_medium` | 30 | 中速 EMA |
| `ema_slow` | 60 | 慢速 EMA |

**买入**: fast > medium > slow（多头排列）+ 价格在快线上方  
**卖出**: 多头排列消失

### 4. 网格交易策略 (GridStrategy)
**文件**: `strategy/grid_strategy.py`

在设定的价格区间内低买高卖，赚取网格利润。

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `grid_low` / `grid_high` | 10000 / 60000 | 价格区间 |
| `grid_count` | 20 | 网格等分数 |
| `position_per_grid_pct` | 5.0% | 每格投入资金 |

**工作原理**: 将 `[grid_low, grid_high]` 等分 → 价格下跌穿越网格线买入 → 价格上涨穿越网格线卖出。每个格子只买一次，跟踪每个网格的持仓状态。

### 5. 均线斜率策略 (SlopeMAStrategy)
**文件**: `strategy/slope_ma_strategy.py`

使用 SMA 斜率判断趋势方向。

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ma_fast` / `ma_medium` / `ma_slow` | 5 / 10 / 20 | SMA 周期 |
| `trail_drop` | 2% | MA5 回落平仓 |
| `take_profit` / `stop_loss` | 8% / 2% | 止盈/止损 |

**买入**: MA5、MA10、MA20 斜率同时向上  
**卖出**: MA5 从最高价回落 trail_drop% 或 止盈/止损

---

## 配置文件

### config.json 结构

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `mode` | string | `backtest` | 运行模式 |
| `interval` | string | `1h` | K线周期 |
| `initial_capital` | float | 10000.0 | 初始资金 |
| `db_path` | string | `market_data.db` | 历史数据数据库 |
| `output_path` | string | `results` | 结果输出目录 |
| `start_date` / `end_date` | string | - | 回测时间范围 |
| `max_workers` | int | auto | 并行回测进程数 |
| `pending_timeout_ms` | int | 300000 | 挂单超时(ms) |
| `pending_check_interval_sec` | int | 60 | 挂单巡检间隔(s) |

### Binance 配置

```json
"binance": {
    "api_key": "${BINANCE_API_KEY}",      // 支持环境变量注入
    "api_secret": "${BINANCE_API_SECRET}",
    "testnet": true,
    "quote_asset": "USDT"
}
```

### Redis 配置（实盘状态持久化）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `redis.host` | localhost | Redis 主机 |
| `redis.port` | 6379 | Redis 端口 |
| `redis.db` | 0 | 数据库编号 |
| `redis.password` | null | 密码 |
| `redis.ttl_seconds` | 604800 | Key 过期时间(7天) |
| `redis.equity_max_len` | 2000 | 权益曲线最大记录数 |

### 策略注册

策略类自动发现: 在 `strategy/` 目录下扫描所有继承 `BaseStrategy` 的类。  
通过 `strategy_mapping` 将配置文件中的 type 字段映射到策略类:

```json
"strategy_mapping": {
    "ma_strategy": "MAStrategy",
    "grid_strategy": "GridStrategy",
    "dual_ma_strategy": "DualMAStrategy",
    "triple_ma_strategy": "TripleMAStrategy",
    "slope_ma_strategy": "SlopeMAStrategy"
}
```

每个策略条目:

| 参数 | 说明 |
|------|------|
| `name` | 策略名称（显示用） |
| `type` | 策略类型（对应 strategy_mapping） |
| `enabled` | 是否启用 |
| `symbol` | 交易对 |
| `stop_loss_pct` / `take_profit_pct` | 风控参数（覆盖默认值） |
| `trailing_stop_pct` / `position_size_pct` | 移动止损 / 仓位比例 |
| `max_positions` | 最大持仓数 |
| 策略特有参数 | 见各策略文档 |

**注意**: 配置文件支持 `//` 行注释（不标准但方便），系统自动去除注释后解析。支持 `$(ENV_VAR)` 和 `${ENV_VAR}` 环境变量注入敏感信息。

---

## 回测功能

### 单品种回测

```python
from core.backtest_engine import BacktestEngine
from strategy.ma_strategy import MAStrategy

engine = BacktestEngine(initial_capital=10000.0)
engine.add_strategy(MAStrategy({"name": "MAStrategy"}))
result = engine.run(
    symbol="btcusdt", interval="1h",
    start_date="2025-01-01", end_date="2025-05-25",
)
print(result.summary())
```

### 多品种多策略回测

```python
engine.add_strategy(ma_strategy)     # 品种: btcusdt
engine.add_strategy(grid_strategy)   # 品种: ethusdt
result = engine.run_many(symbols=["btcusdt", "ethusdt"], interval="1h")
```

所有 K 线按时间排序后组成统一事件流，跨品种时间对齐。

### 并行回测

```python
python main.py  # 使用 ProcessPoolExecutor 自动并行执行
```

`config.json` 中配置:
- `max_workers`: 并行进程数（默认 CPU 核心数）
- 每个策略-品种对作为一个独立任务，自动均分资金

### 回测结果输出

| 输出格式 | 文件 | 说明 |
|----------|------|------|
| JSON | `results/backtest_*.json` | 完整回测数据 |
| HTML | 同目录 `.html` 文件 | 独立可视化报告（ECharts 权益曲线图表） |

### 回测性能指标

| 指标 | 说明 | 计算方式 |
|------|------|----------|
| 总交易次数 | total_trades | 已平仓持仓数 |
| 胜率 | win_rate | 盈利交易 / 总交易 × 100% |
| 盈亏比 | profit_factor | 总盈利 / 总亏损 |
| 总收益率 | total_return_pct | (final_equity / initial_capital - 1) × 100% |
| 最大回撤 | max_drawdown | 从权益曲线计算 (peak - valley) / peak |
| 夏普比率 | sharpe_ratio | 年化收益 / 年化波动 |
| 净盈亏 | net_profit | 总盈亏（已扣除手续费） |

---

## 实盘功能

### 数据流

- 通过 Binance WebSocket 接收实时 K 线数据
- 支持所有 Binance K 线周期（1m ~ 1M）
- 自动关闭的 K 线触发交易逻辑
- 支持多品种同时订阅

### 交易所交互

`BinanceBroker` 提供完整交易所交互:

| 功能 | 说明 |
|------|------|
| 市价单 | 立即以市场价成交 |
| 限价单 | 挂单等待成交，定时巡检 |
| 挂单管理 | PENDING 订单巡检 + 超时自动撤单 |
| 部分成交 | 按增量创建/更新持仓 |
| 余额同步 | 启动时从交易所获取 USDT 余额 |
| 持仓同步 | 从交易所获取当前持仓 |
| 重复平仓防护 | 精确检测持仓是否已有 SELL 挂单 |

### 状态持久化 (Redis)

- 每根 K 线处理后自动保存 Portfolio / 挂单 / 权益曲线到 Redis
- 引擎重启时优先从 Redis 恢复
- Redis 不可用时回退到 Binance API 同步
- Key TTL 可配置（默认 7 天）

### 启动流程

```
1. 创建 LiveEngine
2. 同步交易所余额（取实际余额与配置的最小值）
3. 过滤已启用策略
4. 均分资金，创建投资组合
5. 尝试 Redis 状态恢复
   ├─ 成功 → 跳过同步
   └─ 失败 → 从 Binance API 同步余额/持仓/挂单
6. 策略预热（加载历史K线让指标到达最小需要）
7. 启动 WebSocket 数据流
8. 启动后台巡检线程
```

---

## 风控系统

共享的 `RiskManager` 适用于回测和实盘。

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `stop_loss_pct` | 2.0% | 触发止损 |
| `take_profit_pct` | 6.0% | 触发止盈 |
| `trailing_stop_pct` | 1.5% | 移动止损 |
| `position_size_pct` | 20.0% | 单次建仓资金比例 |

风控检查顺序（每根 K 线）:
1. 更新持仓最高价
2. 检查止损
3. 检查止盈
4. 检查移动止损

每个策略-品种对可以配置独立的风控参数。

---

## GUI 配置工具

```bash
python config_app.py
```

基于 tkinter 的策略参数配置工具，支持:
- 按策略类型分 Tab 管理（趋势跟踪、双均线、三均线、网格、均线斜率）
- 表格展示，支持新增/修改/删除策略
- 实时保存到配置文件

---

## 技术指标 (TA)

**文件**: `indicator/ta.py` — 基于 numpy 实现。

| 指标 | 方法 | 说明 |
|------|------|------|
| SMA | `TA.sma(data, period)` | 简单移动平均线 |
| EMA | `TA.ema(data, period)` | 指数移动平均线 |
| MACD | `TA.macd(data, fast, slow, signal)` | MACD |
| RSI | `TA.rsi(data, period)` | 相对强弱指标 |
| ATR | `TA.atr(high, low, close, period)` | 平均真实波幅 |
| 布林带 | `TA.bollinger(data, period, std_dev)` | 布林带 |
| ADX | `TA.adx(high, low, close, period)` | 平均趋向指数 |
| SuperTrend | `TA.super_trend(high, low, close, period, multiplier)` | 超级趋势 |

---

## 编写自定义策略

继承 `BaseStrategy` 并实现 `on_bar` 方法:

```python
from strategy.base_strategy import BaseStrategy, Signal
from data.datafeed import Bar

class MyStrategy(BaseStrategy):
    def on_start(self):
        # 初始化（可选）
        pass

    def on_bar(self, bar: Bar):
        # 处理每个K线
        if bar.close > self.sma:
            return Signal(
                action="BUY", price=bar.close,
                timestamp=bar.time, reason="突破",
                confidence=0.7,
            )
        return None  # 无信号

    def on_position_open(self, signal: Signal, price: float):
        pass

    def on_position_close(self, price: float, reason: str, pnl: float):
        pass

    def on_end(self):
        # 清理（可选）
        pass
```

在 `config.json` 的 `strategy_mapping` 中注册策略类后即可在配置文件中使用。

---

## 日志

- 终端输出: 所有 INFO 级别及以上日志
- 文件轮转: `logs/live_YYYYMMDD.log`（保留 30 天）
- 信号日志: `logs/signal_YYYYMMDD.log`（仅引擎/订单相关日志）

---

## 依赖

| 包 | 版本要求 | 说明 |
|---|---|---|
| `python-binance` | >= 1.0.19 | 币安 API (REST + WebSocket) |
| `numpy` | >= 1.24.0 | 数值计算 |
| `redis` | >= 4.5.0 | Redis（可选，实盘状态持久化） |

---

## 常见问题

### Q: 回测结果与实盘不一致？
A: 回测使用收盘价模拟，实盘使用实时价格。此外回测 Broker 立即按 bar.close 成交，实盘有挂单/滑点/部分成交等因素。

### Q: 如何添加多个策略？
A: 在 `config.json` 的 `strategies` 数组中添加多个策略条目，系统会自动均分资金并并行管理。

### Q: API Key 如何安全配置？
A: 使用环境变量 `$(BINANCE_API_KEY)` 注入，不要将密钥直接写入配置文件。

### Q: 如何切换回测/实盘？
A: 使用 `BacktestEngine` 进行回测，`LiveEngine` 进行实盘，两者接口一致。通过 `--mode` 参数切换。

### Q: Redis 必须安装吗？
A: 不需要。Redis 不可用时实盘引擎降级为无状态模式，回退到从 Binance API 同步状态。

---

## License

GNU General Public License v3.0