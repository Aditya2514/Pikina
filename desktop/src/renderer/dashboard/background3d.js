/**
 * Pikina OS — interactive 3D background (plain WebGL, no libraries).
 *
 * A rotating wireframe core with orbit rings inside a particle galaxy.
 *  - The scene tilts toward the cursor (parallax) and the core turns to face it.
 *  - Particles near the cursor are pushed away and glow.
 *  - Clicking sends a shockwave through the particles.
 *
 * Cost control: capped frame rate (45 fps focused, 20 fps unfocused), stops
 * completely while the window is hidden, renders a single still frame when
 * the OS asks for reduced motion, and caps the pixel ratio.
 *
 * API: window.PikinaBackground3D.setEnabled(true|false)
 */
(function () {
  'use strict';

  const CONFIG = {
    particles: 2200,
    fpsFocused: 45,
    fpsBlurred: 20,
    maxPixelRatio: 1.5,
    color: [0.0, 0.83, 1.0],   // --c-cyan
    hot:   [0.85, 0.97, 1.0],  // particles near the cursor
    camera: 9.0,
    focal: 1.8,
  };

  // ── Shaders ────────────────────────────────────────────────────────────
  const PROJECT = `
    uniform float uAspect;
    vec4 project(vec3 p, out float z) {
      z = ${CONFIG.camera.toFixed(1)} - p.z;
      vec2 ndc = vec2(p.x * ${CONFIG.focal.toFixed(2)} / z / uAspect, p.y * ${CONFIG.focal.toFixed(2)} / z);
      return vec4(ndc, 0.0, 1.0);
    }`;

  const POINT_VS = `
    attribute vec3 aPos;
    attribute float aSeed;
    uniform mat3 uRot;
    uniform float uTime;
    uniform vec2 uMouse;
    uniform float uMouseActive;
    uniform float uPulse;
    uniform vec2 uPulseOrigin;
    uniform float uPixelRatio;
    varying float vGlow;
    varying float vAlpha;
    ${PROJECT}
    void main() {
      float z;
      vec4 pos = project(uRot * aPos, z);
      vec2 ndc = pos.xy;

      // Cursor repulsion (in screen space, aspect-corrected)
      vec2 d = ndc - uMouse; d.x *= uAspect;
      float dist = length(d);
      float push = smoothstep(0.38, 0.0, dist) * uMouseActive;
      vec2 dir = dist > 0.0001 ? d / dist : vec2(0.0);

      // Click shockwave: an expanding ring that fades out
      vec2 dp = ndc - uPulseOrigin; dp.x *= uAspect;
      float pd = length(dp);
      float wave = exp(-pow((pd - uPulse * 1.6) * 7.0, 2.0)) * exp(-uPulse * 1.1);
      vec2 pdir = pd > 0.0001 ? dp / pd : vec2(0.0);

      vec2 off = dir * push * 0.16 + pdir * wave * 0.11;
      off.x /= uAspect;
      gl_Position = vec4(ndc + off, 0.0, 1.0);

      float twinkle = 0.75 + 0.25 * sin(uTime * 1.7 + aSeed * 40.0);
      gl_PointSize = (2.6 + 5.0 * push + 5.0 * wave) * uPixelRatio * (${CONFIG.camera.toFixed(1)} / z) * twinkle;
      vGlow = max(push, wave);
      vAlpha = clamp(1.6 - z / 12.0, 0.25, 1.0) * (1.0 + 1.5 * max(push, wave));
    }`;

  const POINT_FS = `
    precision mediump float;
    uniform vec3 uColor;
    uniform vec3 uHot;
    varying float vGlow;
    varying float vAlpha;
    void main() {
      float d = length(gl_PointCoord - 0.5);
      float a = smoothstep(0.5, 0.0, d);
      a *= a * vAlpha;
      gl_FragColor = vec4(mix(uColor, uHot, vGlow) * a, a);
    }`;

  const LINE_VS = `
    attribute vec3 aPos;
    uniform mat3 uRot;
    uniform float uScale;
    varying float vAlpha;
    ${PROJECT}
    void main() {
      float z;
      gl_Position = project(uRot * (aPos * uScale), z);
      vAlpha = clamp(1.4 - z / 12.0, 0.2, 1.0);
    }`;

  const LINE_FS = `
    precision mediump float;
    uniform vec3 uColor;
    uniform float uOpacity;
    varying float vAlpha;
    void main() {
      float a = uOpacity * vAlpha;
      gl_FragColor = vec4(uColor * a, a);
    }`;

  // ── Small math helpers (3x3 rotation matrices, column-major) ─────────
  function rotX(a) { const c = Math.cos(a), s = Math.sin(a); return [1, 0, 0, 0, c, s, 0, -s, c]; }
  function rotY(a) { const c = Math.cos(a), s = Math.sin(a); return [c, 0, -s, 0, 1, 0, s, 0, c]; }
  function rotZ(a) { const c = Math.cos(a), s = Math.sin(a); return [c, s, 0, -s, c, 0, 0, 0, 1]; }
  function mul(a, b) {
    const r = new Array(9);
    for (let col = 0; col < 3; col++) {
      for (let row = 0; row < 3; row++) {
        r[col * 3 + row] = a[row] * b[col * 3] + a[3 + row] * b[col * 3 + 1] + a[6 + row] * b[col * 3 + 2];
      }
    }
    return r;
  }
  const lerp = (a, b, t) => a + (b - a) * t;

  // ── Geometry ─────────────────────────────────────────────────────────
  function makeParticles(n) {
    const pos = new Float32Array(n * 3);
    const seed = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      let x, y, z;
      if (i % 5 < 3) {
        // Galaxy disc
        const r = 1.8 + Math.pow(Math.random(), 0.7) * 3.6;
        const a = Math.random() * Math.PI * 2 + r * 0.6; // slight spiral
        x = Math.cos(a) * r;
        z = Math.sin(a) * r;
        y = (Math.random() - 0.5) * 0.35 * (1.0 + r * 0.15);
      } else {
        // Spherical shell
        const u = Math.random() * 2 - 1, t = Math.random() * Math.PI * 2;
        const r = 2.6 + Math.random() * 2.2;
        const s = Math.sqrt(1 - u * u);
        x = s * Math.cos(t) * r; y = u * r; z = s * Math.sin(t) * r;
      }
      pos.set([x, y, z], i * 3);
      seed[i] = Math.random();
    }
    return { pos, seed };
  }

  // Icosphere edges as a flat LINES vertex list
  function makeIcosphereEdges(subdivisions) {
    const t = (1 + Math.sqrt(5)) / 2;
    let verts = [
      [-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0],
      [0, -1, t], [0, 1, t], [0, -1, -t], [0, 1, -t],
      [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1],
    ].map(normalize);
    let faces = [
      [0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11],
      [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8],
      [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9],
      [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1],
    ];
    for (let s = 0; s < subdivisions; s++) {
      const cache = new Map();
      const mid = (a, b) => {
        const key = a < b ? `${a}_${b}` : `${b}_${a}`;
        if (!cache.has(key)) {
          const va = verts[a], vb = verts[b];
          verts.push(normalize([(va[0] + vb[0]) / 2, (va[1] + vb[1]) / 2, (va[2] + vb[2]) / 2]));
          cache.set(key, verts.length - 1);
        }
        return cache.get(key);
      };
      const next = [];
      for (const [a, b, c] of faces) {
        const ab = mid(a, b), bc = mid(b, c), ca = mid(c, a);
        next.push([a, ab, ca], [b, bc, ab], [c, ca, bc], [ab, bc, ca]);
      }
      faces = next;
    }
    const seen = new Set(), out = [];
    for (const [a, b, c] of faces) {
      for (const [u, v] of [[a, b], [b, c], [c, a]]) {
        const key = u < v ? `${u}_${v}` : `${v}_${u}`;
        if (!seen.has(key)) { seen.add(key); out.push(...verts[u], ...verts[v]); }
      }
    }
    return new Float32Array(out);
  }

  function normalize(v) {
    const l = Math.hypot(v[0], v[1], v[2]);
    return [v[0] / l, v[1] / l, v[2] / l];
  }

  function makeRing(radius, segments) {
    const out = [];
    for (let i = 0; i < segments; i++) {
      // Dashed look: skip every 4th segment
      if (i % 4 === 3) continue;
      const a0 = (i / segments) * Math.PI * 2, a1 = ((i + 1) / segments) * Math.PI * 2;
      out.push(Math.cos(a0) * radius, 0, Math.sin(a0) * radius, Math.cos(a1) * radius, 0, Math.sin(a1) * radius);
    }
    return new Float32Array(out);
  }

  // ── WebGL plumbing ───────────────────────────────────────────────────
  function compile(gl, type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  }

  function program(gl, vs, fs) {
    const p = gl.createProgram();
    gl.attachShader(p, compile(gl, gl.VERTEX_SHADER, vs));
    gl.attachShader(p, compile(gl, gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    const uniforms = {};
    const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
    for (let i = 0; i < n; i++) {
      const name = gl.getActiveUniform(p, i).name;
      uniforms[name] = gl.getUniformLocation(p, name);
    }
    return { p, u: uniforms, a: (name) => gl.getAttribLocation(p, name) };
  }

  function buffer(gl, data) {
    const b = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, b);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    return b;
  }

  function bindAttr(gl, buf, loc, size) {
    if (loc < 0) return;
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
  }

  // ── Scene ────────────────────────────────────────────────────────────
  let canvas = null, gl = null, scene = null;
  let rafId = null, lastFrame = 0, startTime = performance.now();
  let enabled = false, focused = document.hasFocus();
  const reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const mouse = { x: 0, y: 0, tx: 0, ty: 0, active: 0, targetActive: 0 };
  const pulse = { t: 99, x: 0, y: 0 };

  function init() {
    canvas = document.createElement('canvas');
    canvas.id = 'bg3d';
    canvas.setAttribute('aria-hidden', 'true');
    Object.assign(canvas.style, {
      position: 'fixed', inset: '0', width: '100vw', height: '100vh',
      zIndex: '0', pointerEvents: 'none', display: 'block',
    });
    document.body.prepend(canvas);

    gl = canvas.getContext('webgl', { alpha: true, premultipliedAlpha: true, antialias: true, powerPreference: 'low-power' });
    if (!gl) { canvas.remove(); canvas = null; return false; }

    const pts = makeParticles(CONFIG.particles);
    scene = {
      points: program(gl, POINT_VS, POINT_FS),
      lines: program(gl, LINE_VS, LINE_FS),
      ptPos: buffer(gl, pts.pos),
      ptSeed: buffer(gl, pts.seed),
      core: buffer(gl, makeIcosphereEdges(1)),
      shell: buffer(gl, makeIcosphereEdges(0)),
      rings: [2.3, 2.85, 3.45].map((r) => buffer(gl, makeRing(r, 160))),
    };
    scene.coreCount = makeIcosphereEdges(1).length / 3;
    scene.shellCount = makeIcosphereEdges(0).length / 3;
    scene.ringCount = makeRing(1, 160).length / 3;

    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE); // additive glow
    gl.disable(gl.DEPTH_TEST);

    resize();
    window.addEventListener('resize', resize);
    window.addEventListener('mousemove', (e) => {
      mouse.tx = (e.clientX / window.innerWidth) * 2 - 1;
      mouse.ty = -((e.clientY / window.innerHeight) * 2 - 1);
      mouse.targetActive = 1;
    });
    document.addEventListener('mouseleave', () => { mouse.tx = 0; mouse.ty = 0; mouse.targetActive = 0; });
    window.addEventListener('mousedown', (e) => {
      pulse.t = 0;
      pulse.x = (e.clientX / window.innerWidth) * 2 - 1;
      pulse.y = -((e.clientY / window.innerHeight) * 2 - 1);
    });
    window.addEventListener('focus', () => { focused = true; });
    window.addEventListener('blur', () => { focused = false; });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) stopLoop(); else if (enabled) startLoop();
    });
    return true;
  }

  function resize() {
    if (!canvas) return;
    const dpr = Math.min(window.devicePixelRatio || 1, CONFIG.maxPixelRatio);
    canvas.width = Math.round(window.innerWidth * dpr);
    canvas.height = Math.round(window.innerHeight * dpr);
    gl.viewport(0, 0, canvas.width, canvas.height);
    if (enabled && (reducedMotion || !rafId)) draw(performance.now());
  }

  function draw(now) {
    const t = (now - startTime) / 1000;
    const dt = Math.min(0.1, (now - (draw.prev || now)) / 1000);
    draw.prev = now;

    mouse.x = lerp(mouse.x, mouse.tx, 0.06);
    mouse.y = lerp(mouse.y, mouse.ty, 0.06);
    mouse.active = lerp(mouse.active, mouse.targetActive, 0.08);
    pulse.t += dt;

    const aspect = canvas.width / canvas.height;
    const dpr = canvas.width / window.innerWidth;

    // Whole scene: slow spin plus parallax tilt toward the cursor
    const sceneRot = mul(rotX(0.32 - mouse.y * 0.25), rotY(t * 0.04 + mouse.x * 0.35));

    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);

    // Particles
    const P = scene.points;
    gl.useProgram(P.p);
    gl.uniformMatrix3fv(P.u.uRot, false, sceneRot);
    gl.uniform1f(P.u.uAspect, aspect);
    gl.uniform1f(P.u.uTime, t);
    gl.uniform2f(P.u.uMouse, mouse.tx, mouse.ty);
    gl.uniform1f(P.u.uMouseActive, mouse.active);
    gl.uniform1f(P.u.uPulse, pulse.t);
    gl.uniform2f(P.u.uPulseOrigin, pulse.x, pulse.y);
    gl.uniform1f(P.u.uPixelRatio, dpr);
    gl.uniform3fv(P.u.uColor, CONFIG.color);
    gl.uniform3fv(P.u.uHot, CONFIG.hot);
    bindAttr(gl, scene.ptPos, P.a('aPos'), 3);
    bindAttr(gl, scene.ptSeed, P.a('aSeed'), 1);
    gl.drawArrays(gl.POINTS, 0, CONFIG.particles);
    gl.disableVertexAttribArray(P.a('aSeed'));

    // Lines: core turns to face the cursor, rings orbit
    const L = scene.lines;
    gl.useProgram(L.p);
    gl.uniform1f(L.u.uAspect, aspect);
    gl.uniform3fv(L.u.uColor, CONFIG.color);
    const lineDraw = (buf, count, rot, scale, opacity) => {
      gl.uniformMatrix3fv(L.u.uRot, false, rot);
      gl.uniform1f(L.u.uScale, scale);
      gl.uniform1f(L.u.uOpacity, opacity);
      bindAttr(gl, buf, L.a('aPos'), 3);
      gl.drawArrays(gl.LINES, 0, count);
    };

    const flash = Math.exp(-pulse.t * 3.0);
    const face = mul(rotX(-mouse.y * 0.6), rotY(mouse.x * 0.8));
    const breathe = 1 + 0.03 * Math.sin(t * 1.3) + 0.08 * flash;
    lineDraw(scene.core, scene.coreCount, mul(face, mul(rotY(t * 0.25), rotX(t * 0.12))), 1.0 * breathe, 0.32 + 0.4 * flash);
    lineDraw(scene.shell, scene.shellCount, mul(face, mul(rotY(-t * 0.18), rotZ(t * 0.07))), 1.45 * breathe, 0.18 + 0.3 * flash);

    const tilts = [[0.35, 0.0], [-0.25, 0.9], [0.15, 2.1]];
    scene.rings.forEach((buf, i) => {
      const rot = mul(sceneRot, mul(rotZ(tilts[i][0]), rotY(t * (0.12 + i * 0.05) * (i % 2 ? -1 : 1) + tilts[i][1])));
      lineDraw(buf, scene.ringCount, rot, 1.0, 0.14);
    });
  }

  function loop(now) {
    rafId = requestAnimationFrame(loop);
    const minGap = 1000 / (focused ? CONFIG.fpsFocused : CONFIG.fpsBlurred);
    if (now - lastFrame < minGap) return;
    lastFrame = now;
    draw(now);
  }

  function startLoop() {
    if (rafId || !canvas) return;
    if (reducedMotion) { draw(performance.now()); return; }
    draw.prev = performance.now();
    rafId = requestAnimationFrame(loop);
  }

  function stopLoop() {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
  }

  function setEnabled(on) {
    enabled = !!on;
    if (enabled) {
      if (!canvas && !init()) { enabled = false; return false; }
      canvas.style.display = 'block';
      if (!document.hidden) startLoop();
    } else {
      stopLoop();
      if (canvas) canvas.style.display = 'none';
    }
    return enabled;
  }

  window.PikinaBackground3D = { setEnabled, isEnabled: () => enabled };
})();
