"""Stable monitor and station identities."""
from homeassistant.helpers.entity import Entity
from .const import DOMAIN

class VimarEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry, key, station=None):
        self.entry = entry
        self.station = station
        monitor = entry.entry_id
        device = monitor if station is None else f"{monitor}_station_{station}"
        self._attr_unique_id = f"{device}_{key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device)},
            "name": f"Vimar {entry.data['host']}" if station is None else f"Vimar targa {station}",
            "manufacturer": "Vimar",
            "model": "View Door (sperimentale)",
        }
        if station is not None:
            self._attr_device_info["via_device_id"] = entry.runtime_data.monitor_device_id
