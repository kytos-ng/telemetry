"""Test kytos_api_helper.py"""
from httpx import Response
from unittest.mock import AsyncMock, MagicMock
from napps.kytos.telemetry_int.kytos_api_helper import (
    get_evc,
    get_stored_flows,
    get_evcs,
)


async def test_get_evcs(evcs_data, monkeypatch) -> None:
    """Test get_evcs."""
    aclient_mock, awith_mock = AsyncMock(), MagicMock()
    aclient_mock.get.return_value = Response(200, json=evcs_data, request=MagicMock())
    awith_mock.return_value.__aenter__.return_value = aclient_mock
    monkeypatch.setattr("httpx.AsyncClient", awith_mock)
    data = await get_evcs()
    assert aclient_mock.get.call_args[0][0] == "/evc/?archived=false"
    assert data == evcs_data


async def test_get_evc(evcs_data, monkeypatch) -> None:
    """Test get_evc."""
    evc_id = "3766c105686749"
    evc_data = evcs_data[evc_id]

    aclient_mock, awith_mock = AsyncMock(), MagicMock()
    aclient_mock.get.return_value = Response(200, json=evc_data, request=MagicMock())
    awith_mock.return_value.__aenter__.return_value = aclient_mock
    monkeypatch.setattr("httpx.AsyncClient", awith_mock)

    data = await get_evc(evc_id)
    assert aclient_mock.get.call_args[0][0] == f"/evc/{evc_id}"
    assert data[evc_id] == evc_data


async def test_get_stored_flows(monkeypatch, intra_evc_evpl_flows_data) -> None:
    """Test get_stored_flows."""
    evc_data = intra_evc_evpl_flows_data
    dpid = "00:00:00:00:00:00:00:01"
    cookies = [evc_data[dpid][0]["flow"]["cookie"]]

    aclient_mock, awith_mock = AsyncMock(), MagicMock()
    aclient_mock.get.return_value = Response(
        200, json=intra_evc_evpl_flows_data, request=MagicMock()
    )
    awith_mock.return_value.__aenter__.return_value = aclient_mock
    monkeypatch.setattr("httpx.AsyncClient", awith_mock)

    data = await get_stored_flows(cookies)
    assert (
        aclient_mock.get.call_args[0][0] == "/stored_flows?"
        f"state=installed&state=pending&"
        f"cookie_range={cookies[0]}&cookie_range={cookies[0]}"
    )
    assert len(data) == 1
    assert list(data.keys()) == cookies
    assert len(data[cookies[0]]) == 2
    for flows in data.values():
        for flow in flows:
            assert flow["switch"] == dpid
