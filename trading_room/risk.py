from __future__ import annotations

import math
from dataclasses import dataclass

from .agents import AgentView
from .config import Config
from .market_snapshot import MarketSnapshot


@dataclass
class RiskDecision:
    allowed: bool
    reason: str
    lot: float = 0.0
    sl_distance: float = 0.0


def calculate_lot(cfg: Config, sl_distance: float) -> float:
    """Lot = mức lỗ tối đa / (khoảng cách SL * contract size). Trần 1.5 lot, sàn 0.01."""
    if sl_distance <= 0:
        return 0.0
    raw = cfg.risk.max_risk_usd_per_trade / (sl_distance * cfg.risk.contract_size)
    stepped = math.floor(raw / cfg.risk.lot_step) * cfg.risk.lot_step
    return round(max(cfg.risk.min_lot, min(cfg.risk.max_lot_per_order, stepped)), 2)


def check_risk(cfg: Config, snapshot: MarketSnapshot, chief: AgentView, views: list[AgentView]) -> RiskDecision:
    """Cửa rủi ro CỨNG bằng code. AI không thể vượt qua, kể cả trưởng phòng."""
    # 1. Công tắc dừng khẩn cấp
    if (cfg.project_root / cfg.risk.kill_switch_file).exists():
        return RiskDecision(False, "Có file STOP, dừng mọi lệnh.")
    # 2. Chế độ chạy
    if cfg.mode == "dry_run":
        pass  # dry_run vẫn qua cửa này để tính lot, nhưng executor sẽ không đặt lệnh thật.
    # 3. Spread
    if snapshot.spread > cfg.risk.max_spread_usd:
        return RiskDecision(False, f"Spread {snapshot.spread} vượt giới hạn {cfg.risk.max_spread_usd}.")
    # 4. Vị thế đang mở
    if snapshot.open_positions >= cfg.max_open_positions:
        return RiskDecision(False, f"Đã có {snapshot.open_positions} vị thế, tối đa {cfg.max_open_positions}.")
    # 5. Lỗ trong ngày
    if snapshot.daily_loss_usd >= cfg.risk.max_daily_loss_usd:
        return RiskDecision(False, f"Lỗ trong ngày {snapshot.daily_loss_usd} chạm giới hạn {cfg.risk.max_daily_loss_usd}.")
    # 6. Trưởng phòng HOLD hoặc thiếu tin cậy
    if chief.direction == "HOLD":
        return RiskDecision(False, "Trưởng phòng quyết HOLD.")
    if not chief.parse_ok:
        return RiskDecision(False, "Kết luận trưởng phòng lỗi JSON, không vào lệnh.")
    if chief.confidence < cfg.risk.min_confidence:
        return RiskDecision(False, f"Độ tin cậy {chief.confidence} dưới {cfg.risk.min_confidence}.")
    # 7. Đồng thuận: nếu đa số analyst ngược hướng trưởng phòng và tin cậy chưa rất cao thì HOLD.
    analyst_dirs = [v.direction for v in views if v.name != "chief" and v.direction in ("BUY", "SELL")]
    if analyst_dirs:
        opposite = sum(1 for d in analyst_dirs if d != chief.direction)
        if opposite > len(analyst_dirs) / 2 and chief.confidence < 0.8:
            return RiskDecision(False, "Đa số analyst ngược hướng trưởng phòng, độ tin cậy chưa đủ cao.")
    # 8. SL/TP hợp lệ
    if chief.stop_loss is None or chief.take_profit is None:
        return RiskDecision(False, "Thiếu stop loss hoặc take profit.")
    price = snapshot.price
    if chief.direction == "BUY":
        sl_distance = price - float(chief.stop_loss)
        tp_distance = float(chief.take_profit) - price
    else:
        sl_distance = float(chief.stop_loss) - price
        tp_distance = price - float(chief.take_profit)
    if sl_distance < cfg.risk.min_sl_distance_usd or sl_distance > cfg.risk.max_sl_distance_usd:
        return RiskDecision(False, f"Khoảng cách SL {sl_distance:.2f} ngoài {cfg.risk.min_sl_distance_usd}-{cfg.risk.max_sl_distance_usd} USD.")
    if tp_distance <= 0 or tp_distance / sl_distance < cfg.risk.min_reward_risk:
        return RiskDecision(False, f"Tỉ lệ lãi/lỗ {tp_distance / sl_distance:.2f} dưới {cfg.risk.min_reward_risk}.")
    # 9. Lỗ ngày + lệnh này không vượt giới hạn (ước tính xấu nhất = max_risk)
    if snapshot.daily_loss_usd + cfg.risk.max_risk_usd_per_trade > cfg.risk.max_daily_loss_usd:
        return RiskDecision(False, "Lệnh này có thể làm lỗ ngày vượt giới hạn.")
    lot = calculate_lot(cfg, sl_distance)
    if lot < cfg.risk.min_lot:
        return RiskDecision(False, "Lot tính ra dưới mức tối thiểu.")
    return RiskDecision(True, "Qua cửa rủi ro.", lot=lot, sl_distance=sl_distance)
