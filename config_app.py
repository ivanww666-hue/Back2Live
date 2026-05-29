# -*- coding: utf-8 -*-
"""
策略配置管理窗口应用
====================
基于 tkinter 的策略参数配置工具。

每个策略类型一个Tab页面，Tab内以表格形式展示该类型的所有策略，
支持新增、修改、删除操作，修改后保存到配置文件。
"""

import sys
import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from utils.json_util import load_json_with_comments, save_json

CURRENT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# 每种策略类型的元数据：参数名 → (显示标签, 类型, 最小值, 最大值)
_STRATEGY_META = {
    "ma_strategy": {
        "label": "趋势跟踪型 (MA)",
        "columns": [
            ("ema_fast", "EMA快", int, 1, 500),
            ("ema_medium", "EMA中", int, 1, 500),
            ("ema_slow", "EMA慢", int, 1, 500),
            ("ema_trend", "EMA长", int, 1, 500),
            ("macd_fast", "MACD快", int, 1, 100),
            ("macd_slow", "MACD慢", int, 1, 100),
            ("macd_signal", "MACD信", int, 1, 100),
            ("adx_period", "ADX周期", int, 1, 100),
            ("adx_threshold", "ADX阈值", int, 1, 100),
            ("super_atr_period", "SuperATR", int, 1, 100),
            ("super_multiplier", "Super×", float, 0.1, 10.0),
            ("stop_loss_pct", "止损%", float, 0.1, 50.0),
            ("take_profit_pct", "止盈%", float, 0.1, 100.0),
            ("trailing_stop_pct", "移动%", float, 0.1, 50.0),
            ("position_size_pct", "仓位%", float, 0.1, 100.0),
            ("max_positions", "最大持仓", int, 1, 100),
        ],
    },
    "dual_ma_strategy": {
        "label": "双均线交叉",
        "columns": [
            ("ema_fast", "EMA快", int, 1, 500),
            ("ema_slow", "EMA慢", int, 1, 500),
            ("stop_loss_pct", "止损%", float, 0.1, 50.0),
            ("take_profit_pct", "止盈%", float, 0.1, 100.0),
            ("trailing_stop_pct", "移动%", float, 0.1, 50.0),
            ("position_size_pct", "仓位%", float, 0.1, 100.0),
            ("max_positions", "最大持仓", int, 1, 100),
        ],
    },
    "triple_ma_strategy": {
        "label": "三均线趋势",
        "columns": [
            ("ema_fast", "EMA快", int, 1, 500),
            ("ema_medium", "EMA中", int, 1, 500),
            ("ema_slow", "EMA慢", int, 1, 500),
            ("stop_loss_pct", "止损%", float, 0.1, 50.0),
            ("take_profit_pct", "止盈%", float, 0.1, 100.0),
            ("trailing_stop_pct", "移动%", float, 0.1, 50.0),
            ("position_size_pct", "仓位%", float, 0.1, 100.0),
            ("max_positions", "最大持仓", int, 1, 100),
        ],
    },
    "grid_strategy": {
        "label": "网格策略",
        "columns": [
            ("grid_low", "下限", float, 1.0, 1000000.0),
            ("grid_high", "上限", float, 1.0, 1000000.0),
            ("grid_count", "网格数", int, 1, 200),
            ("position_per_grid_pct", "每格%", float, 0.1, 100.0),
            ("stop_loss_pct", "止损%", float, 0.1, 50.0),
            ("take_profit_pct", "止盈%", float, 0.1, 100.0),
            ("trailing_stop_pct", "移动%", float, 0.1, 50.0),
            ("max_positions", "最大持仓", int, 1, 200),
        ],
    },
    "slope_ma_strategy": {
        "label": "均线斜率策略",
        "columns": [
            ("ma_fast", "SMA快", int, 1, 500),
            ("ma_medium", "SMA中", int, 1, 500),
            ("ma_slow", "SMA慢", int, 1, 500),
            ("trail_drop", "MA5回落%", float, 0.1, 20.0),
            ("stop_loss", "止损%", float, 0.1, 50.0),
            ("take_profit", "止盈%", float, 0.1, 50.0),
        ],
    },
}


