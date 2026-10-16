import * as pc from "playcanvas";
import * as THREE from "three";

// Three.js : calculs uniquement. Aucun WebGLRenderer Three.js.
// PlayCanvas : chargement natif PLY/SOG, rendu et picking.

// ---------------------------------------------------------------------------
// État général
// ---------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const container = $("viewer");
const params = new URLSearchParams(location.search);

const sleep = (ms) =>
  new Promise((resolve) => setTimeout(resolve, ms));

const pressedKeys = new Set();
const heldButtons = new Map();

const sceneBox = new THREE.Box3();
const sceneCenter = new THREE.Vector3();

const principalAxes = {
  x: new THREE.Vector3(1, 0, 0),
  y: new THREE.Vector3(0, 1, 0),
  z: new THREE.Vector3(0, 0, 1),
};

// Caméra mathématique, synchronisée avec la caméra PlayCanvas.
const camera = new THREE.PerspectiveCamera(50, 1, 0.01, 1000);
camera.position.set(0, 0, 5);
camera.up.set(0, 1, 0);

const target = new THREE.Vector3();

let app = null;
let cameraEntity = null;
let splatEntity = null;
let sceneAsset = null;
let picker = null;
let centers = null;

let sceneReady = false;
let isLoading = false;
let fatalError = false;
let sceneRadius = 1;
let sceneCount = 0;
let mouseDrag = null;
let wheelTimer = null;

let pendingCamera = params.get("camera_pos");
let pendingUp = params.get("camera_up");

// Invalidation des résultats de picking asynchrones.
let pickSerial = 0;
let picking = false;

const canvas = document.createElement("canvas");
canvas.setAttribute("aria-label", "Vue 3D des Gaussian Splats");
container.appendChild(canvas);

const canNavigate = () =>
  !!app &&
  sceneReady &&
  !isLoading &&
  !fatalError &&
  !picking &&
  !gifState.encoding &&
  !gifState.finalizing;

// ---------------------------------------------------------------------------
// GIF
// ---------------------------------------------------------------------------

const GIF_MAX_FRAMES = 60;
const GIF_MAX_WIDTH = 640;
const GIF_MAX_HEIGHT = 480;
const GIF_SETTLE_MS = 600;

const gifState = {
  recording: false,
  encoding: false,
  finalizing: false,
  frames: [],
  width: 0,
  height: 0,
  delay: 100,
  pendingCapture: false,
  lastCapturedSignature: null,
  observedSignature: null,
  changedAt: 0,
  downloadUrl: null,
};

let gifEncoderPromise = null;
let legacyConverterPromise = null;

// ---------------------------------------------------------------------------
// Interface
// ---------------------------------------------------------------------------

function setStatus(message, error = false) {
  $("status").textContent = message;
  $("status").classList.toggle("error", error);
}

function setGifStatus(message, error = false) {
  $("gifStatus").textContent = message;
  $("gifStatus").classList.toggle("error", error);
  $("gifStatus").classList.toggle(
    "recording",
    gifState.recording && !error
  );
}

function updateGifUI() {
  const blocked =
    isLoading ||
    fatalError ||
    gifState.encoding ||
    gifState.finalizing;

  $("gifStart").disabled =
    blocked || !sceneReady || gifState.recording;

  $("gifStop").disabled =
    blocked ||
    (!gifState.recording && !gifState.frames.length);

  $("gifCancel").disabled =
    blocked ||
    (!gifState.recording && !gifState.frames.length);

  $("gifDelay").disabled =
    blocked || gifState.recording;
}

function refreshUI() {
  const blocked =
    isLoading ||
    fatalError ||
    gifState.encoding ||
    gifState.finalizing;

  document.querySelectorAll(
    "#panel button, #panel input, #panel select, #panel textarea"
  ).forEach((element) => {
    if (element.id !== "togglePanel") {
      element.disabled = blocked;
    }
  });

  updateGifUI();
}

function getPrincipalAxisName() {
  const value = $("principalAxis").value;
  return Object.hasOwn(principalAxes, value) ? value : "z";
}

function getPrincipalAxis() {
  return principalAxes[getPrincipalAxisName()];
}

function updatePrincipalAxisHelp() {
  const axis = getPrincipalAxisName().toUpperCase();

  $("principalAxisHelp").textContent =
    `Axe ${axis} du monde, passant par le point visé au centre.`;

  $("orbitPrincipal").title =
    `Maintenir : rotation autour de l’axe ${axis} du monde ` +
    "passant par le point visé au centre";
}

function updateKeyboardHelp() {
  $("keyboardHelp").textContent =
    $("keyboardLayout").value === "azerty"
      ? "ZQSD : déplacer · A/E : descendre / monter"
      : "WASD : déplacer · Q/E : descendre / monter";
}

function clearNavigation() {
  const wasMoving =
    heldButtons.size > 0 ||
    !!mouseDrag?.moved ||
    [...pressedKeys].some((key) => !!actionForKey(key));

  pickSerial++;
  picking = false;

  pressedKeys.clear();

  heldButtons.forEach((button) => {
    button.classList.remove("held");
  });

  heldButtons.clear();
  mouseDrag = null;

  clearTimeout(wheelTimer);
  wheelTimer = null;

  if (wasMoving) requestGifCapture();
}

// ---------------------------------------------------------------------------
// PlayCanvas
// ---------------------------------------------------------------------------

function resizeViewer() {
  if (!app) return;

  const width = Math.max(1, container.clientWidth);
  const height = Math.max(1, container.clientHeight);

  app.resizeCanvas(width, height);

  camera.aspect = width / height;
  camera.updateProjectionMatrix();

  syncCamera();
  requestGifCapture();
}

