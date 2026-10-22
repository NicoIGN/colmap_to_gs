import * as pc from "playcanvas";
import * as THREE from "three";

import {
  createSceneImporter,
  filenameFromUrl,
  isSupportedSceneName,
} from "./viewer/import.js";

import { createLoadProgress } from "./viewer/progress.js";
import { createMemoryWatchdog } from "./viewer/memory.js";
import { createCollisionSystem } from "./viewer/collision.js";
import { createGifRecorder } from "./viewer/gif.js";
import { createNavigationSystem } from "./viewer/navigation.js";
import { createMeasurementSystem } from "./viewer/measurement.js";
import { readGeorefFile } from "./viewer/georef.js";

const $ = (id) => document.getElementById(id);
const container = $("viewer");
const params = new URLSearchParams(location.search);

const sleep = (ms) =>
  new Promise((resolve) => setTimeout(resolve, ms));

const reportedMemory = Number(navigator.deviceMemory) || null;

// Garder les mêmes seuils dans collision.js.
const MAX_COLLISION_SPLATS =
  reportedMemory !== null && reportedMemory <= 4
    ? 500_000
    : 1_000_000;

const COLLISION_CONTROL_IDS = [
  "collisionEnabled",
  "collisionDensity",
  "collisionMargin",
];

const sceneBox = new THREE.Box3();
const sceneCenter = new THREE.Vector3();

const principalAxes = {
  x: new THREE.Vector3(1, 0, 0),
  y: new THREE.Vector3(0, 1, 0),
  z: new THREE.Vector3(0, 0, 1),
};

const camera = new THREE.PerspectiveCamera(50, 1, 0.01, 1000);
camera.position.set(0, 0, 5);
camera.up.set(0, 1, 0);

const target = new THREE.Vector3();

let app = null;
let cameraEntity = null;
let splatEntity = null;
let picker = null;
let importer = null;
let centers = null;

let loadedFileSource = null;
let loadedFileName = null;
let loadedFileIsLocal = false;

let sceneReady = false;
let isLoading = false;
let fatalError = false;
let disposed = false;

let sceneRadius = 1;
let sceneCount = 0;
let collisionBlockedReason = "";

let pendingCamera = params.get("camera_pos");
let pendingUp = params.get("camera_up");

let pickSerial = 0;
let picking = false;

let navigation = null;
let collision = null;
let gifRecorder = null;
let measurement = null;
let memoryWatchdog = null;
let sceneGeoref = null;
let georefSerial = 0;

const canvas = document.createElement("canvas");
canvas.setAttribute("aria-label", "Vue 3D des Gaussian Splats");
container.appendChild(canvas);

const progress = createLoadProgress(container);

const canNavigate = () =>
  !!app &&
  !disposed &&
  sceneReady &&
  !isLoading &&
  !fatalError &&
  !picking &&
  !gifRecorder?.state.encoding &&
  !gifRecorder?.state.finalizing &&
  !measurement?.isActive();

function setStatus(message, error = false) {
  if (disposed) return;

  $("status").textContent = message;
  $("status").classList.toggle("error", error);
}

function loadingStep(message, fraction = null, detail = "") {
  if (disposed) return;

  setStatus(message);
  progress.update(message, fraction, detail);
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

  $("orbitPrincipalNegative").title =
    `Maintenir : rotation négative autour de l’axe ${axis}`;

  $("orbitPrincipalPositive").title =
    `Maintenir : rotation positive autour de l’axe ${axis}`;
}

function updateKeyboardHelp() {
  $("keyboardHelp").textContent =
    $("keyboardLayout").value === "azerty"
      ? "ZQSD : déplacer · A/E : descendre / monter"
      : "WASD : déplacer · Q/E : descendre / monter";
}

function refreshUI() {
  if (disposed) return;

  const blocked = Boolean(
    isLoading ||
    fatalError ||
    gifRecorder?.state.encoding ||
    gifRecorder?.state.finalizing
  );

  document.querySelectorAll(
    "#panel button, #panel input, #panel select, #panel textarea"
  ).forEach((element) => {
    if (element.id !== "togglePanel") {
      element.disabled = blocked;
    }
  });

  for (const id of COLLISION_CONTROL_IDS) {
    const element = $(id);

    element.disabled = blocked || !!collisionBlockedReason;

    if (collisionBlockedReason) {
      element.title = collisionBlockedReason;
    } else {
      element.removeAttribute("title");
    }
  }

  gifRecorder?.updateUI(sceneReady);
}

