"""Who may use Ford Assistant.

Admins always can. So can anyone an admin checks on the dashboard, and so
can a household driver whose Home Assistant person is linked to their user
(the person's ``user_id``). Until any household driver is chosen, nothing
changes: only admins and the people an admin checked can get in.

The same rule covers Ford Assistant's own switches, numbers, times and
selects. Automations (no user) are always allowed. The Ford integration's
own entities keep Home Assistant's usual permissions. Home Assistant can't
show a sidebar entry to only some people, so once anyone who isn't an admin
is allowed, everyone sees Ford Assistant in the sidebar and the page tells
the others they don't have access.
"""

from __future__ import annotations

from typing import Any

from homeassistant.auth.models import User
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1
DATA_ACCESS = f"{DOMAIN}_access"


class Access:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.access"
        )
        self.user_ids: list[str] = []

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.user_ids = [str(uid) for uid in data.get("user_ids", [])]

    async def async_set(self, user_ids: list[str]) -> None:
        known = {user.id for user in await self.hass.auth.async_get_users()}
        self.user_ids = sorted({uid for uid in user_ids if uid in known})
        await self._store.async_save({"user_ids": self.user_ids})

    def allows(self, user: User | None) -> bool:
        if user is None or not user.is_active:
            return False
        if user.is_admin or user.id in self.user_ids:
            return True
        return user.id in household_driver_user_ids(self.hass)

    async def async_allows_context(self, context: Context | None) -> bool:
        if context is None or context.user_id is None:
            return True  # Home Assistant itself: automations, scripts, startup.
        return self.allows(await self.hass.auth.async_get_user(context.user_id))

    async def async_listed_non_admins(self) -> bool:
        """Is anyone who isn't an admin allowed in? Then the sidebar shows to everyone."""
        ids = set(self.user_ids) | household_driver_user_ids(self.hass)
        for uid in ids:
            user = await self.hass.auth.async_get_user(uid)
            if user and user.is_active and not user.is_admin:
                return True
        return False


def household_driver_user_ids(hass: HomeAssistant) -> set[str]:
    """Users linked to an eligible household driver on any loaded vehicle.

    A person grants access only when Home Assistant has linked them to a
    user (Settings → People). A person with no user can still be the active
    driver; they just can't open the dashboard.
    """
    found: set[str] = set()
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        controller = entry.runtime_data
        for person_id in getattr(controller, "household_drivers", ()):
            state = hass.states.get(person_id)
            if state is None:
                continue
            user_id = state.attributes.get("user_id")
            if user_id:
                found.add(str(user_id))
    return found


async def async_get_access(hass: HomeAssistant) -> Access:
    if (access := hass.data.get(DATA_ACCESS)) is None:
        access = hass.data[DATA_ACCESS] = Access(hass)
        await access.async_load()
    return access


def get_access(hass: HomeAssistant) -> Access:
    return hass.data[DATA_ACCESS]


async def async_ensure_allowed(hass: HomeAssistant, context: Context | None) -> None:
    """Raise if the person behind this action may not use Ford Assistant."""
    if await (await async_get_access(hass)).async_allows_context(context):
        return
    raise HomeAssistantError(translation_domain=DOMAIN, translation_key="not_allowed")
