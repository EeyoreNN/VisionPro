#!/usr/bin/env python3
"""Generate every 3D and image asset for Aurora Overlook.

Everything is procedural, so the whole world can be rebuilt (or remixed) from
this one script -- no Blender or Reality Composer Pro required:

    cd projects/aurora-overlook
    python3 -m venv .venv && .venv/bin/pip install -r ../../shared/python/requirements.txt
    .venv/bin/python tools/build_assets.py

Outputs land in site/assets/:

    overlook.usdz        the immersive website environment (real-world meters,
                         origin at the visitor's feet, Y up, -Z = north)
    diorama.usdz         a tabletop floating-island version for the inline
                         <model> on the page
    overlook-light.exr   360 degree HDR lighting map for `environmentmap`
    viewpoints.json      standing spots the page can teleport visitors to

Coordinate conventions follow the visionOS 27 immersive <model> rules: 1 unit =
1 meter, Y up, right handed, and the immersive origin is where the visitor
stands. Lighting is baked into emissive textures (Apple's guidance for web
environments) so the scene looks the same everywhere and renders cheaply.
"""

from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image
from pxr import Gf, UsdGeom

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parents[1] / "shared" / "python"))

from usdkit import (  # noqa: E402  (shared helpers for every project in the repo)
    MeshBuilder as _MeshBuilder, Palette, animate_rotate_y, animate_translate, check, compact,
    copy_textures, define_builder_mesh, define_mesh, direction, fbm, hex_lin, linear_to_srgb,
    make_material, new_stage, package, sky_dome, smoothstep, splat_stars, to_image, value_noise3,
    write_exr,
)

OUT = ROOT / "site" / "assets"

RNG_SEED = 27

# World layout (meters). -Z is north, +X is east.
TERRAIN_HALF = 96.0
TERRAIN_RADIUS = 95.0
SKY_RADIUS = 110.0
DECK_FLOOR = -0.12          # meadow height around the deck; deck top is y = 0
LAKE_CENTER = (0.0, -27.0)
LAKE_RADII = (15.0, 11.0)
VALLEY_DROP = 6.0          # the deck sits on a bluff this far above the valley floor
WATER_Y = -0.35 - VALLEY_DROP
RIDGE_CENTER = (16.0, 30.0)
JETTY_Z = -25.0
JETTY_X = (-21.5, -12.2)
JETTY_TOP = 0.05 - VALLEY_DROP

MOON_AZ, MOON_EL = math.radians(135.0), math.radians(30.0)
AURORA_AZ = 0.0             # the aurora hangs over the northern horizon

ANIM_SECONDS = 30
FPS = 24


MOON_DIR = direction(np.float64(MOON_AZ), np.float64(MOON_EL))
AURORA_DIR = direction(np.float64(AURORA_AZ), np.float64(math.radians(25)))


# --------------------------------------------------------------------------
# Sky
# --------------------------------------------------------------------------

def aurora_curtain(az, el, *, center, width, base, wave, seed, strength, streak_freq):
    """One aurora curtain painted onto the sky as a function of azimuth/elevation."""
    rel = az - center
    window = np.exp(-((rel / width) ** 4))
    e0 = base + wave * np.sin(2.3 * rel + seed) + 0.35 * wave * value_noise3(rel * 3.0, 0.0, 0.0, seed)
    x = el - e0
    bottom = np.exp(-((np.minimum(x, 0.0) / 0.010) ** 2))
    top = np.exp(-np.maximum(x, 0.0) / 0.20)
    profile = bottom * top
    streaks = 0.30 + 0.70 * np.clip(0.5 + 0.5 * value_noise3(rel * streak_freq, 0.0, 3.0, seed + 5), 0, 1) ** 2
    folds = 0.55 + 0.45 * np.clip(0.5 + 0.5 * value_noise3(rel * 5.0, 0.0, 9.0, seed + 9), 0, 1)
    intensity = strength * window * profile * streaks * folds
    t = smoothstep(0.02, 0.28, x)
    green = np.array([0.08, 1.0, 0.42])
    teal = np.array([0.05, 0.75, 0.70])
    violet = np.array([0.55, 0.12, 0.75])
    color = green * (1 - t)[..., None] + violet * t[..., None]
    color = color * 0.85 + teal * 0.15 * (1 - t)[..., None]
    return intensity[..., None] * color


def sky_radiance(az, el):
    """Linear HDR radiance of the night sky for azimuth/elevation grids."""
    dirs = direction(az, el)
    t = np.clip(el / (np.pi / 2), 0, 1) ** 0.45
    horizon = np.array([0.020, 0.034, 0.060])
    zenith = np.array([0.0025, 0.0045, 0.013])
    col = horizon * (1 - t)[..., None] + zenith * t[..., None]
    below = el < 0
    col[below] = np.array([0.008, 0.012, 0.018])

    # Faint green airglow near the horizon, strongest toward the aurora.
    glow = np.exp(-((az - AURORA_AZ) / 1.1) ** 2) * np.exp(-((el - 0.10) ** 2) / 0.02)
    col += glow[..., None] * np.array([0.006, 0.030, 0.020])

    # Milky Way: a tilted great circle with dusty noise and dark lanes.
    nb = np.array([0.35, 0.55, 0.76])
    nb = nb / np.linalg.norm(nb)
    d = dirs @ nb
    n = fbm(dirs[..., 0] * 3, dirs[..., 1] * 3, 4, 3, dirs[..., 2] * 3)
    mw = np.exp(-((d + 0.05 * n) / 0.13) ** 2) * (0.55 + 0.45 * n)
    lanes = 1 - 0.65 * np.exp(-((d - 0.02 + 0.03 * fbm(dirs[..., 0] * 7, dirs[..., 1] * 7, 3, 8, dirs[..., 2] * 7)) / 0.025) ** 2)
    col += (mw * lanes * np.clip(el * 8, 0, 1))[..., None] * np.array([0.016, 0.017, 0.024])

    # Aurora curtains over the northern mountains.
    col += aurora_curtain(az, el, center=-0.15, width=0.95, base=0.26, wave=0.06, seed=1, strength=0.40, streak_freq=140)
    col += aurora_curtain(az, el, center=0.55, width=0.55, base=0.40, wave=0.05, seed=4, strength=0.22, streak_freq=180)
    col += aurora_curtain(az, el, center=-0.85, width=0.45, base=0.34, wave=0.04, seed=7, strength=0.16, streak_freq=160)

    # Moon with a soft halo.
    ang = np.arccos(np.clip(dirs @ MOON_DIR, -1, 1))
    disk_r = 0.011
    disk = smoothstep(disk_r, disk_r * 0.85, ang)
    near = disk > 0
    craters = 0.82 + 0.18 * fbm(dirs[near][:, 0] * 900, dirs[near][:, 1] * 900, 3, 12, dirs[near][:, 2] * 900)
    disk[near] *= craters
    col += disk[..., None] * np.array([3.2, 3.1, 2.9])
    halo = 0.10 * np.exp(-ang / 0.035) + 0.022 * np.exp(-ang / 0.25)
    col += halo[..., None] * np.array([0.75, 0.85, 1.0])
    return col


