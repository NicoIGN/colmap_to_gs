import * as pc from "playcanvas";
import * as THREE from "three";
import { createCollisionSystem } from "./viewer/collision.js";
import { createGifRecorder } from "./viewer/gif.js";
import { createNavigationSystem } from "./viewer/navigation.js";

const $ = (id) => document.getElementById(id);
const container = $("viewer");
const params = new URLSearchParams(location.search);

const sleep = (ms) =>
  new Promise((resolve) => setTimeout(resolve, ms));

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
let sceneAsset = null;
let picker = null;
let centers = null;

let loadedFileSource = null;
let loadedFileName = null;
let loadedFileIsLocal = false;

let sceneReady = false;
let isLoading = false;
let fatalError = false;
let sceneRadius = 1;
let sceneCount = 0;

let pendingCamera = params.get("camera_pos");
let pendingUp = params.get("camera_up");

let pickSerial = 0;
let picking = false;

let navigation = null;
let collision = null;
let gifRecorder = null;
let legacyConverterPromise = null;

const canvas = document.createElement("canvas");
canvas.setAttribute("aria-label", "Vue 3D des Gaussian Splats");
container.appendChild(canvas);

const canNavigate = () =>
  !!app &&
  sceneReady &&
  !isLoading &&
  !fatalError &&
  !picking &&
  !gifRecorder?.state.encoding &&
  !gifRecorder?.state.finalizing;

function setStatus(message, error = false) {
  $("status").textContent = message;
  $("status").classList.toggle("error", error);
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
  const blocked =
    isLoading ||
    fatalError ||
    gifRecorder?.state.encoding ||
    gifRecorder?.state.finalizing;

  document.querySelectorAll(
    "#panel button, #panel input, #panel select, #panel textarea"
  ).forEach((element) => {
    if (element.id !== "togglePanel") {
      element.disabled = blocked;
    }
  });

  gifRecorder?.updateUI(sceneReady);
}

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

