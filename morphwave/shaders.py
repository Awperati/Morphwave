"""GLSL for Morphwave's real-time fractals.

Everything is computed per pixel on the GPU at full resolution, every frame.
Nothing is an image or a texture, so detail stays sharp at any size.

Scenes (the CPU picks one and crossfades to the next every N beats):
  0 julia       2D Julia set, falling forever into its own spiral
  1 mandelbrot  2D Mandelbrot, diving into famous deep spots one after another
  2 apollonian  2D circle-inversion lace, falling into a junction of circles
  3 kifs        3D twisted Menger tunnel you fly through

Every scene moves constantly: the 2D ones zoom in endlessly (a new dive fades
in just before the old one runs out of float precision), the tunnel flies
forward. `travel` sets that speed; the CPU drives it from the BPM, the pace
of the music and the bass.

Other music links:
  mid       rotation (via `spin`) and shape drift
  high      edge glow
  beat      a soft shockwave (2D) or light wave (tunnels), and scene changes; no flashing
  spectrum  lights up colour bands: each band of the fractal is tied to a frequency
  waveform  crisp oscilloscope ring, and nudges the fractal's parameters
  vibe      long-term energy and brightness pick the palette
"""

VERTEX = """
#version 330
in vec2 in_pos;
out vec2 v_uv;
void main() {
    v_uv = in_pos * 0.5 + 0.5;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""

SCENE = """
#version 330
uniform vec2 resolution;
uniform float time;
uniform float atime;      // time that only advances while music plays (stops in silence)
uniform float travel;      // distance fallen / flown; speed follows BPM and pace
uniform float spin;        // accumulated rotation; faster with mids
uniform float curl;        // All-Seeing Spiral: the curls' accumulated turn, its speed set by the bass (curl.py)
uniform float curlWind;    // and how tightly they wind, 0..1 (the eased bass)
uniform float bass;
uniform float mid;
uniform float high;
uniform float level;
uniform float beat;
uniform float energy;      // long-term vibe, 0 calm .. 1 intense
uniform float vibeBright;  // long-term vibe, 0 dark .. 1 bright
uniform sampler2D texAudio;   // row 0 waveform (-1..1), row 1 spectrum (0..1)
uniform sampler2D texPrev;
uniform int sceneA;
uniform int sceneB;
uniform float mixAB;
uniform vec4 seedA;
uniform vec4 seedB;
uniform vec3 palA;
uniform vec3 palB;
uniform vec3 palC;
uniform vec3 palD;
uniform float fx_detail;
uniform float fx_glow;
uniform float fx_kaleido;
uniform float fx_trails;
uniform float fx_hue;
uniform float fx_punch;
uniform float fx_scope;
uniform float fx_react;    // how hard the music bends the scene itself
uniform float beatAge;     // seconds since the last beat
uniform float temporal;    // blend with the last frame to calm sub-pixel shimmer
// Infinite journey: the fractal ahead of the camera turns into the next one.
uniform int famA;          // fractal family behind the switch point
uniform int famB;          // fractal family beyond it
uniform vec4 parA;         // that family's shape parameters
uniform vec4 parB;
uniform float hueA;        // colour offset of each preset
uniform float hueB;
uniform float switchZ;     // world depth where A turns into B (huge = no switch pending)
uniform float blendW;      // half-length of the morph zone along the tunnel
uniform int aa;
uniform float lodBias;     // level of detail: -1 sharpest .. 0 normal .. 2+ fastest (setting + automatic)
in vec2 v_uv;
out vec4 frag;

const float TAU = 6.2831853;

