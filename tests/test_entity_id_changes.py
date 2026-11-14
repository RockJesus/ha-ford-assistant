"""Ford Assistant's entities keep running when their entity ID is changed."""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

import custom_components.ford_assistant as package
from custom_components.ford_assistant.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform, entity_registry as er
from homeassistant.helpers.entity import Entity

from .test_init import _ours, _setup, ford  # noqa: F401, F811

LIFECYCLE = (
    "async_prepare_to_add_to_hass",
    "async_added_to_hass",
    "async_will_remove_from_hass",
)


def _entity_classes() -> list[type[Entity]]:
    found: list[type[Entity]] = []
    for info in pkgutil.walk_packages(package.__path__, f"{package.__name__}."):
        module = importlib.import_module(info.name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(cls, Entity)
                and cls.__module__ == module.__name__
                and cls not in found
            ):
                found.append(cls)
    return found


def test_entities_handle_entity_id_changes() -> None:
    """Every class with its own add/remove code also has the rename hook.

    From Home Assistant 2026.11 a class without it is removed and added
    again when its entity ID changes (until 2027.11).
    """
    classes = _entity_classes()
    assert classes
    missing = [
        f"{cls.__module__}.{cls.__qualname__}"
        for cls in classes
        if any(name in vars(cls) for name in LIFECYCLE)
        and "async_entity_id_changed" not in vars(cls)
    ]
    assert not missing, f"missing async_entity_id_changed: {missing}"


@pytest.mark.skipif(
    not hasattr(Entity, "async_entity_id_changed"),
    reason="Entity IDs are changed in place from Home Assistant 2026.11",
)
async def test_renamed_switch_keeps_working(hass: HomeAssistant, ford: str) -> None:
    async_mock_service(hass, "lock", "lock")
    entry = await _setup(hass, ford)
    old_id = _ours(hass, "switch", "auto_lock")
    assert old_id
    assert hass.states.get(old_id).state == "on"
    entity = next(
        e
        for platform in entity_platform.async_get_platforms(hass, DOMAIN)
        for e in platform.entities.values()
        if e.entity_id == old_id
    )

    new_id = "switch.renamed_auto_lock"
    er.async_get(hass).async_update_entity(old_id, new_entity_id=new_id)
    await hass.async_block_till_done()

    assert hass.states.get(old_id) is None
    assert hass.states.get(new_id).state == "on"
    # Updated in place, not removed and added again.
    assert entity.entity_id == new_id
    assert entity.hass is hass

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": new_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert entry.runtime_data.features["auto_lock"] is False
    assert hass.states.get(new_id).state == "off"
    assert hass.states.get(old_id) is None

    # The controller's listener still writes the state under the new ID.
    entry.runtime_data.set_feature("auto_lock", True)
    await hass.async_block_till_done()
    assert hass.states.get(new_id).state == "on"
