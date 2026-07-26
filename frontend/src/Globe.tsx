import React, { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { fetchTecHour, TEC_NLAT, TEC_NLON } from './tec';

const DAY_TEXTURE_URL = '/textures/earth-day.jpg';
const NIGHT_TEXTURE_URL = '/textures/earth-night.jpg';
const CLOUD_TEXTURE_URL = '/textures/earth-cloud.jpg';

const SUN_DIRECTION = new THREE.Vector3(5, 3, 5).normalize();

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

  // Simple blue -> cyan -> green -> yellow -> red ramp.
  vec3 ramp(float t) {
    vec3 c1 = vec3(0.03, 0.05, 0.35);
    vec3 c2 = vec3(0.00, 0.55, 0.75);
    vec3 c3 = vec3(0.15, 0.75, 0.20);
    vec3 c4 = vec3(0.95, 0.85, 0.10);
    vec3 c5 = vec3(0.85, 0.15, 0.10);
    if (t < 0.25) return mix(c1, c2, t / 0.25);
    if (t < 0.50) return mix(c2, c3, (t - 0.25) / 0.25);
    if (t < 0.75) return mix(c3, c4, (t - 0.50) / 0.25);
    return mix(c4, c5, (t - 0.75) / 0.25);
  }

  void main() {
    vec2 uv = vec2(fract(vUv.x + uLonOffset), vUv.y);
    float v = texture2D(tecMap, uv).r;
    if (v != v) discard;                       // NaN (missing) -> transparent
    float t = clamp((v - uMin) / (uMax - uMin), 0.0, 1.0);
    gl_FragColor = vec4(ramp(t), uOpacity);
    #include <colorspace_fragment>
  }
`;

interface GlobeProps {
  selectedDate: Date | null;
  showTec: boolean;
}

export const Globe: React.FC<GlobeProps> = ({ selectedDate, showTec }) => {
  const mountRef = useRef<HTMLDivElement | null>(null);
  // Bridges from the scene-effect (built once) to the data-effect (runs on prop change).
  const tecMatRef = useRef<THREE.ShaderMaterial | null>(null);
  const tecTexRef = useRef<THREE.DataTexture | null>(null);

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
      earth.rotation.y += 0.0015;
      tecMesh.rotation.y = earth.rotation.y; // overlay is geo-fixed to the earth
      cloudMesh.rotation.y += 0.0018;

      controls.update();
      renderer.render(scene, camera);
      frameId = requestAnimationFrame(animate);
    };
    animate();

    // --- Cleanup ---
    return () => {
      cancelAnimationFrame(frameId);
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

  // --- Data: react to selected date + toggle ---
  useEffect(() => {
    const mat = tecMatRef.current;
    if (!mat) return;

    if (!showTec || !selectedDate) {
      mat.uniforms.uOpacity.value = 0.0;
      return;
    }

    let cancelled = false;
    fetchTecHour(selectedDate, 12) // noon UTC for MVP
      .then((hour) => {
        if (cancelled || !tecMatRef.current) return;
        tecTexRef.current?.dispose();
        const tex = new THREE.DataTexture(
          hour, TEC_NLON, TEC_NLAT, THREE.RedFormat, THREE.HalfFloatType,
        );
        tex.minFilter = THREE.LinearFilter;
        tex.magFilter = THREE.LinearFilter;
        tex.wrapS = THREE.RepeatWrapping;
        tex.needsUpdate = true;
        tecTexRef.current = tex;
        mat.uniforms.tecMap.value = tex;
        mat.uniforms.uOpacity.value = 0.85;
      })
      .catch((e) => console.error('TEC load failed', e));

    return () => {
      cancelled = true;
    };
  }, [selectedDate, showTec]);

  return <div ref={mountRef} className="globe" />;
};
