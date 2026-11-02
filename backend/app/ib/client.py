"""IB Gateway connection, pacing and error handling.

Three things this module exists to guarantee:

1. **Read-only.** The socket is opened with `readonly=True`, so the gateway itself refuses
   order submission. CLAUDE.md's first non-goal is "No order placement. Ever." -- this makes
   that a property of the connection rather than a promise about our own code.
2. **Paced.** IBKR throttles at roughly 50 requests/second and ~100 concurrent market data
   lines. Requests are serialised through a lock with a configured delay.
3. **Survivable.** A dropped connection or a pacing violation logs and lets the scan continue
   with the tickers it can still reach, rather than aborting the run.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

from ib_async import IB, Contract

from backend.app import config

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Errors that mean "this contract/request, not the session". Log and carry on.
BENIGN_ERROR_CODES = {
    162,  # historical data service error / pacing
    165,  # historical data service message
    200,  # no security definition found
    300,  # cannot find EId with tickerId
    354,  # requested market data is not subscribed
    2104,  # market data farm connection is OK
    2106,  # HMDS data farm connection is OK
    2107,  # HMDS data farm connection is inactive
    2108,  # market data farm connection is inactive
    2119,  # market data farm is connecting
    2158,  # sec-def data farm connection is OK
}

FATAL_ERROR_CODES = {
    504,  # not connected
    1100,  # connectivity between IB and TWS has been lost
}


class IBConnectionError(RuntimeError):
    """Raised when the gateway is unreachable, with guidance rather than a bare traceback."""


class IBClient:
    """Owns the socket. One instance per process."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        client_id: int | None = None,
        market_data_type: int | None = None,
    ) -> None:
        self.host = host or config.settings.ib_host
        self.port = port or config.settings.ib_port
        self.client_id = client_id or config.settings.ib_client_id
        self.market_data_type = market_data_type or config.settings.ib_market_data_type

        self.ib = IB()
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._errors: list[dict[str, Any]] = []
        self.ib.errorEvent += self._on_error

    # -- lifecycle ---------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self.ib.isConnected()

    async def connect(self) -> None:
        if self.connected:
            return
        try:
            await self.ib.connectAsync(
                host=self.host,
                port=self.port,
                clientId=self.client_id,
                timeout=8,
                readonly=True,  # the gateway will refuse orders from this session
            )
        except (TimeoutError, ConnectionRefusedError, OSError) as exc:
            raise IBConnectionError(
                f"Could not reach IB Gateway at {self.host}:{self.port}. "
                "Check that Gateway or TWS is running and logged in, that API access is "
                "enabled (Global Configuration > API > Settings > Enable ActiveX and Socket "
                f"Clients), and that the socket port matches {self.port} "
                "(7497 paper / 7496 live)."
            ) from exc

        self.ib.reqMarketDataType(self.market_data_type)
        logger.info(
            "Connected to IB %s:%s clientId=%s marketDataType=%s (read-only)",
            self.host,
            self.port,
            self.client_id,
            self.market_data_type,
        )

    async def disconnect(self) -> None:
        if self.connected:
            self.ib.disconnect()

    # -- pacing ------------------------------------------------------------

    async def paced(self, coro_factory: Callable[[], Awaitable[T]], what: str) -> T | None:
        """Run one IBKR request, serialised and spaced, swallowing per-request failures.

        Returns None on a benign failure so the caller can decide whether the missing datum
        is fatal to that row. It never raises for a single bad contract -- that would abort
        a whole scan over one delisted strike.
        """
        async with self._lock:
            gap = time.monotonic() - self._last_request
            if gap < config.REQUEST_DELAY_SECONDS:
                await asyncio.sleep(config.REQUEST_DELAY_SECONDS - gap)

            try:
                result = await coro_factory()
                return result
            except TimeoutError:
                logger.warning("Timeout: %s", what)
                return None
            except Exception as exc:  # noqa: BLE001 -- one bad request must not end the scan
                logger.warning("Request failed (%s): %s", what, exc)
                return None
            finally:
                self._last_request = time.monotonic()

    # -- market data lines -------------------------------------------------

    async def snapshot(
        self, contract: Contract, generic_ticks: str = "", timeout: float | None = None
    ) -> Any:
        """Subscribe, wait for the fields to populate, then always cancel the line.

        The cancel is in a finally block because leaked subscriptions are how a scan hits the
        ~100 concurrent-line ceiling and starts silently returning empty tickers.
        """
        timeout = timeout or config.MARKET_DATA_TIMEOUT_SECONDS
        ticker = self.ib.reqMktData(contract, generic_ticks, snapshot=False)
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                await asyncio.sleep(0.1)
                if _has_usable_data(ticker):
                    break
            return ticker
        finally:
            # A leaked subscription is how a scan quietly hits the ~100-line ceiling.
            with contextlib.suppress(Exception):
                self.ib.cancelMktData(contract)

    async def snapshots(
        self,
        contracts: Sequence[Contract],
        generic_ticks: str = "",
        timeout: float | None = None,
        batch_size: int | None = None,
    ) -> list[Any]:
        """`snapshot` for many contracts, a batch of lines open at once.

        Waiting on one line at a time costs up to `timeout` per contract -- minutes per ticker
        when the feed is frozen and Greeks never arrive. Here each batch shares one wait.
        Subscriptions are still sent one by one at the paced interval, and a batch stays well
        under the ~100 concurrent-line ceiling. Every line is cancelled before the next batch.
        """
        timeout = timeout or config.MARKET_DATA_TIMEOUT_SECONDS
        batch_size = batch_size or config.MARKET_DATA_BATCH_SIZE
        out: list[Any] = []
        for start in range(0, len(contracts), batch_size):
            batch = contracts[start : start + batch_size]
            tickers: list[Any] = []
            try:
                async with self._lock:
                    for contract in batch:
                        gap = time.monotonic() - self._last_request
                        if gap < config.REQUEST_DELAY_SECONDS:
                            await asyncio.sleep(config.REQUEST_DELAY_SECONDS - gap)
                        tickers.append(self.ib.reqMktData(contract, generic_ticks, snapshot=False))
                        self._last_request = time.monotonic()
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    await asyncio.sleep(0.1)
                    if all(_has_usable_data(t) for t in tickers):
                        break
                out.extend(tickers)
            finally:
                for contract in batch:
                    with contextlib.suppress(Exception):
                        self.ib.cancelMktData(contract)
        return out

    # -- errors ------------------------------------------------------------

    def _on_error(self, reqId: int, code: int, message: str, contract: Any) -> None:
        entry = {
            "reqId": reqId,
            "code": code,
            "message": message,
            "contract": str(contract) if contract else None,
        }
        self._errors.append(entry)

        if code in FATAL_ERROR_CODES:
            logger.error("IB fatal %s: %s", code, message)
        elif code in BENIGN_ERROR_CODES:
            logger.debug("IB %s: %s", code, message)
        else:
            logger.warning("IB %s: %s (%s)", code, message, contract)

    @property
    def recent_errors(self) -> list[dict[str, Any]]:
        return self._errors[-50:]

    def status(self) -> dict[str, Any]:
        """What the UI banner needs to tell the truth about the feed."""
        return {
            "connected": self.connected,
            "host": self.host,
            "port": self.port,
            "client_id": self.client_id,
            "market_data_type": self.market_data_type,
            "market_data_label": MARKET_DATA_LABELS.get(
                self.market_data_type, str(self.market_data_type)
            ),
            "is_delayed": self.market_data_type in (3, 4),
            "greeks_fallback": config.COMPUTE_GREEKS_WHEN_MISSING,
            "readonly": True,
            "recent_errors": self.recent_errors[-10:],
        }


