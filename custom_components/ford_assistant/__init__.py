"""Ford Assistant: a companion to the Ford integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv, issue_registry as ir, service
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .access import async_ensure_allowed, async_get_access
from .const import CONF_VIN, DOMAIN, REQUIRED_ROLES, SERVICE_SET_DRIVING_MODE
from .controller import FordAssistantController
from .driving import ACTIVE_DRIVER_NONE, DRIVING_MODES, duration_config
from .panel import (
    async_register_panel,
    async_setup_websocket,
    async_unregister_panel_if_unused,
)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

type FordAssistantConfigEntry = ConfigEntry[FordAssistantController]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the dashboard's websocket API, access, and actions."""
    await async_get_access(hass)
    async_setup_websocket(hass)
    await _async_register_services(hass)
    return True


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register actions once per Home Assistant run."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_DRIVING_MODE):
        return

    async def async_set_driving_mode(call: ServiceCall) -> None:
        """Set the mode, optionally for a fixed time, and the active driver."""
        await async_ensure_allowed(hass, call.context)
        entry_ids = await service.async_extract_config_entry_ids(call)
        matched = [
            entry
            for entry_id in entry_ids
            if (entry := hass.config_entries.async_get_entry(entry_id))
            and entry.domain == DOMAIN
            and entry.state is ConfigEntryState.LOADED
        ]
        if not matched:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="no_vehicle"
            )
        use_duration = "duration" in call.data
        update_driver = "active_driver" in call.data
        driver = call.data.get("active_driver")
        for entry in matched:
            controller = entry.runtime_data
            if update_driver:
                controller.set_active_driver(
                    None if driver in (None, "", ACTIVE_DRIVER_NONE) else driver,
                    manual=True,
                    reason="service",
                )
            controller.set_driving_mode(
                call.data["mode"],
                duration=call.data.get("duration"),
                use_duration=use_duration,
                reason="service",
            )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_DRIVING_MODE,
        async_set_driving_mode,
        schema=cv.make_entity_service_schema(
            {
                vol.Required("mode"): vol.In(DRIVING_MODES),
                vol.Optional("duration"): duration_config,
                vol.Optional("active_driver"): vol.Any(
                    ACTIVE_DRIVER_NONE, cv.entity_id
                ),
            }
        ),
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: FordAssistantConfigEntry
) -> bool:
    """Set up Ford Assistant for one vehicle."""
    controller = FordAssistantController(hass, entry)
    controller.version = str((await async_get_integration(hass, DOMAIN)).version)
    await controller.async_load()

    issue_id = f"missing_entities_{entry.entry_id}"
    if missing := [role for role in REQUIRED_ROLES if role not in controller.entities]:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="missing_entities",
            translation_placeholders={
                "vehicle": entry.title,
                "entities": ", ".join(missing),
            },
        )
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="missing_entities",
            translation_placeholders={"vin": entry.data[CONF_VIN]},
        )
    ir.async_delete_issue(hass, DOMAIN, issue_id)

    entry.runtime_data = controller
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    controller.async_start()
    entry.async_on_unload(controller.async_stop)
    # Re-check who may see the sidebar: a household driver who isn't an admin
    # means the panel can no longer stay admin-only.
    await async_register_panel(hass, controller.version, update=True)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: FordAssistantConfigEntry
) -> bool:
    """Unload a config entry."""
    await entry.runtime_data.async_flush()
    async_unregister_panel_if_unused(hass, entry.entry_id)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: FordAssistantConfigEntry
) -> None:
    """Delete saved settings when the entry is removed."""
    from homeassistant.helpers.storage import Store

    from .const import STORAGE_VERSION

    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
