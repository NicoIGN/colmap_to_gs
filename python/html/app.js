import * as THREE from "three";
import * as GS from "@mkkellogg/gaussian-splats-3d";

// ---------------------------------------------------------------------------
// État général
// ---------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const container = $("viewer");
const params = new URLSearchParams(location.search);

const pressedKeys = new Set();
const heldButtons = new Map();

const sceneBox = new THREE.Box3();
const sceneCenter = new THREE.Vector3();
const principalAxes = {
  x: new THREE.Vector3(1, 0, 0),
  y: new THREE.Vector3(0, 1, 0),
  z: new THREE.Vector3(0, 0, 1),
};

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
    `Axe ${axis} du monde, passant par le premier splat visé.`;

  $("orbitPrincipal").title =
    `Maintenir : rotation autour de l’axe ${axis} du monde ` +
    "passant par le premier splat visé au centre";
}


let viewer = null;
let viewerHost = null;
let currentUrl = null;
let mouseDrag = null;
let sceneReady = false;
let isLoading = false;
let sceneRadius = 1;

let pendingCamera = params.get("camera_pos");
let pendingUp = params.get("camera_up");

const sleep = (ms) =>
  new Promise((resolve) => setTimeout(resolve, ms));

const canNavigate = () =>
  viewer && sceneReady && !isLoading;

// ---------------------------------------------------------------------------
// État de la capture GIF
// ---------------------------------------------------------------------------

const GIF_MAX_FRAMES = 60;
const GIF_MAX_WIDTH = 640;
const GIF_MAX_HEIGHT = 480;

const gifState = {
  recording: false,
  encoding: false,
  frames: [],
  lastCapturedSignature: null,
  pendingCapture: false,
  width: 0,
  height: 0,
  delay: 100,
  downloadUrl: null,
};

let gifEncoderModulePromise = null;

// ---------------------------------------------------------------------------
// Interface et navigation
// ---------------------------------------------------------------------------

function setStatus(message, error = false) {
  $("status").textContent = message;
  $("status").classList.toggle("error", error);
}

function setUIEnabled(enabled) {
  document.querySelectorAll(
    "#panel button, #panel input, #panel select, #panel textarea"
  ).forEach((element) => {
    if (element.id !== "togglePanel") {
      element.disabled = !enabled;
    }
  });

  updateGifUI();
}

function clearNavigation() {
  const wasNavigating =
    heldButtons.size > 0 ||
    !!mouseDrag?.moved ||
    [...pressedKeys].some((key) => !!actionForKey(key));

  pressedKeys.clear();

  heldButtons.forEach((button) => {
    button.classList.remove("held");
  });

  heldButtons.clear();
  mouseDrag = null;

  if (wasNavigating) {
    requestGifCapture();
  }
}

// ---------------------------------------------------------------------------
// Cycle de vie
// ---------------------------------------------------------------------------

async function withTolerantRemoveChild(fn) {
  const original = Node.prototype.removeChild;

  Node.prototype.removeChild = function(child) {
    if (child && child.parentNode !== this) {
      return child;
    }

    return original.call(this, child);
  };

  try {
    return await fn();
  } finally {
    Node.prototype.removeChild = original;
  }
}

async function destroyViewer() {
  cancelGifRecording();

  clearNavigation();
  sceneReady = false;

  const oldViewer = viewer;
  const oldHost = viewerHost;

  viewer = null;
  viewerHost = null;

  if (oldViewer) {
    try {
      oldViewer.stop();
      await withTolerantRemoveChild(() => oldViewer.dispose());
    } catch (error) {
      console.warn("Nettoyage du viewer :", error);
    }
  }

  oldHost?.remove();
}

