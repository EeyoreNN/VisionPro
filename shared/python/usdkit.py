"""usdkit — small helpers for procedurally generating USDZ scenes for visionOS.

Shared by the projects in this repo (see projects/*/tools/build_assets.py):

    import sys; sys.path.insert(0, str(REPO / "shared" / "python"))
    from usdkit import *

What's inside:
    noise           value_noise3, fbm, smoothstep
    colour          srgb_to_linear, linear_to_srgb, tonemap, to_image, hex_lin, write_exr,
                    splat_stars (star fields for equirect skies)
    geometry        MeshBuilder (+ Palette) for flat-shaded low-poly props, sky_dome,
                    compact, direction
    USD authoring   new_stage, make_material (UsdPreviewSurface), define_mesh,
                    define_builder_mesh, animate_translate, animate_rotate_y,
                    animate_spin_y
    packaging       package (hand-rolled, 64-byte aligned USDZ), copy_textures,
                    check (runs every USD validator)

Conventions: Y up, 1 unit = 1 meter, -Z is "forward"/north, azimuth 0 = -Z and
+90 degrees = +X. Baked lighting goes into emissive textures.
"""

from __future__ import annotations

import math
import shutil
import struct
import zipfile

import numpy as np
from PIL import Image
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt

try:
    import OpenEXR
except ImportError:  # only needed for write_exr
    OpenEXR = None


def direction(az, el):
    """Unit vector for an azimuth (0 = north/-Z, +90 deg = east/+X) and elevation."""
    return np.stack(
        [np.sin(az) * np.cos(el), np.sin(el), -np.cos(az) * np.cos(el)], axis=-1
    )


def _hash3(ix, iy, iz, seed):
    h = (
        ix.astype(np.int64) * 374761393
        + iy.astype(np.int64) * 668265263
        + iz.astype(np.int64) * 1440662683
        + seed * 2654435761
    ) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    h = h ^ (h >> 16)
    return (h & 0xFFFF) / 65535.0


def _fade(t):
    return t * t * t * (t * (t * 6 - 15) + 10)


def value_noise3(x, y, z, seed=0):
    """Smooth lattice noise in [-1, 1]."""
    x, y, z = np.asarray(x, float), np.asarray(y, float), np.asarray(z, float)
    xi, yi, zi = np.floor(x), np.floor(y), np.floor(z)
    fx, fy, fz = _fade(x - xi), _fade(y - yi), _fade(z - zi)
    xi, yi, zi = xi.astype(np.int64), yi.astype(np.int64), zi.astype(np.int64)
    out = 0.0
    for dx in (0, 1):
        wx = fx if dx else 1 - fx
        for dy in (0, 1):
            wy = fy if dy else 1 - fy
            for dz in (0, 1):
                wz = fz if dz else 1 - fz
                out = out + wx * wy * wz * _hash3(xi + dx, yi + dy, zi + dz, seed)
    return out * 2.0 - 1.0


def fbm(x, y, octaves=5, seed=0, z=0.0):
    total, amp, norm = 0.0, 1.0, 0.0
    for i in range(octaves):
        f = 2.0 ** i
        total = total + amp * value_noise3(x * f, y * f, np.asarray(z) * f + i * 17.3, seed + i)
        norm += amp
        amp *= 0.5
    return total / norm


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


# --------------------------------------------------------------------------
# Color helpers
# --------------------------------------------------------------------------

