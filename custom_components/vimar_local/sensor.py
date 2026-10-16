"""Configured station count, not autodiscovery."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory
from .entity import VimarEntity

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([StationCount(entry)])

class StationCount(VimarEntity, SensorEntity):
    _attr_name = "Numero targhe configurate"
    _attr_icon = "mdi:door"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry):
        super().__init__(entry, "station_count")
        self._attr_native_value = entry.data["stations"]
        self._attr_extra_state_attributes = {"source": "configuration", "discovered": False}
