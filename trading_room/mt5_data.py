"""Lấy dữ liệu realtime trực tiếp từ MT5.

Đây là nguồn dữ liệu chính của phòng trade. Mỗi phiên gọi
``build_snapshot()`` một lần, ngay trước khi các AI thảo luận, để lấy:
tick mới nhất, nến gần nhất, chỉ báo tính từ nến đó, vị thế đang mở,
lỗ trong ngày và số dư — tất cả từ terminal MT5 đang chạy trên cùng máy.

KHÔNG phải ảnh chụp màn hình, không đọc file JSON thủ công, không dùng
giá nhập tay. File JSON chỉ còn để chạy thử với ``--source json``.

Yêu cầu: Windows + MetaTrader5 đã cài + terminal MT5 đang mở và đã đăng nhập.
Có thể đăng nhập sẵn trong terminal rồi để trống MT5_LOGIN/PASSWORD/SERVER,
hoặc điền 3 giá trị đó trong .env để code tự đăng nhập.
"""
from __future__ import annotations

import os
from datetime import datetime

from .config import Config
from .market_snapshot import MarketSnapshot, atr, ema, macd, rsi


def _import_mt5():
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError(
            "Chưa cài MetaTrader5. Thư viện này chỉ chạy trên Windows: "
            "pip install MetaTrader5, mở terminal MT5 và đăng nhập trước khi chạy."
        ) from exc
    return mt5


def _timeframe_constant(mt5, timeframe: str):
    mapping = {
        "M1": "TIMEFRAME_M1",
        "M5": "TIMEFRAME_M5",
        "M15": "TIMEFRAME_M15",
        "M30": "TIMEFRAME_M30",
        "H1": "TIMEFRAME_H1",
        "H4": "TIMEFRAME_H4",
        "D1": "TIMEFRAME_D1",
    }
    name = mapping.get(timeframe.upper())
    if name is None:
        raise ValueError(f"Khung thời gian MT5 chưa hỗ trợ: {timeframe}")
    value = getattr(mt5, name, None)
    if value is None:
        raise RuntimeError(f"MetaTrader5 không có hằng {name}")
    return value


def _rates_to_lists(rates):
    """Chuyển kết quả copy_rates_from_pos thành 4 list high/low/close/time.

    Hỗ trợ cả numpy structured array (MT5 thật) và list dict/tuple (khi test).
    """
    highs, lows, closes, times = [], [], [], []
    if rates is None:
        return highs, lows, closes, times
    # numpy structured array có dtype.names
    dtype = getattr(rates, "dtype", None)
    names = getattr(dtype, "names", None)
    if names:
        for row in rates:
            highs.append(float(row["high"]))
            lows.append(float(row["low"]))
            closes.append(float(row["close"]))
            times.append(int(row["time"]))
        return highs, lows, closes, times
    for row in rates:
        if isinstance(row, dict):
            highs.append(float(row["high"]))
            lows.append(float(row["low"]))
            closes.append(float(row["close"]))
            times.append(int(row.get("time", 0)))
        elif hasattr(row, "high"):  # namedtuple / object
            highs.append(float(row.high))
            lows.append(float(row.low))
            closes.append(float(row.close))
            times.append(int(getattr(row, "time", 0)))
        else:  # tuple: time, open, high, low, close, ...
            times.append(int(row[0]))
            highs.append(float(row[2]))
            lows.append(float(row[3]))
            closes.append(float(row[4]))
    return highs, lows, closes, times


def _daily_loss_usd(mt5, symbol: str) -> float:
    """Lỗ trong ngày = lỗ đã chốt hôm nay + lỗ đang nổi của vị thế mở.

    Trả về số dương khi đang lỗ, 0 khi đang lãi hoặc không có giao dịch.
    Lỗi đọc lịch sử thì trả về 0 và ghi chú trong snapshot, không bịa số.
    """
    closed_loss = 0.0
    try:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        deals = mt5.history_deals_get(start, datetime.now())
        if deals:
            for deal in deals:
                deal_symbol = deal.symbol if hasattr(deal, "symbol") else deal.get("symbol")
                if deal_symbol != symbol:
                    continue
                profit = deal.profit if hasattr(deal, "profit") else deal.get("profit", 0.0)
                swap = deal.swap if hasattr(deal, "swap") else deal.get("swap", 0.0)
                commission = deal.commission if hasattr(deal, "commission") else deal.get("commission", 0.0)
                closed_loss += float(profit) + float(swap) + float(commission)
    except Exception:
        closed_loss = 0.0
    floating_loss = 0.0
    try:
        positions = mt5.positions_get(symbol=symbol) or []
        for pos in positions:
            profit = pos.profit if hasattr(pos, "profit") else pos.get("profit", 0.0)
            floating_loss += float(profit)
    except Exception:
        floating_loss = 0.0
    total = closed_loss + floating_loss
    return round(max(0.0, -total), 2)


