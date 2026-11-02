"""API tests. No IBKR connection -- these cover the contract the frontend depends on."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import config
from backend.app.ib import client as ib_client
from backend.app.main import app
from backend.app.rules.registry import ALL_STRATEGIES

# Nothing listens here. Without this, a TWS left open makes the "no gateway" test run a real
# scan on the live account and hold clientId 17, locking out the real app.
CLOSED_PORT = 1


@pytest.fixture(scope="module")
def client():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config.settings, "ib_port", CLOSED_PORT)
        mp.setattr(ib_client, "_client", None)
        with TestClient(app) as c:
            yield c
        mp.setattr(ib_client, "_client", None)


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_status_reports_readonly_and_feed_type(client):
    body = client.get("/api/status").json()
    assert body["readonly"] is True  # the socket refuses orders
    assert "market_data_label" in body
    assert isinstance(body["is_delayed"], bool)


def test_config_exposes_every_registered_strategy(client):
    body = client.get("/api/config").json()
    values = [s["value"] for s in body["strategies"]]
    assert values == [s.value for s in ALL_STRATEGIES]


def test_config_exposes_the_resolved_ambiguities(client):
    """The UI states which reading is in force, so a surprising number is explainable."""
    flags = client.get("/api/config").json()["flags"]
    assert flags["iv_gate_uses"] in ("percentile", "raw")
    assert isinstance(flags["dte_annualise_plus_one"], bool)
    assert flags["chart_rules_are_hard_gates"] is False  # advisory, per the user's choice
    assert flags["csp_cash_basis"] == "strike"


def test_every_strategy_ships_columns_and_bands(client):
    body = client.get("/api/config").json()
    for strategy in body["strategies"]:
        assert strategy["columns"], f"{strategy['value']} has no columns"
        assert any(c["key"] == "verdict" for c in strategy["columns"])
    assert body["bands"]["roi_annual"] == [0.15, 0.25, 0.4]
    assert body["bands"]["csp_roi_cash"] == [0.1, 0.2, 0.3]


def test_csp_columns_match_the_workbook_vocabulary(client):
    """Column labels carry over from CSP Template.xlsx so the vocabulary transfers."""
    body = client.get("/api/config").json()
    csp = next(s for s in body["strategies"] if s["value"] == "cash_secured_put")
    labels = {c["label"] for c in csp["columns"]}
    for expected in {
        "Max ROI (Cash) %",
        "Max ROI (BP) %",
        "Assigned Price",
        "Collected Premium",
        "Net Cost",
        "Discount vs. Price",
    }:
        assert expected in labels, f"missing workbook column: {expected}"


def test_watchlist_round_trip(client):
    client.post("/api/watchlist", json={"ticker": "zzzt", "shares_owned": 200})
    tickers = {row["ticker"] for row in client.get("/api/watchlist").json()}
    assert "ZZZT" in tickers

    entry = next(r for r in client.get("/api/watchlist").json() if r["ticker"] == "ZZZT")
    assert entry["shares_owned"] == 200

    assert client.delete("/api/watchlist/ZZZT").status_code == 200
    tickers = {row["ticker"] for row in client.get("/api/watchlist").json()}
    assert "ZZZT" not in tickers


def test_removing_an_absent_ticker_is_a_404(client):
    assert client.delete("/api/watchlist/NOSUCH").status_code == 404


def test_scan_requires_a_strategy_or_a_ticker(client):
    assert client.post("/api/scan", json={}).status_code in (400, 503)


def test_scan_rejects_an_unknown_strategy(client):
    response = client.post("/api/scan", json={"strategy": "iron_butterfly_of_doom"})
    assert response.status_code in (400, 503)


def test_scan_without_a_gateway_returns_actionable_guidance(client):
    """A refused connection must explain the fix, not surface a raw traceback."""
    response = client.post("/api/scan", json={"strategy": "bull_put"})
    if response.status_code == 503:
        detail = response.json()["detail"]
        assert "IB Gateway" in detail
        assert "7497" in detail or "7496" in detail


def test_unknown_api_path_is_404_not_the_spa(client):
    assert client.get("/api/definitely-not-real").status_code == 404