// Level of detail: far from the camera a fractal is drawn with fewer folding iterations.
// The lost detail is smaller than a pixel there anyway, and each step gets much cheaper.
// lodBias moves where detail starts to drop (each +1 halves the distance), how far it drops,
// how many march steps a ray may take and how close a ray must get to count as a hit.
float g_lodF = 1.0;        // 1 = full detail .. ~0.45 = far away
void setLod(float t) {
    float k = exp2(-lodBias);
    g_lodF = mix(1.0, clamp(0.45 - 0.12 * lodBias, 0.25, 0.7), smoothstep(5.0 * k, 30.0 * k, t));
}
bool lodStop(int i, int n) { return float(i) >= ceil(float(n) * g_lodF); }
// ONE is 1 at run time but unknown to the compiler: `for (k < 4 * ONE)` keeps loops rolled,
// so a big function called in them is compiled once instead of pasted in four times.
#define ONE min(aa, 1)
const vec3 NTAP[4] = vec3[4](vec3(1, -1, -1), vec3(-1, -1, 1), vec3(-1, 1, -1), vec3(1, 1, 1));
float lodEps() { return exp2(0.6 * lodBias); }                         // hit tolerance factor
float lodSteps(float n) { return n * clamp(1.0 - 0.15 * lodBias, 0.55, 1.2); }   // march step budget
float smax(float a, float b, float k) {
    float h = clamp(0.5 - 0.5 * (b - a) / k, 0.0, 1.0);
    return mix(b, a, h) + k * h * (1.0 - h);
}
mat2 rot(float a) { float c = cos(a), s = sin(a); return mat2(c, -s, s, c); }
vec2 cmul(vec2 a, vec2 b) { return vec2(a.x * b.x - a.y * b.y, a.x * b.y + a.y * b.x); }
vec2 cexp(vec2 z) { return exp(z.x) * vec2(cos(z.y), sin(z.y)); }
vec2 clog(vec2 z) { return vec2(log(length(z)), atan(z.y, z.x)); }
vec2 csqrt(vec2 z) { float r = length(z); return vec2(sqrt(0.5 * (r + z.x)), sign(z.y + 1e-12) * sqrt(0.5 * (r - z.x))); }
float hash1(float n) { return fract(sin(n * 127.1) * 43758.5453); }
float hash2(vec2 p) { p = mod(p, 1024.0); return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
vec2 hexLocal(vec2 p) {
    vec2 r = vec2(1.0, 1.7320508);
    vec2 h = r * 0.5;
    vec2 a = mod(p, r) - h;
    vec2 b = mod(p - h, r) - h;
    return dot(a, a) < dot(b, b) ? a : b;
}

float wave(float x) { return texture(texAudio, vec2(fract(x), 0.25)).r; }
float spec(float x) { return texture(texAudio, vec2(clamp(x, 0.0, 1.0), 0.75)).r; }
float specWrap(float x) { return spec(abs(fract(x) * 2.0 - 1.0)); }

vec3 pal(float t) {
    float shift = fx_hue * time * 0.02 + (vibeBright - 0.5) * 0.25;
    return clamp(palA + palB * cos(TAU * (palC * t + palD + shift)), 0.0, 1.0);
}

vec2 kaleido(vec2 p) {
    if (fx_kaleido < 1.5) return p;
    if (fx_kaleido < 2.5) return vec2(abs(p.x), p.y);
    float a = atan(p.y, p.x);
    float r = length(p);
    float seg = TAU / fx_kaleido;
    a = mod(a, seg);
    a = abs(a - seg * 0.5);
    return vec2(cos(a), sin(a)) * r;
}

// ---------------- 2D: Julia, falling into its fixed point ----------------
// A Julia set repeats itself around its repelling fixed point zb, scaled and
// turned by f'(zb) = 2*zb. Zooming by powers of that factor falls forever.
vec3 julia(vec2 uv, vec4 seed, float depth, float cyc) {
    uv = kaleido(uv);
    vec2 base = seed.x < 0.2 ? vec2(-0.8, 0.156) : seed.x < 0.4 ? vec2(-0.7269, 0.1889)
              : seed.x < 0.6 ? vec2(0.285, 0.01) : seed.x < 0.8 ? vec2(-0.835, -0.2321) : vec2(-0.4, 0.6);
    float a = time * 0.05 + seed.z * TAU;
    vec2 c = base + vec2(cos(a), sin(a * 1.3)) * 0.01 + vec2(0.02 * bass, 0.012 * mid) * fx_react
           + 0.006 * wave(0.0) * level * fx_react;
    vec2 zb = 0.5 * (vec2(1.0, 0.0) + csqrt(vec2(1.0, 0.0) - 4.0 * c));
    vec2 step1 = cexp(-depth * clog(2.0 * zb));
    vec2 z = zb + cmul(rot(spin * 0.04 + seed.y * TAU) * uv * 2.2, step1);
    float trap = 1e9;
    float n = 0.0;
    float m2 = 0.0;
    int iters = int(140.0 + 140.0 * fx_detail);
    for (int i = 0; i < 400; i++) {
        if (i >= iters) break;
        z = cmul(z, z) + c;
        m2 = dot(z, z);
        trap = min(trap, abs(length(z) - 0.5 - 0.2 * bass));
        if (m2 > 256.0) break;
        n += 1.0;
    }
    vec3 glow = pal(0.6 + trap) * exp(-trap * 22.0) * fx_glow * (0.35 + 1.6 * high);
    if (m2 <= 256.0) return pal(trap * 3.0 + 0.2) * 0.12 + glow;
    float sn = n - log2(log2(m2)) + 4.0;
    vec3 col = pal(sn * 0.045 + cyc * 0.13);
    col *= 0.15 + 1.0 * mix(0.4, specWrap(sn * 0.025), 0.3 + 0.7 * level);
    return col + glow;
}

// ---------------- 2D: Mandelbrot, one deep dive after another ----------------
vec2 mandelTarget(float k) {
    int i = int(mod(k, 6.0));
    if (i == 0) return vec2(-0.743643887, 0.131825904);      // seahorse valley
    if (i == 1) return vec2(-0.77568377, 0.13646737);        // spiral
    if (i == 2) return vec2(-0.10109636, 0.95628651);        // dendrite
    if (i == 3) return vec2(0.2549870, 0.0005000);           // double spiral
    if (i == 4) return vec2(-1.768778833, -0.001738996);     // antenna minibrot
    return vec2(-0.761574, -0.0847596);                      // spiral arms
}
vec3 mandelbrot(vec2 uv, vec4 seed, float depth, float cyc) {
    uv = kaleido(uv);
    float scale = 2.2 * exp(-8.5 * depth);                    // ~5000x per dive, the limit of float precision
    vec2 c = mandelTarget(floor(seed.x * 6.0) + cyc) + rot(spin * 0.04 + cyc) * uv * scale;
    vec2 z = vec2(0.0);
    float trap = 1e9;
    float n = 0.0;
    float m2 = 0.0;
    int iters = int(150.0 + 250.0 * fx_detail + 250.0 * max(depth, 0.0));
    for (int i = 0; i < 800; i++) {
        if (i >= iters) break;
        z = cmul(z, z) + c;
        m2 = dot(z, z);
        trap = min(trap, abs(z.x * z.y));
        if (m2 > 256.0) break;
        n += 1.0;
    }
    if (m2 <= 256.0) return pal(0.1) * 0.05;
    float sn = n - log2(log2(m2)) + 4.0;
    vec3 col = pal(sqrt(sn) * 0.18 + cyc * 0.17);
    col *= 0.15 + 1.0 * mix(0.4, specWrap(sn * 0.02), 0.3 + 0.7 * level);
    col += pal(0.5 + trap) * exp(-trap * 60.0) * fx_glow * (0.25 + 1.4 * high);
    return col;
}

// ---------------- 2D: Apollonian lace, falling into a junction ----------------
vec3 apollonian(vec2 uv, vec4 seed, float depth, float cyc) {
    uv = kaleido(uv);
    float zoom = 2.2 * exp(-5.0 * depth);
    // Points found (by a search over the lace) to stay detailed at every zoom level.
    int ti = int(mod(floor(seed.x * 4.0) + cyc, 4.0));
    vec2 target = ti == 0 ? vec2(-0.010150, 0.015845) : ti == 1 ? vec2(0.138198, 0.000474)
                : ti == 2 ? vec2(0.093409, -0.093913) : vec2(-0.565024, -0.005532);
    vec2 p = target + rot(spin * 0.04 + cyc * 1.7) * uv * zoom;
    float s = 1.12 + 0.008 * sin(time * 0.3 + seed.z * TAU) + 0.006 * bass * fx_punch;
    float k = 1.0;
    vec4 orb = vec4(1000.0);
    for (int i = 0; i < 12; i++) {
        p = -1.0 + 2.0 * fract(0.5 * p + 0.5);
        float r2 = dot(p, p);
        orb = min(orb, vec4(abs(p), r2, abs(p.x * p.y)));
        float f = s / r2;
        p *= f;
        k *= f;
    }
    float d = 0.25 * abs(p.y) / k;
    float px = zoom / resolution.y;                     // one pixel in this space
    float edge = 1.0 - smoothstep(0.0, 1.5 * px, d);
    float halo = exp(-d / px * 0.35);                   // tight glow hugging each line
    vec3 base = pal(orb.z * 0.5 + orb.w * 1.5 + cyc * 0.11);
    base *= 0.06 + 0.3 * mix(0.6, specWrap(orb.z * 0.4), 0.4 + 0.6 * level);
    vec3 lines = pal(orb.x * 0.8 + 0.3) * (edge * (1.1 + high) + halo * 0.5 * fx_glow * (0.3 + high));
    return base + lines;
}

vec3 scene2D(int id, vec2 uv, vec4 seed, float depth, float cyc) {
    if (id == 0) return julia(uv, seed, depth, cyc);
    if (id == 1) return mandelbrot(uv, seed, depth, cyc);
    return apollonian(uv, seed, depth, cyc);
}

// Endless fall: each dive runs depth 0 -> 1; the next dive fades in over the last 20%.
// The music bends the plane itself: the waveform ripples it radially, mids swirl
// the centre, bass swells it, and every beat sends a shockwave outwards.
vec2 musicWarp(vec2 uv) {
    float r = length(uv);
    float a = atan(uv.y, uv.x);
    float w = wave(fract(a / TAU + time * 0.03));
    uv *= 1.0 + fx_react * (0.06 * w * level + 0.07 * bass * exp(-r * 2.5));
    uv *= 1.0 + fx_react * level * 0.025 * sin(r * 28.0 - time * 5.0) * (0.3 + spec(fract(r * 0.7)));
    uv = rot(fx_react * 0.6 * (mid - 0.3) * exp(-r * 1.8)) * uv;
    float front = beatAge * 1.1;
    uv *= 1.0 - fx_react * fx_punch * 0.025 * exp(-pow((r - front) * 4.0, 2.0)) * exp(-beatAge * 1.2);
    return uv;
}

vec3 falling(int id, vec2 uv, vec4 seed) {
    uv = musicWarp(uv);
    float rate = id == 0 ? 0.11 : id == 1 ? 0.045 : 0.08;   // dives per unit of travel
    float ph = travel * rate + seed.w * 7.0;
    float f = fract(ph);
    float cyc = floor(ph);
    float d = id == 0 ? f * 4.0 : f;                        // Julia: 4 self-similar steps per dive
    vec3 col = scene2D(id, uv, seed, d, cyc);
    float x = smoothstep(0.8, 1.0, f);
    if (x > 0.0) {
        float nd = id == 0 ? (f - 1.0) * 4.0 : max(f - 1.0, -0.06);
        col = mix(col, scene2D(id, uv, seed, nd, cyc + 1.0), x);
    }
    return col;
}

// ---------------- 3D tunnels ----------------
// Each distance function repeats through space, so the flight never ends,
// and carves a clear tube along the camera's path (the z axis).
vec3 g_trap;

// 3: twisted Menger sponge, stone-like architecture
float mengerDE(vec3 w, vec4 seed) {
    vec3 p = mod(w + 1.0, 2.0) - 1.0;
    vec3 b = abs(p) - 1.0;
    float d = length(max(b, 0.0)) + min(max(b.x, max(b.y, b.z)), 0.0);
    float twist = 0.15 + 0.35 * seed.x + 0.05 * sin(time * 0.09) + 0.08 * bass * fx_punch + 0.03 * wave(0.3) * level;
    float s = 1.0;
    g_trap = vec3(1e9);
    for (int m = 0; m < 5; m++) {
        if (lodStop(m, 5)) break;
        vec3 a = mod(p * s, 2.0) - 1.0;
        a.xy = rot(twist * float(m + 1) * (seed.y - 0.5)) * a.xy;
        a.yz = rot(twist * 0.6 * (seed.z - 0.5)) * a.yz;
        s *= 3.0;
        vec3 r = abs(1.0 - 3.0 * abs(a));
        float c = (min(max(r.x, r.y), min(max(r.y, r.z), max(r.z, r.x))) - 1.0) / s;
        d = max(d, c);
        g_trap = min(g_trap, abs(a));
    }
    return smax(d, 0.35 - length(w.xy), 0.04);
}

// 4: 3D Apollonian sphere packing, bubbles inside bubbles
float spheresDE(vec3 w, vec4 seed) {
    vec3 p = w * 0.5;
    float s = 1.2 + 0.12 * seed.x + 0.04 * sin(time * 0.11) + 0.03 * bass * fx_punch;
    float k = 1.0;
    g_trap = vec3(1e9);
    for (int i = 0; i < 9; i++) {
        if (lodStop(i, 9)) break;
        p = -1.0 + 2.0 * fract(0.5 * p + 0.5);
        float r2 = dot(p, p);
        g_trap = min(g_trap, vec3(r2, abs(p.xy)));
        float f = s / r2;
        p *= f;
        k *= f;
    }
    float d = 2.0 * 0.25 * abs(p.y) / k;
    // The packing has huge empty bubbles at some scales; seen from the tube they read as a hole.
    // Fill those voids with a second, offset packing so there is always structure all round.
    vec3 p2 = (w + vec3(1.37, 2.11, 0.0)) * 0.5;
    float k2 = 1.0;
    for (int i = 0; i < 7; i++) {
        if (lodStop(i, 7)) break;
        p2 = -1.0 + 2.0 * fract(0.5 * p2 + 0.5);
        float f = s / dot(p2, p2);
        p2 *= f;
        k2 *= f;
    }
    d = min(d, 2.0 * 0.25 * abs(p2.y) / k2 + 0.004);
    return smax(d, 0.3 - length(w.xy), 0.04);
}

// 5: pseudo-Kleinian limit set, organic caverns
float kleinianDE(vec3 w, vec4 seed) {
    vec3 p = w;
    p.z = mod(p.z + 2.0, 4.0) - 2.0;
    vec3 csize = vec3(0.92436, 0.90756, 0.92436) + vec3(0.0, 0.03 * (seed.x - 0.5), 0.0);
    float size = 0.70968 + 0.02 * sin(time * 0.13) + 0.015 * bass * fx_punch;
    float k = 1.0;
    g_trap = vec3(1e9);
    for (int i = 0; i < 9; i++) {
        if (lodStop(i, 9)) break;
        p = 2.0 * clamp(p, -csize, csize) - p;
        float r2 = dot(p, p);
        g_trap = min(g_trap, abs(p));
        float f = max(size / r2, 1.0);
        p *= f;
        k *= f;
    }
    float rxy = length(p.xy);
    float d = max(rxy - 0.92784, abs(rxy * p.z) / length(p)) / k;
    return smax(d, 0.18 - length(w.xy), 0.04);
}

float g_camZ;

// Waves that run through the fractal's surfaces: each slice of tunnel depth is
// tied to a frequency band, and the waveform wraps around the walls.
float meshRipple(vec3 w, float rz) {
    float ang = atan(w.y, w.x);
    float band = spec(fract(abs(rz) * 0.06 + 0.05));
    float runner = sin(rz * 5.0 - time * 7.0 + ang * 2.0);
    float wav = wave(fract(ang / TAU + rz * 0.1));
    return fx_react * level * (0.03 * band * runner + 0.018 * wav);
}

float tunnelDE(int kind, vec3 w, vec4 seed) {
    float rz = w.z - g_camZ;                                  // distance ahead of the camera
    float ang = atan(w.y, w.x);
    // bass swells the walls outward, the waveform ripples them, mids corkscrew the tunnel
    float swell = 1.0 + fx_react * (0.07 * bass + 0.05 * wave(fract(ang / TAU + rz * 0.12)) * level);
    w.xy = rot(fx_react * 0.12 * (mid - 0.3) * rz) * w.xy / swell;
    float rip = meshRipple(w, rz);
    if (kind == 4) return (spheresDE(w, seed) - rip) * swell * 0.85;
    if (kind == 5) return (kleinianDE(w, seed) - rip) * swell * 0.85;
    return (mengerDE(w, seed) - rip) * swell * 0.85;
}

vec3 tunnel(int kind, vec2 uv, vec4 seed) {
    float fly = kind == 5 ? 0.9 : 1.6;
    vec3 ro = vec3(0.0, 0.0, travel * fly);
    g_camZ = ro.z;
    vec3 rd = normalize(vec3(uv, 1.1));
    rd.xy = rot(spin * 0.15) * rd.xy;
    rd.xz = rot(0.12 * sin(time * 0.07)) * rd.xz;
    float pixel = 1.0 / resolution.y;
    float t = 0.0;
    float glow = 0.0;
    float steps = 0.0;
    bool hit = false;
    // the sphere packing's thin sheets make rays crawl; it gets a smaller step budget
    int maxSteps = int(lodSteps((150.0 + 90.0 * fx_detail) * (kind == 4 ? 0.6 : 1.0)));
    float far = kind == 5 ? 22.0 : kind == 4 ? 30.0 : 42.0;                   // look far down the tunnel
    for (int i = 0; i < 300; i++) {
        if (i >= maxSteps) break;
        setLod(t);
        float d = tunnelDE(kind, ro + rd * t, seed);
        glow += exp(-d * 60.0) * 0.02;
        // rays that have crawled a long way (grazing thin sheets) accept a looser hit, which stops
        // them burning hundreds of tiny steps on detail smaller than a pixel
        if (d < pixel * t * (1.5 - fx_detail) * lodEps() * (1.0 + float(i) * 0.02)) { hit = true; break; }
        t += d * (kind == 3 ? 0.9 : kind == 4 ? 0.9 : 0.75) * (t > 6.0 ? 1.25 : 1.0);    // bigger strides through far empty space
        steps += 1.0;
        if (t > far) break;
    }
    vec3 trapCol = pal(dot(g_trap, vec3(0.3)) * 0.8 + travel * 0.01 + float(kind) * 0.2);
    vec3 col = vec3(0.0);
    if (hit) {
        vec3 p = ro + rd * t;
        vec2 e = vec2(max(pixel * t, 1e-4), 0.0);
        const vec2 kk = vec2(1.0, -1.0);                            // 4-sample normal: a third cheaper than 6
        vec3 n = vec3(0.0);
        for (int k = 0; k < 5 * ONE; k++) {                   // 4 normal taps, then the point itself
            vec3 o = k < 4 ? NTAP[k] : vec3(0.0);                 // (the last call restores its colour data)
            float dk = tunnelDE(kind, p + o * e.x, seed);
            n += o * dk;
        }
        n = normalize(n);
        vec3 l = normalize(vec3(0.4, 0.7, -0.5) - rd * 0.5);
        float dif = clamp(dot(n, l), 0.0, 1.0);
        float rim = pow(1.0 - clamp(dot(n, -rd), 0.0, 1.0), 3.0);
        float ao = 1.0 - steps / float(maxSteps);
        float bands = mix(0.6, specWrap(dot(g_trap, vec3(0.2)) + p.z * 0.05), 0.4 + 0.6 * level);
        col = trapCol * (0.15 + 0.85 * dif) * ao * (0.4 + 0.8 * bands);
        col += pal(0.5) * rim * (0.3 + 1.2 * high) * fx_glow;
        float rz = p.z - ro.z;
        float bandsLit = specWrap(rz * 0.07 + 0.3 * dot(g_trap, vec3(0.3)));
        col += trapCol * bandsLit * level * 0.35 * fx_react;
        float front = beatAge * 7.0;                          // beat pulse travelling down the tunnel
        col += pal(0.75 + rz * 0.03) * exp(-pow((rz - front) * 0.6, 2.0)) * exp(-beatAge * 1.0) * 0.35 * fx_react * fx_punch;
    }
    // Distance fades into a glowing haze in the tunnel's own colour, never to black,
    // so the far end reads as endless rather than as a dark hole.
    vec3 haze = pal(0.5 + float(kind) * 0.2 + travel * 0.005) * (0.18 + 0.25 * level) + trapCol * 0.08;
    col = mix(haze, col, hit ? exp(-t * (kind == 5 ? 0.1 : 0.055)) : 0.0);
    col += trapCol * glow * fx_glow * (0.3 + 1.5 * high);
    return col;
}

// ---------------- 3D: the infinite journey ----------------
// Six fractal families; every preset is a family plus four shape numbers.
// The distance field itself morphs from one preset into the next over a stretch
// of tunnel ahead of the camera, so you fly out of one fractal and into another.
const float FLY = 1.4;     // world units per unit of travel

float jMenger(vec3 w, vec4 q) {                   // twisted Menger sponge
    vec3 p = mod(w + 1.0, 2.0) - 1.0;
    vec3 b = abs(p) - 1.0;
    float d = length(max(b, 0.0)) + min(max(b.x, max(b.y, b.z)), 0.0);
    float s = 1.0;
    for (int m = 0; m < 5; m++) {
        if (lodStop(m, 5)) break;
        vec3 a = mod(p * s, 2.0) - 1.0;
        a.xy = rot(q.x * float(m + 1)) * a.xy;
        a.yz = rot(q.y) * a.yz;
        s *= 3.0;
        vec3 r = abs(1.0 - 3.0 * abs(a));
        d = max(d, (min(max(r.x, r.y), min(max(r.y, r.z), max(r.z, r.x))) - (1.0 + q.z)) / s);
        g_trap = min(g_trap, abs(a));
    }
    return smax(d, q.w - length(w.xy), 0.04);
}

float jSpheres(vec3 w, vec4 q) {                  // Apollonian sphere packing
    vec3 p = w * q.y;
    float k = 1.0;
    for (int i = 0; i < 9; i++) {
        if (lodStop(i, 9)) break;
        p = -1.0 + 2.0 * fract(0.5 * p + 0.5);
        float r2 = dot(p, p);
        g_trap = min(g_trap, vec3(r2, abs(p.xy)));
        float f = q.x / r2;
        p *= f;
        k *= f;
    }
    return smax(0.25 * abs(p.y) / k / q.y, q.z - length(w.xy), 0.04);
}

float jKleinian(vec3 w, vec4 q) {                 // pseudo-Kleinian limit set
    vec3 p = w * q.z;
    p.z = mod(p.z + 2.0, 4.0) - 2.0;
    vec3 csize = vec3(0.92436, 0.90756 + q.x, 0.92436);
    float k = 1.0;
    for (int i = 0; i < 9; i++) {
        if (lodStop(i, 9)) break;
        p = 2.0 * clamp(p, -csize, csize) - p;
        g_trap = min(g_trap, abs(p));
        float f = max(q.y / dot(p, p), 1.0);
        p *= f;
        k *= f;
    }
    float rxy = length(p.xy);
    float d = max(rxy - 0.92784, abs(rxy * p.z) / length(p)) / k / q.z;
    return smax(d, q.w - length(w.xy), 0.04);
}

float jTetra(vec3 w, vec4 q) {                    // Sierpinski tetrahedron, folded and turned
    vec3 p = mod(w + 1.0, 2.0) - 1.0;
    float sc = q.z;
    for (int i = 0; i < 9; i++) {
        if (p.x + p.y < 0.0) p.xy = -p.yx;
        if (p.x + p.z < 0.0) p.xz = -p.zx;
        if (p.y + p.z < 0.0) p.zy = -p.yz;
        p.xy = rot(q.x) * p.xy;
        p.yz = rot(q.y) * p.yz;
        p = p * sc - vec3(1.0) * (sc - 1.0);
        g_trap = min(g_trap, abs(p));
    }
    float d = (length(p) - 1.5) * pow(sc, -9.0);
    return smax(d, q.w - length(w.xy), 0.04);
}

float jBoxKifs(vec3 w, vec4 q) {                  // kaleidoscopic folds ending in boxes
    vec3 p = mod(w + 2.0, 4.0) - 2.0;
    float sc = q.z;
    float k = 1.0;
    for (int i = 0; i < 8; i++) {
        if (lodStop(i, 8)) break;
        p = abs(p);
        if (p.x < p.y) p.xy = p.yx;
        if (p.x < p.z) p.xz = p.zx;
        if (p.y < p.z) p.yz = p.zy;
        p.xy = rot(q.x) * p.xy;
        p.yz = rot(q.y) * p.yz;
        p = p * sc - vec3(1.0, q.w, 0.5 + 0.5 * q.w) * (sc - 1.0);
        k *= sc;
        g_trap = min(g_trap, abs(p));
    }
    vec3 a = abs(p);
    float d = (max(a.x, max(a.y, a.z)) - 1.0) / k;
    return smax(d, 0.4 - length(w.xy), 0.04);
}

float jGyroid(vec3 w, vec4 q) {                   // two layers of gyroid minimal surface
    vec3 p = w;
    p.xy = rot(p.z * q.w) * p.xy;
    float f = q.x;
    float g1 = (abs(dot(sin(p * f), cos(p.zxy * f))) - q.y) / (f * 1.8);
    float f2 = f * q.z;
    float g2 = (abs(dot(sin(p * f2 + 1.3), cos(p.zxy * f2))) - q.y * 0.7) / (f2 * 1.8);
    g_trap = min(g_trap, abs(vec3(g1, g2, sin(p.z * f)) * 4.0));
    return smax(max(g1, -g2), 0.4 - length(w.xy), 0.04);
}


float jFoam(vec3 w, vec4 q) {                     // rock eaten by spheres at every scale
    vec3 p = w;
    float d = -0.2;
    float s = 1.0;
    for (int i = 0; i < 5; i++) {
        if (lodStop(i, 5)) break;
        vec3 c = mod(p * s + q.z * float(i), 2.0) - 1.0;
        c.xy = rot(q.y * float(i + 1)) * c.xy;
        float hole = (length(c) - q.x) / s;
        d = max(d, -hole);
        g_trap = min(g_trap, abs(c));
        s *= q.w;
    }
    return smax(d, 0.35 - length(w.xy), 0.04);
}

float jFacet(vec3 w, vec4 q) {                    // rock eaten by octahedra: faceted crystal catacombs
    vec3 p = w;
    float d = -0.2;
    float s = 1.0;
    for (int i = 0; i < 5; i++) {
        if (lodStop(i, 5)) break;
        vec3 c = mod(p * s + 0.37 * float(i), 2.0) - 1.0;
        c.xz = rot(q.y * float(i + 1)) * c.xz;
        vec3 a = abs(c);
        float hole = ((a.x + a.y + a.z - q.x) * 0.57735) / s;
        d = max(d, -hole);
        g_trap = min(g_trap, a);
        s *= q.z;
    }
    return smax(d, q.w - length(w.xy), 0.04);
}

float jWeb(vec3 w, vec4 q) {                       // spiderweb tunnel: a web strung across every frame
    // q.x spokes, q.y spiral spacing, q.z twist per frame, q.w hole radius
    float r = length(w.xy);
    float a = atan(w.y, w.x);
    float gap = 1.3;
    float id = floor(w.z / gap + 0.5);
    float dz = w.z - id * gap;
    float n = q.x;
    float seg = 6.2831853 / n;
    float a2 = a + id * q.z + 0.05 * sin(r * 4.0 + id);
    float da = mod(a2 + seg * 0.5, seg) - seg * 0.5;
    float dSpoke = abs(da) * r;
    // spiral capture thread, sagging between the spokes, breathing with the bass
    float s = q.y * (1.0 + 0.08 * bass * fx_react);
    float rr = r + a2 / 6.2831853 * s - 0.25 * s * (1.0 - cos(da * n));
    float dRing = abs(mod(rr + s * 0.5, s) - s * 0.5);
    float inWeb = smoothstep(q.w, q.w + 0.05, r);
    float plane = min(dSpoke, dRing);
    float strand = length(vec2(dz, plane)) - 0.011;
    // dew drops where the threads cross, swelling with the treble
    float dew = length(vec3(dz, dSpoke, dRing)) - 0.022 * (1.0 + 0.8 * high * fx_react);
    // guy lines running down the tunnel, holding each web to the next
    float ga = mod(a + 0.4 + seg * 1.5, seg * 3.0) - seg * 1.5;
    float guy = length(vec2(ga * r, r - (q.w + 1.6))) - 0.012;
    g_trap = min(g_trap, vec3(dSpoke * 4.0, dRing * 4.0, abs(dz) + 0.2));
    float d = min(min(strand, dew), guy);
    d = mix(max(d, q.w - r), d, inWeb);                // the centre is left open for the camera
    // offset so the music's surface ripples don't fatten the silk into ropes
    return (d + 0.02 * (0.4 + level)) * 0.6;
}

float famDE(int fam, vec3 w, vec4 q) {
    if (fam == 8) return jWeb(w, q);
    if (fam == 0) return jMenger(w, q);
    if (fam == 1) return jSpheres(w, q);
    if (fam == 2) return jKleinian(w, q);
    if (fam == 3) return jTetra(w, q);
    if (fam == 4) return jBoxKifs(w, q);
    if (fam == 6) return jFoam(w, q);
    if (fam == 7) return jFacet(w, q);
    return jGyroid(w, q);
}

float g_blend;   // 0 = preset A, 1 = preset B at the last evaluated point
float journeyDE(vec3 w) {
    float rz = w.z - g_camZ;
    float ang = atan(w.y, w.x);
    float swell = 1.0 + fx_react * (0.07 * bass + 0.05 * wave(fract(ang / TAU + rz * 0.12)) * level);
    w.xy = rot(fx_react * 0.12 * (mid - 0.3) * rz) * w.xy / swell;

    float s = smoothstep(switchZ - blendW, switchZ + blendW, w.z);
    g_blend = s;
    g_trap = vec3(1e9);
    float rip = meshRipple(w, rz);
    // evaluate A and/or B (only what this point needs) through a single famDE call site
    float d = 0.0;
    vec3 trap = vec3(0.0);
    for (int k = 0; k < 2 * ONE; k++) {
        float wk = k == 0 ? 1.0 - s : s;
        if (wk <= 0.0) continue;
        g_trap = vec3(1e9);
        d += wk * famDE(k == 0 ? famA : famB, w, k == 0 ? parA : parB);
        trap += wk * g_trap;
    }
    g_trap = trap;
    return (d - rip) * swell * (s <= 0.0 || s >= 1.0 ? 0.8 : 0.75);
}

vec3 journey(vec2 uv) {
    vec3 ro = vec3(0.0, 0.0, travel * FLY);
    g_camZ = ro.z;
    vec3 rd = normalize(vec3(uv, 1.1));
    rd.xy = rot(spin * 0.15) * rd.xy;
    rd.xz = rot(0.12 * sin(time * 0.07)) * rd.xz;
    float pixel = 1.0 / resolution.y;
    float t = 0.0;
    float glow = 0.0;
    float steps = 0.0;
    bool hit = false;
    int maxSteps = int(lodSteps(150.0 + 90.0 * fx_detail));
    for (int i = 0; i < 300; i++) {
        if (i >= maxSteps) break;
        setLod(t);
        float d = journeyDE(ro + rd * t);
        glow += exp(-d * 60.0) * 0.02;
        // rays that have crawled a long way (grazing thin sheets) accept a looser hit, which stops
        // them burning hundreds of tiny steps on detail smaller than a pixel
        if (d < pixel * t * (1.5 - fx_detail) * lodEps() * (1.0 + float(i) * 0.02)) { hit = true; break; }
        t += d * 0.8 * (t > 6.0 ? 1.25 : 1.0);
        steps += 1.0;
        if (t > 42.0) break;
    }
    vec3 p = ro + rd * t;
    float hue = mix(hueA, hueB, g_blend);
    vec3 trapCol = pal(dot(g_trap, vec3(0.3)) * 0.8 + hue + travel * 0.005);
    vec3 col = vec3(0.0);
    if (hit) {
        vec2 e = vec2(max(pixel * t, 1e-4), 0.0);
        const vec2 kk = vec2(1.0, -1.0);
        vec3 n = vec3(0.0);
        for (int k = 0; k < 5 * ONE; k++) {                   // 4 normal taps, then the point itself
            vec3 o = k < 4 ? NTAP[k] : vec3(0.0);
            n += o * journeyDE(p + o * e.x);
        }
        n = normalize(n);
        vec3 l = normalize(vec3(0.4, 0.7, -0.5) - rd * 0.5);
        float dif = clamp(dot(n, l), 0.0, 1.0);
        float rim = pow(1.0 - clamp(dot(n, -rd), 0.0, 1.0), 3.0);
        float ao = 1.0 - steps / float(maxSteps);
        float rz = p.z - ro.z;
        float bands = mix(0.6, specWrap(dot(g_trap, vec3(0.2)) + p.z * 0.05), 0.4 + 0.6 * level);
        col = trapCol * (0.15 + 0.85 * dif) * ao * (0.4 + 0.8 * bands);
        col += pal(0.5 + hue) * rim * (0.3 + 1.2 * high) * fx_glow;
        col += trapCol * specWrap(rz * 0.07 + 0.3 * dot(g_trap, vec3(0.3))) * level * 0.35 * fx_react;
        float front = beatAge * 7.0;
        col += pal(0.75 + rz * 0.03 + hue) * exp(-pow((rz - front) * 0.6, 2.0)) * exp(-beatAge) * 0.35 * fx_react * fx_punch;
    }
    vec3 haze = pal(0.5 + hue + travel * 0.005) * (0.18 + 0.25 * level) + trapCol * 0.08;
    col = mix(haze, col, hit ? exp(-t * 0.055) : 0.0);
    col += trapCol * glow * fx_glow * (0.3 + 1.5 * high);
    return col;
}


// ================= hand-built worlds =================
float vnoise3(vec3 p) {
    vec3 f = fract(p);
    // wrap the cell index: far into the flight the coordinates get huge and the hash
    // loses float precision (moss and grass used to vanish after a minute or two)
    vec3 i = mod(floor(p), 211.0);
    f = f * f * (3.0 - 2.0 * f);
    float n = i.x + i.y * 57.0 + i.z * 113.0;
    float a = mix(mix(hash1(n), hash1(n + 1.0), f.x), mix(hash1(n + 57.0), hash1(n + 58.0), f.x), f.y);
    float b = mix(mix(hash1(n + 113.0), hash1(n + 114.0), f.x), mix(hash1(n + 170.0), hash1(n + 171.0), f.x), f.y);
    return mix(a, b, f.z);
}

// One watching eye in a tile: swirling iris that dilates with the bass.
vec3 eyeTileLod(vec2 uv, float density, vec3 tissue, float tOff, float fw);
vec3 eyeTile(vec2 uv, float density, vec3 tissue, float tOff) { return eyeTileLod(uv, density, tissue, tOff, 0.0); }
// fw: how much uv changes per pixel; when an eye shrinks to a few pixels it fades to its average
// colour instead of sparkling
vec3 eyeTileLod(vec2 uv, float density, vec3 tissue, float tOff, float fw) {
    vec2 g = uv * density;
    vec2 l = hexLocal(g);
    vec2 id = g - l;
    float d = length(l);
    float h = hash2(id);
    float blink = smoothstep(0.0, 0.08, abs(fract(time * 0.07 + h) - 0.5) - 0.42);   // rare slow blinks
    float open = 0.36 * (1.0 - blink);
    float lid = abs(l.y) - open * sqrt(max(0.0, 1.0 - (l.x * l.x) / 0.16));
    float ang = atan(l.y, l.x);
    float swirl = 0.5 + 0.5 * sin(ang * 3.0 + d * 26.0 - time * 0.8 - spin * 1.2 + h * 6.0);   // steady, faster with mids
    vec3 iris = mix(pal(0.55 + tOff), vec3(0.92, 0.95, 1.0), swirl * swirl);
    float pupil = 0.07 + 0.07 * bass;
    vec3 col = tissue * (0.55 + 0.45 * smoothstep(0.5, 0.3, d));             // raised skin around each eye
    if (lid < 0.0 && d < 0.34) col = d < pupil ? vec3(0.02) : iris * (0.6 + 0.6 * level);
    col = mix(col, tissue * 0.25, smoothstep(0.012, 0.0, abs(lid)) * step(d, 0.4));   // dark lid line
    vec3 avg = mix(tissue * 0.8, pal(0.55 + tOff) * 0.5, 0.3);
    return mix(col, avg, smoothstep(0.12, 0.3, fw * density));
}

// ---------------- Net of Being: an endless hall of arches, walls made of eyes ----------------
float sdArch(vec2 p, float w) {
    vec2 q = abs(p - vec2(0.0, -0.25)) - vec2(w, 0.45);
    float box = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0);
    return min(box, length(p - vec2(0.0, 0.2)) - w);
}
float g_part;
// Colour waves that ripple out of the far end of the hall towards the camera. They also
// bend the hall itself, so the walls and arches physically ripple as each wave passes.
float nobWave(float rz, float ang) {
    return sin(rz * 2.2 + time * 4.0 + ang + 1.5 * sin(rz * 0.3 - time * 0.7)) * smoothstep(0.0, 1.5, rz);
}
float nobDE(vec3 p) {
    float rz = p.z - g_camZ;
    // the hall is alive: it twists with the mids, its walls undulate in travelling waves,
    // and each arch breathes with the bass and its own frequency band
    p.xy = rot(fx_react * 0.08 * sin(p.z * 0.4 + time * 0.5) * (0.3 + 1.2 * mid)) * p.xy;
    p.x += fx_react * 0.07 * sin(p.y * 3.0 + p.z * 2.0 - time * 2.5) * (0.3 + level);
    p.y += fx_react * 0.05 * sin(p.x * 3.0 + p.z * 1.7 + time * 2.0) * (0.3 + mid);
    float zf = mod(p.z, 2.0) - 1.0;
    float n = floor(p.z / 2.0);
    float w = 0.85 * (0.96 + 0.04 * sin(n * 1.7));
    w *= 1.0 + fx_react * (0.07 * bass + 0.05 * spec(fract(abs(rz) * 0.05)) * level + 0.03 * sin(p.z * 1.3 - time * 2.0));
    float a = sdArch(p.xy, w);
    float wall = -a;
    float frame = min(max(-(a + 0.2), abs(zf) - 0.14), max(-(a + 0.3), abs(zf) - 0.05));
    g_part = frame < wall ? 1.0 : 0.0;
    float amp = fx_react * (0.03 + 0.07 * level + 0.06 * bass);
    float wv = nobWave(rz, atan(p.y - 0.1, p.x)) * amp;
    return (min(wall, frame) - meshRipple(p, rz) * 1.5 - wv) * 0.6;
}
vec3 netOfBeing(vec2 uv) {
    // the camera stays centred and keeps spinning as it falls further down the hall;
    // the spin speeds up with the mids (via `spin`) and with the distance travelled
    vec3 ro = vec3(0.0, -0.05, travel * FLY);
    g_camZ = ro.z;
    vec3 rd = normalize(vec3(uv, 1.25));
    rd.xy = rot(spin * 0.12 + travel * 0.05) * rd.xy;
    float t = 0.0;
    bool hit = false;
    float steps = 0.0;
    float pixel = 1.0 / resolution.y;
    float nSteps = lodSteps(220.0);
    for (int i = 0; i < 260; i++) {
        if (float(i) >= nSteps) break;
        float d = nobDE(ro + rd * t);
        if (d < pixel * t * 1.2 * lodEps()) { hit = true; break; }
        t += d * 0.85;                                     // the warped hall needs slightly careful steps
        steps += 1.0;
        if (t > 60.0) break;
    }
    // colours drift through the palette over time and along the hall; bass nudges them
    float hueS = atime * 0.025 + 0.12 * bass * fx_react;
    vec3 fogc = pal(0.12 + hueS) * 1.3 + vec3(0.25, 0.35, 0.15);                // the glow deep in the hall
    if (!hit) return fogc;
    vec3 p = ro + rd * t;
    vec2 e = vec2(pixel * t + 1e-4, 0.0);
    vec3 n = normalize(vec3(nobDE(p + e.xyy) - nobDE(p - e.xyy), nobDE(p + e.yxy) - nobDE(p - e.yxy),
                            nobDE(p + e.yyx) - nobDE(p - e.yyx)));
    nobDE(p);
    float part = g_part;
    // 1.1141 = 7 hex columns per radian band: the eye pattern wraps round with no seam
    float u = atan(p.y - 0.1, p.x) * 1.1141;
    float hz = hueS + 0.2 * sin(p.z * 0.07 - atime * 0.05);
    vec3 tissue = pal(0.02 + hz + 0.08 * sin(p.z * 0.7)) * (0.5 + 0.5 * specWrap(p.z * 0.05) * (0.4 + 0.6 * level));
    vec3 col;
    float fwp = pixel * t / max(abs(dot(n, rd)), 0.15);                  // world size of a pixel here
    if (part > 0.5) {
        // arch ribs: carved, ridged flesh (tiling eyes over them sliced eyes at every edge)
        float az = atan(p.y - 0.2, p.x);
        float ridge = 0.5 + 0.5 * sin((abs(n.z) > 0.6 ? length(p.xy - vec2(0.0, 0.2)) : p.z) * 40.0 + az * 2.0);
        ridge = mix(ridge, 0.5, smoothstep(0.02, 0.06, fwp));               // fade the ridges out far away
        col = tissue * vec3(1.2, 0.9, 0.5) * (0.55 + 0.6 * ridge) + pal(0.55 + hz) * 0.25 * specWrap(az * 0.3 + p.z * 0.1) * level;
        vec2 fe = vec2(abs(p.x) - 0.28, p.y - 0.75);                          // a watching face on every arch
        float fd = length(fe * vec2(1.0, 1.6));
        if (fd < 0.11) col = fd < 0.04 + 0.02 * bass ? vec3(0.02) : mix(pal(0.6 + hz), vec3(1.0), 0.4) * (0.7 + 0.5 * level);
    } else {
        col = eyeTileLod(vec2(u + 0.04 * sin(p.z * 0.8 + time * 0.6), p.z + 0.05 * sin(u * 3.0 + time * 0.8)), 4.0, tissue, hz, pixel * t * 1.3);   // isotropic: walls are always seen edge-on
    }
    float dif = clamp(dot(n, normalize(vec3(0.3, 0.6, -0.7))), 0.0, 1.0);
    // ambient occlusion from the distance field itself (a step-count AO drew dark ghost
    // outlines wherever rays skimmed past an arch frame)
    float ao = clamp(nobDE(p + n * 0.12) / (0.12 * 0.65), 0.0, 1.0);
    col *= (0.45 + 0.75 * dif) * (0.55 + 0.45 * ao);
    float wv = nobWave(p.z - ro.z, atan(p.y - 0.1, p.x));
    col += pal(0.3 + hz + 0.15 * wv) * max(wv, 0.0) * (0.15 + 0.45 * level) * fx_react;   // the wave's colour
    return mix(col, fogc, 1.0 - exp(-t * 0.09));
}

