# Vision Pro projects

A home for Apple Vision Pro side projects. Each project gets its own folder, and
all of them share one dev server and one GitHub Pages site.

Live gallery: **https://eeyorenn.github.io/VisionPro/**

| Project | What it is | Tech |
| --- | --- | --- |
| [Aurora Overlook](projects/aurora-overlook/) | Tap once and you're standing in a moonlit valley under the northern lights, with three places to stand. | Immersive `<model>` (`requestImmersive()`), `entityTransform` |
| [Orbit Room](projects/orbit-room/) | A room-scale Solar System: the Sun just past your Safari window, planets orbiting at waist height. | Immersive `<model>`, USD animation, `playbackRate` |
| [Pinch Pop](projects/pinch-pop/) | Pop iridescent bubbles with a look and a pinch, or a fingertip poke. Spatial audio, combos. | WebXR, hand tracking, `transient-pointer` |

## Layout

```
projects/
  <project-slug>/
    project.json     title, description, thumbnail, tags (shown in the gallery)
    README.md        what it is + how to build/run it
    site/            static files published at /<project-slug>/
    tools/           build_assets.py (asset generator), previews.json (render jobs)
    screenshots/     images for the README/PRs
shared/
  web/               browser modules published at /shared/ — sites import '../shared/x.js'
    immersive.js       requestImmersive + viewpoints + re-entry, for immersive <model> pages
    pano360.js         drag-to-look 360° fallback viewer
  python/
    usdkit.py          procedural USDZ toolkit: noise, low-poly MeshBuilder, materials,
                       animation, aligned USDZ packaging, USD validation, EXR writing
    requirements.txt   usd-core, numpy, pillow, OpenEXR
  render/            three.js + headless Chromium previews driven by tools/previews.json
scripts/
  serve.py           dev server with USDZ/EXR MIME types + your LAN address for the headset
  build_gallery.py   puts every projects/*/site + shared/web together behind a gallery page
.github/workflows/
  pages.yml          builds the gallery into the gh-pages branch on every push to main
```

Native visionOS apps can go in `projects/<slug>/` too (an Xcode project instead of
`site/`). They just won't show up in the web gallery.

## Try it on your Vision Pro

**On your own network:**

```sh
python3 scripts/serve.py            # serves every project, with a gallery page
```

Open the printed `http://<your-computer-ip>:8000/` address in Safari on the
Vision Pro (it has to be on the same Wi-Fi).

**From anywhere (no computer needed):** every push to `main` builds the site
into the `gh-pages` branch. Turn it on once: *Settings → Pages → Build and
deployment → Deploy from a branch → `gh-pages` / `(root)`*. The site is then at
`https://eeyorenn.github.io/VisionPro/`. Pages on a **private** repo needs a
paid GitHub plan; on the free plan, make the repo public first.

## Adding a project

1. `mkdir -p projects/my-thing/site`, then put an `index.html` in it.
2. Add `projects/my-thing/project.json`:
   ```json
   { "title": "My Thing", "description": "One line.", "thumbnail": "assets/thumb.png", "tags": ["visionOS"] }
   ```
3. For an immersive `<model>` page, import the shared helper:
   ```js
   import { ImmersiveWorld } from '../shared/immersive.js';
   const world = new ImmersiveWorld(document.querySelector('#world'));
   world.setViewpoints([{ id: 'start', name: 'Start', position: [0, 0, 0], heading: 0 }]);
   button.onclick = () => world.enter();
   ```
4. For procedural 3D, start `tools/build_assets.py` with `from usdkit import ...`
   (see either immersive project for the pattern).
5. `python3 scripts/serve.py` and open it on the headset.