def build_sky(width):
    h = width // 2
    u = (np.arange(width) + 0.5) / width
    v = 1.0 - (np.arange(h) + 0.5) / h
    az = (u - 0.5) * 2 * np.pi
    el = (v - 0.5) * np.pi
    AZ, EL = np.meshgrid(az, el)
    img = sky_radiance(AZ, EL)
    rng = np.random.default_rng(RNG_SEED)
    scale = width / 4096
    splat_stars(img, rng, int(7000 * scale ** 0.5))
    splat_stars(img, rng, int(5000 * scale ** 0.5), band_normal=(0.35, 0.55, 0.76))
    return img


# --------------------------------------------------------------------------
# Terrain
# --------------------------------------------------------------------------

def lake_distance(x, z):
    return np.sqrt(((x - LAKE_CENTER[0]) / LAKE_RADII[0]) ** 2 + ((z - LAKE_CENTER[1]) / LAKE_RADII[1]) ** 2)


def natural_height(x, z):
    x, z = np.asarray(x, float), np.asarray(z, float)
    r = np.hypot(x, z)
    n1 = fbm(x / 55, z / 55, 5, 11)
    ridged = 1 - np.abs(fbm(x / 26, z / 26, 5, 23))
    mountains = smoothstep(26, 78, r) * (12 + 30 * ridged ** 1.6 + 9 * n1)
    hills = smoothstep(6, 24, r) * (1.8 + 2.2 * fbm(x / 13, z / 13, 4, 5))
    ridge_hill = 12.5 * np.exp(-((x - RIDGE_CENTER[0]) ** 2 + (z - RIDGE_CENTER[1]) ** 2) / (2 * 9.5 ** 2))
    return mountains + hills + ridge_hill + 0.18 * fbm(x / 2.5, z / 2.5, 3, 7) - VALLEY_DROP


LEDGE_TOP = float(natural_height(np.array(RIDGE_CENTER[0]), np.array(RIDGE_CENTER[1]))) + 0.05

FLATTEN = [
    # (x, z, target height, inner radius)
    (0.0, 0.0, DECK_FLOOR, 4.2),
    (-23.5, JETTY_Z, JETTY_TOP - 0.03, 3.0),
    (RIDGE_CENTER[0], RIDGE_CENTER[1], LEDGE_TOP - 0.05, 2.6),
]


def terrain_height(x, z):
    x, z = np.asarray(x, float), np.asarray(z, float)
    h = natural_height(x, z)
    for fx, fz, target, rad in FLATTEN:
        w = 1 - smoothstep(rad, rad * 2.4, np.hypot(x - fx, z - fz))
        h = h * (1 - w) + target * w
    d = lake_distance(x, z)
    lake = 1 - smoothstep(0.82, 1.28, d)
    floor = WATER_Y - 1.55 + 1.0 * d
    return h * (1 - lake) + floor * lake


def terrain_normals(x, z, eps=0.25):
    hx = (terrain_height(x + eps, z) - terrain_height(x - eps, z)) / (2 * eps)
    hz = (terrain_height(x, z + eps) - terrain_height(x, z - eps)) / (2 * eps)
    n = np.stack([-hx, np.ones_like(hx), -hz], axis=-1)
    return n / np.linalg.norm(n, axis=-1, keepdims=True)


class HeightGrid:
    """Terrain heights cached on a regular grid, sampled bilinearly."""

    def __init__(self, n=768):
        self.n = n
        self.xs = np.linspace(-TERRAIN_HALF * 1.6, TERRAIN_HALF * 1.6, n)
        X, Z = np.meshgrid(self.xs, self.xs)
        self.h = terrain_height(X, Z)

    def __call__(self, x, z):
        span = self.xs[-1] - self.xs[0]
        fx = np.clip((x - self.xs[0]) / span * (self.n - 1), 0, self.n - 1.001)
        fz = np.clip((z - self.xs[0]) / span * (self.n - 1), 0, self.n - 1.001)
        ix, iz = fx.astype(int), fz.astype(int)
        tx, tz = fx - ix, fz - iz
        h = self.h
        return (h[iz, ix] * (1 - tx) * (1 - tz) + h[iz, ix + 1] * tx * (1 - tz)
                + h[iz + 1, ix] * (1 - tx) * tz + h[iz + 1, ix + 1] * tx * tz)


