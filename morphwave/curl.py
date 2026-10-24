"""The All-Seeing Spiral's curls (the owner: "The all seeing eye needs to move more with the beat and when I say
move those spirals should curl and uncurl at a variable speed based on the bass of the music").

The same maths as FrostBeat's src/visualiser/curl.ts. A phase is added up every frame, like spin and travel, so
the curls never jump or stutter; the shader turns the spirals by it (negative: winding in, positive: unwinding).
  - steady bass: they curl in, faster the heavier it is
  - the bass falling away from its recent average: they uncurl, faster the further it falls
  - quiet: they drift in slowly and settle
`wind` (0..1, the eased bass) also tightens the spirals' pitch.
"""

import math

SETTLE = 0.2      # radians a second when quiet
PER_BASS = 1.6    # more, a second, at full bass
SWING = 10.0      # radians a second per unit of the bass above (curl in) or below (uncurl) its average


class SpiralCurl:
    def __init__(self):
        self.curl = 0.0
        self.rate = 0.0
        self._bass = 0.0
        self._avg = 0.0

    def update(self, dt, bass, react=1.0):
        """One frame: dt in seconds, the bass (0..1), fx react. Returns (curl, wind, rate)."""
        dt = min(max(dt, 0.0), 0.1)
        x = min(1.0, max(0.0, bass))
        k = 10.0 if x > self._bass else 3.0           # rise quickly, fall slowly
        self._bass += (x - self._bass) * (1 - math.exp(-dt * k))
        self._avg += (self._bass - self._avg) * (1 - math.exp(-dt * 0.4))
        target = -(SETTLE + PER_BASS * self._bass * react) + SWING * react * (self._avg - self._bass)
        target = max(-6.0, min(6.0, target))
        self.rate += (target - self.rate) * (1 - math.exp(-dt * 4))   # the speed eases too
        self.curl += self.rate * dt
        return self.curl, min(1.0, self._bass * 1.2 * react), self.rate
