"""App desktop Terminal Pro (Tkinter, chỉ dùng thư viện chuẩn) cho phòng trade AI.

Bản vẽ theo ảnh demo Terminal Pro mà Thái đã chốt (lựa chọn 2): nền navy
tối, tiêu đề teal nhạt, nút chính xanh dương, dừng khẩn cấp đỏ, Gỡ STOP cam,
LONG xanh lá, SHORT đỏ, HOLD/CAUTION vàng. Bảng màu nằm ở PALETTE ngay dưới
đây để Thái tự sửa 1 chỗ là đổi cả app — không sửa màu rải rác từng widget.

Bố cục 3 tab, thanh tab ở DƯỚI cùng (như ảnh trước), thanh market watch ở
trên cùng: tab 1 Phòng trade (biểu đồ nến + AI feed + order ticket), tab 2
Nhật ký & lịch sử, tab 3 Cấu hình & an toàn. Khối quyết định trưởng phòng
dùng panel tối như các panel khác, KHÔNG dùng nền nâu/vàng nâu.

Biểu đồ + market watch chạy theo thời gian thực: luồng LiveFeed hỏi tick MT5
mỗi 2 giây và nến mới mỗi ~10 giây, cập nhật giá/spread/số dư/equity và nến
cuối đang chạy. MT5 Python API không tự đẩy giá, nên đây là hỏi theo chu kỳ,
không phải từng mili giây. Luồng realtime tạm dừng trong lúc 1 phiên AI đang
chạy (để không tranh kết nối MT5 với phiên), chạy xong tự nối lại.

Chạy bằng cách bấm đúp file ``run_app.bat`` ở thư mục gốc của dự án, hoặc:
    py -3.13 main.py --app
    py -3.13 -m trading_room.desktop_app

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
from tkinter import messagebox

from .agents import SYSTEM_PROMPTS
from .config import load_config
from .executor import get_executor
from .journal import save_session
from .market_snapshot import load_snapshot
from .mt5_data import MT5DataProvider
from .orchestrator import ANALYST_NAMES, enrich_snapshot, run_room
from .web_ui import EDITABLE_RISK_FIELDS, TIMEFRAMES

AGENT_NAMES = ANALYST_NAMES + ["chief"]

# Bảng màu Terminal Pro — Thái tự sửa ở đây, mọi widget lấy màu từ đây.
PALETTE = {
    "bg": "#0B1220",        # nền cửa sổ: navy rất tối
    "panel": "#141E30",     # nền panel
    "panel2": "#1B2740",    # nền panel nổi hơn 1 bậc / ô nhập liệu
    "border": "#24344D",    # viền panel
    "text": "#F1F5F9",      # chữ chính
    "muted": "#94A3B8",     # chữ phụ
    "cyan": "#5EEAD4",      # tiêu đề, nhấn teal nhạt
    "blue": "#2563EB",      # nút chính, tab đang chọn
    "green": "#22C55E",     # BUY / LONG / PASS
    "red": "#EF4444",       # SELL / SHORT / FAIL
    "amber": "#F59E0B",     # HOLD / NEUTRAL / CAUTION
    "orange": "#EA580C",    # nút Gỡ STOP
    "danger": "#DC2626",    # nút dừng khẩn cấp
    "purple": "#A855F7",    # badge Portfolio Manager
    "grey": "#64748B",      # trạng thái chờ / chưa có
}

AGENT_HOW = {
    "technical": {
        "role": "Phân tích kỹ thuật",
        "how": "Đọc xu hướng, cấu trúc giá, hỗ trợ/kháng cự, RSI, MACD, ATR, EMA và Bollinger trong snapshot. Không đọc tin vĩ mô.",
        "badge": PALETTE["blue"],
    },
    "fundamental": {
        "role": "Phân tích cơ bản",
        "how": "Chỉ dùng tin tức và lịch kinh tế đã lấy được trong snapshot (lịch ForexFactory, RSS Fed). Không có tin mới thì nói rõ, không bịa số liệu.",
        "badge": PALETTE["amber"],
    },
    "sentiment": {
        "role": "Phân tích tâm lý và dòng tiền",
        "how": "Đọc COT vàng (ghi rõ ngày báo cáo, luôn cũ vài ngày) và điểm từ khóa tiêu đề tin tức. Điểm từ khóa không phải Fear & Greed thật.",
        "badge": PALETTE["grey"],
    },
    "contrarian": {
        "role": "AI phản biện",
        "how": "Tìm điểm yếu nhất của phe đa số, kịch bản ngược chiều và điểm vô hiệu. Không đồng thuận cho có.",
        "badge": PALETTE["red"],
    },
    "risk_analyst": {
        "role": "Quản trị rủi ro (tư vấn)",
        "how": "Đánh giá spread, biến động, giờ tin và hậu quả nếu stop bị quét. Không quyết lot, code sẽ quyết.",
        "badge": PALETTE["amber"],
    },
    "portfolio_manager": {
        "role": "Quản lý danh mục",
        "how": "Đánh giá exposure toàn tài khoản, mức tập trung vào vàng và rủi ro nếu cộng thêm 1 lệnh. Không quyết lot, code sẽ quyết.",
        "badge": PALETTE["purple"],
    },
    "chief": {
        "role": "Trưởng phòng giao dịch",
        "how": "Đọc tất cả ý kiến vòng 2, cân nhắc cả phản biện và danh mục, ra 1 quyết định cuối duy nhất. Mâu thuẫn mạnh hoặc bằng chứng yếu thì HOLD.",
        "badge": PALETTE["cyan"],
    },
}

STEPS = [
    "1. Khởi tạo",
    "2. Kết nối",
    "3. Tải dữ liệu",
    "4. Chuẩn bị chỉ báo",
    "5. AI thảo luận",
    "6. Kiểm định rủi ro",
    "7. Sẵn sàng",
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
        "candles": list(getattr(snapshot, "candles", []) or []),
    }


def _build_snapshot(cfg, source: str, snapshot_path: str):
    if source == "mt5":
        provider = MT5DataProvider(cfg)
        try:
            return provider.build_snapshot()
        finally:
            provider.close()
    return load_snapshot(snapshot_path)


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _direction_color(direction: str) -> str:
    if direction == "BUY":
        return PALETTE["green"]
    if direction == "SELL":
        return PALETTE["red"]
    if direction == "HOLD":
        return PALETTE["amber"]
    return PALETTE["grey"]


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


class LiveFeed:
    """Hỏi tick + nến MT5 theo chu kỳ cho market watch và biểu đồ realtime.

    MT5 Python API không tự đẩy giá sang, nên luồng này hỏi lại mỗi 2 giây
    (tick) và mỗi ~10 giây (nến + tài khoản). Khi phiên AI đang chạy thì
    luồng tạm dừng để không tranh kết nối MT5; chạy xong tự nối lại.
    """

    TICK_INTERVAL = 2.0
    CANDLES_EVERY = 5  # số lần hỏi tick thì hỏi nến 1 lần

    def __init__(self, config_path: str, events: queue.Queue):
        self.config_path = config_path
        self.events = events
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._reconnect = threading.Event()
        self._thread: threading.Thread | None = None
        self._error_sent = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._reconnect.set()
        self._paused.clear()

    def _run(self) -> None:
        try:
            cfg = load_config(self.config_path)
            provider = MT5DataProvider(cfg)
        except Exception as exc:
            self.events.put(("live_error", str(exc)))
            return
        try:
            provider.connect()
        except Exception as exc:
            self.events.put(("live_error", str(exc)))
            return
        count = 0
        while not self._stop.is_set():
            if self._paused.is_set():
                if self._stop.wait(timeout=0.2):
                    break
                continue
            if self._reconnect.is_set():
                self._reconnect.clear()
                try:
                    provider.close()
                    provider.connect()
                except Exception:
                    pass
            try:
                tick = provider.get_tick()
                if tick is None:
                    # Tick rỗng: nối lại 1 lần, lần sau vẫn rỗng thì chờ kỳ sau.
                    try:
                        provider.close()
                        provider.connect()
                        tick = provider.get_tick()
                    except Exception:
                        tick = None
                if tick is not None:
                    self._error_sent = False
                    payload = {"bid": tick["bid"], "ask": tick["ask"]}
                    count += 1
                    if count % self.CANDLES_EVERY == 1:
                        try:
                            payload["candles"] = provider.get_candles()
                            payload.update(provider.get_account())
                        except Exception:
                            pass
                    self.events.put(("tick", payload))
                elif not self._error_sent:
                    self._error_sent = True
                    self.events.put(("live_error", "Không đọc được tick MT5. Kiểm tra terminal MT5 có đang mở và đã đăng nhập không."))
            except Exception as exc:
                if not self._error_sent:
                    self._error_sent = True
                    self.events.put(("live_error", str(exc)))
                try:
                    provider.close()
                    provider.connect()
                except Exception:
                    pass
            if self._stop.wait(timeout=self.TICK_INTERVAL):
                break
        try:
            provider.close()
        except Exception:
            pass


class CandleChart(tk.Canvas):
    """Biểu đồ nến vẽ bằng Canvas, chỉ dùng thư viện chuẩn.

    Vẽ nến OHLC, đường EMA20/EMA50, hỗ trợ/kháng cự, cột volume và đường
    giá cuối. Nến cuối cập nhật theo tick mà không cần tải lại toàn bộ nến.
    """

    def __init__(self, master, **kwargs):
        super().__init__(master, bg=PALETTE["bg"], highlightthickness=0, **kwargs)
        self.candles: list[dict] = []
        self.support: list[float] = []
        self.resistance: list[float] = []
        self.visible = 100
        self.bind("<Configure>", lambda _e: self.draw())

    def set_candles(self, candles: list[dict], support=None, resistance=None) -> None:
        self.candles = [dict(c) for c in (candles or [])]
        self.support = list(support or [])
        self.resistance = list(resistance or [])
        self.draw()

    def update_tick(self, bid: float, ask: float) -> None:
        if not self.candles:
            return
        price = (bid + ask) / 2
        last = self.candles[-1]
        last["close"] = price
        last["high"] = max(last["high"], price)
        last["low"] = min(last["low"], price)
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        if width < 60 or height < 60:
            return
        if not self.candles:
            self.create_text(width / 2, height / 2, fill=PALETTE["muted"],
                             text="Chưa có nến — bấm “Làm mới MT5” hoặc “Chạy 1 phiên” (nguồn mt5).",
                             font=("", 10))
            return
        visible = self.candles[-self.visible:]
        plot_h = height * 0.78
        vol_h = height * 0.18
        vol_top = plot_h + 8
        lo = min(c["low"] for c in visible)
        hi = max(c["high"] for c in visible)
        for level in self.support + self.resistance:
            lo = min(lo, level)
            hi = max(hi, level)
        if hi <= lo:
            hi = lo + 1.0
        pad = (hi - lo) * 0.06
        hi += pad
        lo -= pad

        def y_of(price: float) -> float:
            return 8 + (hi - price) / (hi - lo) * (plot_h - 16)

        def x_of(i: int) -> float:
            return 8 + (i + 0.5) * (width - 16) / len(visible)

        step = (width - 16) / len(visible)
        body_w = max(2.0, step * 0.65)
        max_vol = max((c.get("volume", 0.0) or 0.0) for c in visible) or 1.0
        closes = [c["close"] for c in self.candles]
        ema20 = _ema_series(closes, 20)
        ema50 = _ema_series(closes, 50)
        offset = len(self.candles) - len(visible)
        for i, c in enumerate(visible):
            x = x_of(i)
            color = PALETTE["green"] if c["close"] >= c["open"] else PALETTE["red"]
            self.create_line(x, y_of(c["high"]), x, y_of(c["low"]), fill=color, width=1)
            top = y_of(max(c["open"], c["close"]))
            bottom = y_of(min(c["open"], c["close"]))
            self.create_rectangle(x - body_w / 2, top, x + body_w / 2, max(bottom, top + 1),
                                  fill=color, outline=color)
            vol = (c.get("volume", 0.0) or 0.0) / max_vol * vol_h
            self.create_rectangle(x - body_w / 2, vol_top + vol_h - vol, x + body_w / 2,
                                  vol_top + vol_h, fill=color, outline=color, stipple="gray50")
        for series, color in ((ema20, "#FACC15"), (ema50, "#FB923C")):
            points = []
            for i in range(len(visible)):
                points += [x_of(i), y_of(series[offset + i])]
            if len(points) >= 4:
                self.create_line(points, fill=color, width=1)
        for level in self.support:
            self.create_line(8, y_of(level), width - 8, y_of(level), fill="#3B82F6", dash=(4, 3))
            self.create_text(width - 10, y_of(level) - 8, text=f"{level:.2f}", fill="#3B82F6",
                             anchor="e", font=("", 8))
        for level in self.resistance:
            self.create_line(8, y_of(level), width - 8, y_of(level), fill=PALETTE["red"], dash=(4, 3))
            self.create_text(width - 10, y_of(level) - 8, text=f"{level:.2f}", fill=PALETTE["red"],
                             anchor="e", font=("", 8))
        last_price = visible[-1]["close"]
        self.create_line(8, y_of(last_price), width - 8, y_of(last_price), fill=PALETTE["cyan"], dash=(2, 2))
        self.create_text(width - 10, y_of(last_price) + 10, text=f"{last_price:.2f}",
                         fill=PALETTE["cyan"], anchor="e", font=("", 9, "bold"))
        self.create_text(10, height - 6, text="VOLUME", fill=PALETTE["muted"], anchor="sw", font=("", 8))


# ---------- Widget phụ: cùng bảng màu, dễ sửa ----------

def _panel(parent, **kwargs):
    frame = tk.Frame(parent, bg=PALETTE["panel"], highlightbackground=PALETTE["border"],
                     highlightthickness=1, **kwargs)
    return frame


def _label(parent, text: str, size: int = 10, bold: bool = False, color: str | None = None, **kwargs):
    return tk.Label(parent, text=text, bg=PALETTE["panel"], fg=color or PALETTE["text"],
                    font=("", size, "bold" if bold else "normal"), **kwargs)


def _title(parent, text: str):
    return tk.Label(parent, text=text, bg=PALETTE["panel"], fg=PALETTE["cyan"],
                    font=("", 11, "bold"), anchor="w")


def _button(parent, text: str, command, bg: str | None = None, fg: str | None = None, **kwargs):
    return tk.Button(parent, text=text, command=command,
                     bg=bg or PALETTE["panel2"], fg=fg or PALETTE["text"],
                     activebackground=PALETTE["border"], activeforeground=PALETTE["text"],
                     relief="flat", bd=1, padx=10, pady=6, cursor="hand2", **kwargs)


def _entry(parent, width: int = 26):
    return tk.Entry(parent, width=width, bg=PALETTE["panel2"], fg=PALETTE["text"],
                    insertbackground=PALETTE["text"], relief="flat", highlightbackground=PALETTE["border"],
                    highlightthickness=1)


def _text(parent, height: int = 10):
    widget = tk.Text(parent, height=height, wrap="word", bg=PALETTE["panel"],
                     fg=PALETTE["text"], insertbackground=PALETTE["text"],
                     relief="flat", highlightbackground=PALETTE["border"], highlightthickness=1)
    return widget


def _pill(parent, text: str, bg: str):
    return tk.Label(parent, text=text, bg=bg, fg="#0B1220" if bg in (PALETTE["green"], PALETTE["amber"], PALETTE["cyan"]) else PALETTE["text"],
                    font=("", 9, "bold"), padx=8, pady=2)


def _set_text(widget: tk.Text, content: str) -> None:
    widget.config(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", content)
    widget.config(state="disabled")


class DesktopApp(tk.Tk):
    def __init__(self, config_path: str = "config.yaml", output_dir: str = "output"):
        super().__init__()
        self.config_path = config_path
        self.output_dir = output_dir
        self.events: queue.Queue = queue.Queue()
        self.runner = SessionRunner(config_path, output_dir, self.events)
        self.live = LiveFeed(config_path, self.events)
        self.title("AI GOLD TRADING ROOM — TERMINAL PRO")
        self.geometry("1400x900")
        self.minsize(1100, 720)
        self.configure(bg=PALETTE["bg"])
        self._current_views: dict[str, dict] = {}
        self._current_snapshot: dict | None = None
        self._ref_close: float | None = None
        self._last_balance = 0.0
        self._build_widgets()
        self._refresh_status()
        self._load_history()
        self.after(200, self._poll_events)
        self.after(1000, self._tick_clock)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # Realtime chỉ chạy khi nguồn là mt5; nguồn json thì biểu đồ lấy nến
        # từ snapshot (nếu file JSON có nến) và không hỏi MT5.
        try:
            if load_config(config_path).data_source == "mt5":
                self.live.start()
        except Exception:
            pass

    # ---------- Xây giao diện ----------
    def _build_widgets(self) -> None:
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Hàng 0: tiêu đề app
        header = tk.Frame(self, bg=PALETTE["bg"])
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))
        tk.Label(header, text="◎  AI GOLD TRADING ROOM — TERMINAL PRO", bg=PALETTE["bg"],
                 fg=PALETTE["cyan"], font=("", 14, "bold")).pack(side="left")
        tk.Label(header, text="v2.1.3", bg=PALETTE["bg"], fg=PALETTE["muted"],
                 font=("", 10)).pack(side="left", padx=8)
        self.mode_var = tk.StringVar(value="DRY-RUN")
        tk.Label(header, textvariable=self.mode_var, bg=PALETTE["amber"], fg="#0B1220",
                 font=("", 10, "bold"), padx=8, pady=2).pack(side="right")

        # Hàng 1: market watch + tiến trình 7 bước
        bar = _panel(self)
        bar.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        for col, weight in enumerate((1, 2, 1, 1, 1, 1, 2, 3)):
            bar.grid_columnconfigure(col, weight=weight)
        tk.Label(bar, text="MARKET WATCH:", bg=PALETTE["panel"], fg=PALETTE["muted"],
                 font=("", 9, "bold")).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.symbol_var = tk.StringVar(value="XAUUSD")
        tk.Label(bar, textvariable=self.symbol_var, bg=PALETTE["panel"], fg=PALETTE["text"],
                 font=("", 12, "bold")).grid(row=1, column=0, sticky="w", padx=8)
        self.price_var = tk.StringVar(value="—")
        tk.Label(bar, textvariable=self.price_var, bg=PALETTE["panel"], fg=PALETTE["text"],
                 font=("", 16, "bold")).grid(row=0, column=1, rowspan=2, sticky="w")
        self.change_var = tk.StringVar(value="")
        self.change_lbl = tk.Label(bar, textvariable=self.change_var, bg=PALETTE["panel"],
                                   fg=PALETTE["green"], font=("", 11, "bold"))
        self.change_lbl.grid(row=0, column=2, rowspan=2, sticky="w")
        self.spread_var = tk.StringVar(value="SPREAD: —")
        tk.Label(bar, textvariable=self.spread_var, bg=PALETTE["panel"], fg=PALETTE["text"],
                 font=("", 11)).grid(row=0, column=3, rowspan=2, sticky="w")
        self.bal_var = tk.StringVar(value="BAL: —")
        tk.Label(bar, textvariable=self.bal_var, bg=PALETTE["panel"], fg=PALETTE["text"],
                 font=("", 11)).grid(row=0, column=4, rowspan=2, sticky="w")
        self.eq_var = tk.StringVar(value="EQUITY: —")
        tk.Label(bar, textvariable=self.eq_var, bg=PALETTE["panel"], fg=PALETTE["green"],
                 font=("", 11, "bold")).grid(row=0, column=5, rowspan=2, sticky="w")
        self.mt5_var = tk.StringVar(value="MT5: CHƯA KẾT NỐI")
        self.mt5_lbl = tk.Label(bar, textvariable=self.mt5_var, bg=PALETTE["panel"],
                                fg=PALETTE["amber"], font=("", 10, "bold"))
        self.mt5_lbl.grid(row=0, column=6, rowspan=2, sticky="w")
        steps_frame = tk.Frame(bar, bg=PALETTE["panel"])
        steps_frame.grid(row=0, column=7, rowspan=2, sticky="e", padx=8)
        tk.Label(steps_frame, text="PROGRESS:", bg=PALETTE["panel"], fg=PALETTE["muted"],
                 font=("", 9, "bold"), anchor="w").pack(fill="x")
        pills = tk.Frame(steps_frame, bg=PALETTE["panel"])
        pills.pack(fill="x")
        self.step_labels: list[tk.Label] = []
        for label in STEPS:
            lbl = tk.Label(pills, text=label, bg=PALETTE["panel2"], fg=PALETTE["muted"],
                           font=("", 8), padx=5, pady=2)
            lbl.pack(side="left", padx=2)
            self.step_labels.append(lbl)

        # Hàng 2: nội dung 3 tab (chỉ hiện 1 tab, đổi bằng thanh tab dưới cùng)
        self.content = tk.Frame(self, bg=PALETTE["bg"])
        self.content.grid(row=2, column=0, sticky="nsew", padx=10, pady=4)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        self.tab_frames: dict[str, tk.Frame] = {}
        self._build_tab1()
        self._build_tab2()
        self._build_tab3()

        # Hàng 3: thanh tab ở DƯỚI cùng (như ảnh trước)
        tabbar = tk.Frame(self, bg=PALETTE["bg"])
        tabbar.grid(row=3, column=0, pady=(2, 0))
        self.tab_buttons: dict[str, tk.Button] = {}
        for key, label in (("trade", "Phòng trade"), ("history", "Nhật ký & lịch sử"),
                           ("config", "Cấu hình & an toàn")):
            btn = _button(tabbar, label, lambda k=key: self.show_tab(k))
            btn.pack(side="left", padx=4, ipadx=10, ipady=4)
            self.tab_buttons[key] = btn
        self.show_tab("trade")

        # Hàng 4: thanh trạng thái dưới cùng
        status = tk.Frame(self, bg=PALETTE["bg"])
        status.grid(row=4, column=0, sticky="ew", padx=10, pady=(4, 8))
        self.keys_var = tk.StringVar(value="Key: đang kiểm tra...")
        tk.Label(status, textvariable=self.keys_var, bg=PALETTE["bg"], fg=PALETTE["muted"],
                 font=("", 9)).pack(side="left")
        self.clock_var = tk.StringVar(value="")
        tk.Label(status, textvariable=self.clock_var, bg=PALETTE["bg"], fg=PALETTE["muted"],
                 font=("", 9)).pack(side="right")
        self.phase_var = tk.StringVar(value="Chưa chạy phiên nào.")
        tk.Label(status, textvariable=self.phase_var, bg=PALETTE["bg"], fg=PALETTE["cyan"],
                 font=("", 9)).pack(side="left", padx=16)

    def show_tab(self, key: str) -> None:
        for name, frame in self.tab_frames.items():
            frame.grid_forget()
        self.tab_frames[key].grid(row=0, column=0, sticky="nsew")
        for name, btn in self.tab_buttons.items():
            if name == key:
                btn.config(bg=PALETTE["blue"], fg="white")
            else:
                btn.config(bg=PALETTE["panel2"], fg=PALETTE["text"])

    # ----- Tab 1: Phòng trade -----
    def _build_tab1(self) -> None:
        tab = tk.Frame(self.content, bg=PALETTE["bg"])
        self.tab_frames["trade"] = tab
        tab.grid_rowconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=0)
        tab.grid_columnconfigure(0, weight=38)
        tab.grid_columnconfigure(1, weight=34)
        tab.grid_columnconfigure(2, weight=28)

        # Cột trái: biểu đồ + chỉ báo
        left = _panel(tab)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        self.chart_title_var = tk.StringVar(value="BIỂU ĐỒ — XAUUSD • M15 • DRY-RUN")
        tk.Label(left, textvariable=self.chart_title_var, bg=PALETTE["panel"], fg=PALETTE["cyan"],
                 font=("", 11, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 0))
        chart_row = tk.Frame(left, bg=PALETTE["panel"])
        chart_row.grid(row=0, column=0, sticky="e", padx=8, pady=(6, 0))
        self.zoom_var = tk.StringVar(value="100 nến")
        tk.Label(chart_row, textvariable=self.zoom_var, bg=PALETTE["panel"], fg=PALETTE["muted"],
                 font=("", 9)).pack(side="left", padx=4)
        _button(chart_row, "−", lambda: self._change_zoom(-40), bg=PALETTE["panel2"]).pack(side="left", padx=2)
        _button(chart_row, "+", lambda: self._change_zoom(40), bg=PALETTE["panel2"]).pack(side="left", padx=2)
        self.chart = CandleChart(left)
        self.chart.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        tk.Label(left, text="CHỈ BÁO KỸ THUẬT", bg=PALETTE["panel"], fg=PALETTE["cyan"],
                 font=("", 11, "bold"), anchor="w").grid(row=2, column=0, sticky="ew", padx=8)
        self.indicators_text = _text(left, height=5)
        self.indicators_text.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 8))
        _set_text(self.indicators_text, "Chưa có chỉ báo.\nBấm “Làm mới MT5” hoặc “Chạy 1 phiên”.")
        self.source_var = tk.StringVar(value="mt5")
        self.mock_var = tk.BooleanVar(value=True)

        # Cột giữa: AI feed + trưởng phòng (panel tối, không nền nâu)
        center = _panel(tab)
        center.grid(row=0, column=1, sticky="nsew", padx=4)
        center.grid_rowconfigure(1, weight=1)
        center.grid_columnconfigure(0, weight=1)
        feed_head = tk.Frame(center, bg=PALETTE["panel"])
        feed_head.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        tk.Label(feed_head, text="LIVE AI DEBATE FEED", bg=PALETTE["panel"], fg=PALETTE["cyan"],
                 font=("", 11, "bold")).pack(side="left")
        self.feed_sync_var = tk.StringVar(value="")
        tk.Label(feed_head, textvariable=self.feed_sync_var, bg=PALETTE["panel"],
                 fg=PALETTE["muted"], font=("", 9)).pack(side="right")
        canvas = tk.Canvas(center, bg=PALETTE["panel"], highlightthickness=0)
        scrollbar = tk.Scrollbar(center, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=1, column=0, sticky="nsew", padx=4)
        scrollbar.grid(row=1, column=1, sticky="ns")
        scroll_inner = tk.Frame(canvas, bg=PALETTE["panel"])
        scroll_win = canvas.create_window((0, 0), window=scroll_inner, anchor="nw")
        scroll_inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(scroll_win, width=e.width - 8))
        self.cards: dict[str, dict] = {}
        for name in AGENT_NAMES:
            card = tk.Frame(scroll_inner, bg=PALETTE["panel2"], highlightbackground=PALETTE["border"],
                            highlightthickness=1)
            card.pack(fill="x", padx=4, pady=3)
            head = tk.Frame(card, bg=PALETTE["panel2"])
            head.pack(fill="x", padx=6, pady=(4, 0))
            tk.Label(head, text=f"[{name.upper()}]", bg=AGENT_HOW[name]["badge"], fg="#0B1220",
                     font=("", 9, "bold"), padx=6, pady=1).pack(side="left")
            dir_lbl = tk.Label(head, text="—", bg=PALETTE["panel2"], fg=PALETTE["muted"],
                               font=("", 10, "bold"))
            dir_lbl.pack(side="right")
            status_lbl = tk.Label(card, text="Chờ chạy", bg=PALETTE["panel2"], fg=PALETTE["muted"],
                                  font=("", 9), anchor="w")
            status_lbl.pack(fill="x", padx=6)
            conf_canvas = tk.Canvas(card, height=10, bg=PALETTE["panel"], highlightthickness=0)
            conf_canvas.pack(fill="x", padx=6, pady=2)
            reason_lbl = tk.Label(card, text=AGENT_HOW[name]["how"], bg=PALETTE["panel2"],
                                  fg=PALETTE["text"], font=("", 9), wraplength=430, justify="left", anchor="w")
            reason_lbl.pack(fill="x", padx=6, pady=(0, 2))
            detail_btn = _button(card, "Xem cách AI này làm việc",
                                 lambda n=name: self._show_agent_detail(n))
            detail_btn.pack(anchor="e", padx=6, pady=(0, 4))
            self.cards[name] = {"frame": card, "status": status_lbl, "dir": dir_lbl,
                                "conf": conf_canvas, "reason": reason_lbl}
        chief = tk.Frame(scroll_inner, bg=PALETTE["panel2"], highlightbackground=PALETTE["cyan"],
                         highlightthickness=1)
        chief.pack(fill="x", padx=4, pady=6)
        self.chief_title_var = tk.StringVar(value="★ CHIEF DECISION — CHƯA CÓ KẾT LUẬN")
        tk.Label(chief, textvariable=self.chief_title_var, bg=PALETTE["panel2"], fg=PALETTE["cyan"],
                 font=("", 11, "bold"), anchor="w").pack(fill="x", padx=8, pady=(6, 0))
        self.chief_text = tk.Text(chief, height=5, wrap="word", bg=PALETTE["panel2"],
                                  fg=PALETTE["text"], relief="flat")
        self.chief_text.pack(fill="x", padx=8, pady=4)
        _set_text(self.chief_text, "Chưa có kết luận trưởng phòng.")

        # Cột phải: order ticket + kiểm tra rủi ro + tin tức/COT
        right = _panel(tab)
        right.grid(row=0, column=2, sticky="nsew", padx=(4, 0))
        right.grid_rowconfigure(2, weight=1)
        right.grid_columnconfigure(0, weight=1)
        tk.Label(right, text="ORDER TICKET — XAUUSD", bg=PALETTE["panel"], fg=PALETTE["cyan"],
                 font=("", 11, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        self.ticket_vars = {}
        for i, (key, label) in enumerate((("entry", "Entry Price:"), ("sl", "Stop Loss:"),
                                          ("tp", "Take Profit:"), ("lot", "Lot Size:"))):
            row_frame = tk.Frame(right, bg=PALETTE["panel"])
            row_frame.grid(row=i + 1, column=0, sticky="ew", padx=8, pady=1)
            row_frame.grid_columnconfigure(1, weight=1)
            tk.Label(row_frame, text=label, bg=PALETTE["panel"], fg=PALETTE["text"],
                     font=("", 10)).grid(row=0, column=0, sticky="w")
            var = tk.StringVar(value="—")
            self.ticket_vars[key] = var
            tk.Label(row_frame, textvariable=var, bg=PALETTE["panel2"], fg=PALETTE["text"],
                     font=("", 10, "bold"), anchor="e", width=16).grid(row=0, column=1, sticky="e")
        self.risk_est_var = tk.StringVar(value="RỦI RO ƯỚC TÍNH: —")
        tk.Label(right, textvariable=self.risk_est_var, bg=PALETTE["panel"], fg=PALETTE["muted"],
                 font=("", 10, "bold"), anchor="w").grid(row=5, column=0, sticky="ew", padx=8, pady=4)
        tk.Label(right, text="KIỂM TRA RỦI RO", bg=PALETTE["panel"], fg=PALETTE["cyan"],
                 font=("", 11, "bold"), anchor="w").grid(row=6, column=0, sticky="ew", padx=8)
        self.checks_text = _text(right, height=8)
        self.checks_text.grid(row=7, column=0, sticky="nsew", padx=8, pady=2)
        _set_text(self.checks_text, "Chưa chạy kiểm tra nào.")
        tk.Label(right, text="TIN TỨC & COT SENTIMENT", bg=PALETTE["panel"], fg=PALETTE["cyan"],
                 font=("", 11, "bold"), anchor="w").grid(row=8, column=0, sticky="ew", padx=8, pady=(4, 0))
        self.news_text = _text(right, height=8)
        self.news_text.grid(row=9, column=0, sticky="nsew", padx=8, pady=(0, 8))
        _set_text(self.news_text, "Chưa có tin tức / COT.\nBấm “Lấy tin + COT”.")
        self.order_var = tk.StringVar(value="Chưa có lệnh.")
        tk.Label(right, textvariable=self.order_var, bg=PALETTE["panel"], fg=PALETTE["text"],
                 font=("", 10, "bold"), wraplength=320, anchor="w", justify="left").grid(
                     row=10, column=0, sticky="ew", padx=8, pady=(0, 6))

        # Hàng nút điều khiển của tab 1
        controls = tk.Frame(tab, bg=PALETTE["bg"])
        controls.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        tk.Label(controls, text="Nguồn:", bg=PALETTE["bg"], fg=PALETTE["muted"]).pack(side="left")
        for value, label in (("mt5", "MT5"), ("json", "JSON thử")):
            tk.Radiobutton(controls, text=label, variable=self.source_var, value=value,
                           bg=PALETTE["bg"], fg=PALETTE["text"], selectcolor=PALETTE["panel2"],
                           activebackground=PALETTE["bg"], activeforeground=PALETTE["text"]).pack(side="left")
        tk.Checkbutton(controls, text="AI giả (không tốn phí, không cần key)", variable=self.mock_var,
                       bg=PALETTE["bg"], fg=PALETTE["text"], selectcolor=PALETTE["panel2"],
                       activebackground=PALETTE["bg"], activeforeground=PALETTE["text"]).pack(side="left", padx=6)
        self.run_btn = _button(controls, "▶  Chạy 1 phiên", lambda: self._start_run(False), bg=PALETTE["blue"], fg="white")
        self.run_btn.pack(side="left", padx=4)
        self.loop_btn = _button(controls, "↻  Chạy lặp lại", lambda: self._start_run(True), bg=PALETTE["blue"], fg="white")
        self.loop_btn.pack(side="left", padx=4)
        _button(controls, "Làm mới MT5", lambda: self.runner.preview(self.source_var.get(), False)).pack(side="left", padx=4)
        _button(controls, "Lấy tin + COT", lambda: self.runner.preview(self.source_var.get(), True)).pack(side="left", padx=4)
        _button(controls, "⛔  DỪNG KHẨN CẤP", self._create_stop, bg=PALETTE["danger"], fg="white").pack(side="left", padx=4)
        _button(controls, "Gỡ STOP", self._clear_stop, bg=PALETTE["orange"], fg="#0B1220").pack(side="left", padx=4)
        self.stop_var = tk.StringVar(value="")
        tk.Label(controls, textvariable=self.stop_var, bg=PALETTE["bg"], fg=PALETTE["red"],
                 font=("", 10, "bold")).pack(side="left", padx=8)

    # ----- Tab 2: Nhật ký & lịch sử -----
    def _build_tab2(self) -> None:
        tab = tk.Frame(self.content, bg=PALETTE["bg"])
        self.tab_frames["history"] = tab
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(1, weight=1)
        # Dải tóm tắt: chỉ đếm phiên, KHÔNG bịa tỉ lệ thắng hay lãi/lỗ theo phiên
        # (nhật ký thật không ghi lãi/lỗ thực hiện theo phiên).
        summary = _panel(tab)
        summary.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        self.summary_vars = {}
        for i, (key, label) in enumerate((("count", "Phiên đã ghi:"), ("buy", "BUY:"),
                                          ("sell", "SELL:"), ("hold", "HOLD:"),
                                          ("pass", "Cửa rủi ro PASS:"))):
            tk.Label(summary, text=label, bg=PALETTE["panel"], fg=PALETTE["muted"],
                     font=("", 10)).grid(row=0, column=i * 2, sticky="w", padx=(8, 2), pady=6)
            var = tk.StringVar(value="0")
            self.summary_vars[key] = var
            tk.Label(summary, textvariable=var, bg=PALETTE["panel"], fg=PALETTE["cyan"],
                     font=("", 11, "bold")).grid(row=0, column=i * 2 + 1, sticky="w", pady=6)
        left = _panel(tab)
        left.grid(row=1, column=0, sticky="ns", padx=(0, 4))
        _button(left, "Làm mới lịch sử", self._load_history).pack(fill="x", padx=6, pady=(6, 2))
        _button(left, "Mở thư mục output", self._open_output).pack(fill="x", padx=6, pady=2)
        self.history_list = tk.Listbox(left, width=34, bg=PALETTE["panel2"], fg=PALETTE["text"],
                                       selectbackground=PALETTE["blue"], relief="flat",
                                       highlightbackground=PALETTE["border"], highlightthickness=1)
        self.history_list.pack(fill="both", expand=True, padx=6, pady=6)
        self.history_list.bind("<<ListboxSelect>>", self._on_history_select)
        right = _panel(tab)
        right.grid(row=1, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        tk.Label(right, text="CHI TIẾT PHIÊN", bg=PALETTE["panel"], fg=PALETTE["cyan"],
                 font=("", 11, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        self.hist_summary_var = tk.StringVar(value="Chọn một phiên bên trái để xem lại toàn bộ.")
        tk.Label(right, textvariable=self.hist_summary_var, bg=PALETTE["panel"], fg=PALETTE["text"],
                 font=("", 10), anchor="w").grid(row=1, column=0, sticky="ew", padx=8)
        self.hist_views: dict[str, dict] = {}
        views_frame = tk.Frame(right, bg=PALETTE["panel"])
        views_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=4)
        views_frame.grid_columnconfigure(1, weight=1)
        for i, name in enumerate(AGENT_NAMES):
            tk.Label(views_frame, text=name, bg=PALETTE["panel"], fg=PALETTE["text"],
                     font=("", 9), anchor="w", width=18).grid(row=i, column=0, sticky="w")
            dir_lbl = tk.Label(views_frame, text="—", bg=PALETTE["panel"], fg=PALETTE["muted"],
                               font=("", 9, "bold"), width=6)
            dir_lbl.grid(row=i, column=1, sticky="w")
            bar = tk.Canvas(views_frame, height=10, bg=PALETTE["panel"], highlightthickness=0)
            bar.grid(row=i, column=2, sticky="ew", padx=4)
            conf_lbl = tk.Label(views_frame, text="", bg=PALETTE["panel"], fg=PALETTE["text"],
                                font=("", 9), width=5)
            conf_lbl.grid(row=i, column=3, sticky="e")
            self.hist_views[name] = {"dir": dir_lbl, "bar": bar, "conf": conf_lbl}
        tk.Label(right, text="QUYẾT ĐỊNH TRƯỞNG PHÒNG + KIỂM TRA RỦI RO", bg=PALETTE["panel"],
                 fg=PALETTE["cyan"], font=("", 11, "bold"), anchor="w").grid(
                     row=3, column=0, sticky="ew", padx=8, pady=(4, 0))
        self.history_text = _text(right, height=12)
        self.history_text.grid(row=4, column=0, sticky="nsew", padx=8, pady=(0, 8))
        right.grid_rowconfigure(4, weight=1)
        _set_text(self.history_text, "Chọn một phiên bên trái để xem lại toàn bộ.")
        self._history_data: list = []

    # ----- Tab 3: Cấu hình & an toàn -----
    def _build_tab3(self) -> None:
        tab = tk.Frame(self.content, bg=PALETTE["bg"])
        self.tab_frames["config"] = tab
        tab.grid_rowconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=0)
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=1)
        cfg = load_config(self.config_path)
        self.cfg_entries: dict[str, tk.Entry] = {}
        self.cfg_checks: dict[str, tk.BooleanVar] = {}

        left = _panel(tab)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        tk.Label(left, text="CẤU HÌNH GIAO DỊCH", bg=PALETTE["panel"], fg=PALETTE["cyan"],
                 font=("", 11, "bold"), anchor="w").pack(fill="x", padx=8, pady=(6, 4))
        form = tk.Frame(left, bg=PALETTE["panel"])
        form.pack(fill="x", padx=8)
        form.grid_columnconfigure(1, weight=1)

        def row(parent, label: str, key: str, value, row_no: int, disabled: bool = False) -> None:
            tk.Label(parent, text=label, bg=PALETTE["panel"], fg=PALETTE["text"],
                     font=("", 10), anchor="w").grid(row=row_no, column=0, sticky="w", pady=3)
            entry = _entry(parent)
            entry.insert(0, str(value))
            entry.grid(row=row_no, column=1, sticky="ew", pady=3, padx=(6, 0))
            if disabled:
                entry.config(state="disabled")
            self.cfg_entries[key] = entry

        row(form, "Symbol:", "symbol", cfg.symbol, 0)
        row(form, "Timeframe:", "timeframe", cfg.timeframe, 1)
        row(form, "Max open positions:", "max_open_positions", cfg.max_open_positions, 2)
        row(form, "Loop interval (giây):", "loop_interval_seconds", cfg.loop_interval_seconds, 3)
        news_var = tk.BooleanVar(value=cfg.news_enabled)
        sent_var = tk.BooleanVar(value=cfg.sentiment_enabled)
        self.cfg_checks["news_enabled"] = news_var
        self.cfg_checks["sentiment_enabled"] = sent_var
        tk.Checkbutton(form, text="News enabled: Bật", variable=news_var, bg=PALETTE["panel"],
                       fg=PALETTE["text"], selectcolor=PALETTE["panel2"],
                       activebackground=PALETTE["panel"]).grid(row=4, column=0, columnspan=2, sticky="w", pady=3)
        tk.Checkbutton(form, text="Sentiment enabled: Bật", variable=sent_var, bg=PALETTE["panel"],
                       fg=PALETTE["text"], selectcolor=PALETTE["panel2"],
                       activebackground=PALETTE["panel"]).grid(row=5, column=0, columnspan=2, sticky="w", pady=3)
        row(form, "Mode:", "mode", cfg.mode, 6, disabled=True)
        tk.Label(left, text="(Read-only — app không cho đổi sang live)", bg=PALETTE["panel"],
                 fg=PALETTE["muted"], font=("", 9), anchor="w").pack(fill="x", padx=8)

        right = _panel(tab)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        tk.Label(right, text="GIỚI HẠN RỦI RO (USD)", bg=PALETTE["panel"], fg=PALETTE["cyan"],
                 font=("", 11, "bold"), anchor="w").pack(fill="x", padx=8, pady=(6, 4))
        risk_form = tk.Frame(right, bg=PALETTE["panel"])
        risk_form.pack(fill="x", padx=8)
        risk_form.grid_columnconfigure(1, weight=1)
        risk = cfg.risk
        fields = [
            ("Max risk per trade (USD):", "max_risk_usd_per_trade", risk.max_risk_usd_per_trade),
            ("Max daily loss (USD):", "max_daily_loss_usd", risk.max_daily_loss_usd),
            ("Max lot per order:", "max_lot_per_order", risk.max_lot_per_order),
            ("Minimum confidence:", "min_confidence", risk.min_confidence),
            ("Min stop loss distance (USD):", "min_sl_distance_usd", risk.min_sl_distance_usd),
            ("Max stop loss distance (USD):", "max_sl_distance_usd", risk.max_sl_distance_usd),
            ("Minimum reward risk:", "min_reward_risk", risk.min_reward_risk),
            ("Max spread (USD):", "max_spread_usd", risk.max_spread_usd),
            ("Max price move (USD):", "max_price_move_usd", risk.max_price_move_usd),
            ("Max portfolio notional (USD):", "max_portfolio_notional_usd", risk.max_portfolio_notional_usd),
        ]
        for i, (label, key, value) in enumerate(fields):
            row(risk_form, label, key, value, i)

        bottom = tk.Frame(tab, bg=PALETTE["bg"])
        bottom.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        bottom.grid_columnconfigure(0, weight=1)
        bottom.grid_columnconfigure(1, weight=2)
        emergency = tk.Frame(bottom, bg=PALETTE["panel"], highlightbackground=PALETTE["red"],
                             highlightthickness=1)
        emergency.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        tk.Label(emergency, text="⚠ ĐIỀU KHIỂN KHẨN CẤP", bg=PALETTE["panel"], fg=PALETTE["red"],
                 font=("", 11, "bold"), anchor="w").pack(fill="x", padx=8, pady=(6, 2))
        btn_row = tk.Frame(emergency, bg=PALETTE["panel"])
        btn_row.pack(fill="x", padx=8)
        _button(btn_row, "⚠ DỪNG KHẨN CẤP", self._create_stop, bg=PALETTE["danger"], fg="white").pack(side="left", padx=(0, 6))
        _button(btn_row, "Go STOP", self._clear_stop, bg=PALETTE["orange"], fg="#0B1220").pack(side="left")
        tk.Label(emergency, text="STOP file status:", bg=PALETTE["panel"], fg=PALETTE["text"],
                 font=("", 10), anchor="w").pack(fill="x", padx=8, pady=(6, 0))
        self.stop_status_var = tk.StringVar(value="NOT ACTIVE")
        tk.Label(emergency, textvariable=self.stop_status_var, bg=PALETTE["grey"], fg="white",
                 font=("", 10, "bold"), padx=8, pady=2).pack(anchor="w", padx=8, pady=(0, 8))

        keys_panel = _panel(bottom)
        keys_panel.grid(row=0, column=1, sticky="nsew")
        tk.Label(keys_panel, text="BẢO MẬT - AI KEY STATUS", bg=PALETTE["panel"], fg=PALETTE["cyan"],
                 font=("", 11, "bold"), anchor="w").pack(fill="x", padx=8, pady=(6, 2))
        self.key_pills: dict[str, tk.Label] = {}
        pills_row = tk.Frame(keys_panel, bg=PALETTE["panel"])
        pills_row.pack(fill="x", padx=8)
        for env_name, label in (("OPENAI_API_KEY", "OpenAI"), ("GEMINI_API_KEY", "Gemini"),
                                ("ANTHROPIC_API_KEY", "Anthropic")):
            tk.Label(pills_row, text=f"{label}:", bg=PALETTE["panel"], fg=PALETTE["text"],
                     font=("", 10)).pack(side="left", padx=(0, 2))
            pill = _pill(pills_row, "MISSING", PALETTE["grey"])
            pill.pack(side="left", padx=(0, 12))
            self.key_pills[env_name] = pill
        tk.Label(keys_panel, text="Lưu ý: Khóa API được chỉnh sửa trong file .env cục bộ. Không hiển thị giá trị khóa.",
                 bg=PALETTE["panel"], fg=PALETTE["amber"], font=("", 9), anchor="w").pack(fill="x", padx=8, pady=4)
        save_row = tk.Frame(keys_panel, bg=PALETTE["panel"])
        save_row.pack(fill="x", padx=8, pady=(0, 4))
        _button(save_row, "Mở .env trong Notepad", self._open_env).pack(side="left", padx=(0, 6))
        _button(save_row, "Lưu cấu hình", self._save_config, bg=PALETTE["blue"], fg="white").pack(side="left")
        self.config_msg_var = tk.StringVar(value="Một bản sao lưu config.yaml.bak sẽ được tạo khi lưu.")
        tk.Label(keys_panel, textvariable=self.config_msg_var, bg=PALETTE["panel"],
                 fg=PALETTE["muted"], font=("", 9), anchor="w").pack(fill="x", padx=8, pady=(0, 8))

    # ---------- Hành động ----------
    def _change_zoom(self, delta: int) -> None:
        self.chart.visible = max(30, min(200, self.chart.visible + delta))
        self.zoom_var.set(f"{self.chart.visible} nến")
        self.chart.draw()

    def _start_run(self, loop: bool) -> None:
        self.live.pause()
        if not self.runner.start(self.source_var.get(), self.mock_var.get(),
                                 "data/snapshot_example.json", loop):
            self.live.resume()
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
        detail.configure(bg=PALETTE["bg"])
        text = tk.Text(detail, wrap="word", bg=PALETTE["panel"], fg=PALETTE["text"])
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
    def _draw_conf(self, canvas: tk.Canvas, confidence: float, color: str) -> None:
        canvas.delete("all")
        width = canvas.winfo_width()
        if width < 10:
            width = 200
        height = canvas.winfo_height() or 10
        canvas.create_rectangle(0, 0, width, height, fill=PALETTE["panel2"], outline="")
        canvas.create_rectangle(0, 0, max(0, width * max(0.0, min(1.0, float(confidence or 0.0)))),
                                height, fill=color, outline="")

    def _set_card(self, name: str, status: str, direction: str, confidence: float, reasoning: str) -> None:
        card = self.cards.get(name)
        if not card:
            return
        card["status"].config(text=status)
        card["dir"].config(text=direction, fg=_direction_color(direction) if direction in ("BUY", "SELL", "HOLD") else PALETTE["muted"])
        self._draw_conf(card["conf"], confidence, _direction_color(direction))
        card["reason"].config(text=reasoning or "")

    def _set_step(self, index: int) -> None:
        for i, lbl in enumerate(self.step_labels):
            if i < index:
                lbl.config(bg=PALETTE["green"], fg="#0B1220")
            elif i == index:
                lbl.config(bg=PALETTE["amber"], fg="#0B1220")
            else:
                lbl.config(bg=PALETTE["panel2"], fg=PALETTE["muted"])

    def _update_market_bar(self, bid: float, ask: float, balance: float | None = None,
                           equity: float | None = None, connected: bool = True) -> None:
        price = (bid + ask) / 2
        self.price_var.set(f"{price:,.2f} USD")
        self.spread_var.set(f"SPREAD: {ask - bid:.2f}")
        if balance:
            self._last_balance = balance
            self.bal_var.set(f"BAL: {balance:,.2f} USD")
        if equity:
            self.eq_var.set(f"EQUITY: {equity:,.2f} USD")
        if self._ref_close:
            change = price - self._ref_close
            pct = change / self._ref_close * 100 if self._ref_close else 0.0
            sign = "+" if change >= 0 else ""
            self.change_var.set(f"{sign}{change:.2f} ({sign}{pct:.2f}%)")
            self.change_lbl.config(fg=PALETTE["green"] if change >= 0 else PALETTE["red"])
        self.mt5_var.set("MT5: CONNECTED • ONLINE" if connected else "MT5: CHƯA KẾT NỐI")
        self.mt5_lbl.config(fg=PALETTE["green"] if connected else PALETTE["amber"])

    def _show_snapshot(self, snap: dict) -> None:
        self._current_snapshot = snap
        self.symbol_var.set(snap["symbol"])
        self.chart_title_var.set(f"BIỂU ĐỒ — {snap['symbol']} • {snap['timeframe']} • DRY-RUN")
        ind = snap.get("indicators", {})
        candles = snap.get("candles") or []
        if candles:
            if len(candles) >= 2:
                self._ref_close = candles[-2]["close"]
            self.chart.set_candles(candles, ind.get("support"), ind.get("resistance"))
        self._update_market_bar(snap["bid"], snap["ask"], snap.get("account_balance"),
                                snap.get("account_equity"), connected=True)
        lines = [
            f"RSI(14): {ind.get('rsi14')}  •  MACD: {ind.get('macd_line')} / {ind.get('signal')} • Hist {ind.get('macd_histogram')}",
            f"ATR(14): {ind.get('atr14_usd')} USD  •  EMA20: {ind.get('ema20')}  •  EMA50: {ind.get('ema50')}",
            f"BOLLINGER: Upper {ind.get('bollinger_upper_usd')} • Mid {ind.get('bollinger_middle_usd')} • Lower {ind.get('bollinger_lower_usd')}",
            f"Hỗ trợ: {ind.get('support')}  •  Kháng cự: {ind.get('resistance')}",
            f"Vị thế mở: {snap['open_positions']}  •  Lỗ trong ngày: {snap['daily_loss_usd']} USD  •  Nguồn: {snap['source']} lúc {snap.get('captured_at') or 'không rõ'}",
        ]
        _set_text(self.indicators_text, "\n".join(str(x) for x in lines))
        news = (snap.get("news_text") or "Chưa có tin tức.") + "\n\n" + (snap.get("sentiment_text") or "Chưa có dữ liệu sentiment.")
        _set_text(self.news_text, news[:4000])
        self.feed_sync_var.set(f"Synced {snap.get('captured_at') or ''}")

    def _show_result(self, result: dict) -> None:
        self._show_snapshot(result["snapshot"])
        for view in result["views"]:
            self._current_views[view["name"]] = view
            self._set_card(view["name"], "Đã xong", view["direction"], view["confidence"], view["reasoning"])
        chief = result["chief"]
        self._current_views["chief"] = chief
        self._set_card("chief", "Đã xong", chief["direction"], chief["confidence"], chief["reasoning"])
        risk = result["risk"]
        self.chief_title_var.set(f"★ CHIEF DECISION — {chief['direction']} • CONF {int(float(chief['confidence'] or 0) * 100)}%")
        _set_text(self.chief_text,
                  f"Confidence: {chief['confidence']} • Quyết định: {chief['direction']}\n"
                  f"Entry: {chief['entry']} USD, SL: {chief['stop_loss']} USD, TP: {chief['take_profit']} USD\n"
                  f"Lý do: {chief['reasoning']}\nĐiểm vô hiệu: {chief['invalidation']}")
        self.ticket_vars["entry"].set(f"{chief['entry']} USD" if chief["entry"] is not None else "—")
        self.ticket_vars["sl"].set(f"{chief['stop_loss']} USD" if chief["stop_loss"] is not None else "—")
        self.ticket_vars["tp"].set(f"{chief['take_profit']} USD" if chief["take_profit"] is not None else "—")
        self.ticket_vars["lot"].set(f"{risk['lot']}" if risk.get("lot") else "—")
        if risk.get("lot") and risk.get("sl_distance") and self._last_balance:
            risk_usd = risk["lot"] * risk["sl_distance"] * 100
            self.risk_est_var.set(f"RỦI RO ƯỚC TÍNH: {risk_usd:,.0f} USD ({risk_usd / self._last_balance * 100:.2f}% vốn)")
        else:
            self.risk_est_var.set("RỦI RO ƯỚC TÍNH: —")
        checks = "\n".join(f"{'✓ PASS' if c['passed'] else '✕ FAIL'}  {c['name']}: {c['detail']}" for c in risk.get("checks", []))
        _set_text(self.checks_text, checks or risk["reason"])
        self.order_var.set(f"{result['order']}  •  Lot: {risk['lot']}  •  File: {result['saved']}")
        self._load_history()

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.phase_var.set(str(payload))
                elif kind == "step":
                    self._set_step(int(payload))
                elif kind == "tick":
                    self._update_market_bar(payload["bid"], payload["ask"],
                                            payload.get("balance"), payload.get("equity"))
                    self.chart.update_tick(payload["bid"], payload["ask"])
                    if payload.get("candles"):
                        candles = payload["candles"]
                        if len(candles) >= 2:
                            self._ref_close = candles[-2]["close"]
                        snap = self._current_snapshot or {}
                        ind = snap.get("indicators", {})
                        self.chart.set_candles(candles, ind.get("support"), ind.get("resistance"))
                elif kind == "live_error":
                    self.mt5_var.set("MT5: CHƯA KẾT NỐI")
                    self.mt5_lbl.config(fg=PALETTE["amber"])
                    self.phase_var.set(f"Realtime: {payload}")
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
                    self.live.resume()
                    self._refresh_buttons()
                    self._refresh_status()
        except queue.Empty:
            pass
        self.after(200, self._poll_events)

    def _tick_clock(self) -> None:
        self.clock_var.set(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.after(1000, self._tick_clock)

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
        stopped = stop_file.exists()
        self.stop_var.set("ĐÃ DỪNG (file STOP)" if stopped else "")
        self.stop_status_var.set("ACTIVE" if stopped else "NOT ACTIVE")
        keys = []
        for env_name, label in (("OPENAI_API_KEY", "OpenAI"), ("GEMINI_API_KEY", "Gemini"), ("ANTHROPIC_API_KEY", "Claude")):
            present = bool(os.getenv(env_name, "").strip())
            keys.append(f"{label}: {'● có' if present else '○ chưa có'}")
            pill = self.key_pills.get(env_name)
            if pill is not None:
                pill.config(text="PRESENT" if present else "MISSING",
                            bg=PALETTE["green"] if present else PALETTE["grey"])
        self.keys_var.set("Key: " + "  •  ".join(keys) + "  (chỉ hiện có/không có, không hiện giá trị)")
        self.symbol_var.set(cfg.symbol)
        self.chart_title_var.set(f"BIỂU ĐỒ — {cfg.symbol} • {cfg.timeframe} • DRY-RUN")
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
        buy = sum(1 for _p, d in self._history_data if d.get("chief", {}).get("direction") == "BUY")
        sell = sum(1 for _p, d in self._history_data if d.get("chief", {}).get("direction") == "SELL")
        hold = sum(1 for _p, d in self._history_data if d.get("chief", {}).get("direction") == "HOLD")
        passed = sum(1 for _p, d in self._history_data if d.get("risk", {}).get("allowed"))
        self.summary_vars["count"].set(str(len(self._history_data)))
        self.summary_vars["buy"].set(str(buy))
        self.summary_vars["sell"].set(str(sell))
        self.summary_vars["hold"].set(str(hold))
        self.summary_vars["pass"].set(str(passed))

    def _on_history_select(self, _event) -> None:
        sel = self.history_list.curselection()
        if not sel:
            return
        path, data = self._history_data[sel[0]]
        self.hist_summary_var.set(f"Phiên: {data.get('timestamp_utc', '')}  •  File: {path.name}  •  Lệnh: {data.get('order', '')}")
        views = {v["name"]: v for v in data.get("views", [])}
        chief = data.get("chief", {})
        for name, widgets in self.hist_views.items():
            view = views.get(name) if name != "chief" else chief
            if not view:
                continue
            direction = view.get("direction", "—")
            widgets["dir"].config(text=direction, fg=_direction_color(direction))
            widgets["conf"].config(text=f"{int(float(view.get('confidence') or 0) * 100)}%")
            self._draw_conf(widgets["bar"], view.get("confidence") or 0, _direction_color(direction))
            self._current_views[name] = view
            self._set_card(name, "Xem lại từ nhật ký", direction, view.get("confidence") or 0,
                           view.get("reasoning", ""))
        risk = data.get("risk", {})
        checks = "\n".join(f"{'✓ PASS' if c['passed'] else '✕ FAIL'}  {c['name']}: {c['detail']}" for c in risk.get("checks", []))
        chief_line = (f"Hướng: {chief.get('direction')}, tin cậy: {chief.get('confidence')}\n"
                      f"Entry: {chief.get('entry_usd')} USD, SL: {chief.get('stop_loss_usd')} USD, TP: {chief.get('take_profit_usd')} USD\n")
        _set_text(self.history_text, chief_line + "\n" + (checks or risk.get("reason", "")))
        self.order_var.set(str(data.get("order", "")))

    def _on_close(self) -> None:
        self.runner.stop()
        try:
            self.live.stop()
        except Exception:
            pass
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
