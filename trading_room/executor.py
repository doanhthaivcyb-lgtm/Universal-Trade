from __future__ import annotations

import os
from dataclasses import dataclass

from .agents import AgentView
from .config import Config
from .market_snapshot import MarketSnapshot


@dataclass
class OrderResult:
    executed: bool
    ticket: int | None
    message: str
    mode: str


class DryRunExecutor:
    """Không bao giờ đặt lệnh thật. Chỉ ghi lại lệnh đáng lẽ sẽ đặt."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def place(self, snapshot: MarketSnapshot, chief: AgentView, lot: float) -> OrderResult:
        return OrderResult(
            True,
            ticket=None,
            message=(
                f"[DRY-RUN] {chief.direction} {lot} lot {snapshot.symbol} "
                f"SL={chief.stop_loss} TP={chief.take_profit} — không đặt lệnh thật."
            ),
            mode="dry_run",
        )


class MT5Executor:
    """Executor MT5, chỉ chạy trên Windows có MetaTrader5. Mọi lệnh đều có SL/TP và magic."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def place(self, snapshot: MarketSnapshot, chief: AgentView, lot: float) -> OrderResult:
        try:
            import MetaTrader5 as mt5
        except ImportError:
            return OrderResult(False, None, "Chưa cài MetaTrader5 (chỉ Windows). Chuyển sang dry_run.", "dry_run")

        if not mt5.initialize(login=int(os.getenv("MT5_LOGIN", "0") or 0),
                              password=os.getenv("MT5_PASSWORD", ""),
                              server=os.getenv("MT5_SERVER", "")):
            return OrderResult(False, None, f"MT5 initialize thất bại: {mt5.last_error()}", "mt5")

        try:
            info = mt5.account_info()
            if info is None:
                return OrderResult(False, None, "Không đọc được account_info MT5.", "mt5")
            # Demo-only: nếu mode=demo mà tài khoản không phải demo thì chặn.
            if self.cfg.mode == "demo" and info.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
                return OrderResult(False, None, "Chế độ demo nhưng tài khoản MT5 không phải demo. Đã chặn.", "demo")
            if self.cfg.mode == "live" and not self.cfg.live_enabled:
                return OrderResult(False, None, "Chế độ live chưa được bật đủ 2 lớp (allow_live + LIVE_TRADING_ENABLED). Đã chặn.", "live")

            symbol = self.cfg.symbol
            # Một số broker dùng hậu tố, ví dụ XAUUSD.vx — symbol trong config phải khớp đúng tên trên MT5.
            if not mt5.symbol_select(symbol, True):
                return OrderResult(False, None, f"Không chọn được symbol {symbol} trên MT5.", "mt5")
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return OrderResult(False, None, f"Không đọc được tick {symbol}.", "mt5")
            # Giá realtime lúc đặt lệnh: nếu giá đã chạy quá xa so với lúc AI
            # thảo luận thì SL/TP đã cũ, hủy lệnh thay vì đuổi theo giá.
            fresh_price = (float(tick.bid) + float(tick.ask)) / 2
            if abs(fresh_price - snapshot.price) > self.cfg.risk.max_price_move_usd:
                return OrderResult(
                    False, None,
                    f"Giá đã chạy {abs(fresh_price - snapshot.price):.2f} USD khỏi giá lúc thảo luận "
                    f"({snapshot.price:.2f} -> {fresh_price:.2f}), vượt giới hạn "
                    f"{self.cfg.risk.max_price_move_usd} USD. Hủy lệnh.",
                    "mt5",
                )

            order_type = mt5.ORDER_TYPE_BUY if chief.direction == "BUY" else mt5.ORDER_TYPE_SELL
            price = tick.ask if chief.direction == "BUY" else tick.bid
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(lot),
                "type": order_type,
                "price": price,
                "sl": float(chief.stop_loss),
                "tp": float(chief.take_profit),
                "deviation": 20,
                "magic": self.cfg.magic_number,
                "comment": "AI trading room",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                detail = result._asdict() if result is not None else mt5.last_error()
                return OrderResult(False, None, f"order_send thất bại: {detail}", "mt5")
            return OrderResult(True, int(result.order), f"Đã đặt lệnh ticket {result.order}.", self.cfg.mode)
        finally:
            mt5.shutdown()


def get_executor(cfg: Config):
    if cfg.mode in ("demo", "live"):
        return MT5Executor(cfg)
    return DryRunExecutor(cfg)
