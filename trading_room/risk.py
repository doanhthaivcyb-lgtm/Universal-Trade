from __future__ import annotations

import math
from dataclasses import dataclass, field

from .agents import AgentView
from .config import Config
from .market_snapshot import MarketSnapshot


@dataclass
class RiskDecision:
    allowed: bool
    reason: str
    lot: float = 0.0
    sl_distance: float = 0.0
    # Kết quả từng kiểm tra, theo đúng thứ tự trong check_risk. UI hiển thị
    # đúng danh sách này, không tự suy ra kiểm tra nào đã chạy.
    checks: list = field(default_factory=list)


def portfolio_notional_usd(snapshot: MarketSnapshot, contract_size: float) -> float:
    """Tổng giá trị danh nghĩa của mọi vị thế đang mở, tính bằng USD.

    portfolio["positions"] là list dict {symbol, volume, price}. Nếu không có
    dữ liệu danh mục (chạy thử JSON cũ) thì trả về 0, không bịa.
    """
    total = 0.0
    for pos in (snapshot.portfolio or {}).get("positions", []):
        try:
            total += abs(float(pos.get("volume", 0.0))) * float(pos.get("price", 0.0)) * contract_size
        except (TypeError, ValueError):
            continue
    return round(total, 2)


def calculate_lot(cfg: Config, sl_distance: float) -> float:
    """Lot = mức lỗ tối đa (USD) / (khoảng cách SL tính bằng USD * contract size). Trần 1.5 lot, sàn 0.01.
    Mọi khoảng cách đều tính bằng USD, KHÔNG dùng pips."""
    if sl_distance <= 0:
        return 0.0
    raw = cfg.risk.max_risk_usd_per_trade / (sl_distance * cfg.risk.contract_size)
    stepped = math.floor(raw / cfg.risk.lot_step) * cfg.risk.lot_step
    return round(max(cfg.risk.min_lot, min(cfg.risk.max_lot_per_order, stepped)), 2)