// ---------------- Cosmic Ocean: an ocean below, an upside-down ocean above, dancing shards ----------------
float swell(vec2 xz, float t) {
    // layered directional waves, from long swells down to small chop
    float h = 0.10 * sin(dot(xz, vec2(0.8, 0.6)) * 1.3 + t * 1.1);
    h += 0.07 * sin(dot(xz, vec2(-0.5, 0.85)) * 2.1 + t * 1.6);
    h += 0.045 * sin(dot(xz, vec2(0.95, -0.3)) * 3.7 - t * 2.2);
    h += 0.025 * sin(dot(xz, vec2(0.2, 1.0)) * 6.3 + t * 3.1);
    h += 0.014 * sin(dot(xz, vec2(-0.9, -0.4)) * 11.0 + t * 4.5);
    h += 0.008 * sin(dot(xz, vec2(0.6, -0.8)) * 19.0 - t * 6.0);
    return h;
}
float oceanH(vec2 xz) {
    float amp = 1.0 + fx_react * (1.4 * bass + 0.9 * level);           // the sea rises with the music
    float h = swell(xz, time) * amp;
    vec2 c = vec2(0.0, g_camZ + 6.0);
    float r = length(xz - c);
    // rings of ripples rolling out ahead of you, each ring tied to a frequency band
    h += fx_react * level * 0.08 * spec(fract(r * 0.04)) * sin(r * 6.0 - time * 5.0) * exp(-r * 0.04);
    return h;
}
float ceilY(vec2 xz) {
    float r = length(xz - vec2(0.0, g_camZ + 9.0));
    float h = 2.3 - 0.4 * exp(-r * 0.2);
    h += 0.12 * sin(r * 4.0 - time * 1.5) * (1.0 + fx_react * bass);
    h -= swell(xz * 0.8 + 13.0, time * 0.8) * 0.7 * (1.0 + fx_react * level);   // the sky is water too
    return h;
}
float sdTriPrism(vec3 p, vec2 h) {
    p.y = -p.y;                                                              // point downwards
    vec3 q = abs(p);
    return max(q.z - h.y, max(q.x * 0.866025 + p.y * 0.5, -p.y) - h.x * 0.5);
}
float g_shardH;
float shardDE(vec3 p) {
    vec2 cell = floor(p.xz / 3.5 + 0.5);
    if (abs(cell.x) < 0.5) cell.x = 1.0;                                      // keep the flight path clear
    vec2 l = p.xz - cell * 3.5;
    float h = hash2(cell);
    g_shardH = h;
    // every shard dances to its own slice of the spectrum, like the Menger walls pulse
    // driven by the smoothed bass/level envelopes plus a slow per-shard bob, so they glide
    // instead of twitching with every frame of the spectrum
    float band = 0.5 + 0.5 * sin(atime * (0.8 + h) + h * 20.0);
    float lift = fx_react * (0.45 * band * level + 0.25 * bass);
    float size = 1.0 + fx_react * 0.12 * level;
    vec3 q = vec3(l.x, p.y - 1.1 - 0.25 * sin(atime * 0.6 + h * 6.0) - lift, l.y);
    q.xz = rot(atime * 0.35 * (h - 0.5) + h * 6.0) * q.xz;
    q.xy = rot(0.12 * sin(atime * 0.7 + h * 9.0)) * q.xy;                // a slow, steady rock
    return sdTriPrism(q / size, vec2(0.45 + 0.25 * h, 0.05)) * size;
}
float oceanDE(vec3 p) {
    float sea = (p.y - oceanH(p.xz)) * 0.55;
    float sky = (ceilY(p.xz) - p.y) * 0.55;
    float sh = shardDE(p);
    g_part = sea < min(sky, sh) ? 0.0 : (sky < sh ? 1.0 : 2.0);
    return min(min(sea, sky), sh);
}
vec3 cosmicSky(vec3 rd) {
    vec3 sky = vec3(0.0);
    vec2 sd = rd.xy / max(rd.z, 0.05) * 30.0;
    float st = hash2(floor(sd));
    float star = 1.0 - smoothstep(0.03, 0.09, length(fract(sd) - 0.5));             // a point, not the whole cell
    sky += vec3(0.9, 0.9, 1.0) * step(0.97, st) * star * (0.5 + 0.5 * sin(time * 3.0 + st * 40.0));
    float gr = length(rd.xy / max(rd.z, 0.05) - vec2(0.0, 0.18));
    float gang = atan(rd.y / max(rd.z, 0.05) - 0.18, rd.x / max(rd.z, 0.05));
    sky += pal(0.7) * exp(-gr * 9.0) * (0.6 + 0.4 * sin(gang * 2.0 - log(gr + 1e-3) * 4.0 + time)) * (0.6 + level);
    return sky;
}
vec3 waterShade(vec3 rd, vec3 n, float crest, float up) {
    // fresnel mix of a deep body colour and the reflected sky, foam on crests, a sun-glint from the galaxy
    vec3 refl = reflect(rd, n);
    vec3 rc = cosmicSky(refl) + pal(0.5) * 0.3 * smoothstep(0.0, 0.6, refl.y * up);
    float fres = 0.04 + 0.96 * pow(1.0 - max(dot(n, -rd), 0.0), 5.0);
    vec3 col = mix(pal(0.6) * 0.14, rc, fres);
    col += pal(0.5) * smoothstep(0.1, 0.2, crest) * (0.35 + 0.5 * level);
    col += vec3(0.9, 0.95, 1.0) * pow(max(dot(refl, normalize(vec3(0.0, 0.18 * up, 1.0))), 0.0), 80.0) * (0.6 + level);
    return col;
}
vec3 cosmicOcean(vec2 uv) {
    vec3 ro = vec3(0.0, 0.8, travel * FLY * 1.2);
    g_camZ = ro.z;
    vec3 rd = normalize(vec3(uv, 1.2));
    rd.xy = rot(0.05 * sin(spin * 0.2)) * rd.xy;
    float t = 0.0;
    bool hit = false;
    for (int i = 0; i < 220; i++) {
        if (float(i) >= lodSteps(220.0)) break;
        float d = oceanDE(ro + rd * t);
        if (d < 0.0012 * t * lodEps()) { hit = true; break; }
        t += d;
        if (t > 70.0) break;
    }
    vec3 sky = cosmicSky(rd);
    vec3 haze = pal(0.6) * (0.12 + 0.15 * level);
    if (!hit) return sky + haze * 0.5;
    vec3 p = ro + rd * t;
    vec2 e = vec2(0.002 * t + 1e-3, 0.0);
    vec3 n = normalize(vec3(oceanDE(p + e.xyy) - oceanDE(p - e.xyy), oceanDE(p + e.yxy) - oceanDE(p - e.yxy),
                            oceanDE(p + e.yyx) - oceanDE(p - e.yyx)));
    oceanDE(p);                                                         // material of this exact point
    float part = g_part;
    float fres = pow(1.0 - clamp(dot(n, -rd), 0.0, 1.0), 3.0);
    vec3 col;
    if (part < 0.5) {
        col = waterShade(rd, n, oceanH(p.xz) / (1.0 + fx_react * (1.4 * bass + 0.9 * level)), 1.0);
        float bolt = abs(sin(p.x * 2.0 + 1.5 * sin(p.z * 3.0 + time * 0.7) + 0.6 * sin(p.z * 11.0)));
        col += vec3(0.6, 1.0, 0.8) * smoothstep(0.06, 0.0, bolt) * (0.2 + 1.2 * high * level) * exp(-abs(p.x) * 0.3);
    } else if (part < 1.5) {
        float r = length(p.xz - vec2(0.0, g_camZ + 9.0));
        float rings = smoothstep(0.08, 0.0, abs(fract(r * 1.5 - time * 0.3) - 0.5) - 0.42);
        col = waterShade(rd, n, 0.0, -1.0) * 0.8;
        col += pal(0.48) * rings * (0.5 + 0.6 * specWrap(r * 0.05));
        col = mix(col, pal(0.8) * 1.4, exp(-r * 0.6));                              // the vortex overhead
    } else {
        shardDE(p);
        float h = g_shardH;
        float cracks = smoothstep(0.08, 0.0, abs(vnoise3(p * 6.0) - 0.5));
        col = vec3(0.45, 0.25, 0.1) * (0.4 + 0.6 * clamp(dot(n, normalize(vec3(0.2, 1.0, -0.5))), 0.0, 1.0));
        col += vec3(1.0, 0.7, 0.2) * cracks * (0.3 + 1.2 * spec(h) * level);           // veins light with its band (colour only)
        col += pal(0.85) * fres * (1.5 + 1.5 * spec(h) * level);                      // glowing outline
    }
    return mix(col, sky + haze, 1.0 - exp(-t * 0.035));
}

