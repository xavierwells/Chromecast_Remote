import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

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

    def play_media(self, url, content_type, *, title, autoplay, stream_type, callback_function=None):
        self.status.content_id = url
        self.status.content_type = content_type
        self.status.title = title
        self.status.player_state = "PLAYING"
        self.status.idle_reason = None
        self.status.media_session_id += 1
        if callback_function:
            callback_function(True, {"type": "MEDIA_STATUS"})

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

    def wait_for_advance(self):
        deadline = time.monotonic() + 2
        while self.device.queue_advancing and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.device.queue_advancing)

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

    def test_idle_without_reason_after_reaching_duration_advances(self):
        self.action("play-sequence", {"items": [item("One"), item("Two")]})
        current = self.device.cast.media_controller.status
        current.adjusted_current_time = current.duration
        listener = server.DeviceStatusListener(self.device)
        listener.new_media_status(current)
        current.player_state = "IDLE"
        current.idle_reason = None
        listener.new_media_status(current)
        self.wait_for_advance()
        self.assertEqual("Two", self.device.cast.media_controller.status.title)

    def test_empty_real_protocol_message_after_end_advances(self):
        controller = server.CastDeckMediaController()
        self.device.cast.media_controller = controller
        listener = server.DeviceStatusListener(self.device)
        controller.register_status_listener(listener)
        controller.receive_message(None, {"type": "MEDIA_STATUS", "status": [{
            "mediaSessionId": 1, "playerState": "PLAYING", "currentTime": 60,
            "media": {"contentId": item("One")["url"], "duration": 60},
        }]})
        server.replace_queue(self.device, [server.normalize_media_item(item("Two"))])
        # Use the real parser for the receiver event and a fake only for loading.
        with patch.object(controller, "play_media", side_effect=lambda url, content_type, **kwargs: self.confirm_load(controller, url, kwargs)):
            controller.receive_message(None, {"type": "MEDIA_STATUS", "status": []})
            self.wait_for_advance()
        self.assertEqual(item("Two")["url"], controller.status.content_id)
        self.assertEqual([], server.queue_snapshot(self.device))

    @staticmethod
    def confirm_load(controller, url, kwargs):
        controller.receive_message(None, {"type": "MEDIA_STATUS", "status": [{
            "mediaSessionId": 2, "playerState": "PLAYING", "currentTime": 0,
            "media": {"contentId": url, "duration": 60, "metadata": {"title": kwargs["title"]}},
        }]})
        kwargs["callback_function"](True, {"type": "MEDIA_STATUS"})

    def test_empty_real_protocol_message_clears_stale_playing_status(self):
        original = server.MediaController()
        original.receive_message(None, {"type": "MEDIA_STATUS", "status": [{
            "mediaSessionId": 1, "playerState": "PLAYING", "currentTime": 10,
            "media": {"contentId": item("One")["url"], "duration": 60},
        }]})
        original.receive_message(None, {"type": "MEDIA_STATUS", "status": []})
        self.assertEqual("PLAYING", original.status.player_state)
        controller = server.CastDeckMediaController()
        controller.receive_message(None, {"type": "MEDIA_STATUS", "status": [{
            "mediaSessionId": 1, "playerState": "PLAYING", "currentTime": 10,
            "media": {"contentId": item("One")["url"], "duration": 60},
        }]})
        controller.receive_message(None, {"type": "MEDIA_STATUS", "status": []})
        self.assertEqual("IDLE", controller.status.player_state)
        self.assertIsNone(controller.status.content_id)
        self.assertFalse(controller.session_active_event.is_set())

    def test_pause_stop_error_and_early_idle_do_not_advance(self):
        for state, reason, position in [("PAUSED", None, 60), ("IDLE", "CANCELLED", 60),
                                        ("IDLE", "ERROR", 60), ("IDLE", "INTERRUPTED", 60),
                                        ("IDLE", None, 10), ("UNKNOWN", None, 60)]:
            with self.subTest(state=state, reason=reason):
                self.action("play-sequence", {"items": [item("One"), item("Two")]})
                current = self.device.cast.media_controller.status
                current.adjusted_current_time = position
                listener = server.DeviceStatusListener(self.device)
                listener.new_media_status(current)
                current.player_state = state
                current.idle_reason = reason
                listener.new_media_status(current)
                self.assertFalse(self.device.queue_advancing)
                self.assertEqual(["Two"], [queued["title"] for queued in server.queue_snapshot(self.device)])

    def test_disconnect_after_a_long_gap_is_not_treated_as_completion(self):
        self.action("play-sequence", {"items": [item("One"), item("Two")]})
        current = self.device.cast.media_controller.status
        current.adjusted_current_time = 10
        listener = server.DeviceStatusListener(self.device)
        with patch.object(server.time, "monotonic", return_value=100):
            listener.new_media_status(current)
        idle = SimpleNamespace(player_state="IDLE", idle_reason=None, empty_status=True,
                               media_session_id=None, content_id=None)
        with patch.object(server.time, "monotonic", return_value=200):
            listener.new_media_status(idle)
        self.assertFalse(self.device.queue_advancing)
        self.assertEqual(1, len(server.queue_snapshot(self.device)))

    def test_old_connection_cannot_advance_the_reconnected_device(self):
        self.action("play-sequence", {"items": [item("One"), item("Two")]})
        listener = server.DeviceStatusListener(self.device)
        self.device.cast = FakeCast()
        listener.new_media_status(SimpleNamespace(player_state="IDLE", idle_reason="FINISHED",
                                                 media_session_id=2, content_id=item("One")["url"]))
        self.assertFalse(self.device.queue_advancing)
        self.assertEqual(1, len(server.queue_snapshot(self.device)))

    def test_failed_autoadvance_restores_item_and_reports_error(self):
        self.action("play-sequence", {"items": [item("One"), item("Two")]})
        def reject(*args, callback_function, **kwargs):
            callback_function(True, {"type": "LOAD_FAILED"})
        with patch.object(self.device.cast.media_controller, "play_media", side_effect=reject):
            server.schedule_queue_advance(self.device)
            self.wait_for_advance()
        self.assertEqual(["Two"], [queued["title"] for queued in server.queue_snapshot(self.device)])
        self.assertIn("rejected", self.device.queue_error)

    def test_load_does_not_accept_an_already_active_previous_session(self):
        with patch.object(self.device.cast.media_controller, "play_media"):
            with self.assertRaisesRegex(RuntimeError, "did not confirm"):
                server.play_media_item(self.device, self.device.cast.media_controller,
                                       server.normalize_media_item(item("Two")), timeout=0.01)
        self.assertFalse(self.device.queue_loading)

    def test_clear_cancels_a_waiting_advance_worker(self):
        self.action("play-sequence", {"items": [item("One"), item("Two")]})
        with self.device.connection_lock:
            server.schedule_queue_advance(self.device)
            server.replace_queue(self.device, [])
        self.wait_for_advance()
        self.assertEqual("One", self.device.cast.media_controller.status.title)
        self.assertEqual([], server.queue_snapshot(self.device))

    def test_poller_requests_fresh_status_only_on_an_existing_active_channel(self):
        server.replace_queue(self.device, [server.normalize_media_item(item("Two"))])
        media = self.device.cast.media_controller
        media.is_active = True
        media.send_message_nocheck = Mock()
        with patch.dict(server.DEVICES, {"test": self.device}, clear=True):
            server.poll_queued_devices()
            media.send_message_nocheck.assert_called_once_with({"type": "GET_STATUS"})
            media.is_active = False
            server.poll_queued_devices()
            media.send_message_nocheck.assert_called_once()


if __name__ == "__main__":
    unittest.main()