function disableCollisionForMemory(reason) {
  collisionBlockedReason = reason;

  $("collisionEnabled").checked = false;

  // Avec le dernier collision.js fourni :
  // - annule la construction en cours ;
  // - vide les Map publiées et en construction ;
  // - retire les références à la grille.
  //
  // Cela rend les données récupérables par le GC,
  // sans garantir une baisse immédiate de la mémoire du processus.
  collision?.reset();
  collision?.setCollisionStatus(reason);

  navigation?.clearNavigation();

  refreshUI();

  // Ne pas écraser les messages d'étape pendant un import.
  if (!isLoading) {
    setStatus(reason);
  }

  console.warn("[Memory watchdog]", reason);
}

function collisionIsEnabled() {
  return !collisionBlockedReason && $("collisionEnabled").checked;
}

function enforceCollisionBudget() {
  collisionBlockedReason = "";

  if (sceneCount > MAX_COLLISION_SPLATS) {
    collisionBlockedReason =
      "Anticollision désactivée pour limiter la mémoire : " +
      `${sceneCount.toLocaleString()} splats, seuil de ` +
      `${MAX_COLLISION_SPLATS.toLocaleString()}.`;

    $("collisionEnabled").checked = false;

    collision.reset();
    collision.setCollisionStatus(collisionBlockedReason);
  }
}

async function prepareCollision() {
  memoryWatchdog?.check("avant construction anticollision");

  if (!collisionIsEnabled()) return;

  loadingStep("Préparation de l’anticollision…");
  await sleep(0);

  if (disposed || !collisionIsEnabled()) return;

  try {
    await collision.buildGrid({
      onProgress(fraction, message) {
        memoryWatchdog?.check("construction anticollision");

        if (!disposed && !memoryWatchdog?.collisionBlocked) {
          progress.update(message, fraction);
        }
      },
    });

    if (!disposed && collisionIsEnabled()) {
      collision.checkCurrentCollisionPosition(true);
    }
  } catch (error) {
    if (disposed) return;

    $("collisionEnabled").checked = false;
    collision.reset();

    // Préserver l'explication du watchdog plutôt que d'afficher
    // une erreur générique d'annulation.
    if (memoryWatchdog?.collisionBlocked) {
      collisionBlockedReason = memoryWatchdog.reason;
      collision.setCollisionStatus(collisionBlockedReason);
    } else {
      collision.setCollisionStatus(
        `Anticollision indisponible : ${error?.message || error}`,
        true
      );
    }
  } finally {
    refreshUI();
  }
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

  const q = camera.quaternion;
  cameraEntity.setRotation(q.x, q.y, q.z, q.w);

  cameraEntity.camera.fov = camera.fov;
  cameraEntity.camera.horizontalFov = false;
  cameraEntity.camera.nearClip = camera.near;
  cameraEntity.camera.farClip = camera.far;
}

function resizeViewer() {
  if (!app || disposed) return;

  const width = Math.max(1, container.clientWidth);
  const height = Math.max(1, container.clientHeight);

  app.resizeCanvas(width, height);

  camera.aspect = width / height;
  camera.updateProjectionMatrix();

  syncCamera();
  requestGifCapture();
}

function nextRenderedFrame() {
  if (!app || disposed) {
    return Promise.reject(
      new DOMException("Viewer fermé.", "AbortError")
    );
  }

  const currentApp = app;

  return new Promise((resolve, reject) => {
    function cleanup() {
      currentApp.off("postrender", onFrame);
      window.removeEventListener("pagehide", onHide);
    }

    function onFrame() {
      cleanup();
      resolve();
    }

    function onHide(event) {
      if (event.persisted) return;

      cleanup();
      reject(new DOMException("Viewer fermé.", "AbortError"));
    }

    currentApp.once("postrender", onFrame);
    window.addEventListener("pagehide", onHide);
  });
}

