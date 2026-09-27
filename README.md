# Vision Pro projects

A home for Apple Vision Pro side projects. Each project gets its own folder, and
all of them share one dev server and one GitHub Pages site.

| Project | What it is |
| --- | --- |
| [Aurora Overlook](projects/aurora-overlook/) | A **spatial web experience** for Safari on visionOS 27: tap once and you're standing in a moonlit valley under the northern lights. Uses the new immersive `<model>` API (`requestImmersive()`). |

## Layout

```
projects/
  <project-slug>/
    project.json     title, description, thumbnail, tags (shown in the gallery)
    README.md        what it is + how to build/run it
    site/            static files published at /<project-slug>/
    tools/           asset generators, render scripts (optional)
    screenshots/     images for the README/PRs (optional)
scripts/
  serve.py           dev server with USDZ/EXR MIME types + your LAN address for the headset
  build_gallery.py   puts every projects/*/site together behind a gallery page
.github/workflows/
  pages.yml          publishes the gallery to GitHub Pages on every push to main
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

**From anywhere:** in the repo settings, go to *Settings → Pages → Source* and
pick **GitHub Actions**. After that, every push to `main` publishes to
`https://<user>.github.io/<repo>/`.

## Adding a project

1. `mkdir -p projects/my-thing/site`, then put an `index.html` in it.
2. Add `projects/my-thing/project.json`:
   ```json
   { "title": "My Thing", "description": "One line.", "thumbnail": "assets/thumb.png", "tags": ["visionOS"] }
   ```
3. `python3 scripts/serve.py` and open it on the headset.