function updateClipping() {
  const distance = camera.position.distanceTo(target);

  camera.near = Math.max(
    Math.min(distance, sceneRadius) * 1e-4,
    1e-6
  );

  camera.far = Math.max(
    camera.near * 100,
    camera.position.distanceTo(sceneCenter) + sceneRadius * 4,
    distance * 2
  );

  camera.updateProjectionMatrix();
}

function syncCamera() {
  camera.lookAt(target);
  camera.updateMatrixWorld(true);
  updateClipping();

  if (!cameraEntity) return;

  cameraEntity.setPosition(
    camera.position.x,
    camera.position.y,
    camera.position.z
  );

  // Les deux moteurs utilisent une caméra regardant vers -Z.
  const q = camera.quaternion;

  cameraEntity.setRotation(q.x, q.y, q.z, q.w);
  cameraEntity.camera.fov = camera.fov;
  cameraEntity.camera.horizontalFov = false;
  cameraEntity.camera.nearClip = camera.near;
  cameraEntity.camera.farClip = camera.far;
}

function nextRenderedFrame() {
  return new Promise((resolve) => {
    app.once("postrender", resolve);
  });
}

function applyUpFlip() {
  if (!splatEntity) return;

  // Même convention que le code initial :
  // rotation de 180° autour de X, sans rotation implicite supplémentaire.
  splatEntity.setLocalEulerAngles(
    $("flipUp").checked ? 180 : 0,
    0,
    0
  );
}

function releaseScene() {
  sceneReady = false;

  splatEntity?.destroy();
  splatEntity = null;

  if (sceneAsset) {
    sceneAsset.unload();
    app.assets.remove(sceneAsset);
    sceneAsset = null;
  }

  centers = null;
  sceneCount = 0;
}

function loadNativeAsset(url, name) {
  const asset = new pc.Asset(name, "gsplat", {
    url: new URL(url, location.href).href,
    filename: name,
  });

  sceneAsset = asset;
  app.assets.add(asset);

  return new Promise((resolve, reject) => {
    const onLoad = () => {
      asset.off("error", onError);
      resolve(asset);
    };

    const onError = (error) => {
      asset.off("load", onLoad);
      reject(new Error(String(error)));
    };

    asset.once("load", onLoad);
    asset.once("error", onError);

    app.assets.load(asset);
  });
}

// ---------------------------------------------------------------------------
// Compatibilité SPLAT / KSPLAT
// SOG ne passe jamais dans ce convertisseur.
// ---------------------------------------------------------------------------

function loadLegacyConverter() {
  if (!legacyConverterPromise) {
    legacyConverterPromise = import(
      "https://esm.sh/@playcanvas/splat-transform@3.10.1?external=playcanvas"
    ).catch((error) => {
      legacyConverterPromise = null;
      throw error;
    });
  }

  return legacyConverterPromise;
}

async function convertLegacyToPly(url, extension) {
  const {
    readFile,
    writeSource,
    createChunkDataPool,
    UrlReadFileSystem,
    MemoryFileSystem,
  } = await loadLegacyConverter();

  const absoluteUrl = new URL(url, location.href).href;
  const urlFs = new UrlReadFileSystem();

  const [source] = await readFile({
    filename: `input.${extension}`,
    inputFormat: extension,
    fileSystem: {
      createSource() {
        return urlFs.createSource(absoluteUrl);
      },
    },
  });

  if (!source) {
    throw new Error("Aucune scène trouvée dans le fichier.");
  }

  try {
    const output = new MemoryFileSystem();

    await writeSource({
      filename: "converted.ply",
      outputFormat: "ply",
      source,
      pool: createChunkDataPool(),
      options: {},
    }, output);

    const bytes = output.results.get("converted.ply");

    if (!bytes?.byteLength) {
      throw new Error("La conversion en PLY n’a produit aucune donnée.");
    }

    return URL.createObjectURL(
      new Blob([bytes], {
        type: "application/octet-stream",
      })
    );
  } finally {
    await source.close();
  }
}

// ---------------------------------------------------------------------------
// Chargement
// ---------------------------------------------------------------------------

