#!/usr/bin/env python3
"""Assemble every project's site/ folder into one static site with a gallery.

    python3 scripts/build_gallery.py [out_dir]     # default: _site

Each project lives in projects/<slug>/ and is picked up when it has:
    project.json   {"title", "description", "thumbnail" (path inside site/), "tags"}
    site/          the static files to publish at /<slug>/
shared/web/ is published at /shared/, so sites import it as '../shared/<file>.js'.
Used by the GitHub Pages workflow and by scripts/serve.py.
"""

import html
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_projects():
    projects = []
    for folder in sorted((ROOT / "projects").iterdir()):
        meta_file = folder / "project.json"
        if not (meta_file.is_file() and (folder / "site").is_dir()):
            continue
        meta = json.loads(meta_file.read_text())
        meta["slug"] = folder.name
        projects.append(meta)
    return projects


def card(p):
    thumb = f'<img src="{p["slug"]}/{html.escape(p["thumbnail"])}" alt="" loading="lazy">' if p.get("thumbnail") else ""
    tags = "".join(f"<span>{html.escape(t)}</span>" for t in p.get("tags", []))
    return f"""
      <a class="card" href="{p['slug']}/">
        {thumb}
        <div class="body">
          <h2>{html.escape(p['title'])}</h2>
          <p>{html.escape(p.get('description', ''))}</p>
          <div class="tags">{tags}</div>
        </div>
      </a>"""


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vision Pro projects</title>
<meta name="color-scheme" content="dark">
<style>
  body {{ margin: 0; background: #070b16; color: #eef3ff; font: 18px/1.5 -apple-system, system-ui, sans-serif; }}
  main {{ max-width: 1200px; margin: 0 auto; padding: 56px 24px; }}
  h1 {{ font-size: clamp(36px, 5vw, 60px); letter-spacing: -0.03em; margin: 0 0 8px; }}
  .lede {{ color: #b7c3dc; margin: 0 0 40px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 24px; }}
  .card {{ display: flex; flex-direction: column; text-decoration: none; color: inherit; border-radius: 22px; overflow: hidden;
          background: rgba(22, 32, 56, 0.6); border: 1px solid rgba(190, 210, 255, 0.14); transition: transform .2s; }}
  .card:hover, .card:focus-visible {{ transform: translateY(-3px); }}
  .card img {{ width: 100%; aspect-ratio: 16 / 10; object-fit: cover; display: block; }}
  .body {{ padding: 20px 22px 24px; }}
  h2 {{ margin: 0 0 6px; font-size: 22px; }}
  .body p {{ margin: 0 0 14px; color: #b7c3dc; font-size: 16px; }}
  .tags span {{ display: inline-block; font-size: 13px; padding: 4px 10px; margin: 0 6px 6px 0; border-radius: 99px;
               background: rgba(109, 255, 179, 0.12); color: #6dffb3; }}
</style>
</head>
<body>
<main>
  <h1>Vision Pro projects</h1>
  <p class="lede">Spatial web experiments for Apple Vision Pro. Open in Safari on visionOS.</p>
  <div class="grid">{cards}
  </div>
</main>
</body>
</html>
"""


def render_index(projects):
    return PAGE.format(cards="".join(card(p) for p in projects))


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "_site").resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    projects = load_projects()
    for p in projects:
        shutil.copytree(ROOT / "projects" / p["slug"] / "site", out / p["slug"])
    # Shared browser modules (immersive helpers, 360 viewer) at /shared/.
    shutil.copytree(ROOT / "shared" / "web", out / "shared")
    (out / "index.html").write_text(render_index(projects))
    (out / ".nojekyll").write_text("")
    print(f"built {len(projects)} project(s) into {out}")


if __name__ == "__main__":
    main()