function createViewer() {
  viewerHost = document.createElement("div");
  viewerHost.className = "viewer-host";
  container.appendChild(viewerHost);

  viewer = new GS.Viewer({
    rootElement: viewerHost,
    cameraUp: [0, 1, 0],
    initialCameraPosition: [0, 0, 5],
    initialCameraLookAt: [0, 0, 0],
    useBuiltInControls: false,
    sharedMemoryForWorkers: false,
    dynamicScene: false,
  });

  const camera = viewer.camera;
  const target = new THREE.Vector3();

  viewer.controls = {
    target,
    update() {
      camera.lookAt(target);
      camera.updateMatrixWorld(true);
    },
  };

  installMouseControls(viewer);
}

function applyUpFlip() {
  const mesh = viewer?.getSplatMesh();
  if (!mesh) return;

  mesh.rotation.x = $("flipUp").checked ? Math.PI : 0;
  mesh.updateMatrixWorld(true);
}

function formatFromName(name) {
  if (/\.ksplat$/i.test(name)) return GS.SceneFormat.KSplat;
  if (/\.splat$/i.test(name)) return GS.SceneFormat.Splat;
  return GS.SceneFormat.Ply;
}

async function resetAndLoad(url, name) {
  if (isLoading) return;

  isLoading = true;
  setUIEnabled(false);
  setStatus(`Chargement de ${name}…`);

  try {
    await destroyViewer();
    createViewer();

    await viewer.addSplatScene(url, {
      format: formatFromName(name),
      showLoadingUI: true,
      progressiveLoad: false,
      splatAlphaRemovalThreshold: 5,
    });

    viewer.start();
    await sleep(50);

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

    sceneReady = true;

    const count = viewer.getSplatMesh().getSplatCount();
    const size = sceneBox.getSize(new THREE.Vector3());

    const info =
      `${name} — ${count.toLocaleString()} splats · ` +
      `${size.x.toFixed(1)} × ` +
      `${size.y.toFixed(1)} × ` +
      `${size.z.toFixed(1)}`;

    setStatus(
      cameraError ? `${info} · ${cameraError}` : info,
      !!cameraError
    );
  } catch (error) {
    console.error(error);

    await destroyViewer();

    setStatus(`Erreur : ${error?.message || error}`, true);
  } finally {
    isLoading = false;
    setUIEnabled(true);
    $("fileInput").value = "";
  }
}

async function loadFile(file) {
  if (!file || isLoading) return;

  if (!/\.(ply|splat|ksplat)$/i.test(file.name)) {
    setStatus("Format accepté : .ply, .splat ou .ksplat.", true);
    return;
  }

  const previousUrl = currentUrl;
  currentUrl = URL.createObjectURL(file);

  await resetAndLoad(currentUrl, file.name);

  if (previousUrl) {
    URL.revokeObjectURL(previousUrl);
  }
}

// ---------------------------------------------------------------------------
// Emprise et cadrage
// ---------------------------------------------------------------------------