// ---------------- Watching Woods: a forest of giant trunks covered in eyes ----------------
vec2 g_cell;
vec2 g_local;
uniform float sceneAge;     // seconds since this scene began
float g_growth;             // 0 bare trees .. 1 fully branched
float sdCap3(vec3 p, vec3 a, vec3 b, float r) {
    vec3 pa = p - a, ba = b - a;
    float h = clamp(dot(pa, ba) / max(dot(ba, ba), 1e-6), 0.0, 1.0);
    return length(pa - ba * h) - r;
}
// The woods cycle through a set of eerie palettes; the music pushes the cycle along
// and the bass flashes it forward. Each entry: moss/grass colour, sky colour, eye colour.
vec3 woodsCol(int which, float ph) {
    vec3 moss[5] = vec3[5](vec3(0.3, 1.0, 0.08), vec3(0.1, 0.9, 1.0), vec3(1.0, 0.25, 0.85), vec3(1.0, 0.6, 0.05), vec3(0.6, 0.35, 1.0));
    vec3 sky[5]  = vec3[5](vec3(0.28, 0.08, 0.65), vec3(0.05, 0.12, 0.45), vec3(0.35, 0.02, 0.2), vec3(0.3, 0.06, 0.02), vec3(0.06, 0.3, 0.28));
    vec3 eye[5]  = vec3[5](vec3(1.0, 0.12, 0.08), vec3(1.0, 0.85, 0.1), vec3(0.2, 1.0, 0.4), vec3(0.2, 0.5, 1.0), vec3(1.0, 0.3, 0.1));
    float f = fract(ph) * 5.0;
    int i0 = int(floor(f)) % 5;
    int i1 = (i0 + 1) % 5;
    float w = smoothstep(0.0, 1.0, fract(f));
    if (which == 0) return mix(moss[i0], moss[i1], w);
    if (which == 1) return mix(sky[i0], sky[i1], w);
    return mix(eye[i0], eye[i1], w);
}
float woodsPhase() { return atime * 0.012 + travel * 0.004 + 0.25 * (vibeBright - 0.5); }
float trunkRadius(float y) {
    return 0.34 * (1.0 - clamp(y / 70.0, 0.0, 0.9)) + 0.38 * exp(-max(y, 0.0) * 2.5);
}
float woodsDE(vec3 p) {
    vec2 cell = floor(p.xz / 2.4 + 0.5);
    vec2 jit = (vec2(hash2(cell), hash2(cell + 7.1)) - 0.5) * 0.4;      // small, so trees stay inside their cell
    vec2 l = p.xz - cell * 2.4 - jit;
    float r = length(l);
    float ang = atan(l.y, l.x);
    bool path = abs(cell.x) < 0.5;
    // trunks reach far beyond view; bark has deep vertical grooves
    float grooves = 0.025 * abs(sin(ang * 9.0 + p.y * 0.35 + hash2(cell) * 6.0));
    // trunks breathe with the bass and their own frequency band, and sway with the mids
    float tb = spec(hash2(cell));
    float sway = fx_react * (0.12 * mid + 0.08 * tb * level) * sin(p.y * 0.25 + atime * 1.3 + hash2(cell) * 6.0) * min(p.y * 0.1, 1.0);
    l.x += sway;
    r = length(l);
    // only the slender upper part breathes with the music; the flared base is already near the cell edge
    float trunk = path ? 10.0 : (r - trunkRadius(p.y) - 0.34 * fx_react * (0.12 * bass + 0.18 * tb * level) + grooves) * 0.7;
    // Branches that grow over time: the longer the scene runs, the more branches sprout,
    // and each grows its own side-branches, which grow twigs of their own. They stay inside
    // their tree's cell (no clipping at cell edges) and only the nearest slots are checked.
    float branches = 1e9;
    if (!path && p.y > 2.5) {
        vec3 lp = vec3(l.x, p.y, l.y);
        float slot = 1.6;
        float s0 = floor(p.y / slot);
        for (int k = 0; k < 2; k++) {
            float by = (s0 - float(k)) * slot + slot * 0.5;
            if (by < 2.5) continue;
            for (int j = 0; j < 2; j++) {
                float hb = hash2(cell * 1.7 + vec2(by * 0.37, float(j) * 5.3));
                float grownA = smoothstep(hb * 0.85, hb * 0.85 + 0.15, g_growth);      // sprouts when growth passes its seed
                if (grownA <= 0.0) continue;
                float ba = hb * 6.28318 + float(j) * 3.14159;
                vec3 dir = normalize(vec3(cos(ba), 0.6, sin(ba)));
                vec3 b0 = vec3(0.0, by, 0.0) + vec3(cos(ba), 0.0, sin(ba)) * trunkRadius(by) * 0.8;
                vec3 b1 = b0 + dir * 0.32 * grownA;
                branches = min(branches, sdCap3(lp, b0, b1, 0.045 * (0.5 + 0.5 * grownA)));
                // second generation: two side-branches from the end
                float grownB = smoothstep(0.35 + hb * 0.4, 0.5 + hb * 0.4, g_growth);
                if (grownB > 0.0) {
                    for (int q = 0; q < 2; q++) {
                        float sa = ba + (float(q) - 0.5) * 1.3;
                        vec3 d2 = normalize(vec3(cos(sa), 0.9, sin(sa)));
                        vec3 c1 = b1 + d2 * 0.16 * grownB;
                        branches = min(branches, sdCap3(lp, b1, c1, 0.026 * (0.5 + 0.5 * grownB)));
                        // third generation: twigs, skipped far away (level of detail)
                        float grownC = smoothstep(0.65 + hb * 0.3, 0.8 + hb * 0.2, g_growth);
                        if (grownC > 0.0 && g_lodF > 0.75) {
                            for (int u = 0; u < 2; u++) {
                                float ta = sa + (float(u) - 0.5) * 1.1;
                                vec3 d3 = normalize(vec3(cos(ta), 1.2, sin(ta)));
                                branches = min(branches, sdCap3(lp, c1, c1 + d3 * 0.07 * grownC, 0.013));
                            }
                        }
                    }
                }
            }
        }
    }
    float roots = path ? 0.0 : 0.28 * pow(abs(cos(ang * 3.5 + hash2(cell) * 6.0 + sin(r * 2.5))), 10.0) * exp(-r * 0.55);
    roots += 0.045 * abs(sin(p.x * 5.0 + sin(p.z * 4.0)) * sin(p.z * 6.0 + sin(p.x * 3.0)));
    roots += 0.02 * abs(sin(p.x * 13.0 + 2.0 * sin(p.z * 9.0)));
    // grass tufts: bumpy clumps all over the floor, swaying a little with the music
    float tuft = vnoise3(vec3(p.xz * 9.0, 0.0));
    roots += 0.06 * tuft * tuft + 0.015 * sin(p.x * 31.0 + p.z * 23.0 + atime * 2.0) * (0.4 + level);
    float ground = (p.y - roots + 0.02 * sin(p.z * 3.0 - time * 3.0) * fx_react * bass) * 0.6;
    g_cell = cell;
    g_local = l;
    g_part = trunk < ground ? 1.0 : 0.0;
    if (branches < min(trunk, ground)) g_part = 3.0;
    trunk = min(trunk, branches * 0.9);
    // never step further than just past this cell's edge
    vec2 edge = 1.2 - abs(p.xz - cell * 2.4);
    trunk = min(trunk, max(min(edge.x, edge.y), 0.0) + 0.15);   // trees stay 0.15 inside their cell
    return min(trunk, ground) - meshRipple(p, p.z - g_camZ) * 0.5;
}
vec3 watchingWoods(vec2 uv) {
    // The trees are big and you look upwards, so the same speed feels like a crawl here:
    // fly 3.5x faster through the woods, with a little sway on the bass.
    g_growth = clamp(sceneAge / 180.0, 0.0, 1.0);                    // fully branched after ~3 minutes
    vec3 ro = vec3(0.12 * sin(time * 0.4) * (0.3 + bass), 1.0 + 0.08 * bass * fx_react, travel * FLY * 3.5);
    g_camZ = ro.z;
    vec3 rd = normalize(vec3(uv * 1.15, 1.0));
    rd.yz = rot(0.36) * rd.yz;                                        // looking up into the canopy
    rd.xy = rot(0.05 * sin(spin * 0.2)) * rd.xy;
    float t = 0.0;
    bool hit = false;
    for (int i = 0; i < 260; i++) {
        setLod(t);
        if (float(i) >= lodSteps(260.0)) break;
        float d = woodsDE(ro + rd * t);
        if (d < 0.0012 * t * lodEps()) { hit = true; break; }
        t += d;
        if (t > 90.0) break;
    }
    float wph = woodsPhase() + 0.04 * bass * fx_react;
    vec3 mossC = woodsCol(0, wph), skyC = woodsCol(1, wph), eyeC = woodsCol(2, wph);
    vec3 skyc = mix(skyC * 0.1, skyC * (1.0 + 0.6 * level), smoothstep(0.0, 0.9, rd.y));
    vec3 mist = mix(skyC * 0.4, mossC * 0.35, 0.35) * (0.5 + 1.0 * level + 0.8 * bass * fx_react);
    if (!hit) return skyc;
    vec3 p = ro + rd * t;
    woodsDE(p);                                                        // colour data for this exact point
    float part = g_part;
    vec2 cell = g_cell;
    vec2 l = g_local;
    vec2 e = vec2(0.002 * t + 1e-3, 0.0);
    vec3 n = normalize(vec3(woodsDE(p + e.xyy) - woodsDE(p - e.xyy), woodsDE(p + e.yxy) - woodsDE(p - e.yxy),
                            woodsDE(p + e.yyx) - woodsDE(p - e.yyx)));
    float ang = atan(l.y, l.x);
    float groove = abs(sin(ang * 9.0 + p.y * 0.35 + hash2(cell) * 6.0));
    vec3 bark = mix(vec3(0.05, 0.03, 0.09), skyC * 0.25, 0.4) * (0.45 + 0.8 * clamp(dot(n, normalize(vec3(-0.3, 0.8, -0.4))), 0.0, 1.0));
    bark *= 0.55 + 0.45 * groove;
    float mossN = vnoise3(p * vec3(3.0, 1.2, 3.0)) * 0.6 + vnoise3(p * 9.0) * 0.3 + vnoise3(p * 23.0) * 0.1;
    float mossAmt = smoothstep(0.55, 0.6, mossN + 0.15 * exp(-p.y * 0.6) + (part < 0.5 ? 0.08 : 0.0) - 0.08 * groove);
    // moss glows in bands that run up the trunks with the spectrum, and flares on every beat
    float flare = exp(-beatAge * 3.0) * fx_punch;
    vec3 moss = mossC * (0.5 + 1.4 * specWrap(p.y * 0.08 + p.z * 0.02 - atime * 0.2) * level + 0.6 * flare);
    vec3 col = mix(bark, moss, mossAmt);
    if (part < 0.5) {
        // grassy ground: dense blade streaks, darker soil between clumps, glowing spores
        float blades = vnoise3(vec3(p.x * 70.0, p.y * 6.0, p.z * 70.0)) * 0.6 + vnoise3(vec3(p.x * 140.0, 0.0, p.z * 140.0)) * 0.4;
        float clump = vnoise3(vec3(p.xz * 3.0, 1.7));
        vec3 soil = mix(vec3(0.03, 0.02, 0.03), skyC * 0.15, 0.3);
        vec3 grass = mossC * mix(0.25, 1.1, blades) * (0.55 + 0.5 * clamp(n.y, 0.0, 1.0));
        col = mix(soil, grass, smoothstep(0.25, 0.55, clump + 0.3 * blades));
        float spore = step(0.985, hash2(floor(p.xz * 25.0)));
        col += eyeC * spore * (0.4 + 1.6 * high * level) * (0.5 + 0.5 * sin(atime * 3.0 + p.x * 40.0));
    }
    if (part > 0.5 && part < 1.5 && p.y < 40.0) {
        // many eyes on every trunk, at different heights, all turned towards the path
        float a0 = cell.x > 0.0 ? 3.14159 : 0.0;
        float rad = trunkRadius(p.y);
        for (int k = 0; k < 7; k++) {
            float fk = float(k);
            float h = hash2(cell + 3.3 + fk * 1.7);
            float ye = 1.2 + fk * 2.3 + h * 1.4;
            float ea = a0 + (hash2(cell + fk * 5.1) - 0.5) * 1.6;
            float da = mod(ang - ea + 3.14159, 6.28318) - 3.14159;
            vec2 ep = vec2(da * rad, p.y - ye);
            float size = (0.12 + 0.09 * h) * (1.0 + fk * 0.08) * (1.0 + fx_react * (0.35 * spec(h) * level + 0.15 * bass));   // eyes swell with the music
            float er = length(ep) / size;
            float blink = smoothstep(0.0, 0.1, abs(fract(time * 0.05 + h * 3.0) - 0.5) - 0.44);
            if (er < 1.0 && abs(ep.y) < (1.0 - blink) * size * 1.2) {
                vec3 red = eyeC * (0.9 + 0.8 * level);
                float pupil = 0.3 + 0.2 * bass;
                col = er < pupil ? vec3(0.0) : (er < 0.72 ? red : red * 0.3);
                col += vec3(1.0) * (1.0 - smoothstep(0.0, 0.09, length(ep / size - vec2(-0.25, 0.2)))) * 0.6;   // wet glint
            }
            col += eyeC * exp(-max(length(ep) - size * 0.7, 0.0) * 20.0) * (0.3 + 1.2 * high * level + 0.8 * flare);
        }
    }
    // low glowing mist hugging the roots, purple haze into the distance
    float fogAmt = 1.0 - exp(-t * 0.035);
    col = mix(col, mist, (1.0 - smoothstep(0.0, 1.2, p.y)) * 0.35);
    return mix(col, skyc * 0.7, fogAmt);
}


