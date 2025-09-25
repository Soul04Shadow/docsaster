from __future__ import annotations

from decimal import Decimal

from aster_volume_bot.api import ApiClient


def test_serialize_and_signature_matches_documentation_example(monkeypatch) -> None:
    client = ApiClient(
        api_key="dbefbc809e3e83c283a984c3a1459732ea7db1360ca80c5c2c8867408d28cc83",
        api_secret="2b5eb11e18796d12d88f13dc27dbbd02c2cc51ff7059765ed9821957d82bb4d9",
    )

    # Freeze timestamp for deterministic signature
    monkeypatch.setattr(client, "_timestamp", lambda: 1591702613943)

    params = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "quantity": Decimal("1"),
        "price": Decimal("9000"),
        "timeInForce": "GTC",
        "recvWindow": 5000,
    }
    params.setdefault("timestamp", client._timestamp())
    signature = client._sign(ApiClient._serialize(params))
    assert (
        signature
        == "3c661234138461fcc7a7d8746c6558c9842d4e10870d2ecbedf7777cad694af9"
    )