function computeSceneBounds() {
  const mesh = viewer.getSplatMesh();
  const count = mesh.getSplatCount();

  const coordinates = [[], [], []];
  const point = new THREE.Vector3();
  const stride = Math.max(1, Math.ceil(count / 100000));

  for (let i = 0; i < count; i += stride) {
    mesh.getSplatCenter(i, point);

    if ($("flipUp").checked) {
      point.y = -point.y;
      point.z = -point.z;
    }

    if (point.toArray().every(Number.isFinite)) {
      coordinates[0].push(point.x);
      coordinates[1].push(point.y);
      coordinates[2].push(point.z);
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
      values[
        Math.min(
          values.length - 1,
          Math.floor(values.length * q)
        )
      ];

    sceneBox.set(
      new THREE.Vector3(
        ...sorted.map((values) => percentile(values, .01))
      ),
      new THREE.Vector3(
        ...sorted.map((values) => percentile(values, .99))
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

function updateClipping() {
  if (!viewer) return;

  const { camera, controls } = viewer;
  const distance = camera.position.distanceTo(controls.target);

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
  viewer.controls.update();
  updateClipping();
}

function distanceToFit(direction) {
  const camera = viewer.camera;

  const vFov = THREE.MathUtils.degToRad(camera.fov);
  const hFov = 2 * Math.atan(
    Math.tan(vFov / 2) * camera.aspect
  );

  const forward = direction.clone().normalize().negate();
  const up = camera.up.clone().normalize();

  if (Math.abs(forward.dot(up)) > .999) {
    up.set(0, 0, 1);

    if (Math.abs(forward.dot(up)) > .999) {
      up.set(1, 0, 0);
    }
  }

  const right = new THREE.Vector3()
    .crossVectors(forward, up)
    .normalize();

  const trueUp = new THREE.Vector3()
    .crossVectors(right, forward)
    .normalize();

  const half = sceneBox
    .getSize(new THREE.Vector3())
    .multiplyScalar(.5);

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
  if (!viewer) return;

  const { camera, controls } = viewer;

  const dir = direction
    ? direction.clone()
    : camera.position.clone().sub(controls.target);

  if (dir.lengthSq() < 1e-12) {
    dir.set(0, .6, 1);
  }

  dir.normalize();

  camera.up.addScaledVector(dir, -camera.up.dot(dir));

  if (camera.up.lengthSq() < 1e-12) {
    camera.up.set(0, 0, 1);

    if (Math.abs(camera.up.dot(dir)) > .999) {
      camera.up.set(1, 0, 0);
    }

    camera.up.addScaledVector(dir, -camera.up.dot(dir));
  }

  camera.up.normalize();

  controls.target.copy(sceneCenter);

  camera.position
    .copy(sceneCenter)
    .addScaledVector(dir, distanceToFit(dir));

  syncCamera();
}

function setView(kind) {
  if (!canNavigate()) return;

  clearNavigation();

  const { camera, controls } = viewer;

  const axes = {
    front: new THREE.Vector3(0, 1, 0),
    top: new THREE.Vector3(0, 0, 1),
    side: new THREE.Vector3(1, 0, 0),
  };

  const up = axes[kind]?.clone();
  if (!up) return;

  const forward = controls.target
    .clone()
    .sub(camera.position)
    .normalize();

  up.addScaledVector(forward, -up.dot(forward));

  if (up.lengthSq() < 1e-12) {
    setStatus(
      "Cet axe pointe dans la direction de visée : impossible " +
      "de le rendre vertical sans changer la visée.",
      true
    );
    return;
  }

  camera.up.copy(up.normalize());
  syncCamera();
  requestGifCapture();

  const axis = kind === "front"
    ? "Y"
    : kind === "top"
      ? "Z"
      : "X";

  setStatus(`${axis} vertical · vue conservée.`);
}

function recenterTarget() {
  if (!canNavigate()) return;

  clearNavigation();

  const { camera, controls } = viewer;

  const offset = camera.position.clone().sub(controls.target);

  controls.target.copy(sceneCenter);
  camera.position.copy(sceneCenter).add(offset);

  syncCamera();
  requestGifCapture();
}

// ---------------------------------------------------------------------------
// Rotation libre et déplacements
// ---------------------------------------------------------------------------

function rotateAroundAxis(axis, angle) {
  if (!angle) return;

  const { camera, controls } = viewer;

  const rotation = new THREE.Quaternion()
    .setFromAxisAngle(axis.clone().normalize(), angle);

  const offset = camera.position
    .clone()
    .sub(controls.target)
    .applyQuaternion(rotation);

  camera.position.copy(controls.target).add(offset);
  camera.up.applyQuaternion(rotation).normalize();

  controls.update();
}

function orbit(dx, dy) {
  const camera = viewer.camera;

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
  const { camera, controls } = viewer;

  // Sensibilité de base : trois fois le déplacement précédent.
  const PAN_GAIN = 3;

  // Le curseur « Vitesse » agit désormais aussi sur le clic droit.
  // Valeur 10 = sensibilité normale.
  const speed = Number($("step").value) / 10;

  // Éviter une translation presque nulle si la cible est trop proche.
  const referenceDistance = Math.max(
    camera.position.distanceTo(controls.target),
    sceneRadius * 0.1
  );

  const unitsPerPixel =
    2 *
    referenceDistance *
    Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) /
    Math.max(height, 1);

  const scale = unitsPerPixel * PAN_GAIN * speed;

  const right = new THREE.Vector3(1, 0, 0)
    .applyQuaternion(camera.quaternion);

  const up = new THREE.Vector3(0, 1, 0)
    .applyQuaternion(camera.quaternion);

  const delta = right
    .multiplyScalar(-dx * scale)
    .addScaledVector(up, dy * scale);

  // Déplacer ensemble la caméra et sa cible conserve la visée.
  camera.position.add(delta);
  controls.target.add(delta);
}

function translate(x, y, z, distance) {
  if (!x && !y && !z) return;

  const { camera, controls } = viewer;

  const delta = new THREE.Vector3(x, y, -z)
    .normalize()
    .applyQuaternion(camera.quaternion)
    .multiplyScalar(distance);

  camera.position.add(delta);
  controls.target.add(delta);
}

function zoomBy(factor) {
  const { camera, controls } = viewer;

  const offset = camera.position.clone().sub(controls.target);

  const distance = THREE.MathUtils.clamp(
    offset.length() * factor,
    sceneRadius * 1e-4,
    sceneRadius * 1e5
  );

  camera.position
    .copy(controls.target)
    .add(offset.setLength(distance));
}

function selectFirstPointInView() {
  const mesh = viewer.getSplatMesh();

  if (!mesh?.getSplatTree()) {
    setStatus(
      "Sélection indisponible : la structure des splats " +
      "n’est pas prête.",
      true
    );
    return false;
  }

  const dimensions = new THREE.Vector2();
  viewer.getRenderDimensions(dimensions);

  if (!dimensions.x || !dimensions.y) {
    return false;
  }

  viewer.camera.updateMatrixWorld(true);
  mesh.updateMatrixWorld(true);

  const hits = [];

  viewer.raycaster.setFromCameraAndScreenPosition(
    viewer.camera,
    dimensions.clone().multiplyScalar(.5),
    dimensions
  );

  viewer.raycaster.intersectSplatMesh(mesh, hits);

  if (!hits.length) {
    setStatus(
      "Aucun splat visé au centre de l’image.",
      true
    );
    return false;
  }

  viewer.controls.target.copy(hits[0].origin);
  syncCamera();

    setStatus(
      "Rotation autour du premier splat visé · " +
      `axe ${getPrincipalAxisName().toUpperCase()}.`
    );
    
  return true;
}

// ---------------------------------------------------------------------------
// Souris
// ---------------------------------------------------------------------------

function installMouseControls(instance) {
  const canvas = instance.renderer.domElement;
  canvas.style.touchAction = "none";

  canvas.addEventListener("contextmenu", (event) => {
    event.preventDefault();
  });

  canvas.addEventListener("pointerdown", (event) => {
    if (!canNavigate() || mouseDrag || event.button > 2) {
      return;
    }

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
    if (!mouseDrag || mouseDrag.id !== event.pointerId) {
      return;
    }

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
      orbit(-dx * .005, -dy * .005);
    } else {
      panPixels(dx, dy, canvas.clientHeight);
    }

    syncCamera();
  });

  const release = (event) => {
    if (mouseDrag?.id !== event.pointerId) return;
    const moved = mouseDrag.moved;
    mouseDrag = null;
    if (moved) requestGifCapture();
  };

  [
    "pointerup",
    "pointercancel",
    "lostpointercapture",
  ].forEach((type) => {
    canvas.addEventListener(type, release);
  });

  let wheelTimer = null;

  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    if (!canNavigate()) return;

    const pixels = event.deltaMode === 1
      ? event.deltaY * 16
      : event.deltaMode === 2
        ? event.deltaY * canvas.clientHeight
        : event.deltaY;

    zoomBy(
      Math.exp(
        THREE.MathUtils.clamp(pixels * .001, -1, 1)
      )
    );

    syncCamera();

    clearTimeout(wheelTimer);
    wheelTimer = setTimeout(requestGifCapture, 250);
  }, { passive: false });
}

// ---------------------------------------------------------------------------
// Caméra : export / import
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
  return values
    .map((value) => Number(value.toPrecision(12)))
    .join(",");
}