// ---------------- Cube Cascade: endless nested cube shells around the camera ----------------
// The camera sits at the centre of cube shells nested inside each other, each SHELL_S times
// bigger than the last. Every shell is a hollow cube whose six faces are tiled with small
// cubes with gaps between them, and its twelve edges are thin bars. The shells slowly
// stream outwards, so new ones keep emerging near you: the same view in every direction.
float sdRBox(vec3 p, vec3 b, float r) {
    vec3 q = abs(p) - b + r;
    return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0) - r;
}
vec3 hueShift(float a, vec3 c) {
    const vec3 k = vec3(0.57735);
    float ca = cos(a);
    return c * ca + cross(k, c) * sin(a) + k * dot(k, c) * (1.0 - ca);
}
float g_cubeH;
const float SHELL_S = 2.0;
const float CELLS = 10.0;          // cubes across each face
float g_phase;                     // streaming phase 0..1
// one face's tile nearest p (face f of the shell with half-size R): x its distance, y its hash (the colour)
vec2 faceTile(vec3 p, vec3 ap, vec3 f, float R, float cell, float shellId) {
    vec2 uv = f.x > 0.5 ? p.yz : (f.y > 0.5 ? p.xz : p.xy);
    vec2 id = clamp(floor(uv / cell), vec2(-CELLS * 0.5), vec2(CELLS * 0.5 - 1.0));
    vec2 l2 = uv - (id + 0.5) * cell;
    float side = dot(sign(p), f);
    float h = hash1(dot(id, vec2(1.0, 57.0)) + shellId * 13.1 + dot(f * side, vec3(3.0, 7.0, 11.0)) * 0.71);
    float band = spec(fract(h * 0.93 + 0.03));
    float grow = fx_react * (0.7 * band * level + 0.2 * bass + 0.12 * exp(-beatAge * 4.0) * fx_punch);
    float wv = sin(dot(id, vec2(0.6, 0.45)) + shellId * 1.7 - travel * 3.0);
    // the innermost shells shrink away as they stream in, so nothing crowds the camera (cutting them off left
    // sliced cubes that looked like pyramids)
    float sz = cell * (0.19 + 0.07 * h + 0.025 * sin(atime * (0.6 + h) + h * 40.0) + 0.07 * grow + 0.03 * wv * level) * smoothstep(0.3, 0.75, R);
    float lift = cell * (0.25 + 0.5 * grow);                         // cubes stand slightly inside the face
    vec3 l = vec3(l2, dot(ap, f) - (R - lift));
    // sparse tiles so you can see straight through each shell to the next one out
    return vec2(h > 0.72 && sz > 1e-4 ? sdRBox(l, vec3(sz), sz * 0.16) : 1e9, h);
}
// distance to the tiled faces and edges of the shell with half-size R
float shellDE(vec3 p, float R, float shellId, bool detail) {
    vec3 ap = abs(p);
    float m = max(ap.x, max(ap.y, ap.z));
    // which face: the largest axis
    vec3 f = ap.x >= ap.y && ap.x >= ap.z ? vec3(1, 0, 0) : (ap.y >= ap.z ? vec3(0, 1, 0) : vec3(0, 0, 1));
    float cell = 2.0 * R / CELLS;
    vec2 tile = faceTile(p, ap, f, R, cell, shellId);
    float second = ap.x + ap.y + ap.z - m - min(ap.x, min(ap.y, ap.z));
    if (m - second < cell) {
        // near the seam with the next face: a cube standing on that face reaches over it (judged from this
        // face alone, the part across the seam came out as a pyramid)
        vec3 rest = ap * (1.0 - f);
        vec3 f2 = rest.x >= rest.y && rest.x >= rest.z ? vec3(1, 0, 0) : (rest.y >= rest.z ? vec3(0, 1, 0) : vec3(0, 0, 1));
        vec2 t2 = faceTile(p, ap, f2, R, cell, shellId);
        if (t2.x < tile.x) tile = t2;
    }
    float d = tile.x;
    float h = tile.y;
    // This cell's tile (or its emptiness) says nothing about the next cell's, which stands at its own height and
    // size: stepping past it tore cubes and left holes. A neighbour's tile is at least this cell's border (or the
    // seam where the face changes) plus the gap round its own tile away; outside the layer the tiles live in,
    // the layer itself bounds the step.
    vec2 uv = f.x > 0.5 ? p.yz : (f.y > 0.5 ? p.xz : p.xy);
    vec2 l2 = uv - (clamp(floor(uv / cell), vec2(-CELLS * 0.5), vec2(CELLS * 0.5 - 1.0)) + 0.5) * cell;
    float gMax = fx_react * (0.7 * level + 0.2 * bass + 0.12 * fx_punch);
    float szMax = min(cell * (0.315 + 0.07 * gMax + 0.03 * level), 0.45 * cell);
    float slab = max(m - (R - 0.25 * cell + szMax), (R - cell * (0.25 + 0.5 * gMax) - szMax) - m);
    vec2 toBorder = 0.5 * cell - abs(l2);
    float leave = max(min(min(toBorder.x, toBorder.y), (m - second) * 0.7071), 0.0);
    d = min(d, max(slab, leave + 0.5 * cell - szMax));
    g_cubeH = h;
    // edge bars: where two of the coordinates are near R
    vec3 e = abs(ap - R);
    // bold rounded edges: each shell reads clearly as a cube frame, like the sketch
    // each bar runs only along the shell's own edge, not off to infinity
    vec3 over = max(ap - R, 0.0);
    float bar = min(min(length(vec3(e.x, e.y, over.z)), length(vec3(e.y, e.z, over.x))), length(vec3(e.x, e.z, over.y)))
              - cell * (0.22 + 0.1 * bass * fx_react) * smoothstep(0.3, 0.75, R);
    if (bar < d) { d = bar; g_cubeH = -1.0; }
    return d;
}
float cubeDE(vec3 p) {
    float m = max(max(abs(p.x), abs(p.y)), abs(p.z));
    float k = log(max(m, 1e-3)) / log(SHELL_S) - g_phase;
    float k0 = floor(k), k1 = k0 + 1.0;
    float R0 = pow(SHELL_S, k0 + g_phase), R1 = pow(SHELL_S, k1 + g_phase);
    float h0, d0 = shellDE(p, R0, k0, true);
    h0 = g_cubeH;
    float d1 = shellDE(p, R1, k1, true);
    if (d0 < d1) g_cubeH = h0;
    float d = min(d0, d1);
    return d * 0.8;
}
vec3 cubeCascade(vec2 uv) {
    g_phase = fract(atime * 0.04 + travel * 0.05);                  // shells stream outwards with the music
    // camera at the centre, pivoting: looks around freely in every direction
    vec3 ro = vec3(0.0);
    float yaw = atime * 0.06 + spin * 0.08;
    float pitch = 1.1 * sin(atime * 0.041 + 0.7);
    float roll = 0.4 * sin(atime * 0.033);
    vec3 fw = vec3(sin(yaw) * cos(pitch), sin(pitch), cos(yaw) * cos(pitch));
    vec3 rt = normalize(cross(vec3(0.0, 1.0, 0.0), fw));
    vec3 up = cross(fw, rt);
    rt = rt * cos(roll) + up * sin(roll);
    up = cross(fw, rt);
    float r2 = dot(uv, uv);
    vec2 fe = uv * (1.0 + 0.6 * r2);
    vec3 rd = normalize(fw * 0.7 + fe.x * rt + fe.y * up);
    float t = 0.3;
    bool hit = false;
    float steps = 0.0;
    for (int i = 0; i < 240; i++) {
        if (float(i) >= lodSteps(240.0)) break;
        vec3 p = ro + rd * t;
        float d = cubeDE(p);
        if (d < 0.0012 * t * lodEps()) { hit = true; break; }
        // plain steps: over-relaxing them landed rays inside cubes, whose insides lit up as pyramids
        t += d;
        steps += 1.0;
        if (t > 60.0) break;
    }
    float hs = 0.35 * sin(atime * 0.07 + 0.01 * travel) + 0.3 * (mid - 0.3) * fx_react;
    vec3 haze = hueShift(hs, vec3(0.35, 0.18, 0.85)) * (0.45 + 0.4 * level + 0.25 * bass);
    if (!hit) return haze;
    vec3 p = ro + rd * t;
    vec2 e = vec2(0.001 * t + 1e-4, 0.0);
    const vec2 kk = vec2(1.0, -1.0);
    vec3 n = normalize(kk.xyy * cubeDE(p + kk.xyy * e.x) + kk.yyx * cubeDE(p + kk.yyx * e.x)
                     + kk.yxy * cubeDE(p + kk.yxy * e.x) + kk.xxx * cubeDE(p + kk.xxx * e.x));
    cubeDE(p);
    float h = g_cubeH;
    vec3 blue = hueShift(hs, vec3(0.22, 0.2, 0.95)), violet = hueShift(hs, vec3(0.6, 0.3, 1.0));
    vec3 pink = hueShift(hs, vec3(1.0, 0.35, 0.85)), cyan = hueShift(hs, vec3(0.3, 0.95, 0.95));
    vec3 base = h < 0.0 ? mix(blue, violet, 0.5) * 0.8 : mix(mix(blue, violet, h), pink, smoothstep(0.6, 1.0, h) * 0.6);
    vec3 l1 = normalize(-rd + vec3(0.25, 0.5, 0.0));
    float dif = 0.2 + 0.8 * pow(clamp(dot(n, l1), 0.0, 1.0), 1.5);
    vec3 rf = reflect(rd, n);
    float spc = pow(max(dot(rf, l1), 0.0), 40.0) * 0.7;
    float fres = pow(1.0 - clamp(dot(n, -rd), 0.0, 1.0), 3.0);
    float ao = 1.0 - 0.5 * steps / 240.0;
    vec3 col = base * 0.82 * dif * ao;
    col += mix(vec3(1.0, 0.9, 1.0), cyan, step(0.85, max(h, 0.0))) * spc * (0.6 + 0.9 * level);
    col += mix(pink, violet, 0.3) * fres * 0.45;
    col += base * spec(max(h, 0.0)) * level * 0.5 * fx_react;
    col += cyan * smoothstep(0.92, 1.0, max(h, 0.0)) * (0.3 + 1.5 * high * level);
    // each shell further away fades a little more into the haze, so it reads as endless
    return mix(col, haze, smoothstep(0.0, 1.0, 1.0 - exp(-t * 0.06)) * 0.8);
}