def check_risk(cfg: Config, snapshot: MarketSnapshot, chief: AgentView, views: list[AgentView]) -> RiskDecision:
    """Cửa rủi ro CỨNG bằng code. AI không thể vượt qua, kể cả trưởng phòng.

    Mỗi kiểm tra được ghi lại vào checks. Kiểm tra trượt thì dừng ngay tại đó,
    các kiểm tra sau chưa chạy sẽ không có trong danh sách.
    """
    checks: list[dict] = []

    def fail(name: str, reason: str) -> RiskDecision:
        checks.append({"name": name, "passed": False, "detail": reason})
        return RiskDecision(False, reason, checks=checks)

    def ok(name: str, detail: str) -> None:
        checks.append({"name": name, "passed": True, "detail": detail})

    # 1. Công tắc dừng khẩn cấp
    if (cfg.project_root / cfg.risk.kill_switch_file).exists():
        return fail("Công tắc dừng khẩn cấp (file STOP)", "Có file STOP, dừng mọi lệnh.")
    ok("Công tắc dừng khẩn cấp (file STOP)", "Không có file STOP.")
    # 2. Chế độ chạy
    if cfg.mode not in ("dry_run", "demo", "live"):
        return fail("Chế độ chạy hợp lệ", f"Chế độ {cfg.mode} không hợp lệ.")
    ok("Chế độ chạy hợp lệ", f"Chế độ {cfg.mode}.")
    # 3. Spread
    if snapshot.spread > cfg.risk.max_spread_usd:
        return fail("Spread trong giới hạn", f"Spread {snapshot.spread} vượt giới hạn {cfg.risk.max_spread_usd}.")
    ok("Spread trong giới hạn", f"Spread {snapshot.spread} USD.")
    # 4. Vị thế đang mở
    if snapshot.open_positions >= cfg.max_open_positions:
        return fail("Số vị thế đang mở", f"Đã có {snapshot.open_positions} vị thế, tối đa {cfg.max_open_positions}.")
    ok("Số vị thế đang mở", f"{snapshot.open_positions} vị thế.")
    # 5. Lỗ trong ngày
    if snapshot.daily_loss_usd >= cfg.risk.max_daily_loss_usd:
        return fail("Lỗ trong ngày", f"Lỗ trong ngày {snapshot.daily_loss_usd} chạm giới hạn {cfg.risk.max_daily_loss_usd}.")
    ok("Lỗ trong ngày", f"Lỗ trong ngày {snapshot.daily_loss_usd} USD.")
    # 6. Trưởng phòng HOLD hoặc thiếu tin cậy
    if chief.direction == "HOLD":
        return fail("Trưởng phòng không HOLD", "Trưởng phòng quyết HOLD.")
    if not chief.parse_ok:
        return fail("Trưởng phòng không HOLD", "Kết luận trưởng phòng lỗi JSON, không vào lệnh.")
    if chief.confidence < cfg.risk.min_confidence:
        return fail("Trưởng phòng không HOLD", f"Độ tin cậy {chief.confidence} dưới {cfg.risk.min_confidence}.")
    ok("Trưởng phòng không HOLD", f"{chief.direction}, tin cậy {chief.confidence}.")
    # 7. Đồng thuận: nếu đa số analyst ngược hướng trưởng phòng và tin cậy chưa rất cao thì HOLD.
    analyst_dirs = [v.direction for v in views if v.name != "chief" and v.direction in ("BUY", "SELL")]
    if analyst_dirs:
        opposite = sum(1 for d in analyst_dirs if d != chief.direction)
        if opposite > len(analyst_dirs) / 2 and chief.confidence < 0.8:
            return fail("Đồng thuận analyst", "Đa số analyst ngược hướng trưởng phòng, độ tin cậy chưa đủ cao.")
    ok("Đồng thuận analyst", "Không có đa số ngược hướng, hoặc tin cậy đủ cao.")
    # 8. SL/TP hợp lệ — tất cả đều là giá tuyệt đối bằng USD/oz, KHÔNG dùng pips.
    # Kiểm tra thêm: SL/TP phải nằm trong khoảng giá hợp lý quanh giá hiện tại,
    # để loại trường hợp AI trả về khoảng cách pips/điểm thay vì giá tuyệt đối.
    if chief.stop_loss is None or chief.take_profit is None:
        return fail("SL/TP hợp lệ (USD)", "Thiếu stop loss hoặc take profit.")
    price = snapshot.price
    for label, value in (("stop_loss", chief.stop_loss), ("take_profit", chief.take_profit)):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return fail("SL/TP hợp lệ (USD)", f"{label} không phải số USD hợp lệ.")
        if v <= 0 or abs(v - price) > cfg.risk.max_sl_distance_usd * 5:
            return fail("SL/TP hợp lệ (USD)", f"{label} {v} quá xa giá hiện tại {price}, có thể AI đã trả về pips/khoảng cách thay vì giá USD.")
    if chief.direction == "BUY":
        sl_distance = price - float(chief.stop_loss)
        tp_distance = float(chief.take_profit) - price
    else:
        sl_distance = float(chief.stop_loss) - price
        tp_distance = price - float(chief.take_profit)
    if sl_distance < cfg.risk.min_sl_distance_usd or sl_distance > cfg.risk.max_sl_distance_usd:
        return fail("SL/TP hợp lệ (USD)", f"Khoảng cách SL {sl_distance:.2f} ngoài {cfg.risk.min_sl_distance_usd}-{cfg.risk.max_sl_distance_usd} USD.")
    if tp_distance <= 0 or tp_distance / sl_distance < cfg.risk.min_reward_risk:
        return fail("SL/TP hợp lệ (USD)", f"Tỉ lệ lãi/lỗ {tp_distance / sl_distance:.2f} dưới {cfg.risk.min_reward_risk}.")
    ok("SL/TP hợp lệ (USD)", f"Khoảng cách SL {sl_distance:.2f} USD, TP {tp_distance:.2f} USD.")
    # 9. Lỗ ngày + lệnh này không vượt giới hạn (ước tính xấu nhất = max_risk)
    if snapshot.daily_loss_usd + cfg.risk.max_risk_usd_per_trade > cfg.risk.max_daily_loss_usd:
        return fail("Lỗ ngày + lệnh mới", "Lệnh này có thể làm lỗ ngày vượt giới hạn.")
    ok("Lỗ ngày + lệnh mới", "Lỗ ngày cộng rủi ro tối đa của lệnh vẫn trong giới hạn.")
    lot = calculate_lot(cfg, sl_distance)
    if lot < cfg.risk.min_lot:
        return fail("Lot tính ra hợp lệ", "Lot tính ra dưới mức tối thiểu.")
    # 10. Exposure danh mục: danh mục hiện tại + lệnh mới không vượt giới hạn.
    existing_notional = portfolio_notional_usd(snapshot, cfg.risk.contract_size)
    new_notional = lot * snapshot.price * cfg.risk.contract_size
    if existing_notional + new_notional > cfg.risk.max_portfolio_notional_usd:
        return fail(
            "Exposure danh mục",
            f"Exposure danh mục {existing_notional:,.0f} USD + lệnh mới {new_notional:,.0f} USD "
            f"vượt giới hạn {cfg.risk.max_portfolio_notional_usd:,.0f} USD.",
        )
    ok("Exposure danh mục", f"Danh mục {existing_notional:,.0f} USD + lệnh mới {new_notional:,.0f} USD trong giới hạn.")
    return RiskDecision(True, "Qua cửa rủi ro.", lot=lot, sl_distance=sl_distance, checks=checks)
