from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from aster_volume_bot.config import BotConfig, load_config


def test_load_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        """
        {
            "long_account": {"name": "long", "api_key": "k1", "api_secret": "s1"},
            "short_account": {"name": "short", "api_key": "k2", "api_secret": "s2"},
            "bot": {
                "symbol": "BTCUSDT",
                "order_quantity": "0.01",
                "leverage": 50,
                "target_volume": "100"
            }
        }
        """,
        encoding="utf-8",
    )
    long_account, short_account, bot_config = load_config(config_path)
    assert long_account.name == "long"
    assert short_account.api_key == "k2"
    assert isinstance(bot_config, BotConfig)
    assert bot_config.symbol == "BTCUSDT"
    assert bot_config.order_quantity == Decimal("0.01")
    assert bot_config.target_volume == Decimal("100")


def test_load_config_missing_block(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    with pytest.raises(KeyError):
        load_config(config_path)
