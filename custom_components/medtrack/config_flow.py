"""Config flow, options flow and medication subentry flow for MedTrack."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import time
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util
import probatio

from .const import (
    CONF_ACTIVE,
    CONF_CRITICAL_MODE,
    CONF_CRITICAL_VOLUME,
    CONF_CURRENT_STOCK,
    CONF_DAYS,
    CONF_DOSE_AMOUNT,
    CONF_EARLY_TAKE_WINDOW,
    CONF_HISTORY_RETENTION,
    CONF_INSTRUCTIONS,
    CONF_INTERRUPTION_LEVEL,
    CONF_LOW_STOCK_THRESHOLD,
    CONF_MAX_REMINDERS,
    CONF_MAX_SNOOZES,
    CONF_NOTIFY_TARGETS,
    CONF_REFILL_AMOUNT,
    CONF_REQUIRE_UNLOCK,
    CONF_REREMIND_INTERVAL,
    CONF_SNOOZE_LENGTH,
    CONF_TAP_PATH,
    CONF_TIMES,
    CONF_UNIT,
    DEFAULT_CRITICAL_VOLUME,
    DEFAULT_EARLY_TAKE_WINDOW,
    DEFAULT_HISTORY_RETENTION,
    DEFAULT_INTERRUPTION_LEVEL,
    DEFAULT_MAX_REMINDERS,
    DEFAULT_MAX_SNOOZES,
    DEFAULT_NAME,
    DEFAULT_REQUIRE_UNLOCK,
    DEFAULT_REREMIND_INTERVAL,
    DEFAULT_SNOOZE_LENGTH,
    DEFAULT_UNIT,
    DOMAIN,
    OVERRIDE_KEYS,
    RANGE_CRITICAL_VOLUME,
    RANGE_EARLY_TAKE_WINDOW,
    RANGE_HISTORY_RETENTION,
    RANGE_MAX_REMINDERS,
    RANGE_MAX_SNOOZES,
    RANGE_REREMIND_INTERVAL,
    RANGE_SNOOZE_LENGTH,
    SECTION_OVERRIDES,
    SUBENTRY_MEDICATION,
    WEEKDAYS,
    CriticalMode,
    InterruptionLevel,
)
from .targets import available_notify_targets, notify_target_labels

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@callback
def _target_options(hass: HomeAssistant, targets: list[str]) -> list[SelectOptionDict]:
    """Selector options: stored value is the service name, label the phone name."""
    labels = notify_target_labels(hass)
    options = [
        SelectOptionDict(value=target, label=labels.get(target, target))
        for target in targets
    ]
    return sorted(options, key=lambda option: option["label"].casefold())


def _int_selector(bounds: tuple[int, int], unit: str | None = None) -> Any:
    """Return a number box that yields an int within bounds."""
    config = NumberSelectorConfig(
        min=bounds[0], max=bounds[1], step=1, mode=NumberSelectorMode.BOX
    )
    if unit is not None:  # the selector rejects an explicit None
        config["unit_of_measurement"] = unit
    return probatio.All(NumberSelector(config), probatio.Coerce(int))


def _float_selector(
    minimum: float = 0, maximum: float | None = None, step: float | str = "any"
) -> NumberSelector:
    """Return a number box that yields a float."""
    config = NumberSelectorConfig(min=minimum, step=step, mode=NumberSelectorMode.BOX)
    if maximum is not None:
        config["max"] = maximum
    return NumberSelector(config)


class OptionalNumberSelector(NumberSelector):
    """A number box that may be left empty.

    The frontend can send an emptied number box as null or "" instead of
    leaving the field out; the plain NumberSelector rejects both, which made
    optional fields such as "Default refill amount" effectively required.
    Empty becomes None, and the flow treats None as "not set".
    """

    def __init__(self, config: NumberSelectorConfig, *, integer: bool = False) -> None:
        """Create; `integer` returns whole numbers."""
        super().__init__(config)
        self._integer = integer

    def __call__(self, data: Any) -> float | int | None:
        """Validate, allowing an empty value."""
        if data is None or (isinstance(data, str) and not data.strip()):
            return None
        value = super().__call__(data)
        return int(value) if self._integer else value


def _optional_float() -> OptionalNumberSelector:
    return OptionalNumberSelector(
        NumberSelectorConfig(min=0, step="any", mode=NumberSelectorMode.BOX)
    )


def _optional_int(bounds: tuple[int, int], unit: str | None = None) -> Any:
    config = NumberSelectorConfig(
        min=bounds[0], max=bounds[1], step=1, mode=NumberSelectorMode.BOX
    )
    if unit is not None:
        config["unit_of_measurement"] = unit
    return OptionalNumberSelector(config, integer=True)


def hub_settings_schema(targets: list[SelectOptionDict]) -> probatio.Schema:
    """Schema for the hub settings shared by the config and options flows."""
    return probatio.Schema(
        {
            probatio.Required(CONF_NOTIFY_TARGETS): SelectSelector(
                SelectSelectorConfig(
                    options=targets, multiple=True, mode=SelectSelectorMode.LIST
                )
            ),
            probatio.Required(
                CONF_REREMIND_INTERVAL, default=DEFAULT_REREMIND_INTERVAL
            ): _int_selector(RANGE_REREMIND_INTERVAL, "min"),
            probatio.Required(
                CONF_MAX_REMINDERS, default=DEFAULT_MAX_REMINDERS
            ): _int_selector(RANGE_MAX_REMINDERS),
            probatio.Required(
                CONF_MAX_SNOOZES, default=DEFAULT_MAX_SNOOZES
            ): _int_selector(RANGE_MAX_SNOOZES),
            probatio.Required(
                CONF_SNOOZE_LENGTH, default=DEFAULT_SNOOZE_LENGTH
            ): _int_selector(RANGE_SNOOZE_LENGTH, "min"),
            probatio.Required(
                CONF_EARLY_TAKE_WINDOW, default=DEFAULT_EARLY_TAKE_WINDOW
            ): _int_selector(RANGE_EARLY_TAKE_WINDOW, "min"),
            probatio.Required(
                CONF_INTERRUPTION_LEVEL, default=DEFAULT_INTERRUPTION_LEVEL.value
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[level.value for level in InterruptionLevel],
                    translation_key=CONF_INTERRUPTION_LEVEL,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            probatio.Required(
                CONF_REQUIRE_UNLOCK, default=DEFAULT_REQUIRE_UNLOCK
            ): BooleanSelector(),
            probatio.Required(
                CONF_CRITICAL_VOLUME, default=DEFAULT_CRITICAL_VOLUME
            ): _float_selector(*RANGE_CRITICAL_VOLUME, step=0.1),
            probatio.Required(
                CONF_HISTORY_RETENTION, default=DEFAULT_HISTORY_RETENTION
            ): _int_selector(RANGE_HISTORY_RETENTION, "d"),
            probatio.Optional(CONF_TAP_PATH): TextSelector(),
        }
    )


def _validate_hub(
    user_input: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate hub settings beyond what the selectors check.

    Returns (options to store, errors). An empty tap page is not stored.
    """
    errors: dict[str, str] = {}
    options = dict(user_input)
    if not options.get(CONF_NOTIFY_TARGETS):
        errors[CONF_NOTIFY_TARGETS] = "no_targets"
    tap_path = str(options.pop(CONF_TAP_PATH, None) or "").strip()
    if tap_path:
        if tap_path.startswith(("/", "http://", "https://")):
            options[CONF_TAP_PATH] = tap_path
        else:
            errors[CONF_TAP_PATH] = "invalid_tap_path"
    return options, errors


