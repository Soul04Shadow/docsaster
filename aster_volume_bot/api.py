"""HTTP client for interacting with the AsterDEX futures API."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import hmac
import logging
import time
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlencode

try:  # pragma: no cover - exercised when requests is available
    import requests  # type: ignore
except ImportError:  # pragma: no cover - fallback for test environments
    class _MissingRequests:
        class RequestException(Exception):
            pass

        class Session:  # Minimal stub so tests can run without requests installed
            def __init__(self) -> None:
                self.headers: Dict[str, str] = {}

            def get(self, *args: Any, **kwargs: Any) -> Any:
                raise ImportError("The 'requests' package is required to perform HTTP GET requests.")

            def delete(self, *args: Any, **kwargs: Any) -> Any:
                raise ImportError("The 'requests' package is required to perform HTTP DELETE requests.")

            def post(self, *args: Any, **kwargs: Any) -> Any:
                raise ImportError("The 'requests' package is required to perform HTTP POST requests.")

    requests = _MissingRequests()  # type: ignore

logger = logging.getLogger(__name__)

BASE_URL = "https://fapi.asterdex.com"


class ApiError(RuntimeError):
    """Represents an HTTP or API-layer failure."""

    def __init__(self, status_code: int, error_code: Optional[int], message: str, payload: Any = None) -> None:
        super().__init__(f"HTTP {status_code} (code={error_code}): {message}")
        self.status_code = status_code
        self.error_code = error_code
        self.payload = payload


@dataclass
class OrderResult:
    """Minimal representation of the order payload returned by the API."""

    order_id: int
    status: str
    executed_qty: Decimal
    cum_quote: Decimal
    avg_price: Decimal
    update_time: int


class ApiClient:
    """Thin wrapper around the Aster futures REST API."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = BASE_URL,
        session: Optional[requests.Session] = None,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret.encode("utf-8")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        if api_key:
            self.session.headers.update({"X-MBX-APIKEY": api_key})

    # ------------------------------------------------------------------
    # Low level helpers
    # ------------------------------------------------------------------
    def _timestamp(self) -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        return format(value, "f")

    @staticmethod
    def _serialize(params: Dict[str, Any]) -> Dict[str, Any]:
        serialized: Dict[str, Any] = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, Decimal):
                serialized[key] = ApiClient._format_decimal(value)
            elif isinstance(value, bool):
                serialized[key] = "true" if value else "false"
            else:
                serialized[key] = value
        return serialized

    def _sign(self, params: Dict[str, Any]) -> str:
        query_string = urlencode(params, doseq=True)
        return hmac.new(self.api_secret, query_string.encode("utf-8"), hashlib.sha256).hexdigest()

    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, signed: bool = False) -> Any:
        params = params.copy() if params else {}
        params = self._serialize(params)
        if signed:
            params.setdefault("timestamp", self._timestamp())
            signature = self._sign(params)
            params["signature"] = signature
        url = f"{self.base_url}{path}"
        try:
            if method == "GET":
                response = self.session.get(url, params=params, timeout=self.timeout)
            elif method == "DELETE":
                response = self.session.delete(url, params=params, timeout=self.timeout)
            else:  # POST/PUT
                response = self.session.post(url, data=params, timeout=self.timeout)
        except requests.RequestException as exc:  # pragma: no cover - network failure
            raise ApiError(-1, None, f"Network error calling {path}: {exc}") from exc
        if response.status_code != 200:
            try:
                payload = response.json()
                message = payload.get("msg", response.text)
                error_code = payload.get("code")
            except ValueError:
                payload = response.text
                message = response.text
                error_code = None
            raise ApiError(response.status_code, error_code, message, payload)
        if response.headers.get("Content-Type", "").startswith("application/json"):
            return response.json()
        return response.text

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def ping(self) -> Any:
        """Calls the connectivity test endpoint."""

        return self._request("GET", "/fapi/v1/ping")

    def get_server_time(self) -> int:
        response = self._request("GET", "/fapi/v1/time")
        return int(response["serverTime"])

    def change_position_mode(self, dual_side: bool) -> Dict[str, Any]:
        params = {"dualSidePosition": "true" if dual_side else "false"}
        return self._request("POST", "/fapi/v1/positionSide/dual", params=params, signed=True)

    def change_multi_asset_mode(self, multi_asset: bool) -> Dict[str, Any]:
        params = {"multiAssetsMargin": "true" if multi_asset else "false"}
        return self._request("POST", "/fapi/v1/multiAssetsMargin", params=params, signed=True)

    def change_margin_type(self, symbol: str, margin_type: str) -> Dict[str, Any]:
        params = {"symbol": symbol, "marginType": margin_type}
        return self._request("POST", "/fapi/v1/marginType", params=params, signed=True)

    def change_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        params = {"symbol": symbol, "leverage": leverage}
        return self._request("POST", "/fapi/v1/leverage", params=params, signed=True)

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Optional[Decimal] = None,
        position_side: Optional[str] = None,
        time_in_force: Optional[str] = None,
        reduce_only: Optional[bool] = None,
        price: Optional[Decimal] = None,
        new_client_order_id: Optional[str] = None,
        close_position: Optional[bool] = None,
        working_type: Optional[str] = None,
        stop_price: Optional[Decimal] = None,
        callback_rate: Optional[Decimal] = None,
        activation_price: Optional[Decimal] = None,
        new_order_resp_type: Optional[str] = None,
        recv_window: Optional[int] = None,
        quote_order_qty: Optional[Decimal] = None,
    ) -> OrderResult:
        params: Dict[str, Any] = {"symbol": symbol, "side": side, "type": order_type}
        if quantity is not None:
            params["quantity"] = quantity
        if quote_order_qty is not None:
            params["quoteOrderQty"] = quote_order_qty
        if "quantity" not in params and "quoteOrderQty" not in params:
            raise ValueError("An order requires either 'quantity' or 'quote_order_qty'.")
        if position_side:
            params["positionSide"] = position_side
        if time_in_force:
            params["timeInForce"] = time_in_force
        if reduce_only is not None:
            params["reduceOnly"] = reduce_only
        if price is not None:
            params["price"] = price
        if new_client_order_id:
            params["newClientOrderId"] = new_client_order_id
        if close_position is not None:
            params["closePosition"] = close_position
        if working_type:
            params["workingType"] = working_type
        if stop_price is not None:
            params["stopPrice"] = stop_price
        if callback_rate is not None:
            params["callbackRate"] = callback_rate
        if activation_price is not None:
            params["activationPrice"] = activation_price
        if new_order_resp_type:
            params["newOrderRespType"] = new_order_resp_type
        if recv_window is not None:
            params["recvWindow"] = recv_window
        response = self._request("POST", "/fapi/v1/order", params=params, signed=True)
        return OrderResult(
            order_id=int(response["orderId"]),
            status=response.get("status", "UNKNOWN"),
            executed_qty=Decimal(response.get("executedQty", "0")),
            cum_quote=Decimal(response.get("cumQuote", "0")),
            avg_price=Decimal(response.get("avgPrice", "0")),
            update_time=int(response.get("updateTime", 0)),
        )

    def query_order(self, symbol: str, order_id: Optional[int] = None, client_order_id: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"symbol": symbol}
        if order_id is not None:
            params["orderId"] = order_id
        if client_order_id is not None:
            params["origClientOrderId"] = client_order_id
        return self._request("GET", "/fapi/v1/order", params=params, signed=True)

    def cancel_order(self, symbol: str, order_id: Optional[int] = None, client_order_id: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"symbol": symbol}
        if order_id is not None:
            params["orderId"] = order_id
        if client_order_id is not None:
            params["origClientOrderId"] = client_order_id
        return self._request("DELETE", "/fapi/v1/order", params=params, signed=True)

    def get_account_trades(
        self,
        symbol: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        from_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Iterable[Dict[str, Any]]:
        params: Dict[str, Any] = {"symbol": symbol}
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        if from_id is not None:
            params["fromId"] = from_id
        if limit is not None:
            params["limit"] = limit
        return self._request("GET", "/fapi/v1/userTrades", params=params, signed=True)

    def get_income_history(
        self,
        symbol: Optional[str] = None,
        income_type: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Iterable[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = symbol
        if income_type is not None:
            params["incomeType"] = income_type
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        if limit is not None:
            params["limit"] = limit
        return self._request("GET", "/fapi/v1/income", params=params, signed=True)

    def get_account_information(self) -> Dict[str, Any]:
        return self._request("GET", "/fapi/v2/account", signed=True)


__all__ = [
    "ApiClient",
    "ApiError",
    "OrderResult",
    "BASE_URL",
]
