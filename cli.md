# 命令行参数参考

## 基本配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--mode` | `str` | `backtest` | 运行模式：`backtest`（回测）或 `live`（实盘） |
| `--config` | `str` | `config.json` | 配置文件路径（JSON 格式） |
| `--download` | `flag` | `false` | 下载模式：根据配置文件下载所有品种的历史K线后退出 |

## 网络环境

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--testnet` | `flag` | `true` | 使用币安测试网（实盘模式） |
| `--no-testnet` | `flag` | — | 使用币安主网（实盘模式） |

## 策略控制

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--strategies` | `str` | `None` | 指定运行的策略名称（逗号分隔），未指定时运行所有 `enabled=true` 的策略 |

---

## 使用示例

### 回测

```bash
# 默认回测（运行 config.json 中全部启用策略）
python main.py

# 指定配置文件回测
python main.py --mode backtest --config my_config.json

# 回测指定策略
python main.py --strategies "ETH网格"
python main.py --strategies "ETH网格,趋势跟踪型,双均线交叉"
```

### 实盘

```bash
# 测试网实盘（全部策略）
python main.py --mode live --testnet

# 主网实盘
python main.py --mode live --no-testnet

# 测试网实盘指定策略
python main.py --mode live --strategies "ETH网格"

# 主网实盘指定策略
python main.py --mode live --no-testnet --strategies "趋势跟踪型"
```

### 下载历史数据

```bash
# 根据 config.json 中策略的品种下载数据
python main.py --download

# 指定配置文件下载
python main.py --config my_config.json --download
```

---

## 完整参数表

| 参数 | 类型 | 默认值 | 可选值 | 作用域 |
|------|------|--------|--------|--------|
| `--mode` | str | `backtest` | `backtest`, `live` | 全局 |
| `--config` | str | `config.json` | 任意 JSON 文件路径 | 全局 |
| `--download` | flag | `false` | — | 下载模式 |
| `--testnet` | flag | `true` | — | 实盘 |
| `--no-testnet` | flag | — | — | 实盘 |
| `--strategies` | str | `None` | 逗号分隔的策略名称 | 回测 + 实盘 |

---

## 运行控制

| 环境变量 | 说明 |
|------|------|
| `PYTHONPATH` | 确保项目根目录在路径中，否则需从 `f:/backtest` 运行 |

程序入口：`python main.py [参数]`，必须从项目根目录执行。

## 示例命令

| 场景 | 命令 |
|------|------|
| 回测全部策略 | `python main.py --mode backtest` |
| 回测单个策略 | `python main.py --mode backtest --strategies "ETH网格"` |
| 实盘全部策略 | `python main.py --mode live` |
| 实盘指定策略 | `python main.py --mode live --strategies "ETH网格"` |
| 下载历史数据 | `python main.py --download` |
| 自定义配置文件 | `python main.py --config my_config.json --mode backtest` |
| 主网实盘 | `python main.py --mode live --no-testnet` |

python main.py --mode backtest --strategies "ETH网格"
python main.py --mode backtest --strategies "双均线交叉"
python main.py --mode backtest --strategies "三均线趋势"
python main.py --mode backtest --strategies "趋势跟踪型"
python main.py --mode backtest --strategies "均线斜率策略"