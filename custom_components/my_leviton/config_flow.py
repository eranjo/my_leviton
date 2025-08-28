"""Config flow for My Leviton (USB) integration."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant

DOMAIN = "my_leviton"

class MyLevitonConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for My Leviton (USB)."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if user_input is not None:
            return self.async_create_entry(title="My Leviton (USB)", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

async def async_setup_entry(hass: HomeAssistant, entry):
    """Set up My Leviton from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_unload_entry(hass: HomeAssistant, entry):
    """Unload a config entry."""
    hass.data.pop(DOMAIN, None)
    return True
