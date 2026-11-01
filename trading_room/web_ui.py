"""Giao diện web local cho phòng trade AI — chỉ dùng stdlib, không thêm thư viện.

Chạy trên máy Windows của Thái:
    py -3.13 main.py --web
    py -3.13 -m trading_room.web_ui

Mở trình duyệt tại http://127.0.0.1:8000 (chỉ nghe trên máy local, không
mở ra mạng ngoài). Giao diện chỉ bấm nút thay cho gõ lệnh: chạy 1 phiên,
xem dữ liệu MT5, xem thảo luận, xem cửa rủi ro, xem nhật ký, sửa cấu hình
an toàn, và bấm DỪNG KHẨN CẤP.

An toàn:
- UI không bao giờ hiện giá trị key trong .env, chỉ hiện có/không có key.
- UI không cho sửa key, không cho đổi chế độ sang live. Chế độ live vẫn
  cần sửa tay config.yaml + .env như cũ, và UI sẽ từ chối chạy nếu live
  đã bật (để tránh bấm nhầm trên web).
- Chạy phiên qua UI làm đúng luồng như CLI: lấy snapshot mới, thảo luận,
  cửa rủi ro, executor, lưu nhật ký. Không có nút đặt lệnh riêng.
- Chỉ 1 phiên chạy tại một thời điểm. Vòng lặp chạy nền kiểm tra file STOP
  sau mỗi phiên.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import load_config
from .executor import get_executor
from .journal import save_session
from .market_snapshot import load_snapshot
from .mt5_data import MT5DataProvider
from .orchestrator import run_room

HERE = Path(__file__).resolve().parent
INDEX_FILE = HERE / "web_ui" / "index.html"

TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
EDITABLE_RISK_FIELDS = {
    "max_risk_usd_per_trade": (10.0, 5000.0),
    "max_daily_loss_usd": (100.0, 20000.0),
    "max_lot_per_order": (0.01, 1.5),
    "min_confidence": (0.0, 0.95),
    "min_sl_distance_usd": (1.0, 20.0),
    "max_sl_distance_usd": (5.0, 100.0),
    "min_reward_risk": (0.5, 3.0),
    "max_spread_usd": (0.1, 5.0),
    "max_price_move_usd": (1.0, 20.0),
    "max_portfolio_notional_usd": (10000.0, 10000000.0),
}


class UIState:
    """Trạng thái chia sẻ giữa các request. Mọi thay đổi đều giữ trong lock."""

    def __init__(self, config_path: str, output_dir: str = "output"):
        self.config_path = config_path
        self.output_dir = output_dir
        self.lock = threading.Lock()
        self.running = False
        self.looping = False
        self.phase = "Chưa chạy phiên nào."
        self.error = ""
        self.last_result: dict | None = None
        self.last_snapshot: dict | None = None
        self._loop_stop = threading.Event()

    def set_phase(self, phase: str) -> None:
        with self.lock:
            self.phase = phase

    def stop_loop(self) -> None:
        self._loop_stop.set()
        with self.lock:
            self.looping = False


def _config_dict(config_path: str) -> dict:
    import yaml

    path = Path(config_path)
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _keys_present() -> dict:
    # Chỉ trả về True/False, KHÔNG bao giờ trả về giá trị key.
    return {
        "openai": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "gemini": bool(os.getenv("GEMINI_API_KEY", "").strip()),
        "anthropic": bool(os.getenv("ANTHROPIC_API_KEY", "").strip()),
    }


def _snapshot_to_dict(snapshot) -> dict:
    return {
        "symbol": snapshot.symbol,
        "timeframe": snapshot.timeframe,
        "bid": snapshot.bid,
        "ask": snapshot.ask,
        "price": snapshot.price,
        "spread": snapshot.spread,
        "source": snapshot.source,
        "captured_at": snapshot.captured_at,
        "indicators": snapshot.indicators,
        "macro_news": snapshot.macro_news,
        "news_text": snapshot.news_text,
        "sentiment_text": snapshot.sentiment_text,
        "portfolio": snapshot.portfolio,
        "open_positions": snapshot.open_positions,
        "daily_loss_usd": snapshot.daily_loss_usd,
        "account_balance": snapshot.account_balance,
        "account_equity": snapshot.account_equity,
        "notes": snapshot.notes,
    }


def _result_to_dict(snapshot, views, chief, risk, order_message: str, saved_path: str) -> dict:
    return {
        "snapshot": _snapshot_to_dict(snapshot),
        "views": [
            {
                "name": v.name, "provider": v.provider, "model": v.model,
                "direction": v.direction, "confidence": v.confidence,
                "entry": v.entry, "stop_loss": v.stop_loss, "take_profit": v.take_profit,
                "reasoning": v.reasoning, "invalidation": v.invalidation,
                "evidence": v.evidence, "parse_ok": v.parse_ok,
            }
            for v in views
        ],
        "chief": {
            "name": chief.name, "provider": chief.provider, "model": chief.model,
            "direction": chief.direction, "confidence": chief.confidence,
            "entry": chief.entry, "stop_loss": chief.stop_loss, "take_profit": chief.take_profit,
            "reasoning": chief.reasoning, "invalidation": chief.invalidation,
            "evidence": chief.evidence, "parse_ok": chief.parse_ok,
        },
        "risk": {"allowed": risk.allowed, "reason": risk.reason, "lot": risk.lot,
                 "sl_distance": risk.sl_distance, "checks": risk.checks},
        "order": order_message,
        "saved": saved_path,
    }


def _build_snapshot(cfg, source: str, snapshot_path: str):
    if source == "mt5":
        provider = MT5DataProvider(cfg)
        try:
            return provider.build_snapshot()
        finally:
            provider.close()
    return load_snapshot(snapshot_path)


def _run_one_session(state: UIState, source: str, mock: bool, snapshot_path: str) -> dict:
    cfg = load_config(state.config_path)
    if cfg.live_enabled or cfg.mode == "live":
        raise RuntimeError("Chế độ live đã bật. Giao diện web từ chối chạy để tránh bấm nhầm. Tắt live trong config.yaml và .env rồi chạy lại.")
    state.set_phase("Đang lấy dữ liệu MT5..." if source == "mt5" else "Đang đọc file thử...")
    snapshot = _build_snapshot(cfg, source, snapshot_path)
    with state.lock:
        state.last_snapshot = _snapshot_to_dict(snapshot)
    state.set_phase("Đang gọi các AI thảo luận... (có thể mất hàng chục giây)")
    chief, views, risk = run_room(cfg, snapshot, mock=mock)
    state.set_phase("Đang kiểm tra cửa rủi ro và ghi nhật ký...")
    order_message = "Không vào lệnh."
    if risk.allowed:
        result = get_executor(cfg).place(snapshot, chief, risk.lot)
        order_message = result.message
    saved = save_session(Path(state.output_dir), snapshot, views, chief, risk, order_message)
    result = _result_to_dict(snapshot, views, chief, risk, order_message, str(saved))
    with state.lock:
        state.last_result = result
    return result


def _session_worker(state: UIState, source: str, mock: bool, snapshot_path: str, loop: bool) -> None:
    try:
        while True:
            result = _run_one_session(state, source, mock, snapshot_path)
            with state.lock:
                state.error = ""
                state.phase = "Đã xong: " + result["order"]
            if not loop:
                break
            cfg = load_config(state.config_path)
            if state._loop_stop.is_set() or (cfg.project_root / cfg.risk.kill_switch_file).exists():
                break
            state.set_phase(f"Đang nghỉ {cfg.loop_interval_seconds} giây tới phiên tiếp theo...")
            if state._loop_stop.wait(timeout=float(cfg.loop_interval_seconds)):
                break
    except Exception as exc:  # Lỗi được hiện rõ trên UI, không crash server.
        with state.lock:
            state.error = str(exc)
            state.phase = f"Lỗi: {exc}"
    finally:
        with state.lock:
            state.running = False
            state.looping = False


def _start_run(state: UIState, source: str, mock: bool, snapshot_path: str, loop: bool) -> dict:
    with state.lock:
        if state.running:
            return {"started": False, "error": "Đang có 1 phiên chạy, chờ xong rồi chạy tiếp."}
        state.running = True
        state.looping = loop
        state.error = ""
        state._loop_stop.clear()
    thread = threading.Thread(target=_session_worker, args=(state, source, mock, snapshot_path, loop), daemon=True)
    thread.start()
    return {"started": True}


def _list_sessions(output_dir: str) -> list[dict]:
    out = Path(output_dir)
    if not out.exists():
        return []
    sessions = []
    for path in sorted(out.glob("session_*.json"), reverse=True)[:30]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        chief = data.get("chief", {})
        risk = data.get("risk", {})
        sessions.append({
            "name": path.name,
            "timestamp_utc": data.get("timestamp_utc", ""),
            "symbol": data.get("symbol", ""),
            "timeframe": data.get("timeframe", ""),
            "chief_direction": chief.get("direction", ""),
            "chief_confidence": chief.get("confidence", 0.0),
            "risk_allowed": risk.get("allowed", False),
            "risk_reason": risk.get("reason", ""),
            "lot": risk.get("lot", 0.0),
            "order": data.get("order", ""),
        })
    return sessions


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def make_handler(state: UIState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # Giữ log gọn, không spam terminal.
            pass

        def _send_json(self, data: dict, status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, text: str, content_type: str, status: int = 200) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                if not INDEX_FILE.exists():
                    self._send_text("Không tìm thấy giao diện web.", "text/plain; charset=utf-8", 500)
                    return
                self._send_text(INDEX_FILE.read_text(encoding="utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/status":
                cfg = load_config(state.config_path)
                with state.lock:
                    running, looping, phase, error = state.running, state.looping, state.phase, state.error
                stop_file = cfg.project_root / cfg.risk.kill_switch_file
                self._send_json({
                    "symbol": cfg.symbol, "timeframe": cfg.timeframe,
                    "mode": cfg.mode, "allow_live": cfg.allow_live,
                    "live_enabled": cfg.live_enabled,
                    "data_source": cfg.data_source,
                    "loop_interval_seconds": cfg.loop_interval_seconds,
                    "keys": _keys_present(),
                    "stop_exists": stop_file.exists(),
                    "running": running, "looping": looping,
                    "phase": phase, "error": error,
                    "server_time": datetime.now().isoformat(timespec="seconds"),
                })
                return
            if parsed.path == "/api/run-state":
                with state.lock:
                    self._send_json({"running": state.running, "looping": state.looping,
                                     "phase": state.phase, "error": state.error,
                                     "result": state.last_result})
                return
            if parsed.path == "/api/snapshot":
                query = parse_qs(parsed.query)
                enrich = query.get("enrich", ["0"])[0] == "1"
                try:
                    cfg = load_config(state.config_path)
                    snapshot = _build_snapshot(cfg, cfg.data_source, "data/snapshot_example.json")
                    if enrich:
                        from .orchestrator import enrich_snapshot

                        snapshot = enrich_snapshot(cfg, snapshot, mock=False)
                    with state.lock:
                        state.last_snapshot = _snapshot_to_dict(snapshot)
                    self._send_json({"snapshot": _snapshot_to_dict(snapshot)})
                except Exception as exc:
                    self._send_json({"error": str(exc)}, 500)
                return
            if parsed.path == "/api/sessions":
                self._send_json({"sessions": _list_sessions(state.output_dir)})
                return
            if parsed.path == "/api/session":
                name = parse_qs(parsed.query).get("name", [""])[0]
                safe = Path(name).name
                if not safe.startswith("session_") or not safe.endswith(".json"):
                    self._send_json({"error": "Tên file nhật ký không hợp lệ."}, 400)
                    return
                path = Path(state.output_dir) / safe
                if not path.exists():
                    self._send_json({"error": "Không tìm thấy file nhật ký."}, 404)
                    return
                try:
                    self._send_json(json.loads(path.read_text(encoding="utf-8")))
                except Exception as exc:
                    self._send_json({"error": str(exc)}, 500)
                return
            if parsed.path == "/api/config":
                cfg = load_config(state.config_path)
                self._send_json({
                    "symbol": cfg.symbol, "timeframe": cfg.timeframe,
                    "mode": cfg.mode, "allow_live": cfg.allow_live,
                    "data_source": cfg.data_source,
                    "loop_interval_seconds": cfg.loop_interval_seconds,
                    "max_open_positions": cfg.max_open_positions,
                    "news_enabled": cfg.news_enabled,
                    "sentiment_enabled": cfg.sentiment_enabled,
                    "risk": {
                        "max_risk_usd_per_trade": cfg.risk.max_risk_usd_per_trade,
                        "max_daily_loss_usd": cfg.risk.max_daily_loss_usd,
                        "max_lot_per_order": cfg.risk.max_lot_per_order,
                        "min_confidence": cfg.risk.min_confidence,
                        "min_sl_distance_usd": cfg.risk.min_sl_distance_usd,
                        "max_sl_distance_usd": cfg.risk.max_sl_distance_usd,
                        "min_reward_risk": cfg.risk.min_reward_risk,
                        "max_spread_usd": cfg.risk.max_spread_usd,
                        "max_price_move_usd": cfg.risk.max_price_move_usd,
                        "max_portfolio_notional_usd": cfg.risk.max_portfolio_notional_usd,
                    },
                    "providers": cfg.providers,
                    "agents": cfg.agents,
                })
                return
            self._send_json({"error": "Không tìm thấy đường dẫn."}, 404)

        def do_POST(self):
            parsed = urlparse(self.path)
            body = _read_json_body(self)
            if parsed.path == "/api/run":
                source = str(body.get("source", "mt5"))
                if source not in ("mt5", "json"):
                    self._send_json({"started": False, "error": "source phải là mt5 hoặc json."}, 400)
                    return
                mock = bool(body.get("mock", False))
                loop = bool(body.get("loop", False))
                snapshot_path = str(body.get("snapshot", "data/snapshot_example.json"))
                self._send_json(_start_run(state, source, mock, snapshot_path, loop))
                return
            if parsed.path == "/api/stop":
                cfg = load_config(state.config_path)
                stop_file = cfg.project_root / cfg.risk.kill_switch_file
                stop_file.write_text("STOP\n", encoding="utf-8")
                state.stop_loop()
                self._send_json({"ok": True, "stop_exists": True})
                return
            if parsed.path == "/api/clear-stop":
                cfg = load_config(state.config_path)
                stop_file = cfg.project_root / cfg.risk.kill_switch_file
                if stop_file.exists():
                    stop_file.unlink()
                self._send_json({"ok": True, "stop_exists": False})
                return
            if parsed.path == "/api/config":
                try:
                    import yaml

                    data = _config_dict(state.config_path)
                    symbol = str(body.get("symbol", "")).strip()
                    timeframe = str(body.get("timeframe", "")).strip().upper()
                    if not symbol:
                        raise ValueError("Symbol không được để trống.")
                    if timeframe not in TIMEFRAMES:
                        raise ValueError(f"Khung thời gian phải là một trong: {', '.join(TIMEFRAMES)}.")
                    if str(body.get("mode", "")).lower() == "live":
                        raise ValueError("Giao diện web không cho đổi sang chế độ live.")
                    risk_in = body.get("risk", {})
                    if not isinstance(risk_in, dict):
                        raise ValueError("risk phải là một object.")
                    risk = dict(data.get("risk") or {})
                    for field_name, (low, high) in EDITABLE_RISK_FIELDS.items():
                        if field_name not in risk_in:
                            continue
                        value = float(risk_in[field_name])
                        if value < low or value > high:
                            raise ValueError(f"{field_name} phải trong khoảng {low}–{high}.")
                        risk[field_name] = value
                    if risk["min_sl_distance_usd"] >= risk["max_sl_distance_usd"]:
                        raise ValueError("Khoảng cách SL tối thiểu phải nhỏ hơn tối đa.")
                    data["symbol"] = symbol
                    data["timeframe"] = timeframe
                    data["risk"] = risk
                    data["loop_interval_seconds"] = int(body.get("loop_interval_seconds", data.get("loop_interval_seconds", 900)))
                    if data["loop_interval_seconds"] < 60 or data["loop_interval_seconds"] > 86400:
                        raise ValueError("loop_interval_seconds phải trong khoảng 60–86400.")
                    data["max_open_positions"] = int(body.get("max_open_positions", data.get("max_open_positions", 1)))
                    if data["max_open_positions"] < 1 or data["max_open_positions"] > 5:
                        raise ValueError("max_open_positions phải trong khoảng 1–5.")
                    data["news_enabled"] = bool(body.get("news_enabled", data.get("news_enabled", True)))
                    data["sentiment_enabled"] = bool(body.get("sentiment_enabled", data.get("sentiment_enabled", True)))
                    config_path = Path(state.config_path)
                    backup = config_path.with_suffix(".yaml.bak")
                    backup.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
                    config_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
                    self._send_json({"ok": True, "backup": str(backup)})
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 400)
                return
            self._send_json({"error": "Không tìm thấy đường dẫn."}, 404)

    return Handler


def run_server(config_path: str = "config.yaml", host: str = "127.0.0.1", port: int = 8000,
               output_dir: str = "output") -> None:
    # Nạp .env và config một lần để báo lỗi sớm nếu file cấu hình hỏng.
    load_config(config_path)
    state = UIState(config_path, output_dir=output_dir)
    server = ThreadingHTTPServer((host, port), make_handler(state))
    print(f"Giao diện web phòng trade AI: http://{host}:{port}")
    print("Chỉ nghe trên máy local. Dừng bằng Ctrl+C.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Đã dừng giao diện web.")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Giao diện web local cho phòng trade AI")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()
    run_server(args.config, host=args.host, port=args.port, output_dir=args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
