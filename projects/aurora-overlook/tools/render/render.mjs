// Renders the page's preview images from the generated USDZ files with three.js
// in headless Chromium. Run after tools/build_assets.py:
//
//   cd tools/render && npm install && node render.mjs
//
// Writes into site/assets/: sky360.jpg (360 view from the deck, used by the
// non-visionOS fallback viewer), panorama.jpg (wide panorama for <img controls>),
// diorama-poster.png and one thumbnail per viewpoint.

import { createServer } from 'node:http';
import { readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { extname, join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const here = dirname(fileURLToPath(import.meta.url));
const assets = resolve(here, '../../site/assets');
const types = { '.exr': 'image/aces', '.html': 'text/html', '.js': 'text/javascript', '.usdz': 'model/vnd.usdz+zip', '.json': 'application/json' };

const server = createServer(async (req, res) => {
  const path = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  const file = path.startsWith('/assets/') ? join(assets, path.slice(8)) : join(here, path === '/' ? 'render.html' : path);
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
page.on('console', (m) => console.log('  [page]', m.text()));
page.on('pageerror', (e) => console.error('  [page error]', e));
await page.goto(`${base}/render.html`);
await page.waitForFunction(() => window.ready === true);

const save = async (name, dataUrl) => {
  const buf = Buffer.from(dataUrl.split(',')[1], 'base64');
  await writeFile(join(assets, name), buf);
  console.log(`• ${name} (${(buf.length / 1e3).toFixed(0)} kB)`);
};

const { viewpoints } = JSON.parse(await readFile(join(assets, 'viewpoints.json'), 'utf8'));
const only = process.argv.slice(2);
const want = (name) => only.length === 0 || only.some((o) => name.includes(o));

if (want('diorama')) {
  const { png, size } = await page.evaluate(() => window.renderOrbit({ url: '/assets/diorama.usdz' }));
  console.log('  diorama size (m):', size.map((v) => v.toFixed(3)).join(' × '));
  await save('diorama-poster.png', png);
}

for (const vp of viewpoints) {
  if (!want(`view-${vp.id}`)) continue;
  await save(`view-${vp.id}.png`, await page.evaluate((v) => window.renderView({
    url: '/assets/overlook.usdz', pos: v.position, yaw: v.heading, pitch: 8, fov: 75, w: 960, h: 600,
  }), vp));
}

if (want('sky360') || want('panorama')) {
  const deck = viewpoints.find((v) => v.id === 'deck');
  const equirect = await page.evaluate((v) => window.renderEquirect({ url: '/assets/overlook.usdz', pos: v.position, w: 4096 }), deck);
  await save('sky360.jpg', equirect);
  await save('panorama.jpg', await page.evaluate((d) => window.cropPanorama(d, 45, -25), equirect));
}

await browser.close();
server.close();
