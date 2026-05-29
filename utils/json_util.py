# -*- coding: utf-8 -*-
"""JSON 配置工具 — 支持带 // 注释的 JSON 文件读取。"""

import json
import os
import re


def _expand_env_vars(value):
    """Recursively expand environment placeholders in loaded config values."""
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    if isinstance(value, str):
        full_placeholder = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", value)
        if full_placeholder:
            return os.environ.get(full_placeholder.group(1), "")
        return os.path.expandvars(value)
    return value


def load_json_with_comments(filepath: str) -> dict:
    """
    读取支持 // 单行注释的 JSON 文件。

    注释格式:  // 这是注释内容

    Args:
        filepath: JSON 文件路径

    Returns:
        解析后的字典
    """
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    # 移除 // 单行注释（不在字符串内的）
    cleaned = re.sub(r'(?m)\s*//.*$', '', text)
    # 移除尾随逗号（JSON 标准不允许）
    cleaned = re.sub(r',\s*(\n\s*[}\]])', r'\1', cleaned)

    return _expand_env_vars(json.loads(cleaned))


def save_json(filepath: str, data: dict, indent: int = 4):
    """
    保存 JSON 文件，带缩进和中文支持。

    Args:
        filepath: JSON 文件路径
        data: 字典数据
        indent: 缩进空格数
    """
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