MARKET_DATA_LABELS = {
    1: "LIVE",
    2: "FROZEN",
    3: "DELAYED - 15 MIN",
    4: "DELAYED FROZEN - 15 MIN",
}


def _has_usable_data(ticker: Any) -> bool:
    """True once the fields we actually consume have arrived."""
    if ticker is None:
        return False
    has_quote = _is_number(getattr(ticker, "bid", None)) and _is_number(
        getattr(ticker, "ask", None)
    )
    has_last = _is_number(getattr(ticker, "last", None)) or _is_number(
        getattr(ticker, "close", None)
    )
    greeks = getattr(ticker, "modelGreeks", None)
    has_greeks = greeks is not None and greeks.delta is not None
    return (has_quote or has_last) and (has_greeks or not _is_option(ticker))


def _is_option(ticker: Any) -> bool:
    contract = getattr(ticker, "contract", None)
    return getattr(contract, "secType", None) in ("OPT", "FOP")


def _is_number(value: Any) -> bool:
    """IBKR uses NaN and -1 as 'no data' sentinels; neither is a price."""
    if value is None:
        return False
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    return f == f and f >= 0  # NaN != NaN


_client: IBClient | None = None


def get_client() -> IBClient:
    """Process-wide singleton -- IBKR allows one clientId per connection."""
    global _client
    if _client is None:
        _client = IBClient()
    return _client