class StrategyEditDialog(tk.Toplevel):
    """策略编辑弹窗"""

    def __init__(self, parent, strategy_type: str, strategy_config: dict = None):
        super().__init__(parent)
        self.strategy_type = strategy_type
        self.result = None  # 编辑完成后返回的配置字典
        self.title(f"{'修改' if strategy_config else '新增'}策略 - {_STRATEGY_META[strategy_type]['label']}")
        self.geometry("500x650")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        if strategy_config is None:
            strategy_config = {}

        meta = _STRATEGY_META[strategy_type]
        columns = meta["columns"]

        # Canvas + scrollbar
        canvas = tk.Canvas(self, borderwidth=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        frame = ttk.Frame(canvas)
        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 通用字段
        common = ttk.LabelFrame(frame, text="基本信息", padding=10)
        common.pack(fill="x", padx=10, pady=5)

        ttk.Label(common, text="策略名称:").grid(row=0, column=0, sticky="w", padx=5, pady=3)
        self._name_var = tk.StringVar(value=strategy_config.get("name", ""))
        ttk.Entry(common, textvariable=self._name_var, width=30).grid(row=0, column=1, sticky="w", padx=5, pady=3)

        ttk.Label(common, text="交易对:").grid(row=1, column=0, sticky="w", padx=5, pady=3)
        self._symbol_var = tk.StringVar(value=strategy_config.get("symbol", "ethusdt"))
        ttk.Entry(common, textvariable=self._symbol_var, width=12).grid(row=1, column=1, sticky="w", padx=5, pady=3)

        self._enabled = tk.BooleanVar(value=strategy_config.get("enabled", True))
        ttk.Checkbutton(common, text="启用", variable=self._enabled).grid(row=1, column=2, padx=10, pady=3)

        # 策略参数
        params_frame = ttk.LabelFrame(frame, text="策略参数", padding=10)
        params_frame.pack(fill="x", padx=10, pady=5)

        self._vars = {}
        for i, (key, label, dtype, vmin, vmax) in enumerate(columns):
            ttk.Label(params_frame, text=f"{label}:").grid(row=i, column=0, sticky="w", padx=5, pady=2)
            default = strategy_config.get(key, 0)
            if dtype == int:
                var = tk.IntVar(value=int(default))
            else:
                var = tk.DoubleVar(value=float(default))
            ttk.Entry(params_frame, textvariable=var, width=12).grid(row=i, column=1, sticky="w", padx=5, pady=2)
            ttk.Label(params_frame, text=f"[{vmin}~{vmax}]", foreground="gray").grid(row=i, column=2, sticky="w", padx=5, pady=2)
            self._vars[key] = var

        # 按钮
        btn_frame = ttk.Frame(frame, padding=10)
        btn_frame.pack(fill="x", padx=10)

        def on_ok():
            config = {
                "name": self._name_var.get().strip(),
                "type": self.strategy_type,
                "enabled": self._enabled.get(),
                "symbol": self._symbol_var.get().strip().lower(),
            }
            for key, var in self._vars.items():
                config[key] = var.get()
            if config.get("grid_low", 0) > config.get("grid_high", 0):
                config["grid_low"], config["grid_high"] = config["grid_high"], config["grid_low"]
            self.result = config
            self.destroy()

        ttk.Button(btn_frame, text="确定", command=on_ok).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="取消", command=self.destroy).pack(side="right", padx=5)