# ---------------------------------------------------------------------------
# Hub config flow
# ---------------------------------------------------------------------------


class MedTrackConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a MedTrack hub."""

    VERSION = 1
    MINOR_VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MedTrackOptionsFlow:
        """Return the options flow."""
        return MedTrackOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentry types this integration supports."""
        return {SUBENTRY_MEDICATION: MedicationSubentryFlow}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the hub name and global settings."""
        targets = available_notify_targets(self.hass)
        if not targets:
            return self.async_abort(reason="no_notify_targets")

        errors: dict[str, str] = {}
        if user_input is not None:
            options, errors = _validate_hub(user_input)
            if not errors:
                name = options.pop(CONF_NAME).strip() or DEFAULT_NAME
                return self.async_create_entry(title=name, data={}, options=options)

        schema = probatio.Schema(
            {probatio.Required(CONF_NAME, default=DEFAULT_NAME): TextSelector()}
        ).extend(hub_settings_schema(_target_options(self.hass, targets)).schema)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )


class MedTrackOptionsFlow(OptionsFlow):
    """Change the hub settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the hub settings form."""
        errors: dict[str, str] = {}
        if user_input is not None:
            options, errors = _validate_hub(user_input)
            if not errors:
                return self.async_create_entry(data=options)

        current = self.config_entry.options
        # Keep already configured targets selectable even if the phone is
        # currently missing, so the form does not reject the saved value.
        targets = _target_options(
            self.hass,
            sorted(
                set(available_notify_targets(self.hass))
                | set(current.get(CONF_NOTIFY_TARGETS, []))
            ),
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                hub_settings_schema(targets), user_input or current
            ),
            errors=errors,
        )


# ---------------------------------------------------------------------------
# Medication subentry flow
# ---------------------------------------------------------------------------


def medication_schema() -> probatio.Schema:
    """Schema for the medication add/reconfigure form."""
    return probatio.Schema(
        {
            probatio.Required(CONF_NAME): TextSelector(),
            probatio.Required(CONF_DOSE_AMOUNT, default=1): _float_selector(),
            probatio.Required(CONF_UNIT, default=DEFAULT_UNIT): TextSelector(),
            probatio.Required(CONF_TIMES, default=["08:00"]): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TIME, multiple=True)
            ),
            probatio.Required(CONF_DAYS, default=list(WEEKDAYS)): SelectSelector(
                SelectSelectorConfig(
                    options=list(WEEKDAYS),
                    multiple=True,
                    translation_key="weekday",
                    mode=SelectSelectorMode.LIST,
                )
            ),
            probatio.Optional(CONF_INSTRUCTIONS): TextSelector(),
            # Both optional: no stock = no stock tracking for this medication,
            # no threshold = track stock but never alert.
            probatio.Optional(CONF_CURRENT_STOCK): _optional_float(),
            probatio.Optional(CONF_LOW_STOCK_THRESHOLD): _optional_float(),
            probatio.Optional(CONF_REFILL_AMOUNT): _optional_float(),
            probatio.Required(
                CONF_CRITICAL_MODE, default=CriticalMode.NEVER.value
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[mode.value for mode in CriticalMode],
                    translation_key=CONF_CRITICAL_MODE,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            probatio.Optional(SECTION_OVERRIDES): section(
                probatio.Schema(
                    {
                        probatio.Optional(CONF_REREMIND_INTERVAL): _optional_int(
                            RANGE_REREMIND_INTERVAL, "min"
                        ),
                        probatio.Optional(CONF_MAX_REMINDERS): _optional_int(
                            RANGE_MAX_REMINDERS
                        ),
                    }
                ),
                {"collapsed": True},
            ),
            probatio.Required(CONF_ACTIVE, default=True): BooleanSelector(),
        }
    )


def parse_time(value: str) -> str | None:
    """Normalise 'H:MM', 'HH:MM' or 'HH:MM:SS' to 'HH:MM'; None if invalid."""
    parsed: time | None = dt_util.parse_time(value.strip()) if value else None
    if parsed is None or parsed.second or parsed.microsecond:
        return None
    return f"{parsed.hour:02d}:{parsed.minute:02d}"


def validate_medication(
    user_input: Mapping[str, Any], other_names: set[str]
) -> tuple[str, dict[str, Any], dict[str, str]]:
    """Validate the medication form.

    Returns (title, data, errors). `other_names` holds the casefolded names of
    the other medications in the same hub.
    """
    errors: dict[str, str] = {}

    name = str(user_input.get(CONF_NAME, "")).strip()
    if not name:
        errors[CONF_NAME] = "name_required"
    elif name.casefold() in other_names:
        errors[CONF_NAME] = "name_exists"

    if float(user_input.get(CONF_DOSE_AMOUNT, 0)) <= 0:
        errors[CONF_DOSE_AMOUNT] = "invalid_dose"

    if not str(user_input.get(CONF_UNIT, "")).strip():
        errors[CONF_UNIT] = "unit_required"

    raw_times = user_input.get(CONF_TIMES) or []
    times = [parse_time(str(value)) for value in raw_times]
    if not times:
        errors[CONF_TIMES] = "no_times"
    elif None in times:
        errors[CONF_TIMES] = "invalid_time"
    elif len(set(times)) != len(times):
        errors[CONF_TIMES] = "duplicate_time"

    days = [day for day in WEEKDAYS if day in (user_input.get(CONF_DAYS) or [])]
    if not days:
        errors[CONF_DAYS] = "no_days"

    refill = user_input.get(CONF_REFILL_AMOUNT)
    if refill is not None and float(refill) <= 0:
        errors[CONF_REFILL_AMOUNT] = "invalid_refill"

    data: dict[str, Any] = {
        CONF_DOSE_AMOUNT: float(user_input.get(CONF_DOSE_AMOUNT, 0)),
        CONF_UNIT: str(user_input.get(CONF_UNIT, "")).strip(),
        CONF_TIMES: sorted(t for t in times if t is not None),
        CONF_DAYS: days,
        CONF_CRITICAL_MODE: user_input.get(CONF_CRITICAL_MODE, CriticalMode.NEVER),
        CONF_ACTIVE: bool(user_input.get(CONF_ACTIVE, True)),
    }
    if instructions := str(user_input.get(CONF_INSTRUCTIONS) or "").strip():
        data[CONF_INSTRUCTIONS] = instructions
    if refill is not None:
        data[CONF_REFILL_AMOUNT] = float(refill)
    for key in (CONF_CURRENT_STOCK, CONF_LOW_STOCK_THRESHOLD):
        if user_input.get(key) is not None:
            data[key] = float(user_input[key])
    # Overrides are entered in a collapsible section but stored flat.
    for key, value in (user_input.get(SECTION_OVERRIDES) or {}).items():
        if value is not None:
            data[key] = int(value)

    return name, data, errors


def medication_form_values(
    subentry: ConfigSubentry, live_stock: float | None
) -> dict[str, Any]:
    """Turn stored subentry data back into form values (for reconfigure).

    The stock field shows the live stock (after doses taken), not the value
    typed in when the medication was added. Changing it is a stock correction.
    """
    values = dict(subentry.data)
    values[CONF_NAME] = subentry.title
    if live_stock is not None:
        values[CONF_CURRENT_STOCK] = live_stock
    values[SECTION_OVERRIDES] = {
        key: values.pop(key) for key in OVERRIDE_KEYS if key in values
    }
    return values


class MedicationSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure a medication."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a medication."""
        return await self._async_medication_form("user", user_input, None)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change an existing medication."""
        return await self._async_medication_form(
            "reconfigure", user_input, self._get_reconfigure_subentry()
        )

    async def _async_medication_form(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        subentry: ConfigSubentry | None,
    ) -> SubentryFlowResult:
        """Show and handle the medication form for add and reconfigure."""
        entry = self._get_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            other_names = {
                other.title.casefold()
                for other in entry.subentries.values()
                if other.subentry_type == SUBENTRY_MEDICATION
                and (subentry is None or other.subentry_id != subentry.subentry_id)
            }
            title, data, errors = validate_medication(user_input, other_names)
            if not errors:
                if subentry is None:
                    return self.async_create_entry(title=title, data=data)
                # The entry's update listener applies the change in place.
                return self.async_update_and_abort(
                    entry, subentry, title=title, data=data
                )

        suggested = (
            user_input
            if user_input is not None
            else (
                medication_form_values(subentry, self._live_stock(entry, subentry))
                if subentry
                else None
            )
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                medication_schema(), suggested
            ),
            errors=errors,
        )

    @staticmethod
    def _live_stock(entry: ConfigEntry, subentry: ConfigSubentry) -> float | None:
        """Current stock from the running MedTrack, if loaded and tracked."""
        if entry.state is not ConfigEntryState.LOADED:
            return None
        manager = entry.runtime_data
        if subentry.subentry_id not in manager.medications:
            return None
        return manager.stock(subentry.subentry_id)
