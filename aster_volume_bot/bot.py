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


RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
FG_CYAN = "\033[36m"
FG_GREEN = "\033[32m"
FG_MAGENTA = "\033[35m"
FG_YELLOW = "\033[33m"
FG_RED = "\033[31m"
FG_BLUE = "\033[34m"


def _paint(text: str, *styles: str) -> str:
    """Wrap *text* with ANSI style codes."""

    if not styles:
        return text
    return f"{''.join(styles)}{text}{RESET}"


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
        self._last_status_write = 0.0

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        text = format(value, "f")
        trimmed = text.rstrip("0").rstrip(".")
        return trimmed or "0"

    @staticmethod
    def _format_position_side(position_side: str) -> str:
        color = FG_BLUE if position_side.upper() == "LONG" else FG_YELLOW
        return _paint(position_side.upper(), BOLD, color)

    @staticmethod
    def _format_order_side(side: str) -> str:
        color = FG_GREEN if side.upper() == "BUY" else FG_RED
        return _paint(side.upper(), BOLD, color)

    def _account_label(self, account_state: AccountState) -> str:
        color = FG_GREEN if account_state.position_side == "LONG" else FG_MAGENTA
        return f"{_paint(account_state.name, BOLD, color)} {_paint('│', DIM)}"

    def _format_metric(self, label: str, value: Decimal, color: str) -> str:
        return f"{_paint(label, DIM)} {_paint(self._format_decimal(value), color, BOLD)}"

    def _order_description(self) -> str:
        if self.config.order_notional is not None:
            return self._format_metric("Notional (USDT)", self.config.order_notional, FG_CYAN)
        if self.config.order_quantity is None:  # pragma: no cover - guarded by config validation
            raise ValueError("Bot configuration missing order size definition")
        return self._format_metric("Quantity", self.config.order_quantity, FG_CYAN)

    def _format_cycle_summary(self, cycle_result: CycleResult) -> str:
        parts = [
            f"{_paint('Cycle', BOLD)} {_paint(str(self.cycles_completed), FG_CYAN, BOLD)}",
            self._format_metric("ΔVolume", cycle_result.quote_volume, FG_GREEN),
            self._format_metric("ΔFees", cycle_result.fees, FG_YELLOW),
            self._format_metric("Total Volume", self.total_volume, FG_CYAN),
            self._format_metric("Total Fees", self.total_fees, FG_MAGENTA),
        ]
        return f"{_paint('│', DIM)} " + "  ".join(parts)

    def _order_parameters(self) -> Dict[str, Decimal]:
        if self.config.order_notional is not None:
            return {"quote_order_qty": self.config.order_notional}
        if self.config.order_quantity is None:  # pragma: no cover - guarded by config validation
            raise ValueError("Bot configuration missing order quantity and order value")
        return {"quantity": self.config.order_quantity}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def stop(self) -> None:
        self._stop = True

    def run(self, max_cycles: Optional[int] = None) -> None:
        """Runs trading cycles until stopped or the target is achieved."""

        logger.info(
            "%s %s %s %s",
            _paint("Starting", BOLD, FG_CYAN),
            _paint("delta-neutral volume bot", DIM),
            _paint(self.config.symbol, BOLD, FG_CYAN),
            self._order_description(),
        )
        self._prepare_accounts()
        effective_max_cycles = max_cycles if max_cycles is not None else self.config.max_cycles
        try:
            while not self._stop:
                if effective_max_cycles is not None and self.cycles_completed >= effective_max_cycles:
                    logger.info("Reached cycle limit (%s)", effective_max_cycles)
                    break
                if self.config.target_volume is not None and self.total_volume >= self.config.target_volume:
                    logger.info("Target volume %s reached", self.config.target_volume)
                    break
                cycle_result = self._execute_cycle()
                self.cycles_completed += 1
                self.total_volume += cycle_result.quote_volume
                self.total_fees += cycle_result.fees
                self._write_status(cycle_result)
                logger.info(self._format_cycle_summary(cycle_result))
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
            label = self._account_label(account_state)
            if self.config.hedge_mode:
                try:
                    account_state.client.change_position_mode(True)
                except ApiError as exc:
                    if exc.error_code in {-4059, -4046}:  # already configured on Binance-compatible APIs
                        logger.debug("%s already in hedge mode", label)
                    else:
                        raise
            try:
                account_state.client.change_leverage(self.config.symbol, self.config.leverage)
            except ApiError as exc:
                logger.warning("Unable to set leverage for %s: %s", label, exc)
            try:
                account_state.client.change_margin_type(self.config.symbol, self.config.margin_type)
            except ApiError as exc:
                if exc.error_code == -4046:  # no-op if already the same margin type
                    logger.debug("%s already using %s margin", label, self.config.margin_type)
                else:
                    logger.warning("Unable to set margin type for %s: %s", label, exc)

    def _execute_cycle(self) -> CycleResult:
        start = time.time()
        trades: List[TradeRecord] = []
        trades.extend(self._perform_account_round(self.long, open_side="BUY", close_side="SELL"))
        trades.extend(self._perform_account_round(self.short, open_side="SELL", close_side="BUY"))
        end = time.time()
        return CycleResult(trades=trades, start_time=start, end_time=end)

    def _perform_account_round(self, account_state: AccountState, open_side: str, close_side: str) -> Sequence[TradeRecord]:
        label = self._account_label(account_state)
        logger.debug(
            "%s Opening %s position",
            label,
            self._format_position_side(account_state.position_side),
        )
        opening_trades = self._place_and_wait(account_state, open_side)
        if self.config.hold_seconds:
            logger.debug(
                "%s %s for %ss before closing position",
                label,
                _paint("Sleeping", DIM),
                self.config.hold_seconds,
            )
            time.sleep(self.config.hold_seconds)
        logger.debug(
            "%s Closing %s position",
            label,
            self._format_position_side(account_state.position_side),
        )
        closing_trades = self._place_and_wait(account_state, close_side)
        return [*opening_trades, *closing_trades]

    def _place_and_wait(self, account_state: AccountState, side: str) -> Sequence[TradeRecord]:
        if self.config.dry_run:
            return self._simulate_trade(account_state, side)

        order_kwargs = self._order_parameters()
        order_result = account_state.client.place_order(
            symbol=self.config.symbol,
            side=side,
            order_type="MARKET",
            position_side=account_state.position_side,
            new_order_resp_type="RESULT",
            **order_kwargs,
        )
        account_state.last_order_timestamp = order_result.update_time
        logger.debug(
            "%s Submitted order %s (%s %s)",
            self._account_label(account_state),
            _paint(str(order_result.order_id), FG_CYAN, BOLD),
            self._format_order_side(side),
            self._order_description(),
        )
        filled_order = self._wait_for_fill(account_state, order_result.order_id)
        trade_records = list(self._fetch_trade_records(account_state, filled_order))
        if not trade_records:
            logger.warning(
                "%s No trade records found for order %s",
                self._account_label(account_state),
                order_result.order_id,
            )
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
        if self.config.order_quantity is not None:
            qty = self.config.order_quantity
            quote_qty = qty * price
        else:
            quote_qty = self.config.order_notional if self.config.order_notional is not None else Decimal("0")
            qty = quote_qty / price if price else quote_qty
        commission = quote_qty * Decimal("0.0004") * Decimal("-1")
        record = TradeRecord(
            account=account_state.name,
            order_id=int(now),
            side=side,
            position_side=account_state.position_side,
            qty=qty,
            quote_qty=quote_qty,
            commission=commission,
            price=price,
            timestamp=now,
        )
        logger.debug(
            "%s Simulated %s trade %s",
            self._account_label(account_state),
            self._format_order_side(side),
            self._order_description(),
        )
        return [record]

    # ------------------------------------------------------------------
    # Status reporting
    # ------------------------------------------------------------------
    def _write_status(self, cycle_result: CycleResult) -> None:
        if not self.status_file:
            return
        interval_minutes = max(self.config.status_update_interval_minutes, 0.0)
        interval_seconds = interval_minutes * 60 if interval_minutes else 0.0
        now = time.time()
        if self._last_status_write and interval_seconds and (now - self._last_status_write) < interval_seconds:
            logger.debug(
                "Skipping status file update; last write %.0fs ago (interval %.0fs)",
                now - self._last_status_write,
                interval_seconds,
            )
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
            "order_value": str(self.config.order_notional) if self.config.order_notional is not None else None,
            "order_quantity": str(self.config.order_quantity) if self.config.order_quantity is not None else None,
            "leverage": self.config.leverage,
            "margin_type": self.config.margin_type,
            "hedge_mode": self.config.hedge_mode,
            "dry_run": self.config.dry_run,
            "status_update_interval_minutes": self.config.status_update_interval_minutes,
        }
        try:
            Path(self.status_file).write_text(json.dumps(status_payload, indent=2), encoding="utf-8")
            self._last_status_write = now
        except OSError as exc:  # pragma: no cover - disk failure
            logger.error("Failed to write status file %s: %s", self.status_file, exc)


__all__ = [
    "DeltaNeutralVolumeBot",
    "AccountState",
    "TradeRecord",
    "CycleResult",
]