function filenameFromUrl(url) {
  try {
    const leaf = new URL(url, location.href)
      .pathname.split("/").pop();

    return decodeURIComponent(leaf || "scene.ply");
  } catch {
    return url.split("/").pop().split(/[?#]/)[0] || "scene.ply";
  }
}

async function resetAndLoad(url, name) {
  if (
    !app ||
    isLoading ||
    fatalError ||
    gifState.encoding ||
    gifState.finalizing
  ) {
    return;
  }

  if (!/\.(sog|ply|splat|ksplat)$/i.test(name)) {
    setStatus(
      "Formats acceptés : .sog, .ply, .splat ou .ksplat.",
      true
    );
    return;
  }

  isLoading = true;
  clearNavigation();
  cancelGifRecording();
  refreshUI();

  let convertedUrl = null;

  try {
    releaseScene();

    setStatus(`Chargement de ${name}…`);

    let loadUrl = url;
    let loadName = name;

    const extension = name.split(".").pop().toLowerCase();

    if (extension === "splat" || extension === "ksplat") {
      setStatus(`Conversion locale de ${name} en PLY…`);

      convertedUrl = await convertLegacyToPly(url, extension);

      loadUrl = convertedUrl;
      loadName = name.replace(/\.(splat|ksplat)$/i, ".ply");
    }

    const asset = await loadNativeAsset(loadUrl, loadName);
    const resource = asset.resource;

    if (!resource) {
      throw new Error("PlayCanvas n’a pas créé la ressource GSplat.");
    }

    // Centres CPU préparés par PlayCanvas pour le cadrage robuste.
    centers = resource.centers;

    if (!centers?.length) {
      throw new Error(
        "Les centres des splats ne sont pas disponibles pour le cadrage."
      );
    }

    sceneCount = resource.numSplats || centers.length / 3;

    splatEntity = new pc.Entity("Gaussian scene");
    splatEntity.addComponent("gsplat", {
      asset,
      unified: true,
    });

    app.root.addChild(splatEntity);

    camera.position.set(0, 0, 5);
    camera.up.set(0, 1, 0);
    target.set(0, 0, 0);

    applyUpFlip();
    computeSceneBounds();
    fitToView();

    let cameraError = null;

    if (pendingCamera !== null) {
      try {
        applyCameraString(pendingCamera, pendingUp);
      } catch (error) {
        cameraError = error.message;
      }

      pendingCamera = null;
      pendingUp = null;
    }

    // Laisser le moteur préparer le premier rendu.
    await nextRenderedFrame();

    sceneReady = true;

    const size = sceneBox.getSize(new THREE.Vector3());

    const info =
      `${name} — ${sceneCount.toLocaleString()} splats · ` +
      `${size.x.toFixed(1)} × ${size.y.toFixed(1)} × ` +
      `${size.z.toFixed(1)} · PlayCanvas`;

    setStatus(
      cameraError ? `${info} · ${cameraError}` : info,
      !!cameraError
    );
  } catch (error) {
    console.error("Chargement :", error);

    releaseScene();

    setStatus(`Erreur : ${error?.message || error}`, true);
  } finally {
    if (convertedUrl) URL.revokeObjectURL(convertedUrl);

    isLoading = false;
    $("fileInput").value = "";

    refreshUI();
  }
}

async function loadFile(file) {
  if (
    !file ||
    !app ||
    isLoading ||
    gifState.encoding ||
    gifState.finalizing
  ) {
    return;
  }

  if (!/\.(sog|ply|splat|ksplat)$/i.test(file.name)) {
    setStatus(
      "Formats acceptés : .sog, .ply, .splat ou .ksplat.",
      true
    );
    $("fileInput").value = "";
    return;
  }

  const url = URL.createObjectURL(file);

  try {
    await resetAndLoad(url, file.name);
  } finally {
    URL.revokeObjectURL(url);
  }
}

// ---------------------------------------------------------------------------
// Emprise et cadrage
// ---------------------------------------------------------------------------

function computeSceneBounds() {
  const count = centers.length / 3;
  const coordinates = [[], [], []];
  const stride = Math.max(1, Math.ceil(count / 100000));
  const flip = $("flipUp").checked ? -1 : 1;

  for (let i = 0; i < count; i += stride) {
    const x = centers[i * 3];
    const y = centers[i * 3 + 1] * flip;
    const z = centers[i * 3 + 2] * flip;

    if (
      Number.isFinite(x) &&
      Number.isFinite(y) &&
      Number.isFinite(z)
    ) {
      coordinates[0].push(x);
      coordinates[1].push(y);
      coordinates[2].push(z);
    }
  }

  if (!coordinates[0].length) {
    sceneBox.set(
      new THREE.Vector3(-1, -1, -1),
      new THREE.Vector3(1, 1, 1)
    );
  } else {
    const sorted = coordinates.map((values) =>
      Float64Array.from(values).sort()
    );

    const percentile = (values, q) =>
      values[Math.min(
        values.length - 1,
        Math.floor(values.length * q)
      )];

    sceneBox.set(
      new THREE.Vector3(
        ...sorted.map((values) => percentile(values, 0.01))
      ),
      new THREE.Vector3(
        ...sorted.map((values) => percentile(values, 0.99))
      )
    );

    const size = sceneBox.getSize(new THREE.Vector3());

    sceneBox.expandByScalar(
      Math.max(size.x, size.y, size.z, 1e-3) * 1e-3
    );
  }

  sceneBox.getCenter(sceneCenter);

  sceneRadius = Math.max(
    sceneBox.getBoundingSphere(new THREE.Sphere()).radius,
    1e-3
  );
}

function distanceToFit(direction) {
  const vFov = THREE.MathUtils.degToRad(camera.fov);
  const hFov = 2 * Math.atan(
    Math.tan(vFov / 2) * camera.aspect
  );

  const forward = direction.clone().normalize().negate();
  const up = camera.up.clone().normalize();

  if (Math.abs(forward.dot(up)) > 0.999) {
    up.set(0, 0, 1);
    if (Math.abs(forward.dot(up)) > 0.999) up.set(1, 0, 0);
  }

  const right = new THREE.Vector3()
    .crossVectors(forward, up).normalize();

  const trueUp = new THREE.Vector3()
    .crossVectors(right, forward).normalize();

  const half = sceneBox.getSize(new THREE.Vector3())
    .multiplyScalar(0.5);

  const extent = (axis) =>
    Math.abs(half.x * axis.x) +
    Math.abs(half.y * axis.y) +
    Math.abs(half.z * axis.z);

  return (
    Math.max(
      extent(trueUp) / Math.tan(vFov / 2),
      extent(right) / Math.tan(hFov / 2)
    ) + extent(forward)
  ) * 1.08;
}

function fitToView(direction = null) {
  const dir = direction
    ? direction.clone()
    : camera.position.clone().sub(target);

  if (dir.lengthSq() < 1e-12) dir.set(0, 0.6, 1);

  dir.normalize();

  camera.up.addScaledVector(dir, -camera.up.dot(dir));

  if (camera.up.lengthSq() < 1e-12) {
    camera.up.set(0, 0, 1);

    if (Math.abs(camera.up.dot(dir)) > 0.999) {
      camera.up.set(1, 0, 0);
    }

    camera.up.addScaledVector(dir, -camera.up.dot(dir));
  }

  camera.up.normalize();

  target.copy(sceneCenter);

  camera.position.copy(sceneCenter)
    .addScaledVector(dir, distanceToFit(dir));

  syncCamera();
}

function setView(kind) {
  if (!canNavigate()) return;

  clearNavigation();

  const axes = {
    front: new THREE.Vector3(0, 1, 0),
    top: new THREE.Vector3(0, 0, 1),
    side: new THREE.Vector3(1, 0, 0),
  };

  const up = axes[kind]?.clone();
  if (!up) return;

  const forward = target.clone()
    .sub(camera.position).normalize();

  up.addScaledVector(forward, -up.dot(forward));

  if (up.lengthSq() < 1e-12) {
    setStatus(
      "Cet axe est parallèle à la visée : impossible " +
      "de le rendre vertical sans changer la visée.",
      true
    );
    return;
  }

  camera.up.copy(up.normalize());

  syncCamera();
  requestGifCapture();

  const axis = kind === "front" ? "Y" : kind === "top" ? "Z" : "X";

  setStatus(`${axis} vertical · vue conservée.`);
}

function recenterTarget() {
  if (!canNavigate()) return;

  clearNavigation();

  const offset = camera.position.clone().sub(target);

  target.copy(sceneCenter);
  camera.position.copy(sceneCenter).add(offset);

  syncCamera();
  requestGifCapture();
}

// ---------------------------------------------------------------------------
// Rotation et déplacements
// ---------------------------------------------------------------------------

function rotateAroundAxis(axis, angle) {
  if (!angle) return;

  const rotation = new THREE.Quaternion()
    .setFromAxisAngle(axis.clone().normalize(), angle);

  const offset = camera.position.clone()
    .sub(target).applyQuaternion(rotation);

  camera.position.copy(target).add(offset);
  camera.up.applyQuaternion(rotation).normalize();

  camera.lookAt(target);
  camera.updateMatrixWorld(true);
}

function orbit(dx, dy) {
  if (dx) {
    rotateAroundAxis(
      new THREE.Vector3(0, 1, 0)
        .applyQuaternion(camera.quaternion),
      dx
    );
  }

  if (dy) {
    rotateAroundAxis(
      new THREE.Vector3(1, 0, 0)
        .applyQuaternion(camera.quaternion),
      dy
    );
  }
}

function panPixels(dx, dy, height) {
  const PAN_GAIN = 3;
  const speed = Number($("step").value) / 10;

  const distance = Math.max(
    camera.position.distanceTo(target),
    sceneRadius * 0.1
  );

  const unitsPerPixel =
    2 * distance *
    Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) /
    Math.max(height, 1);

  const scale = unitsPerPixel * PAN_GAIN * speed;

  const right = new THREE.Vector3(1, 0, 0)
    .applyQuaternion(camera.quaternion);

  const up = new THREE.Vector3(0, 1, 0)
    .applyQuaternion(camera.quaternion);

  const delta = right.multiplyScalar(-dx * scale)
    .addScaledVector(up, dy * scale);

  camera.position.add(delta);
  target.add(delta);
}

function translate(x, y, z, distance) {
  if (!x && !y && !z) return;

  const delta = new THREE.Vector3(x, y, -z)
    .normalize()
    .applyQuaternion(camera.quaternion)
    .multiplyScalar(distance);

  camera.position.add(delta);
  target.add(delta);
}

function zoomBy(factor) {
  const offset = camera.position.clone().sub(target);

  if (offset.lengthSq() < 1e-20) return;

  const distance = THREE.MathUtils.clamp(
    offset.length() * factor,
    sceneRadius * 1e-4,
    sceneRadius * 1e5
  );

  camera.position.copy(target)
    .add(offset.setLength(distance));
}

// ---------------------------------------------------------------------------
// Picking central PlayCanvas
// ---------------------------------------------------------------------------

async function selectPointInView(key, button) {
  const serial = ++pickSerial;
  picking = true;

  try {
    setStatus("Sélection du point visé au centre…");

    // Le rendu précédent doit avoir préparé les splats.
    await nextRenderedFrame();

    if (
      serial !== pickSerial ||
      heldButtons.get(key) !== button ||
      !sceneReady
    ) {
      return false;
    }

    picker.resize(
      Math.max(1, canvas.width),
      Math.max(1, canvas.height)
    );

    const worldLayer = app.scene.layers.getLayerByName("World");

    picker.prepare(
      cameraEntity.camera,
      app.scene,
      [worldLayer]
    );

    const point = await picker.getWorldPointAsync(
      Math.floor(picker.width / 2),
      Math.floor(picker.height / 2)
    );

    // Un relâchement du bouton ou un nouveau chargement
    // invalide la sélection en cours.
    if (
      serial !== pickSerial ||
      heldButtons.get(key) !== button ||
      !sceneReady
    ) {
      return false;
    }

    if (!point) {
      setStatus("Aucun splat visé au centre de l’image.", true);
      return false;
    }

    target.set(point.x, point.y, point.z);

    if (target.distanceToSquared(camera.position) < 1e-12) {
      setStatus("Le point sélectionné est trop proche de la caméra.", true);
      return false;
    }

    syncCamera();
    requestGifCapture();

    setStatus(
      "Rotation autour du point visé · axe " +
      `${getPrincipalAxisName().toUpperCase()}.`
    );

    return true;
  } catch (error) {
    if (serial === pickSerial) {
      console.error("Picking :", error);
      setStatus(
        `Sélection impossible : ${error?.message || error}`,
        true
      );
    }

    return false;
  } finally {
    if (serial === pickSerial) picking = false;
  }
}

// ---------------------------------------------------------------------------
// Souris
// ---------------------------------------------------------------------------

function installMouseControls() {
  canvas.style.touchAction = "none";

  canvas.addEventListener("contextmenu", (event) => {
    event.preventDefault();
  });

  canvas.addEventListener("pointerdown", (event) => {
    if (!canNavigate() || mouseDrag || event.button > 2) return;

    event.preventDefault();
    clearNavigation();

    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }

    canvas.setPointerCapture(event.pointerId);

    mouseDrag = {
      id: event.pointerId,
      button: event.button,
      x: event.clientX,
      y: event.clientY,
      moved: false,
    };
  });

  canvas.addEventListener("pointermove", (event) => {
    if (!mouseDrag || mouseDrag.id !== event.pointerId) return;

    if (!event.buttons || !canNavigate()) {
      const moved = mouseDrag.moved;
      mouseDrag = null;
      if (moved) requestGifCapture();
      return;
    }

    const dx = event.clientX - mouseDrag.x;
    const dy = event.clientY - mouseDrag.y;

    mouseDrag.x = event.clientX;
    mouseDrag.y = event.clientY;

    if (dx || dy) mouseDrag.moved = true;

    if (mouseDrag.button === 0 && !event.shiftKey) {
      orbit(-dx * 0.005, -dy * 0.005);
    } else {
      panPixels(dx, dy, canvas.clientHeight);
    }

    syncCamera();
    requestGifCapture();
  });

  const release = (event) => {
    if (mouseDrag?.id !== event.pointerId) return;

    const moved = mouseDrag.moved;
    mouseDrag = null;

    if (moved) requestGifCapture();
  };

  ["pointerup", "pointercancel", "lostpointercapture"]
    .forEach((type) => canvas.addEventListener(type, release));

  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    if (!canNavigate()) return;

    const pixels = event.deltaMode === 1
      ? event.deltaY * 16
      : event.deltaMode === 2
        ? event.deltaY * canvas.clientHeight
        : event.deltaY;

    zoomBy(Math.exp(
      THREE.MathUtils.clamp(pixels * 0.001, -1, 1)
    ));

    syncCamera();
    requestGifCapture();

    clearTimeout(wheelTimer);

    wheelTimer = setTimeout(() => {
      wheelTimer = null;
      requestGifCapture();
    }, 250);
  }, { passive: false });
}