def srgb_to_linear(c):
    c = np.asarray(c, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def tonemap(c, exposure=1.0):
    """Soft shoulder so bright aurora and moon roll off instead of clipping."""
    c = np.maximum(c * exposure, 0.0)
    return c / (1.0 + c * 0.35)


def to_image(linear_rgb, exposure=1.0):
    return Image.fromarray(
        (linear_to_srgb(tonemap(linear_rgb, exposure)) * 255 + 0.5).astype(np.uint8)
    )


def hex_lin(h):
    return srgb_to_linear(np.array([int(h[i : i + 2], 16) for i in (1, 3, 5)]) / 255.0)



class Palette:
    """A tiny texture of flat colours x shade levels. Low-poly meshes point their
    UVs at a cell, so a whole scene of props shares one material and one draw."""

    def __init__(self, colors, shades=8, cell=16, exposure_scale=0.62, glow_scale=1.6):
        # colors: {name: (sRGB hex, glows)}
        self.colors = colors
        self.names = list(colors)
        self.shades = shades
        self.cell = cell
        self.exposure_scale = exposure_scale
        self.glow_scale = glow_scale

    def shade_factor(self, level):
        return 0.18 + level * (1.25 / (self.shades - 1))

    def uv(self, name, level):
        row = self.names.index(name)
        return (level + 0.5) / self.shades, 1 - (row + 0.5) / len(self.names)

    def texture(self):
        c = self.cell
        img = np.zeros((len(self.names) * c, self.shades * c, 3))
        for r, name in enumerate(self.names):
            hexc, glows = self.colors[name]
            base = hex_lin(hexc)
            for s in range(self.shades):
                col = base * (self.glow_scale if glows else self.shade_factor(s) * self.exposure_scale)
                img[r * c : (r + 1) * c, s * c : (s + 1) * c] = col
        return img


class MeshBuilder:
    """Accumulates flat-shaded triangles whose UVs point into the palette."""

    def __init__(self, palette, light=None):
        self.palette = palette
        self.light = light or (lambda n: 0.35 + 0.65 * max(0.0, float(n[1])))
        self.points = []
        self.uvs = []
        self.normals = []

    def tri(self, a, b, c, color, ref=None, level=None):
        a, b, c = (np.asarray(p, float) for p in (a, b, c))
        n = np.cross(b - a, c - a)
        ln = np.linalg.norm(n)
        if ln < 1e-12:
            return
        n /= ln
        if ref is not None and np.dot(n, (a + b + c) / 3 - np.asarray(ref, float)) < 0:
            b, c = c, b
            n = -n
        if level is None:
            shades = self.palette.shades
            level = int(np.clip(self.light(n) / 1.1 * (shades - 0.01), 0, shades - 1))
        u, v = self.palette.uv(color, level)
        self.points.extend([a, b, c])
        self.uvs.extend([(u, v)] * 3)
        self.normals.extend([n] * 3)

    def quad(self, a, b, c, d, color, ref=None, level=None):
        self.tri(a, b, c, color, ref, level)
        self.tri(a, c, d, color, ref, level)

    def cylinder(self, p0, p1, r0, r1, sides, color, caps=True, twist=0.0, cap_color=None):
        p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
        axis = p1 - p0
        length = np.linalg.norm(axis)
        axis /= length
        helper = np.array([0, 1, 0]) if abs(axis[1]) < 0.9 else np.array([1, 0, 0])
        e1 = np.cross(axis, helper)
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(axis, e1)
        ang = [twist + 2 * math.pi * i / sides for i in range(sides)]
        ring0 = [p0 + r0 * (math.cos(t) * e1 + math.sin(t) * e2) for t in ang]
        ring1 = [p1 + r1 * (math.cos(t) * e1 + math.sin(t) * e2) for t in ang]
        mid = (p0 + p1) / 2
        for i in range(sides):
            j = (i + 1) % sides
            if r1 <= 1e-6:
                self.tri(ring0[i], ring0[j], p1, color, ref=mid - axis * length * 0.25)
            elif r0 <= 1e-6:
                self.tri(p0, ring1[i], ring1[j], color, ref=mid + axis * length * 0.25)
            else:
                ref_i = mid
                self.quad(ring0[i], ring0[j], ring1[j], ring1[i], color, ref=ref_i)
        if caps:
            cc = cap_color or color
            if r0 > 1e-6:
                for i in range(sides):
                    self.tri(p0, ring0[i], ring0[(i + 1) % sides], cc, ref=p0 + axis)
            if r1 > 1e-6:
                for i in range(sides):
                    self.tri(p1, ring1[i], ring1[(i + 1) % sides], cc, ref=p1 - axis)

    def box(self, center, size, color, yaw=0.0):
        cx, cy, cz = center
        sx, sy, sz = (s / 2 for s in size)
        cy_, sy_ = math.cos(yaw), math.sin(yaw)
        def P(dx, dy, dz):
            x, z = dx * cy_ + dz * sy_, -dx * sy_ + dz * cy_
            return (cx + x, cy + dy, cz + z)
        c = [P(dx, dy, dz) for dx in (-sx, sx) for dy in (-sy, sy) for dz in (-sz, sz)]
        faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        for f in faces:
            self.quad(*(c[i] for i in f), color, ref=center)

    def rock(self, center, radius, rng, color="stone", squash=0.7):
        t = (1 + 5 ** 0.5) / 2
        verts = np.array(
            [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t),
             (0, -1, -t), (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)], float)
        verts /= np.linalg.norm(verts, axis=1, keepdims=True)
        faces = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4),
                 (11, 10, 2), (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8),
                 (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
        jitter = rng.uniform(0.75, 1.2, size=(12, 1))
        scale = np.array([radius, radius * squash, radius * rng.uniform(0.8, 1.2)])
        yaw = rng.uniform(0, 2 * math.pi)
        rot = np.array([[math.cos(yaw), 0, math.sin(yaw)], [0, 1, 0], [-math.sin(yaw), 0, math.cos(yaw)]])
        pts = (verts * jitter * scale) @ rot.T + np.asarray(center, float)
        for f in faces:
            self.tri(*(pts[i] for i in f), color, ref=center)

    def extend(self, other):
        self.points += other.points
        self.uvs += other.uvs
        self.normals += other.normals

    def empty(self):
        return not self.points




def make_material(stage, path, *, texture=None, emissive_tex=False, diffuse=(0.0, 0.0, 0.0),
                  emissive=None, metallic=0.0, roughness=1.0, opacity_from_texture=False, opacity=None):
    mat = UsdShade.Material.Define(stage, path)
    pbr = UsdShade.Shader.Define(stage, f"{path}/PreviewSurface")
    pbr.CreateIdAttr("UsdPreviewSurface")
    pbr.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(float(metallic))
    pbr.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(roughness))
    pbr.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*diffuse))
    if emissive is not None:
        pbr.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*emissive))
    if opacity is not None:
        pbr.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(float(opacity))
    mat.CreateSurfaceOutput().ConnectToSource(pbr.ConnectableAPI(), "surface")
    if texture:
        reader = UsdShade.Shader.Define(stage, f"{path}/UVReader")
        reader.CreateIdAttr("UsdPrimvarReader_float2")
        reader.CreateInput("varname", Sdf.ValueTypeNames.String).Set("st")
        reader.CreateOutput("result", Sdf.ValueTypeNames.Float2)
        tex = UsdShade.Shader.Define(stage, f"{path}/Texture")
        tex.CreateIdAttr("UsdUVTexture")
        tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(texture))
        tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), "result")
        tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
        tex.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
        tex.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("clamp" if opacity_from_texture else "repeat")
        rgb = tex.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
        target = "emissiveColor" if emissive_tex else "diffuseColor"
        pbr.CreateInput(target, Sdf.ValueTypeNames.Color3f).ConnectToSource(rgb)
        if opacity_from_texture:
            a = tex.CreateOutput("a", Sdf.ValueTypeNames.Float)
            pbr.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(a)
    return mat


