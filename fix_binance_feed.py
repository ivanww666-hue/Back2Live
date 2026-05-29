# -*- coding: utf-8 -*-
"""Fix the truncated binance_feed.py file by appending the remaining content."""
import sys
sys.stdout.reconfigure(encoding='utf-8')

content = r'''
        "kline_1d":       saver.on_kline,
        "miniTicker":     saver.on_mini_ticker,
        "ticker":         saver.on_ticker,
        "bookTicker":     saver.on_book_ticker,
        "avgPrice":       saver.on_avg_price,
        "depth":          saver.on_depth,
        "depth_100ms":    saver.on_depth,
        "depth5":         saver.on_partial_depth,
        "depth20":        saver.on_partial_depth,
        "ticker_1h":      saver.on_window_ticker,
        "ticker_1d":      saver.on_window_ticker,
        "referencePrice": saver.on_ref_price,
    }

    socket_type = SOCKET_MAP.get(market_key)
    callback = callback_map.get(market_key)
    return (socket_type, callback) if socket_type and callback else (None, None)


def get_all_streams_and_callbacks(symbol: str, saver: MarketSaver):
    """获取所有行情类型的 streams 和 callbacks"""
    streams = []
    callbacks = {}

    items = [
        ("trade", saver.on_trade),
        ("aggTrade", saver.on_agg_trade),
        ("kline_1m", saver.on_kline),
        ("miniTicker", saver.on_mini_ticker),
        ("ticker", saver.on_ticker),
        ("bookTicker", saver.on_book_ticker),
        ("avgPrice", saver.on_avg_price),
        ("depth", saver.on_depth),
        ("depth5", saver.on_partial_depth),
        ("ticker_1h", saver.on_window_ticker),
        ("referencePrice", saver.on_ref_price),
    ]

    for key, cb in items:
        stream, _ = get_stream_and_callback(symbol, key, saver)
        if stream:
            streams.append(stream)
            callbacks[stream] = cb

    return streams, callbacks


# ============================================================
# 便捷函数: 运行实时行情显示
# ============================================================

def fmt_time(ts: int) -> str:
    """格式化时间戳"""
    return datetime.fromtimestamp(ts / 1000).strftime("%H:%M:%S.%f")[:-3]


def fmt_price(val: str) -> str:
    """格式化价格"""
    f = float(val)
    if f >= 1000:
        return f"{f:.2f}"
    elif f >= 1:
        return f"{f:.4f}"
    else:
        return f"{f:.8f}"


def fmt_qty(val: str) -> str:
    """格式化数量"""
    f = float(val)
    if f >= 1000:
        return f"{f:.2f}"
    elif f >= 1:
        return f"{f:.4f}"
    elif f >= 0.001:
        return f"{f:.6f}"
    else:
        return f"{f:.8f}"


class MarketPrinter:
    """行情数据格式化打印"""

    @staticmethod
    def on_trade(msg):
        sp(f"[{fmt_time(msg['T'])}] [{msg['s']}] 逐笔交易 #{msg['t']} "
           f"价格:{fmt_price(msg['p'])} 数量:{fmt_qty(msg['q'])} "
           f"{'🟢买入' if not msg['m'] else '🔴卖出'}")

    @staticmethod
    def on_agg_trade(msg):
        sp(f"[{fmt_time(msg['T'])}] [{msg['s']}] 归集交易 #{msg['a']} "
           f"价格:{fmt_price(msg['p'])} 数量:{fmt_qty(msg['q'])} "
           f"{'🟢买入' if not msg['m'] else '🔴卖出'}")

    @staticmethod
    def on_kline(msg):
        k = msg["k"]
        status = "✅" if k["x"] else "⏳"
        sp(f"[{fmt_time(msg['E'])}] [{k['s']}] K线({k['i']}) {status} "
           f"开:{fmt_price(k['o'])} 高:{fmt_price(k['h'])} "
           f"低:{fmt_price(k['l'])} 收:{fmt_price(k['c'])} "
           f"量:{fmt_qty(k['v'])} 笔:{k['n']}")

    @staticmethod
    def on_mini_ticker(msg):
        change = ((float(msg['c']) - float(msg['o'])) / float(msg['o']) * 100
                  if float(msg['o']) > 0 else 0)
        arrow = "🟢" if change >= 0 else "🔴"
        sp(f"[{fmt_time(msg['E'])}] [{msg['s']}] 精简Ticker "
           f"最新:{fmt_price(msg['c'])} "
           f"24h高:{fmt_price(msg['h'])} 24h低:{fmt_price(msg['l'])} "
           f"24h涨幅:{change:+.2f}% {arrow}")

    @staticmethod
    def on_ticker(msg):
        sp(f"[{fmt_time(msg['E'])}] [{msg['s']}] 完整Ticker "
           f"最新:{fmt_price(msg['c'])} "
           f"涨跌:{msg['p']} 涨幅:{msg['P']}% "
           f"最高:{fmt_price(msg['h'])} 最低:{fmt_price(msg['l'])} "
           f"成交量:{fmt_qty(msg['v'])} 成交额:{fmt_qty(msg['q'])}")

    @staticmethod
    def on_book_ticker(msg):
        spread = float(msg['a']) - float(msg['b'])
        spread_pct = spread / float(msg['b']) * 100 if float(msg['b']) > 0 else 0
        sp(f"[{msg['s']}] 最优挂单 "
           f"买一:{fmt_price(msg['b'])} ({fmt_qty(msg['B'])}) "
           f"卖一:{fmt_price(msg['a'])} ({fmt_qty(msg['A'])}) "
           f"价差:{fmt_price(str(spread))} ({spread_pct:.4f}%)")

    @staticmethod
    def on_avg_price(msg):
        sp(f"[{fmt_time(msg['E'])}] [{msg['s']}] 平均价格({msg['i']}) "
           f"{fmt_price(msg['w'])}")

    @staticmethod
    def on_depth(msg):
        bids = msg.get("b", [])
        asks = msg.get("a", [])
        best_bid = float(bids[0][0]) if bids else 0
        best_ask = float(asks[0][0]) if asks else 0
        spread = best_ask - best_bid
        spread_pct = spread / best_bid * 100 if best_bid > 0 else 0
        sp(f"[{fmt_time(msg['E'])}] [{msg['s']}] 深度更新 "
           f"买一:{fmt_price(str(best_bid))} 卖一:{fmt_price(str(best_ask))} "
           f"价差:{fmt_price(str(spread))} ({spread_pct:.4f}%) "
           f"变动:买{len(bids)}档 卖{len(asks)}档 "
           f"更新ID:{msg['u']}")

    @staticmethod
    def on_partial_depth(msg):
        bids = msg.get("bids", [])
        asks = msg.get("asks", [])
        best_bid = float(bids[0][0]) if bids else 0
        best_ask = float(asks[0][0]) if asks else 0
        spread = best_ask - best_bid
        sp(f"[有限深度] 买一:{fmt_price(str(best_bid))} 卖一:{fmt_price(str(best_ask))} "
           f"价差:{fmt_price(str(spread))} "
           f"买{len(bids)}档 卖{len(asks)}档 "
           f"更新ID:{msg.get('lastUpdateId', '')}")

    @staticmethod
    def on_window_ticker(msg):
        sp(f"[{fmt_time(msg['E'])}] [{msg['s']}] 滚动统计({msg['e']}) "
           f"最新:{fmt_price(msg['c'])} 涨幅:{msg['P']}% "
           f"最高:{fmt_price(msg['h'])} 最低:{fmt_price(msg['l'])}")

    @staticmethod
    def on_ref_price(msg):
        sp(f"[{fmt_time(msg['t'])}] [{msg['s']}] 参考价格 "
           f"{msg.get('r', '无')}")

    @staticmethod
    def on_all_mini_ticker(msg):
        if isinstance(msg, list):
            sp(f"\n=== 全市场行情 ({len(msg)}个交易对) ===")
            sorted_data = sorted(
                [t for t in msg if isinstance(t, dict)],
                key=lambda x: float(x.get('q', '0') or '0'),
                reverse=True
            )
            for t in sorted_data[:20]:
                symbol = t.get('s', '')
                price = t.get('c', '')
                vol = t.get('q', '')
                sp(f"  {symbol:12s} 最新:{fmt_price(price):>12s}  成交额:{fmt_qty(vol):>12s}")
            sp(f"================================")


# 全局 print 别名
sp = print


def run_market(symbol: str, market_key: str, duration: int = 30):
    """
    运行实时行情显示

    Args:
        symbol: 交易对 (如 "btcusdt")
        market_key: 行情类型 key
        duration: 运行时长(秒)
    """
    symbol_upper = symbol.upper()

    sp(f"\n{'='*60}")
    sp(f"  币安行情 - {symbol_upper}")
    sp(f"{'='*60}")

    twm = ThreadedWebsocketManager()
    twm.start()

    try:
        if market_key == "all_market":
            sp(f"  订阅: 全市场行情")
            sp(f"  运行: {duration}秒")
            sp(f"{'='*60}\n")
            twm.start_miniticker_socket(callback=MarketPrinter.on_all_mini_ticker)

        elif market_key == "all":
            market_name = "全部行情"
            sp(f"  订阅: {market_name}")
            sp(f"  交易对: {symbol_upper}")
            sp(f"  运行: {duration}秒")
            sp(f"  Ctrl+C 提前结束")
            sp(f"{'='*60}\n")

            for key in ["trade", "aggTrade", "kline_1m", "miniTicker", "ticker",
                        "bookTicker", "avgPrice", "depth", "depth5",
                        "ticker_1h", "referencePrice"]:
                socket_name = SOCKET_MAP.get(key)
                callback = CALLBACK_MAP.get(key)
                if socket_name and callback:
                    twm.start_symbol_ticker_socket(
                        callback=callback,
                        symbol=symbol_upper,
                    ) if key == "ticker" else \
                    twm.start_symbol_miniticker_socket(
                        callback=callback,
                        symbol=symbol_upper,
                    ) if key == "miniTicker" else \
                    twm.start_kline_socket(
                        callback=callback,
                        symbol=symbol_upper,
                        interval=BinanceClient.KLINE_INTERVAL_1MINUTE,
                    ) if key == "kline_1m" else \
                    twm.start_trade_socket(
                        callback=callback,
                        symbol=symbol_upper,
                    ) if key == "trade" else \
                    twm.start_aggtrade_socket(
                        callback=callback,
                        symbol=symbol_upper,
                    ) if key == "aggTrade" else \
                    twm.start_book_ticker_socket(
                        callback=callback,
                        symbol=symbol_upper,
                    ) if key == "bookTicker" else \
                    twm.start_depth_socket(
                        callback=callback,
                        symbol=symbol_upper,
                    ) if key == "depth" else \
                    twm.start_depth_socket(
                        callback=callback,
                        symbol=symbol_upper,
                        depth=BinanceClient.DEPTH_5,
                    ) if key == "depth5" else \
                    twm.start_symbol_ticker_socket(
                        callback=callback,
                        symbol=symbol_upper,
                    )

        else:
            socket_name = SOCKET_MAP.get(market_key)
            callback = CALLBACK_MAP.get(market_key)
            if not socket_name or not callback:
                sp(f"❌ 不支持的行情类型: {market_key}")
                return

            market_name = MARKET_TYPES.get(
                next((k for k, v in MARKET_TYPES.items() if v["key"] == market_key), ""),
                {}
            ).get("name", market_key)

            sp(f"  订阅: {market_name}")
            sp(f"  交易对: {symbol_upper}")
            sp(f"  运行: {duration}秒")
            sp(f"  Ctrl+C 提前结束")
            sp(f"{'='*60}\n")

            if market_key == "trade":
                twm.start_trade_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "aggTrade":
                twm.start_aggtrade_socket(callback=callback, symbol=symbol_upper)
            elif market_key.startswith("kline_"):
                interval_map = {
                    "kline_1m": BinanceClient.KLINE_INTERVAL_1MINUTE,
                    "kline_5m": BinanceClient.KLINE_INTERVAL_5MINUTE,
                    "kline_1h": BinanceClient.KLINE_INTERVAL_1HOUR,
                    "kline_1d": BinanceClient.KLINE_INTERVAL_1DAY,
                }
                twm.start_kline_socket(
                    callback=callback,
                    symbol=symbol_upper,
                    interval=interval_map.get(market_key, BinanceClient.KLINE_INTERVAL_1MINUTE),
                )
            elif market_key == "miniTicker":
                twm.start_symbol_miniticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "ticker":
                twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "bookTicker":
                twm.start_book_ticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "avgPrice":
                twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "depth":
                twm.start_depth_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "depth_100ms":
                twm.start_depth_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "depth5":
                twm.start_depth_socket(callback=callback, symbol=symbol_upper, depth=BinanceClient.DEPTH_5)
            elif market_key == "depth20":
                twm.start_depth_socket(callback=callback, symbol=symbol_upper, depth=BinanceClient.DEPTH_20)
            elif market_key in ("ticker_1h", "ticker_1d"):
                twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "referencePrice":
                twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)

        time.sleep(duration)

    except KeyboardInterrupt:
        sp("\n\n⏹ 用户中断")
    finally:
        twm.stop()
        sp(f"\n{'='*60}")
        sp(f"  行情获取结束")
        sp(f"{'='*60}")


# 回调映射 (用于 run_market)
CALLBACK_MAP = {
    "trade":          MarketPrinter.on_trade,
    "aggTrade":       MarketPrinter.on_agg_trade,
    "kline_1m":       MarketPrinter.on_kline,
    "kline_5m":       MarketPrinter.on_kline,
    "kline_1h":       MarketPrinter.on_kline,
    "kline_1d":       MarketPrinter.on_kline,
    "miniTicker":     MarketPrinter.on_mini_ticker,
    "ticker":         MarketPrinter.on_ticker,
    "bookTicker":     MarketPrinter.on_book_ticker,
    "avgPrice":       MarketPrinter.on_avg_price,
    "depth":          MarketPrinter.on_depth,
    "depth_100ms":    MarketPrinter.on_depth,
    "depth5":         MarketPrinter.on_partial_depth,
    "depth20":        MarketPrinter.on_partial_depth,
    "ticker_1h":      MarketPrinter.on_window_ticker,
    "ticker_1d":      MarketPrinter.on_window_ticker,
    "referencePrice": MarketPrinter.on_ref_price,
}


def run_saver(symbol: str, market_key: str, duration: int = 30):
    """
    运行行情数据持久化

    Args:
        symbol: 交易对 (如 "btcusdt")
        market_key: 行情类型 key
        duration: 运行时长(秒)
    """
    symbol_upper = symbol.upper()
    saver = MarketSaver()

    print(f"\n{'='*60}")
    print(f"  币安行情持久化 - {symbol_upper}")
    print(f"{'='*60}")

    twm = ThreadedWebsocketManager()
    twm.start()

    try:
        if market_key == "all_market":
            print(f"  订阅: 全市场行情")
            print(f"  运行: {duration}秒")
            print(f"{'='*60}\n")
            twm.start_miniticker_socket(callback=saver.on_all_mini_ticker)

        elif market_key == "all":
            print(f"  订阅: 全部行情")
            print(f"  交易对: {symbol_upper}")
            print(f"  运行: {duration}秒")
            print(f"  Ctrl+C 提前结束")
            print(f"{'='*60}\n")

            for key in ["trade", "aggTrade", "kline_1m", "miniTicker", "ticker",
                        "bookTicker", "avgPrice", "depth", "depth5",
                        "ticker_1h", "referencePrice"]:
                socket_type, callback = get_stream_and_callback(symbol, key, saver)
                if socket_type and callback:
                    if key == "trade":
                        twm.start_trade_socket(callback=callback, symbol=symbol_upper)
                    elif key == "aggTrade":
                        twm.start_aggtrade_socket(callback=callback, symbol=symbol_upper)
                    elif key == "kline_1m":
                        twm.start_kline_socket(callback=callback, symbol=symbol_upper, interval=BinanceClient.KLINE_INTERVAL_1MINUTE)
                    elif key == "miniTicker":
                        twm.start_symbol_miniticker_socket(callback=callback, symbol=symbol_upper)
                    elif key == "ticker":
                        twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)
                    elif key == "bookTicker":
                        twm.start_book_ticker_socket(callback=callback, symbol=symbol_upper)
                    elif key == "avgPrice":
                        twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)
                    elif key == "depth":
                        twm.start_depth_socket(callback=callback, symbol=symbol_upper)
                    elif key == "depth5":
                        twm.start_depth_socket(callback=callback, symbol=symbol_upper, depth=BinanceClient.DEPTH_5)
                    elif key == "ticker_1h":
                        twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)
                    elif key == "referencePrice":
                        twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)

        else:
            socket_type, callback = get_stream_and_callback(symbol, market_key, saver)
            if not socket_type or not callback:
                print(f"❌ 不支持的行情类型: {market_key}")
                return

            market_name = MARKET_TYPES.get(
                next((k for k, v in MARKET_TYPES.items() if v["key"] == market_key), ""),
                {}
            ).get("name", market_key)

            print(f"  订阅: {market_name}")
            print(f"  交易对: {symbol_upper}")
            print(f"  运行: {duration}秒")
            print(f"  Ctrl+C 提前结束")
            print(f"{'='*60}\n")

            if market_key == "trade":
                twm.start_trade_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "aggTrade":
                twm.start_aggtrade_socket(callback=callback, symbol=symbol_upper)
            elif market_key.startswith("kline_"):
                interval_map = {
                    "kline_1m": BinanceClient.KLINE_INTERVAL_1MINUTE,
                    "kline_5m": BinanceClient.KLINE_INTERVAL_5MINUTE,
                    "kline_1h": BinanceClient.KLINE_INTERVAL_1HOUR,
                    "kline_1d": BinanceClient.KLINE_INTERVAL_1DAY,
                }
                twm.start_kline_socket(
                    callback=callback,
                    symbol=symbol_upper,
                    interval=interval_map.get(market_key, BinanceClient.KLINE_INTERVAL_1MINUTE),
                )
            elif market_key == "miniTicker":
                twm.start_symbol_miniticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "ticker":
                twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "bookTicker":
                twm.start_book_ticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "avgPrice":
                twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "depth":
                twm.start_depth_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "depth_100ms":
                twm.start_depth_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "depth5":
                twm.start_depth_socket(callback=callback, symbol=symbol_upper, depth=BinanceClient.DEPTH_5)
            elif market_key == "depth20":
                twm.start_depth_socket(callback=callback, symbol=symbol_upper, depth=BinanceClient.DEPTH_20)
            elif market_key in ("ticker_1h", "ticker_1d"):
                twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)
            elif market_key == "referencePrice":
                twm.start_symbol_ticker_socket(callback=callback, symbol=symbol_upper)

        time.sleep(duration)

    except KeyboardInterrupt:
        print("\n\n⏹ 用户中断")
    finally:
        twm.stop()
        saver.close()
        print(f"\n{'='*60}")
        print(f"  行情持久化结束")
        print(f"{'='*60}")
'''

with open(r'e:\backtest\data\binance_feed.py', 'a', encoding='utf-8') as f:
    f.write(content)

print('Done appending remaining content to binance_feed.py')