function getCameraString() {
  const { camera, controls } = viewer;

  const up = new THREE.Vector3(0, 1, 0)
    .applyQuaternion(camera.quaternion)
    .normalize();

  return serialize([
    ...camera.position.toArray(),
    ...controls.target.toArray(),
    ...up.toArray(),
  ]);
}

function applyCameraString(value, upValue = null) {
  const count = value.split(",").length;

  if (count !== 6 && count !== 9) {
    throw new Error(
      "Caméra invalide : fournir 6 ou 9 nombres."
    );
  }

  const numbers = parseNumbers(value, count);

  const position = new THREE.Vector3(
    ...numbers.slice(0, 3)
  );

  const target = new THREE.Vector3(
    ...numbers.slice(3, 6)
  );

  const up = count === 9
    ? new THREE.Vector3(...numbers.slice(6, 9))
    : upValue
      ? new THREE.Vector3(...parseNumbers(upValue, 3))
      : new THREE.Vector3(0, 1, 0);

  const direction = target.clone().sub(position);

  if (direction.lengthSq() < 1e-12) {
    throw new Error(
      "La position et la cible doivent être différentes."
    );
  }

  if (up.lengthSq() < 1e-12) {
    throw new Error(
      "Le vecteur vertical ne doit pas être nul."
    );
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

    if (Math.abs(up.dot(direction)) > .999) {
      up.set(1, 0, 0);
    }

    up.addScaledVector(direction, -up.dot(direction));
  }

  viewer.camera.position.copy(position);
  viewer.camera.up.copy(up.normalize());
  viewer.controls.target.copy(target);

  syncCamera();
  $("cameraValue").value = getCameraString();
}

