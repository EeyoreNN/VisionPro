#!/usr/bin/env python3
"""Generate the 3D and image assets for Orbit Room.

A room-scale solar system: the Sun hangs just beyond the Safari window and
the planets orbit it at waist height. The outer ones sweep around the
visitor, who stands on a small floating observation deck. Everything is
procedural:

    cd projects/orbit-room
    python3 -m venv .venv && .venv/bin/pip install -r ../../shared/python/requirements.txt
    .venv/bin/python tools/build_assets.py

Outputs in site/assets/:
    orbit-room.usdz     immersive environment (meters, origin at the visitor's feet, -Z ahead)
    orrery.usdz         a tabletop brass orrery for the inline <model>
    space-light.exr     360 degree HDR lighting map
    viewpoints.json     places to stand
    planets.json        names, colours and facts for the page

Orbits are compressed and planets enlarged so everything fits in a room (the real
Neptune is 30x farther out than Earth). Each planet's lighting is baked toward
the Sun, and planets keep one face to the Sun as they orbit, so the day/night
line always faces the right way.
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image
from pxr import Gf, UsdGeom

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parents[1] / "shared" / "python"))

from usdkit import (  # noqa: E402
    MeshBuilder, Palette, animate_spin_y, check, copy_textures, define_builder_mesh, define_mesh,
    direction, fbm, hex_lin, linear_to_srgb, make_material, new_stage, package, sky_dome,
    smoothstep, splat_stars, to_image, value_noise3, write_exr,
)

OUT = ROOT / "site" / "assets"
SEED = 7

LOOP_SECONDS = 240
FPS = 24
TOTAL = LOOP_SECONDS * FPS

SUN_CENTER = (0.0, 1.2, -4.5)   # orrery centre, meters from the visitor's feet
SYSTEM_TILT = 8.0               # degrees; the near side of the ecliptic dips toward the floor
SUN_RADIUS = 0.28
SKY_RADIUS = 60.0
SKYWALK = (0.0, 2.6, -2.4)      # a second, higher deck looking down on the orbits

# name, orbit radius (m), body radius (m), revolutions per loop, start angle (deg)
PLANETS = [
    ("mercury", 0.45, 0.032, 20, 40),
    ("venus", 0.70, 0.055, 12, 200),
    ("earth", 0.98, 0.060, 8, 120),
    ("mars", 1.28, 0.042, 5, 300),
    ("jupiter", 2.20, 0.200, 3, 70),
    ("saturn", 2.75, 0.165, 2, 230),
    ("uranus", 3.20, 0.100, 1, 160),
    ("neptune", 3.60, 0.098, 1, 340),
]
BELT = (1.50, 1.85)
MOON = (0.14, 0.017, 16)        # orbit radius around Earth, radius, revolutions per loop
SATURN_RINGS = (0.22, 0.40)
SATURN_TILT = 26.0

FACTS = {
    "mercury": {"title": "Mercury", "color": "#b9b2a8", "year": "88 days", "day": "176 days",
                "fact": "A single day (sunrise to sunrise) lasts two of its years."},
    "venus": {"title": "Venus", "color": "#e8cf94", "year": "225 days", "day": "117 days",
              "fact": "The hottest planet: thick clouds trap heat at about 465 °C."},
    "earth": {"title": "Earth", "color": "#4f8fe0", "year": "365.25 days", "day": "24 hours",
              "fact": "The only world we know with liquid oceans on its surface — and city lights on its night side."},
    "mars": {"title": "Mars", "color": "#d0653c", "year": "687 days", "day": "24 h 37 m",
             "fact": "Home to Olympus Mons, a volcano nearly three times the height of Everest."},
    "jupiter": {"title": "Jupiter", "color": "#d6b48c", "year": "11.9 years", "day": "9 h 56 m",
                "fact": "Its Great Red Spot is a storm wider than the whole Earth."},
    "saturn": {"title": "Saturn", "color": "#e4d29c", "year": "29.5 years", "day": "10 h 34 m",
               "fact": "Its rings are hundreds of thousands of km wide but mostly only tens of meters thick."},
    "uranus": {"title": "Uranus", "color": "#9fe0e4", "year": "84 years", "day": "17 h 14 m",
               "fact": "It rolls around the Sun on its side, tilted about 98°."},
    "neptune": {"title": "Neptune", "color": "#4f6fe6", "year": "165 years", "day": "16 h 6 m",
                "fact": "The windiest planet: gusts reach around 2,000 km/h."},
}


# --------------------------------------------------------------------------
# Textures
# --------------------------------------------------------------------------

def sphere_dirs(w):
    """Unit direction for every texel of a w x w/2 equirect (same mapping as sky_dome)."""
    h = w // 2
    u = (np.arange(w) + 0.5) / w
    v = 1.0 - (np.arange(h) + 0.5) / h
    AZ, EL = np.meshgrid((u - 0.5) * 2 * np.pi, (v - 0.5) * np.pi)
    return direction(AZ, EL), EL


def noise3(d, freq, octaves, seed):
    return fbm(d[..., 0] * freq, d[..., 1] * freq, octaves, seed, d[..., 2] * freq)


def sunlight(d, ambient=0.035):
    """Baked lighting: the Sun is along -X in every planet's frame."""
    ndotl = np.clip(-d[..., 0], 0, 1)
    soft = smoothstep(-0.08, 0.25, -d[..., 0])      # gentle terminator
    return ambient + (1 - ambient) * soft * (0.25 + 0.75 * ndotl)