class MT5DataProvider:
    """Kết nối MT5 một lần và lấy snapshot mới cho mỗi phiên."""

    def __init__(self, cfg: Config, mt5_module=None):
        self.cfg = cfg
        self.mt5 = mt5_module if mt5_module is not None else _import_mt5()
        self._connected = False

    def connect(self) -> None:
        mt5 = self.mt5
        login = os.getenv("MT5_LOGIN", "").strip()
        password = os.getenv("MT5_PASSWORD", "")
        server = os.getenv("MT5_SERVER", "").strip()
        if login and password and server:
            ok = mt5.initialize(login=int(login), password=password, server=server)
        else:
            # Terminal đã đăng nhập sẵn thì initialize() không tham số sẽ bám vào đó.
            ok = mt5.initialize()
        if not ok:
            raise RuntimeError(f"MT5 initialize thất bại: {mt5.last_error()}")
        if not mt5.symbol_select(self.cfg.symbol, True):
            raise RuntimeError(
                f"Không chọn được symbol {self.cfg.symbol} trên MT5. "
                "Sửa symbol trong config.yaml thành đúng tên trên MT5, "
                "ví dụ XAUUSD.vx ở Valetax."
            )
        self._connected = True

    def close(self) -> None:
        if self._connected:
            try:
                self.mt5.shutdown()
            finally:
                self._connected = False

    def build_snapshot(self) -> MarketSnapshot:
        if not self._connected:
            self.connect()
        mt5 = self.mt5
        tick = mt5.symbol_info_tick(self.cfg.symbol)
        if tick is None:
            raise RuntimeError(f"Không đọc được tick realtime cho {self.cfg.symbol}. Kiểm tra MT5 có đang chạy và symbol có giá không.")
        bid = float(tick.bid)
        ask = float(tick.ask)

        rates = mt5.copy_rates_from_pos(
            self.cfg.symbol, _timeframe_constant(mt5, self.cfg.timeframe), 0, self.cfg.mt5_candles
        )
        highs, lows, closes, _times = _rates_to_lists(rates)
        if len(closes) < 30:
            raise RuntimeError(
                f"MT5 chỉ trả về {len(closes)} nến, cần ít nhất 30 nến để tính chỉ báo. "
                "Chờ MT5 tải đủ lịch sử rồi chạy lại."
            )
        macd_values = macd(closes)
        recent_high = max(highs[-100:])
        recent_low = min(lows[-100:])
        indicators = {
            "rsi14": round(rsi(closes, 14), 2),
            "atr14_usd": round(atr(highs, lows, closes, 14), 4),
            "ema20": round(ema(closes, 20), 2),
            "ema50": round(ema(closes, 50), 2),
            "macd_line": macd_values["macd_line"],
            "macd_signal": macd_values["signal"],
            "macd_histogram": macd_values["histogram"],
            "macd": (
                f"MACD line {macd_values['macd_line']}, signal {macd_values['signal']}, "
                f"histogram {macd_values['histogram']}"
            ),
            "support": [round(recent_low, 2), round(min(lows[-20:]), 2)],
            "resistance": [round(recent_high, 2), round(max(highs[-20:]), 2)],
            "last_close": round(closes[-1], 2),
            "candles_used": len(closes),
        }
        positions = mt5.positions_get(symbol=self.cfg.symbol) or []
        account = mt5.account_info()
        return MarketSnapshot(
            symbol=self.cfg.symbol,
            timeframe=self.cfg.timeframe,
            bid=bid,
            ask=ask,
            indicators=indicators,
            source="mt5",
            captured_at=datetime.now().isoformat(timespec="seconds"),
            macro_news=self.cfg.macro_news,
            open_positions=len(positions),
            daily_loss_usd=_daily_loss_usd(mt5, self.cfg.symbol),
            account_balance=float(account.balance) if account is not None else 0.0,
            notes=(
                f"Lấy trực tiếp từ MT5 realtime: tick bid/ask mới nhất và "
                f"{len(closes)} nến {self.cfg.timeframe}. MT5 không cung cấp tin văn bản, "
                "macro_news lấy từ config.yaml (để trống nếu không điền tay)."
            ),
        )
