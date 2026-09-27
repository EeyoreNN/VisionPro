// Shared helpers for immersive website environments in Safari on visionOS 27.
//
// Immersive <model> API (modeled on the Fullscreen API):
//   model.requestImmersive()      enter the environment (needs a tap)
//   document.exitImmersive()      leave it (the Digital Crown also works)
//   document.immersiveEnabled     feature/permission detection
//   document.immersiveElement     the <model> currently presented, or null
//   "immersivechange" / "immersiveerror" events
//   model.entityTransform         DOMMatrix placing the world around the visitor
//   model.ready                   promise that resolves once the USDZ is loaded
//
// Usage:
//   import { ImmersiveWorld } from '../shared/immersive.js';
//   const world = new ImmersiveWorld(document.querySelector('#world'));
//   world.setViewpoints(list);                    // [{ id, name, position: [x,y,z], heading }]
//   button.onclick = () => world.enter('deck');   // call straight from the tap
//   world.addEventListener('change', (e) => ...); // e.detail = { immersive, viewpoint }
//   world.addEventListener('status', (e) => ...); // e.detail = { text, busy }

export function modelSupport(modelElement) {
  const model = typeof window.HTMLModelElement === 'function' && modelElement instanceof window.HTMLModelElement;
  const immersive = model && typeof modelElement.requestImmersive === 'function' && document.immersiveEnabled === true;
  return { model, immersive };
}

// The immersive origin is the visitor's feet: real-world meters, Y up, -Z
// straight ahead. Turn the viewpoint's heading (0 = -Z, 90 = +X) in front of
// the visitor, offset by `clearance` degrees so it sits beside the Safari
// window rather than behind it, then shift the world so the spot is at 0,0,0.
export function transformFor(viewpoint, clearance = 30) {
  const [x, y, z] = viewpoint.position;
  const matrix = new DOMMatrix();
  matrix.rotateSelf(0, viewpoint.heading - clearance, 0);
  matrix.translateSelf(-x, -y, -z);
  return matrix;
}

export class ImmersiveWorld extends EventTarget {
  constructor(model, { clearance = 30, loadingText = 'Opening…' } = {}) {
    super();
    this.model = model;
    this.clearance = clearance;
    this.loadingText = loadingText;
    this.support = modelSupport(model);
    this.viewpoints = new Map();
    this.currentId = null;
    this.isImmersive = false;
    this.reenterAfterExit = false;

    const onChange = () => this.#onImmersiveChange();
    // Listen on both; the handler ignores repeats.
    model.addEventListener('immersivechange', onChange);
    document.addEventListener('immersivechange', onChange);
    model.addEventListener('immersiveerror', () => {
      this.reenterAfterExit = false;
      this.#status('It stopped unexpectedly. Tap to go back in.', false);
    });
  }

  get viewpoint() {
    return this.viewpoints.get(this.currentId) ?? null;
  }

  setViewpoints(list) {
    this.viewpoints = new Map(list.map((v) => [v.id, v]));
    if (!this.viewpoints.has(this.currentId)) this.currentId = list[0]?.id ?? null;
  }

  applyViewpoint() {
    const viewpoint = this.viewpoint;
    if (!viewpoint) return;
    const matrix = transformFor(viewpoint, this.clearance);
    try {
      this.model.entityTransform = matrix;
    } catch {
      // Some builds only accept a transform once the model has loaded.
      this.model.ready?.then(() => { this.model.entityTransform = matrix; }).catch(() => {});
    }
  }

  // Must be called synchronously from the tap handler: Safari only opens the
  // environment in response to a user gesture, so nothing may be awaited first.
  enter(id = this.currentId) {
    if (!this.support.immersive) return Promise.resolve(false);
    if (id && this.viewpoints.has(id)) this.currentId = id;
    this.applyViewpoint();
    this.#status(this.loadingText, true);
    let request;
    try {
      request = this.model.requestImmersive();
    } catch (error) {
      request = Promise.reject(error);
    }
    return Promise.resolve(request)
      .then(() => {
        this.#status('', false);
        return this.model.ready?.then(() => this.applyViewpoint());
      })
      .then(() => true)
      .catch((error) => {
        console.warn('requestImmersive failed', error);
        this.#status('It couldn’t open this time. Tap to try again.', false);
        return false;
      });
  }

  // Move to another viewpoint. While inside, step out and straight back in at
  // the new spot (the pattern Apple's seat-preview demo uses).
  goTo(id) {
    if (!this.viewpoints.has(id)) return;
    if (!this.isImmersive) {
      this.enter(id);
      return;
    }
    if (id === this.currentId) return;
    this.currentId = id;
    this.reenterAfterExit = true;
    this.#status(`Moving to ${this.viewpoint.name}…`, true);
    Promise.resolve(document.exitImmersive()).catch(() => { this.reenterAfterExit = false; });
  }

  leave() {
    this.reenterAfterExit = false;
    return Promise.resolve(document.exitImmersive?.()).catch(() => {});
  }

  #onImmersiveChange() {
    const now = document.immersiveElement === this.model;
    if (now === this.isImmersive) return;
    this.isImmersive = now;
    this.applyViewpoint();
    if (now) {
      try { this.model.play?.(); } catch { /* still fine standing still */ }
    }
    this.dispatchEvent(new CustomEvent('change', { detail: { immersive: now, viewpoint: this.viewpoint } }));
    if (!now && this.reenterAfterExit) {
      this.reenterAfterExit = false;
      this.enter(this.currentId);
    }
  }

  #status(text, busy) {
    this.dispatchEvent(new CustomEvent('status', { detail: { text, busy } }));
  }
}