def define_mesh(stage, path, points, faces, uvs=None, uv_interp="vertex", normals=None,
                normal_interp="faceVarying", material=None):
    mesh = UsdGeom.Mesh.Define(stage, path)
    pts = np.asarray(points, dtype=np.float32)
    mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(pts))
    counts = [len(f) for f in faces]
    indices = [i for f in faces for i in f]
    mesh.CreateFaceVertexCountsAttr(Vt.IntArray(counts))
    mesh.CreateFaceVertexIndicesAttr(Vt.IntArray(indices))
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(*pts.min(0).tolist()), Gf.Vec3f(*pts.max(0).tolist())]))
    if normals is not None:
        mesh.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(np.asarray(normals, dtype=np.float32)))
        mesh.SetNormalsInterpolation(normal_interp)
    if uvs is not None:
        pv = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, uv_interp)
        pv.Set(Vt.Vec2fArray.FromNumpy(np.asarray(uvs, dtype=np.float32)))
    if material is not None:
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
    return mesh


def define_builder_mesh(stage, path, mb, material):
    n = len(mb.points)
    faces = [(i, i + 1, i + 2) for i in range(0, n, 3)]
    return define_mesh(stage, path, mb.points, faces, uvs=mb.uvs, uv_interp="vertex",
                       normals=mb.normals, normal_interp="vertex", material=material)


