"""Colour from music (the owner: "Look at different frequencies and EQ Bands to determine how the music fluctuates
the colours of the fractals itself. The RGB spectrum will sit on top of the EQ band, it will also move on a slow
scroll across the rgb scale so specific colours don't stay on the same frequency for too long"; and "Fully
drives colours"). The same maths as FrostBeat's src/visualiser/musiccolour.ts.
  - The 32 log bands are grouped into 8 colour bands, bass to treble. The hue wheel is laid across them: each
    colour band owns a hue, an eighth of the wheel apart.
  - Each colour band's level is eased (quick to rise, slower to fall, and never more than a set step per
    frame), so colours pulse with the music instead of flickering.
  - The louder a band, the more of the picture its hue takes: the present pass (shaders.py PRESENT) recolours
    every pixel by where its own colour and brightness sit along the bands, weighted by their levels.
  - The whole hue-to-band mapping turns slowly round the wheel (one turn every 90 s by default, a little
    faster for a moment on a beat), so no colour stays on one frequency.
"""

import math

from .audio import N_BANDS

COLOUR_BANDS = 8
MUSIC_COLOUR = {"off": 0.0, "subtle": 0.4, "full": 1.0}   # Settings: how much of the colour the music sets
COLOUR_SCROLL_S = (60, 90, 120)                           # Settings: seconds for one turn of the wheel
ATTACK = 12.0          # per second: how fast a band's colour comes up
DECAY = 2.5            # and fades
MAX_STEP_PER_S = 6.0   # a band's level never moves more than this per second (0.1 a frame at 60)
BEAT_PUSH = 1.5        # right after a beat the scroll runs this much faster, easing back within a second


def group_bands(bands, k=COLOUR_BANDS):
    """The 32 bands grouped into k colour bands (the mean of each group), bass first."""
    per = N_BANDS / k
    out = []
    for g in range(k):
        lo, hi = int(round(g * per)), int(round((g + 1) * per))
        out.append(sum(float(bands[i]) for i in range(lo, hi)) / per)
    return out


class MusicColour:
    def __init__(self, scroll_s=90.0):
        self.scroll_s = scroll_s
        self.levels = [0.0] * COLOUR_BANDS
        self.phase = 0.0
        self._push = 0.0

    def update(self, dt, bands, beat=False):
        """One frame: dt in seconds, the 32 bands (0..1), whether a beat landed. Returns the state."""
        dt = min(max(dt, 0.0), 0.1)
        target = group_bands(bands)
        max_step = MAX_STEP_PER_S * dt
        for i in range(COLOUR_BANDS):
            x = min(1.0, max(0.0, target[i]))
            rate = ATTACK if x > self.levels[i] else DECAY
            step = (x - self.levels[i]) * (1 - math.exp(-dt * rate))
            self.levels[i] += max(-max_step, min(max_step, step))
        if beat:
            self._push = 1.0
        self._push *= math.exp(-dt * 3)
        self.phase = (self.phase + dt / max(1.0, self.scroll_s) * (1 + BEAT_PUSH * self._push)) % 1.0
        return self.state()

    def state(self):
        raw = [(l + 0.02) ** 2 for l in self.levels]
        total = sum(raw)
        return {
            "hues": [(self.phase + i / COLOUR_BANDS) % 1.0 for i in range(COLOUR_BANDS)],
            "weights": [r / total for r in raw],
            "levels": list(self.levels),
            "phase": self.phase,
        }


def hue_at(state, u):
    """The hue (and the band level there) a pixel takes at spot u (0..1): the CPU twin of musicColour()."""
    x = y = lv = gs = acc = 0.0
    for w, h, l in zip(state["weights"], state["hues"], state["levels"]):
        d = abs(u - (acc + 0.5 * w))
        d = min(d, 1 - d)
        acc += w
        z = d / (0.5 * w + 0.04)
        g = (w + 0.001) * math.exp(-z * z)
        x += g * math.cos(2 * math.pi * h)
        y += g * math.sin(2 * math.pi * h)
        lv += g * l
        gs += g
    return (math.atan2(y, x) / (2 * math.pi)) % 1.0, lv / max(gs, 1e-6)