function rememberCamera() {
  const url = new URL(location.href);

  url.searchParams.set("camera_pos", getCameraString());
  url.searchParams.delete("camera_up");

  url.searchParams.set(
    "flipUp",
    $("flipUp").checked ? "1" : "0"
  );

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

    setStatus(
      "Caméra copiée : --camera-pos=\"" + value + "\""
    );
  } catch {
    $("cameraValue").focus();
    $("cameraValue").select();

    setStatus(
      "Copier le texte sélectionné avec Ctrl/Cmd+C."
    );
  }
}

function importCamera() {
  if (!canNavigate()) return;

  clearNavigation();

  try {
    applyCameraString($("cameraValue").value);
    rememberCamera();

    setStatus("Caméra restaurée.");
    requestGifCapture();
  } catch (error) {
    setStatus(error.message, true);
  }
}

// ---------------------------------------------------------------------------
// Capture GIF : une image par position de caméra
// ---------------------------------------------------------------------------

function setGifStatus(message, error = false) {
  $("gifStatus").textContent = message;
  $("gifStatus").classList.toggle("error", error);
  $("gifStatus").classList.toggle(
    "recording",
    gifState.recording && !error
  );
}

function updateGifUI() {
  const ready = !!canNavigate();

  $("gifStart").disabled =
    !ready ||
    gifState.recording ||
    gifState.encoding;

  $("gifStop").disabled =
    isLoading ||
    gifState.encoding ||
    (!gifState.recording && gifState.frames.length === 0);

  $("gifCancel").disabled =
    isLoading ||
    gifState.encoding ||
    (!gifState.recording && gifState.frames.length === 0);

  $("gifDelay").disabled =
    isLoading ||
    gifState.recording ||
    gifState.encoding;
}

function clearGifDownload() {
  if (gifState.downloadUrl) {
    URL.revokeObjectURL(gifState.downloadUrl);
    gifState.downloadUrl = null;
  }

  $("gifDownload").hidden = true;
  $("gifDownload").removeAttribute("href");
}