def sky_dome(radius, nu=96, nv=48):
    pts, uvs, faces = [], [], []
    for j in range(nv + 1):
        v = j / nv
        el = (v - 0.5) * math.pi
        for i in range(nu + 1):
            u = i / nu
            az = (u - 0.5) * 2 * math.pi
            d = direction(np.float64(az), np.float64(el))
            pts.append(d * radius)
            uvs.append((u, v))
    for j in range(nv):
        for i in range(nu):
            a = j * (nu + 1) + i
            b = a + 1
            c = a + nu + 2
            d = a + nu + 1
            faces.append((a, b, c, d))
    # Make sure faces point inward (toward the viewer at the center).
    p = np.asarray(pts)
    a, b, c, _ = faces[len(faces) // 2]
    n = np.cross(p[b] - p[a], p[c] - p[a])
    if np.dot(n, p[a]) > 0:
        faces = [tuple(reversed(f)) for f in faces]
    return pts, faces, uvs


def compact(pts, faces, uvs, normals=None):
    used = sorted({i for f in faces for i in f})
    remap = {old: new for new, old in enumerate(used)}
    pts = np.asarray(pts)[used]
    uvs = np.asarray(uvs)[used]
    faces = [tuple(remap[i] for i in f) for f in faces]
    if normals is not None:
        normals = np.asarray(normals)[used]
    return pts, faces, uvs, normals


def animate_translate(xformable, amplitude, period_frames, phase, total_frames):
    op = xformable.AddTranslateOp(opSuffix="drift")
    for f in range(0, total_frames + 1, 6):
        t = 2 * math.pi * (f / period_frames) + phase
        op.Set(Gf.Vec3d(amplitude[0] * math.sin(t * 0.5), amplitude[1] * math.sin(t), amplitude[2] * math.cos(t * 0.5)), f)


def animate_rotate_y(xformable, degrees, total_frames):
    op = xformable.AddRotateYOp(opSuffix="drift")
    for f in range(0, total_frames + 1, 12):
        op.Set(float(degrees * math.sin(2 * math.pi * f / total_frames)), f)


def animate_spin_y(xformable, revolutions, total_frames, phase_degrees=0.0, step=12):
    """Continuous rotation about Y: `revolutions` full turns over the loop
    (negative = clockwise seen from above), so it loops seamlessly."""
    op = xformable.AddRotateYOp()
    op.Set(float(phase_degrees))      # default value, for viewers that don't play animation
    for f in range(0, total_frames + 1, step):
        op.Set(float(phase_degrees + 360.0 * revolutions * f / total_frames), f)
    return op


def new_stage(path, root_name, *, meters=1.0, seconds=30, fps=24):
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, meters)
    root = UsdGeom.Xform.Define(stage, f"/{root_name}")
    stage.SetDefaultPrim(root.GetPrim())
    stage.SetStartTimeCode(0)
    stage.SetEndTimeCode(seconds * fps)
    stage.SetTimeCodesPerSecond(fps)
    stage.SetFramesPerSecond(fps)
    return stage, root