// ---------------------------------------------------------------------------
// Caméra : import / export
// ---------------------------------------------------------------------------

function parseNumbers(value, count) {
  const parts = value.split(",").map((part) => part.trim());
  const numbers = parts.map(Number);

  if (
    parts.length !== count ||
    parts.some((part) => part === "") ||
    !numbers.every(Number.isFinite)
  ) {
    throw new Error(
      `Valeur invalide : fournir ${count} nombres ` +
      "séparés par des virgules."
    );
  }

  return numbers;
}

function serialize(values) {
  return values.map((value) =>
    Number(value.toPrecision(12))
  ).join(",");
}

function getCameraString() {
  const up = new THREE.Vector3(0, 1, 0)
    .applyQuaternion(camera.quaternion).normalize();

  return serialize([
    ...camera.position.toArray(),
    ...target.toArray(),
    ...up.toArray(),
  ]);
}

function applyCameraString(value, upValue = null) {
  const count = value.split(",").length;

  if (count !== 6 && count !== 9) {
    throw new Error("Caméra invalide : fournir 6 ou 9 nombres.");
  }

  const values = parseNumbers(value, count);

  const position = new THREE.Vector3(...values.slice(0, 3));
  const newTarget = new THREE.Vector3(...values.slice(3, 6));

  const up = count === 9
    ? new THREE.Vector3(...values.slice(6, 9))
    : upValue
      ? new THREE.Vector3(...parseNumbers(upValue, 3))
      : new THREE.Vector3(0, 1, 0);

  const direction = newTarget.clone().sub(position);

  if (direction.lengthSq() < 1e-12) {
    throw new Error(
      "La position et la cible doivent être différentes."
    );
  }

  if (up.lengthSq() < 1e-12) {
    throw new Error("Le vecteur vertical ne doit pas être nul.");
  }

  direction.normalize();
  up.normalize();
  up.addScaledVector(direction, -up.dot(direction));

  if (up.lengthSq() < 1e-12) {
    if (count === 9 || upValue) {
      throw new Error(
        "Le vecteur vertical est parallèle à la visée."
      );
    }

    up.set(0, 0, 1);

    if (Math.abs(up.dot(direction)) > 0.999) up.set(1, 0, 0);

    up.addScaledVector(direction, -up.dot(direction));
  }

  camera.position.copy(position);
  camera.up.copy(up.normalize());
  target.copy(newTarget);

  syncCamera();

  $("cameraValue").value = getCameraString();
}

