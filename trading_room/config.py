from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


def load_env_file(path: Path) -> None:
    """Nạp file .env đơn giản, không ghi đè biến môi trường đã có."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


@dataclass
class RiskConfig:
    max_risk_usd_per_trade: float = 500.0
    max_daily_loss_usd: float = 2000.0
    max_lot_per_order: float = 1.5
    min_lot: float = 0.01
    lot_step: float = 0.01
    contract_size: float = 100.0
    min_confidence: float = 0.65
    min_sl_distance_usd: float = 3.0
    max_sl_distance_usd: float = 30.0
    min_reward_risk: float = 1.0
    max_spread_usd: float = 0.50
    # Giá được phép chạy khỏi giá lúc lấy dữ liệu trong lúc AI thảo luận.
    # Vượt quá thì executor hủy lệnh, vì SL/TP của trưởng phòng đã cũ.
    max_price_move_usd: float = 5.0
    # Tổng giá trị danh mục tối đa (USD danh nghĩa) được phép trước khi cộng lệnh mới.
    # Tính: tổng |volume × giá × contract_size| của mọi vị thế đang mở.
    # TẠM: 1.000.000 USD trên vốn 100.000 USD, chờ Thái chốt.
    max_portfolio_notional_usd: float = 1000000.0
    kill_switch_file: str = "STOP"


@dataclass
class Config:
    symbol: str = "XAUUSD"
    timeframe: str = "M15"
    mode: str = "dry_run"
    allow_live: bool = False
    magic_number: int = 20261101
    max_open_positions: int = 1
    # Nguồn dữ liệu chính: "mt5" lấy trực tiếp từ MT5 realtime, "json" chỉ để chạy thử.
    data_source: str = "mt5"
    mt5_candles: int = 200
    # Chu kỳ chạy lại khi dùng --loop, tính bằng giây. Mặc định 900 = 15 phút (M15).
    loop_interval_seconds: int = 900
    # MT5 không có tin văn bản. Điền tay tại đây nếu muốn AI vĩ mô đọc thêm.
    macro_news: str = ""
    # Tin tức / sentiment từ nguồn công khai miễn phí, không cần key.
    news_enabled: bool = True
    news_calendar_url: str = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
    news_rss_feeds: list = field(default_factory=lambda: ["https://www.federalreserve.gov/feeds/press_all.xml"])
    news_timeout_seconds: float = 20.0
    sentiment_enabled: bool = True
    sentiment_cot_csv_url: str = "https://futuresbench.com/data/cot/gold.csv"
    providers: dict = field(default_factory=dict)
    agents: dict = field(default_factory=dict)
    risk: RiskConfig = field(default_factory=RiskConfig)
    project_root: Path = field(default_factory=lambda: Path.cwd())

    @property
    def live_enabled(self) -> bool:
        return self.allow_live and os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"


def load_config(config_path: str | Path = "config.yaml") -> Config:
    root = Path(config_path).resolve().parent if Path(config_path).is_file() else Path.cwd()
    load_env_file(root / ".env")
    data = {}
    if Path(config_path).exists():
        data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    risk = RiskConfig(**(data.get("risk") or {}))
    return Config(
        symbol=data.get("symbol", "XAUUSD"),
        timeframe=data.get("timeframe", "M15"),
        mode=data.get("mode", "dry_run"),
        allow_live=bool(data.get("allow_live", False)),
        magic_number=int(data.get("magic_number", 20261101)),
        max_open_positions=int(data.get("max_open_positions", 1)),
        data_source=str(data.get("data_source", "mt5")),
        mt5_candles=int(data.get("mt5_candles", 200)),
        loop_interval_seconds=int(data.get("loop_interval_seconds", 900)),
        macro_news=str(data.get("macro_news", "")),
        news_enabled=bool(data.get("news_enabled", True)),
        news_calendar_url=str(data.get("news_calendar_url", "https://nfs.faireconomy.media/ff_calendar_thisweek.xml")),
        news_rss_feeds=list(data.get("news_rss_feeds", ["https://www.federalreserve.gov/feeds/press_all.xml"])),
        news_timeout_seconds=float(data.get("news_timeout_seconds", 20.0)),
        sentiment_enabled=bool(data.get("sentiment_enabled", True)),
        sentiment_cot_csv_url=str(data.get("sentiment_cot_csv_url", "https://futuresbench.com/data/cot/gold.csv")),
        providers=data.get("providers", {}),
        agents=data.get("agents", {}),
        risk=risk,
        project_root=root,
    )
