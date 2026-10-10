"""Render Morphwave's icon: a glowing Julia-set fractal in a rounded square, saved as a
multi-size .ico (16-256 px) so it stays crisp in Explorer, the taskbar and Alt+Tab."""
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from pathlib import Path

N = 1024                                   # render big, then downsample for each icon size
OUT = Path(__file__).resolve().parent.parent / "assets"
OUT.mkdir(exist_ok=True)

# Julia set z -> z^2 + c, smooth iteration count
y, x = np.mgrid[-1.25:1.25:N * 1j, -1.25:1.25:N * 1j]
z = x + 1j * y
c = complex(-0.7269, 0.1889)
n = np.zeros(z.shape)
alive = np.ones(z.shape, bool)
for i in range(300):
    z[alive] = z[alive] * z[alive] + c
    escaped = alive & (np.abs(z) > 16)
    n[escaped] = i + 1 - np.log2(np.log2(np.abs(z[escaped])))
    alive &= ~escaped
t = np.where(alive, 0.0, n)
t = (t / 40.0) ** 0.6

# Morphwave palette: deep violet -> magenta -> cyan -> white-hot core
def pal(v):
    v = np.clip(v, 0, 1)[..., None]
    a, b = np.array([0.5, 0.45, 0.7]), np.array([0.5, 0.45, 0.35])
    cc, d = np.array([1.0, 1.0, 1.0]), np.array([0.65, 0.85, 0.05])
    return np.clip(a + b * np.cos(6.2831853 * (cc * v + d)), 0, 1)

rgb = pal(t) * np.clip(t * 1.6, 0, 1)[..., None]
rgb[alive] = np.array([0.04, 0.0, 0.12])                           # the set itself: deep night
# deep violet backdrop with a soft radial glow behind the fractal
r = np.sqrt(x * x + y * y)[..., None]
bg = np.array([0.08, 0.02, 0.22]) * (1.0 - 0.5 * r) + np.array([0.35, 0.05, 0.45]) * np.exp(-r * r * 2.5) * 0.6
weight = np.clip(t * 2.5, 0, 1)[..., None] * (~alive)[..., None]
rgb = rgb * weight + bg * (1 - weight)
rgb[alive] = np.array([0.04, 0.0, 0.12])
img = Image.fromarray((rgb * 255).astype(np.uint8), "RGB")
glow = img.filter(ImageFilter.GaussianBlur(N / 80))
img = Image.blend(img, glow, 0.35).filter(ImageFilter.UnsharpMask(4, 120, 2))

# rounded-square mask with a soft rim, like a modern app tile
mask = Image.new("L", (N, N), 0)
ImageDraw.Draw(mask).rounded_rectangle((0, 0, N - 1, N - 1), radius=int(N * 0.22), fill=255)
rim = Image.new("RGBA", (N, N), (0, 0, 0, 0))
ImageDraw.Draw(rim).rounded_rectangle((6, 6, N - 7, N - 7), radius=int(N * 0.21), outline=(200, 140, 255, 140), width=10)
tile = img.convert("RGBA")
tile.putalpha(mask)
tile = Image.alpha_composite(tile, rim)

tile.resize((512, 512), Image.LANCZOS).save(OUT / "morphwave.png")
sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
tile.resize((256, 256), Image.LANCZOS).save(OUT / "morphwave.ico", sizes=sizes)
print("wrote", OUT / "morphwave.ico")
