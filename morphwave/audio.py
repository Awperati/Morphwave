"""Captures whatever the default playback device is playing and turns it into numbers.

Uses WASAPI loopback, so it hears the mix going to your speakers or headphones.
It follows the default device if you switch outputs.

Short-term values (bass, mid, high, beat) drive the shader every frame.
Long-term values (energy, brightness, bassiness, tempo) describe the music's
"flavour" and feed the AI prompts.
"""

import threading
import time
import warnings

import numpy as np
import soundcard as sc

RATE = 48000
BLOCK = 1024
FFT_SIZE = 2048
N_BANDS = 32
WAVE_POINTS = 256


class AudioAnalyzer(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="audio")
        self._lock = threading.Lock()
        self._halt = threading.Event()
        self.device_choice = ""   # "" = follow the Windows default output
        self._reopen = False
        self._heard = 0           # blocks with sound, for a quick warm-up of the long-term values
        self.device_name = "(none)"
        self.error = ""

        freqs = np.fft.rfftfreq(FFT_SIZE, 1.0 / RATE)
        edges = np.geomspace(30, 16000, N_BANDS + 1)
        self._band_idx = [np.where((freqs >= lo) & (freqs < hi))[0] for lo, hi in zip(edges[:-1], edges[1:])]
        self._band_idx = [i if len(i) else np.array([np.argmin(np.abs(freqs - lo))]) for i, lo in zip(self._band_idx, edges[:-1])]
        self._freqs = freqs
        self._bass = (freqs >= 20) & (freqs < 150)
        self._mid = (freqs >= 150) & (freqs < 2000)
        self._high = (freqs >= 2000) & (freqs < 12000)
        self._window = np.hanning(FFT_SIZE).astype(np.float32)
        self._ring = np.zeros(FFT_SIZE, np.float32)

        # adaptive peaks so the output is 0..1 whatever the volume
        self._peaks = {"bass": 1e-3, "mid": 1e-3, "high": 1e-3, "rms": 1e-3}
        self._band_peak = np.full(N_BANDS, 1e-3, np.float32)
        self._wave_peak = 1e-3
        self._prev_low = None
        self._flux_hist = []
        self._last_beat = 0.0
        self._beat_times = []

        self._state = {
            "bass": 0.0, "mid": 0.0, "high": 0.0, "level": 0.0, "centroid": 0.5,
            "beat_count": 0, "bands": np.zeros(N_BANDS, np.float32), "wave": np.zeros(WAVE_POINTS, np.float32),
            # long-term "flavour"
            "energy": 0.0, "brightness": 0.5, "bassiness": 0.33, "dynamics": 0.0,
            "beat_rate": 0.0, "bpm": 0.0, "silent_for": 0.0,
        }
        self._silent_since = time.time()

    # ---- public ----
    def snapshot(self):
        with self._lock:
            s = dict(self._state)
            s["bands"] = self._state["bands"].copy()
            s["wave"] = self._state["wave"].copy()
        return s

    def stop(self):
        self._halt.set()

    def set_device(self, name):
        """name of a playback device, or "" to follow the Windows default."""
        self.device_choice = name or ""
        self._reopen = True

    @staticmethod
    def list_devices():
        return [s.name for s in sc.all_speakers()]

    def _pick_speaker(self):
        if self.device_choice:
            for s in sc.all_speakers():
                if s.name == self.device_choice:
                    return s
        return sc.default_speaker()

    # ---- capture loop ----
    def run(self):
        warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
        while not self._halt.is_set():
            try:
                spk = self._pick_speaker()
                mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
                self.device_name = spk.name
                self.error = ""
                self._reopen = False
                with mic.recorder(samplerate=RATE, channels=2, blocksize=BLOCK) as rec:
                    check = time.time()
                    while not self._halt.is_set() and not self._reopen:
                        data = rec.record(numframes=BLOCK)
                        self._process(data)
                        if time.time() - check > 2.0:
                            check = time.time()
                            if self._pick_speaker().name != self.device_name:
                                break  # chosen or default output changed, reopen
            except Exception as e:  # device unplugged, exclusive mode, etc.
                self.error = f"{type(e).__name__}: {e}"
                with self._lock:
                    for k in ("bass", "mid", "high", "level"):
                        self._state[k] = 0.0
                time.sleep(1.0)

    def _norm(self, key, value, decay):
        p = max(value, self._peaks[key] * decay, 1e-4)
        self._peaks[key] = p
        return min(value / p, 1.0)

    def _process(self, data):
        now = time.time()
        mono = data.mean(axis=1).astype(np.float32) if data.ndim == 2 else data.astype(np.float32)
        n = len(mono)
        self._ring = np.roll(self._ring, -n)
        self._ring[-n:] = mono[-FFT_SIZE:]
        spec = np.abs(np.fft.rfft(self._ring * self._window)) / FFT_SIZE
        power = spec * spec

        rms = float(np.sqrt(np.mean(mono * mono)))
        bass = float(np.sqrt(power[self._bass].sum()))
        mid = float(np.sqrt(power[self._mid].sum()))
        high = float(np.sqrt(power[self._high].sum()))
        total = power.sum() + 1e-12
        centroid = float((self._freqs * power).sum() / total)

        decay = 0.9995  # about 40 s to forget a loud peak
        nb, nm, nh = self._norm("bass", bass, decay), self._norm("mid", mid, decay), self._norm("high", high, decay)
        nl = self._norm("rms", rms, decay)

        bands = np.array([np.sqrt(power[i].mean()) for i in self._band_idx], np.float32)
        self._band_peak = np.maximum(bands, self._band_peak * 0.999)
        bands_n = np.clip(bands / (self._band_peak + 1e-6), 0, 1)

        # Oscilloscope trace: the newest 1024 samples, started at a rising zero
        # crossing so the shape holds still instead of jittering.
        recent = self._ring[-(WAVE_POINTS * 4 + 512):]
        start = 0
        crossings = np.where((recent[:511] <= 0) & (recent[1:512] > 0))[0]
        if len(crossings):
            start = int(crossings[0])
        trace = recent[start:start + WAVE_POINTS * 4:4]
        self._wave_peak = max(float(np.abs(trace).max()), self._wave_peak * 0.995, 1e-4)
        wave = np.clip(trace / self._wave_peak, -1, 1).astype(np.float32)

        # beat: rise in low-frequency energy well above its recent average
        low = np.log1p(power[: np.searchsorted(self._freqs, 250)] * 1e6)
        beat = False
        if self._prev_low is not None:
            flux = float(np.maximum(low - self._prev_low, 0).sum())
            self._flux_hist.append(flux)
            if len(self._flux_hist) > 47:  # ~1 s
                self._flux_hist.pop(0)
            h = np.array(self._flux_hist)
            if len(h) > 10 and flux > h.mean() + 1.5 * h.std() and now - self._last_beat > 0.18 and rms > 1e-3:
                beat = True
                self._last_beat = now
                self._beat_times.append(now)
        self._prev_low = low
        self._beat_times = [t for t in self._beat_times if now - t < 12]

        silent = rms < 2e-4
        if not silent:
            self._silent_since = now

        with self._lock:
            st = self._state
            a = 0.5  # fast smoothing for visuals
            st["bass"] = st["bass"] * (1 - a) + nb * a
            st["mid"] = st["mid"] * (1 - a) + nm * a
            st["high"] = st["high"] * (1 - a) + nh * a
            st["level"] = st["level"] * (1 - a) + nl * a
            st["centroid"] = st["centroid"] * 0.9 + min(centroid / 5000.0, 1.0) * 0.1
            st["bands"] = st["bands"] * 0.5 + bands_n * 0.5
            st["wave"] = wave
            if beat:
                st["beat_count"] += 1
            # long-term flavour, ~8 s time constant (47 blocks per second)
            if not silent:
                self._heard += 1
            k = max(1.0 / (8 * 47), 1.0 / (self._heard + 1))  # average quickly at first, then settle
            if not silent:
                st["energy"] += (min(rms * 6, 1.0) - st["energy"]) * k
                st["brightness"] += (min(centroid / 4000.0, 1.0) - st["brightness"]) * k
                st["bassiness"] += (bass / (bass + mid + high + 1e-9) - st["bassiness"]) * k
                st["dynamics"] += (abs(nl - st["level"]) - st["dynamics"]) * k
            st["beat_rate"] = len(self._beat_times) / 12.0
            if len(self._beat_times) >= 6:
                gaps = np.diff(self._beat_times)
                bpm = 60.0 / float(np.median(gaps))
                while bpm < 70:
                    bpm *= 2
                while bpm > 180:
                    bpm /= 2
                st["bpm"] = bpm
            else:
                st["bpm"] = 0.0
            st["silent_for"] = now - self._silent_since
