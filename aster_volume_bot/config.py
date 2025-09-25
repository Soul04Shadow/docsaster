"""Configuration utilities for the Aster volume bot."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import json

try:  # Optional dependency, loaded lazily when YAML configs are used.
    import yaml  # type: ignore
except Exception:  # pragma: no cover - handled during runtime when YAML isn't available
    yaml = None  # type: ignore


@dataclass
class AccountConfig:
    """Holds API credentials for a trading account."""

    name: str
    api_key: str
    api_secret: str


@dataclass
class BotConfig:
    """Runtime configuration for the delta-neutral volume bot."""

    symbol: str
    leverage: int
    order_notional: Optional[Decimal] = None
    order_quantity: Optional[Decimal] = None
    margin_type: str = "ISOLATED"
    target_volume: Optional[Decimal] = None
    hold_seconds: float = 2.0
    delay_seconds: float = 1.0
    order_timeout: float = 10.0
    poll_interval: float = 0.5
    status_file: Optional[Path] = None
    dry_run: bool = False
    max_retries: int = 3
    hedge_mode: bool = True
    max_cycles: Optional[int] = None
    status_update_interval_minutes: float = 60.0

    def __post_init__(self) -> None:
        if self.order_notional is None and self.order_quantity is None:
            raise ValueError(
                "Bot configuration requires either 'order_value'/'order_notional' or 'order_quantity'."
            )


def _to_decimal(value: Any) -> Decimal:
    """Converts supported numeric types to :class:`~decimal.Decimal`."""

    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        return Decimal(value)
    raise TypeError(f"Cannot convert value '{value}' to Decimal")


def _load_config_content(path: Path) -> Dict[str, Any]:
    """Loads raw configuration data from JSON or YAML files."""

    suffix = path.suffix.lower()
    with path.open("r", encoding="utf-8") as fp:
        if suffix in {".yaml", ".yml"}:
            if yaml is None:  # pragma: no cover - runtime guard
                raise RuntimeError(
                    "PyYAML is required to load YAML configuration files. Install PyYAML or "
                    "use a JSON configuration file instead."
                )
            data = yaml.safe_load(fp)
        else:
            data = json.load(fp)
    if not isinstance(data, dict):
        raise ValueError("Configuration file must define a JSON or YAML object at the top level.")
    return data


def load_config(path: Path) -> Tuple[AccountConfig, AccountConfig, BotConfig]:
    """Loads bot and account configuration from *path*.

    The configuration file must define the following structure::

        long_account:
            name: primary account name
            api_key: API key string
            api_secret: API secret string
        short_account:
            name: hedge account name
            api_key: API key string
            api_secret: API secret string
        bot:
            symbol: TRADING_PAIR
            leverage: 50
            order_value: 25
            # alternatively specify the base size with order_quantity
            # optional overrides
            target_volume: 1_000_000
            max_cycles: 100
            margin_type: ISOLATED
            status_file: status.json
            status_update_interval_minutes: 60
            hold_seconds: 1.5
            delay_seconds: 0.8
            order_timeout: 8
            poll_interval: 0.5
            dry_run: false
            max_retries: 3
            hedge_mode: true

    Parameters that represent numeric values are converted to :class:`~decimal.Decimal`
    when appropriate. Missing required keys raise :class:`KeyError`.
    """

    path = Path(path)
    data = _load_config_content(path)

    def parse_account(node_name: str) -> AccountConfig:
        node = data.get(node_name)
        if not isinstance(node, dict):
            raise KeyError(f"Missing configuration block '{node_name}'.")
        try:
            name = str(node["name"])
            api_key = str(node["api_key"])
            api_secret = str(node["api_secret"])
        except KeyError as exc:  # pragma: no cover - just protective
            raise KeyError(f"Account block '{node_name}' is missing key: {exc.args[0]}") from exc
        return AccountConfig(name=name, api_key=api_key, api_secret=api_secret)

    long_account = parse_account("long_account")
    short_account = parse_account("short_account")

    bot_data = data.get("bot")
    if not isinstance(bot_data, dict):
        raise KeyError("Missing configuration block 'bot'.")
    try:
        symbol = str(bot_data["symbol"])
        leverage = int(bot_data["leverage"])
    except KeyError as exc:  # pragma: no cover - protective
        raise KeyError(f"Bot configuration is missing key: {exc.args[0]}") from exc

    order_notional_raw = None
    for key in ("order_value", "order_notional", "order_quote_value"):
        if key in bot_data:
            order_notional_raw = bot_data[key]
            break
    order_quantity_raw = bot_data.get("order_quantity")

    order_notional = _to_decimal(order_notional_raw) if order_notional_raw is not None else None
    order_quantity = _to_decimal(order_quantity_raw) if order_quantity_raw is not None else None

    if order_notional is None and order_quantity is None:
        raise ValueError(
            "Bot configuration requires either 'order_value'/'order_notional' or 'order_quantity'."
        )

    target_volume = bot_data.get("target_volume")
    target_volume_decimal = _to_decimal(target_volume) if target_volume is not None else None

    status_file = bot_data.get("status_file")
    status_path = Path(status_file) if status_file else None

    config = BotConfig(
        symbol=symbol,
        leverage=leverage,
        order_notional=order_notional,
        order_quantity=order_quantity,
        margin_type=str(bot_data.get("margin_type", "ISOLATED")),
        target_volume=target_volume_decimal,
        hold_seconds=float(bot_data.get("hold_seconds", 2.0)),
        delay_seconds=float(bot_data.get("delay_seconds", 1.0)),
        order_timeout=float(bot_data.get("order_timeout", 10.0)),
        poll_interval=float(bot_data.get("poll_interval", 0.5)),
        status_file=status_path,
        dry_run=bool(bot_data.get("dry_run", False)),
        max_retries=int(bot_data.get("max_retries", 3)),
        hedge_mode=bool(bot_data.get("hedge_mode", True)),
        max_cycles=int(bot_data["max_cycles"]) if bot_data.get("max_cycles") is not None else None,
        status_update_interval_minutes=float(bot_data.get("status_update_interval_minutes", 60.0)),
    )

    return long_account, short_account, config


__all__ = [
    "AccountConfig",
    "BotConfig",
    "load_config",
]
