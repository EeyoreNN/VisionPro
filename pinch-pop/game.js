// Pinch Pop — a WebXR bubble-popping game for Apple Vision Pro.
//
// On Vision Pro (Safari, WebXR): look at a bubble and pinch (visionOS
// "transient-pointer" input), or poke it with an index fingertip (hand
// tracking). Everywhere else the same game runs in the page with a mouse.
//
// Rounds last 60 s. Blue bubbles are 1 point, gold are 5, storm bubbles
// cost 5. Quick pops chain a combo multiplier (up to ×5).

import * as THREE from 'three';

const ROUND_SECONDS = 60;
const MAX_BUBBLES = 18;
const COMBO_WINDOW = 1.4;
const TYPES = {
  normal: { points: 1, chance: 0.8, radius: [0.075, 0.14], speed: [0.03, 0.07] },
  gold: { points: 5, chance: 0.1, radius: [0.05, 0.07], speed: [0.09, 0.14] },
  storm: { points: -5, chance: 0.1, radius: [0.09, 0.13], speed: [0.02, 0.05] },
};

// ---------------------------------------------------------------------------
// Audio: tiny synthesized pops, positioned where the bubble was.
// ---------------------------------------------------------------------------

class Sound {
  constructor() {
    this.ctx = null;
  }

  unlock() {
    if (!this.ctx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      this.ctx = new Ctx();
      this.noise = this.ctx.createBuffer(1, this.ctx.sampleRate * 0.2, this.ctx.sampleRate);
      const data = this.noise.getChannelData(0);
      for (let i = 0; i < data.length; i++) data[i] = (Math.random() * 2 - 1) * Math.exp(-i / (data.length * 0.12));
    }
    if (this.ctx.state === 'suspended') this.ctx.resume();
  }

  listen(camera) {
    if (!this.ctx) return;
    const l = this.ctx.listener;
    const p = new THREE.Vector3().setFromMatrixPosition(camera.matrixWorld);
    const f = new THREE.Vector3(0, 0, -1).transformDirection(camera.matrixWorld);
    const u = new THREE.Vector3(0, 1, 0).transformDirection(camera.matrixWorld);
    if (l.positionX) {
      const t = this.ctx.currentTime;
      l.positionX.setValueAtTime(p.x, t); l.positionY.setValueAtTime(p.y, t); l.positionZ.setValueAtTime(p.z, t);
      l.forwardX.setValueAtTime(f.x, t); l.forwardY.setValueAtTime(f.y, t); l.forwardZ.setValueAtTime(f.z, t);
      l.upX.setValueAtTime(u.x, t); l.upY.setValueAtTime(u.y, t); l.upZ.setValueAtTime(u.z, t);
    } else {
      l.setPosition(p.x, p.y, p.z);
      l.setOrientation(f.x, f.y, f.z, u.x, u.y, u.z);
    }
  }

