"""Who's driving: modes, profiles, and what each mode may do.

Household is everyday driving. Service is a workshop visit (an oil change,
body shop, or dealer). Guest is anyone else who has the vehicle: borrowed,
lent, or a driver who isn't one of the household people.

The feature switches stay as they are. A mode only filters them, so leaving
Service or Guest mode puts every switch back to what it was.

Active driver
-------------
While the mode is Household, Ford Assistant tries to name who left with the
vehicle. See ``matching_drivers`` for the rule. In short:

* A choice made on the Active driver select (or the set-mode action) is kept
  until the vehicle is parked at home. Parking clears it, so the next trip
  can be detected again.
* The vehicle has departed when its tracker leaves the home zone, or the
  ignition turns on while it is already outside that zone.
* A household person counts when they, or one of their device trackers,
  change from ``home`` to anywhere else within 15 minutes of that departure.
* Exactly one such person becomes the active driver. None, or more than one,
  and the current choice is left alone: Ford Assistant does not guess.
* A person who was already away when Ford Assistant started is not assumed
  to be the driver. Only a leave-home it sees counts.
* The driver is cleared when the vehicle is in the home zone with the
  ignition off, except during the first 3 minutes after startup (the same
  wait the garage uses), so a restart doesn't forget a trip already underway.

Phone as a key
--------------
The Ford integration does not publish a phone-as-a-key entity. Its entity
keys were checked; the nearest sensor (``deviceConnectivity``) only says
whether the vehicle's modem is online. There is nothing in that data that
names a person or a phone, so Ford Assistant does not pretend to read it.
The phone signal it does use is the device tracker already attached to a
Home Assistant person, which is how Home Assistant knows that person's phone.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol

from .const import (
    CONF_DRIVER_PROFILES,
    CONF_GUEST_ACTIONS,
    CONF_GUEST_ALERTS,
    CONF_GUEST_REVERT,
    CONF_SERVICE_ACTIONS,
    CONF_SERVICE_ALERTS,
    CONF_SERVICE_REVERT,
)

MODE_HOUSEHOLD = "household"
MODE_SERVICE = "service"
MODE_GUEST = "guest"
DRIVING_MODES: tuple[str, ...] = (MODE_HOUSEHOLD, MODE_SERVICE, MODE_GUEST)

# Select option meaning nobody is the active driver.
ACTIVE_DRIVER_NONE = "none"

# How close a person's leaving home must be to the vehicle's departure.
DRIVER_MATCH_WINDOW = 15 * 60  # seconds

# Alerts Ford Assistant can send. The keep-list is a subset of these.
ALERTS: tuple[str, ...] = (
    "alarm",
    "windows",
    "fuel",
    "oil",
    "battery",
    "tires",
    "indicators",
    "door",
    "plugin",
    "charge",
    "status_activity",
    "remote_start_activity",
)

# Actions Ford Assistant can take. Anything not allowed is blocked.
# ``lock`` / ``unlock`` / ``honk`` / ``remote_start`` are the commands (the
# dashboard, notification buttons, and Live Activity buttons). The others
# are the automations that run on their own.
ACTIONS: tuple[str, ...] = (
    "auto_lock",
    "night_lock",
    "lock",
    "unlock",
    "honk",
    "precondition",
    "work_prompt",
    "auto_extend",
    "remote_start",
    "garage_open",
    "garage_close",
)

# An automation runs only when every action it needs is allowed. The command
# is listed separately so a profile can stop remote start while still locking.
FEATURE_REQUIRES: dict[str, tuple[str, ...]] = {
    "auto_lock": ("auto_lock", "lock"),
    "night_lock": ("night_lock", "lock"),
    "precondition": ("precondition", "remote_start"),
    "work_prompt": ("work_prompt", "remote_start"),
    "auto_extend": ("auto_extend", "remote_start"),
    "garage_open": ("garage_open",),
    "garage_close": ("garage_close",),
}

# Notification kinds that are alerts. Kinds that only report an action Ford
# Assistant already took (autolock, start, garage, …) are not listed: the
# action itself was already allowed or blocked.
ALERT_OF_KIND: dict[str, str] = {
    "alarm": "alarm",
    "windows": "windows",
    "fuel": "fuel",
    "oil": "oil",
    "battery": "battery",
    "tires": "tires",
    "indicators": "indicators",
    "door": "door",
    "plugin": "plugin",
    "charge": "charge",
}

COMMAND_ACTIONS: dict[str, str] = {
    "LOCK": "lock",
    "HONK": "honk",
    "START": "remote_start",
    "STOP": "remote_start",
    "EXTEND": "remote_start",
}

# Service: the car is in someone else's hands. Keep every alert so a flat
# battery or an alarm still gets through. Block every action, including
# auto-lock, remote start, and the garage. Status refresh is not an action
# and always runs.
SERVICE_DEFAULT_ALERTS: tuple[str, ...] = ALERTS
SERVICE_DEFAULT_ACTIONS: tuple[str, ...] = ()

# Guest: someone else is driving, and the owner's routines should stay out
# of their way. Keep the alerts that protect the car and the trip (alarm,
# doors, windows, fuel, 12V battery, tires, warning lights, charging) and
# the status Live Activity so the owner can still see it. Skip the owner's
# maintenance (oil-life to-do, plug-in reminder) and the remote-start
# countdown. Allow locking, unlocking, honk, and auto-lock so the borrowed
# car isn't left unlocked. Block the owner's schedule (night lock, morning
# start, leaving-work prompt, auto-extend), remote start, and the garage.
GUEST_DEFAULT_ALERTS: tuple[str, ...] = (
    "alarm",
    "windows",
    "fuel",
    "battery",
    "tires",
    "indicators",
    "door",
    "charge",
    "status_activity",
)
GUEST_DEFAULT_ACTIONS: tuple[str, ...] = ("auto_lock", "lock", "unlock", "honk")


@dataclass(frozen=True, slots=True)
class DrivingPolicy:
    """What the current mode actually allows."""

    mode: str
    active_driver: str | None
    allowed_actions: tuple[str, ...]
    blocked_actions: tuple[str, ...]
    kept_alerts: tuple[str, ...]
    dropped_alerts: tuple[str, ...]
    # Phones that should get alerts, or None to use the vehicle's phones.
    notify_devices: tuple[str, ...] | None

    @property
    def automations_suppressed(self) -> bool:
        """True when at least one action Ford Assistant can take is blocked."""
        return bool(self.blocked_actions)

    def allows(self, action: str) -> bool:
        """Whether this action may run."""
        return action in self.allowed_actions

    def keeps(self, alert: str) -> bool:
        """Whether this alert may be sent."""
        return alert in self.kept_alerts


def _subset(
    value: Any, universe: Sequence[str], default: Sequence[str]
) -> tuple[str, ...]:
    """Keep known names, in the canonical order.

    A missing value uses ``default``. An empty list is a real choice (keep
    nothing, or allow nothing) and is preserved.
    """
    chosen = default if value is None or not isinstance(value, list) else value
    picked = {item for item in chosen if isinstance(item, str)}
    return tuple(item for item in universe if item in picked)


def _profile_devices(profile: Mapping[str, Any]) -> tuple[str, ...] | None:
    devices = profile.get("notify_devices")
    if not isinstance(devices, list):
        return None
    cleaned = tuple(str(device) for device in devices if device)
    return cleaned or None


def policy_for(
    mode: str, active_driver: str | None, options: Mapping[str, Any]
) -> DrivingPolicy:
    """The effective allow and keep lists for one vehicle."""
    if mode not in DRIVING_MODES:
        mode = MODE_HOUSEHOLD
    notify: tuple[str, ...] | None = None
    if mode == MODE_HOUSEHOLD:
        kept: Sequence[str] = ALERTS
        allowed: Sequence[str] = ACTIONS
        profiles = options.get(CONF_DRIVER_PROFILES) or {}
        profile = profiles.get(active_driver) if isinstance(profiles, dict) else None
        if active_driver and isinstance(profile, dict):
            # Missing keys mean "everything", matching a driver who was added
            # before a new alert or action existed.
            kept = _subset(profile.get("alerts", list(ALERTS)), ALERTS, ALERTS)
            allowed = _subset(profile.get("actions", list(ACTIONS)), ACTIONS, ACTIONS)
            notify = _profile_devices(profile)
    elif mode == MODE_SERVICE:
        kept = _subset(options.get(CONF_SERVICE_ALERTS), ALERTS, SERVICE_DEFAULT_ALERTS)
        allowed = _subset(
            options.get(CONF_SERVICE_ACTIONS), ACTIONS, SERVICE_DEFAULT_ACTIONS
        )
    else:
        kept = _subset(options.get(CONF_GUEST_ALERTS), ALERTS, GUEST_DEFAULT_ALERTS)
        allowed = _subset(
            options.get(CONF_GUEST_ACTIONS), ACTIONS, GUEST_DEFAULT_ACTIONS
        )
    kept_set = set(kept)
    allowed_set = set(allowed)
    return DrivingPolicy(
        mode=mode,
        active_driver=active_driver if mode == MODE_HOUSEHOLD else None,
        allowed_actions=tuple(action for action in ACTIONS if action in allowed_set),
        blocked_actions=tuple(
            action for action in ACTIONS if action not in allowed_set
        ),
        kept_alerts=tuple(alert for alert in ALERTS if alert in kept_set),
        dropped_alerts=tuple(alert for alert in ALERTS if alert not in kept_set),
        notify_devices=notify,
    )


def configured_revert(options: Mapping[str, Any], mode: str) -> timedelta | None:
    """How long Service or Guest mode waits before returning to Household."""
    key = {MODE_SERVICE: CONF_SERVICE_REVERT, MODE_GUEST: CONF_GUEST_REVERT}.get(mode)
    if key is None:
        return None
    return coerce_duration(options.get(key))


def matching_drivers(
    drivers: Sequence[str],
    left_home: Mapping[str, datetime],
    trackers_by_person: Mapping[str, Sequence[str]],
    departure: datetime,
    now: datetime,
    window: float = DRIVER_MATCH_WINDOW,
) -> list[str]:
    """Household people who left home within ``window`` seconds of ``departure``.

    ``left_home`` maps a person or device-tracker entity id to the moment it
    changed from ``home`` to anything else. A person matches when that moment,
    or the same moment for one of their device trackers, is within ``window``
    of the vehicle leaving. The departure itself has to be recent too, so a
    trip from much earlier can't still match.
    """
    if (now - departure).total_seconds() > window or (
        departure - now
    ).total_seconds() > window:
        return []
    found: list[str] = []
    for person in drivers:
        stamps = [left_home.get(person)]
        for tracker in trackers_by_person.get(person, ()):
            stamps.append(left_home.get(tracker))
        if any(
            stamp is not None and abs((stamp - departure).total_seconds()) <= window
            for stamp in stamps
        ):
            found.append(person)
    return found


def parse_duration(value: Any) -> timedelta:
    """Parse a duration from a service call or a saved options value.

    Accepts a timedelta, a number of seconds, ``HH:MM`` or ``HH:MM:SS``, and
    the mapping the duration selector stores (``days``, ``hours``, ``minutes``,
    ``seconds``, ``milliseconds``). Zero is allowed and means "don't revert".
    """
    if isinstance(value, timedelta):
        if value.total_seconds() < 0:
            raise ValueError("duration must not be negative")
        return value
    if isinstance(value, bool) or value is None:
        raise ValueError("expected a duration")
    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError("duration must not be negative")
        return timedelta(seconds=float(value))
    if isinstance(value, str):
        parts = value.split(":")
        if len(parts) not in (2, 3):
            raise ValueError("expected HH:MM:SS")
        try:
            numbers = [float(part) for part in parts]
        except ValueError as err:
            raise ValueError("expected HH:MM:SS") from err
        if any(number < 0 for number in numbers):
            raise ValueError("duration must not be negative")
        if len(numbers) == 2:
            return timedelta(hours=numbers[0], minutes=numbers[1])
        return timedelta(hours=numbers[0], minutes=numbers[1], seconds=numbers[2])
    if isinstance(value, Mapping):
        allowed = {"days", "hours", "minutes", "seconds", "milliseconds"}
        if not set(value).issubset(allowed) or not value:
            raise ValueError("expected a duration")
        try:
            parsed = timedelta(**{key: float(value[key]) for key in value})
        except (TypeError, ValueError) as err:
            raise ValueError("expected a duration") from err
        if parsed.total_seconds() < 0:
            raise ValueError("duration must not be negative")
        return parsed
    raise ValueError("expected a duration")


def duration_config(value: Any) -> timedelta:
    """Voluptuous wrapper around :func:`parse_duration`."""
    try:
        return parse_duration(value)
    except ValueError as err:
        raise vol.Invalid(str(err)) from err


def coerce_duration(value: Any) -> timedelta | None:
    """A saved duration, or None when it's missing, zero, or unreadable.

    Used for options. A bad saved value must not stop the integration loading.
    """
    if value is None or value is False or value == "":
        return None
    try:
        parsed = parse_duration(value)
    except ValueError:
        return None
    if parsed.total_seconds() <= 0:
        return None
    return parsed