def build_terrain_texture(size, trees):
    """Top-down albedo x baked moonlight, in linear radiance."""
    c = (np.arange(size) + 0.5) / size
    xs = -TERRAIN_HALF + c * 2 * TERRAIN_HALF
    zs = -TERRAIN_HALF + (1 - c) * 2 * TERRAIN_HALF   # image row 0 is +v = +z edge
    X, Z = np.meshgrid(xs, zs)
    H = terrain_height(X, Z)
    texel = 2 * TERRAIN_HALF / size
    dhdz_rows, dhdx = np.gradient(H, texel)
    dhdz = -dhdz_rows                                  # rows run toward -z
    N = np.stack([-dhdx, np.ones_like(H), -dhdz], axis=-1)
    N /= np.linalg.norm(N, axis=-1, keepdims=True)
    slope = 1 - N[..., 1]
    grid = HeightGrid()
    d = lake_distance(X, Z)
    n_big = fbm(X / 9, Z / 9, 4, 31)
    n_fine = fbm(X / 1.3, Z / 1.3, 3, 37)

    grass = hex_lin("#3f5a36")
    grass2 = hex_lin("#566b3a")
    forest = hex_lin("#26392c")
    rock = hex_lin("#6f6c69")
    rock2 = hex_lin("#8a857c")
    snow = hex_lin("#e8eef6")
    sand = hex_lin("#8a7d62")
    bed = hex_lin("#1d2c2c")

    t = np.clip(0.5 + 0.5 * n_big + 0.25 * n_fine, 0, 1)[..., None]
    albedo = grass * (1 - t) + grass2 * t
    forest_mask = smoothstep(-4.0, -1.0, H) * (1 - smoothstep(8.0, 14.0, H)) * np.clip(0.6 + n_big, 0, 1)
    albedo = albedo * (1 - forest_mask[..., None]) + forest * forest_mask[..., None]
    rock_t = np.clip(0.5 + 0.5 * n_fine, 0, 1)[..., None]
    rock_col = rock * (1 - rock_t) + rock2 * rock_t
    rock_mask = smoothstep(0.22, 0.42, slope + 0.08 * n_fine) + smoothstep(18, 28, H) * 0.8
    rock_mask = np.clip(rock_mask, 0, 1)
    albedo = albedo * (1 - rock_mask[..., None]) + rock_col * rock_mask[..., None]
    snow_mask = smoothstep(24 + 5 * n_big, 31 + 5 * n_big, H) * (1 - smoothstep(0.45, 0.7, slope))
    albedo = albedo * (1 - snow_mask[..., None]) + snow * snow_mask[..., None]
    shore = smoothstep(0.86, 0.98, d) * (1 - smoothstep(1.05, 1.16, d + 0.05 * n_fine))
    albedo = albedo * (1 - shore[..., None]) + sand * shore[..., None]
    wet = 1 - smoothstep(0.88, 0.98, d)
    albedo = albedo * (1 - wet[..., None]) + bed * wet[..., None]

    # Moon shadows: march each texel toward the moon across the heightfield.
    shadow = np.ones_like(H)
    step = 1.2
    horiz = np.array([MOON_DIR[0], MOON_DIR[2]])
    horiz = horiz / np.linalg.norm(horiz)
    rise = math.tan(MOON_EL)
    for i in range(1, 70):
        dist = i * step
        hx = X + horiz[0] * dist
        hz = Z + horiz[1] * dist
        occluder = grid(hx, hz)
        shadow = np.minimum(shadow, np.clip(1 - (occluder - (H + dist * rise)) / 1.5, 0, 1))

    # Soft darkening under trees (fake ambient occlusion).
    ao = np.ones_like(H)
    px_per_m = size / (2 * TERRAIN_HALF)
    for tx, tz, th in trees:
        cx = (tx + TERRAIN_HALF) * px_per_m
        cy = (TERRAIN_HALF - tz) * px_per_m
        rad = 0.35 * th * px_per_m
        x0, x1 = int(max(cx - rad * 2, 0)), int(min(cx + rad * 2 + 1, size))
        y0, y1 = int(max(cy - rad * 2, 0)), int(min(cy + rad * 2 + 1, size))
        if x0 >= x1 or y0 >= y1:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        dd = np.hypot(xx - cx, yy - cy) / rad
        ao[y0:y1, x0:x1] *= 1 - 0.55 * np.exp(-dd ** 2)

    ndotm = np.clip(N @ MOON_DIR, 0, 1)
    ndota = np.clip(N @ AURORA_DIR, 0, 1)
    light = (
        np.array([0.62, 0.70, 0.86])[None, None] * (ndotm * shadow)[..., None] * 0.55
        + np.array([0.10, 0.13, 0.22])[None, None] * (0.55 + 0.45 * N[..., 1])[..., None]
        + np.array([0.04, 0.20, 0.12])[None, None] * ndota[..., None] * 0.9
    )
    return albedo * light * ao[..., None]


def trees_layout(rng):
    trees = []
    attempts = 0
    while len(trees) < 420 and attempts < 20000:
        attempts += 1
        x, z = rng.uniform(-70, 70, size=2)
        r = math.hypot(x, z)
        if r < 8.5 or r > 68:
            continue
        if lake_distance(np.array(x), np.array(z)) < 1.22:
            continue
        # Keep the deck's view of the lake and the jetty clear.
        az = math.degrees(math.atan2(x, -z))
        if r < 42 and -45 < az < 40:
            continue
        if math.hypot(x - (-23.5), z - JETTY_Z) < 6:
            continue
        if math.hypot(x - RIDGE_CENTER[0], z - RIDGE_CENTER[1]) < 9:
            continue
        # Keep the ledge's line of sight toward the lake open.
        if -2 < z < 30 and abs(x - 16 * z / 30) < 8:
            continue
        h = float(terrain_height(np.array(x), np.array(z)))
        n = terrain_normals(np.array([x]), np.array([z]))[0]
        if h > 12 or n[1] < 0.8:
            continue
        density = 0.5 + 0.5 * float(fbm(np.array(x / 18), np.array(z / 18), 3, 41))
        if rng.uniform() > density ** 1.5:
            continue
        height = rng.uniform(4.5, 9.5) * (0.8 + 0.4 * density)
        trees.append((x, z, height))
    return trees


# --------------------------------------------------------------------------
# Low-poly props baked into a palette texture
# --------------------------------------------------------------------------

