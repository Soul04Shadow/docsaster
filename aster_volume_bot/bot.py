"""Delta-neutral volume generation bot for Aster futures."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import json
import logging
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .api import ApiClient, ApiError
from .config import AccountConfig, BotConfig

logger = logging.getLogger(__name__)


@dataclass
class AccountState:
    """Runtime state for each trading account."""

    name: str
    config: AccountConfig
    client: ApiClient
    position_side: str
    seen_trade_ids: set[int] = field(default_factory=set)
    last_order_timestamp: Optional[int] = None


@dataclass
class TradeRecord:
    account: str
    order_id: int
    side: str
    position_side: str
    qty: Decimal
    quote_qty: Decimal
    commission: Decimal
    price: Decimal
    timestamp: int


@dataclass
class CycleResult:
    """Holds summary data for a single volume cycle."""

    trades: List[TradeRecord]
    start_time: float
    end_time: float

    @property
    def quote_volume(self) -> Decimal:
        total = Decimal("0")
        for trade in self.trades:
            total += trade.quote_qty.copy_abs()
        return total

    @property
    def fees(self) -> Decimal:
        total = Decimal("0")
        for trade in self.trades:
            total += trade.commission.copy_abs()
        return total


class DeltaNeutralVolumeBot:
    """Coordinates a hedged trading strategy to generate volume."""

    def __init__(self, long_account: AccountConfig, short_account: AccountConfig, config: BotConfig) -> None:
        self.config = config
        self.long = AccountState(
            name=long_account.name,
            config=long_account,
            client=ApiClient(long_account.api_key, long_account.api_secret),
            position_side="LONG",
        )
        self.short = AccountState(
            name=short_account.name,
            config=short_account,
            client=ApiClient(short_account.api_key, short_account.api_secret),
            position_side="SHORT",
        )
        self.total_volume = Decimal("0")
        self.total_fees = Decimal("0")
        self.cycles_completed = 0
        self.status_file = config.status_file
        if self.status_file:
            Path(self.status_file).parent.mkdir(parents=True, exist_ok=True)
        self._stop = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def stop(self) -> None:
        self._stop = True

    def run(self, max_cycles: Optional[int] = None) -> None:
        """Runs trading cycles until stopped or the target is achieved."""

        logger.info("Starting delta-neutral volume bot for %s", self.config.symbol)
        self._prepare_accounts()
        try:
            while not self._stop:
                if max_cycles is not None and self.cycles_completed >= max_cycles:
                    logger.info("Reached cycle limit (%s)", max_cycles)
                    break
                if self.config.target_volume is not None and self.total_volume >= self.config.target_volume:
                    logger.info("Target volume %s reached", self.config.target_volume)
                    break
                cycle_result = self._execute_cycle()
                self.cycles_completed += 1
                self.total_volume += cycle_result.quote_volume
                self.total_fees += cycle_result.fees
                self._write_status(cycle_result)
                logger.info(
                    "Cycle %s completed | ΔVolume=%s | ΔFees=%s | Total Volume=%s | Total Fees=%s",
                    self.cycles_completed,
                    cycle_result.quote_volume,
                    cycle_result.fees,
                    self.total_volume,
                    self.total_fees,
                )
                if self.config.delay_seconds:
                    time.sleep(self.config.delay_seconds)
        except KeyboardInterrupt:  # pragma: no cover - manual interruption
            logger.info("Interrupted by user.")
        except ApiError as exc:
            logger.error("API failure: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _prepare_accounts(self) -> None:
        for account_state in (self.long, self.short):
            if self.config.hedge_mode:
                try:
                    account_state.client.change_position_mode(True)
                except ApiError as exc:
                    if exc.error_code in {-4059, -4046}:  # already configured on Binance-compatible APIs
                        logger.debug("%s already in hedge mode", account_state.name)
                    else:
                        raise
            try:
                account_state.client.change_leverage(self.config.symbol, self.config.leverage)
            except ApiError as exc:
                logger.warning("Unable to set leverage for %s: %s", account_state.name, exc)
            try:
                account_state.client.change_margin_type(self.config.symbol, self.config.margin_type)
            except ApiError as exc:
                if exc.error_code == -4046:  # no-op if already the same margin type
                    logger.debug("%s already using %s margin", account_state.name, self.config.margin_type)
                else:
                    logger.warning("Unable to set margin type for %s: %s", account_state.name, exc)

    def _execute_cycle(self) -> CycleResult:
        start = time.time()
        trades: List[TradeRecord] = []
        trades.extend(self._perform_account_round(self.long, open_side="BUY", close_side="SELL"))
        trades.extend(self._perform_account_round(self.short, open_side="SELL", close_side="BUY"))
        end = time.time()
        return CycleResult(trades=trades, start_time=start, end_time=end)

    def _perform_account_round(self, account_state: AccountState, open_side: str, close_side: str) -> Sequence[TradeRecord]:
        logger.debug("%s | Opening %s position", account_state.name, account_state.position_side)
        opening_trades = self._place_and_wait(account_state, open_side)
        if self.config.hold_seconds:
            logger.debug("Sleeping for %ss before closing position", self.config.hold_seconds)
            time.sleep(self.config.hold_seconds)
        logger.debug("%s | Closing %s position", account_state.name, account_state.position_side)
        closing_trades = self._place_and_wait(account_state, close_side)
        return [*opening_trades, *closing_trades]

    def _place_and_wait(self, account_state: AccountState, side: str) -> Sequence[TradeRecord]:
        if self.config.dry_run:
            return self._simulate_trade(account_state, side)

        order_result = account_state.client.place_order(
            symbol=self.config.symbol,
            side=side,
            order_type="MARKET",
            quantity=self.config.order_quantity,
            position_side=account_state.position_side,
            new_order_resp_type="RESULT",
        )
        account_state.last_order_timestamp = order_result.update_time
        logger.debug(
            "%s | Submitted order %s (%s %s)", account_state.name, order_result.order_id, side, self.config.order_quantity
        )
        filled_order = self._wait_for_fill(account_state, order_result.order_id)
        trade_records = list(self._fetch_trade_records(account_state, filled_order))
        if not trade_records:
            logger.warning("%s | No trade records found for order %s", account_state.name, order_result.order_id)
        return trade_records

    def _wait_for_fill(self, account_state: AccountState, order_id: int) -> Dict[str, Any]:
        deadline = time.time() + self.config.order_timeout
        last_status = None
        while time.time() < deadline:
            response = account_state.client.query_order(self.config.symbol, order_id=order_id)
            status = response.get("status")
            last_status = status
            if status in {"FILLED", "PARTIALLY_FILLED"}:
                return response
            if status in {"CANCELED", "REJECTED", "EXPIRED"}:
                raise ApiError(400, None, f"Order {order_id} failed with status {status}")
            time.sleep(self.config.poll_interval)
        raise ApiError(408, None, f"Timeout waiting for order {order_id} to fill (last status: {last_status})")

    def _fetch_trade_records(self, account_state: AccountState, order_payload: Dict[str, Any]) -> Iterable[TradeRecord]:
        trades = account_state.client.get_account_trades(self.config.symbol, limit=100)
        order_id = int(order_payload["orderId"])
        results: List[TradeRecord] = []
        for trade in trades:
            try:
                trade_id = int(trade["id"])
            except (KeyError, ValueError):
                continue
            if trade_id in account_state.seen_trade_ids:
                continue
            if int(trade.get("orderId", -1)) != order_id:
                continue
            account_state.seen_trade_ids.add(trade_id)
            results.append(
                TradeRecord(
                    account=account_state.name,
                    order_id=order_id,
                    side=str(trade.get("side", "")),
                    position_side=str(trade.get("positionSide", account_state.position_side)),
                    qty=Decimal(trade.get("qty", "0")),
                    quote_qty=Decimal(trade.get("quoteQty", "0")),
                    commission=Decimal(trade.get("commission", "0")),
                    price=Decimal(trade.get("price", "0")),
                    timestamp=int(trade.get("time", 0)),
                )
            )
        return sorted(results, key=lambda rec: rec.timestamp)

    # ------------------------------------------------------------------
    # Dry-run helpers
    # ------------------------------------------------------------------
    def _simulate_trade(self, account_state: AccountState, side: str) -> Sequence[TradeRecord]:
        now = int(time.time() * 1000)
        price = Decimal("1")
        quote_qty = self.config.order_quantity * price
        commission = quote_qty * Decimal("0.0004") * Decimal("-1")
        record = TradeRecord(
            account=account_state.name,
            order_id=int(now),
            side=side,
            position_side=account_state.position_side,
            qty=self.config.order_quantity,
            quote_qty=quote_qty,
            commission=commission,
            price=price,
            timestamp=now,
        )
        logger.debug("%s | Simulated %s trade", account_state.name, side)
        return [record]

    # ------------------------------------------------------------------
    # Status reporting
    # ------------------------------------------------------------------
    def _write_status(self, cycle_result: CycleResult) -> None:
        if not self.status_file:
            return
        status_payload = {
            "symbol": self.config.symbol,
            "cycles_completed": self.cycles_completed,
            "last_cycle_start": cycle_result.start_time,
            "last_cycle_end": cycle_result.end_time,
            "last_cycle_volume": str(cycle_result.quote_volume),
            "last_cycle_fees": str(cycle_result.fees),
            "total_volume": str(self.total_volume),
            "total_fees": str(self.total_fees),
            "target_volume": str(self.config.target_volume) if self.config.target_volume else None,
            "order_quantity": str(self.config.order_quantity),
            "leverage": self.config.leverage,
            "margin_type": self.config.margin_type,
            "hedge_mode": self.config.hedge_mode,
            "dry_run": self.config.dry_run,
        }
        try:
            Path(self.status_file).write_text(json.dumps(status_payload, indent=2), encoding="utf-8")
        except OSError as exc:  # pragma: no cover - disk failure
            logger.error("Failed to write status file %s: %s", self.status_file, exc)


__all__ = [
    "DeltaNeutralVolumeBot",
    "AccountState",
    "TradeRecord",
    "CycleResult",
]
