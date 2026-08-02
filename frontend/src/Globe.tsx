import React, { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { fetchTecDay, TEC_NLAT, TEC_NLON } from './tec';
import type { TecMode } from './tecMode';

const DAY_TEXTURE_URL = '/textures/earth-day.jpg';
const NIGHT_TEXTURE_URL = '/textures/earth-night.jpg';
const CLOUD_TEXTURE_URL = '/textures/earth-cloud.jpg';

// Sun in the equatorial (horizontal) plane so the 23.44° seasonal tilt lives
// entirely in the Earth's axis (see the tilt effect below). Keeping X=Z=5 holds
// the azimuth at 45°, so the HOUR_PHASE hour-tracking derivation is unaffected.
const SUN_DIRECTION = new THREE.Vector3(5, 0, 5).normalize();

// Earth's axial tilt is constant; only the direction it leans relative to the
// (fixed) sun changes with the season. AXIAL_TILT is that fixed magnitude;
// SUN_AZIMUTH is the sun's bearing in the XZ plane (from +Z toward +X), which
// the lean tracks toward at the June solstice.
const AXIAL_TILT = THREE.MathUtils.degToRad(23.44);
const SUN_AZIMUTH = Math.atan2(SUN_DIRECTION.x, SUN_DIRECTION.z);

// Sun's ecliptic longitude, ~0 at the vernal equinox (~Mar 20, DOY 79) and
// advancing 360°/year. Enough to drive a visually correct seasonal tilt; this
// is not a precise ephemeris. Uses UTC to match the rest of the pipeline.
function eclipticLongitude(date: Date): number {
  const start = Date.UTC(date.getUTCFullYear(), 0, 0);
  const doy = (date.getTime() - start) / 86400000;
  return ((doy - 79) / 365.24) * 2 * Math.PI; // radians
}

const TEC_CELLS = TEC_NLAT * TEC_NLON; // 71*73 values per hourly map

// Difference mode saturates the diverging ramp at +/- this many TECU, so the
// legend in UIPanel must use the same number.
export const DIFF_SATURATION = 30;

// --- Coupling the displayed UT hour to the globe's spin -----------------------
// TEC peaks near the subsolar point, so the bright crest must sit over the lit
// hemisphere. The overlay is geo-locked to the Earth (rotates with it), so a
// FIXED hour would let the crest drift off the dayside as the globe spins.
// Instead we pick, each frame, the UT hour whose subsolar longitude currently
// faces the (fixed) sun -- one full revolution then sweeps all 24 UT hours,
// exactly as a spinning Earth advances UT in reality.
//
// We derive the hour from the sun's LOCAL azimuth in the Earth's own frame,
// i.e. the geographic meridian currently facing the sun. Doing it from the local
// sun direction (not the spin angle alone) matters once the Earth is tilted:
// spinning about the tilted axis makes the subsolar longitude advance
// non-uniformly with spin (an obliquity / equation-of-time effect), so a purely
// linear map would let the crest lead and lag the true subsolar point within a
// revolution. Deriving from the local sun direction cancels that automatically
// and reduces exactly to the old linear mapping when the tilt is zero.
//
// Calibration: at zero tilt the sun's local azimuth is SUN_AZIMUTH - theta, so
// the previous h = theta/15 + HOUR_PHASE is equivalent to
// h = HOUR_PHASE + SUN_AZIMUTH/15 - localAzimuth/15. HOUR_PHASE stays the visual
// nudge (+/-1 rotates the crest one UT hour) if texture registration needs it.
const HOUR_PHASE = 15;
const DEG_PER_HOUR = 15;

// True if any half-float in the pack is NaN. A half NaN is exponent-all-ones
// (0x7C00) with a nonzero mantissa; Inf (mantissa 0) never occurs in TEC, so a
// bit test is enough and avoids decoding every value. Used ONLY to gate the
// difference overlay (below) -- the single-source 'predicted'/'actual' render
// paths never call this, so native predicted rendering is unaffected.
function hasNaNHalf(a: Uint16Array): boolean {
  for (let i = 0; i < a.length; i++) {
    if ((a[i] & 0x7c00) === 0x7c00 && (a[i] & 0x03ff) !== 0) return true;
  }
  return false;
}

// Build a difference day pack (predicted - actual) in the SAME half-float
// [24*TEC_CELLS] layout the texture expects, so it drops straight into the
// existing dayDataRef/hourBuf pipeline. Decoding to float and re-encoding keeps
// NaN as NaN (either side missing -> the shader discards that cell). Both inputs
// are exactly one UT day of 71x73 maps, so lengths match.
function diffDay(predicted: Uint16Array, actual: Uint16Array): Uint16Array {
  const out = new Uint16Array(predicted.length);
  for (let i = 0; i < out.length; i++) {
    const d = THREE.DataUtils.fromHalfFloat(predicted[i]) -
              THREE.DataUtils.fromHalfFloat(actual[i]);
    out[i] = THREE.DataUtils.toHalfFloat(d); // NaN - x = NaN, preserved as a half NaN
  }
  return out;
}

// The globe's outermost visible shell is the TEC overlay sphere (radius 1.01);
// fit to that plus breathing room so nothing ever touches the screen edge.
const GLOBE_FIT_RADIUS = 1.01;
const GLOBE_FIT_MARGIN = 1.18; // ~18% padding around the globe

// Camera distance at which a sphere of GLOBE_FIT_RADIUS just fits inside the
// frustum's *limiting* dimension — vertical on wide screens, horizontal on tall/
// narrow ones — so the globe is never cropped at any aspect ratio. A sphere of
// radius r at distance d subtends a half-angle asin(r/d); set that equal to the
// smaller of the vertical/horizontal half-FOVs and solve for d = r / sin(half).
function globeFitDistance(camera: THREE.PerspectiveCamera): number {
  const vHalf = THREE.MathUtils.degToRad(camera.fov) / 2;
  const hHalf = Math.atan(Math.tan(vHalf) * camera.aspect);
  const half = Math.min(vHalf, hHalf);
  return (GLOBE_FIT_RADIUS * GLOBE_FIT_MARGIN) / Math.sin(half);
}

// Sun azimuth in degrees for the calibration below (from +Z toward +X).
const SUN_AZIMUTH_DEG = THREE.MathUtils.radToDeg(SUN_AZIMUTH);
// Scratch objects reused each frame so the loop allocates nothing.
const _invQuat = new THREE.Quaternion();
const _localSun = new THREE.Vector3();

// UT hour whose subsolar meridian faces the sun, given the Earth's full world
// orientation (parent tilt * spin). Transforms the fixed world sun direction
// into the Earth's local frame and reads its azimuth, so it stays correct under
// the seasonal axial tilt (see the comment block above hourFromRotation's old
// linear form). Reduces to h = theta/15 + HOUR_PHASE when the tilt is zero.
function subsolarHour(worldQuat: THREE.Quaternion): number {
  _localSun.copy(SUN_DIRECTION).applyQuaternion(_invQuat.copy(worldQuat).invert());
  const localAzDeg = (Math.atan2(_localSun.x, _localSun.z) * 180) / Math.PI;
  const h = Math.floor(HOUR_PHASE + SUN_AZIMUTH_DEG / DEG_PER_HOUR - localAzDeg / DEG_PER_HOUR);
  return ((h % 24) + 24) % 24; // wrap into 0..23
}

// JavaScript code so it needs the /* glsl */ tag to be able to render here
const vertexShader = /* glsl */ `
  varying vec2 vUv;
  varying vec3 vNormalW;
  void main() {
    vUv = uv;
    vNormalW = normalize(mat3(modelMatrix) * normal);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const fragmentShader = /* glsl */ `
  uniform sampler2D dayTexture;
  uniform sampler2D nightTexture;
  uniform vec3 sunDirection;
  varying vec2 vUv;
  varying vec3 vNormalW;
  void main() {
    float intensity = dot(normalize(vNormalW), sunDirection);
    float dayAmount = smoothstep(-0.15, 0.15, intensity);

    vec3 dayColor = texture2D(dayTexture, vUv).rgb;
    vec3 nightColor = texture2D(nightTexture, vUv).rgb;
    vec3 color = mix(nightColor, dayColor, dayAmount);

    gl_FragColor = vec4(color, 1.0);
    #include <colorspace_fragment>
  }
`;

// --- TEC overlay shaders ---
const tecVertexShader = /* glsl */ `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const tecFragmentShader = /* glsl */ `
  uniform sampler2D tecMap;
  uniform float uMin;
  uniform float uMax;
  uniform float uOpacity;
  uniform float uLonOffset;
  uniform float uDiverging;   // >0.5 => render as signed error (blue<0<red)
  varying vec2 vUv;

  // Diverging blue->white->red ramp for the difference (predicted - actual)
  // overlay. s in [-1,1]: negative (model too low) = blue, 0 = near-white,
  // positive (model too high) = red. sRGB space (linearised on output, like ramp).
  // pow(|s|, 0.6) lifts the mid-range so small/moderate errors read as saturated
  // colour instead of washed-out pastel; the .css legend mirrors the same curve.
  vec3 diverging(float s) {
    vec3 neg = vec3(0.09, 0.36, 0.93);   // strong blue
    vec3 mid = vec3(0.97, 0.97, 0.97);
    vec3 pos = vec3(0.93, 0.12, 0.10);   // strong red
    float m = pow(clamp(abs(s), 0.0, 1.0), 0.6);
    return s < 0.0 ? mix(mid, neg, m) : mix(mid, pos, m);
  }

  // Inferno colormap (matplotlib), Matt Zucker's 6th-order polynomial fit.
  // Perceptually uniform + monotonic luminance + colorblind-safe, so equal TEC
  // steps read as equal colour steps and the map still makes sense in grayscale.
  // Returns sRGB-space colour (the fit approximates the sRGB colormap); we
  // linearise below before output so it composites correctly with the Earth.
  vec3 ramp(float t) {
    const vec3 c0 = vec3(0.0002189403691192265, 0.001651004631001012, -0.01948089843709184);
    const vec3 c1 = vec3(0.1065134194856116, 0.5639564367884091, 3.932712388889277);
    const vec3 c2 = vec3(11.60249308247187, -3.972853965665698, -15.9423941062914);
    const vec3 c3 = vec3(-41.70399613139459, 17.43639888205313, 44.35414519872813);
    const vec3 c4 = vec3(77.162935699427, -33.40235894210092, -81.80730925738993);
    const vec3 c5 = vec3(-71.31942824499214, 32.62606426397723, 73.20951985803202);
    const vec3 c6 = vec3(25.13112622477341, -12.24266895238567, -23.07032500287172);
    return c0 + t*(c1 + t*(c2 + t*(c3 + t*(c4 + t*(c5 + t*c6)))));
  }

  void main() {
    vec2 uv = vec2(fract(vUv.x + uLonOffset), 1.0 - vUv.y);
    float v = texture2D(tecMap, uv).r;
    if (v != v) discard;                       // NaN (missing) -> transparent

    if (uDiverging > 0.5) {
      // Difference mode: v is signed error TECU. Saturate at +/-uMax; fade the
      // near-zero band so only meaningful over/under-predictions read as solid.
      float s = clamp(v / uMax, -1.0, 1.0);
      // Reach full opacity by ~40% of range so moderate errors pop; only true
      // near-zero noise stays transparent.
      float a = uOpacity * smoothstep(0.04, 0.4, abs(s));
      gl_FragColor = vec4(pow(clamp(diverging(s), 0.0, 1.0), vec3(2.2)), a);
    } else {
      float t = clamp((v - uMin) / (uMax - uMin), 0.0, 1.0);

      // Fade the low end so quiet regions let the Earth show through and only the
      // TEC crests read as solid. Flat opacity would veil the whole night side.
      float a = uOpacity * smoothstep(0.0, 0.35, t);

      // ramp() is sRGB; the renderer works in linear + re-encodes via
      // colorspace_fragment, so linearise here to avoid a double gamma encode.
      gl_FragColor = vec4(pow(clamp(ramp(t), 0.0, 1.0), vec3(2.2)), a);
    }
    #include <colorspace_fragment>
  }
`;

interface GlobeProps {
  selectedDate: Date | null;
  tecMode: TecMode;
  // Fires with the UT hour (0..23) whenever the displayed map ticks to a new hour.
  onHourChange?: (hour: number) => void;
}

export const Globe: React.FC<GlobeProps> = ({ selectedDate, tecMode, onHourChange }) => {
  const mountRef = useRef<HTMLDivElement | null>(null);
  // Bridges from the scene-effect (built once) to the data-effect (runs on prop change).
  const tecMatRef = useRef<THREE.ShaderMaterial | null>(null);
  // The tilt frame holding earth + clouds + overlay; oriented per selectedDate.
  const tiltGroupRef = useRef<THREE.Group | null>(null);
  const tecTexRef = useRef<THREE.DataTexture | null>(null);
  // Whole selected day (24 * TEC_CELLS half-floats); the animation loop slices
  // out the hour that matches the current spin. null when TEC is off.
  const dayDataRef = useRef<Uint16Array | null>(null);
  // Persistent CELLS-sized buffer backing tecTexRef, refilled on each hour change.
  const hourBufRef = useRef<Uint16Array | null>(null);
  // Which hour is currently uploaded to the texture (-1 forces a refill).
  const shownHourRef = useRef<number>(-1);
  // Click-to-freeze: when true, the globe (and thus the tracked hour) is paused.
  const frozenRef = useRef<boolean>(false);
  // Latest onHourChange, kept fresh so the once-built animation loop can call it.
  const onHourChangeRef = useRef(onHourChange);
  onHourChangeRef.current = onHourChange;

  // --- Scene: built once ---
  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const width = mount.clientWidth;
    const height = mount.clientHeight;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(width, height);
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
    camera.position.set(0, 0, globeFitDistance(camera)); // fit width+height, uncropped

    // --- Sunlight source for standard material lighting (clouds) ---
    const sunLight = new THREE.DirectionalLight(0xffffff, 10);
    sunLight.position.copy(SUN_DIRECTION).multiplyScalar(10);
    scene.add(sunLight);

    const ambientLight = new THREE.AmbientLight(0xffffff, 0.05);
    scene.add(ambientLight);

    // --- Textures ---
    const loader = new THREE.TextureLoader();
    const dayTexture = loader.load(DAY_TEXTURE_URL);
    const nightTexture = loader.load(NIGHT_TEXTURE_URL);
    const cloudTexture = loader.load(CLOUD_TEXTURE_URL);
    dayTexture.colorSpace = THREE.SRGBColorSpace;
    nightTexture.colorSpace = THREE.SRGBColorSpace;
    cloudTexture.colorSpace = THREE.SRGBColorSpace;

    // Tilt frame: earth + clouds + overlay live inside this group so the seasonal
    // axial tilt applies to all three together. The per-mesh spin stays on the
    // children, so earth.rotation.y remains the pure spin phase for hour tracking.
    const earthSystem = new THREE.Group();
    scene.add(earthSystem);
    tiltGroupRef.current = earthSystem;

    // Earth
    const earthGeometry = new THREE.SphereGeometry(1, 64, 64);
    const earthMaterial = new THREE.ShaderMaterial({
      uniforms: {
        dayTexture: { value: dayTexture },
        nightTexture: { value: nightTexture },
        sunDirection: { value: SUN_DIRECTION },
      },
      vertexShader,
      fragmentShader,
    });
    const earth = new THREE.Mesh(earthGeometry, earthMaterial);
    earthSystem.add(earth);

    // Clouds
    const cloudGeometry = new THREE.SphereGeometry(1.005, 64, 64);
    const cloudMaterial = new THREE.MeshStandardMaterial({
      map: cloudTexture,
      transparent: true,
      opacity: 0.4,
      blending: THREE.NormalBlending,
      depthWrite: false,
    });
    const cloudMesh = new THREE.Mesh(cloudGeometry, cloudMaterial);
    earthSystem.add(cloudMesh);

    // TEC overlay (translucent sphere just above the surface; data set later)
    const tecGeometry = new THREE.SphereGeometry(1.01, 64, 64);
    const tecMaterial = new THREE.ShaderMaterial({
      uniforms: {
        tecMap: { value: null },
        uMin: { value: 0.0 },
        uMax: { value: 100.0 },
        uOpacity: { value: 0.0 },
        uLonOffset: { value: 0.0 },
        uDiverging: { value: 0.0 },
      },
      vertexShader: tecVertexShader,
      fragmentShader: tecFragmentShader,
      transparent: true,
      depthWrite: false,
    });
    const tecMesh = new THREE.Mesh(tecGeometry, tecMaterial);
    earthSystem.add(tecMesh);
    tecMatRef.current = tecMaterial;

    // One reusable half-float texture; the loop copies the active hour's slice
    // into hourBuf and flags needsUpdate instead of reallocating each hour.
    const hourBuf = new Uint16Array(TEC_CELLS);
    const tecTex = new THREE.DataTexture(
      hourBuf, TEC_NLON, TEC_NLAT, THREE.RedFormat, THREE.HalfFloatType,
    );
    tecTex.minFilter = THREE.LinearFilter;
    tecTex.magFilter = THREE.LinearFilter;
    tecTex.wrapS = THREE.RepeatWrapping;
    tecTex.needsUpdate = true;
    tecMaterial.uniforms.tecMap.value = tecTex;
    tecTexRef.current = tecTex;
    hourBufRef.current = hourBuf;

    // --- Controls ---
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.enablePan = false;
    controls.minDistance = 1.5;
    controls.maxDistance = 12; // tall/narrow screens need the camera further back

    const resizeObserver = new ResizeObserver(() => {
      const w = mount.clientWidth;
      const h = mount.clientHeight;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      // Re-fit so the globe stays fully on-screen at the new aspect ratio.
      // setLength keeps the user's current orbit direction, only the distance.
      camera.position.setLength(globeFitDistance(camera));
      controls.update();
    });
    resizeObserver.observe(mount);

    // --- Animation Loop ---
    let frameId: number;
    const worldQuat = new THREE.Quaternion(); // reused: earth's tilt * spin each frame
    const animate = () => {
      if (!frozenRef.current) {
        earth.rotation.y += 0.0015;
        cloudMesh.rotation.y += 0.0018;
      }
      tecMesh.rotation.y = earth.rotation.y; // overlay is geo-fixed to the earth

      // Show the UT hour whose subsolar longitude faces the sun, so the crest
      // stays over the lit hemisphere. All 24 hours are already in dayDataRef,
      // so this is just a buffer copy on the frames where the hour ticks over.
      const day = dayDataRef.current;
      if (day) {
        // Combine the (static) seasonal tilt with the current spin, then read the
        // hour from the true local sun direction so the crest tracks the subsolar
        // point even about the tilted axis.
        worldQuat.multiplyQuaternions(earthSystem.quaternion, earth.quaternion);
        const h = subsolarHour(worldQuat);
        if (h !== shownHourRef.current) {
          shownHourRef.current = h;
          hourBuf.set(day.subarray(h * TEC_CELLS, (h + 1) * TEC_CELLS));
          tecTex.needsUpdate = true;
          onHourChangeRef.current?.(h); // drive the UTC counter in the HUD
        }
      }

      controls.update();
      renderer.render(scene, camera);
      frameId = requestAnimationFrame(animate);
    };
    animate();

    // --- Click-to-freeze: a click (not a drag) on the globe toggles the spin.
    // OrbitControls still orbits the camera on drag; we only react to taps that
    // land on the Earth/overlay and barely moved between down and up.
    const raycaster = new THREE.Raycaster();
    const ndc = new THREE.Vector2();
    let downX = 0, downY = 0;
    const onPointerDown = (e: PointerEvent) => { downX = e.clientX; downY = e.clientY; };
    const onPointerUp = (e: PointerEvent) => {
      if (Math.hypot(e.clientX - downX, e.clientY - downY) > 5) return; // was a drag
      const rect = renderer.domElement.getBoundingClientRect();
      ndc.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      ndc.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(ndc, camera);
      if (raycaster.intersectObjects([earth, tecMesh]).length > 0) {
        frozenRef.current = !frozenRef.current;
      }
    };
    renderer.domElement.addEventListener('pointerdown', onPointerDown);
    renderer.domElement.addEventListener('pointerup', onPointerUp);

    // --- Cleanup ---
    return () => {
      cancelAnimationFrame(frameId);
      renderer.domElement.removeEventListener('pointerdown', onPointerDown);
      renderer.domElement.removeEventListener('pointerup', onPointerUp);
      resizeObserver.disconnect();
      controls.dispose();
      earthGeometry.dispose();
      cloudGeometry.dispose();
      tecGeometry.dispose();
      earthMaterial.dispose();
      cloudMaterial.dispose();
      tecMaterial.dispose();
      dayTexture.dispose();
      nightTexture.dispose();
      cloudTexture.dispose();
      tecTexRef.current?.dispose();
      tecMatRef.current = null;
      tiltGroupRef.current = null;
      renderer.dispose();
      mount.removeChild(renderer.domElement);
    };
  }, []);

  // --- Seasonal axial tilt: orient the tilt frame for the selected date ---
  // The 23.44° tilt magnitude is constant; only the direction it leans relative
  // to the fixed sun changes. It leans toward the sun at the June solstice
  // (λ = 90°), away in December (λ = 270°), and sideways at the equinoxes, so the
  // subsolar latitude comes out as the real solar declination.
  useEffect(() => {
    const sys = tiltGroupRef.current;
    if (!sys) return;
    const date = selectedDate ?? new Date();
    const lambda = eclipticLongitude(date);
    const leanAzimuth = SUN_AZIMUTH + (lambda - Math.PI / 2);

    // Tip the pole toward `leanAzimuth` with a SINGLE rotation about a horizontal
    // axis. A separate rotation about world Y (to "swing" the lean) would also
    // spin the globe about the vertical, which desyncs the sun-facing-meridian
    // hour tracking and drifts the TEC crest onto the night side. A pure
    // horizontal-axis tilt changes only latitude, leaving longitude registration
    // (and thus HOUR_PHASE) intact. Axis is horizontal, perpendicular to the lean
    // direction; rotating +Y about it by AXIAL_TILT leans the north pole toward
    // azimuth `leanAzimuth`.
    const axis = new THREE.Vector3(Math.cos(leanAzimuth), 0, -Math.sin(leanAzimuth));
    sys.quaternion.setFromAxisAngle(axis, AXIAL_TILT);
  }, [selectedDate]);

  // --- Data: react to selected date + mode (off / predicted / actual) ---
  useEffect(() => {
    const mat = tecMatRef.current;
    if (!mat) return;

    if (tecMode === 'off' || !selectedDate) {
      mat.uniforms.uOpacity.value = 0.0;
      dayDataRef.current = null;
      shownHourRef.current = -1;
      return;
    }

    // Difference mode needs BOTH sources (to subtract); the single-source modes
    // fetch just one. Each fetchTecDay is memoized, so 'difference' reuses the
    // same day packs already cached by the 'actual'/'predicted' views.
    mat.uniforms.uDiverging.value = tecMode === 'difference' ? 1.0 : 0.0;
    mat.uniforms.uMax.value = tecMode === 'difference' ? DIFF_SATURATION : 100.0;

    let cancelled = false;
    const load =
      tecMode === 'difference'
        ? Promise.all([
            fetchTecDay(selectedDate, 'actual'),
            fetchTecDay(selectedDate, 'predicted'),
          ]).then(([actual, predicted]) => {
            // Gate the DIFFERENCE overlay only: if the predicted map has any NaN,
            // don't render a partial/misleading difference for this day. This does
            // not affect the 'predicted' mode above, which renders the same pack
            // natively regardless of NaN.
            if (hasNaNHalf(predicted)) {
              throw new Error('predicted map incomplete for this day; difference skipped');
            }
            return diffDay(predicted, actual);
          })
        : fetchTecDay(selectedDate, tecMode === 'predicted' ? 'predicted' : 'actual');

    load
      .then((day) => {
        if (cancelled || !tecMatRef.current) return;
        dayDataRef.current = day;
        shownHourRef.current = -1; // force the loop to refill the texture
        mat.uniforms.uOpacity.value = 0.85;
      })
      .catch((e) => {
        // A missing day/source 404s; clear the overlay so a stale map isn't shown.
        console.error('TEC load failed', e);
        if (!cancelled && tecMatRef.current) {
          mat.uniforms.uOpacity.value = 0.0;
          dayDataRef.current = null;
        }
      });

    return () => {
      cancelled = true;
    };
  }, [selectedDate, tecMode]);

  return <div ref={mountRef} className="globe" />;
};
