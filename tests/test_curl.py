"""The All-Seeing Spiral's curls follow the bass (morphwave/curl.py), and its eyes swell on the beat."""

import unittest

from morphwave import shaders
from morphwave.curl import SpiralCurl

DT = 1 / 60


def run(curl, bass, seconds):
    out = None
    for _ in range(int(seconds / DT)):
        out = curl.update(DT, bass)
    return out


class Curl(unittest.TestCase):
    def test_heavy_bass_curls_in_fast_and_quiet_settles_slowly(self):
        heavy = SpiralCurl()
        _, wind, rate = run(heavy, 0.9, 20)
        self.assertLess(rate, -1.4)
        self.assertGreater(wind, 0.9)
        quiet = SpiralCurl()
        _, wind, rate = run(quiet, 0.0, 20)
        self.assertAlmostEqual(rate, -0.2, delta=0.02)
        self.assertLess(wind, 0.01)

    def test_the_bass_dropping_away_uncurls(self):
        c = SpiralCurl()
        run(c, 0.9, 10)
        rates = [c.update(DT, 0.05)[2] for _ in range(60)]
        self.assertGreater(max(rates), 1.0)

    def test_a_pulsing_bass_curls_and_uncurls_and_never_jumps(self):
        c = SpiralCurl()
        last, rates, worst = 0.0, [], 0.0
        for i in range(60 * 20):
            bass = 0.9 if (i // 60) % 2 == 0 else 0.1      # a second on, a second off
            curl, _, rate = c.update(DT, bass)
            worst = max(worst, abs(curl - last))
            last = curl
            rates.append(rate)
        self.assertLess(min(rates), -0.5)
        self.assertGreater(max(rates), 0.5)
        self.assertLessEqual(worst, 6 * DT + 1e-9)

    def test_the_shader_uses_it(self):
        src = shaders.SCENE
        self.assertIn("eySpiral(q, pitch, curl + zi2 * 1.7, k)", src)
        self.assertIn("size * 0.36 * (1.0 - 0.4 * curlWind)", src)
        self.assertIn("g_eyPulse = fx_react * clamp(0.6 * beat + 0.5 * exp(-beatAge * 6.0) * fx_punch, 0.0, 1.0)", src)


if __name__ == "__main__":
    unittest.main()