PALETTE = {
    # name: (sRGB hex, glows)
    "pine": ("#2f5a45", False),
    "pine_dark": ("#24473a", False),
    "pine_snow": ("#c9d6df", False),
    "trunk": ("#5a4232", False),
    "deck": ("#a57a52", False),
    "deck_dark": ("#7e5a3c", False),
    "wood_post": ("#6b4c34", False),
    "brass": ("#c79a4b", False),
    "iron": ("#3a3f47", False),
    "stone": ("#7d7b78", False),
    "stone_dark": ("#5d5b5a", False),
    "lantern": ("#ffcf7a", True),
    "firefly": ("#d8ff9a", True),
    "cloth": ("#3f6f86", False),
    "moss": ("#48613a", False),
    "white": ("#ffffff", True),
}
PALETTE_TEX = Palette(PALETTE)


def aurora_light(n):
    """Moonlight from the south-east plus a green bounce from the aurora."""
    return 0.30 + 0.62 * max(0.0, float(n @ MOON_DIR)) + 0.28 * max(0.0, float(n @ AURORA_DIR)) + 0.16 * n[1]


def MeshBuilder():
    return _MeshBuilder(PALETTE_TEX, aurora_light)


def build_tree(mb, x, y, z, height, rng):
    lean = rng.normal(0, 0.03, size=2)
    top = np.array([x + lean[0] * height, y + height, z + lean[1] * height])
    base = np.array([x, y - 0.3, z])
    mb.cylinder(base, base + (top - base) * 0.3, 0.16, 0.12, 5, "trunk", caps=False)
    tiers = 3
    color = "pine" if rng.uniform() < 0.6 else "pine_dark"
    twist = rng.uniform(0, 1)
    for k in range(tiers):
        f0 = 0.18 + k * 0.22
        f1 = f0 + 0.45 + (0.1 if k == tiers - 1 else 0)
        p0 = base + (top - base) * f0
        p1 = base + (top - base) * min(f1, 1.0)
        radius = height * (0.26 - 0.06 * k) * rng.uniform(0.9, 1.1)
        tier_color = "pine_snow" if (k == tiers - 1 and y > 6) else color
        mb.cylinder(p0, p1, radius, 0.0, 7, tier_color, caps=True, twist=twist + k)


def octagon(radius, y, offset=math.pi / 8):
    return [np.array([radius * math.sin(offset + k * math.pi / 4), y, -radius * math.cos(offset + k * math.pi / 4)]) for k in range(8)]


def build_deck(mb):
    """The overlook deck: an octagonal platform whose top is the real floor (y = 0)."""
    top = octagon(3.1, 0.0)
    bot = octagon(3.1, -0.45)
    center = np.array([0.0, 0.0, 0.0])
    for k in range(8):
        j = (k + 1) % 8
        mb.tri(center, top[k], top[j], "deck" if k % 2 else "deck_dark", ref=center - [0, 1, 0], level=5)
        mb.quad(top[k], top[j], bot[j], bot[k], "wood_post", ref=np.array([0, -0.2, 0]))
    # Brass compass inlay with a north arrow.
    inlay = octagon(0.42, 0.006)
    for k in range(8):
        mb.tri(np.array([0, 0.006, 0]), inlay[k], inlay[(k + 1) % 8], "brass", ref=np.array([0, -1, 0]), level=5)
    mb.tri([0, 0.009, -1.25], [-0.16, 0.009, -0.42], [0.16, 0.009, -0.42], "brass", ref=[0, -1, -0.8], level=7)
    mb.tri([0, 0.009, 0.8], [-0.1, 0.009, 0.42], [0.1, 0.009, 0.42], "brass", ref=[0, -1, 0.6], level=3)

    posts = octagon(2.95, 0.0)
    for k, p in enumerate(posts):
        mb.box(p + [0, 0.5, 0], (0.12, 1.0, 0.12), "wood_post", yaw=-(math.pi / 8 + k * math.pi / 4))
    for k in range(8):
        if k == 7:          # leave the north edge open toward the lake
            continue
        a, b = posts[k], posts[(k + 1) % 8]
        for hgt, r in ((0.98, 0.05), (0.52, 0.03)):
            mb.cylinder(a + [0, hgt, 0], b + [0, hgt, 0], r, r, 6, "wood_post", caps=False)
    for k in (7, 0, 3, 4):   # lanterns flanking the opening and at the back
        p = posts[k] + [0, 1.0, 0]
        mb.box(p + [0, 0.02, 0], (0.16, 0.04, 0.16), "iron")
        mb.box(p + [0, 0.14, 0], (0.12, 0.2, 0.12), "lantern")
        mb.cylinder(p + [0, 0.24, 0], p + [0, 0.36, 0], 0.11, 0.0, 4, "iron", twist=math.pi / 4)

    # A brass telescope on a tripod, aimed at the aurora.
    base = np.array([2.0, 0.0, 0.7])
    apex = base + [0, 1.15, 0]
    for k in range(3):
        a = 2 * math.pi * k / 3 + 0.4
        foot = base + [0.5 * math.cos(a), 0.0, 0.5 * math.sin(a)]
        mb.cylinder(foot, apex, 0.025, 0.02, 4, "iron")
    aim = np.array([-0.25, 0.62, -0.74])
    aim /= np.linalg.norm(aim)
    mb.cylinder(apex - aim * 0.55, apex + aim * 0.75, 0.065, 0.085, 10, "brass", cap_color="iron")
    mb.cylinder(apex - aim * 0.75, apex - aim * 0.55, 0.025, 0.03, 6, "iron")
    mb.cylinder(apex + [0, -0.06, 0], apex + [0, 0.05, 0], 0.07, 0.07, 6, "iron")

    # A bench along the back rail.
    back = (posts[3] + posts[4]) / 2 * 0.86
    yaw = math.atan2(back[0], -back[2])
    mb.box(back + [0, 0.44, 0], (1.7, 0.07, 0.45), "deck", yaw=-yaw)
    side = np.array([math.cos(yaw), 0, math.sin(yaw)])
    for s in (-0.7, 0.7):
        mb.box(back + side * s + [0, 0.21, 0], (0.08, 0.42, 0.38), "wood_post", yaw=-yaw)


