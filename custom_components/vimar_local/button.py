"""Momentary relay commands and a separate test button."""
from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity import EntityCategory
from .entity import VimarEntity
from .event import signal
from .protocol.vimar_watch import Client, ProtocolError
from .protocol.vimar_relay import request, open_relay

async def async_setup_entry(hass, entry, async_add_entities):
    entities = []
    for station in range(1, entry.data["stations"] + 1):
        entities.append(SimulatePress(entry, station))
        entities.extend(Relay(entry, station, output) for output in range(1, entry.data["outputs_per_station"] + 1))
    async_add_entities(entities)

class SimulatePress(VimarEntity, ButtonEntity):
    _attr_name = "Simula pressione campanello"
    _attr_icon = "mdi:bell-ring"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry, station):
        super().__init__(entry, "simulate_press", station)

    async def async_press(self):
        async_dispatcher_send(self.hass, signal(self.entry, self.station))

class Relay(VimarEntity, ButtonEntity):
    _attr_icon = "mdi:electric-switch"

    def __init__(self, entry, station, output):
        super().__init__(entry, f"output_{output}", station)
        self.output = output
        self._attr_name = f"Aziona uscita {output}"
        self._attr_extra_state_attributes = {"station": station, "output": output, "physical_state_known": False, "experimental": True}

    def _open(self):
        data = self.entry.data
        client = Client(data["host"], data["password"])
        status = client.send(request(client.secret, "get.device.status"))
        open_relay(client, self.station, self.output, data.get("unlock_password") or data["password"], status.findtext(".//devability", ""))

    async def async_press(self):
        try:
            await self.hass.async_add_executor_job(self._open)
        except Exception as exc:
            # Only expose a whitelisted device code or exception class, never XML/secrets.
            import re
            code = re.fullmatch(r"Device error ([0-9]{1,6})", str(exc)) if isinstance(exc, ProtocolError) else None
            detail = f"errore dispositivo {code[1]}" if code else type(exc).__name__
            raise HomeAssistantError(
                f"Comando non confermato ({detail}); nessun retry. Verificare fisicamente l'uscita."
            ) from None
