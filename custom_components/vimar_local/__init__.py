"""Experimental local Vimar integration."""
from dataclasses import dataclass
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.helpers import device_registry as dr
from .const import DOMAIN, PLATFORMS
from .bridge import VideoBridge

@dataclass
class Runtime:
    bridge: VideoBridge
    monitor_device_id: str

async def async_setup_entry(hass, entry):
    bridge = VideoBridge(entry.data)
    await hass.async_add_executor_job(bridge.start)
    monitor = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)},
        name=f"Vimar {entry.data["host"]}", manufacturer="Vimar",
        model="View Door (sperimentale)",
    )
    entry.runtime_data = Runtime(bridge, monitor.id)
    async def stop(_event):
        await hass.async_add_executor_job(bridge.stop)
    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop))
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await hass.async_add_executor_job(bridge.stop)
        raise
    return True

async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await hass.async_add_executor_job(entry.runtime_data.bridge.stop)
        return True
    return False