function rememberCamera() {
  const url = new URL(location.href);

  url.searchParams.set("camera_pos", getCameraString());
  url.searchParams.delete("camera_up");
  url.searchParams.set("flipUp", $("flipUp").checked ? "1" : "0");

  try {
    history.replaceState(null, "", url);
  } catch (error) {
    console.warn("Mise à jour de l’URL :", error);
  }
}

async function exportCamera() {
  if (!canNavigate()) return;

  clearNavigation();

  const value = getCameraString();

  $("cameraValue").value = value;
  $("cameraSection").open = true;

  rememberCamera();

  try {
    await navigator.clipboard.writeText(value);
    setStatus(`Caméra copiée : --camera-pos="${value}"`);
  } catch {
    $("cameraValue").focus();
    $("cameraValue").select();

    setStatus("Copier le texte sélectionné avec Ctrl/Cmd+C.");
  }
}

function importCamera() {
  if (!canNavigate()) return;

  clearNavigation();

  try {
    applyCameraString($("cameraValue").value);
    rememberCamera();
    requestGifCapture();

    setStatus("Caméra restaurée.");
  } catch (error) {
    setStatus(error.message, true);
  }
}

// ---------------------------------------------------------------------------
// Capture GIF après stabilisation de la caméra
// ---------------------------------------------------------------------------

function navigationIsActive() {
  return (
    picking ||
    mouseDrag !== null ||
    wheelTimer !== null ||
    heldButtons.size > 0 ||
    [...pressedKeys].some((key) => !!actionForKey(key))
  );
}

