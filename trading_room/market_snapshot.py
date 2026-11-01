from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


# "Snapshot" ở đây KHÔNG phải ảnh chụp màn hình. Nó là một bản ghi dữ liệu
# thị trường tại một thời điểm: giá bid/ask, chỉ báo, vị thế, lỗ trong ngày.
# Bản ghi này được tạo mới trực tiếp từ MT5 mỗi phiên (xem mt5_data.py),
# hoặc đọc từ file JSON chỉ để chạy thử. AI chỉ đọc bản ghi này, không xem ảnh.
@dataclass
class MarketSnapshot:
    symbol: str
    timeframe: str
    bid: float
    ask: float
    # Các chỉ báo do MT5 tính trực tiếp từ nến realtime. AI không được tự bịa số.
    indicators: dict = field(default_factory=dict)
    source: str = "json"  # "mt5" khi lấy trực tiếp từ MT5, "json" khi đọc file thử
    captured_at: str = ""
    macro_news: str = ""
    # Tin tức và sentiment lấy được từ nguồn công khai (xem news_fetcher /
    # sentiment_data). Để trống khi chưa lấy được — AI không được bịa.
    news_text: str = ""
    sentiment_text: str = ""
    # Danh mục toàn tài khoản từ MT5: tổng exposure, vị thế từng symbol.
    # Dùng cho Portfolio Manager và cửa rủi ro exposure.
    portfolio: dict = field(default_factory=dict)
    open_positions: int = 0
    daily_loss_usd: float = 0.0
    account_balance: float = 0.0
    account_equity: float = 0.0
    notes: str = ""
    # Nến OHLCV để vẽ biểu đồ trong app desktop. Mỗi nến là dict:
    # {"time": int, "open": float, "high": float, "low": float,
    #  "close": float, "volume": float}. Chỉ có khi lấy từ MT5; file JSON
    # thử thường không có nến thì biểu đồ trống, không bịa nến.
    candles: list = field(default_factory=list)

    @property
    def price(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        return round(self.ask - self.bid, 4)

    def to_prompt_text(self) -> str:
        return (
            f"Symbol: {self.symbol}, khung: {self.timeframe}, nguồn dữ liệu: {self.source}, "
            f"thời điểm lấy dữ liệu: {self.captured_at}\n"
            f"Bid: {self.bid}, Ask: {self.ask}, Spread: {self.spread}\n"
            f"Chỉ báo: {json.dumps(self.indicators, ensure_ascii=False)}\n"
            f"Vĩ mô/tin điền tay: {self.macro_news}\n"
            f"{self.news_text}\n"
            f"{self.sentiment_text}\n"
            f"Danh mục toàn tài khoản: {json.dumps(self.portfolio, ensure_ascii=False)}\n"
            f"Vị thế đang mở (symbol này): {self.open_positions}, lỗ trong ngày: {self.daily_loss_usd} USD, "
            f"số dư: {self.account_balance} USD, equity: {self.account_equity} USD\n"
            f"Ghi chú: {self.notes}\n"
            f"ĐƠN VỊ GIÁ: mọi entry, stop loss, take profit đều là giá tuyệt đối tính bằng USD/oz. "
            f"KHÔNG dùng pips, KHÔNG dùng điểm. Ví dụ: giá 4000 USD, stop loss 3990 USD nghĩa là cách 10 USD."
        )


def load_snapshot(path: str | Path) -> MarketSnapshot:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    candles = []
    for row in data.get("candles", []) or []:
        try:
            candles.append({
                "time": int(row.get("time", 0)),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", row.get("tick_volume", 0.0)) or 0.0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return MarketSnapshot(
        symbol=data["symbol"],
        timeframe=data.get("timeframe", "M15"),
        bid=float(data["bid"]),
        ask=float(data["ask"]),
        indicators=data.get("indicators", {}),
        macro_news=data.get("macro_news", ""),
        news_text=data.get("news_text", ""),
        sentiment_text=data.get("sentiment_text", ""),
        portfolio=data.get("portfolio", {}),
        open_positions=int(data.get("open_positions", 0)),
        daily_loss_usd=float(data.get("daily_loss_usd", 0.0)),
        account_balance=float(data.get("account_balance", 0.0)),
        account_equity=float(data.get("account_equity", data.get("account_balance", 0.0))),
        notes=data.get("notes", ""),
        candles=candles,
    )


def ema(values: list[float], period: int) -> float:
    """EMA đơn giản, dùng khi tính chỉ báo từ nến MT5."""
    if not values:
        return 0.0
    k = 2 / (period + 1)
    result = values[0]
    for v in values[1:]:
        result = v * k + result * (1 - k)
    return result


def rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Average True Range, tính từ nến MT5."""
    if len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    if not trs:
        return 0.0
    return sum(trs[-period:]) / min(period, len(trs))


def bollinger(closes: list[float], period: int = 20, std_mult: float = 2.0) -> dict:
    """Bollinger Bands từ giá đóng cửa: SMA20 ± 2 độ lệch chuẩn."""
    if len(closes) < period:
        return {"bollinger_upper_usd": 0.0, "bollinger_middle_usd": 0.0, "bollinger_lower_usd": 0.0}
    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((v - middle) ** 2 for v in window) / period
    band = std_mult * (variance ** 0.5)
    return {
        "bollinger_upper_usd": round(middle + band, 2),
        "bollinger_middle_usd": round(middle, 2),
        "bollinger_lower_usd": round(middle - band, 2),
    }


def macd(closes: list[float]) -> dict:
    """MACD(12,26,9) từ giá đóng cửa. Trả về dict có macd_line/signal/histogram."""
    if len(closes) < 26:
        return {"macd_line": 0.0, "signal": 0.0, "histogram": 0.0}
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    line = [a - b for a, b in zip(ema12, ema26)]
    signal = _ema_series(line, 9)
    return {
        "macd_line": round(line[-1], 4),
        "signal": round(signal[-1], 4),
        "histogram": round(line[-1] - signal[-1], 4),
    }
