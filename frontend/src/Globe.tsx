import React, { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { fetchTecDay, TEC_NLAT, TEC_NLON } from './tec';
import type { TecMode } from './tecMode';

const DAY_TEXTURE_URL = '/textures/earth-day.jpg';
const NIGHT_TEXTURE_URL = '/textures/earth-night.jpg';
const CLOUD_TEXTURE_URL = '/textures/earth-cloud.jpg';

const SUN_DIRECTION = new THREE.Vector3(5, 3, 5).normalize();

const TEC_CELLS = TEC_NLAT * TEC_NLON; // 71*73 values per hourly map

// --- Coupling the displayed UT hour to the globe's spin -----------------------
// TEC peaks near the subsolar point, so the bright crest must sit over the lit
// hemisphere. The overlay is geo-locked to the Earth (rotates with it), so a
// FIXED hour would let the crest drift off the dayside as the globe spins.
// Instead we pick, each frame, the UT hour whose subsolar longitude currently
// faces the (fixed) sun -- one full revolution then sweeps all 24 UT hours,
// exactly as a spinning Earth advances UT in reality.
//
// Derivation: geographic longitude L sits at world azimuth -L; after the Earth
// spins by angle theta its azimuth is -L - theta. The subsolar longitude for
// UT hour h is ~ (180 - 15h) deg. Requiring that longitude to face the sun
// (azimuth atan2(5,5) = 45 deg for SUN_DIRECTION) gives, after the theta terms
// cancel, h = theta_deg/15 + 15. The crest then stays pinned at the sun azimuth
// for ALL theta. HOUR_PHASE is that constant; nudge it by +/-1 to rotate the
// crest one UT hour (15 deg) if the texture registration needs a visual tweak.
const HOUR_PHASE = 15;
const DEG_PER_HOUR = 15;

function hourFromRotation(rotationY: number): number {
  const deg = (rotationY * 180) / Math.PI;
  const h = Math.floor(deg / DEG_PER_HOUR + HOUR_PHASE);
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
  varying vec2 vUv;

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
    float t = clamp((v - uMin) / (uMax - uMin), 0.0, 1.0);

    // Fade the low end so quiet regions let the Earth show through and only the
    // TEC crests read as solid. Flat opacity would veil the whole night side.
    float a = uOpacity * smoothstep(0.0, 0.35, t);

    // ramp() is sRGB; the renderer works in linear + re-encodes via
    // colorspace_fragment, so linearise here to avoid a double gamma encode.
    gl_FragColor = vec4(pow(clamp(ramp(t), 0.0, 1.0), vec3(2.2)), a);
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
    camera.position.set(0, 0, 3);

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
    scene.add(earth);

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
    scene.add(cloudMesh);

    // TEC overlay (translucent sphere just above the surface; data set later)
    const tecGeometry = new THREE.SphereGeometry(1.01, 64, 64);
    const tecMaterial = new THREE.ShaderMaterial({
      uniforms: {
        tecMap: { value: null },
        uMin: { value: 0.0 },
        uMax: { value: 100.0 },
        uOpacity: { value: 0.0 },
        uLonOffset: { value: 0.0 },
      },
      vertexShader: tecVertexShader,
      fragmentShader: tecFragmentShader,
      transparent: true,
      depthWrite: false,
    });
    const tecMesh = new THREE.Mesh(tecGeometry, tecMaterial);
    scene.add(tecMesh);
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
    controls.maxDistance = 6;

    const resizeObserver = new ResizeObserver(() => {
      const w = mount.clientWidth;
      const h = mount.clientHeight;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    });
    resizeObserver.observe(mount);

    // --- Animation Loop ---
    let frameId: number;
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
        const h = hourFromRotation(earth.rotation.y);
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
      renderer.dispose();
      mount.removeChild(renderer.domElement);
    };
  }, []);

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

    // Load the whole day for the selected source; the animation loop slices out
    // the hour that matches the current spin (see hourFromRotation). Re-selecting
    // a cached (source, day) is instant because fetchTecDay memoizes it.
    const source = tecMode === 'predicted' ? 'predicted' : 'actual';
    let cancelled = false;
    fetchTecDay(selectedDate, source)
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