function cameraCaptureSignature() {
  return [
    ...camera.position.toArray(),
    ...camera.quaternion.toArray(),
    ...target.toArray(),
    canvas.width,
    canvas.height,
    Number($("flipUp").checked),
  ].map((value) =>
    Number(value).toPrecision(8)
  ).join(",");
}

function requestGifCapture() {
  if (!gifState.recording) return;

  gifState.pendingCapture = true;
  gifState.changedAt = performance.now();
}

function clearGifDownload() {
  if (gifState.downloadUrl) {
    URL.revokeObjectURL(gifState.downloadUrl);
    gifState.downloadUrl = null;
  }

  $("gifDownload").hidden = true;
  $("gifDownload").removeAttribute("href");
}

function startGifRecording() {
  if (!canNavigate() || gifState.recording) return;

  clearNavigation();

  if (!canvas.width || !canvas.height) {
    setGifStatus("La vue 3D n’est pas prête.", true);
    return;
  }

  const scale = Math.min(
    1,
    GIF_MAX_WIDTH / canvas.width,
    GIF_MAX_HEIGHT / canvas.height
  );

  clearGifDownload();

  gifState.width = Math.max(1, Math.round(canvas.width * scale));
  gifState.height = Math.max(1, Math.round(canvas.height * scale));
  gifState.delay = Number($("gifDelay").value);
  gifState.frames = [];
  gifState.lastCapturedSignature = null;
  gifState.observedSignature = null;
  gifState.pendingCapture = false;
  gifState.recording = true;

  requestGifCapture();

  setGifStatus("● Capture active — attente de stabilisation…");
  refreshUI();
}

function captureGifFrame() {
  const output = document.createElement("canvas");

  output.width = gifState.width;
  output.height = gifState.height;

  const context = output.getContext("2d", {
    willReadFrequently: true,
  });

  if (!context) {
    throw new Error("Impossible de créer le canvas de capture.");
  }

  context.fillStyle = "#10131b";
  context.fillRect(0, 0, output.width, output.height);

  const scale = Math.min(
    output.width / canvas.width,
    output.height / canvas.height
  );

  const width = canvas.width * scale;
  const height = canvas.height * scale;

  // Appel effectué dans postrender : le canvas contient
  // l’image que PlayCanvas vient de produire.
  context.drawImage(
    canvas,
    (output.width - width) / 2,
    (output.height - height) / 2,
    width,
    height
  );

  gifState.frames.push(
    context.getImageData(0, 0, output.width, output.height).data
  );

  if (gifState.frames.length >= GIF_MAX_FRAMES) {
    gifState.recording = false;

    setGifStatus(
      `Limite de ${GIF_MAX_FRAMES} images atteinte. ` +
      "Cliquer sur « Terminer et exporter »."
    );
  } else {
    setGifStatus(
      `● Capture active — ${gifState.frames.length} position(s) · ` +
      `${gifState.width} × ${gifState.height} px`
    );
  }

  updateGifUI();
}

function processGifCapture() {
  if (
    !gifState.recording ||
    !sceneReady ||
    isLoading ||
    document.hidden
  ) {
    return;
  }

  try {
    const now = performance.now();
    const signature = cameraCaptureSignature();

    if (signature !== gifState.observedSignature) {
      gifState.observedSignature = signature;
      gifState.changedAt = now;
      gifState.pendingCapture = true;
    }

    if (
      !gifState.pendingCapture ||
      navigationIsActive() ||
      now - gifState.changedAt < GIF_SETTLE_MS
    ) {
      return;
    }

    gifState.pendingCapture = false;

    if (signature === gifState.lastCapturedSignature) return;

    captureGifFrame();

    gifState.lastCapturedSignature = signature;
  } catch (error) {
    console.error("Capture GIF :", error);

    gifState.recording = false;

    setGifStatus(
      `Capture interrompue : ${error?.message || error}. ` +
      "Les images déjà capturées restent exportables.",
      true
    );

    updateGifUI();
  }
}

function loadGifEncoder() {
  if (!gifEncoderPromise) {
    gifEncoderPromise = import(
      "https://cdn.jsdelivr.net/npm/gifenc@1.0.3/+esm"
    ).catch((error) => {
      gifEncoderPromise = null;
      throw error;
    });
  }

  return gifEncoderPromise;
}

async function stopAndExportGif() {
  if (
    gifState.encoding ||
    gifState.finalizing ||
    isLoading
  ) {
    return;
  }

  // Bloquer les nouvelles interactions pendant la capture
  // éventuelle de la dernière position.
  clearNavigation();

  gifState.finalizing = true;
  refreshUI();

  try {
    if (gifState.recording && !document.hidden) {
      requestGifCapture();

      await sleep(GIF_SETTLE_MS + 50);
      await nextRenderedFrame();
    }
  } finally {
    gifState.recording = false;
    gifState.pendingCapture = false;
    gifState.finalizing = false;
  }

  if (!gifState.frames.length) {
    setGifStatus(
      "Aucune image capturée. Démarrer la capture puis " +
      "laisser la caméra immobile pendant 600 ms."
    );

    refreshUI();
    return;
  }

  gifState.encoding = true;
  refreshUI();

  const frames = gifState.frames;
  const { width, height, delay } = gifState;

  try {
    setGifStatus("Chargement de l’encodeur GIF…");

    const { GIFEncoder, quantize, applyPalette } =
      await loadGifEncoder();

    const gif = GIFEncoder();

    for (let index = 0; index < frames.length; index++) {
      setGifStatus(
        `Encodage du GIF : ${index + 1} / ${frames.length}…`
      );

      await sleep(0);

      const palette = quantize(frames[index], 256);
      const pixels = applyPalette(frames[index], palette);

      gif.writeFrame(pixels, width, height, {
        palette,
        delay,
        repeat: 0,
      });
    }

    gif.finish();

    const blob = new Blob([gif.bytes()], {
      type: "image/gif",
    });

    clearGifDownload();

    gifState.downloadUrl = URL.createObjectURL(blob);

    const timestamp = new Date().toISOString()
      .replace(/[:.]/g, "-");

    const link = $("gifDownload");

    link.href = gifState.downloadUrl;
    link.download = `vue-3d-${timestamp}.gif`;
    link.textContent = "Télécharger à nouveau le GIF";
    link.hidden = false;
    link.click();

    gifState.frames = [];

    setGifStatus(
      `GIF prêt : ${frames.length} image(s) · ` +
      `${width} × ${height} px · ` +
      `${(blob.size / 1024 / 1024).toFixed(2)} Mo`
    );
  } catch (error) {
    console.error("Export GIF :", error);

    setGifStatus(
      `Export impossible : ${error?.message || error}. ` +
      "Les images sont conservées : tu peux réessayer.",
      true
    );
  } finally {
    gifState.encoding = false;
    refreshUI();
  }
}