function navigationIsActive() {
  return mouseDrag !== null ||
    heldButtons.size > 0 ||
    [...pressedKeys].some((key) => !!actionForKey(key));
}

function cameraCaptureSignature() {
  const { camera, controls, renderer } = viewer;
  const canvas = renderer.domElement;

  return [
    ...camera.position.toArray(),
    ...camera.quaternion.toArray(),
    ...controls.target.toArray(),
    canvas.width,
    canvas.height,
    Number($("flipUp").checked),
  ]
    .map((value) => Number(value).toPrecision(8))
    .join(",");
}

function requestGifCapture() {
  if (gifState.recording) {
    gifState.pendingCapture = true;
  }
}

function processGifCapture() {
  if (
    !gifState.pendingCapture ||
    !gifState.recording ||
    !canNavigate() ||
    navigationIsActive()
  ) {
    return;
  }

  gifState.pendingCapture = false;

  try {
    const signature = cameraCaptureSignature();

    if (signature === gifState.lastCapturedSignature) {
      return;
    }

    captureGifFrame();
    gifState.lastCapturedSignature = signature;
  } catch (error) {
    console.error("Capture GIF :", error);

    gifState.recording = false;

    setGifStatus(
      `Capture interrompue : ${error?.message || error}. ` +
      (
        gifState.frames.length
          ? "Les images déjà capturées restent exportables."
          : ""
      ),
      true
    );

    updateGifUI();
  }
}

function startGifRecording() {
  if (
    !canNavigate() ||
    gifState.encoding ||
    gifState.recording
  ) {
    return;
  }

  clearNavigation();

  const canvas = viewer.renderer.domElement;

  if (!canvas.width || !canvas.height) {
    setGifStatus(
      "La vue 3D n’est pas encore prête.",
      true
    );
    return;
  }

  const scale = Math.min(
    1,
    GIF_MAX_WIDTH / canvas.width,
    GIF_MAX_HEIGHT / canvas.height
  );

  clearGifDownload();

  gifState.width = Math.max(
    1,
    Math.round(canvas.width * scale)
  );

  gifState.height = Math.max(
    1,
    Math.round(canvas.height * scale)
  );

  gifState.delay = Number($("gifDelay").value);
  gifState.frames = [];
  gifState.lastCapturedSignature = null;
  gifState.pendingCapture = false;
  gifState.recording = true;

  setGifStatus("● Capture active.");
  updateGifUI();

  requestGifCapture();
}

