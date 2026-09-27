// Aurora Overlook — spatial web experience for Safari on visionOS 27.
// The immersive plumbing (requestImmersive, viewpoints, re-entry) lives in
// shared/web/immersive.js; this file wires it to the page.

import { ImmersiveWorld } from '../shared/immersive.js';

const $ = (selector) => document.querySelector(selector);

const world = new ImmersiveWorld($('#world'), {
  clearance: 30,
  loadingText: 'Opening the valley… the first visit downloads about 5 MB.',
});
const diorama = $('#diorama');
const enterButton = $('#enter');
const statusLine = $('#status');
const pill = $('#support-pill');
const dock = $('#dock');

function setBusy(busy) {
  enterButton.setAttribute('aria-busy', String(busy));
  enterButton.querySelector('span').textContent = busy ? 'Opening the valley…' : 'Step inside';
}

function showViewpoint() {
  const viewpoint = world.viewpoint;
  if (!viewpoint) return;
  $('#dock-name').textContent = viewpoint.name;
  for (const button of document.querySelectorAll('.dock-views [data-go]')) {
    button.setAttribute('aria-pressed', String(button.dataset.go === viewpoint.id));
  }
  for (const card of document.querySelectorAll('.viewpoint')) {
    card.classList.toggle('is-current', world.isImmersive && card.dataset.viewpoint === viewpoint.id);
  }
}

world.addEventListener('status', ({ detail }) => {
  setBusy(detail.busy);
  statusLine.textContent = detail.text;
});

world.addEventListener('change', ({ detail }) => {
  document.body.classList.toggle('is-immersive', detail.immersive);
  dock.hidden = !detail.immersive;
  enterButton.hidden = detail.immersive || !world.support.immersive;
  showViewpoint();
  if (detail.immersive) {
    $('#leave').focus({ preventScroll: true });
  } else if (!world.reenterAfterExit) {
    statusLine.textContent = 'Welcome back. The valley will be waiting.';
  }
});

function showSupport() {
  if (world.support.immersive) {
    pill.dataset.state = 'ready';
    pill.textContent = 'Ready to step inside';
    enterButton.hidden = false;
    for (const button of document.querySelectorAll('.viewpoint [data-go]')) button.hidden = false;
    return;
  }
  document.documentElement.classList.toggle('no-model', !world.support.model);
  if (world.support.model) {
    pill.dataset.state = 'inline';
    pill.textContent = '3D ready · immersive on Vision Pro';
    statusLine.textContent = 'Your browser shows the 3D valley inline. Open this page in Safari on Apple Vision Pro to step inside it.';
  } else {
    pill.dataset.state = 'none';
    pill.textContent = 'Preview mode';
    $('#stage-caption').textContent = 'Preview image · open on Vision Pro for 3D';
    statusLine.textContent = 'Open this page in Safari on Apple Vision Pro (visionOS 27) to step inside. Meanwhile, look around in 360° below.';
  }
  $('#pano360').hidden = false;
  import('../shared/pano360.js').then(({ mountPanorama }) => mountPanorama($('#pano360-canvas'), 'assets/sky360.jpg'));
}

enterButton.addEventListener('click', () => world.enter());
$('#leave').addEventListener('click', () => world.leave());
document.addEventListener('click', (event) => {
  const target = event.target.closest('[data-go]');
  if (target) world.goTo(target.dataset.go);
});
if (world.support.model && diorama.ready) {
  diorama.ready.catch(() => document.documentElement.classList.add('no-model'));
}

fetch('assets/viewpoints.json')
  .then((response) => response.json())
  .then((data) => world.setViewpoints(data.viewpoints))
  .catch((error) => {
    console.warn('viewpoints.json failed to load, using the deck only', error);
    world.setViewpoints([{ id: 'deck', name: 'Overlook deck', position: [0, 0, 0], heading: 0 }]);
  })
  .finally(() => {
    showViewpoint();
    showSupport();
  });
