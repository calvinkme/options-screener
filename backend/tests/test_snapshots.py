"""Batched market-data lines. A fake IB stands in -- no IBKR connection."""

from __future__ import annotations

import time
from types import SimpleNamespace

from backend.app import config
from backend.app.ib import marketdata
from backend.app.ib.client import IBClient


class FakeIB:
    """Records open lines; tickers never populate, so every batch hits its timeout."""

    def __init__(self) -> None:
        self.open: set[int] = set()
        self.peak = 0
        self.errorEvent = self  # IBClient subscribes with +=

    def __iadd__(self, handler):
        return self

    def reqMktData(self, contract, generic_ticks, snapshot):
        self.open.add(contract.conId)
        self.peak = max(self.peak, len(self.open))
        return SimpleNamespace(contract=contract, bid=None, ask=None, last=None, close=None)

    def cancelMktData(self, contract):
        self.open.discard(contract.conId)


def make_client(monkeypatch) -> tuple[IBClient, FakeIB]:
    monkeypatch.setattr(config, "REQUEST_DELAY_SECONDS", 0)
    client = IBClient()
    fake = FakeIB()
    client.ib = fake
    return client, fake


def contracts(n: int):
    return [SimpleNamespace(conId=i, secType="OPT") for i in range(n)]


async def test_one_shared_wait_per_batch_not_per_contract(monkeypatch):
    client, _ = make_client(monkeypatch)
    started = time.monotonic()
    tickers = await client.snapshots(contracts(30), timeout=0.3, batch_size=10)
    elapsed = time.monotonic() - started
    assert len(tickers) == 30
    # 3 batches x 0.3s. One-at-a-time would be 30 x 0.3s = 9s.
    assert elapsed < 2.0


async def test_concurrent_lines_never_exceed_batch_size(monkeypatch):
    client, fake = make_client(monkeypatch)
    await client.snapshots(contracts(25), timeout=0.1, batch_size=10)
    assert fake.peak == 10


async def test_every_line_is_cancelled(monkeypatch):
    client, fake = make_client(monkeypatch)
    await client.snapshots(contracts(25), timeout=0.1, batch_size=10)
    assert fake.open == set()


async def test_order_matches_input(monkeypatch):
    client, _ = make_client(monkeypatch)
    cs = contracts(12)
    tickers = await client.snapshots(cs, timeout=0.1, batch_size=5)
    assert [t.contract.conId for t in tickers] == [c.conId for c in cs]


def _detail(strike: float, right: str, con_id: int = 1):
    return SimpleNamespace(contract=SimpleNamespace(strike=strike, right=right, conId=con_id))


def test_only_listed_strikes_are_quoted():
    # The chain advertises 312.5 (listed on another expiry); this expiry does not list it.
    details = [_detail(310.0, "P"), _detail(310.0, "C"), _detail(315.0, "P")]
    got = marketdata.listed_contracts(details, [310.0, 312.5, 315.0], ("P", "C"))
    assert [(c.strike, c.right) for c in got] == [(310.0, "P"), (310.0, "C"), (315.0, "P")]


def test_strikes_outside_the_window_and_unwanted_rights_are_dropped():
    details = [_detail(300.0, "P"), _detail(310.0, "C"), _detail(310.0, "P")]
    got = marketdata.listed_contracts(details, [310.0], ("P",))
    assert [(c.strike, c.right) for c in got] == [(310.0, "P")]


def test_no_details_means_no_contracts_not_a_crash():
    assert marketdata.listed_contracts(None, [310.0], ("P", "C")) == []
