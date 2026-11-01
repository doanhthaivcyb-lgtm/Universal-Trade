from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MarketSnapshot:
    symbol: str
    timeframe: str
    bid: float
    ask: float
    # Các chỉ báo do người chạy cung cấp hoặc do MT5 tính. AI không được tự bịa số.
    indicators: dict = field(default_factory=dict)
    macro_news: str = ""
    open_positions: int = 0
    daily_loss_usd: float = 0.0
    account_balance: float = 0.0
    notes: str = ""

    @property
    def price(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        return round(self.ask - self.bid, 4)

    def to_prompt_text(self) -> str:
        return (
            f"Symbol: {self.symbol}, khung: {self.timeframe}\n"
            f"Bid: {self.bid}, Ask: {self.ask}, Spread: {self.spread}\n"
            f"Chỉ báo: {json.dumps(self.indicators, ensure_ascii=False)}\n"
            f"Vĩ mô/tin: {self.macro_news}\n"
            f"Vị thế đang mở: {self.open_positions}, lỗ trong ngày: {self.daily_loss_usd} USD, "
            f"số dư: {self.account_balance} USD\n"
            f"Ghi chú: {self.notes}"
        )


def load_snapshot(path: str | Path) -> MarketSnapshot:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return MarketSnapshot(
        symbol=data["symbol"],
        timeframe=data.get("timeframe", "M15"),
        bid=float(data["bid"]),
        ask=float(data["ask"]),
        indicators=data.get("indicators", {}),
        macro_news=data.get("macro_news", ""),
        open_positions=int(data.get("open_positions", 0)),
        daily_loss_usd=float(data.get("daily_loss_usd", 0.0)),
        account_balance=float(data.get("account_balance", 0.0)),
        notes=data.get("notes", ""),
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
