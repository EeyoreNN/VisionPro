// Tiny drag-to-look 360° viewer for browsers without immersive <model> support.
// Renders an equirectangular image by casting a ray per pixel in a shader.

const VERTEX = `
attribute vec2 p;
varying vec2 v;
void main() { v = p; gl_Position = vec4(p, 0.0, 1.0); }`;

const FRAGMENT = `
precision highp float;
varying vec2 v;
uniform sampler2D img;
uniform float yaw, pitch, fov, aspect;
void main() {
  float t = tan(fov * 0.5);
  vec3 d = normalize(vec3(v.x * t * aspect, v.y * t, -1.0));
  float cp = cos(pitch), sp = sin(pitch);
  d = vec3(d.x, d.y * cp + d.z * sp, -d.y * sp + d.z * cp);
  float cy = cos(yaw), sy = sin(yaw);
  d = vec3(d.x * cy - d.z * sy, d.y, d.x * sy + d.z * cy);
  float az = atan(d.x, -d.z);
  float el = asin(clamp(d.y, -1.0, 1.0));
  gl_FragColor = texture2D(img, vec2(az / 6.28318530718 + 0.5, 0.5 - el / 3.14159265359));
}`;

export function mountPanorama(canvas, src) {
  const gl = canvas.getContext('webgl', { antialias: false });
  if (!gl) {
    canvas.replaceWith(Object.assign(new Image(), { src, alt: '360 degree view from the overlook deck', className: 'panorama-flat' }));
    return;
  }
  const compile = (type, code) => {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, code);
    gl.compileShader(shader);
    return shader;
  };
  const program = gl.createProgram();
  gl.attachShader(program, compile(gl.VERTEX_SHADER, VERTEX));
  gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FRAGMENT));
  gl.linkProgram(program);
  gl.useProgram(program);

  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(program, 'p');
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

  const u = (name) => gl.getUniformLocation(program, name);
  const view = { yaw: 0, pitch: 0.18, fov: 1.35 };
  let loaded = false;
  let interacted = false;
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

  const texture = gl.createTexture();
  const image = new Image();
  image.onload = () => {
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, image);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    loaded = true;
  };
  image.src = src;

  const draw = () => {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const w = Math.round(canvas.clientWidth * dpr);
    const h = Math.round(canvas.clientHeight * dpr);
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    gl.viewport(0, 0, w, h);
    if (loaded) {
      gl.uniform1f(u('yaw'), view.yaw);
      gl.uniform1f(u('pitch'), view.pitch);
      gl.uniform1f(u('fov'), view.fov);
      gl.uniform1f(u('aspect'), w / h);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    }
  };

  const tick = () => {
    if (!interacted && !reduceMotion) view.yaw += 0.0006;
    draw();
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);

  let drag = null;
  canvas.addEventListener('pointerdown', (e) => {
    interacted = true;
    drag = { x: e.clientX, y: e.clientY, yaw: view.yaw, pitch: view.pitch };
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const scale = view.fov / canvas.clientHeight;
    view.yaw = drag.yaw - (e.clientX - drag.x) * scale;
    view.pitch = Math.max(-1.2, Math.min(1.2, drag.pitch + (e.clientY - drag.y) * scale));
  });
  const end = () => { drag = null; };
  canvas.addEventListener('pointerup', end);
  canvas.addEventListener('pointercancel', end);
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    interacted = true;
    view.fov = Math.max(0.6, Math.min(1.9, view.fov * Math.exp(e.deltaY * 0.001)));
  }, { passive: false });
}
