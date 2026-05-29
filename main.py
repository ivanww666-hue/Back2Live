# -*- coding: utf-8 -*-
"""
主入口
=====
虚拟货币现货交易回测/实盘系统

支持:
1. 回测模式: 使用历史数据测试策略
2. 实盘模式: 连接 Binance 进行实盘/模拟盘交易
3. 多策略多品种同时运行
4. 回测/实盘自由切换
"""

import os
import sys
import json
import logging
from typing import Optional, Dict, Any, List
from concurrent.futures import ProcessPoolExecutor, as_completed
import traceback
from datetime import datetime
from utils.json_util import load_json_with_comments

# 配置日志
from utils.logger import init_logging
init_logging()
logger = logging.getLogger("Main")


# ---- 策略动态加载 ----
_STRATEGY_CACHE: Dict[str, type] = {}


def _load_strategy_mapping() -> Dict[str, type]:
    """从 config.json 中的 strategy_mapping 加载策略类型映射"""
    global _STRATEGY_CACHE
    if _STRATEGY_CACHE:
        return _STRATEGY_CACHE

    import importlib
    import pkgutil
    from strategy.base_strategy import BaseStrategy

    # 自动发现所有策略类
    discovered = {}
    strategy_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategy")
    if os.path.isdir(strategy_dir):
        for _, module_name, _ in pkgutil.iter_modules([strategy_dir]):
            if module_name.startswith("_") or module_name == "base_strategy":
                continue
            try:
                mod = importlib.import_module(f"strategy.{module_name}")
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if isinstance(attr, type) and issubclass(attr, BaseStrategy) and attr is not BaseStrategy:
                        discovered[attr_name] = attr
            except Exception as e:
                logger.warning(f"跳过模块 strategy.{module_name}: {e}")

    # 从配置文件加载映射: type_key → class_name
    mapping = {}
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        cfg = load_json_with_comments(config_path)
        strategy_mapping = cfg.get("strategy_mapping", {})
        for type_key, class_name in strategy_mapping.items():
            if class_name in discovered:
                mapping[type_key] = discovered[class_name]
            else:
                logger.warning(f"strategy_mapping: 类 '{class_name}' 未在 strategy/ 中找到")
    except Exception as e:
        logger.warning(f"加载 strategy_mapping 失败: {e}，回退到自动命名")

    _STRATEGY_CACHE = mapping
    logger.info("=" * 50)
    logger.info("  策略映射 — config.json 中 type 字段可选值:")
    for key, cls in mapping.items():
        logger.info(f"    \"type\": \"{key}\"  →  {cls.__name__}")
    logger.info("=" * 50)
    return mapping


def _create_strategy(strategy_type: str, config: dict):
    """
    根据策略类型创建策略实例。

    优先从 config.json 的 strategy_mapping 中查找，
    若无映射则自动将类名转为 snake_case 作为 type key。
    """
    mapping = _load_strategy_mapping()

    if strategy_type in mapping:
        return mapping[strategy_type](config)

    from strategy.ma_strategy import MAStrategy
    logger.warning(f"未找到策略类型 '{strategy_type}', 使用默认 MAStrategy")
    return MAStrategy(config)