def package(stage_path, usdz_path):
    """Write a USDZ by hand: an uncompressed zip whose files start on 64-byte
    boundaries, root layer first, textures kept under textures/."""
    stage = Usd.Stage.Open(str(stage_path))
    textures = sorted({
        prim.GetAttribute("inputs:file").Get().path
        for prim in stage.Traverse()
        if prim.GetAttribute("inputs:file") and prim.GetAttribute("inputs:file").Get()
    })
    work = stage_path.parent
    entries = [stage_path.name] + textures
    if usdz_path.exists():
        usdz_path.unlink()
    with zipfile.ZipFile(usdz_path, "w", zipfile.ZIP_STORED) as zf:
        for arcname in entries:
            data = (work / arcname).read_bytes()
            info = zipfile.ZipInfo(arcname, date_time=(2026, 9, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            offset = zf.fp.tell()
            pad = -(offset + 30 + len(arcname.encode()) + 4) % 64
            info.extra = struct.pack("<HH", 0x1986, pad) + b"\0" * pad
            zf.writestr(info, data)
    with zipfile.ZipFile(usdz_path) as zf:
        for info in zf.infolist():
            start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
            assert start % 64 == 0, f"{info.filename} is not 64-byte aligned"


def check(usdz_path):
    """Run every validator USD ships (package layout, texture formats, shading,
    stage metadata...) against a finished .usdz."""
    from pxr import UsdValidation

    registry = UsdValidation.ValidationRegistry()
    names = [m.name for m in registry.GetAllValidatorMetadata()]
    context = UsdValidation.ValidationContext(registry.GetOrLoadValidatorsByName(names))
    stage = Usd.Stage.Open(str(usdz_path))
    errors = context.Validate(stage)
    for e in errors:
        print("   !", e.GetMessage())
    if not errors:
        print(f"   ✓ passed {len(names)} USD validators")
    return not errors

def copy_textures(work, textures):
    (work / "textures").mkdir(exist_ok=True)
    for name, path in textures.items():
        shutil.copy(path, work / "textures" / name)


def write_exr(path, linear_rgb):
    h, w, _ = linear_rgb.shape
    rgba = np.concatenate([linear_rgb, np.ones((h, w, 1))], axis=-1).astype(np.float16)
    header = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}
    if OpenEXR is None:
        raise RuntimeError("pip install OpenEXR to write .exr lighting maps")
    with OpenEXR.File(header, {"RGBA": rgba}) as f:
        f.write(str(path))






def splat_stars(img, rng, count, *, band_normal=None, min_elevation=-0.02, horizon_fade=True, sigma=0.55):
    """Draw stars into an equirectangular float image (rows = elevation).

    band_normal: crowd the stars toward the great circle with this normal (a Milky Way).
    horizon_fade: dim stars near the horizon (atmospheric extinction); off for space.
    """
    h, w, _ = img.shape
    v = rng.normal(size=(count, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    if band_normal is not None:
        nb = np.asarray(band_normal, float)
        nb = nb / np.linalg.norm(nb)
        v = v - np.outer(v @ nb, nb) * rng.uniform(0.75, 1.0, size=(count, 1))
        v /= np.linalg.norm(v, axis=1, keepdims=True)
    el = np.arcsin(np.clip(v[:, 1], -1, 1))
    az = np.arctan2(v[:, 0], -v[:, 2])
    keep = el > min_elevation
    el, az = el[keep], az[keep]
    mags = rng.pareto(2.2, size=el.shape[0]) * 0.18 + 0.03
    mags = np.minimum(mags, 3.5)
    temp = rng.uniform(0, 1, size=el.shape[0])
    tint = np.stack([0.85 + 0.25 * temp, 0.90 + 0.05 * temp, 1.15 - 0.30 * temp], axis=1)
    if horizon_fade:
        mags *= np.clip(el / 0.25, 0.15, 1.0)
    px = (az / (2 * np.pi) + 0.5) * w
    py = (0.5 - el / np.pi) * h
    sigma_y = sigma * h / 2048
    for x, y, m, c, e in zip(px, py, mags, tint, el):
        sx = sigma_y / max(math.cos(e), 0.08)
        rx, ry = int(math.ceil(sx * 3)), int(math.ceil(sigma_y * 3))
        x0, y0 = int(x), int(y)
        ys = np.arange(y0 - ry, y0 + ry + 1)
        xs = np.arange(x0 - rx, x0 + rx + 1)
        ys = ys[(ys >= 0) & (ys < h)]
        if ys.size == 0:
            continue
        gx = np.exp(-(((xs + 0.5 - x) / sx) ** 2) / 2)
        gy = np.exp(-(((ys + 0.5 - y) / sigma_y) ** 2) / 2)
        patch = np.outer(gy, gx)[..., None] * (m * c)
        img[ys[:, None], (xs % w)[None, :]] += patch


__all__ = [
    "direction", "value_noise3", "fbm", "smoothstep",
    "srgb_to_linear", "linear_to_srgb", "tonemap", "to_image", "hex_lin", "write_exr",
    "splat_stars", "Palette", "MeshBuilder", "sky_dome", "compact",
    "new_stage", "make_material", "define_mesh", "define_builder_mesh",
    "animate_translate", "animate_rotate_y", "animate_spin_y", "package", "copy_textures", "check",
]