def craters(d, rng, count, size=(0.03, 0.18)):
    shade = np.zeros(d.shape[:-1])
    centres = rng.normal(size=(count, 3))
    centres /= np.linalg.norm(centres, axis=1, keepdims=True)
    for c, r in zip(centres, rng.uniform(*size, size=count) ** 1.5):
        ang = np.arccos(np.clip(d @ c, -1, 1))
        t = ang / r
        bowl = np.where(t < 1, -0.35 * (1 - t ** 2), 0)
        rim = 0.25 * np.exp(-((t - 1.0) / 0.18) ** 2)
        shade += bowl + rim
    return shade


def planet_texture(name, w, rng):
    d, el = sphere_dirs(w)
    lat = el
    light = sunlight(d)
    n = noise3(d, 3.0, 5, 11)
    if name == "earth":
        land = noise3(d, 1.7, 6, 21) + 0.12 * noise3(d, 7, 3, 22)
        is_land = smoothstep(0.02, 0.07, land)
        ocean = hex_lin("#0b2f66") * (0.8 + 0.2 * n[..., None])
        coast = hex_lin("#1c6aa8")
        ocean = ocean * (1 - smoothstep(-0.1, 0.02, land)[..., None]) + coast * smoothstep(-0.1, 0.02, land)[..., None] * 0.7
        dry = smoothstep(0.1, 0.5, 1 - np.abs(np.abs(lat) - 0.4) * 3 + 0.4 * n)
        greens = hex_lin("#2f6a2c") * (1 - dry[..., None]) + hex_lin("#b09a63") * dry[..., None]
        albedo = ocean * (1 - is_land[..., None]) + greens * is_land[..., None]
        ice = smoothstep(1.12, 1.25, np.abs(lat) + 0.08 * n)
        albedo = albedo * (1 - ice[..., None]) + hex_lin("#f2f6fb") * ice[..., None]
        clouds = np.clip(smoothstep(0.05, 0.45, noise3(d * np.array([1, 2.2, 1]), 2.6, 6, 31)), 0, 1) * 0.85
        albedo = albedo * (1 - clouds[..., None]) + hex_lin("#ffffff") * clouds[..., None]
        rgb = albedo * light[..., None] * 1.25
        # City lights on the night side of land.
        cities = (value_noise3(d[..., 0] * 90, d[..., 1] * 90, d[..., 2] * 90, 41) > 0.55) * is_land
        cities = cities * (np.abs(lat) < 1.1) * (1 - clouds) * smoothstep(0.12, 0.0, light)
        rgb += cities[..., None] * np.array([1.0, 0.68, 0.30]) * 0.9
        return rgb
    if name == "mars":
        base = hex_lin("#b4532a") * (0.75 + 0.25 * n[..., None])
        dark = smoothstep(0.1, 0.35, noise3(d, 2.2, 5, 51))
        albedo = base * (1 - 0.45 * dark[..., None])
        albedo *= (1 + 0.6 * craters(d, rng, 60, (0.02, 0.1))[..., None])
        cap = smoothstep(1.28, 1.36, np.abs(lat) + 0.05 * n)
        albedo = albedo * (1 - cap[..., None]) + hex_lin("#f4efe8") * cap[..., None]
        return albedo * light[..., None] * 1.3
    if name in ("mercury", "moon"):
        base = hex_lin("#8e8a85" if name == "mercury" else "#9a9a9c") * (0.8 + 0.2 * n[..., None])
        albedo = base * np.clip(1 + craters(d, rng, 140, (0.02, 0.14))[..., None], 0.4, 1.5)
        if name == "moon":
            maria = smoothstep(0.15, 0.35, noise3(d, 1.6, 4, 61))
            albedo *= (1 - 0.4 * maria[..., None])
            return albedo * 1.2    # small and seen from every side: lit evenly
        return albedo * light[..., None] * 1.3
    if name == "venus":
        swirl = noise3(d * np.array([1, 3, 1]), 2.0, 6, 71)
        albedo = hex_lin("#e3c687") * (0.85 + 0.15 * swirl[..., None]) + hex_lin("#f6e9c4") * smoothstep(0.2, 0.6, swirl)[..., None] * 0.3
        return albedo * light[..., None] * 1.2
    # Gas and ice giants: bands warped by turbulence.
    warp = lat + 0.05 * noise3(d, 4.0, 5, 81) + 0.02 * noise3(d, 16, 3, 83)
    if name == "jupiter":
        bands = np.sin(warp * 14) * 0.5 + 0.5
        fine = np.sin(warp * 41 + 2) * 0.5 + 0.5
        light_c, dark_c = hex_lin("#efe1c6"), hex_lin("#a86f45")
        mix = np.clip(bands * 0.8 + fine * 0.2, 0, 1)
        albedo = light_c * mix[..., None] + dark_c * (1 - mix[..., None])
        # Great Red Spot.
        az = np.arctan2(d[..., 0], -d[..., 2])
        spot = np.exp(-(((az - 0.9) / 0.22) ** 2 + ((lat + 0.36) / 0.09) ** 2))
        albedo = albedo * (1 - spot[..., None]) + hex_lin("#c0472d") * spot[..., None]
        return albedo * light[..., None] * 1.25
    if name == "saturn":
        bands = np.sin(warp * 11) * 0.5 + 0.5
        albedo = hex_lin("#e7d3a1") * (0.85 + 0.15 * bands[..., None]) * (1 - 0.2 * smoothstep(0.9, 1.4, np.abs(lat))[..., None])
        return albedo * light[..., None] * 1.25
    if name == "uranus":
        bands = np.sin(warp * 6) * 0.5 + 0.5
        albedo = hex_lin("#a6e3e6") * (0.92 + 0.08 * bands[..., None])
        return albedo * light[..., None] * 1.2
    if name == "neptune":
        bands = np.sin(warp * 9) * 0.5 + 0.5
        albedo = hex_lin("#3558d6") * (0.8 + 0.2 * bands[..., None])
        az = np.arctan2(d[..., 0], -d[..., 2])
        spot = np.exp(-(((az + 1.9) / 0.2) ** 2 + ((lat + 0.35) / 0.08) ** 2))
        albedo = albedo * (1 - 0.6 * spot[..., None])
        streak = smoothstep(0.55, 0.8, noise3(d * np.array([0.3, 4, 0.3]), 3, 3, 91))
        albedo = albedo + streak[..., None] * 0.25
        return albedo * light[..., None] * 1.25
    raise ValueError(name)