def build_jetty(mb, rng):
    x0, x1 = JETTY_X
    width = 1.8
    n = int((x1 - x0) / 0.26)
    for i in range(n):
        x = x0 + (i + 0.5) * (x1 - x0) / n
        mb.box((x, JETTY_TOP - 0.03, JETTY_Z), (0.23, 0.06, width), "deck" if i % 2 else "deck_dark")
    for x in np.linspace(x0 + 0.3, x1 - 0.15, 5):
        for s in (-1, 1):
            p = np.array([x, WATER_Y, JETTY_Z + s * (width / 2 - 0.08)])
            mb.cylinder(p + [0, -1.3, 0], p + [0, JETTY_TOP - WATER_Y + 0.06, 0], 0.09, 0.09, 6, "wood_post")
    mb.box(((x0 + x1) / 2, JETTY_TOP - 0.12, JETTY_Z - width / 2 + 0.1), (x1 - x0, 0.12, 0.1), "wood_post")
    mb.box(((x0 + x1) / 2, JETTY_TOP - 0.12, JETTY_Z + width / 2 - 0.1), (x1 - x0, 0.12, 0.1), "wood_post")
    # Lantern pole at the end of the jetty.
    pole = np.array([x1 - 0.2, JETTY_TOP, JETTY_Z + width / 2 - 0.15])
    mb.cylinder(pole, pole + [0, 1.9, 0], 0.04, 0.035, 6, "iron")
    mb.cylinder(pole + [0, 1.9, 0], pole + [0.35, 1.9, 0], 0.025, 0.025, 4, "iron")
    lamp = pole + [0.35, 1.72, 0]
    mb.box(lamp, (0.14, 0.2, 0.14), "lantern")
    mb.cylinder(lamp + [0, 0.1, 0], lamp + [0, 0.22, 0], 0.12, 0.0, 4, "iron", twist=math.pi / 4)
    # A little rowing boat tied up alongside.
    bx, bz = x1 - 2.6, JETTY_Z - width / 2 - 0.85
    hull = [np.array(p, float) for p in (
        (bx - 1.5, WATER_Y + 0.25, bz), (bx - 0.6, WATER_Y + 0.28, bz - 0.55), (bx + 0.8, WATER_Y + 0.28, bz - 0.55),
        (bx + 1.4, WATER_Y + 0.3, bz), (bx + 0.8, WATER_Y + 0.28, bz + 0.55), (bx - 0.6, WATER_Y + 0.28, bz + 0.55))]
    keel = np.array([bx, WATER_Y - 0.12, bz])
    for k in range(6):
        mb.tri(hull[k], hull[(k + 1) % 6], keel, "cloth", ref=keel + [0, 1, 0])
    inner = np.array([bx, WATER_Y + 0.12, bz])
    for k in range(6):
        mb.tri(hull[k], hull[(k + 1) % 6], inner, "deck_dark", ref=inner - [0, 1, 0], level=3)
    mb.box((bx, WATER_Y + 0.2, bz), (0.25, 0.04, 1.0), "deck")


def build_ledge(mb, rng):
    cx, cz = RIDGE_CENTER
    y = LEDGE_TOP
    mb.cylinder([cx, y - 0.9, cz], [cx, y, cz], 2.3, 2.1, 9, "stone", twist=0.3)
    for k in range(5):
        a = 2.4 + k * 0.35
        mb.rock((cx + 2.6 * math.cos(a), y + 0.1, cz + 2.6 * math.sin(a)), rng.uniform(0.35, 0.6), rng, "stone_dark")
    # A small cairn.
    base = np.array([cx + 1.2, y, cz + 1.1])
    for k, r in enumerate((0.32, 0.26, 0.2, 0.14, 0.09)):
        mb.rock(base + [rng.normal(0, 0.02), 0.08 + k * 0.2, rng.normal(0, 0.02)], r, rng, "stone" if k % 2 else "stone_dark", squash=0.55)
    # Prayer-flag style line between two poles.
    p0 = np.array([cx - 1.9, y, cz + 0.9])
    p1 = np.array([cx - 0.4, y, cz + 2.1])
    for p in (p0, p1):
        mb.cylinder(p, p + [0, 1.7, 0], 0.03, 0.025, 5, "wood_post")
    colors = ["cloth", "lantern", "moss", "brass", "cloth", "pine_snow"]
    for k in range(6):
        t = (k + 0.5) / 6
        a = p0 + (p1 - p0) * t + [0, 1.62 - 0.25 * math.sin(math.pi * t), 0]
        mb.quad(a, a + (p1 - p0) * 0.1, a + (p1 - p0) * 0.1 + [0, -0.22, 0], a + [0, -0.22, 0], colors[k], level=4)
        mb.quad(a, a + [0, -0.22, 0], a + (p1 - p0) * 0.1 + [0, -0.22, 0], a + (p1 - p0) * 0.1, colors[k], level=4)


def scatter_rocks(mb, rng, count, max_r=60):
    placed = 0
    while placed < count:
        x, z = rng.uniform(-max_r, max_r, size=2)
        r = math.hypot(x, z)
        if r < 5 or r > max_r:
            continue
        d = float(lake_distance(np.array(x), np.array(z)))
        if d < 0.95:
            continue
        near_shore = d < 1.3
        if not near_shore and rng.uniform() > 0.35:
            continue
        y = float(terrain_height(np.array(x), np.array(z)))
        mb.rock((x, y + 0.05, z), rng.uniform(0.2, 0.9 if not near_shore else 0.55), rng, "stone" if rng.uniform() < 0.6 else "stone_dark")
        placed += 1