function captureGifFrame() {
  const source = viewer.renderer.domElement;

  if (!source.width || !source.height) {
    throw new Error("Le canvas 3D a une taille nulle.");
  }

  const canvas = document.createElement("canvas");
  canvas.width = gifState.width;
  canvas.height = gifState.height;

  const context = canvas.getContext("2d", {
    willReadFrequently: true,
  });

  if (!context) {
    throw new Error(
      "Impossible de créer le canvas de capture."
    );
  }

  // Redessiner la scène puis copier immédiatement le canvas WebGL.
  // Seule la vue 3D est capturée, pas le panneau HTML.
  viewer.render();

  context.fillStyle = "#10131b";
  context.fillRect(0, 0, canvas.width, canvas.height);

  const scale = Math.min(
    canvas.width / source.width,
    canvas.height / source.height
  );

  const width = source.width * scale;
  const height = source.height * scale;

  context.drawImage(
    source,
    (canvas.width - width) / 2,
    (canvas.height - height) / 2,
    width,
    height
  );

  gifState.frames.push(
    context.getImageData(
      0,
      0,
      canvas.width,
      canvas.height
    ).data
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

function loadGifEncoder() {
  if (!gifEncoderModulePromise) {
    gifEncoderModulePromise = import(
      "https://cdn.jsdelivr.net/npm/gifenc@1.0.3/+esm"
    ).catch((error) => {
      gifEncoderModulePromise = null;
      throw error;
    });
  }

  return gifEncoderModulePromise;
}

async function stopAndExportGif() {
  if (gifState.encoding) return;

  gifState.recording = false;
  gifState.pendingCapture = false;

  if (!gifState.frames.length) {
    setGifStatus(
      "Aucune image capturée. Démarrer la capture, puis effectuer des changements de caméra."
    );

    updateGifUI();
    return;
  }

  gifState.encoding = true;
  updateGifUI();

  const frames = gifState.frames;
  const width = gifState.width;
  const height = gifState.height;
  const delay = gifState.delay;

  try {
    setGifStatus("Chargement de l’encodeur GIF…");

    const {
      GIFEncoder,
      quantize,
      applyPalette,
    } = await loadGifEncoder();

    const gif = GIFEncoder();

    for (let index = 0; index < frames.length; index++) {
      setGifStatus(
        `Encodage du GIF : ${index + 1} / ${frames.length}…`
      );

      await sleep(0);

      const rgba = frames[index];
      const palette = quantize(rgba, 256);
      const indexedPixels = applyPalette(rgba, palette);

      gif.writeFrame(indexedPixels, width, height, {
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

    const timestamp = new Date()
      .toISOString()
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
    updateGifUI();
  }
}

function cancelGifRecording() {
  if (gifState.encoding) return;

  const hadRecording =
    gifState.recording || gifState.frames.length > 0;

  gifState.recording = false;
  gifState.frames = [];
  gifState.lastCapturedSignature = null;
  gifState.pendingCapture = false;

  if (hadRecording) {
    setGifStatus("Enregistrement annulé.");
  }

  updateGifUI();
}

// ---------------------------------------------------------------------------
// Boutons maintenus
// ---------------------------------------------------------------------------

document.querySelectorAll("[data-hold]").forEach((button) => {
  function begin(key) {
    if (!canNavigate() || heldButtons.has(key)) {
      return false;
    }

      if (
        button.dataset.hold === "orbitPrincipal" &&
        !selectFirstPointInView()
      ) {
        return false;
      }

    heldButtons.set(key, button);
    button.classList.add("held");

    return true;
  }

  function end(key) {
    if (!heldButtons.delete(key)) return;

    if (![...heldButtons.values()].includes(button)) {
      button.classList.remove("held");
    }

    requestGifCapture();
  }

  button.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !canNavigate()) {
      return;
    }

    event.preventDefault();
    button.focus({ preventScroll: true });
    button.setPointerCapture(event.pointerId);

    begin(event.pointerId);
  });

  [
    "pointerup",
    "pointercancel",
    "lostpointercapture",
  ].forEach((type) => {
    button.addEventListener(type, (event) => {
      end(event.pointerId);
    });
  });

  button.addEventListener("keydown", (event) => {
    if (!["Enter", " "].includes(event.key)) {
      return;
    }

    event.preventDefault();

    if (!event.repeat) {
      begin(button);
    }
  });

  button.addEventListener("keyup", (event) => {
    if (["Enter", " "].includes(event.key)) {
      event.preventDefault();
      end(button);
    }
  });

  button.addEventListener("blur", () => {
    end(button);
  });
});

// ---------------------------------------------------------------------------
// Clavier et animation continue
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

function isEditing(target) {
  return target instanceof Element &&
    !!target.closest(
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
  const wasMoving = pressedKeys.has(key) && !!actionForKey(key);

  pressedKeys.delete(key);

  if (wasMoving) {
    requestGifCapture();
  }
});

window.addEventListener("blur", clearNavigation);

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    clearNavigation();
  }
});

$("panel").addEventListener("focusin", clearNavigation);

let lastFrame = null;