def sun_texture(w):
    d, _ = sphere_dirs(w)
    gran = noise3(d, 22, 4, 101) * 0.6 + noise3(d, 6, 4, 103) * 0.4
    spots = smoothstep(0.55, 0.7, noise3(d, 4, 4, 107))
    rgb = np.array([3.2, 1.75, 0.55]) * (0.85 + 0.25 * gran[..., None]) * (1 - 0.5 * spots[..., None])
    return rgb


def ring_texture(w=1024, h=256, inner=SATURN_RINGS[0], outer=SATURN_RINGS[1], planet_r=0.165):
    """u = radial position (inner→outer), v = angle around the planet."""
    u = (np.arange(w) + 0.5) / w
    v = (np.arange(h) + 0.5) / h
    U, V = np.meshgrid(u, v)
    fine = 0.5 + 0.5 * value_noise3(U * 180, 0, 0, 111)
    coarse = 0.5 + 0.5 * value_noise3(U * 18, 0, 1, 113)
    density = (0.35 + 0.45 * coarse + 0.25 * fine) * smoothstep(0.0, 0.06, U) * smoothstep(1.0, 0.93, U)
    density *= 1 - 0.92 * np.exp(-((U - 0.63) / 0.022) ** 2)        # Cassini division
    density *= 1 - 0.5 * smoothstep(0.0, 0.25, 0.25 - U)             # faint inner C ring
    rho = inner + U * (outer - inner)
    theta = V * 2 * np.pi
    shadow = (np.cos(theta) > 0) & (np.abs(rho * np.sin(theta)) < planet_r * 0.98)
    col = hex_lin("#e6d6ad") * (0.75 + 0.35 * coarse[..., None])
    col = col * np.where(shadow, 0.12, 1.0)[..., None]
    rgb = linear_to_srgb(col * 1.1)
    alpha = np.clip(density, 0, 1) * 0.95
    return Image.fromarray((np.concatenate([rgb, alpha[..., None]], -1) * 255 + 0.5).astype(np.uint8), "RGBA")


