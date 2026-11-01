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
    kill_switch_file: str = "STOP"


@dataclass
class Config:
    symbol: str = "XAUUSD"
    timeframe: str = "M15"
    mode: str = "dry_run"
    allow_live: bool = False
    magic_number: int = 20261101
    max_open_positions: int = 1
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
        providers=data.get("providers", {}),
        agents=data.get("agents", {}),
        risk=risk,
        project_root=root,
    )
