"""Colour from music (morphwave/musiccolour.py): the EQ bands colour the fractals; the hue wheel scrolls."""

import unittest

import numpy as np

from morphwave import config, shaders
from morphwave.audio import BLOCK, RATE, AudioAnalyzer
from morphwave.musiccolour import COLOUR_BANDS, MAX_STEP_PER_S, MusicColour, hue_at

DT = BLOCK / RATE


def hear(hz, seconds=4.0):
    an = AudioAnalyzer()
    mc = MusicColour(90)
    t = np.arange(BLOCK) / RATE
    st = None
    for i in range(int(seconds / DT)):
        tone = 0.3 * np.sin(2 * np.pi * hz * (t + i * DT)).astype(np.float32)
        an._process(np.stack([tone, tone], axis=1))
        st = mc.update(DT, an.snapshot()["bands"])
    return st


def share(state, hue):
    n = 0
    for i in range(400):
        d = abs(hue_at(state, (i + 0.5) / 400)[0] - hue)
        if min(d, 1 - d) < 1 / 16:
            n += 1
    return n / 400


class MusicColourTest(unittest.TestCase):
    def test_bass_tone_fills_the_picture_with_the_bass_colour_and_treble_with_the_treble_colour(self):
        bass = hear(60)
        self.assertEqual(bass["weights"].index(max(bass["weights"])), 0)
        self.assertGreater(share(bass, bass["hues"][0]), 0.6)
        self.assertLess(share(bass, bass["hues"][-1]), 0.15)
        treble = hear(8000)
        top = treble["weights"].index(max(treble["weights"]))
        self.assertGreaterEqual(top, COLOUR_BANDS - 2)
        self.assertGreater(share(treble, treble["hues"][top]), 0.5)
        self.assertLess(share(treble, treble["hues"][0]), 0.15)

    def test_the_wheel_scrolls_one_turn_per_setting_and_faster_on_beats(self):
        bands = [0.5] * 32
        m = MusicColour(90)
        h0 = m.update(0, bands)["hues"][0]
        for _ in range(45 * 60):
            m.update(1 / 60, bands)
        self.assertAlmostEqual((m.state()["hues"][0] - h0) % 1.0, 0.5, delta=0.01)
        slow, beats = MusicColour(120), MusicColour(120)
        for i in range(600):
            slow.update(1 / 60, bands)
            beats.update(1 / 60, bands, i % 30 == 0)
        self.assertAlmostEqual(slow.phase, 10 / 120, delta=0.002)
        self.assertTrue(slow.phase * 1.3 < beats.phase < slow.phase * 3)

    def test_no_flicker(self):
        m = MusicColour(90)
        prev, worst = m.state()["levels"], 0.0
        for i in range(240):
            st = m.update(1 / 60, [float(i % 2)] * 32)
            worst = max(worst, max(abs(a - b) for a, b in zip(st["levels"], prev)))
            prev = st["levels"]
        self.assertLessEqual(worst, MAX_STEP_PER_S / 60 + 1e-9)

    def test_full_by_default_and_the_present_pass_uses_it(self):
        self.assertEqual(config.DEFAULT_SETTINGS["music_colour"], "full")
        self.assertEqual(config.DEFAULT_SETTINGS["colour_scroll"], 90)
        self.assertIn("col = musicColour(col);", shaders.PRESENT)


if __name__ == "__main__":
    unittest.main()