def space_sky(w):
    d, el = sphere_dirs(w)
    band = np.array([0.2, 0.9, -0.38])
    band /= np.linalg.norm(band)
    dist = d @ band
    n = noise3(d, 3, 5, 121)
    mw = np.exp(-((dist + 0.06 * n) / 0.16) ** 2) * (0.5 + 0.5 * n)
    col = np.zeros(d.shape) + np.array([0.0012, 0.0016, 0.0035])
    col += mw[..., None] * np.array([0.020, 0.019, 0.026])
    neb1 = smoothstep(0.25, 0.75, noise3(d, 2.2, 6, 131)) * np.exp(-((dist - 0.15) / 0.3) ** 2)
    neb2 = smoothstep(0.3, 0.8, noise3(d, 2.8, 6, 137)) * np.exp(-((dist + 0.2) / 0.35) ** 2)
    col += neb1[..., None] * np.array([0.030, 0.006, 0.040])
    col += neb2[..., None] * np.array([0.004, 0.026, 0.034])
    img = col
    rng = np.random.default_rng(SEED)
    splat_stars(img, rng, 4500, min_elevation=-2, horizon_fade=False, sigma=0.5)
    splat_stars(img, rng, 4000, band_normal=band, min_elevation=-2, horizon_fade=False, sigma=0.5)
    return img


def platform_texture(size=1024, radius=1.0):
    c = (np.arange(size) + 0.5) / size * 2 - 1
    X, Y = np.meshgrid(c, -c)
    r = np.hypot(X, Y) * radius
    ang = np.arctan2(X, Y)
    base = np.array([0.012, 0.018, 0.032]) * (1 + 0.15 * fbm(X * 6, Y * 6, 3, 141))[..., None]
    rings = np.exp(-((np.mod(r + 0.1, 0.2) - 0.1) / 0.004) ** 2) * (r > 0.15)
    # Hour-marker ticks every 15 degrees in a band near the rim.
    ticks = (np.abs(np.mod(ang + np.pi / 24, np.pi / 12) - np.pi / 24) * r < 0.004) * smoothstep(0.78, 0.8, r) * (r < 0.9)
    rim = smoothstep(0.93, 0.975, r) * (1 - smoothstep(0.985, 1.0, r))
    north = (np.abs(X) * radius < 0.03 * (1 - (Y * radius - 0.6) / 0.25)) * (Y * radius > 0.6) * (Y * radius < 0.85)
    glow = np.array([0.10, 0.55, 0.75])
    col = base + glow[None, None] * (0.18 * rings + 0.35 * ticks)[..., None] + np.array([0.3, 1.6, 2.0]) * rim[..., None]
    col += np.array([1.2, 0.9, 0.4]) * north[..., None]
    return col


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