class StrategyTypeTab(ttk.Frame):
    """单个策略类型的Tab页面 — 包含表格 + 按钮"""

    def __init__(self, parent, app, strategy_type: str):
        super().__init__(parent)
        self.app = app
        self.strategy_type = strategy_type
        self._strategies: list[dict] = []  # 该类型下的所有策略
        self._build()

    def _build(self):
        meta = _STRATEGY_META[self.strategy_type]
        columns = meta["columns"]

        # 表格列: 名称 | 启用 | symbol | ...参数列...
        col_ids = ["name", "enabled", "symbol"] + [c[0] for c in columns]
        col_labels = ["策略名称", "启用", "交易对"] + [c[1] for c in columns]

        # 工具栏
        toolbar = ttk.Frame(self, padding=5)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text=meta["label"], font=("", 10, "bold")).pack(side="left", padx=5)
        ttk.Button(toolbar, text="新增", command=self._add).pack(side="right", padx=3)
        ttk.Button(toolbar, text="修改", command=self._edit).pack(side="right", padx=3)
        ttk.Button(toolbar, text="删除", command=self._delete).pack(side="right", padx=3)

        # Treeview
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self._tree = ttk.Treeview(
            tree_frame,
            columns=col_ids,
            show="headings",
            selectmode="browse",
        )
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        # 设置列
        for col_id, col_label in zip(col_ids, col_labels):
            width = 110 if col_id not in ("name", "enabled", "symbol") else (160 if col_id == "name" else 60)
            self._tree.heading(col_id, text=col_label, anchor="center")
            self._tree.column(col_id, width=width, anchor="center", minwidth=50)

        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        # 双击编辑
        self._tree.bind("<Double-1>", lambda e: self._edit())

    def load(self):
        """从 app._config 加载该类型的所有策略并填充表格"""
        self._tree.delete(*self._tree.get_children())
        strategies = self.app._config.get("strategies", [])
        self._strategies = [s for s in strategies if s.get("type") == self.strategy_type]
        meta = _STRATEGY_META[self.strategy_type]

        for s in self._strategies:
            values = [
                s.get("name", ""),
                "✅" if s.get("enabled", True) else "❌",
                s.get("symbol", ""),
            ]
            for key, _, _, _, _ in meta["columns"]:
                val = s.get(key, "")
                values.append(val)
            self._tree.insert("", "end", values=values)

    def _get_selected(self) -> dict | None:
        sel = self._tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选中一行")
            return None
        idx = self._tree.index(sel[0])
        return self._strategies[idx] if 0 <= idx < len(self._strategies) else None

    def _add(self):
        dialog = StrategyEditDialog(self, self.strategy_type)
        self.wait_window(dialog)
        if dialog.result:
            self.app._config.setdefault("strategies", []).append(dialog.result)
            self.app._refresh_all_tabs()
            self.app._status.config(text=f"已添加: {dialog.result['name']}")

    def _edit(self):
        sc = self._get_selected()
        if sc is None:
            return
        dialog = StrategyEditDialog(self, self.strategy_type, sc.copy())
        self.wait_window(dialog)
        if dialog.result:
            # 更新对应策略
            all_strategies = self.app._config.get("strategies", [])
            for i, s in enumerate(all_strategies):
                if s is sc:
                    all_strategies[i] = dialog.result
                    break
            self.app._refresh_all_tabs()
            self.app._status.config(text=f"已修改: {dialog.result['name']}")

    def _delete(self):
        sc = self._get_selected()
        if sc is None:
            return
        if messagebox.askyesno("确认删除", f"删除策略「{sc.get('name', '')}」？"):
            self.app._config["strategies"].remove(sc)
            self.app._refresh_all_tabs()
            self.app._status.config(text=f"已删除: {sc.get('name', '')}")