function cameraCaptureSignature() {
  return [
    ...camera.position.toArray(),
    ...camera.quaternion.toArray(),
    ...target.toArray(),
    canvas.width,
    canvas.height,
    Number($("flipUp").checked),
  ].map((value) => Number(value).toPrecision(8)).join(",");
}

function requestGifCapture() {
  if (!disposed) {
    gifRecorder?.requestCapture(cameraCaptureSignature());
  }
}

function applyUpFlip() {
  splatEntity?.setLocalEulerAngles(
    $("flipUp").checked ? 180 : 0,
    0,
    0
  );
}

function releaseScene() {
  sceneReady = false;
    
  georefSerial++;
  sceneGeoref = null;
    
  pickSerial++;
  picking = false;
    
  measurement?.reset();
  collision?.reset();

  splatEntity?.destroy();
  splatEntity = null;

  centers = null;
  sceneCount = 0;
  sceneRadius = 1;

  sceneBox.makeEmpty();
  sceneCenter.set(0, 0, 0);

  collisionBlockedReason = "";

  importer?.release();
}

function canStartLoad() {
  return (
    !!app &&
    !disposed &&
    !isLoading &&
    !fatalError &&
    !gifRecorder?.state.encoding &&
    !gifRecorder?.state.finalizing
  );
}

