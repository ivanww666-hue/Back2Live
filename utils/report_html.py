# -*- coding: utf-8 -*-
"""
回测报告 JSON → HTML 转换工具
==============================
将 save_results 生成的 JSON 文件转换为独立的可视化 HTML 页面。
红涨绿跌（中国A股习惯）。
"""

import json
import os
import sys
from datetime import datetime


def _sample_data(data_list: list, max_points: int = 2000) -> list:
    n = len(data_list)
    if n <= max_points:
        return data_list
    step = n / max_points
    result = []
    for i in range(max_points):
        idx = int(i * step)
        if idx >= n:
            idx = n - 1
        result.append(data_list[idx])
    return result


def _ts_to_str(ts: int) -> str:
    try:
        return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


RED_UP = "#e74c3c"
GREEN_DOWN = "#00b894"
NEUTRAL = "#636e72"


def _pos_color(v):
    return RED_UP if v > 0 else GREEN_DOWN if v < 0 else NEUTRAL


def json_to_html(json_path: str, output_path: str = None, strategy_params: dict = None):
    if not os.path.exists(json_path):
        print(f"文件不存在: {json_path}")
        return None
    if output_path is None:
        output_path = os.path.splitext(json_path)[0] + ".html"

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    summary = data.get("summary", {})
    config = data.get("config", {})
    trades = data.get("trades", [])
    equity_curve = data.get("equity_curve", [])
    account = data.get("account", {})
    start_time = data.get("start_time", 0)
    end_time = data.get("end_time", 0)
    initial_capital = config.get("initial_capital", 10000.0)

    total_fee_sum = sum(t.get("fee", 0) for t in trades)
    total_pnl_sum = sum(t.get("pnl", 0) for t in trades if t.get("type") == "CLOSE")

    portfolios = account.get("portfolios", {})
    max_positions = 0
    risk_params = {}
    if portfolios:
        first_key = list(portfolios.keys())[0]
        pf = portfolios[first_key]
        final_cash = pf.get("current_capital", 0)
        final_equity = pf.get("total_equity", 0)
        open_positions = pf.get("open_positions", 0)
        position_value = final_equity - final_cash
        max_positions = pf.get("max_positions", 0)
        risk_params = pf.get("risk", {})
    else:
        final_cash = 0
        final_equity = account.get("total_equity", 0)
        open_positions = 0
        position_value = 0

    EXCLUDED = {"name", "type", "enabled", "symbol"}
    RISK_ORDER = [
        ("max_positions", "最大持仓数"),
        ("stop_loss_pct", "止损"),
        ("take_profit_pct", "止盈"),
        ("trailing_stop_pct", "移动止损"),
        ("position_size_pct", "仓位比例"),
    ]

    strategy_params_clean = {}
    if strategy_params:
        for k, v in strategy_params.items():
            if k not in EXCLUDED and k not in dict(RISK_ORDER):
                strategy_params_clean[k] = v

    risk_display = {}
    for key, label in RISK_ORDER:
        if key == "max_positions":
            val = max_positions
        else:
            val = risk_params.get(key) if risk_params.get(key) is not None else (strategy_params or {}).get(key, "-")
        if val is None:
            val = "-"
        risk_display[key] = (label, val)

    equity_sampled = _sample_data(equity_curve, 2000)
    eq_times = []
    eq_values = []
    for entry in equity_sampled:
        eq_times.append(_ts_to_str(entry["time"]))
        eq_values.append(round(entry["equity"], 2))

    all_trades = list(trades)
    all_trades.reverse()

    pnl_cls = "positive" if total_pnl_sum > 0 else "negative" if total_pnl_sum < 0 else "neutral"
    pnl_bg = _pos_color(total_pnl_sum)

    html = '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n'
    html += '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
    html += '<title>回测报告 — ' + str(config.get("strategies", ["未知"])[0]) + ' @ ' + str(config.get("symbol", "N/A")) + '</title>\n'
    html += '<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>\n'
    html += '<style>\n'
    html += '*{margin:0;padding:0;box-sizing:border-box}'
    html += 'body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f0f2f5;color:#333}'
    html += '.header{background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);color:#fff;padding:24px 32px}'
    html += '.header h1{font-size:22px;margin-bottom:6px}'
    html += '.header .subtitle{font-size:13px;opacity:0.7}'
    html += '.container{max-width:1400px;margin:0 auto;padding:20px 24px}'
    html += '.metrics-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:14px;margin-bottom:24px}'
    html += '.metric-card{background:#fff;border-radius:10px;padding:16px 18px;box-shadow:0 1px 4px rgba(0,0,0,.06)}'
    html += '.metric-card .label{font-size:12px;color:#888;margin-bottom:4px}'
    html += '.metric-card .value{font-size:22px;font-weight:700}'
    html += '.metric-card .value.positive{color:' + RED_UP + '}.metric-card .value.negative{color:' + GREEN_DOWN + '}.metric-card .value.neutral{color:' + NEUTRAL + '}'
    html += '.section-title{margin-bottom:10px;color:#555}'
    html += '.account-summary{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px;margin-bottom:24px}'
    html += '.account-card{color:#fff;border-radius:10px;padding:18px 20px}'
    html += '.account-card .label{font-size:12px;opacity:0.7;margin-bottom:4px}'
    html += '.account-card .value{font-size:24px;font-weight:700}'
    html += '.param-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:14px;margin-bottom:24px}'
    html += '.param-item{background:#fff;border-radius:8px;padding:12px 16px;box-shadow:0 1px 3px rgba(0,0,0,.05)}'
    html += '.param-label{font-size:11px;color:#888;margin-bottom:2px}'
    html += '.param-value{font-size:18px;font-weight:600;color:#555}'
    html += '.chart-box{background:#fff;border-radius:10px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.06);margin-bottom:24px}'
    html += '.chart-box h3{font-size:15px;margin-bottom:10px;color:#555}'
    html += '.chart{width:100%;height:450px}'
    html += '.table-box{background:#fff;border-radius:10px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.06);margin-bottom:24px;overflow-x:auto;max-height:600px;overflow-y:auto}'
    html += '.table-box h3{font-size:15px;margin-bottom:10px;color:#555}'
    html += 'table{width:100%;border-collapse:collapse;font-size:13px}'
    html += 'thead{position:sticky;top:0;z-index:1}'
    html += 'th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #eee}'
    html += 'th{background:#fafafa;font-weight:600;color:#555;white-space:nowrap}'
    html += 'tr:hover{background:#f8f9ff}'
    html += 'tr.summary-row{background:#fff3cd!important;font-weight:700;border-bottom:2px solid #ffc107}'
    html += 'tr.summary-row:hover{background:#fff3cd!important}'
    html += '.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}'
    html += '.badge-buy{background:#d4edda;color:#155724}.badge-sell{background:#f8d7da;color:#721c24}'
    html += '.badge-open{background:#cce5ff;color:#004085}.badge-close{background:#e2d9f3;color:#5a3e85}'
    html += '.footer{text-align:center;font-size:12px;color:#aaa;padding:20px 0}'
    html += '</style>\n</head>\n<body>\n'

    html += '<div class="header">\n<h1>📊 回测报告</h1>\n<div class="subtitle">\n'
    html += '策略: ' + str(config.get("strategies", ["未知"])[0]) + ' &nbsp;|&nbsp;'
    html += '品种: ' + str(config.get("symbol", "N/A")).upper() + ' &nbsp;|&nbsp;'
    html += '周期: ' + str(config.get("interval", "N/A")) + ' &nbsp;|&nbsp;'
    html += '数据量: ' + f'{config.get("data_count", 0):,}' + ' 根K线 &nbsp;|&nbsp;'
    html += '时间: ' + _ts_to_str(start_time) + ' ~ ' + _ts_to_str(end_time) + '\n'
    html += '</div>\n</div>\n\n<div class="container">\n'

    html += '<div class="metrics-grid">\n'

    def metric(name, val, cls):
        return f'<div class="metric-card"><div class="label">{name}</div><div class="value {cls}">{val}</div></div>\n'

    html += metric("初始资金", f"{initial_capital:,.2f}", "neutral")
    html += metric("交易盈亏", f"{total_pnl_sum:+,.2f}", pnl_cls)
    html += metric("总手续费", f"{summary.get('total_fee', 0):.2f}", "neutral")
    net_cls = "positive" if summary.get("net_profit", 0) > 0 else "negative" if summary.get("net_profit", 0) < 0 else "neutral"
    html += metric("净盈亏", f"{summary.get('net_profit', 0):+.2f}", net_cls)
    html += metric("总交易次数", str(summary.get("total_trades", 0)), "neutral")
    html += metric("盈利交易", str(summary.get("winning_trades", 0)), "positive")
    html += metric("平均盈利", f"{summary.get('avg_win', 0):.2f}", "positive")
    html += metric("亏损交易", str(summary.get("losing_trades", 0)), "negative")
    html += metric("平均亏损", f"{summary.get('avg_loss', 0):.2f}", "negative")
    wr_cls = "positive" if summary.get("win_rate", 0) > 50 else "negative"
    html += metric("胜率", f"{summary.get('win_rate', 0):.2f}%", wr_cls)
    html += metric("盈亏比", f"{summary.get('profit_factor', 0):.2f}", "neutral")
    html += metric("最大回撤", f"{summary.get('max_drawdown', 0):.2f}%", "negative")
    r_cls = "positive" if summary.get("total_return_pct", 0) > 0 else "negative" if summary.get("total_return_pct", 0) < 0 else "neutral"
    html += metric("总收益率", f"{summary.get('total_return_pct', 0):+.2f}%", r_cls)
    sr_cls = "positive" if summary.get("sharpe_ratio", 0) > 0 else "negative"
    html += metric("夏普比率", f"{summary.get('sharpe_ratio', 0):.2f}", sr_cls)
    html += '</div>\n\n'

    html += '<h3 class="section-title">📋 回测结束时账户状态</h3>\n<div class="account-summary">\n'
    html += f'<div class="account-card" style="background:linear-gradient(135deg,#1a1a2e,#2d3436);"><div class="label">💰 现金余额</div><div class="value">{final_cash:,.2f} USDT</div></div>\n'
    html += f'<div class="account-card" style="background:linear-gradient(135deg,#1a1a2e,#2d3436);"><div class="label">📦 持仓数量</div><div class="value">{open_positions} 个</div></div>\n'
    html += f'<div class="account-card" style="background:linear-gradient(135deg,#1a1a2e,#2d3436);"><div class="label">📊 持仓市值</div><div class="value">{position_value:,.2f} USDT</div></div>\n'
    html += '</div>\n\n'

    html += '<h3 class="section-title">⚙️ 策略参数</h3>\n<div class="param-grid">\n'
    for k, v in strategy_params_clean.items():
        label = k.replace("_", " ").title()
        if isinstance(v, float) and k.endswith("_pct"):
            val_str = f"{v}%"
        elif isinstance(v, float):
            val_str = f"{v:g}"
        elif isinstance(v, int) and abs(v) >= 1000:
            val_str = f"{v:,}"
        else:
            val_str = str(v)
        html += f'<div class="param-item"><div class="param-label">{label}</div><div class="param-value">{val_str}</div></div>\n'
    for key, label in RISK_ORDER:
        val = risk_display[key][1]
        val_str = f"{val}%" if isinstance(val, (int, float)) and key.endswith("_pct") else str(val)
        html += f'<div class="param-item"><div class="param-label">{label}</div><div class="param-value">{val_str}</div></div>\n'
    html += '</div>\n\n'

    html += '<div class="chart-box">\n<h3>📈 权益曲线 (Equity Curve)</h3>\n<div class="chart" id="equityChart"></div>\n</div>\n\n'

    # ---- 全部交易记录（含现金余额列） ----
    html += '<div class="table-box">\n'
    html += f'<h3>📋 全部交易记录 (共 {len(trades)} 笔)</h3>\n'
    html += '<table>\n<thead>\n<tr>\n'
    html += '<th>类型</th><th>方向</th><th>价格</th><th>数量</th><th>手续费</th><th>盈亏</th><th>现金余额</th><th>时间</th><th>原因</th>\n'
    html += '</tr>\n</thead>\n<tbody>\n'
    html += f'<tr class="summary-row"><td colspan="4" style="text-align:right;">📊 合计</td>'
    html += f'<td style="white-space:nowrap;">{total_fee_sum:,.4f}</td>'
    html += f'<td style="white-space:nowrap;"><span style="font-size:13px;font-weight:700;color:{pnl_bg}">{total_pnl_sum:+,.2f}</span></td>'
    html += '<td></td><td></td><td></td></tr>\n'

    for t in all_trades:
        t_type = t.get("type", "")
        t_side = t.get("side", "")
        type_badge = "badge-open" if t_type == "OPEN" else "badge-close" if t_type == "CLOSE" else ""
        side_badge = "badge-buy" if t_side == "BUY" else "badge-sell" if t_side == "SELL" else ""
        if t_type == "CLOSE" and "pnl" in t:
            pnl = t.get("pnl", 0)
            pnl_pct = t.get("pnl_pct", 0)
            pnl_c = _pos_color(pnl)
            pnl_text = f'<span style="font-size:13px;font-weight:600;color:{pnl_c}">{pnl:+.2f} ({pnl_pct:+.2f}%)</span>'
        else:
            pnl_text = '<span style="color:#aaa;">-</span>'

        # 直接从交易记录读取现金余额
        cash_val = t.get("cash")
        if isinstance(cash_val, (int, float)):
            cash_display = f"{cash_val:,.2f}"
        else:
            cash_display = "-"

        html += f'<tr>'
        html += f'<td><span class="badge {type_badge}">{t_type}</span></td>'
        html += f'<td><span class="badge {side_badge}">{t_side}</span></td>'
        html += f'<td>{t.get("price", 0):.4f}</td>'
        html += f'<td>{t.get("quantity", 0):.6f}</td>'
        html += f'<td>{t.get("fee", 0):.4f}</td>'
        html += f'<td style="white-space:nowrap;">{pnl_text}</td>'
        html += f'<td style="white-space:nowrap;">{cash_display}</td>'
        html += f'<td>{_ts_to_str(t.get("time", 0))}</td>'
        html += f'<td style="font-size:11px;color:#888;">{str(t.get("reason","-"))[:60]}</td>'
        html += '</tr>\n'

    html += '</tbody>\n</table>\n</div>\n\n</div>\n\n'

    html += '<div class="footer">\n生成时间: ' + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + ' | 回测系统 Report\n</div>\n\n'

    html += '<script>\n(function(){\n'
    html += 'var chart=echarts.init(document.getElementById("equityChart"));\n'
    html += 'var rawTimes=' + json.dumps(eq_times) + ';\n'
    html += 'var rawValues=' + json.dumps(eq_values) + ';\n'
    html += 'var ic=' + json.dumps(initial_capital) + ';\n'
    html += 'chart.setOption({tooltip:{trigger:"axis",formatter:function(p){return p[0].axisValue+"<br/>权益: $"+p[0].value.toLocaleString()}},'
    html += 'grid:{left:60,right:20,top:20,bottom:40},'
    html += 'xAxis:{type:"category",data:rawTimes,axisLabel:{show:true,interval:Math.floor(rawTimes.length/10)||1,rotate:30,fontSize:10},boundaryGap:false},'
    html += 'yAxis:{type:"value",axisLabel:{formatter:"${value}"},splitLine:{lineStyle:{color:"#eee"}}},'
    html += 'dataZoom:[{type:"slider",start:0,end:100,height:20,bottom:0},{type:"inside",start:0,end:100}],'
    html += 'series:[{name:"权益",type:"line",data:rawValues,smooth:true,symbol:"none",'
    html += 'lineStyle:{color:"#0984e3",width:1.5},'
    html += 'areaStyle:{color:new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:"rgba(9,132,227,0.2)"},{offset:1,color:"rgba(9,132,227,0.02)"}])},'
    html += 'markLine:{silent:true,data:[{yAxis:ic,label:{formatter:"初始 $"+ic},lineStyle:{color:"#b2bec3",type:"dashed"}}]}}]});\n'
    html += 'window.addEventListener("resize",function(){chart.resize();});\n'
    html += '})();\n</script>\n\n</body>\n</html>'

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ HTML 报告已生成: {output_path}")
    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python utils/report_html.py <回测JSON文件路径> [输出HTML路径]")
        sys.exit(1)
    json_file = sys.argv[1]
    out_file = sys.argv[2] if len(sys.argv) > 2 else None
    json_to_html(json_file, out_file)