class ConfigEditor(tk.Tk):
    """主窗口"""

    def __init__(self, config_path: str = CURRENT_CONFIG):
        super().__init__()
        self.config_path = config_path
        self.title(f"策略配置管理 - {os.path.basename(config_path)}")
        self.geometry("1050x700")
        self.resizable(True, True)

        self._config = {}
        self._load_config()

        # 菜单栏
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="打开配置...", command=self._open_config)
        file_menu.add_command(label="另存为...", command=self._save_as)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.destroy)
        menubar.add_cascade(label="文件", menu=file_menu)
        self.config(menu=menubar)

        # 工具栏
        toolbar = ttk.Frame(self, padding=5)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="配置文件:").pack(side="left", padx=5)
        self._path_label = ttk.Label(toolbar, text=config_path, foreground="gray")
        self._path_label.pack(side="left", padx=5)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(toolbar, text="间隔:").pack(side="left", padx=5)
        self._interval_var = tk.StringVar(value=self._config.get("interval", "1h"))
        ttk.Combobox(toolbar, textvariable=self._interval_var,
                     values=["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"],
                     width=6, state="readonly").pack(side="left", padx=3)
        ttk.Label(toolbar, text="起始:").pack(side="left", padx=5)
        self._start_var = tk.StringVar(value=self._config.get("start_date", "2024-01-01"))
        ttk.Entry(toolbar, textvariable=self._start_var, width=10).pack(side="left", padx=3)
        ttk.Label(toolbar, text="终止:").pack(side="left", padx=5)
        self._end_var = tk.StringVar(value=self._config.get("end_date", "2026-01-20"))
        ttk.Entry(toolbar, textvariable=self._end_var, width=10).pack(side="left", padx=3)

        ttk.Button(toolbar, text="💾 保存", command=self._save).pack(side="right", padx=5)
        ttk.Button(toolbar, text="🔄 刷新", command=self._reload).pack(side="right", padx=5)

        # Notebook + 各策略类型Tab
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True, padx=5, pady=5)
        self._tabs: dict[str, StrategyTypeTab] = {}

        for stype in _STRATEGY_META:
            tab = StrategyTypeTab(self._notebook, self, stype)
            self._notebook.add(tab, text=_STRATEGY_META[stype]["label"])
            self._tabs[stype] = tab

        self._refresh_all_tabs()

        # 状态栏
        self._status = ttk.Label(self, text="就绪", relief="sunken", anchor="w", padding=5)
        self._status.pack(side="bottom", fill="x")

    def _load_config(self):
        try:
            self._config = load_json_with_comments(self.config_path)
        except Exception as e:
            messagebox.showerror("错误", f"无法加载配置文件:\n{e}")
            self._config = {"strategies": [], "strategy_mapping": {}, "interval": "1h", "initial_capital": 10000.0}

    def _refresh_all_tabs(self):
        for tab in self._tabs.values():
            tab.load()

    def _save(self):
        try:
            self._config["interval"] = self._interval_var.get()
            self._config["start_date"] = self._start_var.get()
            self._config["end_date"] = self._end_var.get()
            save_json(self.config_path, self._config)
            self._status.config(text=f"✅ 已保存到 {os.path.basename(self.config_path)}")
            messagebox.showinfo("成功", "配置已保存")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def _reload(self):
        self._load_config()
        self._interval_var.set(self._config.get("interval", "1h"))
        self._start_var.set(self._config.get("start_date", "2024-01-01"))
        self._end_var.set(self._config.get("end_date", "2026-01-20"))
        self._refresh_all_tabs()
        self._status.config(text=f"已刷新 {os.path.basename(self.config_path)}")

    def _open_config(self):
        path = filedialog.askopenfilename(
            title="选择配置文件",
            initialdir=os.path.dirname(self.config_path),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.config_path = path
            self.title(f"策略配置管理 - {os.path.basename(path)}")
            self._path_label.config(text=path)
            self._reload()

    def _save_as(self):
        path = filedialog.asksaveasfilename(
            title="另存为配置文件",
            initialdir=os.path.dirname(CURRENT_CONFIG),
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.config_path = path
            self.title(f"策略配置管理 - {os.path.basename(path)}")
            self._path_label.config(text=path)
            self._save()


def main():
    app = ConfigEditor()
    app.mainloop()


if __name__ == "__main__":
    main()