// ---------------- All-Seeing Spiral: an engraved tunnel of curling waves and watching eyes ----------------
// Rings stream towards you out of a giant eye. Each ring is a band covered in watching eyes,
// and between the rings stand circles of curling wave-scrolls that face you and wind up with
// the music. Everything is drawn like an engraving: blue ink hatching on pale paper, lines
// thickening in shadow, with a slightly misprinted red second pass.
const float EY_R = 1.75;      // tunnel wall radius
const float EY_S = 1.35;      // ring spacing along the tunnel
const float EY_N = 9.0;       // curls per ring
const float EY_NE = 7.0;      // eyes per band
const float EY_END = 10.5;    // the giant eye's distance ahead of the camera
float g_eyPart;               // 0 wall, 1 band, 2 curl, 3 eyeball, 4 giant eye
float g_eySpiral;             // curl: position across its spiral arms
float g_eyCurlR;              // curl: distance from its centre / its size
vec2 g_eyEye;                 // eyeball: position on the eye, -1..1 across
float g_eyId;                 // which ring / eye, for variety
float g_eyPulse;              // the beat, 0..1: the eyes swell with it, and the tunnel breathes

float eyTwist(float z) { return 0.22 * sin(z * 0.11 + atime * 0.05) + spin * 0.06; }

