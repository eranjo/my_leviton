"""Config flow for My Leviton (USB) integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries

from .const import DOMAIN, UNIQUE_ID

# Stable USB controller id used for the single config entry.
CONFIG_UNIQUE_ID = UNIQUE_ID


class MyLevitonConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for My Leviton (USB)."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        # One USB controller → one config entry. Re-adding after a HACS
        # update was creating a second entry and a second set of entities.
        await self.async_set_unique_id(CONFIG_UNIQUE_ID)
        self._abort_if_unique_id_configured()

        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="My Leviton (USB)", data={})

        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))
