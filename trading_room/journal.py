from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .agents import AgentView
from .market_snapshot import MarketSnapshot
from .risk import RiskDecision


def save_session(output_dir: Path, snapshot: MarketSnapshot, views: list[AgentView],
                 chief: AgentView, risk: RiskDecision, order_message: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    data = {
        "timestamp_utc": stamp,
        "symbol": snapshot.symbol,
        "timeframe": snapshot.timeframe,
        "snapshot": snapshot.to_prompt_text(),
        "views": [
            {
                "name": v.name, "provider": v.provider, "model": v.model,
                "direction": v.direction, "confidence": v.confidence,
                "entry": v.entry, "stop_loss": v.stop_loss, "take_profit": v.take_profit,
                "reasoning": v.reasoning, "invalidation": v.invalidation,
                "evidence": v.evidence, "parse_ok": v.parse_ok, "raw": v.raw_text,
            }
            for v in views
        ],
        "news_text": snapshot.news_text,
        "sentiment_text": snapshot.sentiment_text,
        "portfolio": snapshot.portfolio,
        "units": "USD only for entry/stop_loss/take_profit, no pips",
        "chief": {"direction": chief.direction, "confidence": chief.confidence,
                  "entry_usd": chief.entry, "stop_loss_usd": chief.stop_loss, "take_profit_usd": chief.take_profit},
        "risk": {"allowed": risk.allowed, "reason": risk.reason, "lot": risk.lot, "checks": risk.checks},
        "order": order_message,
    }
    path = output_dir / f"session_{stamp}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    md = output_dir / f"session_{stamp}.md"
    lines = [
        f"# Phiên phòng trade AI — {stamp} UTC",
        "",
        f"Symbol: {snapshot.symbol} ({snapshot.timeframe}), giá {snapshot.price}, spread {snapshot.spread}",
        f"Kết luận trưởng phòng: **{chief.direction}**, tin cậy {chief.confidence}, "
        f"entry {chief.entry} USD, SL {chief.stop_loss} USD, TP {chief.take_profit} USD (đơn vị USD, không dùng pips)",
        f"Cửa rủi ro: {'CHO PHÉP' if risk.allowed else 'CHẶN'} — {risk.reason}, lot tính ra: {risk.lot}",
        f"Lệnh: {order_message}",
        "",
        "## Ý kiến từng AI",
    ]
    for v in views:
        lines.append(f"- **{v.name}** ({v.provider}): {v.direction} ({v.confidence}) — {v.reasoning}")
    md.write_text("\n".join(lines), encoding="utf-8")
    return path
