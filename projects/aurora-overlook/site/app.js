// Aurora Overlook — spatial web experience for Safari on visionOS 27.
//
// Immersive model API used here (modeled on the Fullscreen API):
//   model.requestImmersive()      enter the website environment (needs a tap)
//   document.exitImmersive()      leave it (the Digital Crown also works)
//   document.immersiveEnabled     feature/permission detection
//   document.immersiveElement     the <model> currently presented, or null
//   "immersivechange" / "immersiveerror" events
//   model.entityTransform         DOMMatrix placing the world around the visitor
//   model.ready                   promise that resolves once the USDZ is loaded

const $ = (selector) => document.querySelector(selector);

const world = $('#world');
const diorama = $('#diorama');
const enterButton = $('#enter');
const leaveButton = $('#leave');
const statusLine = $('#status');
const pill = $('#support-pill');
const dock = $('#dock');
const dockName = $('#dock-name');

// Turn the valley so the view you came for sits beside the Safari window,
// not behind it (Apple's guidance for immersive web environments).
const WINDOW_CLEARANCE_DEGREES = 30;

const hasModel = typeof window.HTMLModelElement === 'function' && world instanceof window.HTMLModelElement;
const canImmersive = hasModel && typeof world.requestImmersive === 'function' && document.immersiveEnabled === true;

const viewpoints = new Map();
let currentId = 'deck';
let isImmersive = false;
let reenterAfterExit = false;

function setStatus(text) {
  statusLine.textContent = text;
}

function setBusy(busy) {
  enterButton.setAttribute('aria-busy', String(busy));
  enterButton.querySelector('span').textContent = busy ? 'Opening the valley…' : 'Step inside';
}

// The immersive origin is the visitor's feet with real-world meters, Y up and
// -Z straight ahead. Rotate the chosen heading in front of the visitor (offset
// so it clears the window), then shift the world so the viewpoint is at 0,0,0.
export function transformFor(viewpoint) {
  const [x, y, z] = viewpoint.position;
  const matrix = new DOMMatrix();
  matrix.rotateSelf(0, viewpoint.heading - WINDOW_CLEARANCE_DEGREES, 0);
  matrix.translateSelf(-x, -y, -z);
  return matrix;
}

function applyViewpoint() {
  const viewpoint = viewpoints.get(currentId);
  if (!viewpoint) return;
  try {
    world.entityTransform = transformFor(viewpoint);
  } catch (error) {
    // Some builds only accept a transform once the model has loaded.
    world.ready?.then(() => { world.entityTransform = transformFor(viewpoint); }).catch(() => {});
  }
  dockName.textContent = viewpoint.name;
  for (const button of document.querySelectorAll('.dock-views [data-go]')) {
    button.setAttribute('aria-pressed', String(button.dataset.go === currentId));
  }
  for (const card of document.querySelectorAll('.viewpoint')) {
    card.classList.toggle('is-current', isImmersive && card.dataset.viewpoint === currentId);
  }
}

// Must run synchronously inside the tap handler: Safari only opens the
// environment in response to a user gesture, so nothing may be awaited first.
function enter(id = currentId) {
  if (!canImmersive) return;
  currentId = id;
  applyViewpoint();
  setBusy(true);
  setStatus('Opening the valley… the first visit downloads about 5 MB.');
  let request;
  try {
    request = world.requestImmersive();
  } catch (error) {
    request = Promise.reject(error);
  }
  Promise.resolve(request)
    .then(() => {
      setStatus('');
      // Re-apply once loaded in case the first assignment was ignored.
      return world.ready?.then(applyViewpoint);
    })
    .catch((error) => {
      console.warn('requestImmersive failed', error);
      setStatus('The valley couldn’t open this time. Tap “Step inside” to try again.');
    })
    .finally(() => setBusy(false));
}

function goTo(id) {
  if (!viewpoints.has(id)) return;
  if (!isImmersive) {
    enter(id);
    return;
  }
  if (id === currentId) return;
  // Like Apple's seat-preview demo: step out and straight back in at the new spot.
  currentId = id;
  reenterAfterExit = true;
  setStatus(`Moving to ${viewpoints.get(id).name}…`);
  Promise.resolve(document.exitImmersive()).catch(() => { reenterAfterExit = false; });
}

function onImmersiveChange() {
  const nowImmersive = document.immersiveElement === world;
  if (nowImmersive === isImmersive) return;
  isImmersive = nowImmersive;
  document.body.classList.toggle('is-immersive', isImmersive);
  dock.hidden = !isImmersive;
  enterButton.hidden = isImmersive || !canImmersive;

  if (isImmersive) {
    applyViewpoint();
    try { world.play?.(); } catch { /* the scene is still beautiful standing still */ }
    leaveButton.focus({ preventScroll: true });
    return;
  }

  applyViewpoint();
  if (reenterAfterExit) {
    reenterAfterExit = false;
    enter(currentId);
    return;
  }
  setStatus('Welcome back. The valley will be waiting.');
}

function showSupport() {
  if (canImmersive) {
    pill.dataset.state = 'ready';
    pill.textContent = 'Ready to step inside';
    enterButton.hidden = false;
    for (const button of document.querySelectorAll('.viewpoint [data-go]')) button.hidden = false;
    return;
  }
  document.documentElement.classList.toggle('no-model', !hasModel);
  if (hasModel) {
    pill.dataset.state = 'inline';
    pill.textContent = '3D ready · immersive on Vision Pro';
    setStatus('Your browser shows the 3D valley inline. Open this page in Safari on Apple Vision Pro to step inside it.');
  } else {
    pill.dataset.state = 'none';
    pill.textContent = 'Preview mode';
    $('#stage-caption').textContent = 'Preview image · open on Vision Pro for 3D';
    setStatus('Open this page in Safari on Apple Vision Pro (visionOS 27) to step inside. Meanwhile, look around in 360° below.');
  }
  $('#pano360').hidden = false;
  import('./pano360.js').then(({ mountPanorama }) => mountPanorama($('#pano360-canvas'), 'assets/sky360.jpg'));
}

async function loadViewpoints() {
  const response = await fetch('assets/viewpoints.json');
  const data = await response.json();
  for (const viewpoint of data.viewpoints) viewpoints.set(viewpoint.id, viewpoint);
}

function wireEvents() {
  enterButton.addEventListener('click', () => enter());
  leaveButton.addEventListener('click', () => {
    reenterAfterExit = false;
    Promise.resolve(document.exitImmersive?.()).catch(() => {});
  });
  document.addEventListener('click', (event) => {
    const target = event.target.closest('[data-go]');
    if (target) goTo(target.dataset.go);
  });

  // Listen on both the element and the document; the handler is idempotent.
  world.addEventListener('immersivechange', onImmersiveChange);
  document.addEventListener('immersivechange', onImmersiveChange);
  world.addEventListener('immersiveerror', () => {
    reenterAfterExit = false;
    setBusy(false);
    setStatus('The valley stopped unexpectedly. Tap “Step inside” to go back.');
  });

  if (hasModel && diorama.ready) {
    diorama.ready.catch(() => {
      document.documentElement.classList.add('no-model');
    });
  }
}

wireEvents();
loadViewpoints()
  .catch((error) => {
    console.warn('viewpoints.json failed to load, using the deck only', error);
    viewpoints.set('deck', { id: 'deck', name: 'Overlook deck', position: [0, 0, 0], heading: 0 });
  })
  .finally(showSupport);
