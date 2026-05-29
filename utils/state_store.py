# -*- coding: utf-8 -*-
"""Redis 状态持久化层
========================

在 Redis 中保存实盘引擎的完整运行时状态，实现：
- 引擎重启时快速恢复（无需重新调用 Binance API）
- 挂单、持仓、资金、权益曲线均可恢复
- Redis 不可用时降级为无状态模式（不阻塞引擎启动）

Redis Key 设计:
    backtest:live:account:{strategy}:{symbol}  → JSON — 单个 portfolio 快照
    backtest:live:orders:{strategy}:{symbol}   → JSON — pending 订单列表
    backtest:live:equity:{strategy}:{symbol}   → JSON — 最近 N 条权益记录
    backtest:live:meta                         → JSON — 启动配置（interval, strategies 等）
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Redis 可选依赖
try:
    import redis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False
    redis = None  # type: ignore


class StateStore:
    """Redis-backed state persistence for the live trading engine."""

    KEY_PREFIX = "backtest:live"

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        ttl_seconds: int = 86400 * 7,  # 默认 7 天过期
        equity_max_len: int = 2000,
    ):
        self._host = host
        self._port = port
        self._db = db
        self._password = password
        self._ttl = ttl_seconds
        self._equity_max_len = equity_max_len
        self._client: Optional["redis.Redis"] = None

    # ── 连接管理 ─────────────────────────────────────────

    @property
    def client(self) -> Optional["redis.Redis"]:
        if self._client is not None:
            return self._client
        if not HAS_REDIS:
            logger.warning("redis 未安装，状态持久化已禁用")
            return None
        try:
            self._client = redis.Redis(
                host=self._host,
                port=self._port,
                db=self._db,
                password=self._password,
                socket_connect_timeout=3,
                socket_timeout=3,
                decode_responses=True,
            )
            self._client.ping()
            logger.info("Redis connected: %s:%d db=%d", self._host, self._port, self._db)
        except Exception as exc:
            logger.error("Redis 连接失败: %s，状态持久化已禁用", exc)
            self._client = None
        return self._client

    @property
    def available(self) -> bool:
        return self.client is not None

    def disconnect(self):
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    # ── Key 构建 ─────────────────────────────────────────

    def _account_key(self, strategy_name: str, symbol: str) -> str:
        return f"{self.KEY_PREFIX}:account:{strategy_name}:{symbol}"

    def _orders_key(self, strategy_name: str, symbol: str) -> str:
        return f"{self.KEY_PREFIX}:orders:{strategy_name}:{symbol}"

    def _equity_key(self, strategy_name: str, symbol: str) -> str:
        return f"{self.KEY_PREFIX}:equity:{strategy_name}:{symbol}"

    def _meta_key(self) -> str:
        return f"{self.KEY_PREFIX}:meta"

    # ── 保存 ────────────────────────────────────────────

    def save_portfolio(self, strategy_name: str, symbol: str, data: dict):
        """保存单个 portfolio 状态到 Redis。"""
        c = self.client
        if not c:
            return
        key = self._account_key(strategy_name, symbol)
        c.set(key, json.dumps(data, ensure_ascii=False))
        if self._ttl > 0:
            c.expire(key, self._ttl)

    def save_pending_orders(self, strategy_name: str, symbol: str, orders: List[dict]):
        """保存挂单列表到 Redis。"""
        c = self.client
        if not c:
            return
        key = self._orders_key(strategy_name, symbol)
        if orders:
            c.set(key, json.dumps(orders, ensure_ascii=False))
        else:
            c.delete(key)  # 无挂单时清理 key
        if self._ttl > 0:
            c.expire(key, self._ttl)

    def append_equity(self, strategy_name: str, symbol: str, entry: dict):
        """追加一条权益记录（最近 N 条）。"""
        c = self.client
        if not c:
            return
        key = self._equity_key(strategy_name, symbol)
        payload = json.dumps(entry, ensure_ascii=False)
        c.rpush(key, payload)
        # 裁剪到最近 N 条
        c.ltrim(key, -self._equity_max_len, -1)
        if self._ttl > 0:
            c.expire(key, self._ttl)

    def save_meta(self, meta: dict):
        """保存引擎元信息。"""
        c = self.client
        if not c:
            return
        c.set(self._meta_key(), json.dumps(meta, ensure_ascii=False))
        if self._ttl > 0:
            c.expire(self._meta_key(), self._ttl)

    # ── 加载 ────────────────────────────────────────────

    def load_portfolio(self, strategy_name: str, symbol: str) -> Optional[dict]:
        """加载单个 portfolio 状态。"""
        c = self.client
        if not c:
            return None
        raw = c.get(self._account_key(strategy_name, symbol))
        if raw:
            return json.loads(raw)
        return None

    def load_pending_orders(self, strategy_name: str, symbol: str) -> List[dict]:
        """加载挂单列表。"""
        c = self.client
        if not c:
            return []
        raw = c.get(self._orders_key(strategy_name, symbol))
        if raw:
            return json.loads(raw)
        return []

    def load_equity(self, strategy_name: str, symbol: str) -> List[dict]:
        """加载权益曲线。"""
        c = self.client
        if not c:
            return []
        raw = c.lrange(self._equity_key(strategy_name, symbol), 0, -1)
        return [json.loads(item) for item in raw]

    def load_meta(self) -> Optional[dict]:
        """加载引擎元信息。"""
        c = self.client
        if not c:
            return None
        raw = c.get(self._meta_key())
        if raw:
            return json.loads(raw)
        return None

    # ── 批量保存 ────────────────────────────────────────

    def save_all(
        self,
        strategy_name: str,
        symbol: str,
        portfolio_data: dict,
        pending_orders: List[dict],
        equity_entries: Optional[List[dict]] = None,
    ):
        """一次性保存 portfolio + pending orders + equity。"""
        self.save_portfolio(strategy_name, symbol, portfolio_data)
        self.save_pending_orders(strategy_name, symbol, pending_orders)
        if equity_entries:
            for entry in equity_entries:
                self.append_equity(strategy_name, symbol, entry)

    # ── 清理 ────────────────────────────────────────────

    def clear_all(self, strategy_name: str, symbol: str):
        """清除某个 portfolio 的所有 Redis 数据。"""
        c = self.client
        if not c:
            return
        c.delete(
            self._account_key(strategy_name, symbol),
            self._orders_key(strategy_name, symbol),
            self._equity_key(strategy_name, symbol),
        )

    def clear_everything(self):
        """清除所有 backtest:live:* 前缀的 Redis 数据。"""
        c = self.client
        if not c:
            return
        keys = c.keys(f"{self.KEY_PREFIX}:*")
        if keys:
            c.delete(*keys)