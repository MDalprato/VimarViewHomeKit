"""Configure the known topology; the monitor does not discover stations."""
import ipaddress
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from .const import DOMAIN

SCHEMA = vol.Schema({
    vol.Required("host"): str,
    vol.Required("password"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
    vol.Required("stations", default=2): vol.All(vol.Coerce(int), vol.Range(min=1, max=2)),
    vol.Required("outputs_per_station", default=2): vol.All(vol.Coerce(int), vol.Range(min=1, max=2)),
    vol.Optional("unlock_password", default=""): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
})

class VimarConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                address = ipaddress.IPv4Address(user_input["host"].strip())
                if not address.is_private or address.is_loopback or address.is_unspecified:
                    raise ValueError
                if not user_input["password"]:
                    raise ValueError
                user_input["host"] = str(address)
            except ValueError:
                errors["base"] = "invalid_input"
            else:
                await self.async_set_unique_id(user_input["host"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f"Vimar {user_input['host']}", data=user_input)
        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)