function cancelGifRecording() {
  if (gifState.encoding || gifState.finalizing) return;

  const hadRecording =
    gifState.recording || gifState.frames.length > 0;

  gifState.recording = false;
  gifState.frames = [];
  gifState.pendingCapture = false;
  gifState.lastCapturedSignature = null;
  gifState.observedSignature = null;

  if (hadRecording) setGifStatus("Enregistrement annulé.");

  updateGifUI();
}

// ---------------------------------------------------------------------------
// Boutons maintenus
// ---------------------------------------------------------------------------

document.querySelectorAll("[data-hold]").forEach((button) => {
  function end(key) {
    if (!heldButtons.delete(key)) return;

    if (button.dataset.hold === "orbitPrincipal" && picking) {
      pickSerial++;
      picking = false;
    }

    if (![...heldButtons.values()].includes(button)) {
      button.classList.remove("held");
    }

    requestGifCapture();
  }

  async function begin(key) {
    if (!canNavigate() || heldButtons.has(key)) return;

    heldButtons.set(key, button);
    button.classList.add("held");

    if (button.dataset.hold === "orbitPrincipal") {
      const selected = await selectPointInView(key, button);

      if (!selected && heldButtons.get(key) === button) {
        end(key);
      }
    }
  }

  button.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !canNavigate()) return;

    event.preventDefault();

    button.focus({ preventScroll: true });
    button.setPointerCapture(event.pointerId);

    void begin(event.pointerId);
  });

  ["pointerup", "pointercancel", "lostpointercapture"]
    .forEach((type) => {
      button.addEventListener(type, (event) => {
        end(event.pointerId);
      });
    });

  button.addEventListener("keydown", (event) => {
    if (!["Enter", " "].includes(event.key)) return;

    event.preventDefault();

    if (!event.repeat) void begin(button);
  });

  button.addEventListener("keyup", (event) => {
    if (!["Enter", " "].includes(event.key)) return;

    event.preventDefault();
    end(button);
  });

  button.addEventListener("blur", () => end(button));
});

// ---------------------------------------------------------------------------
// Clavier et animation
// ---------------------------------------------------------------------------

const commonKeys = {
  arrowleft: "orbitLeft",
  arrowright: "orbitRight",
  arrowup: "orbitUp",
  arrowdown: "orbitDown",
  "+": "zoomIn",
  "=": "zoomIn",
  "-": "zoomOut",
};

function actionForKey(key) {
  const layout = $("keyboardLayout").value === "azerty"
    ? {
        z: "forward",
        s: "backward",
        q: "left",
        d: "right",
        a: "down",
        e: "up",
      }
    : {
        w: "forward",
        s: "backward",
        a: "left",
        d: "right",
        q: "down",
        e: "up",
      };

  return commonKeys[key] || layout[key];
}

function isEditing(element) {
  return element instanceof Element &&
    !!element.closest(
      "input, textarea, select, button, summary, [contenteditable]"
    );
}

window.addEventListener("keydown", (event) => {
  if (
    !canNavigate() ||
    isEditing(event.target) ||
    event.ctrlKey ||
    event.metaKey ||
    event.altKey
  ) {
    return;
  }

  const key = event.key.toLowerCase();

  if (key === "r") {
    event.preventDefault();

    if (!event.repeat) {
      clearNavigation();
      fitToView();
      requestGifCapture();
    }
  } else if (actionForKey(key) || key === "shift") {
    event.preventDefault();
    pressedKeys.add(key);
  }
});

window.addEventListener("keyup", (event) => {
  const key = event.key.toLowerCase();
  const moving = pressedKeys.has(key) && !!actionForKey(key);

  pressedKeys.delete(key);

  if (moving) requestGifCapture();
});

window.addEventListener("blur", clearNavigation);

document.addEventListener("visibilitychange", () => {
  if (document.hidden) clearNavigation();
});

$("panel").addEventListener("focusin", clearNavigation);

function navigationFrame(deltaTime) {
  if (!canNavigate() || document.hidden) return;

  const dt = Math.min(deltaTime, 0.05);
  const actions = new Set();

  heldButtons.forEach((button) => {
    actions.add(button.dataset.hold);
  });

  pressedKeys.forEach((key) => {
    const action = actionForKey(key);
    if (action) actions.add(action);
  });

  if (!actions.size) return;

  const axis = (positive, negative) =>
    Number(actions.has(positive)) -
    Number(actions.has(negative));

  const amount =
    dt *
    Number($("step").value) / 10 *
    (pressedKeys.has("shift") ? 3 : 1);

  orbit(
    axis("orbitLeft", "orbitRight") * amount,
    axis("orbitDown", "orbitUp") * amount
  );

  if (actions.has("orbitPrincipal")) {
    rotateAroundAxis(getPrincipalAxis(), amount);
  }

  translate(
    axis("right", "left"),
    axis("up", "down"),
    axis("forward", "backward"),
    sceneRadius * amount * 0.6
  );

  const zoom = axis("zoomOut", "zoomIn");

  if (zoom) zoomBy(Math.exp(zoom * amount));

  syncCamera();
  requestGifCapture();
}