def build_fireflies(rng, groups=3, per_group=12, centers=((0, -9), (-18, -21), (10, -13)), spread=6.0):
    builders = []
    for g in range(groups):
        mb = MeshBuilder()
        cx, cz = centers[g % len(centers)]
        for _ in range(per_group):
            x = cx + rng.uniform(-spread, spread)
            z = cz + rng.uniform(-spread, spread)
            if lake_distance(np.array(x), np.array(z)) < 0.95:
                ground = WATER_Y
            else:
                ground = float(terrain_height(np.array(x), np.array(z)))
            y = ground + rng.uniform(0.5, 2.2)
            s = rng.uniform(0.03, 0.05)
            c = np.array([x, y, z])
            pts = [c + [s, 0, 0], c + [-s, 0, 0], c + [0, s, 0], c + [0, -s, 0], c + [0, 0, s], c + [0, 0, -s]]
            for a, b, cc in ((0, 2, 4), (4, 2, 1), (1, 2, 5), (5, 2, 0), (0, 4, 3), (4, 1, 3), (1, 5, 3), (5, 0, 3)):
                mb.tri(pts[a], pts[b], pts[cc], "firefly", ref=c)
        builders.append(mb)
    return builders


# --------------------------------------------------------------------------
# Aurora ribbons (animated, translucent)
# --------------------------------------------------------------------------

def build_aurora_texture(w=1024, h=256):
    u = (np.arange(w) + 0.5) / w
    v = 1 - (np.arange(h) + 0.5) / h  # v = 0 bottom edge of the curtain
    U, V = np.meshgrid(u, v)
    streak = np.clip(0.5 + 0.5 * value_noise3(U * 90, 0, 0, 51), 0, 1) ** 1.6
    streak = 0.25 + 0.75 * streak
    folds = 0.5 + 0.5 * np.clip(0.5 + 0.5 * value_noise3(U * 9, 0, 1, 53), 0, 1)
    bottom = smoothstep(0.0, 0.16, V)
    top = np.exp(-V * 2.6) * (1 - smoothstep(0.75, 1.0, V))
    ends = smoothstep(0.0, 0.12, U) * smoothstep(1.0, 0.88, U)
    alpha = np.clip(bottom * top * streak ** 1.6 * folds * ends * 0.85, 0, 1)
    t = smoothstep(0.05, 0.6, V)[..., None]
    col = np.array([0.35, 1.0, 0.62]) * (1 - t) + np.array([0.80, 0.35, 1.0]) * t
    rgb = linear_to_srgb(col * (0.55 + 0.45 * streak[..., None]))
    rgba = np.concatenate([rgb, alpha[..., None]], axis=-1)
    return Image.fromarray((rgba * 255 + 0.5).astype(np.uint8), "RGBA")


def build_aurora_ribbons(specs, rows=6, segs=90):
    """Curtains following arcs in the northern sky. Returns points, faces, uvs."""
    points, uvs, faces = [], [], []
    for spec in specs:
        radius, a0, a1, y0, y1, seed = spec
        base = len(points)
        for i in range(segs + 1):
            s = i / segs
            az = math.radians(a0 + (a1 - a0) * s)
            wiggle = 0.06 * radius * math.sin(3.1 * az * 4 + seed) + 0.025 * radius * math.sin(11 * az + 2 * seed)
            for j in range(rows):
                t = j / (rows - 1)
                rr = radius + wiggle + 0.08 * radius * t ** 1.5
                y = y0 + (y1 - y0) * t
                points.append((rr * math.sin(az), y, -rr * math.cos(az)))
                uvs.append((s * 2.0 + seed * 0.37, t))
        for i in range(segs):
            for j in range(rows - 1):
                a = base + i * rows + j
                b = a + rows
                # Both windings so the curtain is visible from either side.
                faces.append((a, b, b + 1, a + 1))
                faces.append((a, a + 1, b + 1, b))
    return points, faces, uvs


def terrain_grid(n=241, radius=TERRAIN_RADIUS):
    xs = np.linspace(-TERRAIN_HALF, TERRAIN_HALF, n)
    X, Z = np.meshgrid(xs, xs)
    H = terrain_height(X, Z)
    pts = np.stack([X, H, Z], axis=-1).reshape(-1, 3)
    uvs = np.stack([(X + TERRAIN_HALF) / (2 * TERRAIN_HALF), (Z + TERRAIN_HALF) / (2 * TERRAIN_HALF)], -1).reshape(-1, 2)
    N = terrain_normals(X, Z).reshape(-1, 3)
    faces = []
    for j in range(n - 1):
        for i in range(n - 1):
            cx, cz = (xs[i] + xs[i + 1]) / 2, (xs[j] + xs[j + 1]) / 2
            if math.hypot(cx, cz) > radius:
                continue
            a = j * n + i
            faces.append((a, a + n, a + n + 1, a + 1))   # counter-clockwise seen from above
    return compact(pts, faces, uvs, N)


def polar_terrain(radius, rings=70, segs=160):
    """Terrain disc on a polar grid, for the diorama's round island."""
    pts, uvs = [[0.0, float(terrain_height(np.array(0.0), np.array(0.0))), 0.0]], [(0.5, 0.5)]
    for r_i in range(1, rings + 1):
        r = radius * (r_i / rings) ** 0.9
        for s in range(segs):
            a = 2 * math.pi * s / segs
            x, z = r * math.sin(a), -r * math.cos(a)
            pts.append([x, 0.0, z])
            uvs.append(((x + TERRAIN_HALF) / (2 * TERRAIN_HALF), (z + TERRAIN_HALF) / (2 * TERRAIN_HALF)))
    pts = np.asarray(pts)
    pts[:, 1] = terrain_height(pts[:, 0], pts[:, 2])
    faces = []
    for s in range(segs):
        faces.append((0, 1 + (s + 1) % segs, 1 + s))
    for r_i in range(1, rings):
        o0 = 1 + (r_i - 1) * segs
        o1 = 1 + r_i * segs
        for s in range(segs):
            t = (s + 1) % segs
            faces.append((o0 + s, o0 + t, o1 + t, o1 + s))
    # Orient faces upward.
    a, b, c = faces[0]
    n = np.cross(pts[b] - pts[a], pts[c] - pts[a])
    if n[1] < 0:
        faces = [tuple(reversed(f)) for f in faces]
    normals = terrain_normals(pts[:, 0], pts[:, 2])
    edge = pts[1 + (rings - 1) * segs :]
    return pts, faces, uvs, normals, edge


