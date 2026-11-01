"""Chạy phòng trade AI với dữ liệu realtime trực tiếp từ MT5.

Chạy 1 phiên, dữ liệu lấy trực tiếp từ MT5 (mặc định, xem data_source trong config.yaml):
    python main.py --mock

Chạy lặp lại mỗi chu kỳ trong config (mặc định 900 giây = M15), dừng bằng Ctrl+C
hoặc tạo file STOP trong thư mục dự án:
    python main.py --loop

Chạy thử không cần MT5, dùng file JSON mẫu, không tốn phí API, không đặt lệnh:
    python main.py --mock --source json --snapshot data/snapshot_example.json

Chạy với key AI thật trong .env (vẫn mặc định dry_run trong config.yaml):
    python main.py

Mở app desktop (bấm đúp run_app.bat cũng mở app này, không cần trình duyệt):
    python main.py --app

Mở giao diện web local thay cho gõ lệnh (chỉ nghe trên máy local):
    python main.py --web
    # rồi mở http://127.0.0.1:8000 trong trình duyệt
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from trading_room.config import load_config
from trading_room.executor import get_executor
from trading_room.journal import save_session
from trading_room.market_snapshot import MarketSnapshot, load_snapshot
from trading_room.mt5_data import MT5DataProvider
from trading_room.orchestrator import run_room


def _get_snapshot(cfg, source: str, snapshot_path: str, provider: MT5DataProvider | None) -> MarketSnapshot:
    if source == "mt5":
        if provider is None:
            provider = MT5DataProvider(cfg)
        return provider.build_snapshot()
    return load_snapshot(snapshot_path)


def _run_once(cfg, snapshot: MarketSnapshot, mock: bool, output_dir: str) -> None:
    chief, views, risk = run_room(cfg, snapshot, mock=mock)
    print(f"Nguồn: {snapshot.source} lúc {snapshot.captured_at or 'không rõ'}")
    print(f"Giá: {snapshot.price}  Spread: {snapshot.spread}  Vị thế mở: {snapshot.open_positions}")
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
    saved = save_session(Path(output_dir), snapshot, views, chief, risk, order_message)
    print(f"Đã lưu nhật ký phiên: {saved}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Phòng trade AI — XAUUSD, dữ liệu realtime từ MT5")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--source", choices=["mt5", "json"], default=None,
                        help="mt5: lấy realtime từ MT5. json: đọc file thử. Mặc định lấy từ config.yaml.")
    parser.add_argument("--snapshot", default="data/snapshot_example.json",
                        help="Chỉ dùng khi --source json.")
    parser.add_argument("--mock", action="store_true", help="Dùng AI giả, không gọi API thật")
    parser.add_argument("--loop", action="store_true", help="Chạy lặp lại theo loop_interval_seconds")
    parser.add_argument("--app", action="store_true", help="Mở app desktop (Tkinter) thay cho dòng lệnh")
    parser.add_argument("--web", action="store_true", help="Mở giao diện web local thay cho dòng lệnh")
    parser.add_argument("--port", type=int, default=8000, help="Cổng giao diện web, mặc định 8000")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()

    if args.app:
        from trading_room.desktop_app import main as app_main

        return app_main(["--config", args.config, "--output-dir", args.output_dir])

    if args.web:
        from trading_room.web_ui import run_server

        run_server(args.config, host="127.0.0.1", port=args.port, output_dir=args.output_dir)
        return 0

    cfg = load_config(args.config)
    source = args.source or cfg.data_source
    provider: MT5DataProvider | None = None
    try:
        if source == "mt5":
            provider = MT5DataProvider(cfg)
            provider.connect()
        while True:
            if (cfg.project_root / cfg.risk.kill_switch_file).exists():
                print("Có file STOP, dừng chạy.")
                return 0
            snapshot = _get_snapshot(cfg, source, args.snapshot, provider)
            _run_once(cfg, snapshot, args.mock, args.output_dir)
            if not args.loop:
                return 0
            print(f"Nghỉ {cfg.loop_interval_seconds} giây tới phiên tiếp theo...")
            time.sleep(cfg.loop_interval_seconds)
    except KeyboardInterrupt:
        print("Đã dừng bởi người dùng.")
        return 0
    finally:
        if provider is not None:
            provider.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
