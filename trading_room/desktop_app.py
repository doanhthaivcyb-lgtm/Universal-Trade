"""App desktop (Tkinter, chỉ dùng thư viện chuẩn) cho phòng trade AI.

Chạy bằng cách bấm đúp file ``run_app.bat`` ở thư mục gốc của dự án, hoặc:
    py -3.13 main.py --app
    py -3.13 -m trading_room.desktop_app

App mở thành 1 cửa sổ trên máy Windows, không cần trình duyệt, không cần
gõ lệnh. App hiện đầy đủ: dữ liệu MT5, tin tức, sentiment, danh mục, từng
AI đang làm gì ở từng bước, cách mỗi AI làm việc, kết luận trưởng phòng,
từng kiểm tra của cửa rủi ro, nhật ký các phiên, và cấu hình an toàn.

An toàn giống bản web: app không bao giờ hiện giá trị key trong .env, chỉ
hiện có/không có key; không cho sửa key, không cho đổi sang chế độ live;
chạy phiên làm đúng luồng như CLI, không có nút đặt lệnh riêng; chỉ 1
phiên chạy tại một thời điểm.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from .agents import SYSTEM_PROMPTS
from .config import load_config
from .executor import get_executor
from .journal import save_session
from .market_snapshot import load_snapshot
from .mt5_data import MT5DataProvider
from .orchestrator import ANALYST_NAMES, enrich_snapshot, run_room
from .web_ui import EDITABLE_RISK_FIELDS, TIMEFRAMES

AGENT_NAMES = ANALYST_NAMES + ["chief"]

AGENT_HOW = {
    "technical": {
        "role": "Phân tích kỹ thuật",
        "how": "Đọc xu hướng, cấu trúc giá, hỗ trợ/kháng cự, RSI, MACD, ATR, EMA và Bollinger trong snapshot. Không đọc tin vĩ mô.",
    },
    "fundamental": {
        "role": "Phân tích cơ bản",
        "how": "Chỉ dùng tin tức và lịch kinh tế đã lấy được trong snapshot (lịch ForexFactory, RSS Fed). Không có tin mới thì nói rõ, không bịa số liệu.",
    },
    "sentiment": {
        "role": "Phân tích tâm lý và dòng tiền",
        "how": "Đọc COT vàng (ghi rõ ngày báo cáo, luôn cũ vài ngày) và điểm từ khóa tiêu đề tin tức. Điểm từ khóa không phải Fear & Greed thật.",
    },
    "contrarian": {
        "role": "AI phản biện",
        "how": "Tìm điểm yếu nhất của phe đa số, kịch bản ngược chiều và điểm vô hiệu. Không đồng thuận cho có.",
    },
    "risk_analyst": {
        "role": "Quản trị rủi ro (tư vấn)",
        "how": "Đánh giá spread, biến động, giờ tin và hậu quả nếu stop bị quét. Không quyết lot, code sẽ quyết.",
    },
    "portfolio_manager": {
        "role": "Quản lý danh mục",
        "how": "Đánh giá exposure toàn tài khoản, mức tập trung vào vàng và rủi ro nếu cộng thêm 1 lệnh. Không quyết lot, code sẽ quyết.",
    },
    "chief": {
        "role": "Trưởng phòng giao dịch",
        "how": "Đọc tất cả ý kiến vòng 2, cân nhắc cả phản biện và danh mục, ra 1 quyết định cuối duy nhất. Mâu thuẫn mạnh hoặc bằng chứng yếu thì HOLD.",
    },
}

STEPS = [
    "1. Lấy dữ liệu MT5",
    "2. Lấy tin tức + COT",
    "3. Vòng 1: phân tích độc lập",
    "4. Vòng 2: tranh luận",
    "5. Trưởng phòng tổng hợp",
    "6. Cửa rủi ro cứng",
    "7. Executor + nhật ký",
]


def _view_to_dict(view, round_no: int = 0) -> dict:
    return {
        "name": view.name, "provider": view.provider, "model": view.model,
        "direction": view.direction, "confidence": view.confidence,
        "entry": view.entry, "stop_loss": view.stop_loss, "take_profit": view.take_profit,
        "reasoning": view.reasoning, "invalidation": view.invalidation,
        "evidence": list(view.evidence or []), "parse_ok": view.parse_ok,
        "raw_text": view.raw_text, "round": round_no,
    }


def _snapshot_to_dict(snapshot) -> dict:
    return {
        "symbol": snapshot.symbol, "timeframe": snapshot.timeframe,
        "bid": snapshot.bid, "ask": snapshot.ask, "price": snapshot.price,
        "spread": snapshot.spread, "source": snapshot.source,
        "captured_at": snapshot.captured_at, "indicators": snapshot.indicators,
        "macro_news": snapshot.macro_news, "news_text": snapshot.news_text,
        "sentiment_text": snapshot.sentiment_text, "portfolio": snapshot.portfolio,
        "open_positions": snapshot.open_positions, "daily_loss_usd": snapshot.daily_loss_usd,
        "account_balance": snapshot.account_balance, "account_equity": snapshot.account_equity,
        "notes": snapshot.notes, "prompt_text": snapshot.to_prompt_text(),
    }


def _build_snapshot(cfg, source: str, snapshot_path: str):
    if source == "mt5":
        provider = MT5DataProvider(cfg)
        try:
            return provider.build_snapshot()
        finally:
            provider.close()
    return load_snapshot(snapshot_path)


class SessionRunner:
    """Chạy phiên trong luồng nền, gửi sự kiện về hàng đợi để UI cập nhật."""

    def __init__(self, config_path: str, output_dir: str, events: queue.Queue):
        self.config_path = config_path
        self.output_dir = output_dir
        self.events = events
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.running = False
        self.looping = False
        self._round = 0

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, source: str, mock: bool, snapshot_path: str, loop: bool) -> bool:
        if self.is_running():
            return False
        self._stop.clear()
        self.looping = loop
        self.running = True
        self._thread = threading.Thread(
            target=self._worker, args=(source, mock, snapshot_path, loop), daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        self.looping = False

    def _emit(self, kind: str, payload=None) -> None:
        self.events.put((kind, payload))

    def _on_progress(self, event: dict) -> None:
        phase = event.get("phase", "")
        if phase == "round1":
            self._round = 1
        elif phase == "round2":
            self._round = 2
        elif phase in ("chief_start", "chief_done"):
            self._round = 3
        step = {"snapshot": 0, "news": 1, "round1": 2, "agent_start": 2,
                "round2": 3, "chief_start": 4, "chief_done": 4, "risk": 5, "done": 6}.get(phase)
        if step is not None:
            self._emit("step", step)
        view = event.get("view")
        if view is not None:
            self._emit("view", _view_to_dict(view, self._round))
        self._emit("log", event.get("status", ""))

    def _run_one(self, source: str, mock: bool, snapshot_path: str) -> dict:
        cfg = load_config(self.config_path)
        if cfg.live_enabled or cfg.mode == "live":
            raise RuntimeError("Chế độ live đã bật. App từ chối chạy để tránh bấm nhầm.")
        self._emit("step", 0)
        self._emit("log", "Đang lấy dữ liệu MT5..." if source == "mt5" else "Đang đọc file thử...")
        snapshot = _build_snapshot(cfg, source, snapshot_path)
        snap_dict = _snapshot_to_dict(snapshot)
        self._emit("snapshot", snap_dict)
        self._emit("step", 1)
        chief, views, risk = run_room(cfg, snapshot, mock=mock, on_progress=self._on_progress)
        self._emit("step", 5)
        order_message = "Không vào lệnh."
        if risk.allowed:
            self._emit("log", "Cửa rủi ro cho phép, đang gọi executor...")
            result = get_executor(cfg).place(snapshot, chief, risk.lot)
            order_message = result.message
        saved = save_session(Path(self.output_dir), snapshot, views, chief, risk, order_message)
        result = {
            "snapshot": snap_dict,
            "views": [_view_to_dict(v, 2) for v in views],
            "chief": _view_to_dict(chief, 3),
            "risk": {"allowed": risk.allowed, "reason": risk.reason, "lot": risk.lot,
                     "sl_distance": risk.sl_distance, "checks": risk.checks},
            "order": order_message, "saved": str(saved),
        }
        self._emit("result", result)
        self._emit("step", 6)
        return result

    def _worker(self, source: str, mock: bool, snapshot_path: str, loop: bool) -> None:
        try:
            while True:
                self._run_one(source, mock, snapshot_path)
                if not loop:
                    break
                cfg = load_config(self.config_path)
                if self._stop.is_set() or (cfg.project_root / cfg.risk.kill_switch_file).exists():
                    break
                self._emit("log", f"Đang nghỉ {cfg.loop_interval_seconds} giây tới phiên tiếp theo...")
                if self._stop.wait(timeout=float(cfg.loop_interval_seconds)):
                    break
        except Exception as exc:
            self._emit("error", str(exc))
        finally:
            self.running = False
            self.looping = False
            self._emit("finished", None)

    def preview(self, source: str, enrich: bool) -> None:
        """Lấy dữ liệu MT5 để xem trước, không gọi AI. Chạy trong luồng nền."""

        def _job():
            try:
                cfg = load_config(self.config_path)
                snapshot = _build_snapshot(cfg, source, "data/snapshot_example.json")
                if enrich:
                    snapshot = enrich_snapshot(cfg, snapshot, mock=False)
                self._emit("snapshot", _snapshot_to_dict(snapshot))
                self._emit("log", "Đã lấy dữ liệu mới nhất.")
            except Exception as exc:
                self._emit("error", str(exc))

        threading.Thread(target=_job, daemon=True).start()



class DesktopApp(tk.Tk):
    def __init__(self, config_path: str = "config.yaml", output_dir: str = "output"):
        super().__init__()
        self.config_path = config_path
        self.output_dir = output_dir
        self.events: queue.Queue = queue.Queue()
        self.runner = SessionRunner(config_path, output_dir, self.events)
        self.title("Phòng trade AI — XAUUSD")
        self.geometry("1280x820")
        self.minsize(1024, 640)
        try:
            self.style = ttk.Style(self)
            if "clam" in self.style.theme_names():
                self.style.theme_use("clam")
        except Exception:
            pass
        self._current_views: dict[str, dict] = {}
        self._current_snapshot: dict | None = None
        self._build_widgets()
        self._refresh_status()
        self._load_history()
        self.after(200, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- Xây giao diện ----------
    def _build_widgets(self) -> None:
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Phòng trade AI — XAUUSD", font=("", 16, "bold")).pack(side="left")
        self.mode_var = tk.StringVar(value="DRY-RUN")
        ttk.Label(top, textvariable=self.mode_var, font=("", 12, "bold"), foreground="#1e8449").pack(side="left", padx=12)
        self.price_var = tk.StringVar(value="Chưa có giá")
        ttk.Label(top, textvariable=self.price_var).pack(side="left", padx=8)
        self.stop_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.stop_var, foreground="#c0392b", font=("", 11, "bold")).pack(side="right")

        controls = ttk.Frame(self, padding=(8, 0, 8, 8))
        controls.pack(fill="x")
        ttk.Label(controls, text="Nguồn:").pack(side="left")
        self.source_var = tk.StringVar(value="mt5")
        ttk.Combobox(controls, textvariable=self.source_var, values=["mt5", "json"], width=8, state="readonly").pack(side="left", padx=4)
        self.mock_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text="AI giả (không tốn phí, không cần key)", variable=self.mock_var).pack(side="left", padx=6)
        self.run_btn = ttk.Button(controls, text="▶ Chạy 1 phiên", command=lambda: self._start_run(False))
        self.run_btn.pack(side="left", padx=4)
        self.loop_btn = ttk.Button(controls, text="↻ Chạy lặp lại", command=lambda: self._start_run(True))
        self.loop_btn.pack(side="left", padx=4)
        ttk.Button(controls, text="Làm mới dữ liệu MT5", command=lambda: self.runner.preview(self.source_var.get(), False)).pack(side="left", padx=4)
        ttk.Button(controls, text="Lấy tin + COT", command=lambda: self.runner.preview(self.source_var.get(), True)).pack(side="left", padx=4)
        stop_btn = ttk.Button(controls, text="⛔ DỪNG KHẨN CẤP", command=self._create_stop)
        stop_btn.pack(side="left", padx=4)
        ttk.Button(controls, text="Gỡ STOP", command=self._clear_stop).pack(side="left", padx=4)

        steps = ttk.Frame(self, padding=(8, 0, 8, 4))
        steps.pack(fill="x")
        ttk.Label(steps, text="Quy trình phiên:", font=("", 10, "bold")).pack(side="left")
        self.step_labels: list[ttk.Label] = []
        for label in STEPS:
            lbl = ttk.Label(steps, text="○ " + label, foreground="#5d6d7e")
            lbl.pack(side="left", padx=6)
            self.step_labels.append(lbl)
        self.phase_var = tk.StringVar(value="Chưa chạy phiên nào.")
        ttk.Label(self, textvariable=self.phase_var, padding=(8, 0, 8, 4), wraplength=1200).pack(fill="x", anchor="w")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=4)

        # Tab 1: phòng trade
        tab1 = ttk.Frame(notebook, padding=6)
        notebook.add(tab1, text="Phòng trade")
        paned = ttk.PanedWindow(tab1, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, padding=4)
        paned.add(left, weight=1)
        ttk.Label(left, text="Dữ liệu MT5 • Tin tức • Sentiment • Danh mục", font=("", 11, "bold")).pack(anchor="w")
        self.data_text = tk.Text(left, wrap="word", height=30)
        self.data_text.pack(fill="both", expand=True)
        self.data_text.insert("1.0", "Chưa có dữ liệu.\nBấm “Làm mới dữ liệu MT5” hoặc “Chạy 1 phiên”.")
        self.data_text.config(state="disabled")

        center = ttk.Frame(paned, padding=4)
        paned.add(center, weight=2)
        ttk.Label(center, text="Các AI đang làm việc — bấm “Xem cách AI làm việc” ở từng thẻ", font=("", 11, "bold")).pack(anchor="w")
        self.cards_frame = ttk.Frame(center)
        self.cards_frame.pack(fill="both", expand=True)
        self.cards: dict[str, dict] = {}
        for name in AGENT_NAMES:
            card = ttk.LabelFrame(self.cards_frame, text=f"{name} — {AGENT_HOW[name]['role']}", padding=6)
            card.pack(fill="x", pady=3)
            header = ttk.Frame(card)
            header.pack(fill="x")
            status_lbl = ttk.Label(header, text="Chờ chạy", foreground="#5d6d7e")
            status_lbl.pack(side="left")
            dir_lbl = ttk.Label(header, text="—", font=("", 11, "bold"))
            dir_lbl.pack(side="right")
            conf = ttk.Progressbar(card, maximum=1.0, length=200)
            conf.pack(fill="x", pady=3)
            reason_lbl = ttk.Label(card, text=AGENT_HOW[name]["how"], wraplength=520, justify="left")
            reason_lbl.pack(fill="x", anchor="w")
            detail_btn = ttk.Button(card, text="Xem cách AI này làm việc", command=lambda n=name: self._show_agent_detail(n))
            detail_btn.pack(anchor="e", pady=2)
            self.cards[name] = {"frame": card, "status": status_lbl, "dir": dir_lbl,
                                "conf": conf, "reason": reason_lbl}

        right = ttk.Frame(paned, padding=4)
        paned.add(right, weight=1)
        ttk.Label(right, text="Kết luận + Cửa rủi ro", font=("", 11, "bold")).pack(anchor="w")
        self.chief_text = tk.Text(right, wrap="word", height=12)
        self.chief_text.pack(fill="x")
        self.chief_text.insert("1.0", "Chưa có kết luận.")
        self.chief_text.config(state="disabled")
        ttk.Label(right, text="10 kiểm tra cửa rủi ro:").pack(anchor="w", pady=(8, 0))
        self.checks_text = tk.Text(right, wrap="word", height=14)
        self.checks_text.pack(fill="both", expand=True)
        self.checks_text.insert("1.0", "Chưa chạy kiểm tra nào.")
        self.checks_text.config(state="disabled")
        self.order_var = tk.StringVar(value="Chưa có lệnh.")
        ttk.Label(right, textvariable=self.order_var, wraplength=360, font=("", 11, "bold")).pack(fill="x", pady=6, anchor="w")

        # Tab 2: nhật ký
        tab2 = ttk.Frame(notebook, padding=6)
        notebook.add(tab2, text="Nhật ký & lịch sử")
        left2 = ttk.Frame(tab2)
        left2.pack(side="left", fill="y", padx=(0, 6))
        ttk.Button(left2, text="Làm mới lịch sử", command=self._load_history).pack(fill="x")
        ttk.Button(left2, text="Mở thư mục output", command=self._open_output).pack(fill="x", pady=4)
        self.history_list = tk.Listbox(left2, width=34, height=28)
        self.history_list.pack(fill="y", expand=True)
        self.history_list.bind("<<ListboxSelect>>", self._on_history_select)
        self.history_text = tk.Text(tab2, wrap="word")
        self.history_text.pack(side="left", fill="both", expand=True)
        self.history_text.insert("1.0", "Chọn một phiên bên trái để xem lại toàn bộ.")
        self.history_text.config(state="disabled")
        self._history_data: list[dict] = []

        # Tab 3: cấu hình
        tab3 = ttk.Frame(notebook, padding=10)
        notebook.add(tab3, text="Cấu hình & an toàn")
        self._build_config_tab(tab3)

        status = ttk.Frame(self, padding=6)
        status.pack(fill="x")
        self.keys_var = tk.StringVar(value="Key: đang kiểm tra...")
        ttk.Label(status, textvariable=self.keys_var).pack(side="left")
        ttk.Label(status, text="Mọi giá SL/TP tính bằng USD, không dùng pips. Chế độ live bị khóa trên app.").pack(side="right")

    def _build_config_tab(self, parent: ttk.Frame) -> None:
        cfg = load_config(self.config_path)
        self.cfg_entries: dict[str, tk.Entry] = {}
        self.cfg_checks: dict[str, tk.BooleanVar] = {}

        def row(label: str, key: str, value, row_no: int) -> None:
            ttk.Label(parent, text=label).grid(row=row_no, column=0, sticky="w", pady=3)
            entry = ttk.Entry(parent, width=28)
            entry.insert(0, str(value))
            entry.grid(row=row_no, column=1, sticky="w", pady=3, padx=6)
            self.cfg_entries[key] = entry

        row("Symbol trên MT5:", "symbol", cfg.symbol, 0)
        row("Khung thời gian (M1/M5/M15/M30/H1/H4/D1):", "timeframe", cfg.timeframe, 1)
        row("Chế độ (chỉ xem):", "mode", cfg.mode, 2)
        self.cfg_entries["mode"].config(state="disabled")
        row("Số vị thế tối đa:", "max_open_positions", cfg.max_open_positions, 3)
        row("Chu kỳ lặp (giây):", "loop_interval_seconds", cfg.loop_interval_seconds, 4)
        risk = cfg.risk
        fields = [
            ("Lỗ tối đa 1 lệnh (USD):", "max_risk_usd_per_trade", risk.max_risk_usd_per_trade),
            ("Lỗ tối đa trong ngày (USD):", "max_daily_loss_usd", risk.max_daily_loss_usd),
            ("Lot tối đa 1 lệnh:", "max_lot_per_order", risk.max_lot_per_order),
            ("Độ tin cậy tối thiểu:", "min_confidence", risk.min_confidence),
            ("Khoảng cách SL tối thiểu (USD):", "min_sl_distance_usd", risk.min_sl_distance_usd),
            ("Khoảng cách SL tối đa (USD):", "max_sl_distance_usd", risk.max_sl_distance_usd),
            ("Tỉ lệ lãi/lỗ tối thiểu:", "min_reward_risk", risk.min_reward_risk),
            ("Spread tối đa (USD):", "max_spread_usd", risk.max_spread_usd),
            ("Giá chạy tối đa cho phép (USD):", "max_price_move_usd", risk.max_price_move_usd),
            ("Exposure danh mục tối đa (USD):", "max_portfolio_notional_usd", risk.max_portfolio_notional_usd),
        ]
        for i, (label, key, value) in enumerate(fields, start=5):
            row(label, key, value, i)
        news_var = tk.BooleanVar(value=cfg.news_enabled)
        sent_var = tk.BooleanVar(value=cfg.sentiment_enabled)
        self.cfg_checks["news_enabled"] = news_var
        self.cfg_checks["sentiment_enabled"] = sent_var
        ttk.Checkbutton(parent, text="Bật lấy tin tức", variable=news_var).grid(row=15, column=0, sticky="w")
        ttk.Checkbutton(parent, text="Bật lấy sentiment (COT)", variable=sent_var).grid(row=15, column=1, sticky="w")
        ttk.Button(parent, text="Lưu cấu hình", command=self._save_config).grid(row=16, column=0, pady=10, sticky="w")
        ttk.Button(parent, text="Mở file .env bằng Notepad", command=self._open_env).grid(row=16, column=1, pady=10, sticky="w")
        self.config_msg_var = tk.StringVar(value="Lưu sẽ tạo bản sao config.yaml.bak. Đổi model AI vẫn sửa tay trong config.yaml.")
        ttk.Label(parent, textvariable=self.config_msg_var, wraplength=700).grid(row=17, column=0, columnspan=2, sticky="w")
        ttk.Label(parent, text="Key AI không hiện giá trị ở đây và không sửa được ở đây. Điền key trong file .env bằng Notepad, không dán key vào chat hay lên mạng.", wraplength=700, foreground="#c0392b").grid(row=18, column=0, columnspan=2, sticky="w", pady=6)

    # ---------- Hành động ----------
    def _start_run(self, loop: bool) -> None:
        if not self.runner.start(self.source_var.get(), self.mock_var.get(),
                                 "data/snapshot_example.json", loop):
            messagebox.showinfo("Đang chạy", "Đang có 1 phiên chạy, chờ xong rồi chạy tiếp.")
            return
        for name in AGENT_NAMES:
            self._set_card(name, "Đang chờ...", "—", 0.0, AGENT_HOW[name]["how"])
        self.phase_var.set("Đang chạy phiên...")
        self._refresh_buttons()

    def _create_stop(self) -> None:
        cfg = load_config(self.config_path)
        stop_file = cfg.project_root / cfg.risk.kill_switch_file
        stop_file.write_text("STOP\n", encoding="utf-8")
        self.runner.stop()
        self.stop_var.set("ĐÃ DỪNG (file STOP)")
        self._refresh_status()

    def _clear_stop(self) -> None:
        cfg = load_config(self.config_path)
        stop_file = cfg.project_root / cfg.risk.kill_switch_file
        if stop_file.exists():
            stop_file.unlink()
        self.stop_var.set("")
        self._refresh_status()

    def _open_output(self) -> None:
        out = Path(self.output_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(out))  # noqa: S606 - mở thư mục local do người dùng bấm nút
        else:
            messagebox.showinfo("Thư mục output", str(out))

    def _open_env(self) -> None:
        env_path = Path(self.config_path).resolve().parent / ".env"
        if not env_path.exists():
            messagebox.showinfo("Chưa có file .env", "Chưa có file .env. Copy .env.example thành .env trước rồi mở lại.")
            return
        if os.name == "nt":
            os.system(f'notepad "{env_path}"')
        else:
            messagebox.showinfo("File .env", str(env_path))

    def _save_config(self) -> None:
        import yaml

        try:
            path = Path(self.config_path)
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            symbol = self.cfg_entries["symbol"].get().strip()
            timeframe = self.cfg_entries["timeframe"].get().strip().upper()
            if not symbol:
                raise ValueError("Symbol không được để trống.")
            if timeframe not in TIMEFRAMES:
                raise ValueError(f"Khung thời gian phải là một trong: {', '.join(TIMEFRAMES)}.")
            risk = dict(data.get("risk") or {})
            for field_name, (low, high) in EDITABLE_RISK_FIELDS.items():
                value = float(self.cfg_entries[field_name].get())
                if value < low or value > high:
                    raise ValueError(f"{field_name} phải trong khoảng {low}–{high}.")
                risk[field_name] = value
            if risk["min_sl_distance_usd"] >= risk["max_sl_distance_usd"]:
                raise ValueError("Khoảng cách SL tối thiểu phải nhỏ hơn tối đa.")
            data["symbol"] = symbol
            data["timeframe"] = timeframe
            data["risk"] = risk
            data["max_open_positions"] = int(self.cfg_entries["max_open_positions"].get())
            data["loop_interval_seconds"] = int(self.cfg_entries["loop_interval_seconds"].get())
            data["news_enabled"] = bool(self.cfg_checks["news_enabled"].get())
            data["sentiment_enabled"] = bool(self.cfg_checks["sentiment_enabled"].get())
            backup = path.with_suffix(".yaml.bak")
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            self.config_msg_var.set("Đã lưu cấu hình. Bản sao cũ ở config.yaml.bak.")
            self._refresh_status()
        except Exception as exc:
            messagebox.showerror("Không lưu được cấu hình", str(exc))

    def _show_agent_detail(self, name: str) -> None:
        view = self._current_views.get(name)
        cfg = load_config(self.config_path)
        agent_cfg = cfg.agents.get(name, {})
        provider = agent_cfg.get("provider", "")
        model = (cfg.providers.get(provider, {}) or {}).get("model", "")
        detail = tk.Toplevel(self)
        detail.title(f"{name} — {AGENT_HOW[name]['role']} làm việc như thế nào")
        detail.geometry("860x640")
        text = tk.Text(detail, wrap="word")
        text.pack(fill="both", expand=True, padx=8, pady=8)
        parts = [
            f"AI: {name} — {AGENT_HOW[name]['role']}",
            f"Nhà cung cấp / model: {provider} / {model}",
            f"Cách làm việc: {AGENT_HOW[name]['how']}",
            "",
            "1) Đầu vào: cùng một snapshot cho mọi AI — giá bid/ask, chỉ báo, tin tức, sentiment, danh mục. Xem phần dữ liệu trong cửa sổ chính, hoặc file nhật ký của phiên.",
            "2) Lệnh hệ thống (system prompt) gửi cho AI:",
            SYSTEM_PROMPTS.get(name, "(Trưởng phòng dùng prompt riêng trong orchestrator.)"),
            "",
            "3) Đầu ra bắt buộc: DUY NHẤT 1 JSON với direction, confidence, entry, stop_loss, take_profit (giá USD), reasoning, invalidation, evidence. JSON lỗi thì coi như HOLD.",
        ]
        if self._current_snapshot:
            parts += ["", "4) Snapshot đã gửi trong phiên gần nhất (rút gọn):",
                      self._current_snapshot.get("prompt_text", "")[:3000]]
        if view:
            parts += ["", "5) Kết quả của AI này trong phiên gần nhất:",
                      f"Hướng: {view['direction']}, tin cậy: {view['confidence']}, "
                      f"entry: {view['entry']}, SL: {view['stop_loss']}, TP: {view['take_profit']}",
                      f"Lý do: {view['reasoning']}",
                      f"Điểm vô hiệu: {view['invalidation']}",
                      f"Bằng chứng: {view['evidence']}",
                      "", "Câu trả lời thô của AI:", view.get("raw_text") or view.get("raw", "")]
        else:
            parts.append("\nAI này chưa chạy trong phiên gần nhất.")
        text.insert("1.0", "\n".join(str(p) for p in parts))
        text.config(state="disabled")

    # ---------- Cập nhật giao diện từ sự kiện ----------
    def _set_card(self, name: str, status: str, direction: str, confidence: float, reasoning: str) -> None:
        card = self.cards.get(name)
        if not card:
            return
        card["status"].config(text=status)
        card["dir"].config(text=direction, foreground="#1e8449" if direction == "BUY" else "#c0392b" if direction == "SELL" else "#5d6d7e")
        card["conf"].config(value=max(0.0, min(1.0, float(confidence or 0.0))))
        card["reason"].config(text=reasoning or "")

    def _set_step(self, index: int) -> None:
        for i, lbl in enumerate(self.step_labels):
            if i < index:
                lbl.config(text="● " + STEPS[i], foreground="#1e8449")
            elif i == index:
                lbl.config(text="▶ " + STEPS[i], foreground="#1a5276")
            else:
                lbl.config(text="○ " + STEPS[i], foreground="#5d6d7e")

    def _set_text(self, widget: tk.Text, content: str) -> None:
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.config(state="disabled")

    def _show_snapshot(self, snap: dict) -> None:
        self._current_snapshot = snap
        self.price_var.set(f"Giá {snap['price']} • Spread {snap['spread']} USD • {snap['source']} lúc {snap.get('captured_at') or 'không rõ'}")
        ind = snap.get("indicators", {})
        lines = [
            f"Symbol: {snap['symbol']} ({snap['timeframe']}), nguồn: {snap['source']}",
            f"Bid {snap['bid']} / Ask {snap['ask']} / Spread {snap['spread']} USD",
            f"RSI14: {ind.get('rsi14')}, ATR14: {ind.get('atr14_usd')} USD, EMA20: {ind.get('ema20')}, EMA50: {ind.get('ema50')}",
            f"Bollinger: trên {ind.get('bollinger_upper_usd')}, giữa {ind.get('bollinger_middle_usd')}, dưới {ind.get('bollinger_lower_usd')}",
            f"Hỗ trợ: {ind.get('support')}, kháng cự: {ind.get('resistance')}",
            f"Vị thế mở (symbol): {snap['open_positions']}, lỗ trong ngày: {snap['daily_loss_usd']} USD",
            f"Số dư: {snap['account_balance']} USD, equity: {snap['account_equity']} USD",
            "",
            "TIN TỨC:", snap.get("news_text") or "Chưa có tin tức.",
            "",
            "SENTIMENT:", snap.get("sentiment_text") or "Chưa có dữ liệu sentiment.",
            "",
            "DANH MỤC:", json.dumps(snap.get("portfolio") or {}, ensure_ascii=False, indent=2),
            "",
            snap.get("notes") or "",
        ]
        self._set_text(self.data_text, "\n".join(str(x) for x in lines))

    def _show_result(self, result: dict) -> None:
        self._show_snapshot(result["snapshot"])
        for view in result["views"]:
            self._current_views[view["name"]] = view
            self._set_card(view["name"], "Đã xong", view["direction"], view["confidence"], view["reasoning"])
        chief = result["chief"]
        self._current_views["chief"] = chief
        self._set_card("chief", "Đã xong", chief["direction"], chief["confidence"], chief["reasoning"])
        risk = result["risk"]
        self._set_text(self.chief_text,
                       f"Hướng: {chief['direction']}, tin cậy: {chief['confidence']}\n"
                       f"Entry: {chief['entry']} USD, SL: {chief['stop_loss']} USD, TP: {chief['take_profit']} USD\n"
                       f"Lý do: {chief['reasoning']}\nĐiểm vô hiệu: {chief['invalidation']}\n"
                       f"Bằng chứng: {chief['evidence']}")
        checks = "\n".join(f"{'✓' if c['passed'] else '✕'} {c['name']}: {c['detail']}" for c in risk.get("checks", []))
        self._set_text(self.checks_text, checks or risk["reason"])
        self.order_var.set(f"{result['order']}  •  Lot: {risk['lot']}  •  File: {result['saved']}")
        self._load_history()

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.phase_var.set(str(payload))
                elif kind == "step":
                    step_no = int(payload)
                    self._set_step(step_no)
                    if step_no == 3:
                        for name in ANALYST_NAMES:
                            self._set_card(name, "Đang tranh luận...", self.cards[name]["dir"].cget("text"),
                                           self.cards[name]["conf"].cget("value"), self.cards[name]["reason"].cget("text"))
                    elif step_no == 4:
                        self._set_card("chief", "Đang tổng hợp...", "—", 0.0, AGENT_HOW["chief"]["how"])
                elif kind == "snapshot":
                    self._show_snapshot(payload)
                elif kind == "view":
                    self._current_views[payload["name"]] = payload
                    status = "Đã xong vòng 2" if payload.get("round") == 2 else "Đã xong"
                    self._set_card(payload["name"], status, payload["direction"], payload["confidence"], payload["reasoning"])
                elif kind == "result":
                    self._show_result(payload)
                elif kind == "error":
                    messagebox.showerror("Lỗi khi chạy phiên", str(payload))
                    self.phase_var.set(f"Lỗi: {payload}")
                elif kind == "finished":
                    self._refresh_buttons()
                    self._refresh_status()
        except queue.Empty:
            pass
        self.after(200, self._poll_events)

    def _refresh_buttons(self) -> None:
        running = self.runner.is_running()
        state = "disabled" if running else "normal"
        self.run_btn.config(state=state)
        self.loop_btn.config(state=state)

    def _refresh_status(self) -> None:
        try:
            cfg = load_config(self.config_path)
        except Exception:
            return
        self.mode_var.set("LIVE" if cfg.mode == "live" else "DEMO" if cfg.mode == "demo" else "DRY-RUN")
        stop_file = cfg.project_root / cfg.risk.kill_switch_file
        self.stop_var.set("ĐÃ DỪNG (file STOP)" if stop_file.exists() else "")
        keys = []
        for env_name, label in (("OPENAI_API_KEY", "OpenAI"), ("GEMINI_API_KEY", "Gemini"), ("ANTHROPIC_API_KEY", "Claude")):
            keys.append(f"{label}: {'● có' if os.getenv(env_name, '').strip() else '○ chưa có'}")
        self.keys_var.set("Key: " + "  •  ".join(keys) + "  (chỉ hiện có/không có, không hiện giá trị)")
        self._refresh_buttons()

    def _load_history(self) -> None:
        out = Path(self.output_dir)
        self.history_list.delete(0, "end")
        self._history_data = []
        if not out.exists():
            return
        for path in sorted(out.glob("session_*.json"), reverse=True)[:30]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            self._history_data.append((path, data))
            chief = data.get("chief", {})
            self.history_list.insert("end", f"{data.get('timestamp_utc', '')} — {chief.get('direction', '')}")

    def _on_history_select(self, _event) -> None:
        sel = self.history_list.curselection()
        if not sel:
            return
        _path, data = self._history_data[sel[0]]
        self._set_text(self.history_text, json.dumps(data, ensure_ascii=False, indent=2))
        views = data.get("views", [])
        for view in views:
            self._current_views[view["name"]] = view
            self._set_card(view["name"], "Xem lại từ nhật ký", view["direction"], view["confidence"], view["reasoning"])
        risk = data.get("risk", {})
        checks = "\n".join(f"{'✓' if c['passed'] else '✕'} {c['name']}: {c['detail']}" for c in risk.get("checks", []))
        self._set_text(self.checks_text, checks or risk.get("reason", ""))
        self.order_var.set(str(data.get("order", "")))
        self._set_text(self.data_text, str(data.get("snapshot", "")) + "\n\nTIN TỨC:\n" + str(data.get("news_text", "")) + "\n\nSENTIMENT:\n" + str(data.get("sentiment_text", "")))

    def _on_close(self) -> None:
        self.runner.stop()
        self.destroy()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="App desktop cho phòng trade AI")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args(argv)
    try:
        app = DesktopApp(args.config, output_dir=args.output_dir)
    except tk.TclError as exc:
        print(f"Không mở được cửa sổ Tkinter: {exc}")
        print("Trên Windows, cài Python từ python.org (bản có kèm Tkinter) rồi chạy lại.")
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
