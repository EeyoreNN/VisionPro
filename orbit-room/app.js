// Orbit Room — a room-scale Solar System for Safari on visionOS 27.
// Immersive plumbing lives in shared/web/immersive.js; this file adds the
// planet list and the time controls (play/pause and playbackRate on <model>).

import { ImmersiveWorld } from '../shared/immersive.js';

const $ = (selector) => document.querySelector(selector);

const worldEl = $('#world');
const world = new ImmersiveWorld(worldEl, {
  clearance: 30,
  loadingText: 'Opening the Solar System… about 3 MB the first time.',
});
const enterButton = $('#enter');
const statusLine = $('#status');
const pill = $('#support-pill');

let paused = false;
let speed = 1;

function setBusy(busy) {
  enterButton.setAttribute('aria-busy', String(busy));
  enterButton.querySelector('span').textContent = busy ? 'Opening…' : 'Step into orbit';
}

function applyTime() {
  try {
    worldEl.playbackRate = speed;
    if (paused) worldEl.pause?.();
    else worldEl.play?.();
  } catch (error) {
    console.warn('animation controls unavailable', error);
  }
  $('#pause').textContent = paused ? 'Play' : 'Pause';
  $('#pause').setAttribute('aria-pressed', String(paused));
  for (const button of document.querySelectorAll('[data-speed]')) {
    button.setAttribute('aria-pressed', String(Number(button.dataset.speed) === speed));
  }
}

function showViewpoint() {
  const viewpoint = world.viewpoint;
  if (!viewpoint) return;
  $('#dock-name').textContent = viewpoint.name;
  for (const button of document.querySelectorAll('.dock [data-go]')) {
    button.setAttribute('aria-pressed', String(button.dataset.go === viewpoint.id));
  }
}

world.addEventListener('status', ({ detail }) => {
  setBusy(detail.busy);
  statusLine.textContent = detail.text;
});

world.addEventListener('change', ({ detail }) => {
  document.body.classList.toggle('is-immersive', detail.immersive);
  $('#dock').hidden = !detail.immersive;
  enterButton.hidden = detail.immersive || !world.support.immersive;
  showViewpoint();
  if (detail.immersive) {
    applyTime();
    $('#leave').focus({ preventScroll: true });
  } else if (!world.reenterAfterExit) {
    statusLine.textContent = 'Back on Earth. The planets keep turning without you.';
  }
});

function renderPlanets(data) {
  const list = $('#planet-list');
  const loop = data.loop_seconds;
  list.replaceChildren(...data.planets.map((p) => {
    const item = document.createElement('li');
    item.className = 'planet';
    item.style.setProperty('--planet', p.color);
    const lap = loop / p.revs_per_loop;
    item.innerHTML = `
      <span class="planet-dot" aria-hidden="true"></span>
      <div>
        <h3></h3>
        <p class="planet-stats"><span>Year <b></b></span><span>Day <b></b></span><span>Here: one lap every <b></b></span></p>
        <p class="planet-fact"></p>
      </div>`;
    item.querySelector('h3').textContent = p.title;
    const [year, day, here] = item.querySelectorAll('.planet-stats b');
    year.textContent = p.year;
    day.textContent = p.day;
    here.textContent = lap >= 60 ? `${Math.round(lap / 60)} min` : `${Math.round(lap)} s`;
    item.querySelector('.planet-fact').textContent = p.fact;
    return item;
  }));
}

function showSupport() {
  if (world.support.immersive) {
    pill.dataset.state = 'ready';
    pill.textContent = 'Ready to step in';
    enterButton.hidden = false;
    for (const button of document.querySelectorAll('.view-card [data-go]')) button.hidden = false;
    return;
  }
  document.documentElement.classList.toggle('no-model', !world.support.model);
  if (world.support.model) {
    pill.dataset.state = 'inline';
    pill.textContent = '3D ready · immersive on Vision Pro';
    statusLine.textContent = 'Your browser shows the orrery inline. Open this page in Safari on Apple Vision Pro to step into it.';
  } else {
    pill.dataset.state = 'none';
    pill.textContent = 'Preview mode';
    $('#stage-caption').textContent = 'Preview image · open on Vision Pro for 3D';
    statusLine.textContent = 'Open this page in Safari on Apple Vision Pro (visionOS 27) to step in. Meanwhile, look around the deck in 360° below.';
  }
  $('#sky360').hidden = false;
  import('../shared/pano360.js').then(({ mountPanorama }) => mountPanorama($('#pano360-canvas'), 'assets/space360.jpg'));
}

enterButton.addEventListener('click', () => world.enter());
$('#leave').addEventListener('click', () => world.leave());
$('#pause').addEventListener('click', () => { paused = !paused; applyTime(); });
document.addEventListener('click', (event) => {
  const go = event.target.closest('[data-go]');
  if (go) world.goTo(go.dataset.go);
  const rate = event.target.closest('[data-speed]');
  if (rate) { speed = Number(rate.dataset.speed); paused = false; applyTime(); }
});

fetch('assets/planets.json').then((r) => r.json()).then(renderPlanets).catch((e) => console.warn(e));
fetch('assets/viewpoints.json')
  .then((response) => response.json())
  .then((data) => world.setViewpoints(data.viewpoints))
  .catch(() => world.setViewpoints([{ id: 'deck', name: 'Observation deck', position: [0, 0, 0], heading: 0 }]))
  .finally(() => {
    showViewpoint();
    showSupport();
  });