// ---------------------------------------------------------------------------
// Interface
// ---------------------------------------------------------------------------

$("fit").addEventListener("click", () => {
  if (!canNavigate()) return;

  clearNavigation();
  fitToView();
  requestGifCapture();
});

$("recenterTarget").addEventListener("click", recenterTarget);
$("exportCam").addEventListener("click", exportCamera);
$("importCam").addEventListener("click", importCamera);

$("principalAxis").addEventListener("change", () => {
  clearNavigation();
  updatePrincipalAxisHelp();
});

document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", () => {
    setView(button.dataset.view);
  });
});

$("step").addEventListener("input", () => {
  $("stepValue").value = $("step").value;
});

$("keyboardLayout").addEventListener("change", () => {
  clearNavigation();
  updateKeyboardHelp();
});

$("togglePanel").addEventListener("click", () => {
  clearNavigation();

  const collapsed = $("panel").classList.toggle("collapsed");

  $("togglePanel").textContent = collapsed ? "+" : "−";
  $("togglePanel").setAttribute("aria-expanded", String(!collapsed));
  $("togglePanel").setAttribute(
    "aria-label",
    collapsed ? "Développer le panneau" : "Réduire le panneau"
  );
});

$("flipUp").addEventListener("change", () => {
  if (!canNavigate()) return;

  clearNavigation();

  // Aucun rechargement nécessaire avec PlayCanvas.
  applyUpFlip();
  computeSceneBounds();
  syncCamera();
  requestGifCapture();

  setStatus(
    `Axe Y ${$("flipUp").checked ? "vers le bas" : "vers le haut"} · ` +
    "vue conservée."
  );
});

$("fileInput").addEventListener("change", (event) => {
  void loadFile(event.target.files?.[0]);
});

$("gifStart").addEventListener("click", startGifRecording);
$("gifStop").addEventListener("click", stopAndExportGif);
$("gifCancel").addEventListener("click", cancelGifRecording);

// ---------------------------------------------------------------------------
// Glisser-déposer
// ---------------------------------------------------------------------------

let dragDepth = 0;

window.addEventListener("dragenter", (event) => {
  event.preventDefault();

  if (!Array.from(event.dataTransfer?.types ?? []).includes("Files")) {
    return;
  }

  dragDepth++;
  $("drop").style.display = "flex";
});

window.addEventListener("dragover", (event) => {
  event.preventDefault();
});

window.addEventListener("dragleave", (event) => {
  event.preventDefault();

  dragDepth = Math.max(0, dragDepth - 1);

  if (!dragDepth) $("drop").style.display = "none";
});

window.addEventListener("drop", (event) => {
  event.preventDefault();

  dragDepth = 0;
  $("drop").style.display = "none";

  void loadFile(event.dataTransfer?.files?.[0]);
});

// ---------------------------------------------------------------------------
// Initialisation
// ---------------------------------------------------------------------------

$("flipUp").checked = ["1", "true"].includes(params.get("flipUp"));

if (pendingCamera !== null) {
  $("cameraValue").value = pendingCamera;
}

updateKeyboardHelp();
updatePrincipalAxisHelp();
refreshUI();

installMouseControls();

let resizeObserver = null;

async function initialize() {
  isLoading = true;
  refreshUI();

  try {
    const graphicsDevice = await pc.createGraphicsDevice(canvas, {
      deviceTypes: ["webgl2"],
      antialias: false,
      alpha: false,

      // La capture se fait dans postrender, mais conserver le buffer
      // simplifie aussi le comportement selon les navigateurs.
      preserveDrawingBuffer: true,
    });

    app = new pc.Application(canvas, {
      graphicsDevice,
    });

    app.setCanvasFillMode(pc.FILLMODE_NONE);
    app.setCanvasResolution(pc.RESOLUTION_AUTO);

    app.graphicsDevice.maxPixelRatio = Math.min(
      window.devicePixelRatio || 1,
      2
    );

    // Les centres sont utilisés par le cadrage percentile.
    app.scene.gsplatCentersEnabled = true;

    cameraEntity = new pc.Entity("Camera");

    cameraEntity.addComponent("camera", {
      clearColor: new pc.Color(
        0x10 / 255,
        0x13 / 255,
        0x1b / 255
      ),
      fov: camera.fov,
      nearClip: camera.near,
      farClip: camera.far,
    });

    app.root.addChild(cameraEntity);

    picker = new pc.Picker(app, 1, 1, true);

    app.on("update", navigationFrame);
    app.on("postrender", processGifCapture);

    app.start();

    resizeObserver = new ResizeObserver(resizeViewer);
    resizeObserver.observe(container);

    resizeViewer();

    isLoading = false;
    refreshUI();

    const fileParam = params.get("file");

    if (fileParam) {
      await resetAndLoad(fileParam, filenameFromUrl(fileParam));
    }
  } catch (error) {
    console.error("Initialisation PlayCanvas :", error);

    isLoading = false;
    fatalError = true;

    setStatus(
      `Initialisation impossible : ${error?.message || error}`,
      true
    );

    refreshUI();
  }
}

window.addEventListener("pagehide", (event) => {
  if (event.persisted) return;

  clearNavigation();

  if (gifState.downloadUrl) {
    URL.revokeObjectURL(gifState.downloadUrl);
  }

  resizeObserver?.disconnect();
  picker?.destroy();

  if (app) {
    releaseScene();
    app.destroy();
    app = null;
  }
});

void initialize();
