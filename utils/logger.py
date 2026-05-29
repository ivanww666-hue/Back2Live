# -*- coding: utf-8 -*-
"""日志配置模块 — 统一管理日志格式、输出和轮转策略。"""

import os
import sys
import logging
from logging.handlers import TimedRotatingFileHandler


class _SignalFilter(logging.Filter):
    """只允许信号/订单相关的 logger 通过"""
    _ALLOWED = {"core.live_engine", "core.binance_broker"}

    def filter(self, record: logging.LogRecord) -> bool:
        for prefix in self._ALLOWED:
            if record.name == prefix or record.name.startswith(prefix + "."):
                return True
        return False


def _log_namer(default_name: str) -> str:
    """轮转文件命名: live.log.2026-05-28 → live_20260528.log"""
    from datetime import datetime as _dt
    base = default_name.rsplit(".", 1)[0]  # live.log
    if base.endswith(".log"):
        base = base[:-4]  # live
    return default_name.replace(
        base + ".log",
        f"{base}_{_dt.now().strftime('%Y%m%d')}.log",
    )


def init_logging(log_dir: str = "logs",
                 log_name: str = "live.log",
                 log_level: int = logging.INFO,
                 backup_count: int = 30):
    """
    初始化日志配置。

    Args:
        log_dir: 日志目录
        log_name: 日志文件名
        log_level: 日志级别
        backup_count: 保留历史日志天数
    """
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, log_name)

    # suppress duplicate handlers on re-import
    root = logging.getLogger()
    if root.handlers:
        return

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1. 终端输出
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(log_level)

    # 2. 全量日志文件
    file_handler = TimedRotatingFileHandler(
        log_file, when="midnight", interval=1,
        backupCount=backup_count, encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(log_level)
    file_handler.namer = _log_namer

    # 3. 信号/订单专用日志文件
    signal_file = os.path.join(log_dir, "signal.log")
    signal_handler = TimedRotatingFileHandler(
        signal_file, when="midnight", interval=1,
        backupCount=backup_count, encoding="utf-8",
    )
    signal_handler.setFormatter(formatter)
    signal_handler.setLevel(logging.INFO)
    signal_handler.namer = _log_namer
    signal_handler.addFilter(_SignalFilter())

    root.setLevel(log_level)
    root.addHandler(stream_handler)
    root.addHandler(file_handler)
    root.addHandler(signal_handler)