function computeSceneBounds() {
  const count = centers.length / 3;
  const stride = Math.max(1, Math.ceil(count / 100_000));
  const capacity = Math.ceil(count / stride);

  const coordinates = [
    new Float32Array(capacity),
    new Float32Array(capacity),
    new Float32Array(capacity),
  ];

  const flip = $("flipUp").checked ? -1 : 1;
  let valid = 0;

  for (let i = 0; i < count; i += stride) {
    const x = centers[i * 3];
    const y = centers[i * 3 + 1] * flip;
    const z = centers[i * 3 + 2] * flip;

    if (
      Number.isFinite(x) &&
      Number.isFinite(y) &&
      Number.isFinite(z)
    ) {
      coordinates[0][valid] = x;
      coordinates[1][valid] = y;
      coordinates[2][valid] = z;
      valid++;
    }
  }

  if (!valid) {
    sceneBox.set(
      new THREE.Vector3(-1, -1, -1),
      new THREE.Vector3(1, 1, 1)
    );
  } else {
    const sorted = coordinates.map((values) =>
      values.subarray(0, valid).sort()
    );

    const percentile = (values, q) =>
      values[Math.min(valid - 1, Math.floor(valid * q))];

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


async function loadSceneInternal(source, name) {
  if (!canStartLoad()) return false;

  if (!isSupportedSceneName(name)) {
    setStatus(
      "Formats acceptés : .sog, .ply, .splat ou .ksplat.",
      true
    );
    return false;
  }

  isLoading = true;
  navigation.clearNavigation();
  gifRecorder.cancel(sceneReady);

  const startedAt = performance.now();

  try {
    releaseScene();
    refreshUI();

    loadingStep(`Préparation de ${name}…`);

    // Laisser le navigateur afficher la barre avant les allocations.
    await sleep(0);

    const asset = await importer.load(source, name);

    if (disposed) return false;

    const resource = asset.resource;

    if (!resource) {
      throw new Error("PlayCanvas n’a pas créé la ressource GSplat.");
    }

    centers = resource.centers;

    if (!centers?.length || centers.length % 3 !== 0) {
      throw new Error(
        "Les centres des splats ne sont pas disponibles pour le cadrage."
      );
    }

    sceneCount = Math.max(
      centers.length / 3,
      Number(resource.numSplats) || 0
    );

    enforceCollisionBudget();
    refreshUI();

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

    loadingStep("Calcul du cadrage…");
    await sleep(0);

    if (disposed) return false;

    computeSceneBounds();
    navigation.fitToView(sceneBox);

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

    syncCamera();

    loadingStep("Premier rendu…");
    await nextRenderedFrame();

    if (disposed) return false;
  
    memoryWatchdog?.check("après premier rendu");
    await prepareCollision();

    if (disposed) return false;

    sceneReady = true;

    const size = sceneBox.getSize(new THREE.Vector3());
    const elapsed = ((performance.now() - startedAt) / 1000).toFixed(1);

    let info =
      `${name} — ${sceneCount.toLocaleString()} splats · ` +
      `${size.x.toFixed(1)} × ${size.y.toFixed(1)} × ` +
      `${size.z.toFixed(1)} · ${elapsed} s · PlayCanvas`;

    if (collisionBlockedReason) {
      info += " · anticollision désactivée (protection mémoire)";
    }

    if (cameraError) {
      info += ` · ${cameraError}`;
    }

    setStatus(info, !!cameraError);
    return true;
  } catch (error) {
    if (disposed) return false;

    console.error("Chargement :", error);

    releaseScene();
    setStatus(`Erreur : ${error?.message || error}`, true);

    return false;
  } finally {
    isLoading = false;

    if (!disposed) {
      progress.hide();
      $("fileInput").value = "";
      refreshUI();
    }
  }
}

async function resetAndLoad(source, name) {
  if (!canStartLoad()) return;

  loadedFileSource = null;
  loadedFileName = null;
  loadedFileIsLocal = false;

  const loaded = await loadSceneInternal(source, name);

  if (!loaded || disposed) return;

  loadedFileName = name;

  if (source instanceof Blob) {
    loadedFileIsLocal = true;
  } else {
    const resolvedUrl = new URL(source, location.href);

    if (
      resolvedUrl.protocol === "http:" ||
      resolvedUrl.protocol === "https:"
    ) {
      loadedFileSource = source;
    } else {
      loadedFileIsLocal = true;
    }
  }

  rememberCamera();
}

async function loadFile(file) {
  if (!file || !canStartLoad()) return;

  if (!isSupportedSceneName(file.name)) {
    setStatus(
      "Formats acceptés : .sog, .ply, .splat ou .ksplat.",
      true
    );

    $("fileInput").value = "";
    return;
  }

  await resetAndLoad(file, file.name);
}

async function selectPointInView(key, button) {
  const serial = ++pickSerial;
  picking = true;

  const stillValid = () =>
    !disposed &&
    serial === pickSerial &&
    navigation.heldButtons.get(key) === button &&
    sceneReady;

  try {
    setStatus("Sélection du point visé au centre…");

    await nextRenderedFrame();

    if (!stillValid()) return false;

    picker.resize(
      Math.max(1, canvas.width),
      Math.max(1, canvas.height)
    );

    const worldLayer = app.scene.layers.getLayerByName("World");

    picker.prepare(cameraEntity.camera, app.scene, [worldLayer]);

    const point = await picker.getWorldPointAsync(
      Math.floor(picker.width / 2),
      Math.floor(picker.height / 2)
    );

    if (!stillValid()) return false;

    if (!point) {
      setStatus("Aucun splat visé au centre de l’image.", true);
      return false;
    }

    const selectedTarget = new THREE.Vector3(
      point.x,
      point.y,
      point.z
    );

    if (selectedTarget.distanceToSquared(camera.position) < 1e-12) {
      setStatus(
        "Le point sélectionné est trop proche de la caméra.",
        true
      );
      return false;
    }

    target.copy(selectedTarget);

    syncCamera();
    requestGifCapture();

    setStatus(
      "Rotation autour du point visé · axe " +
      `${getPrincipalAxisName().toUpperCase()}.`
    );

    return true;
  } catch (error) {
    if (!disposed && serial === pickSerial) {
      console.error("Picking :", error);

      setStatus(
        `Sélection impossible : ${error?.message || error}`,
        true
      );
    }

    return false;
  } finally {
    if (serial === pickSerial) {
      picking = false;
    }
  }
}

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
    .applyQuaternion(camera.quaternion)
    .normalize();

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

    if (Math.abs(up.dot(direction)) > 0.999) {
      up.set(1, 0, 0);
    }

    up.addScaledVector(direction, -up.dot(direction));
  }

  camera.position.copy(position);
  camera.up.copy(up.normalize());
  target.copy(newTarget);

  syncCamera();
  $("cameraValue").value = getCameraString();
}

