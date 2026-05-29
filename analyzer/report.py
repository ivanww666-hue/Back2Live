# -*- coding: utf-8 -*-
"""
报告生成模块
===========
生成格式化的回测报告，支持文本和 JSON 格式输出。
"""

from typing import Dict, Optional, List
from datetime import datetime
from .performance import PerformanceAnalyzer


class ReportGenerator:
    """
    报告生成器

    将回测结果和绩效分析转换为可读的报告格式。
    """

    def __init__(self, result_dict: dict):
        """
        Args:
            result_dict: 回测结果字典
        """
        self.data = result_dict
        self.analyzer = PerformanceAnalyzer(result_dict)

    def generate_text_report(self) -> str:
        """生成文本格式报告"""
        config = self.data.get("config", {})
        summary = self.data.get("summary", {})
        perf = self.analyzer.get_summary()

        lines = []
        lines.append("=" * 60)
        lines.append("  回测报告")
        lines.append("=" * 60)
        lines.append("")

        # 基本信息
        lines.append("【基本信息】")
        lines.append(f"  交易对: {config.get('symbol', 'N/A')}")
        lines.append(f"  K线周期: {config.get('interval', 'N/A')}")
        lines.append(f"  数据量: {config.get('data_count', 0)} 根K线")
        lines.append(f"  初始资金: ${config.get('initial_capital', 0):,.2f}")
        lines.append(f"  策略: {', '.join(config.get('strategies', []))}")
        lines.append("")

        # 时间范围
        start_ts = self.data.get("start_time", 0)
        end_ts = self.data.get("end_time", 0)

        def fmt_time(ts):
            if ts and ts > 0:
                # 支持毫秒和秒时间戳
                if ts > 1e12:
                    ts = ts / 1000
                return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            return "N/A"

        lines.append("【时间范围】")
        lines.append(f"  开始: {fmt_time(start_ts)}")
        lines.append(f"  结束: {fmt_time(end_ts)}")
        lines.append(f"  耗时: {self.data.get('duration_seconds', 0):.2f} 秒")
        lines.append("")

        # 账户摘要
        lines.append("【账户摘要】")
        lines.append(f"  总收益率: {summary.get('total_return_pct', 0):+.2f}%")
        lines.append(f"  总盈亏: ${summary.get('total_return', 0):+,.2f}")
        lines.append(f"  总手续费: ${summary.get('total_fee', 0):,.2f}")
        lines.append(f"  净盈亏: ${summary.get('net_profit', 0):+,.2f}")
        lines.append("")

        # 绩效指标
        lines.append("【绩效指标】")
        lines.append(f"  总交易次数: {perf['total_trades']}")
        lines.append(f"  盈利交易: {perf['winning_trades']}")
        lines.append(f"  亏损交易: {perf['losing_trades']}")
        lines.append(f"  胜率: {perf['win_rate_pct']:.2f}%")
        lines.append(f"  平均盈利: ${perf['avg_win']:+,.2f}")
        lines.append(f"  平均亏损: ${perf['avg_loss']:+,.2f}")
        lines.append(f"  盈亏比: {perf['profit_factor']:.2f}")
        lines.append("")

        # 风险指标
        lines.append("【风险指标】")
        lines.append(f"  最大回撤: {perf['max_drawdown_pct']:.2f}%")
        lines.append(f"  最大回撤持续: {perf['max_drawdown_duration']} 根K线")
        lines.append(f"  夏普比率: {perf['sharpe_ratio']:.2f}")
        lines.append(f"  卡玛比率: {perf['calmar_ratio']:.2f}")
        lines.append("")

        # 最近交易（按品种+策略分组）
        trades = self.data.get("trades", [])
        if trades:
            lines.append("【最近交易记录】")
            # 按 (symbol, strategy) 分组
            from collections import OrderedDict
            groups: Dict[str, List[dict]] = OrderedDict()
            for t in trades:
                key = f"{t.get('symbol', '?')} | {t.get('strategy', '?')}"
                if key not in groups:
                    groups[key] = []
                groups[key].append(t)

            for key, group_trades in groups.items():
                symbol, strategy = key.split(" | ", 1)
                lines.append(f"  [{symbol}] 策略: {strategy}")
                lines.append(f"  {'时间':<20} {'类型':<6} {'方向':<6} {'价格':<12} {'数量':<10} {'手续费':<10} {'原因':<12}")
                lines.append("  " + "-" * 76)
                for t in group_trades[-10:]:
                    t_time = fmt_time(t.get("time", 0))
                    t_type = t.get("type", "?")
                    side = t.get("side", "?")
                    price = f"${t.get('price', 0):,.2f}"
                    qty = f"{t.get('quantity', 0):.8f}"
                    fee = f"${t.get('fee', 0):,.2f}"
                    reason = t.get("reason", "")
                    lines.append(f"  {t_time:<20} {t_type:<6} {side:<6} {price:<12} {qty:<10} {fee:<10} {reason:<12}")
                lines.append("")
            lines.append("")

        lines.append("=" * 60)
        return "\n".join(lines)

    def generate_json_report(self) -> dict:
        """生成 JSON 格式报告"""
        return {
            "report_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "config": self.data.get("config", {}),
            "time_range": {
                "start": self.data.get("start_time", 0),
                "end": self.data.get("end_time", 0),
                "duration_seconds": self.data.get("duration_seconds", 0),
            },
            "summary": self.data.get("summary", {}),
            "performance": self.analyzer.get_summary(),
            "trade_count": len(self.data.get("trades", [])),
        }

    def print_report(self):
        """打印报告到控制台"""
        print(self.generate_text_report())