// distance to the centre line of an Archimedean spiral that winds `rot` around
float eySpiral(vec2 q, float pitch, float rot, out float k) {
    float r = length(q);
    float a = atan(q.y, q.x) + rot;
    k = r / pitch - a / TAU;
    // no clamp on the arm index: as `rot` grows the arms keep winding in towards the heart
    return abs(k - floor(k + 0.5)) * pitch;
}

float eyDE(vec3 p) {
    float rz = p.z - g_camZ;
    float r = length(p.xy);
    float ang = atan(p.y, p.x) + eyTwist(p.z);
    float swellB = 1.0 + fx_react * (0.06 * bass + 0.05 * g_eyPulse);
    float wall = EY_R * swellB + 0.05 - r;
    float d = wall;
    g_eyPart = 0.0;

    // ---- bands of eyes (one per ring) ----
    float zc = p.z / EY_S;
    float zi = floor(zc);
    float zf = (fract(zc) - 0.5) * EY_S;
    float band = spec(fract(abs(zi) * 0.073 + 0.05));
    float bh = 0.26 + 0.05 * band * level * fx_react;        // band height from the wall
    float h = EY_R * swellB - r;                              // height above the wall
    vec2 bq = vec2(zf, h - bh);
    vec2 bb = abs(bq) - vec2(0.07, bh);
    float dBand = length(max(bb, 0.0)) + min(max(bb.x, bb.y), 0.0) - 0.035;
    if (dBand < d) { d = dBand; g_eyPart = 1.0; g_eyId = zi; }
    // eyeballs bulging from the band's front face
    float seg = TAU / EY_NE;
    float ae = ang + zi * 0.9;
    float ce = floor(ae / seg + 0.5);
    float rm = EY_R * swellB - bh;
    vec3 eq = vec3((ae - ce * seg) * rm, h - bh, zf + 0.085);
    float dEye = length(eq * vec3(0.42, 0.95, 1.5)) - 0.09 * (1.0 + 0.4 * g_eyPulse);
    if (dEye * 0.5 < d) { d = dEye * 0.5; g_eyPart = 3.0; g_eyEye = eq.xy / vec2(0.2, 0.1); g_eyId = zi * 13.0 + ce; }

    // ---- circles of curling scrolls between the bands ----
    float zc2 = zc + 0.5;
    float zi2 = floor(zc2);
    float zf2 = (fract(zc2) - 0.5) * EY_S;
    float segC = TAU / EY_N;
    float ac = ang + mod(zi2, 2.0) * 0.5 * segC;
    float cc = floor(ac / segC + 0.5);
    vec2 u = vec2((ac - cc * segC) * (EY_R - 0.3), h);
    float cb = spec(fract(cc / EY_N * 0.6 + abs(zi2) * 0.05));
    float size = 0.3 * (1.0 + fx_react * 0.18 * cb * level);
    vec2 q = u - vec2(0.0, size + 0.02);
    if (mod(cc + zi2, 2.0) > 0.5) q.x = -q.x;                     // neighbours curl the other way
    float k;
    // the curls wind in and out with the bass (curl: turned on the CPU, so it never jumps), tighter as it swells
    float pitch = size * 0.36 * (1.0 - 0.4 * curlWind);
    float sd = eySpiral(q, pitch, curl + zi2 * 1.7, k);
    float cr = length(q);
    sd = max(sd, cr - size);                                      // only inside its circle
    float th = 0.02 + 0.024 * cr / size;                          // thinner at the heart
    vec2 cq = vec2(sd - th, abs(zf2) - 0.05);
    float dCurl = (length(max(cq, 0.0)) + min(max(cq.x, cq.y), 0.0)) * 0.7;
    if (dCurl < d) { d = dCurl; g_eyPart = 2.0; g_eySpiral = k; g_eyCurlR = cr / size; g_eyId = zi2 * 7.0 + cc; }

    // ---- the giant eye at the end ----
    float dEnd = g_camZ + EY_END - p.z;
    if (dEnd < d) { d = dEnd; g_eyPart = 4.0; }
    return d;
}

// Engraving: parallel lines along `coord`, spaced about `spacing` pixels apart on screen
// (fw = how much coord changes per pixel). Lines thicken with `dark`. Octaves cross-fade
// so the spacing stays even at any distance. `shift` moves them (the red misprint).
float eyHatch(float coord, float fw, float dark, float shift) {
    float P = fw * 4.5 * resolution.y / 1080.0;
    float lv = log2(max(P, 1e-6));
    float o = floor(lv);
    float f = lv - o;
    float res = 0.0;
    for (int j = 0; j < 2; j++) {
        float per = exp2(o + float(j));
        float x = abs(fract(coord / per + shift) - 0.5) * per;
        float hw = clamp(dark, 0.0, 1.0) * 0.5 * per;
        float l = 1.0 - smoothstep(hw - fw * 0.6, hw + fw * 0.6, x);
        res += l * (j == 0 ? 1.0 - f : f);
    }
    return res;
}

// A watching eye drawn in ink. q: -1..1 across the almond. Returns blue ink, red ink,
// and how much of it is the coloured iris (z) and a white highlight (w).
vec4 eyInkEye(vec2 q, float fw, float seed, float big) {
    float blink = smoothstep(0.0, 0.06, abs(fract(time * 0.05 + seed * 0.37) - 0.5) - 0.44);
    float open = 0.55 * (1.0 - blink);
    float lid = abs(q.y) - open * (1.0 - q.x * q.x);
    float lw = fw * (big > 0.5 ? 2.5 : 1.6) + 0.02;
    float blue = 1.0 - smoothstep(lw, lw + fw, abs(lid));
    float red = 1.0 - smoothstep(lw * 0.8, lw * 0.8 + fw, abs(lid + 0.07));
    float inside = step(lid, 0.0) * step(abs(q.x), 1.0);
    vec2 look = 0.25 * vec2(sin(time * 0.31 + seed), 0.4 * sin(time * 0.23 + seed * 2.0));
    vec2 iq = q - look;
    float ir = length(iq);
    float irisR = 0.42;
    float iris = inside * (1.0 - smoothstep(irisR - fw, irisR + fw, ir));
    float pupil = 0.13 + 0.12 * bass * fx_react;
    // iris: radial engraved spokes, darker towards the rim; pupil solid ink
    float spokes = eyHatch(atan(iq.y, iq.x) * 0.25, fw * 0.25 / max(ir, 0.05), 0.35 + 0.5 * smoothstep(0.2, irisR, ir), 0.0);
    blue = max(blue, iris * max(spokes, step(ir, pupil)));
    blue = max(blue, inside * (1.0 - smoothstep(fw, 2.0 * fw, abs(ir - irisR))));
    red = max(red, iris * (1.0 - smoothstep(fw, 2.0 * fw, abs(ir - irisR * 0.7))) * 0.8);
    // sclera: a few lines following the lids
    blue = max(blue, inside * (1.0 - iris) * eyHatch(lid, fw, 0.25, 0.0) * smoothstep(-0.25, -0.02, lid));
    float hl = inside * (1.0 - smoothstep(0.0, 0.06, length(iq - vec2(-0.12, 0.12))));
    float hl2 = inside * (1.0 - smoothstep(0.0, 0.04, length(iq - vec2(0.1, 0.05))));
    return vec4(blue * (1.0 - hl), max(red, hl2) * (1.0 - hl), iris * (1.0 - step(ir, pupil)), hl);
}

vec3 allSeeing(vec2 uv) {
    vec3 ro = vec3(0.0, 0.0, travel * FLY);
    g_camZ = ro.z;
    g_eyPulse = fx_react * clamp(0.6 * beat + 0.5 * exp(-beatAge * 6.0) * fx_punch, 0.0, 1.0);
    vec3 rd = normalize(vec3(uv, 1.0));
    rd.xy = rot(0.05 * sin(time * 0.1)) * rd.xy;
    float pixel = 1.0 / resolution.y;
    float t = 0.05;
    float tNear = 1e9;
    float steps = 0.0;
    float nSteps = lodSteps(170.0);
    for (int i = 0; i < 200; i++) {
        if (float(i) >= nSteps) break;
        float d = eyDE(ro + rd * t);
        if (d < pixel * t * 3.0 && tNear > 1e8) tNear = t;            // first close pass
        if (d < pixel * t * 0.8 * lodEps()) break;
        t += d * 0.8;
        steps += 1.0;
        if (t > EY_END + 2.0) break;
    }
    vec3 p = ro + rd * t;
    vec2 e = vec2(max(pixel * t, 1e-4), 0.0);
    const vec2 kk = vec2(1.0, -1.0);
    vec3 n = normalize(kk.xyy * eyDE(p + kk.xyy * e.x) + kk.yyx * eyDE(p + kk.yyx * e.x)
                     + kk.yxy * eyDE(p + kk.yxy * e.x) + kk.xxx * eyDE(p + kk.xxx * e.x));
    eyDE(p);                                                              // restore this point's data
    float part = g_eyPart;
    float rz = p.z - ro.z;
    float facing = max(abs(dot(n, rd)), 0.12);
    float fw = pixel * t / facing;                                        // world size of one pixel here

    // light from over your shoulder, a beat pulse rolling down the tunnel
    vec3 L = normalize(vec3(-0.45, 0.55, -0.7));
    float dif = clamp(dot(n, L), 0.0, 1.0);
    float ao = 1.0 - 0.6 * steps / nSteps;
    float front = beatAge * 6.0;
    float pulse = exp(-pow((rz - front) * 0.7, 2.0)) * exp(-beatAge * 1.2) * fx_react * fx_punch;
    float light = (0.1 + 0.9 * dif) * ao + 0.5 * pulse;
    // lit faces stay almost bare paper; shade fills in fast (engravings are high-contrast)
    float dark = clamp(0.1 + smoothstep(0.15, 0.85, 1.0 - light) * (0.8 + 0.4 * level), 0.0, 1.0);
    if (part > 2.5 && part < 3.5) dark *= 0.35;

    float r = length(p.xy);
    float ang = atan(p.y, p.x) + eyTwist(p.z);
    float coord, coord2;
    if (part < 0.5) { coord = p.z; coord2 = ang * EY_R; }                       // rings round the tube
    else if (part < 1.5) { coord = abs(n.z) > 0.5 ? r : p.z; coord2 = ang * r; }   // band face / band rim
    else if (part < 2.5) { coord = g_eySpiral * 0.11; coord2 = r; }                 // lines follow the curl
    else { coord = r; coord2 = ang * r; }

    float blue = eyHatch(coord, fw, dark, 0.0);
    float red = eyHatch(coord, fw, dark * 0.85, 0.35) * 0.9;
    blue = max(blue, eyHatch(coord2, fw, (dark - 0.55) * 1.6, 0.0));               // cross-hatch in deep shade
    float glowIris = 0.0;
    vec3 irisCol = pal(0.55 + 0.3 * specWrap(g_eyId * 0.07));
    if (part > 2.5 && part < 3.5) {                                           // the small watching eyes
        vec4 ink = eyInkEye(g_eyEye, fw / 0.1, g_eyId, 0.0);
        blue = mix(blue * 0.3, 1.0, ink.x);
        red = max(red * 0.3, ink.y);
        glowIris = ink.z;
    } else if (part > 3.5) {                                                  // the giant eye at the end
        vec2 q = rot(-eyTwist(p.z)) * p.xy / vec2(1.25, 1.25) / (1.0 + 0.2 * g_eyPulse);   // it swells on the beat
        vec4 ink = eyInkEye(q, fw / 1.25, 0.0, 1.0);
        float outside = smoothstep(0.0, 0.02, abs(q.y) - 0.55 * (1.0 - q.x * q.x)) + step(1.0, abs(q.x));
        // around the eye: faint rings, the same pale paper the far tunnel fades into
        float rings = eyHatch(length(q) * 2.0, fw * 2.0 / 1.25, 0.12 + 0.15 * specWrap(length(q) * 0.5 - time * 0.05), 0.0);
        blue = mix(ink.x, rings * 0.5, clamp(outside, 0.0, 1.0));
        red = mix(ink.y, 0.0, clamp(outside, 0.0, 1.0));
        glowIris = ink.z;
    }
    // silhouettes: rays that skimmed past something nearer before landing here
    if (tNear < t * 0.93 - 0.03) { blue = max(blue, 0.85); red = max(red, 0.4); }

    vec3 paper = mix(vec3(0.97, 0.96, 1.0), pal(0.15) * 0.3 + vec3(0.72, 0.68, 0.95), 0.25 + 0.25 * (1.0 - vibeBright));
    vec3 blueInk = mix(vec3(0.1, 0.14, 0.82), pal(0.62) * vec3(0.4, 0.5, 1.0), 0.25);
    vec3 redInk = mix(vec3(0.88, 0.1, 0.18), pal(0.02) * vec3(1.0, 0.4, 0.5), 0.2);
    vec3 col = paper * (1.0 + 0.4 * pulse);
    col = mix(col, redInk, clamp(red, 0.0, 1.0) * 0.8);
    col = mix(col, blueInk, clamp(blue, 0.0, 1.0));
    col = mix(col, irisCol * (0.7 + 0.9 * high * level) + 0.15, glowIris * (0.35 + 0.5 * level) * fx_react);
    // Far rings fade completely into the paper before they reach the giant eye's wall,
    // so new rings grow out of the haze instead of popping out of the wall.
    if (part < 3.5) col = mix(col, paper, smoothstep(EY_END - 6.0, EY_END - 0.4, rz));
    return col;
}