function buildViewUrl() {
  const url = new URL(location.href);

  for (const key of ["file", "camera_up", "camera_pos", "flipUp"]) {
    url.searchParams.delete(key);
  }

  if (!loadedFileIsLocal && loadedFileSource) {
    url.searchParams.set("file", loadedFileSource);
  }

  url.searchParams.set(
    "flipUp",
    $("flipUp").checked ? "1" : "0"
  );

  url.searchParams.set("camera_pos", getCameraString());

  return url;
}

function rememberCamera() {
  const url = buildViewUrl();

  try {
    history.replaceState(null, "", url.href);
  } catch (error) {
    console.warn("Mise à jour de l’URL :", error);
  }

  return url.href;
}

function getViewUrl() {
  if (loadedFileIsLocal || !loadedFileSource) return null;
  return buildViewUrl().href;
}

async function exportCamera() {
  if (!canNavigate()) return;

  navigation.clearNavigation();

  const cameraString = getCameraString();

  $("cameraValue").value = cameraString;
  $("cameraSection").open = true;

  rememberCamera();

  const viewUrl = getViewUrl();

  if (viewUrl) {
    try {
      await navigator.clipboard.writeText(viewUrl);

      setStatus(
        `URL complète copiée · ${loadedFileName} · ` +
        "caméra et flipUp inclus."
      );
    } catch {
      window.prompt(
        "Copie cette URL complète avec Ctrl/Cmd+C :",
        viewUrl
      );

      setStatus(
        "Copie automatique indisponible : URL présentée " +
        "pour copie manuelle."
      );
    }

    return;
  }

  try {
    await navigator.clipboard.writeText(cameraString);

    setStatus(
      "Fichier local : position caméra copiée " +
      "(sans paramètre file)."
    );
  } catch {
    $("cameraValue").focus();
    $("cameraValue").select();

    setStatus(
      "Fichier local : copie la position caméra avec Ctrl/Cmd+C."
    );
  }
}

function importCamera() {
  if (!canNavigate()) return;

  navigation.clearNavigation();

  try {
    applyCameraString($("cameraValue").value);
    rememberCamera();
    requestGifCapture();

    setStatus("Caméra restaurée.");
  } catch (error) {
    setStatus(error.message, true);
  }
}

function navigationIsActive() {
  return (
    picking ||
    navigation.heldButtons.size > 0 ||
    navigation.pressedKeys.size > 0
  );
}

function isPrincipalOrbitActive() {
  if (!navigation) return false;

  for (const button of navigation.heldButtons.values()) {
    if (
      button.dataset.hold === "orbitPrincipalPositive" ||
      button.dataset.hold === "orbitPrincipalNegative"
    ) {
      return true;
    }
  }

  return false;
}

