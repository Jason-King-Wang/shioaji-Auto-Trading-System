from __future__ import annotations

import json
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox

from .config import Settings
from .model_order_interface import load_model_order_batch, process_model_order_batch
from .model_registry import discover_installed_models, find_model_by_code
from .paths import PROJECT_ROOT
from .quick_simulator import (
    load_quote_prices,
    parse_stock_buy_request,
    parse_stock_sell_request,
    resolve_request_prices,
    resolve_sell_request_prices,
    simulate_buy_orders,
    simulate_sell_orders,
)
from .setup_wizard import OFFICIAL_SETUP_SUMMARY, read_env_file, setup_status, write_sinopac_env


ACTION_TO_CODE = {"買進": "buy", "賣出": "sell"}
LOT_TO_CODE = {"零股": "odd", "整張": "common"}
BG = "#f5f6f8"
PANEL_BG = "#ffffff"
BORDER = "#9aa3af"
TEXT = "#111827"
HINT = "#53657d"
PRIMARY = "#1f5fbf"


class SimpleTradingApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("永豐簡單交易")
        self.geometry("1180x800")
        self.minsize(1040, 700)
        self.configure(bg=BG)
        self.settings = Settings.load()
        self.quote_file = self.settings.project_root / "examples" / "fake_quotes_example.csv"
        self._build_layout()
        self._refresh_setup_status()
        self._refresh_model_list()
        if not setup_status().complete:
            self.after(250, self._open_setup_dialog)

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        header = tk.Frame(self, bg=BG, padx=18, pady=16)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        tk.Label(header, text="永豐簡單交易", bg=BG, fg=TEXT, font=("Microsoft JhengHei UI", 18, "bold")).grid(row=0, column=0, sticky="w")
        self.setup_status_var = tk.StringVar()
        tk.Label(header, textvariable=self.setup_status_var, bg=BG, fg=TEXT, font=("Microsoft JhengHei UI", 10)).grid(row=0, column=1, sticky="w", padx=20)
        self._button(header, "永豐設定", self._open_setup_dialog).grid(row=0, column=2, padx=(8, 0))
        self._button(header, "詳細說明", self._show_details).grid(row=0, column=3, padx=(8, 0))
        self._button(header, "跑模擬審核測試", self._run_review_test).grid(row=0, column=4, padx=(8, 0))

        order = self._panel("正常下單")
        order.grid(row=1, column=0, sticky="ew", padx=18, pady=(4, 14))
        for column in range(3):
            order.columnconfigure(column, weight=1, uniform="order")

        self.action_var = tk.StringVar(value="買進")
        self.stock_var = tk.StringVar(value="2330")
        self.price_var = tk.StringVar(value="982")
        self.budget_var = tk.StringVar(value="100000")
        self.qty_var = tk.StringVar(value="100")
        self.lot_var = tk.StringVar(value="零股")

        self._field(order, "買賣", self._menu(order, self.action_var, list(ACTION_TO_CODE)), 0, 0)
        self._field(order, "單位", self._menu(order, self.lot_var, list(LOT_TO_CODE)), 0, 1)
        self._field(order, "股票代號", self._entry(order, self.stock_var), 0, 2)
        self._field(order, "價格", self._entry(order, self.price_var), 2, 0)
        self._field(order, "買進預算", self._entry(order, self.budget_var), 2, 1)
        self._field(order, "賣出股數", self._entry(order, self.qty_var), 2, 2)

        tk.Label(
            order,
            text="買進時填預算；賣出時填股數。零股以股為單位，整張會用 1000 股為一張。",
            bg=PANEL_BG,
            fg=HINT,
            font=("Microsoft JhengHei UI", 9),
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))

        buttons = tk.Frame(order, bg=PANEL_BG)
        buttons.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        self._button(buttons, "模擬下單", self._simulate_manual_order, primary=True).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._button(buttons, "取消", self._clear_output).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        model = self._panel("交易模型")
        model.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 14))
        for column in range(4):
            model.columnconfigure(column, weight=1)

        self.model_code_var = tk.StringVar(value="DEMO")
        self.model_budget_var = tk.StringVar(value="100000")
        self.model_list_var = tk.StringVar()

        self._field(model, "模型代碼", self._entry(model, self.model_code_var), 0, 0)
        self._field(model, "總買進預算", self._entry(model, self.model_budget_var), 0, 1)
        self._button(model, "套用模型", self._apply_model_code, primary=True).grid(row=1, column=2, sticky="ew", padx=(16, 8), pady=(23, 0))
        self._button(model, "選擇 intents 檔", self._load_model_file).grid(row=1, column=3, sticky="ew", padx=(8, 0), pady=(23, 0))
        self._button(model, "取消模型", self._clear_output).grid(row=2, column=3, sticky="ew", padx=(8, 0), pady=(12, 0))
        tk.Label(model, textvariable=self.model_list_var, bg=PANEL_BG, fg=HINT, font=("Microsoft JhengHei UI", 9)).grid(row=2, column=0, columnspan=3, sticky="w", pady=(14, 0))

        output_frame = tk.Frame(self, bg=BG, padx=18, pady=18)
        output_frame.grid(row=3, column=0, sticky="nsew", pady=(0, 0))
        output_frame.columnconfigure(0, weight=1)
        output_frame.rowconfigure(0, weight=1)
        self.output = tk.Text(output_frame, wrap="word", height=18, font=("Consolas", 10), relief="solid", bd=1, bg="white", fg=TEXT)
        self.output.grid(row=0, column=0, sticky="nsew")
        scrollbar = tk.Scrollbar(output_frame, command=self.output.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.output.configure(yscrollcommand=scrollbar.set)

    def _panel(self, title: str) -> tk.LabelFrame:
        return tk.LabelFrame(
            self,
            text=title,
            bg=PANEL_BG,
            fg=TEXT,
            bd=1,
            relief="solid",
            padx=18,
            pady=16,
            font=("Microsoft JhengHei UI", 11, "bold"),
        )

    def _entry(self, parent: tk.Widget, variable: tk.StringVar, *, secret: bool = False) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=variable,
            show="*" if secret else "",
            bg="white",
            fg=TEXT,
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=PRIMARY,
            insertbackground=TEXT,
            font=("Microsoft JhengHei UI", 11),
        )

    def _menu(self, parent: tk.Widget, variable: tk.StringVar, values: list[str]) -> tk.OptionMenu:
        menu = tk.OptionMenu(parent, variable, *values)
        menu.configure(
            bg="white",
            fg=TEXT,
            activebackground="#eef4ff",
            activeforeground=TEXT,
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground=BORDER,
            font=("Microsoft JhengHei UI", 11),
            anchor="w",
        )
        menu["menu"].configure(font=("Microsoft JhengHei UI", 10))
        return menu

    def _button(self, parent: tk.Widget, text: str, command, *, primary: bool = False) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=PRIMARY if primary else "#e6e3dd",
            fg="white" if primary else TEXT,
            activebackground="#174ea6" if primary else "#d8d5ce",
            activeforeground="white" if primary else TEXT,
            relief="solid",
            bd=1,
            padx=14,
            pady=9,
            font=("Microsoft JhengHei UI", 10, "bold" if primary else "normal"),
        )

    def _field(self, parent: tk.Widget, label: str, widget: tk.Widget, row: int, column: int) -> None:
        cell = tk.Frame(parent, bg=PANEL_BG)
        cell.grid(row=row, column=column, sticky="ew", padx=(0 if column == 0 else 18, 0), pady=(2, 14))
        cell.columnconfigure(0, weight=1)
        tk.Label(cell, text=label, bg=PANEL_BG, fg=TEXT, font=("Microsoft JhengHei UI", 10)).grid(row=0, column=0, sticky="w")
        widget.grid(row=1, column=0, sticky="ew", ipady=7, pady=(6, 0))

    def _refresh_setup_status(self) -> None:
        status = setup_status()
        self.setup_status_var.set("永豐設定：完成" if status.complete else "永豐設定：尚未完成")

    def _refresh_model_list(self) -> None:
        models = discover_installed_models()
        if not models:
            self.model_list_var.set("已安裝模型：無")
            return
        shown = ", ".join(f"{model.code}（{model.name}）" for model in models)
        self.model_list_var.set(f"已安裝模型：{shown}")

    def _open_setup_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("第一次設定永豐 / Shioaji")
        dialog.configure(bg=BG)
        dialog.transient(self)
        dialog.grab_set()
        dialog.columnconfigure(1, weight=1)

        values = read_env_file()
        fields = [
            ("API Key", "api_key", "SINOPAC_API_KEY", False),
            ("Secret Key", "secret_key", "SINOPAC_SECRET_KEY", True),
            ("身分證字號", "person_id", "SINOPAC_PERSON_ID", False),
            ("CA 憑證路徑", "ca_path", "SINOPAC_CA_PATH", False),
            ("CA 憑證密碼", "ca_password", "SINOPAC_CA_PASSWORD", True),
        ]
        entries: dict[str, tk.Entry] = {}
        for row, (label, key, env_key, secret) in enumerate(fields):
            tk.Label(dialog, text=label, bg=BG, fg=TEXT, font=("Microsoft JhengHei UI", 10)).grid(row=row, column=0, sticky="w", padx=14, pady=7)
            entry = self._entry(dialog, tk.StringVar(value=values.get(env_key, "")), secret=secret)
            entry.grid(row=row, column=1, sticky="ew", padx=14, pady=7, ipady=6)
            entries[key] = entry
            if key == "ca_path":
                self._button(dialog, "選擇", lambda e=entry: self._browse_ca(e)).grid(row=row, column=2, padx=14, pady=7)

        note = "這些資料會存在本專案 .env，且 .gitignore 已排除，不會放進 git。"
        tk.Label(dialog, text=note, bg=BG, fg=HINT, font=("Microsoft JhengHei UI", 9)).grid(row=len(fields), column=0, columnspan=3, sticky="w", padx=14, pady=(10, 4))

        buttons = tk.Frame(dialog, bg=BG)
        buttons.grid(row=len(fields) + 1, column=0, columnspan=3, sticky="e", padx=14, pady=14)
        self._button(buttons, "詳細說明", self._show_details).pack(side="left", padx=4)
        self._button(buttons, "儲存", lambda: self._save_setup(dialog, entries), primary=True).pack(side="left", padx=4)
        self._button(buttons, "取消", dialog.destroy).pack(side="left", padx=4)

    def _browse_ca(self, entry: tk.Entry) -> None:
        path = filedialog.askopenfilename(
            title="選擇 Sinopac CA 憑證",
            filetypes=[("Certificate", "*.pfx *.p12"), ("All files", "*.*")],
        )
        if path:
            entry.delete(0, tk.END)
            entry.insert(0, path)

    def _save_setup(self, dialog: tk.Toplevel, entries: dict[str, tk.Entry]) -> None:
        values = {key: entry.get().strip() for key, entry in entries.items()}
        if any(not value for value in values.values()):
            messagebox.showwarning("資料不足", "請先填完所有欄位。")
            return
        write_sinopac_env(values)
        self._refresh_setup_status()
        messagebox.showinfo("已儲存", "永豐設定已寫入本專案 .env。")
        dialog.destroy()

    def _show_details(self) -> None:
        messagebox.showinfo("永豐程式交易前置說明", OFFICIAL_SETUP_SUMMARY)

    def _run_review_test(self) -> None:
        if not setup_status().complete:
            messagebox.showwarning("尚未設定", "請先完成永豐設定。")
            return
        self._append_output("開始執行永豐模擬審核測試：login + simulation place_order...\n")
        command = [
            sys.executable,
            str(PROJECT_ROOT / "run.py"),
            "api-test-stock",
            "--stock-id",
            "2890",
            "--quantity",
            "1",
            "--order-lot",
            "IntradayOdd",
        ]
        self._run_subprocess(command)

    def _simulate_manual_order(self) -> None:
        try:
            settings = Settings.load()
            quotes = load_quote_prices(self.quote_file)
            if ACTION_TO_CODE[self.action_var.get()] == "buy":
                request = parse_stock_buy_request(f"{self.stock_var.get()}:{self.price_var.get()}")
                resolved = resolve_request_prices([request], quotes)
                result = simulate_buy_orders(
                    resolved,
                    budget=float(self.budget_var.get()),
                    fees=settings.fees,
                    order_lot=LOT_TO_CODE[self.lot_var.get()],
                    buffer_multiplier=settings.auto_trading.cost_buffer_multiplier,
                )
                self._show_json(asdict(result))
            else:
                request = parse_stock_sell_request(
                    f"{self.stock_var.get()}:{self.price_var.get()}",
                    quantity=int(self.qty_var.get()),
                )
                resolved = resolve_sell_request_prices([request], quotes)
                result = simulate_sell_orders(
                    resolved,
                    fees=settings.fees,
                    order_lot=LOT_TO_CODE[self.lot_var.get()],
                )
                self._show_json(asdict(result))
        except Exception as exc:
            messagebox.showerror("模擬失敗", str(exc))

    def _apply_model_code(self) -> None:
        try:
            model = find_model_by_code(self.model_code_var.get())
            self._process_model_file(model.order_file, source_model=model.code)
        except Exception as exc:
            messagebox.showerror("模型套用失敗", str(exc))

    def _load_model_file(self) -> None:
        path = filedialog.askopenfilename(
            title="選擇模型 order intents",
            filetypes=[("Order intents", "*.json *.csv"), ("All files", "*.*")],
        )
        if path:
            self._process_model_file(Path(path), source_model="")

    def _process_model_file(self, path: Path, *, source_model: str) -> None:
        settings = Settings.load()
        batch = load_model_order_batch(path)
        if source_model:
            batch.source_model = source_model
        if self.model_budget_var.get().strip():
            batch.buy_budget = float(self.model_budget_var.get())
        result = process_model_order_batch(
            batch,
            fees=settings.fees,
            quote_file=self.quote_file,
            buffer_multiplier=settings.auto_trading.cost_buffer_multiplier,
            write_output=False,
        )
        self._show_json(asdict(result))

    def _run_subprocess(self, command: list[str]) -> None:
        def worker() -> None:
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=120,
                )
                self._append_output(completed.stdout)
                if completed.stderr:
                    self._append_output("\n[stderr]\n" + completed.stderr)
                self._append_output(f"\nexit_code: {completed.returncode}\n")
            except Exception as exc:
                self._append_output(f"\nerror: {exc}\n")

        threading.Thread(target=worker, daemon=True).start()

    def _show_json(self, payload: object) -> None:
        self.output.delete("1.0", tk.END)
        self.output.insert(tk.END, json.dumps(payload, ensure_ascii=False, indent=2))

    def _append_output(self, text: str) -> None:
        self.output.after(0, lambda: (self.output.insert(tk.END, text), self.output.see(tk.END)))

    def _clear_output(self) -> None:
        self.output.delete("1.0", tk.END)


def command_app(_args=None) -> int:
    app = SimpleTradingApp()
    app.mainloop()
    return 0
