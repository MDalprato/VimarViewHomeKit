"""Run inside the HA container; no physical monitor is contacted."""
import asyncio
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import urllib.request
import urllib.error

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from custom_components.vimar_local import button, camera, event, sensor
from custom_components.vimar_local.bridge import VideoBridge


def entry():
    return SimpleNamespace(entry_id="test_vimar", runtime_data=SimpleNamespace(monitor_device_id="monitor-test-id"), data={"host": "192.168.1.100", "password": "test-only", "stations": 2, "outputs_per_station": 2})


class Entities(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.tmp.name)
        self.entry = entry()

    async def asyncTearDown(self):
        await self.hass.async_stop()
        self.tmp.cleanup()

    async def test_topology_and_unique_ids(self):
        entities = []
        for platform in (sensor, button, event, camera):
            await platform.async_setup_entry(self.hass, self.entry, entities.extend)
        self.assertEqual(len(entities), 11)
        self.assertEqual(len({e.unique_id for e in entities}), 11)
        for entity in entities[1:]:
            self.assertNotIn("via_device", entity.device_info)
            self.assertEqual(entity.device_info["via_device_id"], "monitor-test-id")
        self.assertEqual(entities[0].native_value, 2)
        self.assertEqual(sum(isinstance(e, button.Relay) for e in entities), 4)

    async def test_simulation_routes_to_correct_station(self):
        bells = [event.Doorbell(self.entry, i) for i in (1, 2)]
        for bell in bells:
            bell.hass = self.hass
            bell.entity_id = f"event.test_{bell.station}"
            bell.async_write_ha_state = Mock()
            bell.async_on_remove = Mock()
            await bell.async_added_to_hass()
        test_button = button.SimulatePress(self.entry, 2)
        test_button.hass = self.hass
        await test_button.async_press()
        await asyncio.sleep(0)
        bells[0].async_write_ha_state.assert_not_called()
        bells[1].async_write_ha_state.assert_called_once()
        self.assertEqual(bells[1].state_attributes["event_type"], "ring")
        first = bells[1].state
        await test_button.async_press()
        await asyncio.sleep(0)
        self.assertEqual(bells[1].async_write_ha_state.call_count, 2)
        self.assertNotEqual(bells[1].state, first)
        for bell in bells:
            bell.async_on_remove.call_args.args[0]()

    async def test_relay_single_attempt_on_timeout(self):
        relay = button.Relay(self.entry, 2, 1)
        relay.hass = self.hass
        client = Mock(secret="test-hash")
        client.send.return_value.findtext.return_value = "AAAAAA=="
        with patch.object(button, "Client", return_value=client), patch.object(button, "open_relay", side_effect=TimeoutError) as command:
            with self.assertRaises(HomeAssistantError):
                await relay.async_press()
        command.assert_called_once_with(client, 2, 1, "test-only", "AAAAAA==")

    async def test_snapshot_reuses_camera_stream(self):
        cam = camera.VimarCamera(self.entry, 1)
        stream = Mock(async_get_image=AsyncMock(return_value=b"jpeg"))
        cam.async_create_stream = AsyncMock(return_value=stream)
        self.assertEqual(await cam.async_camera_image(320, 180), b"jpeg")
        stream.async_get_image.assert_awaited_once_with(width=320, height=180)

    async def test_separate_unlock_password(self):
        self.entry.data["unlock_password"] = "different-test-pin"
        relay = button.Relay(self.entry, 1, 2)
        client = Mock(secret="test-hash")
        client.send.return_value.findtext.return_value = "AAAAAA=="
        with patch.object(button, "Client", return_value=client), patch.object(button, "open_relay") as command:
            relay._open()
        command.assert_called_once_with(client, 1, 2, "different-test-pin", "AAAAAA==")


class Video(unittest.TestCase):
    def test_bridge_token_and_decodable_h264(self):
        # Synthetic video validates HTTP transport + FFmpeg without network to Vimar.
        source = subprocess.check_output(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=480x280:r=10", "-t", "1", "-c:v", "libx264", "-f", "h264", "pipe:1"])
        bridge = VideoBridge(entry().data)
        with patch("custom_components.vimar_local.bridge.Client"), patch("custom_components.vimar_local.bridge.video_packets", side_effect=lambda *args: (packet for packet in [source])):
            bridge.start()
            try:
                url = bridge.source(2)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(url.replace(bridge.token, "wrong"))
                self.assertEqual(error.exception.code, 404)
                error.exception.close()
                received = urllib.request.urlopen(url, timeout=5).read()
                jpeg = subprocess.check_output(["ffmpeg", "-v", "error", "-f", "mpegts", "-i", "pipe:0", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"], input=received)
                self.assertTrue(jpeg.startswith(b"\xff\xd8"))
                import io
                import av
                with av.open(io.BytesIO(received), format="mpegts") as container:
                    packets = [p for p in container.demux(video=0) if p.size]
                    self.assertGreater(len(packets), 5)
                    self.assertTrue(all(p.dts is not None and p.pts is not None for p in packets))
                    self.assertEqual([p.dts for p in packets], sorted({p.dts for p in packets}))
            finally:
                bridge.stop()
            self.assertFalse(bridge.thread.is_alive())

    def test_second_consumer_is_rejected(self):
        bridge = VideoBridge(entry().data)
        bridge.start()
        try:
            bridge.locks[1].acquire()
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(bridge.source(1))
            self.assertEqual(error.exception.code, 503)
            error.exception.close()
        finally:
            bridge.locks[1].release()
            bridge.stop()


if __name__ == "__main__":
    unittest.main()
