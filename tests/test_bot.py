from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

import pytest

from aster_volume_bot.api import OrderResult
from aster_volume_bot.bot import DeltaNeutralVolumeBot
from aster_volume_bot.config import AccountConfig, BotConfig


class FakeApiClient:
    def __init__(self, api_key: str, api_secret: str) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.place_order_calls = []
        self.exchange_info_calls = 0
        self._price = Decimal("16345")

    def get_symbol_price(self, symbol: str) -> Decimal:
        return self._price

    def get_exchange_info(self, symbol: str | None = None) -> dict:
        self.exchange_info_calls += 1
        return {
            "symbols": [
                {
                    "symbol": symbol,
                    "filters": [
                        {
                            "filterType": "LOT_SIZE",
                            "stepSize": "0.001",
                        }
                    ],
                }
            ]
        }

    # ------------------------------------------------------------------
    # No-op stubs for methods exercised during tests
    # ------------------------------------------------------------------
    def change_position_mode(self, *_args, **_kwargs) -> None:  # pragma: no cover - unused in tests
        pass

    def change_leverage(self, *_args, **_kwargs) -> None:  # pragma: no cover - unused in tests
        pass

    def change_margin_type(self, *_args, **_kwargs) -> None:  # pragma: no cover - unused in tests
        pass

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal | None = None,
        position_side: str | None = None,
        time_in_force: str | None = None,
        reduce_only: bool | None = None,
        price: Decimal | None = None,
        new_client_order_id: str | None = None,
        close_position: bool | None = None,
        working_type: str | None = None,
        stop_price: Decimal | None = None,
        callback_rate: Decimal | None = None,
        activation_price: Decimal | None = None,
        new_order_resp_type: str | None = None,
        recv_window: int | None = None,
        quote_order_qty: Decimal | None = None,
    ) -> OrderResult:
        call = {
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "quantity": quantity,
            "quote_order_qty": quote_order_qty,
            "position_side": position_side,
        }
        self.place_order_calls.append(call)
        return OrderResult(
            order_id=1,
            status="FILLED",
            executed_qty=Decimal("0"),
            cum_quote=Decimal("0"),
            avg_price=Decimal("0"),
            update_time=0,
        )

    def query_order(self, *_args, **_kwargs) -> dict:  # pragma: no cover - unused thanks to patching
        return {"status": "FILLED", "orderId": 1}

    def get_account_trades(self, *_args, **_kwargs):  # pragma: no cover - unused thanks to patching
        return []


@pytest.fixture(autouse=True)
def _patch_api_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aster_volume_bot.bot.ApiClient", FakeApiClient)


def make_bot(*, dry_run: bool = False) -> DeltaNeutralVolumeBot:
    long_account = AccountConfig(name="long", api_key="k", api_secret="s")
    short_account = AccountConfig(name="short", api_key="k", api_secret="s")
    config = BotConfig(
        symbol="BTCUSDT",
        leverage=10,
        order_notional=Decimal("25"),
        order_quantity=None,
        dry_run=dry_run,
    )
    return DeltaNeutralVolumeBot(long_account, short_account, config)


def test_order_notional_converted_to_quantity(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = make_bot()
    bot._wait_for_fill = lambda *args, **kwargs: {"orderId": 1}
    bot._fetch_trade_records = lambda *args, **kwargs: []

    trades = bot._place_and_wait(bot.long, side="BUY")
    assert trades == []

    call = bot.long.client.place_order_calls[0]
    expected_qty = (Decimal("25") / Decimal("16345")).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
    assert call["quantity"] == expected_qty
    assert call["quote_order_qty"] is None
    assert bot.long.client.exchange_info_calls == 1

    # Subsequent orders reuse the cached step size rather than re-fetching exchange info.
    bot._place_and_wait(bot.long, side="SELL")
    assert bot.long.client.exchange_info_calls == 1


def test_dry_run_uses_computed_quantity() -> None:
    bot = make_bot(dry_run=True)
    trades = bot._simulate_trade(bot.long, side="BUY")
    expected_qty = (Decimal("25") / Decimal("16345")).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
    assert trades[0].qty == expected_qty
    assert trades[0].quote_qty == expected_qty * Decimal("16345")