def uv_sphere(radius, nu=48, nv=24):
    pts, faces, uvs = sky_dome(radius, nu, nv)
    faces = [tuple(reversed(f)) for f in faces]      # outward
    normals = [p / radius for p in np.asarray(pts)]
    return pts, faces, uvs, normals


def annulus(inner, outer, segs=180):
    pts, uvs, faces = [], [], []
    for i in range(segs + 1):
        t = i / segs
        a = t * 2 * np.pi
        for k, r in enumerate((inner, outer)):
            pts.append((r * math.cos(a), 0.0, r * math.sin(a)))
            uvs.append((float(k), t))
    for i in range(segs):
        a, b = 2 * i, 2 * i + 2
        faces.append((a, a + 1, b + 1, b))
        faces.append((a, b, b + 1, a + 1))           # both sides
    return pts, faces, uvs


def disc_mesh(radius, segs=96, rings=8):
    pts, uvs, faces = [(0.0, 0.0, 0.0)], [(0.5, 0.5)], []
    for r_i in range(1, rings + 1):
        r = radius * r_i / rings
        for s in range(segs):
            a = 2 * math.pi * s / segs
            x, z = r * math.sin(a), -r * math.cos(a)
            pts.append((x, 0.0, z))
            uvs.append((0.5 + 0.5 * x / radius, 0.5 - 0.5 * z / radius))
    for s in range(segs):
        faces.append((0, 1 + s, 1 + (s + 1) % segs))
    for r_i in range(1, rings):
        o0, o1 = 1 + (r_i - 1) * segs, 1 + r_i * segs
        for s in range(segs):
            t = (s + 1) % segs
            faces.append((o0 + s, o1 + s, o1 + t, o0 + t))
    p = np.asarray(pts)
    a, b, c = faces[0]
    if np.cross(p[b] - p[a], p[c] - p[a])[1] < 0:
        faces = [tuple(reversed(f)) for f in faces]
    return pts, faces, uvs


PALETTE = Palette({
    "metal": ("#3a4458", False),
    "metal_dark": ("#222a3a", False),
    "rock": ("#7b6f64", False),
    "rock_dark": ("#554b44", False),
    "brass": ("#c79a4b", False),
    "glow": ("#62e3ff", True),
})


def platform_underside(mb, radius, depth):
    segs = 32
    top = [np.array([radius * math.sin(2 * math.pi * i / segs), -0.005, -radius * math.cos(2 * math.pi * i / segs)]) for i in range(segs)]
    mid = [p * 0.98 + np.array([0, -0.08, 0]) for p in top]
    tip = np.array([0, -depth, 0])
    for i in range(segs):
        j = (i + 1) % segs
        mb.quad(top[i], top[j], mid[j], mid[i], "metal", ref=np.array([0, -0.04, 0]))
        mb.tri(mid[i], mid[j], tip, "metal_dark", ref=np.array([0, 0.2, 0]))
    # A glowing band around the hull so the deck reads from below.
    band = [p * 0.97 + np.array([0, -0.11, 0]) for p in top]
    for i in range(segs):
        j = (i + 1) % segs
        mb.quad(mid[i], mid[j], band[j], band[i], "glow", ref=np.array([0, -0.04, 0]))


