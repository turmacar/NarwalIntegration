"""Tests for the static-map refresh that runs after a clean ends."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import tests.ha_stubs  # noqa: E402

tests.ha_stubs.install()

from homeassistant.exceptions import HomeAssistantError  # noqa: E402

from custom_components.narwal import _async_register_services  # noqa: E402
from custom_components.narwal.const import DOMAIN, SERVICE_REFRESH_MAP  # noqa: E402
from custom_components.narwal.coordinator import NarwalCoordinator  # noqa: E402
from custom_components.narwal.narwal_client import NarwalState  # noqa: E402
from custom_components.narwal.narwal_client.const import WorkingStatus  # noqa: E402
from tests import test_coordinator_map  # noqa: E402


def _coordinator() -> NarwalCoordinator:
    coordinator = test_coordinator_map.TestCoordinatorMapRefresh()._make_coordinator()
    coordinator.client.get_map = AsyncMock()
    coordinator._refresh_dock_status = AsyncMock()
    coordinator._scope_pending_map_display_cache_snapshot = MagicMock()
    coordinator._restore_pending_map_display_cache = MagicMock()
    return coordinator


def test_return_to_dock_schedules_refresh_after_clean() -> None:
    """Docking after a clean refreshes the map as well as the dock status."""
    coordinator = _coordinator()
    coordinator._prev_working_status = WorkingStatus.CLEANING
    state = NarwalState()
    state.map_data = MagicMock()
    state.working_status = WorkingStatus.STANDBY

    coordinator._on_state_update(state)

    scheduled = coordinator.hass.async_create_task.call_args.args[0]
    assert scheduled.cr_code.co_name == "_refresh_after_clean"


async def test_refresh_after_clean_refreshes_dock_before_map() -> None:
    coordinator = _coordinator()
    order = MagicMock()
    order.attach_mock(coordinator._refresh_dock_status, "dock")
    order.attach_mock(coordinator.client.get_map, "map")

    await coordinator._refresh_after_clean()

    assert [call[0] for call in order.mock_calls] == ["dock", "map"]


async def test_async_refresh_map_pushes_update_and_restores_pending_cache() -> None:
    coordinator = _coordinator()

    assert await coordinator.async_refresh_map() is True

    coordinator.client.get_map.assert_awaited_once()
    coordinator._scope_pending_map_display_cache_snapshot.assert_called_once()
    coordinator._restore_pending_map_display_cache.assert_called_once()
    coordinator.async_set_updated_data.assert_called_once_with(coordinator.client.state)


async def test_async_refresh_map_failure_keeps_cached_map() -> None:
    coordinator = _coordinator()
    cached_map = MagicMock()
    coordinator.client.state.map_data = cached_map
    coordinator.client.get_map = AsyncMock(side_effect=TimeoutError)

    assert await coordinator.async_refresh_map() is False

    assert coordinator.client.state.map_data is cached_map
    coordinator._restore_pending_map_display_cache.assert_not_called()
    coordinator.async_set_updated_data.assert_not_called()


def _refresh_map_handler(loaded: dict):
    hass = MagicMock()
    hass.data = {DOMAIN: loaded}
    hass.services.has_service.return_value = False
    handlers = {}
    hass.services.async_register.side_effect = (
        lambda domain, name, handler, **kwargs: handlers.setdefault((domain, name), handler)
    )
    _async_register_services(hass)
    return handlers[(DOMAIN, SERVICE_REFRESH_MAP)]


async def test_refresh_map_service_refreshes_every_loaded_vacuum() -> None:
    first, second = _coordinator(), _coordinator()
    first.async_refresh_map = AsyncMock(return_value=True)
    second.async_refresh_map = AsyncMock(return_value=True)
    handler = _refresh_map_handler({"entry-1": first, "entry-2": second, "other": object()})

    await handler(SimpleNamespace(data={}))

    first.async_refresh_map.assert_awaited_once()
    second.async_refresh_map.assert_awaited_once()


async def test_refresh_map_service_reports_failure_after_trying_every_vacuum() -> None:
    failing, healthy = _coordinator(), _coordinator()
    failing.async_refresh_map = AsyncMock(return_value=False)
    healthy.async_refresh_map = AsyncMock(return_value=True)
    handler = _refresh_map_handler({"entry-1": failing, "entry-2": healthy})

    with pytest.raises(HomeAssistantError, match="could not be refreshed"):
        await handler(SimpleNamespace(data={}))

    healthy.async_refresh_map.assert_awaited_once()


async def test_refresh_map_service_requires_a_loaded_vacuum() -> None:
    handler = _refresh_map_handler({})

    with pytest.raises(HomeAssistantError, match="No Narwal vacuum is loaded"):
        await handler(SimpleNamespace(data={}))