function navigationFrame(time) {
  const dt = lastFrame === null
    ? 0
    : Math.min((time - lastFrame) / 1000, .05);

  lastFrame = time;

  if (canNavigate() && !document.hidden) {
    const actions = new Set();

    heldButtons.forEach((button) => {
      actions.add(button.dataset.hold);
    });

    pressedKeys.forEach((key) => {
      const action = actionForKey(key);

      if (action) {
        actions.add(action);
      }
    });

    if (actions.size) {
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
        sceneRadius * amount * .6
      );

      const zoom = axis("zoomOut", "zoomIn");

      if (zoom) {
        zoomBy(Math.exp(zoom * amount));
      }

      syncCamera();
    }
  }

  processGifCapture();
  requestAnimationFrame(navigationFrame);
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

function updateKeyboardHelp() {
  $("keyboardHelp").textContent =
    $("keyboardLayout").value === "azerty"
      ? "ZQSD : déplacer · A/E : descendre / monter"
      : "WASD : déplacer · Q/E : descendre / monter";
}

$("keyboardLayout").addEventListener("change", () => {
  clearNavigation();
  updateKeyboardHelp();
  updatePrincipalAxisHelp();
});

$("togglePanel").addEventListener("click", () => {
  clearNavigation();

  const collapsed = $("panel").classList.toggle("collapsed");

  $("togglePanel").textContent = collapsed ? "+" : "−";

  $("togglePanel").setAttribute(
    "aria-expanded",
    String(!collapsed)
  );

  $("togglePanel").setAttribute(
    "aria-label",
    collapsed
      ? "Développer le panneau"
      : "Réduire le panneau"
  );
});

$("flipUp").addEventListener("change", async () => {
  if (!canNavigate()) return;

  clearNavigation();

  const cameraState = getCameraString();
  const fileParam = params.get("file");

  if (!fileParam) {
    applyUpFlip();
    computeSceneBounds();
    syncCamera();

    setStatus(
      `Axe Y ${
        $("flipUp").checked
          ? "vers le bas"
          : "vers le haut"
      } · vue conservée.`
    );

    requestGifCapture();
    return;
  }

  pendingCamera = cameraState;
  pendingUp = null;

  let name = fileParam.split("/").pop();

  try {
    name = decodeURIComponent(name);
  } catch {
    // Conserver le nom brut si l'encodage est invalide.
  }

  await resetAndLoad(fileParam, name);

  if (sceneReady) {
    setStatus(
      `Axe Y ${
        $("flipUp").checked
          ? "vers le bas"
          : "vers le haut"
      } · vue rechargée.`
    );
  }
});

$("fileInput").addEventListener("change", (event) => {
  loadFile(event.target.files?.[0]);
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

  if (
    !Array.from(
      event.dataTransfer?.types ?? []
    ).includes("Files")
  ) {
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

  if (!dragDepth) {
    $("drop").style.display = "none";
  }
});

window.addEventListener("drop", (event) => {
  event.preventDefault();

  dragDepth = 0;
  $("drop").style.display = "none";

  loadFile(event.dataTransfer?.files?.[0]);
});

// ---------------------------------------------------------------------------
// Nettoyage des téléchargements
// ---------------------------------------------------------------------------

window.addEventListener("pagehide", (event) => {
  if (event.persisted) return;

  if (gifState.downloadUrl) {
    URL.revokeObjectURL(gifState.downloadUrl);
  }

  if (currentUrl) {
    URL.revokeObjectURL(currentUrl);
  }
});

// ---------------------------------------------------------------------------
// Initialisation
// ---------------------------------------------------------------------------

$("flipUp").checked = ["1", "true"].includes(
  params.get("flipUp")
);

if (pendingCamera !== null) {
  $("cameraValue").value = pendingCamera;
}

updateKeyboardHelp();
updateGifUI();

requestAnimationFrame(navigationFrame);

const fileParam = params.get("file");

if (fileParam) {
  let name = fileParam.split("/").pop();

  try {
    name = decodeURIComponent(
      new URL(fileParam, location.href)
        .pathname
        .split("/")
        .pop()
    );
  } catch {
    // Conserver le nom brut.
  }

  resetAndLoad(fileParam, name);
}
