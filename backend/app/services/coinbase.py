from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class CoinbaseCredentials:
    api_key: str
    api_secret: str
    passphrase: str = ""


class CoinbaseClient:
    def __init__(self, base_url: str | None = None, timeout: float = 15.0) -> None:
        self.base_url = base_url or settings.coinbase_api_base_url
        self.timeout = timeout
        self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout)
        self._last_request_ts = 0.0
        self.min_interval_seconds = 0.12

    def _throttle(self) -> None:
        now = time.time()
        elapsed = now - self._last_request_ts
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)
        self._last_request_ts = time.time()

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        credentials: CoinbaseCredentials | None = None,
        retries: int = 3,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {"Content-Type": "application/json"}

        for attempt in range(retries):
            self._throttle()
            try:
                if credentials:
                    timestamp = str(time.time())
                    body = json.dumps(json_data or {}, separators=(",", ":")) if json_data else ""
                    message = f"{timestamp}{method.upper()}{path}{body}"
                    signature = hmac.new(
                        credentials.api_secret.encode("utf-8"),
                        message.encode("utf-8"),
                        hashlib.sha256,
                    ).hexdigest()
                    headers.update(
                        {
                            "CB-ACCESS-KEY": credentials.api_key,
                            "CB-ACCESS-SIGN": signature,
                            "CB-ACCESS-TIMESTAMP": timestamp,
                        }
                    )
                    if credentials.passphrase:
                        headers["CB-ACCESS-PASSPHRASE"] = credentials.passphrase

                response = self._client.request(
                    method=method.upper(),
                    url=path,
                    params=params,
                    json=json_data,
                    headers=headers,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                logger.error(
                    "coinbase_http_error",
                    extra={
                        "context": {
                            "method": method,
                            "path": path,
                            "status_code": status_code,
                            "body": exc.response.text[:300],
                        }
                    },
                )
                raise
            except Exception:
                if attempt < retries - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise

    def get_server_time(self) -> datetime:
        data = self._request("GET", "/api/v3/brokerage/time")
        epoch = int(data.get("epochSeconds", time.time()))
        return datetime.fromtimestamp(epoch, tz=timezone.utc)

    def get_products(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/api/v3/brokerage/products")
        products = data.get("products", [])
        return [p for p in products if p.get("quote_currency_id") == "USDC"]

    def get_product(self, product_id: str) -> dict[str, Any]:
        data = self._request("GET", f"/api/v3/brokerage/products/{product_id}")
        return data.get("product", data)

    def get_candles(
        self,
        product_id: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        params = {
            "start": int(start.timestamp()),
            "end": int(end.timestamp()),
            "granularity": granularity,
        }
        data = self._request(
            "GET",
            f"/api/v3/brokerage/products/{product_id}/candles",
            params=params,
        )
        candles = data.get("candles", [])
        out = []
        for c in candles:
            out.append(
                {
                    "start": int(c.get("start", 0)),
                    "open": float(c.get("open", 0.0)),
                    "high": float(c.get("high", 0.0)),
                    "low": float(c.get("low", 0.0)),
                    "close": float(c.get("close", 0.0)),
                    "volume": float(c.get("volume", 0.0)),
                }
            )
        out.sort(key=lambda x: x["start"])
        return out

    def get_best_bid_ask(self, product_ids: list[str]) -> dict[str, Any]:
        params = {"product_ids": ",".join(product_ids)}
        data = self._request("GET", "/api/v3/brokerage/best_bid_ask", params=params)
        return data

    def place_limit_order(
        self,
        credentials: CoinbaseCredentials,
        product_id: str,
        side: str,
        size: str,
        price: str,
        client_order_id: str | None = None,
        post_only: bool = True,
    ) -> dict[str, Any]:
        order_id = client_order_id or str(uuid.uuid4())
        payload = {
            "client_order_id": order_id,
            "product_id": product_id,
            "side": side.upper(),
            "order_configuration": {
                "limit_limit_gtc": {
                    "base_size": size,
                    "limit_price": price,
                    "post_only": post_only,
                }
            },
        }
        return self._request(
            "POST",
            "/api/v3/brokerage/orders",
            json_data=payload,
            credentials=credentials,
        )

    def place_market_order(
        self,
        credentials: CoinbaseCredentials,
        product_id: str,
        side: str,
        size: str,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        order_id = client_order_id or str(uuid.uuid4())
        payload = {
            "client_order_id": order_id,
            "product_id": product_id,
            "side": side.upper(),
            "order_configuration": {
                "market_market_ioc": {
                    "base_size": size,
                }
            },
        }
        return self._request(
            "POST",
            "/api/v3/brokerage/orders",
            json_data=payload,
            credentials=credentials,
        )

    def cancel_orders(self, credentials: CoinbaseCredentials, order_ids: list[str]) -> dict[str, Any]:
        payload = {"order_ids": order_ids}
        return self._request(
            "POST",
            "/api/v3/brokerage/orders/batch_cancel",
            json_data=payload,
            credentials=credentials,
        )

    def get_order(self, credentials: CoinbaseCredentials, order_id: str) -> dict[str, Any]:
        data = self._request(
            "GET",
            f"/api/v3/brokerage/orders/historical/{order_id}",
            credentials=credentials,
        )
        return data

    def list_fills(
        self,
        credentials: CoinbaseCredentials,
        product_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if product_id:
            params["product_id"] = product_id
        if start:
            params["start_sequence_timestamp"] = start.isoformat()
        if end:
            params["end_sequence_timestamp"] = end.isoformat()
        data = self._request(
            "GET",
            "/api/v3/brokerage/orders/historical/fills",
            params=params,
            credentials=credentials,
        )
        return data.get("fills", [])

    def list_open_orders(self, credentials: CoinbaseCredentials) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            "/api/v3/brokerage/orders/historical/batch",
            params={"order_status": "OPEN"},
            credentials=credentials,
        )
        return data.get("orders", [])


coinbase_client = CoinbaseClient()