  #panner(position) {
    const panner = this.ctx.createPanner();
    panner.panningModel = 'HRTF';
    panner.distanceModel = 'inverse';
    panner.refDistance = 0.5;
    if (panner.positionX) {
      panner.positionX.value = position.x; panner.positionY.value = position.y; panner.positionZ.value = position.z;
    } else {
      panner.setPosition(position.x, position.y, position.z);
    }
    panner.connect(this.ctx.destination);
    return panner;
  }

  pop(type, position, combo) {
    if (!this.ctx) return;
    const t = this.ctx.currentTime;
    const out = this.#panner(position);

    const noise = this.ctx.createBufferSource();
    noise.buffer = this.noise;
    const band = this.ctx.createBiquadFilter();
    band.type = 'bandpass';
    band.frequency.value = type === 'storm' ? 300 : 2400 + combo * 400;
    band.Q.value = 1.2;
    const ng = this.ctx.createGain();
    ng.gain.setValueAtTime(type === 'storm' ? 0.9 : 0.6, t);
    ng.gain.exponentialRampToValueAtTime(0.001, t + 0.12);
    noise.connect(band).connect(ng).connect(out);
    noise.start(t);

    const notes = type === 'gold' ? [880, 1320, 1760] : type === 'storm' ? [110] : [520 * Math.pow(2, Math.min(combo, 8) / 12)];
    notes.forEach((freq, i) => {
      const osc = this.ctx.createOscillator();
      osc.type = type === 'storm' ? 'sawtooth' : 'sine';
      const g = this.ctx.createGain();
      const start = t + i * 0.06;
      osc.frequency.setValueAtTime(freq * (type === 'storm' ? 1 : 1.6), start);
      osc.frequency.exponentialRampToValueAtTime(freq, start + 0.08);
      g.gain.setValueAtTime(0.0001, start);
      g.gain.exponentialRampToValueAtTime(type === 'storm' ? 0.35 : 0.25, start + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, start + (type === 'gold' ? 0.5 : 0.22));
      osc.connect(g).connect(out);
      osc.start(start);
      osc.stop(start + 0.6);
    });
  }

  chime(up = true) {
    if (!this.ctx) return;
    const t = this.ctx.currentTime;
    (up ? [523, 659, 784] : [784, 659, 523, 392]).forEach((f, i) => {
      const osc = this.ctx.createOscillator();
      const g = this.ctx.createGain();
      osc.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t + i * 0.12);
      g.gain.exponentialRampToValueAtTime(0.18, t + i * 0.12 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + i * 0.12 + 0.5);
      osc.connect(g).connect(this.ctx.destination);
      osc.start(t + i * 0.12);
      osc.stop(t + i * 0.12 + 0.6);
    });
  }
}

// ---------------------------------------------------------------------------
// Visuals
// ---------------------------------------------------------------------------

const bubbleMaterial = (type) => new THREE.ShaderMaterial({
  transparent: true,
  depthWrite: false,
  uniforms: {
    time: { value: 0 },
    seed: { value: Math.random() * 10 },
    fade: { value: 0 },
    kind: { value: type === 'gold' ? 1 : type === 'storm' ? 2 : 0 },
  },
  vertexShader: `
    varying vec3 vNormal; varying vec3 vView;
    void main() {
      vec4 world = modelMatrix * vec4(position, 1.0);
      vNormal = normalize(mat3(modelMatrix) * normal);
      vView = normalize(cameraPosition - world.xyz);
      gl_Position = projectionMatrix * viewMatrix * world;
    }`,
  fragmentShader: `
    uniform float time, seed, fade, kind;
    varying vec3 vNormal; varying vec3 vView;
    void main() {
      float facing = abs(dot(normalize(vNormal), normalize(vView)));
      float rim = pow(1.0 - facing, 2.2);
      // Thin-film interference: hue shifts with viewing angle and swirls over time.
      float film = rim * 2.6 + seed + time * 0.15 + vNormal.y * 0.8;
      vec3 irid = 0.55 + 0.45 * cos(6.2831 * (film + vec3(0.0, 0.33, 0.67)));
      vec3 color = irid;
      float alpha = mix(0.16, 0.95, rim);
      if (kind > 0.5 && kind < 1.5) { color = mix(vec3(1.0, 0.78, 0.25), irid, 0.25) * 1.25; alpha = mix(0.35, 1.0, rim); }
      if (kind > 1.5) {
        float flash = step(0.97, fract(sin(floor(time * 6.0 + seed * 7.0)) * 43758.5));
        color = mix(vec3(0.16, 0.12, 0.28), vec3(0.8, 0.85, 1.0), flash * (1.0 - facing));
        alpha = mix(0.55, 0.95, rim);
      }
      // A soft specular glint from an overhead light.
      vec3 h = normalize(normalize(vec3(0.3, 1.0, 0.4)) + normalize(vView));
      float glint = pow(max(dot(normalize(vNormal), h), 0.0), 90.0);
      gl_FragColor = vec4(color + glint * 1.5, clamp(alpha + glint, 0.0, 1.0) * (1.0 - fade));
    }`,
});