function installUI() {
  $("fit").addEventListener("click", () => {
    if (!canNavigate()) return;

    navigation.clearNavigation();
    navigation.fitToView(sceneBox);
  });

  $("recenterTarget").addEventListener(
    "click",
    navigation.recenterTarget
  );

  $("exportCam").addEventListener("click", exportCamera);
  $("importCam").addEventListener("click", importCamera);

  $("principalAxis").addEventListener("change", () => {
    navigation.clearNavigation();
    updatePrincipalAxisHelp();
  });

  $("lockPrincipalRotation").addEventListener("change", () => {
    navigation.clearNavigation();

    const axis = getPrincipalAxisName().toUpperCase();

    setStatus(
      $("lockPrincipalRotation").checked
        ? `Rotations verrouillées sur l’axe ${axis} · orientation conservée.`
        : "Rotations libres réactivées."
    );
  });

  $("alignPrincipalUp").addEventListener("click", () => {
    if (!canNavigate()) return;

    navigation.clearNavigation();

    const forward = target.clone().sub(camera.position);

    if (forward.lengthSq() < 1e-12) {
      setStatus(
        "Impossible de redresser : caméra et cible confondues.",
        true
      );
      return;
    }

    forward.normalize();

    const up = getPrincipalAxis().clone().normalize();
    up.addScaledVector(forward, -up.dot(forward));

    if (up.lengthSq() < 1e-8) {
      setStatus(
        "L’axe principal est aligné avec le regard. " +
        "Oriente légèrement la vue avant de la redresser.",
        true
      );
      return;
    }

    camera.up.copy(up.normalize());

    syncCamera();
    requestGifCapture();

    setStatus(
      `Vue redressée · axe ${getPrincipalAxisName().toUpperCase()} vers le haut.`
    );
  });

  $("step").addEventListener("input", () => {
    $("stepValue").value = $("step").value;
  });

  $("keyboardLayout").addEventListener("change", () => {
    navigation.clearNavigation();
    updateKeyboardHelp();
  });

  $("togglePanel").addEventListener("click", () => {
    navigation.clearNavigation();

    const collapsed = $("panel").classList.toggle("collapsed");

    $("togglePanel").textContent = collapsed ? "+" : "−";

    $("togglePanel").setAttribute(
      "aria-expanded",
      String(!collapsed)
    );

    $("togglePanel").setAttribute(
      "aria-label",
      collapsed ? "Développer le panneau" : "Réduire le panneau"
    );
  });

  $("flipUp").addEventListener("change", () => {
    if (!canNavigate()) return;

    navigation.clearNavigation();
    measurement?.reset();

    applyUpFlip();
    computeSceneBounds();
    syncCamera();
    requestGifCapture();

    if (!collisionBlockedReason) {
      collision.checkCurrentCollisionPosition(collisionIsEnabled());
    }

    setStatus(
      `Axe Y ${
        $("flipUp").checked ? "vers le bas" : "vers le haut"
      } · vue conservée.`
    );
  });

  $("collisionEnabled").addEventListener("change", async () => {
    navigation.clearNavigation();

    if (collisionBlockedReason) {
      $("collisionEnabled").checked = false;
      collision.setCollisionStatus(collisionBlockedReason);
      refreshUI();
      return;
    }

    if (!$("collisionEnabled").checked) {
      collision.reset();
      collision.setCollisionStatus("Anticollision désactivée.");
      return;
    }

    if (!sceneReady) {
      collision.setCollisionStatus(
        "L’anticollision sera préparée au prochain chargement."
      );
      return;
    }

    isLoading = true;
    refreshUI();

    try {
      await prepareCollision();
    } finally {
      isLoading = false;

      if (!disposed) {
        progress.hide();
        refreshUI();
      }
    }
  });

  $("collisionDensity").addEventListener("input", () => {
    $("collisionDensityValue").value = $("collisionDensity").value;

    if (!collisionBlockedReason) {
      collision.checkCurrentCollisionPosition(collisionIsEnabled());
    }
  });

  $("collisionMargin").addEventListener("input", () => {
    $("collisionMarginValue").value = $("collisionMargin").value;

    if (!collisionBlockedReason) {
      collision.checkCurrentCollisionPosition(collisionIsEnabled());
    }
  });

  $("fileInput").addEventListener("change", (event) => {
    void loadFile(event.target.files?.[0]);
  });

  $("gifStart").addEventListener("click", () => {
    gifRecorder.start(sceneReady);
    refreshUI();
  });

  $("gifStop").addEventListener("click", async () => {
    navigation.clearNavigation();

    await gifRecorder.stopAndExport({
      sceneReady,
      nextFrame: nextRenderedFrame,
    });

    refreshUI();
  });

  $("gifCancel").addEventListener("click", () => {
    gifRecorder.cancel(sceneReady);
    refreshUI();
  });
}

async function applySceneGeoref(file) {
  if (!canStartLoad()) return;

  if (!sceneReady || !splatEntity) {
    setStatus(
      "Charge d’abord une scène, puis dépose son georef_stats.json.",
      true
    );
    return;
  }

  const serial = ++georefSerial;
  const entityAtStart = splatEntity;

  const stillValid = () =>
    !disposed &&
    serial === georefSerial &&
    sceneReady &&
    splatEntity === entityAtStart;

  try {
    setStatus(`Lecture de ${file.name}…`);

    const georef = await readGeorefFile(file);

    if (!stillValid()) return;

    // Remplacement seulement après validation :
    // un JSON invalide conserve le géoréférencement précédent.
    sceneGeoref = georef;

    const { lat, lon, height } = georef.origin;

      setStatus(
        `Référence géographique associée — EPSG:${georef.epsg} · ` +
        `origine X=${georef.origin.x.toFixed(3)}, ` +
        `Y=${georef.origin.y.toFixed(3)}, H=${georef.origin.h.toFixed(3)}`
      );
  } catch (error) {
    if (!stillValid()) return;

    console.error("Géoréférencement :", error);

    setStatus(
      `Géoréférencement non appliqué : ${error?.message || error}`,
      true
    );
  }
}