function requestGifCapture() {
  gifRecorder?.requestCapture(cameraCaptureSignature());
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

function applyUpFlip() {
  if (!splatEntity) return;

  splatEntity.setLocalEulerAngles(
    $("flipUp").checked ? 180 : 0,
    0,
    0
  );
}

function releaseScene() {
  sceneReady = false;

  collision?.reset();

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

function filenameFromUrl(url) {
  try {
    const leaf = new URL(url, location.href)
      .pathname.split("/").pop();

    return decodeURIComponent(leaf || "scene.ply");
  } catch {
    return url.split("/").pop().split(/[?#]/)[0] || "scene.ply";
  }
}

async function loadSceneInternal(url, name) {
  if (
    !app ||
    isLoading ||
    fatalError ||
    gifRecorder?.state.encoding ||
    gifRecorder?.state.finalizing
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
  navigation.clearNavigation();
  gifRecorder.cancel(sceneReady);
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

    await nextRenderedFrame();

    if ($("collisionEnabled").checked) {
      try {
        await collision.buildGrid();
        collision.checkCurrentCollisionPosition(true);
      } catch (error) {
        $("collisionEnabled").checked = false;
        collision.setCollisionStatus(
          `Anticollision indisponible : ${error?.message || error}`,
          true
        );
      }
    }

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

async function resetAndLoad(url, name) {
  if (
    !app ||
    isLoading ||
    fatalError ||
    gifRecorder?.state.encoding ||
    gifRecorder?.state.finalizing
  ) {
    return;
  }

  loadedFileSource = null;
  loadedFileName = null;
  loadedFileIsLocal = false;

  await loadSceneInternal(url, name);

  if (!sceneReady) return;

  loadedFileName = name;

  const resolvedUrl = new URL(url, location.href);

  if (
    resolvedUrl.protocol === "http:" ||
    resolvedUrl.protocol === "https:"
  ) {
    loadedFileSource = url;
    loadedFileIsLocal = false;
  } else {
    loadedFileSource = null;
    loadedFileIsLocal = true;
  }

  rememberCamera();
}

async function loadFile(file) {
  if (
    !file ||
    !app ||
    isLoading ||
    gifRecorder?.state.encoding ||
    gifRecorder?.state.finalizing
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

async function selectPointInView(key, button) {
  const serial = ++pickSerial;
  picking = true;

  try {
    setStatus("Sélection du point visé au centre…");

    await nextRenderedFrame();

    if (
      serial !== pickSerial ||
      navigation.heldButtons.get(key) !== button ||
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

    if (
      serial !== pickSerial ||
      navigation.heldButtons.get(key) !== button ||
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
      setStatus(
        "Le point sélectionné est trop proche de la caméra.",
        true
      );
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

function rememberCamera() {
  const url = new URL(location.href);

  url.searchParams.delete("file");
  url.searchParams.delete("camera_up");
  url.searchParams.delete("camera_pos");
  url.searchParams.delete("flipUp");

  if (!loadedFileIsLocal && loadedFileSource) {
    url.searchParams.set("file", loadedFileSource);
  }

  url.searchParams.set(
    "flipUp",
    $("flipUp").checked ? "1" : "0"
  );

  url.searchParams.set("camera_pos", getCameraString());

  try {
    history.replaceState(null, "", url.href);
  } catch (error) {
    console.warn("Mise à jour de l’URL :", error);
  }

  return url.href;
}

function getViewUrl() {
  if (loadedFileIsLocal || !loadedFileSource) {
    return null;
  }

  const url = new URL(location.href);

  url.searchParams.delete("file");
  url.searchParams.delete("flipUp");
  url.searchParams.delete("camera_pos");
  url.searchParams.delete("camera_up");

  url.searchParams.set("file", loadedFileSource);
  url.searchParams.set(
    "flipUp",
    $("flipUp").checked ? "1" : "0"
  );
  url.searchParams.set("camera_pos", getCameraString());

  return url.href;
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
    [...navigation.pressedKeys].some((key) =>
      !!navigation.pressedKeys.has(key)
    )
  );
}

function isPrincipalOrbitActive() {
  if (!navigation) return false;

  return [...navigation.heldButtons.values()].some((button) =>
    button.dataset.hold === "orbitPrincipalPositive" ||
    button.dataset.hold === "orbitPrincipalNegative"
  );
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

    const locked = $("lockPrincipalRotation").checked;
    const axis = getPrincipalAxisName().toUpperCase();

    setStatus(
      locked
        ? `Rotations verrouillées sur l’axe ${axis} · orientation conservée.`
        : "Rotations libres réactivées."
    );
  });

  $("alignPrincipalUp").addEventListener("click", () => {
    if (!canNavigate()) return;
    +
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
    
    // Projeter l’axe principal dans le plan de l’écran.
    // Son sens positif devient le haut de l’image.
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
    
    // Correction explicite du roulis, même avec le verrou actif.
    // La position de la caméra et la cible restent inchangées.
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
      collapsed
        ? "Développer le panneau"
        : "Réduire le panneau"
    );
  });

  $("flipUp").addEventListener("change", () => {
    if (!canNavigate()) return;

    navigation.clearNavigation();

    applyUpFlip();
    computeSceneBounds();
    syncCamera();
    requestGifCapture();

    collision.checkCurrentCollisionPosition(
      $("collisionEnabled").checked
    );

    setStatus(
      `Axe Y ${
        $("flipUp").checked ? "vers le bas" : "vers le haut"
      } · vue conservée.`
    );
  });

  $("collisionEnabled").addEventListener(
    "change",
    async () => {
      navigation.clearNavigation();

      if (!$("collisionEnabled").checked) {
        collision.setCollisionStatus(
          "Anticollision désactivée."
        );
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
        await collision.buildGrid();
        collision.checkCurrentCollisionPosition(true);
      } catch (error) {
        $("collisionEnabled").checked = false;

        collision.setCollisionStatus(
          `Anticollision indisponible : ${error?.message || error}`,
          true
        );
      } finally {
        isLoading = false;
        refreshUI();
      }
    }
  );

  $("collisionDensity").addEventListener("input", () => {
    $("collisionDensityValue").value =
      $("collisionDensity").value;

    collision.checkCurrentCollisionPosition(
      $("collisionEnabled").checked
    );
  });

  $("collisionMargin").addEventListener("input", () => {
    $("collisionMarginValue").value =
      $("collisionMargin").value;

    collision.checkCurrentCollisionPosition(
      $("collisionEnabled").checked
    );
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

function installDnD() {
  let dragDepth = 0;

  window.addEventListener("dragenter", (event) => {
    event.preventDefault();

    if (!Array.from(
      event.dataTransfer?.types ?? []
    ).includes("Files")) {
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

    void loadFile(event.dataTransfer?.files?.[0]);
  });
}

$("flipUp").checked = ["1", "true"].includes(
  params.get("flipUp")
);

if (pendingCamera !== null) {
  $("cameraValue").value = pendingCamera;
}

updateKeyboardHelp();
updatePrincipalAxisHelp();

async function initialize() {
  isLoading = true;
  refreshUI();

  try {
    const graphicsDevice = await pc.createGraphicsDevice(canvas, {
      deviceTypes: ["webgl2"],
      antialias: false,
      alpha: false,
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

    collision = createCollisionSystem({
      getFlipUp: () => $("flipUp").checked,
      getThreshold: () => Number($("collisionDensity").value),
      getMargin: () => Number($("collisionMargin").value),
      getSceneRadius: () => sceneRadius,
      getCenters: () => centers,
      getSceneCount: () => sceneCount,
      getCamera: () => camera,
      getTarget: () => target,
      setStatus,
      sleep,
    });

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
      isRotationLocked: () =>
        $("lockPrincipalRotation").checked,
      getSpeedValue: () => Number($("step").value),
      getKeyboardLayout: () => $("keyboardLayout").value,
      navigateWithCollision: (move, enabled) =>
        collision.navigateWithCollision(move, enabled),
      syncCamera,
      requestGifCapture,
      selectPointInView,
      setStatus,
    });

    navigation.installMouseControls();
    navigation.installHeldButtons();
    navigation.installKeyboard();

    installUI();
    installDnD();

    app.on("update", navigation.frame);
      app.on("postrender", () => {
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

      navigation.clearNavigation();
      gifRecorder.clearDownload();
      resizeObserver.disconnect();
      picker?.destroy();

      if (app) {
        releaseScene();
        app.destroy();
        app = null;
      }
    });

    resizeViewer();

    isLoading = false;
    refreshUI();

    const fileParam = params.get("file");

    if (fileParam) {
      await resetAndLoad(
        fileParam,
        filenameFromUrl(fileParam)
      );
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

void initialize();
