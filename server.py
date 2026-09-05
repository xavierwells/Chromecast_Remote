"""CastDeck local web server and Google Cast bridge."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import signal
import socket
import sys
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import pychromecast

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
MEDIA_DIR = Path(os.environ.get("CASTDECK_MEDIA_DIR", ROOT / "media")).resolve()
MEDIA_TYPES = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".wav": "audio/wav", ".flac": "audio/flac", ".ogg": "audio/ogg",
    ".opus": "audio/ogg", ".m3u8": "application/vnd.apple.mpegurl",
}
DEVICES: dict[str, "CastDevice"] = {}
DEMO_STATUS: dict[str, dict] = {}
STREAMS: set[queue.Queue] = set()
LOCK = threading.RLock()
BROWSER = None
DEMO_MODE = False


@dataclass
class CastDevice:
    id: str
    name: str
    model: str
    host: str
    port: int
    cast: object | None = None
    cast_info: object | None = None
    connection_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    status_listener: object | None = field(default=None, repr=False)

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "model": self.model,
                "host": self.host, "port": self.port, "online": True}


class DeviceStatusListener:
    """Forward receiver and media changes to every open CastDeck browser."""

    def __init__(self, device: CastDevice):
        self.device = device

    def publish(self) -> None:
        cast = self.device.cast
        if cast is not None:
            broadcast("status", {"id": self.device.id, "status": cast_status_payload(cast)})

    def new_cast_status(self, _status) -> None:
        self.publish()

    def new_media_status(self, _status) -> None:
        self.publish()


def attach_status_listener(device: CastDevice) -> None:
    if device.cast is None:
        return
    listener = DeviceStatusListener(device)
    device.status_listener = listener
    device.cast.register_status_listener(listener)
    device.cast.media_controller.register_status_listener(listener)


def device_list() -> list[dict]:
    with LOCK:
        return [device.public() for device in DEVICES.values()]


def media_list(port: int) -> list[dict]:
    """List playable files in the dedicated media directory."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for media_path in sorted(MEDIA_DIR.rglob("*"), key=lambda item: item.name.lower()):
        if not media_path.is_file() or media_path.suffix.lower() not in MEDIA_TYPES:
            continue
        relative = media_path.relative_to(MEDIA_DIR).as_posix()
        items.append({
            "name": media_path.stem,
            "fileName": media_path.name,
            "relativePath": relative,
            "size": media_path.stat().st_size,
            "contentType": MEDIA_TYPES[media_path.suffix.lower()],
            "castUrl": f"http://{lan_address()}:{port}/media/{quote(relative, safe='/')}",
        })
    return items[:1000]


def broadcast(event: str, data) -> None:
    message = f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()
    with LOCK:
        dead = []
        for stream in STREAMS:
            try:
                stream.put_nowait(message)
            except queue.Full:
                dead.append(stream)
        for stream in dead:
            STREAMS.discard(stream)


def add_cast(cast) -> None:
    info = cast.cast_info
    device = CastDevice(str(info.uuid), info.friendly_name or "Chromecast",
                        info.model_name or "Google Cast", info.host, info.port, cast, info)
    attach_status_listener(device)
    with LOCK:
        DEVICES[device.id] = device
    broadcast("devices", device_list())


def add_demo_devices() -> None:
    for item in (
        CastDevice("demo-living-room", "Living Room TV", "Chromecast Ultra", "192.168.1.42", 8009),
        CastDevice("demo-kitchen", "Kitchen Display", "Nest Hub", "192.168.1.58", 8009),
        CastDevice("demo-bedroom", "Bedroom", "Chromecast", "192.168.1.67", 8009),
    ):
        DEVICES[item.id] = item


def default_demo_status(device_id: str) -> dict:
    active = device_id != "demo-bedroom"
    return {
        "volume": {"level": 0.42, "muted": False},
        "playerState": "PLAYING" if active else "IDLE",
        "media": {"title": "Nightmare on Elm Street", "subtitle": "Movie night demo", "image": "",
                  "currentTime": 142, "duration": 596,
                  "contentId": "https://example.com/movie-night.mp4"} if active else None,
    }


def get_demo_status(device_id: str) -> dict:
    return DEMO_STATUS.setdefault(device_id, default_demo_status(device_id))


def patch_demo_status(device_id: str, **changes) -> dict:
    status = get_demo_status(device_id)
    status.update(changes)
    broadcast("status", {"id": device_id, "status": status})
    return status


def image_url(images) -> str:
    if not images:
        return ""
    first = images[0]
    return str(first.get("url", "")) if isinstance(first, dict) else str(getattr(first, "url", ""))