// ---------------- dispatch ----------------
float g_zoom = 1.0;   // >1 dives the view into the centre (used for the fall between scenes)
vec3 sceneColor(int id, vec4 seed) {
    vec2 fc = 0.5 * resolution + (gl_FragCoord.xy - 0.5 * resolution) / g_zoom;
    if (id == 6) return journey((fc - 0.5 * resolution) / resolution.y);
    if (id == 7) return netOfBeing((fc - 0.5 * resolution) / resolution.y);
    if (id == 8) return cosmicOcean((fc - 0.5 * resolution) / resolution.y);
    if (id == 9) return watchingWoods((fc - 0.5 * resolution) / resolution.y);
    if (id == 10) return cubeCascade((fc - 0.5 * resolution) / resolution.y);
    if (id == 11) return allSeeing((fc - 0.5 * resolution) / resolution.y);
    if (id >= 3) return tunnel(id, (fc - 0.5 * resolution) / resolution.y, seed);
    int n = lodBias > 0.9 ? max(1, aa - 1) : aa;
    vec3 acc = vec3(0.0);
    for (int j = 0; j < n; j++) {
        for (int i = 0; i < n; i++) {
            vec2 o = (vec2(float(i), float(j)) + 0.5) / float(n) - 0.5;
            acc += falling(id, (fc + o - 0.5 * resolution) / resolution.y, seed);
        }
    }
    return acc / float(n * n);
}

void main() {
    // Fall into the next scene: it opens as a portal at the vanishing point and swells
    // outwards faster and faster until it fills the screen, while the camera accelerates.
    // Each pixel only renders the scene(s) it can actually show, so the transition costs
    // little more than a single scene. (One sceneColor call site: compiled once, not 4x.)
    vec3 col = vec3(0.0);
    float m = 0.0, zoomA = 1.0, zoomB = 1.0, rimGlow = 0.0;
    if (mixAB > 0.0) {
        vec2 pp = (gl_FragCoord.xy - 0.5 * resolution) / resolution.y;
        float grow = smoothstep(0.25, 1.0, mixAB);
        float rad = grow * grow * 1.3;                                  // accelerating opening
        float edge = 0.04 + 0.12 * grow;
        m = 1.0 - smoothstep(rad - edge, rad + edge, length(pp) * (1.0 + 0.15 * sin(atan(pp.y, pp.x) * 6.0 + time)));
        // the old scene's view plunges into the centre, faster and faster (only the view moves)
        zoomA = 1.0 + 6.0 * grow * grow * grow;
        // the new scene starts as a wide, far-away view inside the opening and settles to normal
        zoomB = mix(0.35, 1.0, grow * grow);
        rimGlow = exp(-pow((length(pp) - rad) / (edge + 0.01), 2.0)) * step(0.01, grow) * (1.0 - grow);
    }
    for (int k = 0; k < 2 * ONE; k++) {
        float wk = k == 0 ? (m >= 0.999 ? 0.0 : 1.0 - (m > 0.001 ? m : 0.0)) : (m > 0.001 ? m : 0.0);
        if (wk <= 0.0) continue;
        g_zoom = k == 0 ? zoomA : zoomB;
        col += wk * sceneColor(k == 0 ? sceneA : sceneB, k == 0 ? seedA : seedB);
    }
    g_zoom = 1.0;
    col += pal(0.6 + time * 0.05) * rimGlow * 0.6;                      // a glowing rim around the opening

    // crisp oscilloscope ring traced by the live waveform
    if (fx_scope > 0.0) {
        vec2 p = (gl_FragCoord.xy - 0.5 * resolution) / resolution.y;
        float r = length(p);
        float around = fract(atan(p.y, p.x) / TAU + 0.5);
        float ringR = 0.3 + 0.04 * bass;
        float rr = ringR + wave(around) * 0.08 * (0.3 + level);
        float px = 1.0 / resolution.y;
        float line = 1.0 - smoothstep(0.6 * px, 2.0 * px, abs(r - rr));
        col = mix(col, pal(around + time * 0.05) * 1.3, line * fx_scope);
    }



    // Temporal smoothing: a light blend with the last frame hides the flicker of
    // detail smaller than a pixel (strongest while flying through the tunnels).
    vec3 prev = texture(texPrev, v_uv).rgb;
    col = mix(col, prev, temporal * (sceneA == 11 ? 0.25 : sceneA >= 3 ? 1.0 : 0.5));   // ink lines would smear
    // afterglow: keep the brighter of now and a faded last frame
    if (sceneA < 3) col = max(col, prev * fx_trails);       // 3D flights would smear into streaks

    frag = vec4(clamp(col, 0.0, 1.0), 1.0);
}
"""

PRESENT = """
#version 330
uniform sampler2D scene;
uniform sampler2D hud;
uniform vec2 resolution;
uniform vec2 hudSize;
uniform float hudOn;
uniform float uiScale;
uniform float bands[32];
uniform float high;
uniform float beat;
uniform float level;
uniform float fx_aberr;
uniform float fx_sat;
uniform float alive;     // 1 while music plays, eases to 0 in silence
uniform float colorAmt; // how much colour the music is providing right now
uniform sampler2D card;  // controls card, premultiplied alpha
uniform vec2 cardSize;
uniform float cardAlpha;
in vec2 v_uv;
out vec4 frag;

uniform vec4 musHue[2];      // colour from music (musiccolour.py): each colour band's hue, bass first
uniform vec4 musW[2];        // how much of the picture each takes (sums to 1)
uniform vec4 musLevel[2];    // each one's level, eased
uniform float musAmt;        // 0 = off .. 1 = the music sets the colour
vec3 musRgb2hsv(vec3 c) {
    vec4 K = vec4(0.0, -1.0 / 3.0, 2.0 / 3.0, -1.0);
    vec4 p = mix(vec4(c.bg, K.wz), vec4(c.gb, K.xy), step(c.b, c.g));
    vec4 q = mix(vec4(p.xyw, c.r), vec4(c.r, p.yzx), step(p.x, c.r));
    float d = q.x - min(q.w, q.y);
    return vec3(abs(q.z + (q.w - q.y) / (6.0 * d + 1e-10)), d / (q.x + 1e-10), q.x);
}
vec3 musHsv2rgb(vec3 c) {
    vec3 p = abs(fract(c.xxx + vec3(1.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - 3.0);
    return c.z * mix(vec3(1.0), clamp(p - 1.0, 0.0, 1.0), c.y);
}
vec3 musicColour(vec3 col) {
    if (musAmt <= 0.0) return col;
    vec3 hsv = musRgb2hsv(col);
    // where this pixel sits along the bands: its own hue and brightness pick the spot; a loud band covers more
    float u = fract(hsv.x + hsv.z * 0.35);
    vec2 dir = vec2(0.0);
    float lv = 0.0, gs = 0.0, acc = 0.0;
    for (int i = 0; i < 2; i++) {
        for (int j = 0; j < 4; j++) {
            float w = musW[i][j];
            float d = abs(u - (acc + 0.5 * w));
            d = min(d, 1.0 - d);
            acc += w;
            float z = d / (0.5 * w + 0.04);
            float g = (w + 0.001) * exp(-z * z);
            float h = 6.2831853 * musHue[i][j];
            dir += g * vec2(cos(h), sin(h));
            lv += g * musLevel[i][j];
            gs += g;
        }
    }
    lv /= max(gs, 1e-6);
    float hue = fract(atan(dir.y, dir.x) / 6.2831853 + 1.0);
    vec3 m = musHsv2rgb(vec3(hue, mix(hsv.y, 0.5 + 0.45 * lv, 0.8), clamp(hsv.z * (0.85 + 0.3 * lv), 0.0, 1.0)));
    return mix(col, m, musAmt);
}

void main() {
    vec2 c = v_uv - 0.5;
    vec2 d = c * fx_aberr * (0.002 + 0.008 * high);
    vec3 col = vec3(texture(scene, v_uv + d).r, texture(scene, v_uv).g, texture(scene, v_uv - d).b);
    col = musicColour(col);   // each EQ band's hue, by how loud it is (musiccolour.py)
    float l = dot(col, vec3(0.299, 0.587, 0.114));
    col = mix(vec3(l), col, fx_sat * (0.9 + 0.25 * level) * colorAmt);   // the music provides the colour
    col *= mix(0.65, 1.05, colorAmt) * (1.0 - dot(c, c) * 0.55);

    if (cardAlpha > 0.0) {
        // bottom-centre, drifting down slightly as it dissolves
        vec2 px = v_uv * resolution;
        vec2 origin = vec2(0.5 * (resolution.x - cardSize.x), (36.0 - 12.0 * (1.0 - cardAlpha)) * uiScale);
        vec2 q = px - origin;
        if (q.x >= 0.0 && q.y >= 0.0 && q.x < cardSize.x && q.y < cardSize.y) {
            vec4 k = texture(card, q / cardSize) * cardAlpha;
            col = col * (1.0 - k.a) + k.rgb;
        }
    }

    if (hudOn > 0.5) {
        vec2 px = v_uv * resolution;
        float pad = 24.0 * uiScale;
        vec2 tl = vec2(px.x - pad, resolution.y - px.y - pad);
        if (tl.x >= 0.0 && tl.y >= 0.0 && tl.x < hudSize.x && tl.y < hudSize.y) {
            vec4 h = texture(hud, vec2(tl.x / hudSize.x, 1.0 - tl.y / hudSize.y));
            col = col * (1.0 - h.a) + h.rgb;
        }
        float bw = 9.0 * uiScale;
        float bh = 110.0 * uiScale;
        vec2 b = px - vec2(pad, pad);
        if (b.x >= 0.0 && b.y >= 0.0 && b.x < bw * 32.0 && b.y < bh) {
            int i = int(b.x / bw);
            float inBar = step(1.5 * uiScale, mod(b.x, bw));
            col = mix(col, col * 0.4, 0.6);
            if (b.y < bands[i] * bh && inBar > 0.5) {
                col = mix(vec3(0.3, 0.9, 1.0), vec3(1.0, 0.3, 0.8), b.y / bh);
            }
        }
    }
    frag = vec4(clamp(col, 0.0, 1.0), 1.0);
}
"""