/**
 * Point dans le monde affiché -> coordonnées géographiques.
 *
 * À utiliser ensuite pour un point mesuré, une cible ou la caméra.
 * Retourne null sans référence géographique.
 */
function getAbsoluteCoordinates(worldPoint) {
  if (!sceneGeoref || !sceneReady || !splatEntity) {
    return null;
  }

  const worldFromEnu = new THREE.Matrix4().fromArray(
    splatEntity.getWorldTransform().data
  );

  // Monde affiché -> ENU local (annule flipUp notamment)
  const enu = new THREE.Vector3(
    worldPoint.x,
    worldPoint.y,
    worldPoint.z
  ).applyMatrix4(worldFromEnu.invert());

  const projected = sceneGeoref.enuToProjected(
    enu.x,
    enu.y,
    enu.z
  );

  return {
    epsg: sceneGeoref.epsg,
    x: projected.x,
    y: projected.y,
    h: projected.h,
  };
}

/**
 * Un seul fichier par dépôt :
 * - scène : comportement existant ;
 * - JSON : métadonnées de la scène déjà chargée.
 */
async function handleDroppedFiles(fileList) {
  if (!fileList?.length || !canStartLoad()) return;

  const files = Array.from(fileList);

  if (files.length !== 1) {
    setStatus(
      "Dépose un seul fichier à la fois : la scène, puis son JSON.",
      true
    );
    return;
  }

  const file = files[0];

  if (/\.json$/i.test(file.name)) {
    await applySceneGeoref(file);
    return;
  }

  await loadFile(file);
}

function installDnD() {
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

    if (!dragDepth) {
      $("drop").style.display = "none";
    }
  });

  window.addEventListener("drop", (event) => {
    event.preventDefault();

    dragDepth = 0;
    $("drop").style.display = "none";

        void handleDroppedFiles(event.dataTransfer?.files).catch((error) => {
        if (disposed) return;
    
        console.error("Dépôt :", error);
        setStatus(
            `Dépôt impossible : ${error?.message || error}`,
            true
        );
        });
  });
}

$("flipUp").checked = ["1", "true"].includes(params.get("flipUp"));

if (pendingCamera !== null) {
  $("cameraValue").value = pendingCamera;
}

updateKeyboardHelp();
updatePrincipalAxisHelp();