def _run_single_backtest_task(args: dict) -> dict:
    """
    单个回测任务（独立进程执行）

    Args:
        args: {
            strategy_config: 策略配置 dict
            symbol: 交易对
            interval: K线周期
            start_date: 开始日期
            end_date: 结束日期
            db_path: 数据库路径
            initial_capital: 初始资金
            maker_fee: Maker 费率
            taker_fee: Taker 费率
            stop_loss_pct: 止损百分比
            take_profit_pct: 止盈百分比
            trailing_stop_pct: 移动止损百分比
            output_path: 输出目录
        }

    Returns:
        回测结果字典
    """
    from core.backtest_engine import BacktestEngine
    from analyzer.report import ReportGenerator

    symbol = args["symbol"]
    strategy_config = args["strategy_config"]
    strategy_name = strategy_config.get("name", "MAStrategy")
    strategy_type = strategy_config.get("type", "ma_strategy")

    # ---- 从策略配置读取风控参数（策略独立） ----
    stop_loss_pct = strategy_config.get("stop_loss_pct", args.get("stop_loss_pct", 2.0))
    take_profit_pct = strategy_config.get("take_profit_pct", args.get("take_profit_pct", 6.0))
    trailing_stop_pct = strategy_config.get("trailing_stop_pct", args.get("trailing_stop_pct", 1.5))
    position_size_pct = strategy_config.get("position_size_pct", args.get("position_size_pct", 20.0))
    max_positions = strategy_config.get("max_positions", 3)

    # ---- 根据策略类型加载策略类 ----
    strategy = _create_strategy(strategy_type, strategy_config)

    # 构建引擎配置
    engine_config = {
        "initial_capital": args["initial_capital"],
        "symbol": symbol,
        "interval": args["interval"],
        "db_path": args["db_path"],
        "output_path": args["output_path"],
        "strategies": [strategy_config],
    }

    # 创建引擎
    engine = BacktestEngine(
        initial_capital=args["initial_capital"],
        maker_fee=args["maker_fee"],
        taker_fee=args["taker_fee"],
        config=engine_config,
    )

    # 设置风控（使用策略自有参数）
    engine.setup_risk_manager(
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        trailing_stop_pct=trailing_stop_pct,
        position_size_pct=position_size_pct,
    )

    # 添加策略 & 创建投资组合
    engine.add_strategy(strategy)
    engine.create_portfolio(
        strategy_name=strategy_name,
        symbol=symbol,
        capital=args["initial_capital"],
        max_positions=max_positions,
    )

    task_label = f"{strategy_name}:{symbol}"
    logger = logging.getLogger("BacktestTask")
    logger.info(f"[{task_label}] 开始回测 (sl={stop_loss_pct}% tp={take_profit_pct}% ts={trailing_stop_pct}% pos={position_size_pct}%)")

    # 运行回测
    result = engine.run(
        symbol=symbol,
        interval=args["interval"],
        start_date=args.get("start_date"),
        end_date=args.get("end_date"),
        db_path=args["db_path"],
    )

    # 打印结果摘要
    print(f"\n{'='*60}")
    print(f"  [{task_label}] 回测结果")
    print(result.summary())

    # 生成报告
    report = ReportGenerator({
        "summary": result.to_dict(),
        "config": result.config,
        "trades": result.trades,
        "equity_curve": result.equity_curve,
        "drawdown_curve": result.drawdown_curve,
        "start_time": result.start_time,
        "end_time": result.end_time,
        "duration_seconds": result.duration_seconds,
    })
    report.print_report()

    # 保存结果
    output_path = args.get("output_path", "results")
    os.makedirs(output_path, exist_ok=True)
    safe_symbol = symbol.replace("/", "_").replace(":", "_")
    safe_name = strategy_name.replace("/", "_").replace(":", "_")
    result_file = engine.save_results(
        os.path.join(output_path, f"backtest_{safe_name}_{safe_symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    )

    # 同时生成 HTML 报告
    from utils.report_html import json_to_html
    json_to_html(result_file, strategy_params=strategy_config)
    return {
        "strategy": strategy_name,
        "symbol": symbol,
        "summary": result.to_dict(),
        "trades": result.trades,
        "equity_curve": result.equity_curve,
        "result_file": result_file,
    }


def run_backtest(config: dict) -> dict:
    """
    运行回测（支持并行多策略多品种）

    Args:
        config: 配置字典

    Returns:
        回测结果字典（合并所有任务）
    """
    strategies_config = config.get("strategies", [])
    if not strategies_config:
        return {}

    interval = config.get("interval", "1h")
    start_date = config.get("start_date")
    end_date = config.get("end_date")
    db_path = config.get("db_path", "market_data.db")
    initial_capital = config.get("initial_capital", 10000.0)
    maker_fee = config.get("maker_fee", 0.001)
    taker_fee = config.get("taker_fee", 0.001)
    output_path = config.get("output_path", "results")
    max_workers = config.get("max_workers", None)

    # 过滤已启用的策略
    enabled_strategies = [sc for sc in strategies_config if sc.get("enabled", True)]
    skipped_count = len(strategies_config) - len(enabled_strategies)
    if skipped_count > 0:
        logger.info(f"跳过 {skipped_count} 个未启用的策略")

    # 命令行指定策略名称过滤
    name_filter = config.get("_strategy_filter")
    if name_filter:
        before = len(enabled_strategies)
        enabled_strategies = [sc for sc in enabled_strategies if sc.get("name") in name_filter]
        logger.info(f"按名称过滤: {before}→{len(enabled_strategies)} 个策略 (指定: {name_filter})")

    # 按启用策略数量均分初始资金
    strategy_count = len(enabled_strategies)
    capital_per_strategy = initial_capital / strategy_count if strategy_count > 0 else initial_capital
    logger.info(f"启用策略数: {strategy_count}, 每策略本金: ${capital_per_strategy:,.2f} (总资金: ${initial_capital:,.2f})")

    # 构建任务列表: 每個策略條目自帶 symbol
    tasks = []
    for sc in enabled_strategies:
        symbol = sc.get("symbol", "btcusdt")
        tasks.append({
            "strategy_config": sc,
            "symbol": symbol,
            "interval": interval,
            "start_date": start_date,
            "end_date": end_date,
            "db_path": db_path,
            "initial_capital": capital_per_strategy,
            "maker_fee": maker_fee,
            "taker_fee": taker_fee,
            "output_path": output_path,
        })

    total_tasks = len(tasks)
    if total_tasks == 0:
        logger.warning("没有回测任务")
        return {}

    logger.info(f"并行回测: {total_tasks} 个任务 (策略×品种), "
                f"max_workers={max_workers or 'auto'}")

    # 并行执行
    all_results = []
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_task = {
            executor.submit(_run_single_backtest_task, task): task
            for task in tasks
        }

        for future in as_completed(future_to_task):
            task = future_to_task[future]
            label = f"{task['strategy_config'].get('name', '?')}:{task['symbol']}"
            try:
                result = future.result()
                all_results.append(result)
                logger.info(f"[{label}] 完成: 收益率={result['summary']['total_return_pct']:.2f}%")
            except Exception as e:
                logger.error(f"[{label}] 失败: {e}")
                traceback.print_exc()

    # 合并结果
    merged_summary = {
        "tasks": len(all_results),
        "total_tasks": total_tasks,
        "per_task": [
            {"strategy": r["strategy"], "symbol": r["symbol"],
             "total_return_pct": r["summary"].get("total_return_pct", 0),
             "total_trades": r["summary"].get("total_trades", 0),
             "win_rate": r["summary"].get("win_rate", 0),
             "max_drawdown": r["summary"].get("max_drawdown", 0),
             "result_file": r.get("result_file", "")}
            for r in all_results
        ],
    }

    # 汇总收益
    if all_results:
        avg_return = sum(r["summary"].get("total_return_pct", 0) for r in all_results) / len(all_results)
        merged_summary["avg_return_pct"] = round(avg_return, 2)

    return merged_summary


def download_historical_data_from_config(config: dict):
    """
    根据配置文件中所有策略的品种，下载历史K线数据

    自动去重，避免同一品种重复下载。
    """
    from data.binance_feed import HistoricalDataDownloader

    strategies_config = config.get("strategies", [])
    db_path = config.get("db_path", "market_data.db")
    interval = config.get("interval", "1h")
    start_date = config.get("start_date", "2024-01-01")
    end_date = config.get("end_date")

    # 从策略中收集唯一品种
    symbols = set()
    for sc in strategies_config:
        symbol = sc.get("symbol", "")
        if symbol:
            symbols.add(symbol.lower())

    if not symbols:
        logger.error("配置文件中未找到任何品种")
        return

    symbols = sorted(symbols)
    logger.info(f"将下载以下品种的历史数据: {symbols}")
    logger.info(f"K线周期: {interval}, 时间范围: {start_date} ~ {end_date or '当前'}")
    logger.info(f"数据库: {db_path}")

    downloader = HistoricalDataDownloader(db_path)

    for symbol in symbols:
        logger.info(f"\n{'='*50}")
        logger.info(f"下载 {symbol} {interval} ...")
        try:
            downloader.download(symbol, interval, start_date, end_date)
        except Exception as e:
            logger.error(f"下载 {symbol} 失败: {e}")

    logger.info(f"\n所有品种下载完毕!")


def run_live(config: dict):
    """
    运行实盘

    参考回测流程：创建引擎 → 设置风控 → 添加策略 → 创建投资组合 → 启动实盘

    Args:
        config: 配置字典
    """
    from core.live_engine import LiveEngine

    initial_capital = config.get("initial_capital", 10000.0)
    maker_fee = config.get("maker_fee", 0.001)
    taker_fee = config.get("taker_fee", 0.001)
    interval = config.get("interval", "1m")

    # 1. 创建引擎
    engine = LiveEngine(
        initial_capital=initial_capital,
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        config=config,
    )

    # 1.5 从 Binance 同步真实余额，用实际余额替代配置中的 initial_capital
    try:
        engine.binance_broker.sync_balance()
        actual_balance = engine.account.initial_capital
        if actual_balance > 0:
            logger.info(f"从交易所同步余额: ${actual_balance:,.2f} (配置: ${initial_capital:,.2f})")
            initial_capital = min(initial_capital, actual_balance)
            logger.info(f"实盘使用资金: ${initial_capital:,.2f}")
        else:
            logger.warning("交易所余额为 0，使用配置中的 initial_capital")
    except Exception as e:
        logger.error(f"同步余额失败: {e}，使用配置中的 initial_capital")

    strategies_config = config.get("strategies", [])
    if not strategies_config:
        logger.warning("没有配置任何策略，使用默认 MA 策略")
        strategies_config = [{"name": "MAStrategy", "type": "ma_strategy", "enabled": True}]

    # 过滤已启用的策略
    enabled_strategies = [sc for sc in strategies_config if sc.get("enabled", True)]
    skipped_count = len(strategies_config) - len(enabled_strategies)
    if skipped_count > 0:
        logger.info(f"跳过 {skipped_count} 个未启用的策略")

    # 命令行指定策略名称过滤
    name_filter = config.get("_strategy_filter")
    if name_filter:
        before = len(enabled_strategies)
        enabled_strategies = [sc for sc in enabled_strategies if sc.get("name") in name_filter]
        logger.info(f"按名称过滤: {before}→{len(enabled_strategies)} 个策略 (指定: {name_filter})")

    # 收集所有需要交易的品种（只取启用的）
    symbols = set()
    for sc in enabled_strategies:
        sym = (sc.get("symbol") or "btcusdt").lower()
        symbols.add(sym)
    symbols = sorted(symbols)

    # 2. 按启用策略数量均分本金
    strategy_count = len(enabled_strategies)
    capital_per_strategy = initial_capital / strategy_count if strategy_count > 0 else initial_capital
    logger.info(f"启用策略数: {strategy_count}, 每策略本金: ${capital_per_strategy:,.2f} (总余额: ${initial_capital:,.2f})")

    for sc in enabled_strategies:
        strategy_type = sc.get("type", "ma_strategy")
        strategy = _create_strategy(strategy_type, sc)

        symbol = sc.get("symbol", "btcusdt").lower()
        strategy_name = sc.get("name", strategy.name)
        max_positions = sc.get("max_positions", 3)

        engine.add_strategy(strategy)

        risk_mgr = engine.create_risk_manager_from_config(sc)

        engine.create_portfolio(
            strategy_name=strategy_name,
            symbol=symbol,
            capital=capital_per_strategy,
            max_positions=max_positions,
            risk_manager=risk_mgr,
        )

        logger.info(
            f"添加策略: {strategy_name} | 品种: {symbol} | "
            f"资本: ${capital_per_strategy:,.2f} | "
            f"风控: sl={risk_mgr.stop_loss_pct}% tp={risk_mgr.take_profit_pct}% "
            f"ts={risk_mgr.trailing_stop_pct}% pos_size={risk_mgr.position_size_pct}% max_pos={max_positions}"
        )

    # 3. 启动实盘
    engine.run(symbols, interval)

    logger.info(f"实盘运行中... (按 Ctrl+C 停止)")
    try:
        import time
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        logger.info("收到停止信号")
    finally:
        engine.stop()

    # 保存权益曲线
    output_path = config.get("output_path", "results")
    os.makedirs(output_path, exist_ok=True)
    with open(os.path.join(output_path, "live_equity_curve.json"), "w") as f:
        json.dump(engine.equity_curve, f, ensure_ascii=False, indent=2)
    logger.info("权益曲线已保存")


def main():
    """主入口"""
    import argparse

    parser = argparse.ArgumentParser(
        description="虚拟货币现货交易回测/实盘系统"
    )
    parser.add_argument("--mode", type=str, default="backtest",
                        choices=["backtest", "live"],
                        help="运行模式: backtest(回测) / live(实盘)")
    parser.add_argument("--config", type=str, default="config.json",
                        help="配置文件路径 (JSON)")
    parser.add_argument("--download", action="store_true", default=False,
                        help="下载模式: 根据配置文件下载所有品种的历史K线")
    parser.add_argument("--testnet", action="store_true", default=None,
                        help="使用测试网 (实盘模式)")
    parser.add_argument("--no-testnet", action="store_false", dest="testnet",
                        help="使用主网 (实盘模式)")
    parser.add_argument("--strategies", type=str, default=None,
                        help="指定运行的策略名称（逗号分隔），默认全部启用")
    parser.add_argument("--report", type=str, default=None,
                        help="生成 HTML 报告: 指定回测结果 JSON 文件路径")

    args = parser.parse_args()

    # ---- 报告模式：JSON → HTML ----
    if args.report:
        from utils.report_html import json_to_html
        # 尝试从 config.json 查找对应策略参数
        params = None
        try:
            if os.path.exists(args.config):
                cfg = load_json_with_comments(args.config)
                strategies = cfg.get('strategies', [])
                import json as _json
                with open(args.report, 'r', encoding='utf-8') as _f:
                    _data = _json.load(_f)
                strat_names = _data.get('config', {}).get('strategies', [])
                strat_name = strat_names[0] if strat_names else ''
                for sc in strategies:
                    if sc.get('name') == strat_name:
                        params = sc
                        break
        except Exception:
            pass
        json_to_html(args.report, strategy_params=params)
        return
        return

    # 从配置文件加载
    if not os.path.exists(args.config):
        logger.error(f"配置文件不存在: {args.config}")
        return

    config = load_json_with_comments(args.config)
    logger.info(f"从配置文件加载: {args.config}")

    # 命令行 testnet 覆盖配置文件（只在用户明确指定时生效）
    if args.testnet is not None:
        if "binance" in config:
            config["binance"]["testnet"] = args.testnet
        else:
            config["testnet"] = args.testnet

    # 命令行指定策略名称过滤
    if args.strategies:
        names = set(name.strip() for name in args.strategies.split(","))
        config["_strategy_filter"] = names
        logger.info(f"指定策略: {names}")

    # ---- 下载模式 ----
    if args.download:
        download_historical_data_from_config(config)
        return

    if args.mode == "backtest":
        merged = run_backtest(config)
        logger.info(f"回测完成! {merged.get('total_tasks', 0)} 个任务, "
                     f"平均收益率: {merged.get('avg_return_pct', 0):.2f}%")
    else:
        run_live(config)


if __name__ == "__main__":
    main()