"""App desktop Terminal Pro (CustomTkinter) cho phòng trade AI.

Bản viết lại theo đúng ảnh demo Terminal Pro mà Thái đã chốt (lựa chọn 2):
góc bo tròn, thẻ nổi, nút lớn, font Segoe UI, bảng màu navy/teal như ảnh.
Tkinter cổ điển không vẽ được kiểu này (vuông, font mặc định xấu) nên bản
trước dù sửa bố cục vẫn bị Thái chê xấu; bản này dùng CustomTkinter
(thư viện giao diện hiện đại xây trên Tkinter, cài thêm 1 dòng pip).

Bố cục 3 tab, thanh tab ở DƯỚI cùng, thanh market watch ở trên cùng:
tab 1 Phòng trade (biểu đồ nến + AI feed + order ticket),
tab 2 Nhật ký & lịch sử, tab 3 Cấu hình & an toàn.
Khối quyết định trưởng phòng dùng panel tối viền cyan, KHÔNG nền nâu.

Biểu đồ + market watch chạy theo thời gian thực: luồng LiveFeed hỏi tick MT5
mỗi 2 giây và nến mới mỗi ~10 giây. MT5 Python API không tự đẩy giá nên đây
là hỏi theo chu kỳ, không phải từng mili giây. Luồng realtime tạm dừng trong
lúc 1 phiên AI đang chạy, chạy xong tự nối lại.

Cài đặt (Windows, Python 3.13): py -3.13 -m pip install -r requirements.txt
Chạy: bấm đúp run_app.bat, hoặc py -3.13 main.py --app.

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
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

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
    "blue_hover": "#1D4ED8",
    "green": "#22C55E",     # BUY / LONG / PASS
    "red": "#EF4444",       # SELL / SHORT / FAIL
    "amber": "#F59E0B",     # HOLD / NEUTRAL / CAUTION
    "orange": "#EA580C",    # nút Gỡ STOP
    "danger": "#DC2626",    # nút dừng khẩn cấp
    "danger_hover": "#B91C1C",
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


# ---------- Font: Segoe UI trên Windows, DejaVu Sans trên Linux ----------
_FAMILY_CACHE: str | None = None


def _font_family() -> str:
    global _FAMILY_CACHE
    if _FAMILY_CACHE is not None:
        return _FAMILY_CACHE
    try:
        families = set(tkfont.families())
    except Exception:
        families = set()
    for candidate in ("Segoe UI", "Inter", "Roboto", "DejaVu Sans", "Arial", "Helvetica"):
        if candidate in families:
            _FAMILY_CACHE = candidate
            return candidate
    _FAMILY_CACHE = ""
    return ""


def F(size: int, bold: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(family=_font_family() or None, size=size,
                       weight="bold" if bold else "normal")


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
        # Thử nối lại mỗi 5 giây thay vì bỏ luôn ở lần đầu thất bại: terminal
        # MT5 có thể chưa đăng nhập xong lúc app vừa mở (lỗi -6 Authorization
        # failed thường là lúc đó), sau đó nối được thì dữ liệu vẫn chạy.
        while not self._stop.is_set():
            try:
                provider.connect()
                self._error_sent = False
                break
            except Exception as exc:
                if not self._error_sent:
                    self._error_sent = True
                    self.events.put(("live_error", str(exc)))
                if self._stop.wait(timeout=5.0):
                    return
        if self._stop.is_set():
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
        self.font_family = "DejaVu Sans"
        self.candles: list[dict] = []
        self.support: list[float] = []
        self.resistance: list[float] = []
        self.visible = 100
        self.bind("<Configure>", lambda _e: self.draw())

    def set_candles(self, candles: list[dict], support=None, resistance=None) -> None:
        self.candles = [dict(c) for c in (candles or [])]
        # Gộp các mức trùng nhau (chỉ báo trả về list có thể lặp) để nhãn
        # trên biểu đồ không đè lên nhau.
        self.support = sorted({round(float(v), 2) for v in (support or [])})
        self.resistance = sorted({round(float(v), 2) for v in (resistance or [])})
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
                             font=(self.font_family, 10))
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

        # Chừa lề phải 64px cho nhãn giá, như ảnh demo; nến không vẽ
        # đè lên nhãn giá và nhãn giá không bị cắt ở mép phải.
        plot_right = width - 64

        def x_of(i: int) -> float:
            return 8 + (i + 0.5) * (plot_right - 8) / len(visible)

        step = (plot_right - 8) / len(visible)
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
            self.create_line(8, y_of(level), plot_right, y_of(level), fill="#3B82F6", dash=(4, 3))
            self.create_text(10, y_of(level) - 8, text=f"SUPPORT {level:.2f}", fill="#3B82F6",
                             anchor="w", font=(self.font_family, 8))
        for level in self.resistance:
            self.create_line(8, y_of(level), plot_right, y_of(level), fill=PALETTE["red"], dash=(4, 3))
            self.create_text(10, y_of(level) - 8, text=f"RESISTANCE {level:.2f}", fill=PALETTE["red"],
                             anchor="w", font=(self.font_family, 8))
        last_price = visible[-1]["close"]
        self.create_line(8, y_of(last_price), plot_right, y_of(last_price), fill=PALETTE["cyan"], dash=(2, 2))
        self.create_text(width - 6, y_of(last_price), text=f"{last_price:.2f}",
                         fill=PALETTE["cyan"], anchor="e", font=(self.font_family, 9, "bold"))
        # Nhãn VOLUME có nền tối riêng để không bị cột volume đè lên chữ.
        self.create_rectangle(6, height - 20, 66, height - 3, fill=PALETTE["bg"], outline="")
        self.create_text(10, height - 6, text="VOLUME", fill=PALETTE["muted"], anchor="sw", font=(self.font_family, 8))



# ---------- Widget phụ CustomTkinter: cùng bảng màu, dễ sửa ----------

def _frame(parent, radius: int = 12, color: str | None = None, border: bool = True, **kwargs):
    return ctk.CTkFrame(parent, corner_radius=radius, fg_color=color or PALETTE["panel"],
                        border_width=1 if border else 0, border_color=PALETTE["border"], **kwargs)


def _label(parent, text: str = "", size: int = 13, bold: bool = False,
           color: str | None = None, **kwargs):
    return ctk.CTkLabel(parent, text=text, font=F(size, bold),
                        text_color=color or PALETTE["text"], **kwargs)


def _button(parent, text: str, command, color: str | None = None, text_color: str = "white",
            size: int = 14, height: int = 44, **kwargs):
    bg = color or PALETTE["panel2"]
    hover = {"#2563EB": PALETTE["blue_hover"], "#DC2626": PALETTE["danger_hover"]}.get(bg, bg)
    return ctk.CTkButton(parent, text=text, command=command, font=F(size, True),
                         fg_color=bg, hover_color=hover, text_color=text_color,
                         corner_radius=10, height=height, **kwargs)


def _entry(parent, width: int = 200, **kwargs):
    return ctk.CTkEntry(parent, width=width, font=F(13), height=36,
                        fg_color=PALETTE["panel2"], border_color=PALETTE["border"],
                        text_color=PALETTE["text"], **kwargs)


def _textbox(parent, height: int = 120, **kwargs):
    return ctk.CTkTextbox(parent, height=height, font=F(13), corner_radius=8,
                          fg_color=PALETTE["panel"], border_width=1,
                          border_color=PALETTE["border"], text_color=PALETTE["text"], **kwargs)


def _pill(parent, text: str, bg: str, text_color: str | None = None, size: int = 12):
    return ctk.CTkLabel(parent, text=text, font=F(size, True), fg_color=bg,
                        text_color=text_color or ("#FFFFFF" if bg in (PALETTE["red"], PALETTE["grey"])
                                                  else "#0B1220"),
                        corner_radius=6, padx=8, pady=2)


def _bar(parent, color: str, height: int = 8):
    bar = ctk.CTkProgressBar(parent, height=height, progress_color=color,
                             fg_color=PALETTE["panel2"], corner_radius=4)
    bar.set(0.0)
    return bar


def _set_bar(bar: ctk.CTkProgressBar, value: float, color: str) -> None:
    bar.configure(progress_color=color)
    bar.set(max(0.0, min(1.0, float(value or 0.0))))


def _set_text(widget: ctk.CTkTextbox, content: str) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", content)
    widget.configure(state="disabled")


class DesktopApp(ctk.CTk):
    def __init__(self, config_path: str = "config.yaml", output_dir: str = "output"):
        ctk.set_appearance_mode("dark")
        super().__init__()
        self.config_path = config_path
        self.output_dir = output_dir
        self.events: queue.Queue = queue.Queue()
        self.runner = SessionRunner(config_path, output_dir, self.events)
        self.live = LiveFeed(config_path, self.events)
        self.title("AI GOLD TRADING ROOM — TERMINAL PRO")
        self.geometry("1440x920")
        self.minsize(1100, 720)
        self.configure(fg_color=PALETTE["bg"])
        self._current_views: dict[str, dict] = {}
        self._current_snapshot: dict | None = None
        self._ref_close: float | None = None
        self._last_balance = 0.0
        self._last_equity = 0.0
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
    # Lưu ý: CTkLabel không nhận textvariable, nên mọi chữ cập nhật động
    # đều giữ tham chiếu widget và gọi .configure(text=...) trực tiếp.
    def _build_widgets(self) -> None:
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Hàng 0: tiêu đề app
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))
        _label(header, "◎  AI GOLD TRADING ROOM — TERMINAL PRO", size=17, bold=True,
               color=PALETTE["cyan"]).pack(side="left")
        _label(header, "v2.1.3", size=12, color=PALETTE["muted"]).pack(side="left", padx=8)
        self.mode_pill = _pill(header, "DRY-RUN", PALETTE["amber"], size=12)
        self.mode_pill.pack(side="right")

        # Hàng 1: market watch (trên) + tiến trình 7 bước (dưới)
        bar = _frame(self)
        bar.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        top = ctk.CTkFrame(bar, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(8, 2))
        stack = ctk.CTkFrame(top, fg_color="transparent")
        stack.pack(side="left")
        _label(stack, "MARKET WATCH:", size=11, bold=True, color=PALETTE["muted"]).pack(anchor="w")
        self.symbol_lbl = _label(stack, "XAUUSD", size=14, bold=True)
        self.symbol_lbl.pack(anchor="w")
        self.price_lbl = _label(top, "—", size=26, bold=True)
        self.price_lbl.pack(side="left", padx=12)
        self.change_lbl = _label(top, "", size=14, bold=True, color=PALETTE["green"])
        self.change_lbl.pack(side="left", padx=4)

        def _mini_box(caption: str, color: str, bold: bool) -> ctk.CTkLabel:
            box = _frame(top, radius=8, color=PALETTE["panel2"])
            box.pack(side="left", padx=4, pady=2)
            _label(box, caption, size=10, color=PALETTE["muted"]).pack(fill="x", padx=8, pady=(4, 0))
            value_lbl = _label(box, "—", size=13, bold=bold, color=color)
            value_lbl.pack(fill="x", padx=8, pady=(0, 4))
            return value_lbl

        self.spread_lbl = _mini_box("SPREAD:", PALETTE["text"], False)
        self.bal_lbl = _mini_box("BAL:", PALETTE["text"], False)
        self.eq_lbl = _mini_box("EQUITY:", PALETTE["green"], True)
        self.mt5_lbl = _label(top, "MT5: CHƯA KẾT NỐI", size=13, bold=True, color=PALETTE["amber"])
        self.mt5_lbl.pack(side="left", padx=10)

        prog = ctk.CTkFrame(bar, fg_color="transparent")
        prog.pack(fill="x", padx=10, pady=(2, 8))
        _label(prog, "PROGRESS:", size=11, bold=True, color=PALETTE["muted"]).pack(side="left", padx=(0, 8))
        self.step_labels: list[ctk.CTkLabel] = []
        for label in STEPS:
            lbl = _pill(prog, label, PALETTE["panel2"], text_color=PALETTE["muted"], size=11)
            lbl.pack(side="left", padx=2)
            self.step_labels.append(lbl)

        # Hàng 2: nội dung 3 tab
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=2, column=0, sticky="nsew", padx=12, pady=4)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        self.tab_frames: dict[str, ctk.CTkFrame] = {}
        self._build_tab1()
        self._build_tab2()
        self._build_tab3()

        # Hàng 3: thanh tab ở DƯỚI cùng (như ảnh demo)
        tabbar = ctk.CTkFrame(self, fg_color="transparent")
        tabbar.grid(row=3, column=0, pady=(2, 0))
        self.tab_buttons: dict[str, ctk.CTkButton] = {}
        for key, label in (("trade", "Phòng trade"), ("history", "Nhật ký & lịch sử"),
                           ("config", "Cấu hình & an toàn")):
            btn = _button(tabbar, label, lambda k=key: self.show_tab(k), height=40, size=13)
            btn.pack(side="left", padx=4, ipadx=10)
            self.tab_buttons[key] = btn
        self.show_tab("trade")

        # Hàng 4: thanh trạng thái dưới cùng
        status = ctk.CTkFrame(self, fg_color="transparent")
        status.grid(row=4, column=0, sticky="ew", padx=12, pady=(4, 10))
        self.keys_lbl = _label(status, "Key: đang kiểm tra...", size=11, color=PALETTE["muted"])
        self.keys_lbl.pack(side="left")
        self.clock_lbl = _label(status, "", size=11, color=PALETTE["muted"])
        self.clock_lbl.pack(side="right")
        self.phase_lbl = _label(status, "Chưa chạy phiên nào.", size=11, color=PALETTE["cyan"])
        self.phase_lbl.pack(side="left", padx=16)

    def show_tab(self, key: str) -> None:
        for name, frame in self.tab_frames.items():
            frame.grid_forget()
        self.tab_frames[key].grid(row=0, column=0, sticky="nsew")
        for name, btn in self.tab_buttons.items():
            if name == key:
                btn.configure(fg_color=PALETTE["blue"], text_color="white")
            else:
                btn.configure(fg_color=PALETTE["panel2"], text_color=PALETTE["text"])

    # ---------- Tab 1: Phòng trade ----------
    def _build_tab1(self) -> None:
        tab = ctk.CTkFrame(self.content, fg_color="transparent")
        tab.grid_rowconfigure(0, weight=1)
        tab.grid_columnconfigure(0, weight=40)
        tab.grid_columnconfigure(1, weight=33)
        tab.grid_columnconfigure(2, weight=27)
        self.tab_frames["trade"] = tab

        # Cột trái: biểu đồ + chỉ báo
        left = ctk.CTkScrollableFrame(tab, corner_radius=12, fg_color=PALETTE["panel"],
                                          border_width=1, border_color=PALETTE["border"],
                                          scrollbar_button_color=PALETTE["panel2"],
                                          scrollbar_button_hover_color=PALETTE["border"])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        left_top = ctk.CTkFrame(left, fg_color="transparent")
        left_top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        _label(left_top, "BIỂU ĐỒ NẾN", size=14, bold=True, color=PALETTE["cyan"]).pack(side="left")
        self.chart_title_lbl = _label(left_top, "M15 • DRY-RUN", size=12, color=PALETTE["muted"])
        self.chart_title_lbl.pack(side="left", padx=8)
        _button(left_top, "−", lambda: self._change_zoom(20), color=PALETTE["panel2"],
                size=14, height=28, width=36).pack(side="right", padx=2)
        _button(left_top, "+", lambda: self._change_zoom(-20), color=PALETTE["panel2"],
                size=14, height=28, width=36).pack(side="right", padx=2)
        self.zoom_lbl = _label(left_top, "100 nến", size=11, color=PALETTE["muted"])
        self.zoom_lbl.pack(side="right", padx=6)
        self.chart = CandleChart(left)
        self.chart.font_family = _font_family() or "DejaVu Sans"
        self.chart.grid(row=1, column=0, sticky="nsew", padx=10)
        ind = ctk.CTkFrame(left, fg_color="transparent")
        ind.grid(row=2, column=0, sticky="ew", padx=12, pady=(6, 4))
        ind.grid_columnconfigure(1, weight=1)
        self.ind_labels: dict[str, ctk.CTkLabel] = {}
        for row, (name, key) in enumerate((
                ("RSI (14)", "rsi"), ("MACD", "macd"), ("ATR (14)", "atr"),
                ("EMA 20", "ema20"), ("EMA 50", "ema50"), ("Bollinger", "bollinger"))):
            _label(ind, name, size=12, color=PALETTE["muted"]).grid(row=row, column=0, sticky="w", pady=1)
            value_lbl = _label(ind, "—", size=12, bold=True)
            value_lbl.grid(row=row, column=1, sticky="e", pady=1)
            self.ind_labels[key] = value_lbl
        self.rsi_bar = _bar(ind, PALETTE["green"], height=6)
        self.rsi_bar.grid(row=0, column=1, sticky="e", padx=(8, 64))
        self.levels_lbl = _label(left, "", size=11, color=PALETTE["muted"],
                                 wraplength=420, justify="left")
        self.levels_lbl.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 10))

        # Cột giữa: AI feed cuộn + khối trưởng phòng cố định ở đáy
        center = ctk.CTkFrame(tab, fg_color="transparent")
        center.grid(row=0, column=1, sticky="nsew", padx=6)
        center.grid_rowconfigure(1, weight=1)
        center.grid_columnconfigure(0, weight=1)
        feed_head = ctk.CTkFrame(center, fg_color="transparent")
        feed_head.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        _label(feed_head, "LIVE AI DEBATE FEED", size=14, bold=True, color=PALETTE["cyan"]).pack(side="left")
        self.feed_sync_lbl = _label(feed_head, "", size=11, color=PALETTE["muted"])
        self.feed_sync_lbl.pack(side="right")
        self.feed = ctk.CTkScrollableFrame(center, corner_radius=12, fg_color=PALETTE["panel"],
                                           border_width=1, border_color=PALETTE["border"],
                                           scrollbar_button_color=PALETTE["panel2"],
                                           scrollbar_button_hover_color=PALETTE["border"])
        self.feed.grid(row=1, column=0, sticky="nsew")
        self.feed.grid_columnconfigure(0, weight=1)
        self.cards: dict[str, dict] = {}
        for row, name in enumerate(ANALYST_NAMES):
            self.cards[name] = self._build_agent_card(self.feed, name)
        chief_frame = _frame(center, radius=12, color=PALETTE["panel"], border=True)
        chief_frame.configure(border_color=PALETTE["cyan"])
        chief_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        chief_head = ctk.CTkFrame(chief_frame, fg_color="transparent")
        chief_head.pack(fill="x", padx=12, pady=(10, 2))
        _label(chief_head, "◆ TRƯỞNG PHÒNG — QUYẾT ĐỊNH CUỐI", size=13, bold=True,
               color=PALETTE["cyan"]).pack(side="left")
        self.chief_title_lbl = _pill(chief_head, "—", PALETTE["grey"], size=11)
        self.chief_title_lbl.pack(side="right")
        self.chief_text = _textbox(chief_frame, height=78)
        self.chief_text.pack(fill="x", padx=12, pady=4)
        _set_text(self.chief_text, "Chưa có quyết định.")
        _button(chief_frame, "👁  Xem cách trưởng phòng làm việc",
                lambda: self._show_agent_detail("chief"), color=PALETTE["panel2"],
                size=12, height=30).pack(fill="x", padx=12, pady=(0, 10))

        # Cột phải: order ticket + kiểm định rủi ro + tin tức
        right = _frame(tab)
        right.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        right.grid_columnconfigure(0, weight=1)
        _label(right, "ORDER TICKET", size=14, bold=True, color=PALETTE["cyan"]).pack(
            anchor="w", padx=12, pady=(10, 2))
        self.ticket_title_lbl = _label(right, "XAUUSD • M15 • DRY-RUN", size=12, color=PALETTE["muted"])
        self.ticket_title_lbl.pack(anchor="w", padx=12)
        ticket = ctk.CTkFrame(right, fg_color="transparent")
        ticket.pack(fill="x", padx=12, pady=6)
        ticket.grid_columnconfigure(1, weight=1)
        self.ticket_labels: dict[str, ctk.CTkLabel] = {}
        for row, (caption, key) in enumerate((
                ("Hướng:", "direction"), ("Entry:", "entry"), ("Stop Loss:", "sl"),
                ("Take Profit:", "tp"), ("Lot (code tính):", "lot"),
                ("Độ tin cậy:", "confidence"), ("Rủi ro ước tính:", "risk"))):
            _label(ticket, caption, size=12, color=PALETTE["muted"]).grid(
                row=row, column=0, sticky="w", pady=2)
            value_lbl = _label(ticket, "—", size=12, bold=True)
            value_lbl.grid(row=row, column=1, sticky="e", pady=2)
            self.ticket_labels[key] = value_lbl
        self.risk_est_lbl = _label(right, "", size=11, color=PALETTE["muted"])
        self.risk_est_lbl.pack(fill="x", padx=12)
        _label(right, "KIỂM ĐỊNH RỦI RO", size=13, bold=True, color=PALETTE["cyan"]).pack(
            anchor="w", padx=12, pady=(8, 2))
        self.risk_text = _textbox(right, height=128)
        self.risk_text.pack(fill="x", padx=12)
        _set_text(self.risk_text, "Chưa chạy phiên nào.")
        _label(right, "TIN TỨC & COT", size=13, bold=True, color=PALETTE["cyan"]).pack(
            anchor="w", padx=12, pady=(8, 2))
        self.news_text = _textbox(right, height=110)
        self.news_text.pack(fill="x", padx=12, pady=(0, 4))
        _set_text(self.news_text, "Tin tức chỉ được lấy trong phiên thật (không phải mock).")
        self.order_lbl = _label(right, "", size=12, color=PALETTE["cyan"])
        self.order_lbl.pack(fill="x", padx=12, pady=(0, 10))

        # Hàng điều khiển: nguồn + mock + 6 nút lớn
        controls = _frame(tab, radius=12)
        controls.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        controls.grid_columnconfigure(0, weight=1)
        ctl_top = ctk.CTkFrame(controls, fg_color="transparent")
        ctl_top.pack(fill="x", padx=12, pady=(10, 4))
        _label(ctl_top, "NGUỒN DỮ LIỆU:", size=11, bold=True, color=PALETTE["muted"]).pack(side="left")
        self.source_var = tk.StringVar(value="MT5")
        self.source_seg = ctk.CTkSegmentedButton(
            ctl_top, values=["MT5", "JSON thử"], variable=self.source_var,
            font=F(13), selected_color=PALETTE["blue"], unselected_color=PALETTE["panel2"],
            text_color=PALETTE["text"], height=34, command=self._on_source_change)
        self.source_seg.pack(side="left", padx=8)
        self.mock_var = tk.BooleanVar(value=False)
        self.mock_box = ctk.CTkCheckBox(ctl_top, text="Dùng AI giả (mock, không tốn key)",
                                        variable=self.mock_var, font=F(13),
                                        text_color=PALETTE["text"], fg_color=PALETTE["blue"])
        self.mock_box.pack(side="left", padx=8)
        self.stop_lbl = _label(ctl_top, "STOP: TẮT", size=12, bold=True, color=PALETTE["grey"])
        self.stop_lbl.pack(side="right", padx=4)
        ctl_btns = ctk.CTkFrame(controls, fg_color="transparent")
        ctl_btns.pack(fill="x", padx=12, pady=(0, 12))
        for col in range(6):
            ctl_btns.grid_columnconfigure(col, weight=1)
        self.btn_run = _button(ctl_btns, "▶  Chạy 1 phiên", self._start_run, color=PALETTE["blue"])
        self.btn_run.grid(row=0, column=0, sticky="ew", padx=3)
        self.btn_loop = _button(ctl_btns, "↻  Chạy vòng lặp", self._start_loop)
        self.btn_loop.grid(row=0, column=1, sticky="ew", padx=3)
        self.btn_refresh = _button(ctl_btns, "⟳  Làm mới dữ liệu", self._refresh_data)
        self.btn_refresh.grid(row=0, column=2, sticky="ew", padx=3)
        self.btn_news = _button(ctl_btns, "📰  Tin tức & COT", self._refresh_news)
        self.btn_news.grid(row=0, column=3, sticky="ew", padx=3)
        self.btn_stop = _button(ctl_btns, "DỪNG KHẨN CẤP", self._create_stop, color=PALETTE["danger"])
        self.btn_stop.grid(row=0, column=4, sticky="ew", padx=3)
        self.btn_clear_stop = _button(ctl_btns, "Gỡ STOP", self._clear_stop,
                                      color=PALETTE["orange"], text_color="#0B1220")
        self.btn_clear_stop.grid(row=0, column=5, sticky="ew", padx=3)

    def _build_agent_card(self, parent, name: str) -> dict:
        info = AGENT_HOW[name]
        card = _frame(parent, radius=10)
        card.pack(fill="x", pady=4, padx=2)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(8, 2))
        badge = ctk.CTkLabel(head, text=f"[{name.upper()}]", font=F(12, True),
                             fg_color=info["badge"], text_color="white",
                             corner_radius=6, padx=8, pady=2)
        badge.pack(side="left")
        pill = _pill(head, "CHỜ", PALETTE["grey"], size=11)
        pill.pack(side="right")
        status_lbl = _label(card, info["role"], size=12, color=PALETTE["muted"])
        status_lbl.pack(fill="x", padx=10)
        bar = _bar(card, PALETTE["grey"])
        bar.pack(fill="x", padx=10, pady=4)
        reasoning_lbl = _label(card, "", size=12, wraplength=330, justify="left")
        reasoning_lbl.pack(fill="x", padx=10)
        _button(card, "👁  Xem cách AI này làm việc",
                lambda n=name: self._show_agent_detail(n), color=PALETTE["panel2"],
                size=12, height=30).pack(fill="x", padx=10, pady=(4, 10))
        return {"frame": card, "pill": pill, "status": status_lbl,
                "bar": bar, "reasoning": reasoning_lbl}

    # ---------- Tab 2: Nhật ký & lịch sử ----------
    def _build_tab2(self) -> None:
        tab = ctk.CTkFrame(self.content, fg_color="transparent")
        tab.grid_rowconfigure(1, weight=1)
        tab.grid_columnconfigure(0, weight=45)
        tab.grid_columnconfigure(1, weight=55)
        self.tab_frames["history"] = tab

        # Dải tổng kết: chỉ đếm phiên thật đã ghi, KHÔNG bịa tỉ lệ thắng/P&L
        # vì nhật ký phiên không ghi lãi/lỗ thực tế theo phiên.
        summary = _frame(tab, radius=12)
        summary.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        summary.grid_columnconfigure(tuple(range(5)), weight=1)
        self.summary_labels: dict[str, ctk.CTkLabel] = {}
        for col, (caption, key) in enumerate((
                ("PHIÊN ĐÃ GHI", "sessions"), ("BUY", "buy"), ("SELL", "sell"),
                ("HOLD", "hold"), ("RỦI RO CHO PHÉP", "pass"))):
            box = ctk.CTkFrame(summary, fg_color="transparent")
            box.grid(row=0, column=col, sticky="ew", padx=8, pady=8)
            _label(box, caption, size=11, color=PALETTE["muted"]).pack()
            value_lbl = _label(box, "0", size=20, bold=True)
            value_lbl.pack()
            self.summary_labels[key] = value_lbl

        left = _frame(tab)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        _label(left, "DANH SÁCH PHIÊN", size=14, bold=True, color=PALETTE["cyan"]).pack(
            anchor="w", padx=12, pady=(10, 4))
        btn_row = ctk.CTkFrame(left, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 6))
        _button(btn_row, "⟳  Làm mới", self._load_history, color=PALETTE["panel2"],
                size=12, height=32).pack(side="left", padx=(0, 6))
        _button(btn_row, "📂  Mở thư mục output", self._open_output, color=PALETTE["panel2"],
                size=12, height=32).pack(side="left")
        self.history_scroll = ctk.CTkScrollableFrame(
            left, corner_radius=10, fg_color=PALETTE["panel2"], border_width=0,
            scrollbar_button_color=PALETTE["border"])
        self.history_scroll.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.history_scroll.grid_columnconfigure(0, weight=1)
        self._history: list[tuple[str, dict]] = []

        right = _frame(tab)
        right.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        right.grid_columnconfigure(0, weight=1)
        _label(right, "CHI TIẾT PHIÊN", size=14, bold=True, color=PALETTE["cyan"]).pack(
            anchor="w", padx=12, pady=(10, 2))
        self.hist_summary_lbl = _label(right, "Chưa chọn phiên nào.", size=12, color=PALETTE["muted"])
        self.hist_summary_lbl.pack(anchor="w", padx=12)
        self.hist_cards: dict[str, dict] = {}
        for name in AGENT_NAMES:
            row = ctk.CTkFrame(right, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=2)
            row.grid_columnconfigure(1, weight=1)
            _label(row, f"[{name.upper()}]", size=11, bold=True).grid(row=0, column=0, sticky="w")
            bar = _bar(row, PALETTE["grey"], height=6)
            bar.grid(row=0, column=1, sticky="ew", padx=8)
            pill = _pill(row, "—", PALETTE["grey"], size=11)
            pill.grid(row=0, column=2, sticky="e")
            conf_lbl = _label(row, "", size=11, color=PALETTE["muted"])
            conf_lbl.grid(row=0, column=3, sticky="e", padx=(6, 0))
            self.hist_cards[name] = {"bar": bar, "pill": pill, "conf": conf_lbl}
        _label(right, "QUYẾT ĐỊNH TRƯỞNG PHÒNG", size=12, bold=True, color=PALETTE["cyan"]).pack(
            anchor="w", padx=12, pady=(8, 2))
        self.hist_chief_text = _textbox(right, height=64)
        self.hist_chief_text.pack(fill="x", padx=12)
        _set_text(self.hist_chief_text, "Chưa chọn phiên nào.")
        _label(right, "KIỂM ĐỊNH RỦI RO", size=12, bold=True, color=PALETTE["cyan"]).pack(
            anchor="w", padx=12, pady=(8, 2))
        self.hist_checks_text = _textbox(right, height=96)
        self.hist_checks_text.pack(fill="x", padx=12, pady=(0, 6))
        _set_text(self.hist_checks_text, "—")
        self.hist_order_lbl = _label(right, "", size=12, color=PALETTE["cyan"])
        self.hist_order_lbl.pack(fill="x", padx=12, pady=(0, 10))

    # ---------- Tab 3: Cấu hình & an toàn ----------
    def _build_tab3(self) -> None:
        tab = ctk.CTkFrame(self.content, fg_color="transparent")
        tab.grid_rowconfigure(0, weight=1)
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=1)
        self.tab_frames["config"] = tab
        self.entries: dict[str, ctk.CTkEntry] = {}

        def _row(parent, caption: str, key: str, default: str = "", disabled: bool = False):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=3)
            row.grid_columnconfigure(1, weight=1)
            _label(row, caption, size=12, color=PALETTE["muted"]).grid(row=0, column=0, sticky="w")
            entry = _entry(row, width=220)
            entry.grid(row=0, column=1, sticky="e")
            entry.insert(0, default)
            if disabled:
                entry.configure(state="disabled", fg_color=PALETTE["panel"],
                                text_color=PALETTE["muted"])
            self.entries[key] = entry
            return entry

        left = ctk.CTkScrollableFrame(tab, corner_radius=12, fg_color=PALETTE["panel"],
                                          border_width=1, border_color=PALETTE["border"],
                                          scrollbar_button_color=PALETTE["panel2"],
                                          scrollbar_button_hover_color=PALETTE["border"])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        _label(left, "CẤU HÌNH CHUNG", size=14, bold=True, color=PALETTE["cyan"]).pack(
            anchor="w", padx=12, pady=(10, 4))
        _row(left, "Symbol:", "symbol", "XAUUSD")
        _row(left, "Khung thời gian:", "timeframe", "M15")
        _row(left, "Chế độ (chỉ đọc):", "mode", "dry_run", disabled=True)
        _row(left, "Cho phép live (chỉ đọc):", "allow_live", "false", disabled=True)
        _row(left, "Nghỉ giữa 2 phiên vòng lặp (giây):", "loop_interval_seconds", "900")
        self.news_var = tk.BooleanVar(value=True)
        ctk.CTkSwitch(left, text="News enabled (lịch ForexFactory + RSS Fed)",
                      variable=self.news_var, font=F(12), text_color=PALETTE["text"],
                      fg_color=PALETTE["border"], progress_color=PALETTE["cyan"]).pack(
            anchor="w", padx=12, pady=4)
        self.sentiment_var = tk.BooleanVar(value=True)
        ctk.CTkSwitch(left, text="Sentiment enabled (COT + từ khóa tiêu đề)",
                      variable=self.sentiment_var, font=F(12), text_color=PALETTE["text"],
                      fg_color=PALETTE["border"], progress_color=PALETTE["cyan"]).pack(
            anchor="w", padx=12, pady=4)
        _label(left, "AI & MODEL (CHỈ ĐỌC)", size=13, bold=True, color=PALETTE["cyan"]).pack(
            anchor="w", padx=12, pady=(12, 2))
        self.agent_model_labels: list[ctk.CTkLabel] = []
        for _ in AGENT_NAMES:
            lbl = _label(left, "", size=12)
            lbl.pack(anchor="w", padx=12)
            self.agent_model_labels.append(lbl)
        _label(left, "NGUỒN DỮ LIỆU (CHỈ ĐỌC)", size=13, bold=True, color=PALETTE["cyan"]).pack(
            anchor="w", padx=12, pady=(12, 2))
        for line in ("Giá & nến: MT5 (máy Windows, terminal đã đăng nhập)",
                     "Lịch kinh tế: ForexFactory (XML) + RSS Fed",
                     "COT vàng: FuturesBench (dữ liệu CFTC, công khai)"):
            _label(left, "•  " + line, size=12, color=PALETTE["muted"]).pack(
                anchor="w", padx=12, pady=1)

        right = ctk.CTkScrollableFrame(tab, corner_radius=12, fg_color=PALETTE["panel"],
                                           border_width=1, border_color=PALETTE["border"],
                                           scrollbar_button_color=PALETTE["panel2"],
                                           scrollbar_button_hover_color=PALETTE["border"])
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        _label(right, "GIỚI HẠN RỦI RO", size=14, bold=True, color=PALETTE["cyan"]).pack(
            anchor="w", padx=12, pady=(10, 4))
        for field in EDITABLE_RISK_FIELDS:
            _row(right, field + ":", field, "")
        _label(right, "Đơn vị: USD/oz cho giá, USD cho tiền. SL/TP là giá tuyệt đối, không dùng pips.",
               size=11, color=PALETTE["muted"]).pack(anchor="w", padx=12, pady=4)
        _label(right, "AN TOÀN", size=13, bold=True, color=PALETTE["cyan"]).pack(
            anchor="w", padx=12, pady=(8, 2))
        self.stop_status_lbl = _label(right, "STOP: TẮT — app được phép chạy phiên.",
                                      size=12, color=PALETTE["green"])
        self.stop_status_lbl.pack(anchor="w", padx=12)
        self.keys_config_lbl = _label(right, "Key: đang kiểm tra...", size=12, color=PALETTE["muted"])
        self.keys_config_lbl.pack(anchor="w", padx=12, pady=(0, 6))
        safe_row = ctk.CTkFrame(right, fg_color="transparent")
        safe_row.pack(fill="x", padx=12, pady=4)
        _button(safe_row, "DỪNG KHẨN CẤP", self._create_stop, color=PALETTE["danger"],
                size=12, height=38).pack(side="left", padx=(0, 6), fill="x", expand=True)
        _button(safe_row, "Gỡ STOP", self._clear_stop, color=PALETTE["orange"],
                text_color="#0B1220", size=12, height=38).pack(side="left", fill="x", expand=True)
        _button(right, "Mở file .env bằng Notepad (điền key ở đó, không hiện trong app)",
                self._open_env, color=PALETTE["panel2"], size=12, height=38).pack(
            fill="x", padx=12, pady=4)
        self.btn_save = _button(right, "💾  Lưu cấu hình", self._save_config,
                                color=PALETTE["blue"], height=48, size=15)
        self.btn_save.pack(fill="x", padx=12, pady=(8, 12))
        self.config_msg_lbl = _label(right, "", size=12, color=PALETTE["cyan"])
        self.config_msg_lbl.pack(fill="x", padx=12, pady=(0, 10))

    # ---------- Hành động ----------
    def _source(self) -> str:
        return "mt5" if self.source_var.get() == "MT5" else "json"

    def _on_source_change(self, _value=None) -> None:
        if self._source() == "mt5":
            self.live.start()
        else:
            self.live.stop()
        self._refresh_status()

    def _start_run(self, loop: bool = False) -> None:
        if self.runner.is_running():
            return
        try:
            cfg = load_config(self.config_path)
        except Exception as exc:
            messagebox.showerror("Lỗi cấu hình", str(exc))
            return
        if cfg.live_enabled or cfg.mode == "live":
            messagebox.showerror("Từ chối chạy",
                                 "Chế độ live đã bật. App từ chối chạy để tránh bấm nhầm.")
            return
        self.live.pause()
        for name, card in self.cards.items():
            card["pill"].configure(text="CHỜ", fg_color=PALETTE["grey"], text_color="white")
            card["status"].configure(text=AGENT_HOW[name]["role"])
            card["reasoning"].configure(text="")
            _set_bar(card["bar"], 0.0, PALETTE["grey"])
        self._set_step(0)
        self.phase_lbl.configure(text="Đang chạy phiên...")
        self.runner.start(self._source(), bool(self.mock_var.get()),
                          "data/snapshot_example.json", loop)
        self._refresh_buttons()

    def _start_loop(self) -> None:
        self._start_run(loop=True)

    def _refresh_data(self) -> None:
        self.phase_lbl.configure(text="Đang làm mới dữ liệu...")
        self.runner.preview(self._source(), enrich=False)

    def _refresh_news(self) -> None:
        self.phase_lbl.configure(text="Đang lấy tin tức & COT...")
        self.runner.preview(self._source(), enrich=True)

    def _create_stop(self) -> None:
        try:
            cfg = load_config(self.config_path)
            stop_path = cfg.project_root / cfg.risk.kill_switch_file
            stop_path.write_text("STOP\n", encoding="utf-8")
            self.phase_lbl.configure(text="Đã tạo file STOP. Phiên mới sẽ không chạy, vòng lặp sẽ dừng.")
        except Exception as exc:
            messagebox.showerror("Lỗi", str(exc))
        self._refresh_status()

    def _clear_stop(self) -> None:
        try:
            cfg = load_config(self.config_path)
            stop_path = cfg.project_root / cfg.risk.kill_switch_file
            if stop_path.exists():
                stop_path.unlink()
            self.phase_lbl.configure(text="Đã gỡ STOP.")
        except Exception as exc:
            messagebox.showerror("Lỗi", str(exc))
        self._refresh_status()

    def _refresh_buttons(self) -> None:
        running = self.runner.is_running()
        state = "disabled" if running else "normal"
        for btn in (self.btn_run, self.btn_loop, self.btn_refresh, self.btn_news):
            btn.configure(state=state)

    def _set_step(self, step: int) -> None:
        for i, lbl in enumerate(self.step_labels):
            if i < step:
                lbl.configure(fg_color=PALETTE["green"], text_color="#0B1220")
            elif i == step:
                lbl.configure(fg_color=PALETTE["blue"], text_color="white")
            else:
                lbl.configure(fg_color=PALETTE["panel2"], text_color=PALETTE["muted"])

    def _change_zoom(self, delta: int) -> None:
        self.chart.visible = max(30, min(200, self.chart.visible + delta))
        self.chart.draw()
        self.zoom_lbl.configure(text=f"{self.chart.visible} nến")

    # ---------- Hiển thị dữ liệu ----------
    def _fmt_price(self, value) -> str:
        return "—" if value is None else f"{float(value):,.2f}"

    def _usd(self, value) -> str:
        return "—" if value is None else f"{float(value):,.2f} USD"

    def _update_market_bar(self, price: float, spread: float, balance: float, equity: float) -> None:
        self.price_lbl.configure(text=f"{price:,.2f} USD")
        if self._ref_close is None:
            self._ref_close = price
        change = price - self._ref_close
        pct = (change / self._ref_close * 100) if self._ref_close else 0.0
        self.change_lbl.configure(text=f"{change:+,.2f} ({pct:+.2f}%)",
                                  text_color=PALETTE["green"] if change >= 0 else PALETTE["red"])
        self.spread_lbl.configure(text=f"{spread:.2f}")
        self.bal_lbl.configure(text=f"{balance:,.2f} USD")
        self.eq_lbl.configure(text=f"{equity:,.2f} USD")
        self._last_balance = balance
        self._last_equity = equity

    def _show_snapshot(self, snap: dict) -> None:
        self._current_snapshot = snap
        self.symbol_lbl.configure(text=snap.get("symbol", "XAUUSD"))
        self.chart_title_lbl.configure(
            text=f"{snap.get('timeframe', '')} • {snap.get('source', '').upper()} • DRY-RUN")
        self.ticket_title_lbl.configure(
            text=f"{snap.get('symbol', '')} • {snap.get('timeframe', '')} • DRY-RUN")
        ind = snap.get("indicators") or {}

        def _num(*keys):
            for key in keys:
                value = ind.get(key)
                if value is not None:
                    try:
                        return float(value)
                    except (TypeError, ValueError):
                        return None
            return None

        rsi = _num("rsi14", "rsi")
        self.ind_labels["rsi"].configure(text="—" if rsi is None else f"{rsi:.1f}")
        _set_bar(self.rsi_bar, (rsi or 0.0) / 100.0,
                 PALETTE["green"] if (rsi or 50) <= 30 else
                 PALETTE["red"] if (rsi or 50) >= 70 else PALETTE["cyan"])
        macd = ind.get("macd")
        if isinstance(macd, dict):
            self.ind_labels["macd"].configure(
                text=f"{float(macd.get('line', 0) or 0):+.2f} / "
                     f"{float(macd.get('signal', 0) or 0):+.2f}")
        elif isinstance(macd, str):
            self.ind_labels["macd"].configure(text=macd[:44])
        else:
            line = _num("macd_line")
            signal = _num("macd_signal")
            self.ind_labels["macd"].configure(
                text="—" if line is None else f"{line:+.2f} / {signal:+.2f}"
                if signal is not None else f"{line:+.2f}")
        atr = _num("atr14_usd", "atr")
        self.ind_labels["atr"].configure(text="—" if atr is None else f"{atr:,.2f}")
        ema20 = _num("ema20")
        self.ind_labels["ema20"].configure(text="—" if ema20 is None else f"{ema20:,.2f}")
        ema50 = _num("ema50")
        self.ind_labels["ema50"].configure(text="—" if ema50 is None else f"{ema50:,.2f}")
        boll_upper = _num("bollinger_upper_usd", "bollinger_upper")
        boll_lower = _num("bollinger_lower_usd", "bollinger_lower")
        boll = ind.get("bollinger")
        if boll_upper is not None and boll_lower is not None:
            self.ind_labels["bollinger"].configure(
                text=f"{boll_lower:,.2f} – {boll_upper:,.2f}")
        elif isinstance(boll, dict) and boll.get("upper") is not None:
            self.ind_labels["bollinger"].configure(
                text=f"{float(boll['lower']):,.2f} – {float(boll['upper']):,.2f}")
        else:
            self.ind_labels["bollinger"].configure(text="—")
        support = ind.get("support") or []
        resistance = ind.get("resistance") or []
        self.levels_lbl.configure(
            text="Hỗ trợ: " + (", ".join(f"{float(v):,.2f}" for v in support) or "—")
            + "   •   Kháng cự: " + (", ".join(f"{float(v):,.2f}" for v in resistance) or "—"))
        candles = snap.get("candles") or []
        if candles:
            self.chart.set_candles(candles, support, resistance)
        self._update_market_bar(float(snap.get("price") or 0), float(snap.get("spread") or 0),
                                float(snap.get("account_balance") or 0),
                                float(snap.get("account_equity") or 0))
        news_parts = []
        if snap.get("news_text"):
            news_parts.append("TIN TỨC:\n" + str(snap["news_text"]))
        if snap.get("sentiment_text"):
            news_parts.append("SENTIMENT / COT:\n" + str(snap["sentiment_text"]))
        if news_parts:
            _set_text(self.news_text, "\n\n".join(news_parts))
        self.feed_sync_lbl.configure(
            text=f"Đồng bộ: {snap.get('captured_at', '')} • nguồn {snap.get('source', '')}")
        self.mt5_lbl.configure(text="MT5: ĐÃ KẾT NỐI • ONLINE", text_color=PALETTE["green"])

    def _set_card(self, name: str, view: dict, round_no: int) -> None:
        self._current_views[name] = view
        card = self.cards.get(name)
        if card is None:
            return
        direction = view.get("direction", "HOLD")
        conf = float(view.get("confidence") or 0.0)
        color = _direction_color(direction)
        card["pill"].configure(text=f"{direction} • CONF {int(conf * 100)}%",
                               fg_color=color, text_color="#0B1220")
        stage = "vòng 1" if round_no == 1 else "vòng 2 (tranh luận)" if round_no == 2 else ""
        card["status"].configure(text=f"{AGENT_HOW[name]['role']} — {stage}" if stage
                                else AGENT_HOW[name]["role"])
        _set_bar(card["bar"], conf, color)
        reasoning = str(view.get("reasoning") or "")
        card["reasoning"].configure(text=reasoning[:400])

    def _format_checks(self, checks: list) -> str:
        lines = []
        for check in checks or []:
            if isinstance(check, str):
                lines.append(check)
                continue
            ok = check.get("ok", check.get("passed"))
            mark = "✓ PASS" if ok else "✗ CHẶN"
            detail = check.get("detail") or ""
            lines.append(f"{mark} — {check.get('name', '')}" + (f": {detail}" if detail else ""))
        return "\n".join(lines)

    def _show_result(self, result: dict) -> None:
        for view in result.get("views", []):
            self._set_card(view["name"], view, view.get("round", 2))
        chief = result.get("chief") or {}
        direction = chief.get("direction", "HOLD")
        conf = float(chief.get("confidence") or 0.0)
        color = _direction_color(direction)
        self.chief_title_lbl.configure(text=f"{direction} • CONF {int(conf * 100)}%",
                                       fg_color=color, text_color="#0B1220")
        entry = self._usd(chief.get("entry"))
        sl = self._usd(chief.get("stop_loss"))
        tp = self._usd(chief.get("take_profit"))
        chief_lines = [
            str(chief.get("reasoning") or "Không có lý do."),
            f"Entry {entry} • SL {sl} • TP {tp}",
            f"Vô hiệu khi: {chief.get('invalidation') or '—'}",
        ]
        _set_text(self.chief_text, "\n".join(chief_lines))
        risk = result.get("risk") or {}
        self.ticket_labels["direction"].configure(text=direction)
        self.ticket_labels["entry"].configure(text=entry)
        self.ticket_labels["sl"].configure(text=sl)
        self.ticket_labels["tp"].configure(text=tp)
        lot = risk.get("lot")
        lot_value = None if lot is None else float(lot)
        self.ticket_labels["lot"].configure(
            text="—" if not lot_value else f"{lot_value:.2f}")
        self.ticket_labels["confidence"].configure(text=f"{int(conf * 100)}%")
        sl_distance = risk.get("sl_distance")
        sl_value = None if sl_distance is None else float(sl_distance)
        self.ticket_labels["risk"].configure(
            text="—" if not sl_value else f"{sl_value:.2f} USD")
        est = None
        if sl_value and lot_value:
            est = sl_value * lot_value * 100.0
        self.risk_est_lbl.configure(
            text="" if est is None else f"Lỗ tối đa ước tính nếu chạm SL: {est:,.2f} USD (chưa tính spread/phí).")
        checks_text = self._format_checks(risk.get("checks"))
        verdict = "CHO PHÉP" if risk.get("allowed") else "CHẶN"
        _set_text(self.risk_text,
                  f"Kết luận: {verdict} — {risk.get('reason', '')}\n\n{checks_text}")
        self.order_lbl.configure(text=str(result.get("order", "")))
        self._load_history()

    # ---------- Vòng lặp sự kiện ----------
    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "step":
                    self._set_step(int(payload))
                elif kind == "view":
                    self._set_card(payload["name"], payload, payload.get("round", 0))
                elif kind == "log":
                    text = str(payload or "")
                    current = self.phase_lbl.cget("text")
                    if text and (current.startswith("Lỗi:") or current.startswith("Realtime:")):
                        self.phase_lbl.configure(text=text)
                    elif text:
                        self.phase_lbl.configure(text=text)
                elif kind == "snapshot":
                    self._show_snapshot(payload)
                elif kind == "result":
                    self._show_result(payload)
                elif kind == "error":
                    self.phase_lbl.configure(text=f"Lỗi: {payload}", text_color=PALETTE["red"])
                elif kind == "tick":
                    bid = float(payload["bid"])
                    ask = float(payload["ask"])
                    price = (bid + ask) / 2
                    current = self.phase_lbl.cget("text")
                    if current.startswith("Lỗi:") or current.startswith("Realtime:"):
                        self.phase_lbl.configure(text="Realtime đang chạy.",
                                                 text_color=PALETTE["cyan"])
                    self.mt5_lbl.configure(text="MT5: ĐÃ KẾT NỐI • ONLINE",
                                           text_color=PALETTE["green"])
                    self.chart.update_tick(bid, ask)
                    if payload.get("candles"):
                        snap = self._current_snapshot or {}
                        ind = snap.get("indicators") or {}
                        self.chart.set_candles(payload["candles"],
                                               ind.get("support"), ind.get("resistance"))
                    balance = payload.get("balance")
                    equity = payload.get("equity")
                    self._update_market_bar(
                        price, ask - bid,
                        float(balance) if balance is not None else self._last_balance,
                        float(equity) if equity is not None else self._last_equity)
                elif kind == "live_error":
                    self.phase_lbl.configure(text=f"Realtime: {payload}",
                                             text_color=PALETTE["amber"])
                    self.mt5_lbl.configure(text="MT5: CHƯA KẾT NỐI", text_color=PALETTE["amber"])
                elif kind == "finished":
                    self._refresh_buttons()
                    self.live.resume()
                    self._refresh_status()
        except queue.Empty:
            pass
        except Exception as exc:
            # Một payload lỗi không được giết vòng lặp sự kiện: ghi lỗi rồi
            # vẫn hẹn lần quét tiếp theo.
            try:
                self.phase_lbl.configure(text=f"Lỗi hiển thị: {exc}",
                                         text_color=PALETTE["red"])
            except Exception:
                pass
        self.after(200, self._poll_events)

    def _tick_clock(self) -> None:
        self.clock_lbl.configure(text=datetime.now().strftime("%H:%M:%S  %d/%m/%Y"))
        self.after(1000, self._tick_clock)

    # ---------- Trạng thái / lịch sử / cấu hình ----------
    def _set_entry(self, entry: ctk.CTkEntry, text: str) -> None:
        was_disabled = str(entry.cget("state")) == "disabled"
        if was_disabled:
            entry.configure(state="normal")
        entry.delete(0, "end")
        entry.insert(0, text)
        if was_disabled:
            entry.configure(state="disabled")

    def _key_status(self) -> tuple[bool, bool, bool]:
        try:
            cfg = load_config(self.config_path)
            env_path = cfg.project_root / ".env"
            content = env_path.read_text(encoding="utf-8", errors="ignore") if env_path.exists() else ""
        except Exception:
            content = ""
        present = {}
        for line in content.splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                present[key.strip()] = value.strip().strip('"').strip("'")
        return (bool(present.get("OPENAI_API_KEY")), bool(present.get("GEMINI_API_KEY")),
                bool(present.get("ANTHROPIC_API_KEY")))

    def _refresh_status(self) -> None:
        try:
            cfg = load_config(self.config_path)
        except Exception:
            return
        mode = str(cfg.mode).upper()
        self.mode_pill.configure(text=mode,
                                 fg_color=PALETTE["red"] if cfg.mode == "live"
                                 else PALETTE["cyan"] if cfg.mode == "demo"
                                 else PALETTE["amber"],
                                 text_color="#0B1220" if cfg.mode != "demo" else "#0B1220")
        self.symbol_lbl.configure(text=cfg.symbol)
        self.chart_title_lbl.configure(text=f"{cfg.timeframe} • {cfg.data_source.upper()} • {mode}")
        self.ticket_title_lbl.configure(text=f"{cfg.symbol} • {cfg.timeframe} • {mode}")
        stop_exists = (cfg.project_root / cfg.risk.kill_switch_file).exists()
        self.stop_lbl.configure(text="STOP: ĐANG BẬT" if stop_exists else "STOP: TẮT",
                                text_color=PALETTE["red"] if stop_exists else PALETTE["grey"])
        self.stop_status_lbl.configure(
            text=("STOP: ĐANG BẬT — app sẽ không chạy phiên mới." if stop_exists
                  else "STOP: TẮT — app được phép chạy phiên."),
            text_color=PALETTE["red"] if stop_exists else PALETTE["green"])
        openai_ok, gemini_ok, claude_ok = self._key_status()
        keys_text = ("Key: OpenAI " + ("✓ có" if openai_ok else "✗ thiếu")
                     + " • Gemini " + ("✓ có" if gemini_ok else "✗ thiếu")
                     + " • Claude " + ("✓ có" if claude_ok else "✗ thiếu")
                     + "  (chỉ kiểm tra có/không, không hiện giá trị)")
        self.keys_lbl.configure(text=keys_text)
        self.keys_config_lbl.configure(text=keys_text)
        self._set_entry(self.entries["symbol"], cfg.symbol)
        self._set_entry(self.entries["timeframe"], cfg.timeframe)
        self._set_entry(self.entries["mode"], cfg.mode)
        self._set_entry(self.entries["allow_live"], str(cfg.allow_live).lower())
        self._set_entry(self.entries["loop_interval_seconds"], str(cfg.loop_interval_seconds))
        self.news_var.set(bool(cfg.news_enabled))
        self.sentiment_var.set(bool(cfg.sentiment_enabled))
        for field in EDITABLE_RISK_FIELDS:
            if field in self.entries:
                value = getattr(cfg.risk, field, "")
                self._set_entry(self.entries[field], str(value))
        agents = getattr(cfg, "agents", {}) or {}
        providers = getattr(cfg, "providers", {}) or {}
        for lbl, name in zip(self.agent_model_labels, AGENT_NAMES):
            agent = agents.get(name)
            if agent is not None:
                if isinstance(agent, dict):
                    provider = agent.get("provider", "—")
                    model = agent.get("model")
                else:
                    provider, model = agent.provider, getattr(agent, "model", None)
                if not model:
                    prov = providers.get(provider)
                    if isinstance(prov, dict):
                        model = prov.get("model")
                    elif prov is not None:
                        model = getattr(prov, "model", None)
                lbl.configure(text=f"{name}: {provider} • {model or '—'}")
            else:
                lbl.configure(text=f"{name}: —")

    def _load_history(self) -> None:
        output = Path(self.output_dir)
        files = sorted(output.glob("session_*.json"), reverse=True) if output.exists() else []
        self._history = []
        counts = {"sessions": 0, "buy": 0, "sell": 0, "hold": 0, "pass": 0}
        for path in files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            self._history.append((str(path), data))
            chief = data.get("chief") or {}
            direction = str(chief.get("direction", "HOLD")).upper()
            counts["sessions"] += 1
            if direction == "BUY":
                counts["buy"] += 1
            elif direction == "SELL":
                counts["sell"] += 1
            else:
                counts["hold"] += 1
            if (data.get("risk") or {}).get("allowed"):
                counts["pass"] += 1
        for key, lbl in self.summary_labels.items():
            lbl.configure(text=str(counts[key]))
        for child in self.history_scroll.winfo_children():
            child.destroy()
        if not self._history:
            _label(self.history_scroll, "Chưa có phiên nào được ghi.", size=12,
                   color=PALETTE["muted"]).pack(anchor="w", padx=4, pady=4)
            return
        for index, (_path, data) in enumerate(self._history):
            chief = data.get("chief") or {}
            direction = str(chief.get("direction", "HOLD")).upper()
            conf = int(float(chief.get("confidence") or 0.0) * 100)
            verdict = "PASS" if (data.get("risk") or {}).get("allowed") else "CHẶN"
            captured = str(data.get("timestamp_utc") or "")[:19]
            row_btn = ctk.CTkButton(
                self.history_scroll, text=f"{captured} — {direction} {conf}% • {verdict}",
                font=F(12), anchor="w", fg_color="transparent",
                hover_color=PALETTE["panel2"], text_color=PALETTE["text"], height=32,
                command=lambda i=index: self._on_history_select(i))
            row_btn.pack(fill="x", padx=2, pady=2)
        self._on_history_select(0)

    def _on_history_select(self, index: int) -> None:
        if index < 0 or index >= len(self._history):
            return
        _path, data = self._history[index]
        chief = data.get("chief") or {}
        risk = data.get("risk") or {}
        direction = str(chief.get("direction", "HOLD")).upper()
        conf = float(chief.get("confidence") or 0.0)
        verdict = "CHO PHÉP" if risk.get("allowed") else "CHẶN"
        self.hist_summary_lbl.configure(
            text=f"{data.get('timestamp_utc', '')} • {data.get('symbol', '')} "
                 f"({data.get('timeframe', '')}) • "
                 f"{direction} {int(conf * 100)}% • Rủi ro: {verdict} — {risk.get('reason', '')}")
        views = {str(v.get("name")): v for v in data.get("views", [])}
        views["chief"] = chief
        self._current_views = views
        for name, widgets in self.hist_cards.items():
            view = views.get(name) or {}
            view_dir = str(view.get("direction", "HOLD")).upper()
            view_conf = float(view.get("confidence") or 0.0)
            color = _direction_color(view_dir)
            widgets["pill"].configure(text=view_dir, fg_color=color, text_color="#0B1220")
            _set_bar(widgets["bar"], view_conf, color)
            widgets["conf"].configure(text=f"{int(view_conf * 100)}%")
        chief_entry = chief.get("entry", chief.get("entry_usd"))
        chief_sl = chief.get("stop_loss", chief.get("stop_loss_usd"))
        chief_tp = chief.get("take_profit", chief.get("take_profit_usd"))
        chief_lines = [
            str(chief.get("reasoning") or "Không có lý do."),
            f"Entry {self._usd(chief_entry)} • SL {self._usd(chief_sl)} • TP {self._usd(chief_tp)}",
            f"Vô hiệu khi: {chief.get('invalidation') or '—'}",
        ]
        _set_text(self.hist_chief_text, "\n".join(chief_lines))
        _set_text(self.hist_checks_text, self._format_checks(risk.get("checks")))
        self.hist_order_lbl.configure(text=str(data.get("order", "")))
        # Đồng bộ khối trưởng phòng ở tab 1 theo phiên đang xem.
        color = _direction_color(direction)
        self.chief_title_lbl.configure(text=f"{direction} • CONF {int(conf * 100)}%",
                                       fg_color=color, text_color="#0B1220")
        _set_text(self.chief_text, "\n".join(chief_lines))

    def _show_agent_detail(self, name: str) -> None:
        info = AGENT_HOW[name]
        view = self._current_views.get(name) or {}
        snap = self._current_snapshot or {}
        prompt_key = name if name in SYSTEM_PROMPTS else ("risk" if name == "risk_analyst" else name)
        system_prompt = SYSTEM_PROMPTS.get(prompt_key, "(không có system prompt)")
        parts = [
            f"AI: {name} — {info['role']}",
            f"Cách làm việc: {info['how']}",
            "",
            "SYSTEM PROMPT ĐÃ GỬI:",
            system_prompt,
            "",
            "SNAPSHOT ĐÃ GỬI (trích):",
            str(snap.get("prompt_text", ""))[:1500] or "(chưa có snapshot)",
            "",
            "CÂU TRẢ LỜI THÔ CỦA AI:",
            str(view.get("raw_text") or view.get("raw") or "(chưa có câu trả lời)"),
            "",
            "LÝ DO (đã phân tích):",
            str(view.get("reasoning") or "—"),
            f"Bằng chứng: {view.get('evidence') or []}",
        ]
        dialog = ctk.CTkToplevel(self)
        dialog.title(f"Cách {name} làm việc")
        dialog.geometry("760x560")
        dialog.configure(fg_color=PALETTE["bg"])
        text_box = _textbox(dialog, height=460)
        text_box.pack(fill="both", expand=True, padx=12, pady=12)
        _set_text(text_box, "\n".join(parts))
        _button(dialog, "Đóng", dialog.destroy, color=PALETTE["panel2"], size=13, height=38).pack(
            padx=12, pady=(0, 12))

    def _save_config(self) -> None:
        import shutil

        import yaml

        try:
            cfg = load_config(self.config_path)
            config_path = Path(self.config_path)
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            symbol = self.entries["symbol"].get().strip()
            timeframe = self.entries["timeframe"].get().strip().upper()
            if not symbol:
                raise ValueError("Symbol không được để trống.")
            if timeframe not in TIMEFRAMES:
                raise ValueError(f"Khung thời gian phải là một trong: {', '.join(TIMEFRAMES)}.")
            loop_interval = int(self.entries["loop_interval_seconds"].get().strip())
            if loop_interval <= 0:
                raise ValueError("Nghỉ giữa 2 phiên phải lớn hơn 0.")
            risk_values = {}
            for field, (low, high) in EDITABLE_RISK_FIELDS.items():
                value = float(self.entries[field].get().strip())
                if not low <= value <= high:
                    raise ValueError(f"{field} phải trong khoảng {low} – {high}.")
                risk_values[field] = value
            if config_path.exists():
                shutil.copy2(config_path, config_path.with_suffix(".yaml.bak"))
            raw["symbol"] = symbol
            raw["timeframe"] = timeframe
            raw["loop_interval_seconds"] = loop_interval
            raw["news_enabled"] = bool(self.news_var.get())
            raw["sentiment_enabled"] = bool(self.sentiment_var.get())
            raw.setdefault("risk", {}).update(risk_values)
            header = ("# Cấu hình phòng trade AI — sửa bằng app desktop, "
                      "bản cũ lưu ở config.yaml.bak\n")
            config_path.write_text(header + yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
                                   encoding="utf-8")
            load_config(self.config_path)  # kiểm tra đọc lại được
            self.config_msg_lbl.configure(text="Đã lưu cấu hình (backup: config.yaml.bak).",
                                          text_color=PALETTE["green"])
            self._refresh_status()
        except Exception as exc:
            self.config_msg_lbl.configure(text=f"Không lưu được: {exc}", text_color=PALETTE["red"])
            messagebox.showerror("Không lưu được cấu hình", str(exc))

    def _open_env(self) -> None:
        try:
            cfg = load_config(self.config_path)
            env_path = cfg.project_root / ".env"
            if not env_path.exists():
                messagebox.showinfo("Chưa có .env",
                                    "Chưa có file .env. Copy .env.example thành .env rồi mở lại.")
                return
            if os.name == "nt":
                os.startfile(str(env_path))  # noqa: S606 - file do chính user tạo
            else:
                import subprocess

                subprocess.run(["xdg-open", str(env_path)], check=False)
        except Exception as exc:
            messagebox.showerror("Lỗi", str(exc))

    def _open_output(self) -> None:
        try:
            output = Path(self.output_dir).resolve()
            output.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(output))  # noqa: S606 - thư mục output của app
            else:
                import subprocess

                subprocess.run(["xdg-open", str(output)], check=False)
        except Exception as exc:
            messagebox.showerror("Lỗi", str(exc))

    def _on_close(self) -> None:
        try:
            self.runner.stop()
        except Exception:
            pass
        try:
            self.live.stop()
        except Exception:
            pass
        self.destroy()


def main(config_path: str = "config.yaml", output_dir: str = "output") -> None:
    import argparse

    parser = argparse.ArgumentParser(description="App desktop Terminal Pro (CustomTkinter)")
    parser.add_argument("--config", default=config_path)
    parser.add_argument("--output-dir", default=output_dir)
    args = parser.parse_args()
    try:
        app = DesktopApp(config_path=args.config, output_dir=args.output_dir)
    except Exception as exc:
        print(f"Không mở được app desktop: {exc}")
        print("Cài thêm giao diện: py -3.13 -m pip install -r requirements.txt")
        raise
    try:
        app.mainloop()
    except tk.TclError:
        pass


if __name__ == "__main__":
    main()
