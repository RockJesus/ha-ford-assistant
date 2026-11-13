"""Whether the current driving mode is blocking any action."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FordAssistantConfigEntry
from .entity import FordAssistantEntity

# Coordinator or push updates; nothing polls on its own.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FordAssistantConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the automations-suppressed binary sensor."""
    async_add_entities([AutomationsSuppressedBinarySensor(entry.runtime_data)])


class AutomationsSuppressedBinarySensor(FordAssistantEntity, BinarySensorEntity):
    """On when the current mode blocks at least one Ford Assistant action.

    Off in Household with no active driver, and off for a driver whose
    profile allows every action. On in the Service and Guest defaults,
    because both block something. Your own automations can use this as a
    condition; Ford Assistant already enforces the same list itself.
    """

    _attr_translation_key = "automations_suppressed"

    def __init__(self, controller) -> None:
        """Initialise the sensor."""
        super().__init__(controller, "automations_suppressed")

    @property
    def is_on(self) -> bool:
        """Whether any action is currently blocked."""
        return self.controller.policy.automations_suppressed