def island_underside(mb, edge, depth, rng):
    """A jagged rocky underside hanging below the diorama island."""
    segs = len(edge)
    layers = 6
    rings = [edge]
    center = edge.mean(axis=0)
    for k in range(1, layers + 1):
        t = k / layers
        ring = []
        for s, p in enumerate(edge):
            dxz = np.array([p[0] - center[0], 0, p[2] - center[2]])
            shrink = (1 - t) ** 0.85
            jag = 1 + 0.08 * rng.normal()
            q = np.array([center[0], 0, center[2]]) + dxz * shrink * jag
            q[1] = min(edge[:, 1].min(), 0) - depth * t ** 1.3 + rng.normal(0, depth * 0.03)
            ring.append(q)
        rings.append(np.asarray(ring))
    tip = np.array([center[0], rings[-1][:, 1].min() - depth * 0.15, center[2]])
    for k in range(layers):
        r0, r1 = rings[k], rings[k + 1]
        for s in range(segs):
            t = (s + 1) % segs
            color = "stone" if (k + s // 5) % 3 else "stone_dark"
            ref = np.array([center[0], (r0[s][1] + r1[s][1]) / 2, center[2]])
            mb.quad(r0[s], r0[t], r1[t], r1[s], color, ref=ref)
    last = rings[-1]
    for s in range(segs):
        mb.tri(last[s], last[(s + 1) % segs], tip, "stone_dark", ref=tip + [0, depth, 0])


def water_disc(y, pad=1.35, segs=96, rings=6):
    cx, cz = LAKE_CENTER
    rx, rz = LAKE_RADII
    pts, uvs, faces = [[cx, y, cz]], [(0.5, 0.5)], []
    for r in range(1, rings + 1):
        f = pad * r / rings
        for s in range(segs):
            a = 2 * math.pi * s / segs
            pts.append([cx + rx * f * math.cos(a), y, cz + rz * f * math.sin(a)])
            uvs.append((0.5 + 0.5 * f * math.cos(a) / pad, 0.5 + 0.5 * f * math.sin(a) / pad))
    for s in range(segs):
        faces.append((0, 1 + (s + 1) % segs, 1 + s))
    for r in range(1, rings):
        o0, o1 = 1 + (r - 1) * segs, 1 + r * segs
        for s in range(segs):
            t = (s + 1) % segs
            faces.append((o0 + s, o0 + t, o1 + t, o1 + s))
    p = np.asarray(pts)
    a, b, c = faces[0]
    if np.cross(p[b] - p[a], p[c] - p[a])[1] < 0:
        faces = [tuple(reversed(f)) for f in faces]
    return pts, faces, uvs


# --------------------------------------------------------------------------
# Scenes
# --------------------------------------------------------------------------

AURORA_SPECS = [
    # radius, az start, az end (deg), bottom y, top y, seed
    (92.0, -70.0, -5.0, 50.0, 92.0, 1),
    (88.0, -20.0, 45.0, 56.0, 100.0, 2),
    (96.0, 25.0, 80.0, 48.0, 84.0, 3),
]


def build_overlook(work, textures, props, trees_mb, fireflies):
    stage_path = work / "overlook.usdc"
    stage, root = new_stage(stage_path, "Overlook", seconds=ANIM_SECONDS, fps=FPS)
    total = ANIM_SECONDS * FPS
    looks = "/Overlook/Looks"
    sky_mat = make_material(stage, f"{looks}/Sky", texture="textures/sky.jpg", emissive_tex=True)
    terrain_mat = make_material(stage, f"{looks}/Terrain", texture="textures/terrain.jpg", emissive_tex=True)
    palette_mat = make_material(stage, f"{looks}/Palette", texture="textures/palette.png", emissive_tex=True)
    water_mat = make_material(stage, f"{looks}/Water", diffuse=(0.05, 0.08, 0.10), emissive=(0.004, 0.010, 0.016),
                              metallic=1.0, roughness=0.12)
    aurora_mat = make_material(stage, f"{looks}/Aurora", texture="textures/aurora.png", emissive_tex=True,
                               opacity_from_texture=True)

    pts, faces, uvs = sky_dome(SKY_RADIUS)
    define_mesh(stage, "/Overlook/Sky", pts, faces, uvs=uvs, material=sky_mat)
    tp, tf, tu, tn = terrain_grid()
    define_mesh(stage, "/Overlook/Terrain", tp, tf, uvs=tu, normals=tn, normal_interp="vertex", material=terrain_mat)
    wp, wf, wu = water_disc(WATER_Y)
    define_mesh(stage, "/Overlook/Water", wp, wf, uvs=wu, normals=[(0, 1, 0)] * len(wp), normal_interp="vertex",
                material=water_mat)
    define_builder_mesh(stage, "/Overlook/Props", props, palette_mat)
    define_builder_mesh(stage, "/Overlook/Forest", trees_mb, palette_mat)

    UsdGeom.Xform.Define(stage, "/Overlook/Fireflies")
    for g, mb in enumerate(fireflies):
        x = UsdGeom.Xform.Define(stage, f"/Overlook/Fireflies/Swarm{g}")
        animate_translate(x, (0.8, 0.35, 0.8), total / (2 + g), g * 1.7, total)
        define_builder_mesh(stage, f"/Overlook/Fireflies/Swarm{g}/Lights", mb, palette_mat)

    aur = UsdGeom.Xform.Define(stage, "/Overlook/Aurora")
    animate_rotate_y(aur, 4.0, total)
    ap, af, au = build_aurora_ribbons(AURORA_SPECS)
    define_mesh(stage, "/Overlook/Aurora/Curtains", ap, af, uvs=au, material=aurora_mat)

    stage.GetRootLayer().Save()
    copy_textures(work, textures)
    return stage_path


def build_diorama(work, textures, rng):
    stage_path = work / "diorama.usdc"
    stage, root = new_stage(stage_path, "Diorama", seconds=ANIM_SECONDS, fps=FPS)
    total = ANIM_SECONDS * FPS
    radius = 50.0
    scale = 0.0036   # 100 m across becomes a 36 cm tabletop island
    root.AddScaleOp().Set(Gf.Vec3f(scale, scale, scale))
    looks = "/Diorama/Looks"
    terrain_mat = make_material(stage, f"{looks}/Terrain", texture="textures/terrain.jpg", emissive_tex=True)
    palette_mat = make_material(stage, f"{looks}/Palette", texture="textures/palette.png", emissive_tex=True)
    water_mat = make_material(stage, f"{looks}/Water", diffuse=(0.06, 0.10, 0.13), emissive=(0.02, 0.05, 0.07),
                              metallic=1.0, roughness=0.15)
    aurora_mat = make_material(stage, f"{looks}/Aurora", texture="textures/aurora.png", emissive_tex=True,
                               opacity_from_texture=True)

    pts, faces, uvs, normals, edge = polar_terrain(radius)
    define_mesh(stage, "/Diorama/Terrain", pts, faces, uvs=uvs, normals=normals, normal_interp="vertex",
                material=terrain_mat)
    wp, wf, wu = water_disc(WATER_Y, pad=1.3)
    define_mesh(stage, "/Diorama/Water", wp, wf, uvs=wu, normals=[(0, 1, 0)] * len(wp), normal_interp="vertex",
                material=water_mat)

    props = MeshBuilder()
    build_deck(props)
    build_jetty(props, rng)
    build_ledge(props, rng)
    scatter_rocks(props, rng, 40, max_r=radius - 4)
    island_underside(props, edge, 38.0, rng)
    define_builder_mesh(stage, "/Diorama/Props", props, palette_mat)

    forest = MeshBuilder()
    for x, z, h in TREES:
        if math.hypot(x, z) < radius - 2.5:
            build_tree(forest, x, float(terrain_height(np.array(x), np.array(z))), z, h, rng)
    define_builder_mesh(stage, "/Diorama/Forest", forest, palette_mat)

    aur = UsdGeom.Xform.Define(stage, "/Diorama/Aurora")
    animate_rotate_y(aur, 6.0, total)
    specs = [(40.0, -60.0, 0.0, 34.0, 62.0, 1), (43.0, -15.0, 50.0, 38.0, 70.0, 2)]
    ap, af, au = build_aurora_ribbons(specs, segs=60)
    define_mesh(stage, "/Diorama/Aurora/Curtains", ap, af, uvs=au, material=aurora_mat)

    stage.GetRootLayer().Save()
    copy_textures(work, textures)
    return stage_path



TREES: list = []


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(RNG_SEED)
    work = Path(tempfile.mkdtemp(prefix="overlook-"))
    tex_dir = work / "tex"
    tex_dir.mkdir()

    print("• laying out the forest")
    TREES.extend(trees_layout(rng))
    print(f"  {len(TREES)} trees")

    print("• painting the night sky")
    sky = build_sky(4096)
    to_image(sky, exposure=2.6).save(tex_dir / "sky.jpg", quality=90, subsampling=0)
    light = build_sky(1024)
    write_exr(OUT / "overlook-light.exr", light * 2.2)

    print("• baking moonlight into the terrain")
    terrain = build_terrain_texture(2048, TREES)
    to_image(terrain, exposure=2.6).save(tex_dir / "terrain.jpg", quality=90)

    print("• palette + aurora textures")
    to_image(PALETTE_TEX.texture(), exposure=2.6).save(tex_dir / "palette.png")
    build_aurora_texture().save(tex_dir / "aurora.png")

    textures = {p.name: p for p in tex_dir.iterdir()}

    print("• building props")
    props = MeshBuilder()
    build_deck(props)
    build_jetty(props, rng)
    build_ledge(props, rng)
    scatter_rocks(props, rng, 70)
    forest = MeshBuilder()
    for x, z, h in TREES:
        build_tree(forest, x, float(terrain_height(np.array(x), np.array(z))), z, h, rng)
    fireflies = build_fireflies(rng)

    print("• authoring overlook.usdz")
    overlook = build_overlook(work, textures, props, forest, fireflies)
    package(overlook, OUT / "overlook.usdz")
    print("• authoring diorama.usdz")
    diorama = build_diorama(work, textures, np.random.default_rng(RNG_SEED + 1))
    package(diorama, OUT / "diorama.usdz")

    viewpoints = [
        {
            "id": "deck",
            "name": "Overlook deck",
            "blurb": "Stand on the brass compass rose. The lake opens to the north and the aurora ripples above the peaks.",
            "position": [0.0, 0.0, 0.0],
            "heading": 0,
        },
        {
            "id": "jetty",
            "name": "Lantern jetty",
            "blurb": "Walk to the end of the old jetty. Water on three sides, fireflies drifting over the shallows.",
            "position": [JETTY_X[1] - 1.8, JETTY_TOP, JETTY_Z],
            "heading": 75,
        },
        {
            "id": "ledge",
            "name": "Summit ledge",
            "blurb": "Climb the southern ridge for the whole valley: deck, lake and the northern lights in one sweep.",
            "position": [RIDGE_CENTER[0], round(LEDGE_TOP, 3), RIDGE_CENTER[1]],
            "heading": -16,
        },
    ]
    (OUT / "viewpoints.json").write_text(json.dumps({"viewpoints": viewpoints}, indent=2) + "\n")

    for name in ("overlook.usdz", "diorama.usdz"):
        path = OUT / name
        print(f"• checking {name}: {path.stat().st_size / 1e6:.2f} MB")
        check(path)
    print(f"• EXR: {(OUT / 'overlook-light.exr').stat().st_size / 1e6:.2f} MB")
    shutil.copy(tex_dir / "sky.jpg", work / "sky-preview.jpg")
    print(f"done — scratch files in {work}")


if __name__ == "__main__":
    main()
