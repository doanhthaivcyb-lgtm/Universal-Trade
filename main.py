"""Chạy 1 phiên phòng trade AI.

Ví dụ chạy thử không tốn phí API, không đặt lệnh thật:
    python main.py --mock --snapshot data/snapshot_example.json

Chạy thật với key trong .env (mặc định dry_run trong config.yaml):
    python main.py --snapshot data/snapshot_example.json
"""
from __future__ import annotations

import argparse
from pathlib import Path

from trading_room.config import load_config
from trading_room.executor import get_executor
from trading_room.journal import save_session
from trading_room.market_snapshot import load_snapshot
from trading_room.orchestrator import run_room


def main() -> int:
    parser = argparse.ArgumentParser(description="Phòng trade AI — XAUUSD")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--snapshot", default="data/snapshot_example.json")
    parser.add_argument("--mock", action="store_true", help="Dùng AI giả, không gọi API thật")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()

    cfg = load_config(args.config)
    snapshot = load_snapshot(args.snapshot)

    chief, views, risk = run_room(cfg, snapshot, mock=args.mock)

    print(f"Giá: {snapshot.price}  Spread: {snapshot.spread}")
    for view in views:
        print(f"- {view.name} ({view.provider}): {view.direction} tin cậy {view.confidence} — {view.reasoning}")
    print(f"Trưởng phòng: {chief.direction} tin cậy {chief.confidence}")
    print(f"Cửa rủi ro: {'CHO PHÉP' if risk.allowed else 'CHẶN'} — {risk.reason} (lot {risk.lot})")

    order_message = "Không vào lệnh."
    if risk.allowed:
        executor = get_executor(cfg)
        result = executor.place(snapshot, chief, risk.lot)
        order_message = result.message
        print(order_message)
    else:
        print("Không vào lệnh.")

    saved = save_session(Path(args.output_dir), snapshot, views, chief, risk, order_message)
    print(f"Đã lưu nhật ký phiên: {saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