def build_belt(rng, count=320):
    mb = MeshBuilder(PALETTE)
    for _ in range(count):
        a = rng.uniform(0, 2 * np.pi)
        r = rng.uniform(*BELT)
        c = np.array([r * math.cos(a), rng.normal(0, 0.03), r * math.sin(a)])
        s = -c / np.linalg.norm(c)      # toward the Sun
        mb.light = lambda n, s=s: 0.15 + 1.0 * max(0.0, float(n @ s))
        mb.rock(c, rng.uniform(0.006, 0.024), rng, "rock" if rng.uniform() < 0.6 else "rock_dark")
    return mb


def rock_method():
    """MeshBuilder in usdkit has no rock(); add a small icosahedron rock."""
    def rock(self, center, radius, rng, color, squash=0.75):
        t = (1 + 5 ** 0.5) / 2
        verts = np.array([(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t),
                          (0, -1, -t), (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)], float)
        verts /= np.linalg.norm(verts, axis=1, keepdims=True)
        faces = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4),
                 (11, 10, 2), (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8),
                 (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
        pts = verts * rng.uniform(0.7, 1.25, size=(12, 1)) * np.array([radius, radius * squash, radius]) + center
        for f in faces:
            self.tri(*(pts[i] for i in f), color, ref=center)
    MeshBuilder.rock = rock


rock_method()


# --------------------------------------------------------------------------
# Scene assembly
# --------------------------------------------------------------------------

def add_system(stage, root, looks, *, orbit_lines=True):
    """Sun, planets, moon, rings, belt and orbit lines under root (orrery centre at root origin)."""
    sys_x = UsdGeom.Xform.Define(stage, f"{root}/System")
    sys_x.AddRotateXOp().Set(SYSTEM_TILT)

    sun = UsdGeom.Xform.Define(stage, f"{root}/System/Sun")
    animate_spin_y(sun, 2, TOTAL)
    p, f, u, n = uv_sphere(SUN_RADIUS, 64, 32)
    define_mesh(stage, f"{root}/System/Sun/Surface", p, f, uvs=u, normals=n, normal_interp="vertex", material=looks["sun"])
    for k, (scale, alpha) in enumerate(((1.18, 0.30), (1.45, 0.14), (1.9, 0.07), (2.7, 0.035))):
        p, f, u, n = uv_sphere(SUN_RADIUS * scale, 32, 16)
        define_mesh(stage, f"{root}/System/SunGlow{k}", p, f, uvs=u, normals=n, normal_interp="vertex",
                    material=looks[f"glow{k}"])

    for name, orbit_r, radius, revs, start in PLANETS:
        orbit = UsdGeom.Xform.Define(stage, f"{root}/System/{name.title()}Orbit")
        animate_spin_y(orbit, -revs, TOTAL, phase_degrees=start)     # counter-clockwise from above
        body = UsdGeom.Xform.Define(stage, f"{root}/System/{name.title()}Orbit/{name.title()}")
        body.AddTranslateOp().Set(Gf.Vec3d(orbit_r, 0, 0))
        parent = body.GetPath()
        if name == "saturn":
            tilt = UsdGeom.Xform.Define(stage, f"{parent}/Tilt")
            tilt.AddRotateXOp().Set(SATURN_TILT)
            parent = tilt.GetPath()
            rp, rf, ru = annulus(*SATURN_RINGS)
            define_mesh(stage, f"{parent}/Rings", rp, rf, uvs=ru, material=looks["rings"])
        p, f, u, n = uv_sphere(radius)
        define_mesh(stage, f"{parent}/Surface", p, f, uvs=u, normals=n, normal_interp="vertex", material=looks[name])
        if name == "earth":
            moon_orbit = UsdGeom.Xform.Define(stage, f"{parent}/MoonOrbit")
            animate_spin_y(moon_orbit, -MOON[2], TOTAL)
            moon = UsdGeom.Xform.Define(stage, f"{parent}/MoonOrbit/Moon")
            moon.AddTranslateOp().Set(Gf.Vec3d(MOON[0], 0, 0))
            p, f, u, n = uv_sphere(MOON[1], 24, 12)
            define_mesh(stage, f"{parent}/MoonOrbit/Moon/Surface", p, f, uvs=u, normals=n, normal_interp="vertex",
                        material=looks["moon"])
        if orbit_lines:
            lp, lf, lu = annulus(orbit_r - 0.003, orbit_r + 0.003, segs=240)
            define_mesh(stage, f"{root}/System/Lines/{name.title()}", lp, lf, uvs=lu, material=looks["line"])

    belt = UsdGeom.Xform.Define(stage, f"{root}/System/Belt")
    animate_spin_y(belt, -1, TOTAL)
    define_builder_mesh(stage, f"{root}/System/Belt/Rocks", build_belt(np.random.default_rng(SEED + 3)), looks["palette"])


def make_looks(stage, root):
    L = f"{root}/Looks"
    looks = {name: make_material(stage, f"{L}/{name.title()}", texture=f"textures/{name}.jpg", emissive_tex=True)
             for name, *_ in PLANETS}
    looks["moon"] = make_material(stage, f"{L}/Moon", texture="textures/moon.jpg", emissive_tex=True)
    looks["sun"] = make_material(stage, f"{L}/Sun", texture="textures/sun.jpg", emissive_tex=True)
    for k, alpha in enumerate((0.30, 0.14, 0.07, 0.035)):
        looks[f"glow{k}"] = make_material(stage, f"{L}/SunGlow{k}", emissive=(1.0, 0.62, 0.22), opacity=alpha)
    looks["rings"] = make_material(stage, f"{L}/Rings", texture="textures/rings.png", emissive_tex=True, opacity_from_texture=True)
    looks["line"] = make_material(stage, f"{L}/OrbitLine", emissive=(0.25, 0.45, 0.9), opacity=0.35)
    looks["palette"] = make_material(stage, f"{L}/Palette", texture="textures/palette.png", emissive_tex=True)
    looks["sky"] = make_material(stage, f"{L}/Sky", texture="textures/sky.jpg", emissive_tex=True)
    looks["deck"] = make_material(stage, f"{L}/Deck", texture="textures/deck.jpg", emissive_tex=True,
                                  metallic=0.6, roughness=0.35)
    return looks


def add_deck(stage, path, looks, radius, rng):
    x = UsdGeom.Xform.Define(stage, path)
    p, f, u = disc_mesh(radius)
    define_mesh(stage, f"{path}/Top", p, f, uvs=u, normals=[(0, 1, 0)] * len(p), normal_interp="vertex", material=looks["deck"])
    mb = MeshBuilder(PALETTE, lambda n: 0.45 + 0.4 * max(0.0, float(n[1])) + 0.2 * max(0.0, float(-n[2])))
    platform_underside(mb, radius, radius * 0.6)
    define_builder_mesh(stage, f"{path}/Hull", mb, looks["palette"])
    return x


def build_room(work):
    stage_path = work / "orbit-room.usdc"
    stage, root = new_stage(stage_path, "OrbitRoom", seconds=LOOP_SECONDS, fps=FPS)
    looks = make_looks(stage, "/OrbitRoom")
    p, f, u = sky_dome(SKY_RADIUS, 96, 48)
    define_mesh(stage, "/OrbitRoom/Sky", p, f, uvs=u, material=looks["sky"])

    centre = UsdGeom.Xform.Define(stage, "/OrbitRoom/Orrery")
    centre.AddTranslateOp().Set(Gf.Vec3d(*SUN_CENTER))
    add_system(stage, "/OrbitRoom/Orrery", looks)

    rng = np.random.default_rng(SEED + 5)
    add_deck(stage, "/OrbitRoom/Deck", looks, 1.0, rng)
    sky = add_deck(stage, "/OrbitRoom/Skywalk", looks, 0.8, rng)
    sky.AddTranslateOp().Set(Gf.Vec3d(*SKYWALK))
    sky.AddScaleOp().Set(Gf.Vec3f(0.8, 0.8, 0.8))

    stage.GetRootLayer().Save()
    return stage_path


def build_orrery(work):
    """A tabletop version: brass stand, no sky or decks."""
    stage_path = work / "orrery.usdc"
    stage, root = new_stage(stage_path, "Orrery", seconds=LOOP_SECONDS, fps=FPS)
    root.AddScaleOp().Set(Gf.Vec3f(0.045, 0.045, 0.045))
    looks = make_looks(stage, "/Orrery")
    add_system(stage, "/Orrery", looks)
    mb = MeshBuilder(PALETTE, lambda n: 0.5 + 0.5 * max(0.0, float(n[1])) + 0.2 * max(0.0, float(n[2])))
    base_y = -1.45
    mb.cylinder([0, base_y - 0.12, 0], [0, base_y, 0], 1.1, 1.05, 48, "brass")
    mb.cylinder([0, base_y, 0], [0, base_y + 0.08, 0], 0.7, 0.6, 48, "metal_dark")
    mb.cylinder([0, base_y + 0.08, 0], [0, -SUN_RADIUS * 0.9, 0], 0.05, 0.04, 16, "brass")
    define_builder_mesh(stage, "/Orrery/Stand", mb, looks["palette"])
    stage.GetRootLayer().Save()
    return stage_path


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    work = Path(tempfile.mkdtemp(prefix="orbit-room-"))
    tex = work / "tex"
    tex.mkdir()

    print("• painting planets")
    for name, *_ in PLANETS:
        w = 2048 if name in ("earth", "jupiter") else 1024
        to_image(planet_texture(name, w, rng)).save(tex / f"{name}.jpg", quality=90)
    to_image(planet_texture("moon", 512, rng)).save(tex / "moon.jpg", quality=90)
    to_image(sun_texture(1024)).save(tex / "sun.jpg", quality=90)
    ring_texture().save(tex / "rings.png")
    to_image(PALETTE.texture()).save(tex / "palette.png")
    print("• painting the Milky Way")
    sky = space_sky(4096)
    to_image(sky, exposure=2.2).save(tex / "sky.jpg", quality=90, subsampling=0)
    to_image(platform_texture()).save(tex / "deck.jpg", quality=92)

    print("• lighting map")
    light = space_sky(1024) * 2.2
    d, _ = sphere_dirs(1024)
    to_sun = np.array(SUN_CENTER) - np.array([0.0, 1.6, 0.0])
    to_sun /= np.linalg.norm(to_sun)
    ang = np.arccos(np.clip(d @ to_sun, -1, 1))
    light += (60 * np.exp(-(ang / 0.05) ** 2) + 1.5 * np.exp(-ang / 0.35))[..., None] * np.array([1.0, 0.7, 0.35])
    write_exr(OUT / "space-light.exr", light)

    copy_textures(work, {p.name: p for p in tex.iterdir()})
    print("• authoring orbit-room.usdz")
    package(build_room(work), OUT / "orbit-room.usdz")
    print("• authoring orrery.usdz")
    package(build_orrery(work), OUT / "orrery.usdz")

    viewpoints = [
        {"id": "deck", "name": "Observation deck", "position": [0.0, 0.0, 0.0], "heading": 0,
         "blurb": "Stand at the edge of the Solar System. Neptune and Uranus sweep past at arm's length."},
        {"id": "skywalk", "name": "Skywalk", "position": list(SKYWALK), "heading": 0,
         "blurb": "Float above the plane of the planets and watch all eight orbits at once."},
    ]
    (OUT / "viewpoints.json").write_text(json.dumps({"viewpoints": viewpoints}, indent=2) + "\n")
    planets = [{"id": n, **FACTS[n], "orbit_m": r, "revs_per_loop": revs} for n, r, _, revs, _ in PLANETS]
    (OUT / "planets.json").write_text(json.dumps({"loop_seconds": LOOP_SECONDS, "planets": planets}, indent=2, ensure_ascii=False) + "\n")

    for name in ("orbit-room.usdz", "orrery.usdz"):
        path = OUT / name
        print(f"• checking {name}: {path.stat().st_size / 1e6:.2f} MB")
        check(path)
    print(f"done — scratch files in {work}")


if __name__ == "__main__":
    main()