async function initialize() {
  isLoading = true;
  refreshUI();
  loadingStep("Initialisation du moteur graphique…");

  try {
    const graphicsDevice = await pc.createGraphicsDevice(canvas, {
      deviceTypes: ["webgl2"],
      antialias: false,
      alpha: false,

      // Conservé pour la compatibilité du module GIF existant.
      preserveDrawingBuffer: true,
    });

    app = new pc.Application(canvas, { graphicsDevice });

    app.setCanvasFillMode(pc.FILLMODE_NONE);
    app.setCanvasResolution(pc.RESOLUTION_AUTO);

    app.graphicsDevice.maxPixelRatio = Math.min(
      window.devicePixelRatio || 1,
      2
    );

    // Les centres restent nécessaires au cadrage et aux mesures,
    // même lorsque l'anticollision est désactivée.
    app.scene.gsplatCentersEnabled = true;

    importer = createSceneImporter({
      app,
      setStatus,

      onProgress(message, fraction, detail) {
        if (!disposed) {
          progress.update(message, fraction, detail);
        }
      },
    });

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

    collision = createCollisionSystem({
      getFlipUp: () => $("flipUp").checked,
      getThreshold: () => Number($("collisionDensity").value),

      // Marge en unités de scène.
      getMargin: () => Number($("collisionMargin").value),

      getSceneRadius: () => sceneRadius,
      getCenters: () => centers,
      getSceneCount: () => sceneCount,
      getCamera: () => camera,
      getTarget: () => target,
      setStatus,
      sleep,
    });
      
    memoryWatchdog = createMemoryWatchdog({
        getSceneCount: () => sceneCount,
        maxCollisionSplats: MAX_COLLISION_SPLATS,
        onDisableCollision: disableCollisionForMemory,
      });

    memoryWatchdog.start();

    gifRecorder = createGifRecorder({
      getCanvas: () => canvas,
      canNavigate,
      isLoading: () => isLoading,
      renderNow: () => {},
    });

    navigation = createNavigationSystem({
      canvas,
      camera,
      target,

      getSceneRadius: () => sceneRadius,
      getSceneCenter: () => sceneCenter,
      getCanNavigate: canNavigate,
      getPrincipalAxis,
      getPrincipalAxisName,

      isRotationLocked: () => $("lockPrincipalRotation").checked,
      getSpeedValue: () => Number($("step").value),
      getKeyboardLayout: () => $("keyboardLayout").value,

      navigateWithCollision: (move, enabled) =>
        collision.navigateWithCollision(
          move,
          enabled && collisionIsEnabled()
        ),

      syncCamera,
      requestGifCapture,
      selectPointInView,
      setStatus,
    });

    measurement = createMeasurementSystem({
      canvas,
      container,
      camera,

      getCenters: () => centers,
      getAbsoluteCoordinates,
        
      getModelMatrix: () => {
        const matrix = new THREE.Matrix4();

        if (splatEntity) {
          matrix.fromArray(splatEntity.getWorldTransform().data);
        }

        return matrix;
      },

      getSceneRadius: () => sceneRadius,
      getPrincipalAxis,

      canMeasure: () =>
        !!app &&
        !disposed &&
        sceneReady &&
        !isLoading &&
        !fatalError &&
        !picking &&
        !gifRecorder?.state.encoding &&
        !gifRecorder?.state.finalizing,

      clearNavigation: () => navigation.clearNavigation(),
      setStatus,

      pickTolerancePx: 4,
      neighborhoodRadiusRatio: 0.005,
      horizontalSnapAngleDeg: 5,
      verticalSnapAngleDeg: 5,
    });

    navigation.installMouseControls();
    navigation.installHeldButtons();
    navigation.installKeyboard();

    installUI();
    installDnD();

    app.on("update", navigation.frame);

    app.on("postrender", () => {
      measurement?.render();

      gifRecorder.processCapture({
        sceneReady,
        documentHidden: document.hidden,
        navigationIsActive,
        signature: cameraCaptureSignature(),
        continuous: isPrincipalOrbitActive(),
        cameraQuaternion: camera.quaternion,
      });
    });

    app.start();

    const resizeObserver = new ResizeObserver(resizeViewer);
    resizeObserver.observe(container);

    window.addEventListener("pagehide", (event) => {
      if (event.persisted) return;

      disposed = true;
      memoryWatchdog?.dispose();
      memoryWatchdog = null;
        
      navigation.clearNavigation();

      measurement?.dispose();
      measurement = null;

      gifRecorder.cancel(false);
      gifRecorder.clearDownload();

      resizeObserver.disconnect();

      picker?.destroy();
      picker = null;

      releaseScene();

      importer?.dispose();
      importer = null;

      progress.dispose();

      app?.destroy();
      app = null;
      cameraEntity = null;
    });

    resizeViewer();

    isLoading = false;
    progress.hide();
    refreshUI();

    const fileParam = params.get("file");

    if (fileParam) {
      await resetAndLoad(fileParam, filenameFromUrl(fileParam));
    } else {
      setStatus("Ouvre un fichier .sog, .ply, .splat ou .ksplat.");
    }
  } catch (error) {
    if (disposed) return;

    console.error("Initialisation PlayCanvas :", error);

    isLoading = false;
    fatalError = true;

    progress.hide();

    setStatus(
      `Initialisation impossible : ${error?.message || error}`,
      true
    );

    refreshUI();
  }
}

void initialize();
