// Renders preview images of a project's USDZ files with three.js in headless
// Chromium, driven by the project's tools/previews.json.
//
//   cd shared/render && npm install
//   node render.mjs ../../projects/aurora-overlook            # every job
//   node render.mjs ../../projects/aurora-overlook view-deck  # jobs whose output matches
//
// previews.json:
//   {
//     "environment": "assets/light.exr",          // optional, used as scene.environment
//     "additive": "aurora|glow",                    // optional regex: meshes drawn as additive glow
//     "viewpoints": "assets/viewpoints.json",       // optional, for jobs with "viewpoint": "<id>"
//     "jobs": [
//       { "type": "orbit", "model": "assets/x.usdz", "out": "assets/poster.png", "azimuth": 30, "elevation": 22, "distance": 0.7 },
//       { "type": "view", "model": "assets/x.usdz", "out": "assets/view.png", "viewpoint": "deck", "pitch": 8, "fov": 75 },
//       { "type": "view", "model": "assets/x.usdz", "out": "assets/view2.png", "position": [0, 0, 0], "yaw": 90 },
//       { "type": "equirect", "model": "assets/x.usdz", "out": "assets/360.jpg", "viewpoint": "deck",
//         "panorama": { "out": "assets/pano.jpg", "top": 45, "bottom": -25 } }
//     ]
//   }
// Paths are relative to the project's site/ folder.

import { createServer } from 'node:http';
import { readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { extname, join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const here = dirname(fileURLToPath(import.meta.url));
const project = resolve(process.argv[2] ?? '.');
const site = join(project, 'site');
const config = JSON.parse(await readFile(join(project, 'tools', 'previews.json'), 'utf8'));
const only = process.argv.slice(3);

const types = { '.html': 'text/html', '.js': 'text/javascript', '.usdz': 'model/vnd.usdz+zip', '.exr': 'image/aces', '.json': 'application/json' };
const server = createServer(async (req, res) => {
  const path = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  const file = path.startsWith('/site/') ? join(site, path.slice(6)) : join(here, path === '/' ? 'render.html' : path);
  try {
    const body = await readFile(file);
    res.writeHead(200, { 'content-type': types[extname(file)] ?? 'application/octet-stream' });
    res.end(body);
  } catch {
    res.writeHead(404).end();
  }
}).listen(0);
const base = `http://127.0.0.1:${server.address().port}`;

const executablePath = process.env.CHROMIUM_PATH
  ?? ['/opt/pw-browsers/chromium-1194/chrome-linux/chrome'].find(existsSync);
const browser = await chromium.launch({
  executablePath,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage();
page.on('pageerror', (e) => console.error('  [page error]', e));
await page.goto(`${base}/render.html`);
await page.waitForFunction(() => window.ready === true);
await page.evaluate((c) => window.configure({ environmentMap: c.environment, additivePattern: c.additive }), config);

const viewpoints = config.viewpoints
  ? Object.fromEntries(JSON.parse(await readFile(join(site, config.viewpoints), 'utf8')).viewpoints.map((v) => [v.id, v]))
  : {};

const save = async (name, dataUrl) => {
  const buf = Buffer.from(dataUrl.split(',')[1], 'base64');
  await writeFile(join(site, name), buf);
  console.log(`• ${name} (${(buf.length / 1e3).toFixed(0)} kB)`);
};

for (const job of config.jobs) {
  if (only.length && !only.some((o) => job.out.includes(o))) continue;
  const vp = job.viewpoint ? viewpoints[job.viewpoint] : null;
  const args = { ...job, position: job.position ?? vp?.position ?? [0, 0, 0], yaw: job.yaw ?? vp?.heading ?? 0 };
  if (job.type === 'orbit') {
    const { png, size } = await page.evaluate((a) => window.renderOrbit(a), args);
    console.log(`  ${job.model} size (m): ${size.map((v) => v.toFixed(3)).join(' × ')}`);
    await save(job.out, png);
  } else if (job.type === 'view') {
    await save(job.out, await page.evaluate((a) => window.renderView(a), args));
  } else if (job.type === 'equirect') {
    const equirect = await page.evaluate((a) => window.renderEquirect(a), args);
    await save(job.out, equirect);
    if (job.panorama) {
      const { out, top, bottom } = job.panorama;
      await save(out, await page.evaluate(([d, t, b]) => window.cropPanorama(d, t, b), [equirect, top, bottom]));
    }
  } else {
    console.warn(`unknown job type ${job.type}`);
  }
}

await browser.close();
server.close();
