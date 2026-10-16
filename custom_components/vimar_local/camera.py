"""Expose the decrypted local video through HA's stream integration."""
from homeassistant.components.camera import Camera, CameraEntityFeature
from .entity import VimarEntity

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([VimarCamera(entry, i) for i in range(1, entry.data["stations"] + 1)])

class VimarCamera(VimarEntity, Camera):
    _attr_name = "Video"
    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_frontend_stream_type = "hls"

    def __init__(self, entry, station):
        Camera.__init__(self)
        VimarEntity.__init__(self, entry, "video", station)

    async def stream_source(self):
        return self.entry.runtime_data.bridge.source(self.station)

    async def async_camera_image(self, width=None, height=None):
        stream = await self.async_create_stream()
        if stream is None:
            return None
        return await stream.async_get_image(width=width, height=height)