def cast_status_payload(cast) -> dict:
    receiver = cast.status
    media = cast.media_controller.status
    has_media = bool(media and media.content_id)
    return {
        "volume": {"level": float(getattr(receiver, "volume_level", 0) or 0),
                   "muted": bool(getattr(receiver, "volume_muted", False))},
        "playerState": getattr(media, "player_state", "IDLE") or "IDLE",
        "media": {
            "title": getattr(media, "title", None) or getattr(cast, "app_display_name", None) or "Now casting",
            "subtitle": getattr(media, "artist", None) or getattr(media, "series_title", None) or "",
            "image": image_url(getattr(media, "images", None)),
            "currentTime": float(getattr(media, "adjusted_current_time", 0) or 0),
            "duration": float(getattr(media, "duration", 0) or 0),
            "contentId": getattr(media, "content_id", "") or "",
        } if has_media else None,
    }


def cast_status(device: CastDevice) -> dict:
    with device.connection_lock:
        return cast_status_payload(connected_cast(device))


def connected_cast(device: CastDevice):
    """Return a live Cast client. Caller must hold the device connection lock."""
    cast = device.cast
    if cast is None:
        raise RuntimeError("That Chromecast is not connected.")
    socket_client = cast.socket_client
    if not socket_client.is_alive() and socket_client.ident is not None:
        if device.cast_info is None or BROWSER is None:
            raise RuntimeError("The Chromecast connection ended. Refresh the device list.")
        cast = pychromecast.get_chromecast_from_cast_info(
            device.cast_info, BROWSER.zc, tries=3, retry_wait=1, timeout=6
        )
        device.cast = cast
        attach_status_listener(device)
    cast.wait(timeout=6)
    return cast


class CastDeckServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "CastDeck/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, data, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = min(int(self.headers.get("Content-Length", "0") or 0), 32768)
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def get_device(self, device_id: str) -> CastDevice:
        with LOCK:
            device = DEVICES.get(unquote(device_id))
        if device is None:
            raise KeyError("That Chromecast is no longer available.")
        return device

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/devices":
            return self.send_json(device_list())
        if parsed.path == "/api/media":
            return self.send_json(media_list(self.server.server_port))
        if parsed.path == "/api/events":
            return self.event_stream()
        if parsed.path.startswith("/media/"):
            return self.serve_media(parsed.path.removeprefix("/media/"))
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "devices"] and parts[3] == "status":
            try:
                device = self.get_device(parts[2])
                return self.send_json(get_demo_status(device.id) if DEMO_MODE else cast_status(device))
            except KeyError as error:
                return self.send_json({"error": str(error.args[0])}, HTTPStatus.NOT_FOUND)
            except Exception as error:
                return self.send_json({"error": friendly_error(error)}, HTTPStatus.BAD_GATEWAY)
        return self.serve_static(parsed.path)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/media/"):
            return self.serve_media(parsed.path.removeprefix("/media/"), head_only=True)
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parts = urlparse(self.path).path.strip("/").split("/")
        if len(parts) != 4 or parts[:2] != ["api", "devices"]:
            return self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
        try:
            device = self.get_device(parts[2])
            result = self.perform_action(device, parts[3], self.read_json())
            self.send_json(result or {"ok": True})
        except KeyError as error:
            self.send_json({"error": str(error.args[0])}, HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self.send_json({"error": friendly_error(error)}, HTTPStatus.BAD_GATEWAY)

    def perform_action(self, device: CastDevice, action: str, payload: dict):
        if action not in {"play-url", "play", "pause", "stop", "seek", "volume", "mute"}:
            raise KeyError("Unknown control.")
        if DEMO_MODE:
            return self.perform_demo_action(device, action, payload)
        with device.connection_lock:
            cast = connected_cast(device)
            media = cast.media_controller
            if action == "play-url":
                url = str(payload.get("url", "")).strip()
                parsed = urlparse(url)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise ValueError("Use a direct HTTP or HTTPS media URL.")
                content_type = str(payload.get("contentType", "video/mp4")).strip() or "video/mp4"
                title = str(payload.get("title", "")).strip() or "CastDeck media"
                stream_type = "LIVE" if "mpegurl" in content_type.lower() else "BUFFERED"
                media.play_media(url, content_type, title=title, autoplay=True, stream_type=stream_type)
                media.block_until_active(timeout=10)
            elif action == "volume":
                cast.set_volume(max(0.0, min(1.0, float(payload.get("level", 0)))))
            elif action == "mute":
                cast.set_volume_muted(bool(payload.get("muted", False)))
            elif action == "seek":
                media.seek(max(0.0, float(payload.get("time", 0))))
            else:
                getattr(media, action)()
            return cast_status(device)

    def perform_demo_action(self, device: CastDevice, action: str, payload: dict) -> dict:
        current = get_demo_status(device.id)
        if action == "play-url":
            url = str(payload.get("url", "")).strip()
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("Use a direct HTTP or HTTPS media URL.")
            title = str(payload.get("title", "")).strip() or "CastDeck media"
            return patch_demo_status(device.id, playerState="PLAYING", media={
                "title": title, "subtitle": parsed.hostname, "image": "", "currentTime": 0,
                "duration": 600, "contentId": url})
        if action in {"play", "pause", "stop"}:
            return patch_demo_status(device.id, playerState={"play": "PLAYING", "pause": "PAUSED", "stop": "IDLE"}[action])
        if action == "seek" and current.get("media"):
            current["media"]["currentTime"] = max(0, float(payload.get("time", 0)))
        elif action == "volume":
            current["volume"]["level"] = max(0, min(1, float(payload.get("level", 0))))
        elif action == "mute":
            current["volume"]["muted"] = bool(payload.get("muted", False))
        broadcast("status", {"id": device.id, "status": current})
        return current

    def event_stream(self) -> None:
        stream: queue.Queue = queue.Queue(maxsize=20)
        with LOCK:
            STREAMS.add(stream)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(f"event: devices\ndata: {json.dumps(device_list())}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    message = stream.get(timeout=15)
                except queue.Empty:
                    message = b": keep-alive\n\n"
                self.wfile.write(message)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        finally:
            with LOCK:
                STREAMS.discard(stream)

    def serve_static(self, request_path: str) -> None:
        relative = unquote(request_path).lstrip("/") or "index.html"
        target = (PUBLIC / relative).resolve()
        if PUBLIC.resolve() not in target.parents and target != PUBLIC.resolve():
            return self.send_error(HTTPStatus.FORBIDDEN)
        if not target.is_file():
            target = PUBLIC / "index.html"
        body = target.read_bytes()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") or content_type == "application/javascript" else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def serve_media(self, request_path: str, head_only: bool = False) -> None:
        target = (MEDIA_DIR / unquote(request_path)).resolve()
        if MEDIA_DIR not in target.parents or not target.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)

        size = target.stat().st_size
        start, end = 0, max(0, size - 1)
        partial = False
        range_header = self.headers.get("Range", "")
        if range_header.startswith("bytes=") and size:
            try:
                raw_start, raw_end = range_header[6:].split(",", 1)[0].split("-", 1)
                if raw_start:
                    start = int(raw_start)
                    end = min(int(raw_end), size - 1) if raw_end else size - 1
                elif raw_end:
                    length = min(int(raw_end), size)
                    start, end = size - length, size - 1
                if start < 0 or start >= size or end < start:
                    raise ValueError
                partial = True
            except (ValueError, TypeError):
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return

        content_type = MEDIA_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        content_length = end - start + 1 if size else 0
        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head_only or not content_length:
            return

        with target.open("rb") as media_file:
            media_file.seek(start)
            remaining = content_length
            while remaining:
                chunk = media_file.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def friendly_error(error: Exception) -> str:
    message = str(error).strip()
    if not message:
        return "The Chromecast did not respond. Check that it is online and on the same network."
    if "timeout" in message.lower() or "timed out" in message.lower():
        return "The Chromecast did not respond in time."
    return message


def lan_address() -> str:
    """Return the preferred private-network address without sending traffic."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 80))
        return str(probe.getsockname()[0])
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        probe.close()


def main() -> None:
    global BROWSER, DEMO_MODE
    parser = argparse.ArgumentParser(description="Private local Chromecast controller")
    parser.add_argument("--demo", action="store_true", help="use simulated devices")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "4173")))
    args = parser.parse_args()
    DEMO_MODE = args.demo or os.environ.get("CASTDECK_DEMO") == "1"
    if DEMO_MODE:
        add_demo_devices()
    else:
        BROWSER = pychromecast.get_chromecasts(blocking=False, callback=add_cast)
    server = CastDeckServer((args.host, args.port), Handler)
    mode = " (demo mode)" if DEMO_MODE else ""
    print(f"CastDeck is ready{mode}", flush=True)
    print(f"  This computer: http://127.0.0.1:{args.port}", flush=True)
    if args.host == "0.0.0.0":
        print(f"  Phone on Wi-Fi: http://{lan_address()}:{args.port}", flush=True)
    else:
        print(f"  Listening at:  http://{args.host}:{args.port}", flush=True)

    def shutdown(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        if BROWSER is not None:
            BROWSER.stop_discovery()
        with LOCK:
            casts = [device.cast for device in DEVICES.values() if device.cast is not None]
        for cast in casts:
            cast.disconnect()
        server.server_close()


if __name__ == "__main__":
    main()
