"""Reads the current track from Windows' media controls (Spotify, browsers, players...)."""

import asyncio
import threading
import time

try:
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as _Manager
except Exception:  # package missing or unsupported Windows build
    _Manager = None


class NowPlaying(threading.Thread):
    def __init__(self, poll_seconds=3.0):
        super().__init__(daemon=True, name="nowplaying")
        self.poll = poll_seconds
        self.title = ""
        self.artist = ""
        self.app = ""
        self.changes = 0  # increments whenever the track changes
        self.available = _Manager is not None
        self._halt = threading.Event()

    def stop(self):
        self._halt.set()

    def describe(self):
        if not self.title:
            return ""
        if self.artist:
            return f'"{self.title}" by {self.artist}'
        return f'"{self.title}"'

    async def _read(self, mgr):
        session = mgr.get_current_session()
        if not session:
            return "", "", ""
        props = await session.try_get_media_properties_async()
        return session.source_app_user_model_id or "", props.title or "", props.artist or ""

    def run(self):
        if not self.available:
            return
        loop = asyncio.new_event_loop()
        try:
            mgr = loop.run_until_complete(_Manager.request_async())
        except Exception:
            self.available = False
            return
        while not self._halt.is_set():
            try:
                app, title, artist = loop.run_until_complete(self._read(mgr))
                if (title, artist) != (self.title, self.artist):
                    self.app, self.title, self.artist = app, title, artist
                    self.changes += 1
            except Exception:
                pass
            time.sleep(self.poll)