function makeSkyDome() {
  const geometry = new THREE.SphereGeometry(40, 48, 24);
  const material = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    depthWrite: false,
    uniforms: { time: { value: 0 } },
    vertexShader: 'varying vec3 vDir; void main(){ vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: `
      uniform float time; varying vec3 vDir;
      void main() {
        float h = vDir.y;
        vec3 top = vec3(0.09, 0.10, 0.26), mid = vec3(0.42, 0.27, 0.52), low = vec3(0.98, 0.62, 0.55);
        vec3 c = h > 0.0 ? mix(mid, top, smoothstep(0.0, 0.7, h)) : mix(mid, low * 0.5, smoothstep(0.0, -0.4, h));
        c += vec3(1.0, 0.7, 0.6) * 0.25 * exp(-abs(h) * 9.0);
        gl_FragColor = vec4(c, 1.0);
      }`,
  });
  return new THREE.Mesh(geometry, material);
}

function makeFloor() {
  const group = new THREE.Group();
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(1.35, 1.4, 96),
    new THREE.MeshBasicMaterial({ color: 0xffd1e8, transparent: true, opacity: 0.5, side: THREE.DoubleSide })
  );
  ring.rotation.x = -Math.PI / 2;
  const disc = new THREE.Mesh(
    new THREE.CircleGeometry(12, 64),
    new THREE.MeshBasicMaterial({ color: 0x1a1433, transparent: true, opacity: 0.85 })
  );
  disc.rotation.x = -Math.PI / 2;
  disc.position.y = -0.002;
  group.add(disc, ring);
  return group;
}

function makeMotes(count = 400) {
  const positions = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const r = 2 + Math.random() * 10;
    const a = Math.random() * Math.PI * 2;
    positions.set([Math.cos(a) * r, Math.random() * 5, Math.sin(a) * r], i * 3);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  return new THREE.Points(geometry, new THREE.PointsMaterial({ color: 0xffe3f3, size: 0.03, transparent: true, opacity: 0.7, depthWrite: false }));
}

class Burst {
  constructor(scene) {
    this.count = 28;
    this.geometry = new THREE.BufferGeometry();
    this.positions = new Float32Array(this.count * 3);
    this.velocities = new Float32Array(this.count * 3);
    this.geometry.setAttribute('position', new THREE.BufferAttribute(this.positions, 3));
    this.material = new THREE.PointsMaterial({ size: 0.018, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending });
    this.points = new THREE.Points(this.geometry, this.material);
    this.points.frustumCulled = false;
    this.points.visible = false;
    this.age = 1;
    scene.add(this.points);
  }

  fire(position, color, radius) {
    for (let i = 0; i < this.count; i++) {
      const dir = new THREE.Vector3().randomDirection();
      this.positions.set([position.x + dir.x * radius, position.y + dir.y * radius, position.z + dir.z * radius], i * 3);
      const speed = 0.4 + Math.random() * 0.8;
      this.velocities.set([dir.x * speed, dir.y * speed + 0.2, dir.z * speed], i * 3);
    }
    this.material.color.set(color);
    this.age = 0;
    this.points.visible = true;
  }

  update(dt) {
    if (this.age >= 1) return;
    this.age += dt / 0.6;
    for (let i = 0; i < this.count; i++) {
      this.velocities[i * 3 + 1] -= 1.6 * dt;
      for (let k = 0; k < 3; k++) this.positions[i * 3 + k] += this.velocities[i * 3 + k] * dt;
    }
    this.geometry.attributes.position.needsUpdate = true;
    this.material.opacity = Math.max(0, 1 - this.age);
    if (this.age >= 1) this.points.visible = false;
  }
}

// A floating text panel for scores inside the headset.
class Panel {
  constructor() {
    this.canvas = document.createElement('canvas');
    this.canvas.width = 1024;
    this.canvas.height = 384;
    this.texture = new THREE.CanvasTexture(this.canvas);
    this.texture.colorSpace = THREE.SRGBColorSpace;
    this.mesh = new THREE.Mesh(
      new THREE.PlaneGeometry(0.72, 0.27),
      new THREE.MeshBasicMaterial({ map: this.texture, transparent: true, depthWrite: false })
    );
    this.mesh.renderOrder = 10;
    this.key = '';
  }

  draw(lines) {
    const key = JSON.stringify(lines);
    if (key === this.key) return;
    this.key = key;
    const c = this.canvas.getContext('2d');
    c.clearRect(0, 0, 1024, 384);
    c.fillStyle = 'rgba(20, 14, 40, 0.72)';
    c.beginPath();
    c.roundRect(8, 8, 1008, 368, 64);
    c.fill();
    c.textAlign = 'center';
    c.fillStyle = '#fff';
    const [big, small, tiny] = lines;
    c.font = '700 124px -apple-system, system-ui, sans-serif';
    c.fillText(big, 512, 160);
    c.font = '500 58px -apple-system, system-ui, sans-serif';
    c.fillStyle = '#ffd6ec';
    c.fillText(small ?? '', 512, 260);
    c.font = '500 40px -apple-system, system-ui, sans-serif';
    c.fillStyle = 'rgba(255,255,255,0.7)';
    c.fillText(tiny ?? '', 512, 330);
    this.texture.needsUpdate = true;
  }
}

// ---------------------------------------------------------------------------
// Game
// ---------------------------------------------------------------------------

export class PinchPop extends EventTarget {
  constructor(canvas) {
    super();
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.xr.enabled = true;
    this.renderer.xr.setReferenceSpaceType('local-floor');

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(46, 1, 0.01, 100);
    this.camera.position.set(0, 1.5, 0.6);
    this.camera.lookAt(0, 1.4, -1);

    this.sky = makeSkyDome();
    this.floor = makeFloor();
    this.motes = makeMotes();
    this.scene.add(this.sky, this.floor, this.motes);

    this.bubbles = [];
    this.bursts = Array.from({ length: 10 }, () => new Burst(this.scene));
    this.burstIndex = 0;
    this.panel = new Panel();
    this.scene.add(this.panel.mesh);
    this.panel.mesh.visible = false;
    this.fingers = [0, 1].map((i) => {
      const hand = this.renderer.xr.getHand(i);
      const tip = new THREE.Mesh(new THREE.SphereGeometry(0.008, 12, 8), new THREE.MeshBasicMaterial({ color: 0xffe0f0 }));
      tip.visible = false;
      this.scene.add(hand, tip);
      return { hand, tip };
    });

    this.sound = new Sound();
    this.clock = new THREE.Clock();
    this.state = 'idle';          // idle | playing | over
    this.score = 0;
    this.best = Number(safeStorage('get', 'pinchpop-best') ?? 0);
    this.combo = 0;
    this.lastPop = -10;
    this.timeLeft = ROUND_SECONDS;
    this.spawnTimer = 0;
    this.elapsed = 0;
    this.forward = new THREE.Vector3(0, 0, -1);
    this.session = null;
    this.xrRefSpace = null;

    this.raycaster = new THREE.Raycaster();
    canvas.addEventListener('pointerdown', (e) => this.#onPointer(e));
    this.resize();
    new ResizeObserver(() => this.resize()).observe(canvas);
    this.renderer.setAnimationLoop((time, frame) => this.#tick(frame));
  }

  resize() {
    if (this.renderer.xr.isPresenting) return;
    const w = this.canvas.clientWidth || 1;
    const h = this.canvas.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  // ---- sessions -----------------------------------------------------------

  static async xrMode() {
    if (!navigator.xr) return null;
    try {
      if (await navigator.xr.isSessionSupported('immersive-ar')) return 'immersive-ar';
      if (await navigator.xr.isSessionSupported('immersive-vr')) return 'immersive-vr';
    } catch { /* not allowed here */ }
    return null;
  }

  async enterXR(mode) {
    this.sound.unlock();
    const session = await navigator.xr.requestSession(mode, {
      requiredFeatures: ['local-floor'],
      optionalFeatures: ['hand-tracking'],
    });
    this.session = session;
    const passthrough = mode === 'immersive-ar';
    this.sky.visible = !passthrough;
    this.floor.visible = !passthrough;
    this.motes.visible = !passthrough;
    await this.renderer.xr.setSession(session);
    this.xrRefSpace = this.renderer.xr.getReferenceSpace();
    session.addEventListener('selectstart', (e) => this.#onXRSelect(e));
    session.addEventListener('end', () => {
      this.session = null;
      this.sky.visible = this.floor.visible = this.motes.visible = true;
      this.panel.mesh.visible = false;
      if (this.state === 'playing') this.#finish();
      this.dispatchEvent(new Event('xrend'));
      this.resize();
    });
    this.#resetForward();
    this.state = 'idle';
    this.panel.mesh.visible = true;
    this.dispatchEvent(new Event('xrstart'));
  }

  // ---- round flow ---------------------------------------------------------

  start() {
    this.sound.unlock();
    for (const b of this.bubbles) this.scene.remove(b.mesh);
    this.bubbles = [];
    this.score = 0;
    this.combo = 0;
    this.timeLeft = ROUND_SECONDS;
    this.spawnTimer = 0;
    this.#resetForward();
    this.state = 'playing';
    this.sound.chime(true);
    this.#emit();
  }

  #finish() {
    this.state = 'over';
    const isBest = this.score > this.best;
    if (isBest) {
      this.best = this.score;
      safeStorage('set', 'pinchpop-best', String(this.best));
    }
    this.sound.chime(false);
    for (const b of this.bubbles) b.leaving = true;
    this.dispatchEvent(new CustomEvent('over', { detail: { score: this.score, best: this.best, isBest } }));
    this.#emit();
  }

  #emit() {
    this.dispatchEvent(new CustomEvent('update', {
      detail: { state: this.state, score: this.score, best: this.best, timeLeft: Math.ceil(this.timeLeft), combo: this.#multiplier() },
    }));
  }

  #multiplier() {
    return Math.min(1 + Math.floor(this.combo / 3), 5);
  }

  #resetForward() {
    const cam = this.renderer.xr.isPresenting ? this.renderer.xr.getCamera() : this.camera;
    const f = new THREE.Vector3(0, 0, -1).applyQuaternion(cam.quaternion);
    f.y = 0;
    if (f.lengthSq() < 1e-4) f.set(0, 0, -1);
    this.forward.copy(f.normalize());
  }

  // ---- bubbles ------------------------------------------------------------

  #spawn() {
    const roll = Math.random();
    const type = roll < TYPES.gold.chance ? 'gold' : roll < TYPES.gold.chance + TYPES.storm.chance ? 'storm' : 'normal';
    const spec = TYPES[type];
    const radius = THREE.MathUtils.lerp(...spec.radius, Math.random());
    const inXR = this.renderer.xr.isPresenting;
    const spread = inXR ? THREE.MathUtils.degToRad(100) : THREE.MathUtils.degToRad(30);
    const angle = (Math.random() * 2 - 1) * spread;
    const dist = inXR ? THREE.MathUtils.lerp(0.45, 1.25, Math.random()) : THREE.MathUtils.lerp(1.2, 2.1, Math.random());
    const dir = this.forward.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), angle);
    const origin = inXR ? new THREE.Vector3().setFromMatrixPosition(this.renderer.xr.getCamera().matrixWorld) : new THREE.Vector3(0, 1.5, 0.6);
    const pos = new THREE.Vector3(origin.x, 0, origin.z).addScaledVector(dir, dist);
    pos.y = inXR ? THREE.MathUtils.lerp(0.75, 1.55, Math.random()) : THREE.MathUtils.lerp(0.9, 1.7, Math.random());

    const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius, 40, 24), bubbleMaterial(type));
    mesh.position.copy(pos);
    mesh.scale.setScalar(0.01);
    this.scene.add(mesh);
    this.bubbles.push({
      mesh, type, radius, born: this.elapsed, life: 11 + Math.random() * 5,
      rise: THREE.MathUtils.lerp(...spec.speed, Math.random()),
      wobble: Math.random() * Math.PI * 2, leaving: false, popped: false, fade: 0,
    });
  }

  #pop(bubble) {
    if (bubble.popped || bubble.leaving || this.state !== 'playing') return;
    bubble.popped = true;
    const now = this.elapsed;
    if (bubble.type === 'storm') {
      this.combo = 0;
    } else {
      this.combo = now - this.lastPop < COMBO_WINDOW ? this.combo + 1 : 0;
      this.lastPop = now;
    }
    const points = TYPES[bubble.type].points * (bubble.type === 'storm' ? 1 : this.#multiplier());
    this.score = Math.max(0, this.score + points);
    const color = bubble.type === 'gold' ? 0xffd166 : bubble.type === 'storm' ? 0x9aa0ff : 0xffc6ec;
    this.bursts[this.burstIndex++ % this.bursts.length].fire(bubble.mesh.position, color, bubble.radius);
    this.sound.pop(bubble.type, bubble.mesh.position, this.combo);
    this.dispatchEvent(new CustomEvent('pop', { detail: { type: bubble.type, points } }));
    this.#emit();
  }

  #hitAlongRay(origin, direction) {
    let best = null;
    let bestT = Infinity;
    for (const b of this.bubbles) {
      if (b.popped || b.leaving) continue;
      const oc = origin.clone().sub(b.mesh.position);
      const r = b.radius * b.mesh.scale.x * 1.35;          // generous: eyes wobble
      const bq = oc.dot(direction);
      const c = oc.lengthSq() - r * r;
      const disc = bq * bq - c;
      if (disc < 0) continue;
      const t = -bq - Math.sqrt(disc);
      if (t > 0 && t < bestT) { bestT = t; best = b; }
    }
    return best;
  }

  #onPointer(event) {
    if (this.renderer.xr.isPresenting) return;
    this.sound.unlock();
    if (this.state !== 'playing') return;
    const rect = this.canvas.getBoundingClientRect();
    const ndc = new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera);
    const hit = this.#hitAlongRay(this.raycaster.ray.origin, this.raycaster.ray.direction);
    if (hit) this.#pop(hit);
  }

  #onXRSelect(event) {
    if (this.state !== 'playing') {
      this.start();
      return;
    }
    const pose = event.frame.getPose(event.inputSource.targetRaySpace, this.xrRefSpace);
    if (!pose) return;
    const m = new THREE.Matrix4().fromArray(pose.transform.matrix);
    // The XR camera rig may be offset from the reference space; bring the ray into world space.
    const parent = this.renderer.xr.getCamera().parent;
    if (parent) m.premultiply(parent.matrixWorld);
    const origin = new THREE.Vector3().setFromMatrixPosition(m);
    const direction = new THREE.Vector3(0, 0, -1).transformDirection(m);
    const hit = this.#hitAlongRay(origin, direction);
    if (hit) this.#pop(hit);
  }

  // ---- frame loop ---------------------------------------------------------

  #tick(frame) {
    const dt = Math.min(this.clock.getDelta(), 0.05);
    this.elapsed += dt;
    const inXR = this.renderer.xr.isPresenting;
    const cam = inXR ? this.renderer.xr.getCamera() : this.camera;
    this.sky.material.uniforms.time.value = this.elapsed;
    this.motes.rotation.y += dt * 0.01;

    if (this.state === 'playing') {
      this.timeLeft -= dt;
      this.spawnTimer -= dt;
      const progress = 1 - this.timeLeft / ROUND_SECONDS;
      if (this.spawnTimer <= 0 && this.bubbles.filter((b) => !b.popped && !b.leaving).length < MAX_BUBBLES) {
        this.#spawn();
        this.spawnTimer = THREE.MathUtils.lerp(0.95, 0.4, progress);
      }
      if (Math.ceil(this.timeLeft) !== this.lastShownSecond) {
        this.lastShownSecond = Math.ceil(this.timeLeft);
        this.#emit();
      }
      if (this.timeLeft <= 0) this.#finish();
    }

    // Fingertip pokes.
    for (const { hand, tip } of this.fingers) {
      const joint = hand.joints?.['index-finger-tip'];
      tip.visible = !!(inXR && joint && joint.visible !== false && hand.visible !== false);
      if (!tip.visible) continue;
      joint.getWorldPosition(tip.position);
      for (const b of this.bubbles) {
        if (!b.popped && !b.leaving && tip.position.distanceTo(b.mesh.position) < b.radius * b.mesh.scale.x + 0.012) this.#pop(b);
      }
    }

    // Bubble motion, fade in/out and cleanup.
    for (const b of this.bubbles) {
      const age = this.elapsed - b.born;
      b.mesh.material.uniforms.time.value = this.elapsed;
      if (b.popped) {
        b.fade += dt / 0.12;
        b.mesh.scale.multiplyScalar(1 + dt * 6);
      } else {
        b.mesh.scale.setScalar(Math.min(1, age / 0.5) * (1 + 0.03 * Math.sin(age * 5 + b.wobble)));
        b.mesh.position.y += b.rise * dt;
        b.mesh.position.x += Math.sin(age * 0.9 + b.wobble) * 0.02 * dt;
        b.mesh.position.z += Math.cos(age * 0.7 + b.wobble) * 0.02 * dt;
        if (age > b.life || b.mesh.position.y > 2.6) b.leaving = true;
        if (b.leaving) b.fade += dt / 0.6;
      }
      b.mesh.material.uniforms.fade.value = Math.min(1, b.fade);
    }
    this.bubbles = this.bubbles.filter((b) => {
      if (b.fade < 1) return true;
      this.scene.remove(b.mesh);
      b.mesh.geometry.dispose();
      b.mesh.material.dispose();
      return false;
    });
    for (const burst of this.bursts) burst.update(dt);

    // Score panel floats ahead of you, lazily following your gaze.
    if (inXR) {
      const head = new THREE.Vector3().setFromMatrixPosition(cam.matrixWorld);
      const look = new THREE.Vector3(0, 0, -1).transformDirection(cam.matrixWorld);
      look.y = 0;
      if (look.lengthSq() > 1e-4) look.normalize();
      const target = head.clone().addScaledVector(look, 1.1);
      target.y = head.y + 0.28;
      this.panel.mesh.position.lerp(target, 1 - Math.exp(-dt * 2.5));
      this.panel.mesh.lookAt(head.x, this.panel.mesh.position.y, head.z);
      const lines = this.state === 'playing'
        ? [`${this.score}`, `${Math.ceil(this.timeLeft)} s · ×${this.#multiplier()}`, 'Look + pinch, or poke with a fingertip']
        : this.state === 'over'
          ? [`${this.score} points`, this.score >= this.best ? 'New best!' : `Best ${this.best}`, 'Pinch anywhere to play again']
          : ['Pinch Pop', 'Pinch anywhere to start', 'Gold = 5 · storm clouds cost 5'];
      this.panel.draw(lines);
    }

    this.sound.listen(cam);
    this.renderer.render(this.scene, this.camera);
  }
}

function safeStorage(op, key, value) {
  try {
    return op === 'get' ? localStorage.getItem(key) : localStorage.setItem(key, value);
  } catch {
    return null;
  }
}
