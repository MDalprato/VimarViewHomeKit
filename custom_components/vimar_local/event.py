"""Explicitly simulated doorbell presses, ready for a future real source."""
from homeassistant.components.event import EventEntity, EventDeviceClass
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .entity import VimarEntity
from .const import DOMAIN

def signal(entry, station):
    return f"{DOMAIN}_{entry.entry_id}_{station}_press"

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([Doorbell(entry, i) for i in range(1, entry.data["stations"] + 1)])

class Doorbell(VimarEntity, EventEntity):
    _attr_name = "Pressione campanello (simulata)"
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = ["ring"]
    _attr_extra_state_attributes = {"simulated": True, "source": "manual_test"}

    def __init__(self, entry, station):
        super().__init__(entry, "press", station)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        @callback
        def pressed():
            self._trigger_event("ring", {"simulated": True, "source": "manual_test", "station": self.station})
            self.async_write_ha_state()
        self.async_on_remove(async_dispatcher_connect(self.hass, signal(self.entry, self.station), pressed))
