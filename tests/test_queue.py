import time
import unittest
from types import SimpleNamespace

import server


class FakeMediaStatus:
    def __init__(self):
        self.content_id = "https://example.com/already-playing.mp4"
        self.content_type = "video/mp4"
        self.title = "Already playing"
        self.artist = None
        self.series_title = None
        self.images = []
        self.adjusted_current_time = 10
        self.duration = 60
        self.player_state = "PLAYING"
        self.idle_reason = None
        self.media_session_id = 1


class FakeMediaController:
    def __init__(self):
        self.status = FakeMediaStatus()

    def play_media(self, url, content_type, *, title, autoplay, stream_type):
        self.status.content_id = url
        self.status.content_type = content_type
        self.status.title = title
        self.status.player_state = "PLAYING"
        self.status.idle_reason = None
        self.status.media_session_id += 1

    def block_until_active(self, timeout):
        del timeout

    def play(self):
        self.status.player_state = "PLAYING"

    def pause(self):
        self.status.player_state = "PAUSED"

    def stop(self):
        self.status.player_state = "IDLE"
        self.status.idle_reason = "CANCELLED"

    def seek(self, value):
        self.status.adjusted_current_time = value


class FakeCast:
    def __init__(self):
        self.status = SimpleNamespace(volume_level=0.5, volume_muted=False)
        self.media_controller = FakeMediaController()
        self.socket_client = SimpleNamespace(is_alive=lambda: True, ident=1)
        self.app_display_name = "Default Media Receiver"

    def wait(self, timeout):
        del timeout

    def set_volume(self, value):
        self.status.volume_level = value

    def set_volume_muted(self, value):
        self.status.volume_muted = value


def item(name):
    return {"url": f"https://example.com/{name.lower()}.mp4", "title": name, "contentType": "video/mp4"}


class QueueTests(unittest.TestCase):
    def setUp(self):
        server.DEMO_MODE = False
        self.device = server.CastDevice("test", "Test TV", "Chromecast", "127.0.0.1", 8009, FakeCast())
        self.handler = server.Handler.__new__(server.Handler)

    def action(self, name, payload=None):
        return self.handler.perform_action(self.device, name, payload or {})

    def test_sequence_next_and_stop(self):
        status = self.action("play-sequence", {"items": [item("One"), item("Two"), item("Three")]})
        self.assertEqual("One", status["media"]["title"])
        self.assertEqual(["Two", "Three"], [queued["title"] for queued in status["queue"]])

        status = self.action("queue-next")
        self.assertEqual("Two", status["media"]["title"])
        self.assertEqual(["Three"], [queued["title"] for queued in status["queue"]])

        status = self.action("stop")
        self.assertEqual([], status["queue"])
        self.assertEqual("IDLE", status["playerState"])

    def test_finished_item_advances_only_once(self):
        self.action("play-sequence", {"items": [item("One"), item("Two"), item("Three")]})
        current = self.device.cast.media_controller.status
        current.player_state = "IDLE"
        current.idle_reason = "FINISHED"
        finished = SimpleNamespace(
            player_state="IDLE", idle_reason="FINISHED",
            media_session_id=current.media_session_id, content_id=current.content_id,
        )
        listener = server.DeviceStatusListener(self.device)

        listener.new_media_status(finished)
        deadline = time.time() + 2
        while self.device.queue_advancing and time.time() < deadline:
            time.sleep(0.01)

        self.assertEqual("Two", self.device.cast.media_controller.status.title)
        self.assertEqual(["Three"], [queued["title"] for queued in server.queue_snapshot(self.device)])

        listener.new_media_status(finished)
        time.sleep(0.05)
        self.assertEqual("Two", self.device.cast.media_controller.status.title)
        self.assertEqual(["Three"], [queued["title"] for queued in server.queue_snapshot(self.device)])


if __name__ == "__main__":
    unittest.main()
