import * as THREE from "three";

export function createCollisionSystem({
  getFlipUp,
  getThreshold,
  getMargin,
  getSceneRadius,
  getCenters,
  getSceneCount,
  getCamera,
  getTarget,
  setStatus,
  sleep,
}) {
  const COLLISION_MAX_CELLS = 150_000;
  const COLLISION_MAX_STEPS = 512;
  const MAX_MARGIN_CELL_TESTS = 4096;

  const reportedMemory = Number(navigator.deviceMemory) || null;
  const maxSplats = reportedMemory !== null && reportedMemory <= 4
    ? 500_000
    : 1_000_000;

  const state = {
    cells: null,
    cellSize: 0,
    building: false,
    lastWarningAt: 0,
  };

  const counters = {
    calls: 0,
    bypassed: 0,
    accepted: 0,
    blocked: 0,
  };

  let generation = 0;
  let buildPromise = null;
  let buildingCells = null;
  let lastCheck = null;

  function collisionKey(x, y, z) {
    return `${x},${y},${z}`;
  }

  function setCollisionStatus(message, error = false) {
    const element = document.getElementById("collisionStatus");

    if (!element) return;

    element.textContent = message;
    element.classList.toggle("error", error);
  }

  function reset() {
    generation++;

    state.cells?.clear();
    buildingCells?.clear();

    state.cells = null;
    state.cellSize = 0;
    state.building = false;
    state.lastWarningAt = 0;

    buildingCells = null;
    buildPromise = null;
    lastCheck = null;

    for (const key of Object.keys(counters)) {
      counters[key] = 0;
    }
  }

  function cameraToCollisionSpace(position) {
    const result = position.clone();

    if (getFlipUp()) {
      result.y = -result.y;
      result.z = -result.z;
    }

    return result;
  }

  function intersectsDenseCells(position) {
    const { cells, cellSize } = state;

    if (!cells || !cellSize) return false;

    const threshold = Math.max(1, Number(getThreshold()) || 1);
    const margin = Math.max(0, Number(getMargin()) || 0);

    const minX = Math.floor((position.x - margin) / cellSize);
    const maxX = Math.floor((position.x + margin) / cellSize);
    const minY = Math.floor((position.y - margin) / cellSize);
    const maxY = Math.floor((position.y + margin) / cellSize);
    const minZ = Math.floor((position.z - margin) / cellSize);
    const maxZ = Math.floor((position.z + margin) / cellSize);

    const tests =
      (maxX - minX + 1) *
      (maxY - minY + 1) *
      (maxZ - minZ + 1);

    // Ne pas lancer une boucle cubique incontrôlée avec une marge énorme.
    // Par sécurité, le déplacement est refusé, pas accepté silencieusement.
    if (!Number.isFinite(tests) || tests > MAX_MARGIN_CELL_TESTS) {
      lastCheck = { reason: "margin-limit" };
      return true;
    }

    const marginSquared = margin * margin;

    for (let x = minX; x <= maxX; x++) {
      for (let y = minY; y <= maxY; y++) {
        for (let z = minZ; z <= maxZ; z++) {
          const density = cells.get(collisionKey(x, y, z)) || 0;

          if (density < threshold) continue;

          // Distance du point à la boîte de la cellule :
          // marge sphérique plutôt qu'un simple cube de voisinage.
          const dx = Math.max(
            x * cellSize - position.x,
            0,
            position.x - (x + 1) * cellSize
          );

          const dy = Math.max(
            y * cellSize - position.y,
            0,
            position.y - (y + 1) * cellSize
          );

          const dz = Math.max(
            z * cellSize - position.z,
            0,
            position.z - (z + 1) * cellSize
          );

          if (dx * dx + dy * dy + dz * dz <= marginSquared) {
            return true;
          }
        }
      }
    }

    return false;
  }

  function collisionPathIsClear(fromWorld, toWorld) {
    const from = cameraToCollisionSpace(fromWorld);
    const to = cameraToCollisionSpace(toWorld);
    const distance = from.distanceTo(to);

    if (distance < 1e-12) {
      lastCheck = { reason: "no-displacement" };
      return true;
    }

    const steps = Math.max(
      1,
      Math.ceil(distance / Math.max(state.cellSize * 0.5, 1e-6))
    );

    if (steps > COLLISION_MAX_STEPS) {
      lastCheck = { reason: "step-limit", steps };
      return false;
    }

    const position = new THREE.Vector3();

    for (let i = 0; i <= steps; i++) {
      position.lerpVectors(from, to, i / steps);

      // Éviter qu'un diagnostic d'un précédent appel ne soit réutilisé.
      lastCheck = null;

      if (intersectsDenseCells(position)) {
        lastCheck = {
          reason: lastCheck?.reason || "dense-cell",
          steps,
          sample: i,
          position: position.toArray(),
        };

        return false;
      }
    }

    lastCheck = { reason: "path-clear", steps };
    return true;
  }

  function reportCollisionBlock() {
    const now = performance.now();

    if (now - state.lastWarningAt < 1000) return;

    state.lastWarningAt = now;

    const reason = lastCheck?.reason;

    setCollisionStatus(
      reason === "margin-limit"
        ? "Mouvement bloqué : marge trop grande. Réduis la marge."
        : reason === "step-limit"
          ? "Mouvement bloqué : déplacement trop long pour le test anticollision."
          : "Mouvement bloqué : cellule dense ou marge de sécurité atteinte."
    );
  }

  function navigateWithCollision(move, enabled) {
    counters.calls++;

    if (!enabled || !state.cells || state.building) {
      counters.bypassed++;
      move();
      return true;
    }

    const camera = getCamera();
    const target = getTarget();

    const previousPosition = camera.position.clone();
    const previousTarget = target.clone();
    const previousUp = camera.up.clone();

    move();

    if (collisionPathIsClear(previousPosition, camera.position)) {
      counters.accepted++;
      return true;
    }

    counters.blocked++;

    camera.position.copy(previousPosition);
    target.copy(previousTarget);
    camera.up.copy(previousUp);

    camera.lookAt(target);
    camera.updateMatrixWorld(true);

    reportCollisionBlock();
    return false;
  }

  function buildGrid({ onProgress = () => {} } = {}) {
    if (state.cells) return Promise.resolve();
    if (buildPromise) return buildPromise;

    const centers = getCenters();

    if (!centers?.length || centers.length % 3 !== 0) {
      return Promise.reject(new Error("Aucune scène disponible."));
    }

    const count = centers.length / 3;

    if (Math.max(count, Number(getSceneCount()) || 0) > maxSplats) {
      return Promise.reject(new Error(
        `Scène trop volumineuse pour l’anticollision : ` +
        `seuil de ${maxSplats.toLocaleString()} splats.`
      ));
    }

    const id = generation;
    state.building = true;

    function checkActive() {
      if (id !== generation || getCenters() !== centers) {
        throw new DOMException(
          "Construction de grille interrompue.",
          "AbortError"
        );
      }
    }

    const task = (async () => {
      let cellSize = Math.max(getSceneRadius() / 160, 1e-6);

      try {
        for (let attempt = 1; attempt <= 8; attempt++) {
          checkActive();

          const cells = new Map();
          buildingCells = cells;
          let overflow = false;

          for (let i = 0; i < count; i++) {
            const x = centers[i * 3];
            const y = centers[i * 3 + 1];
            const z = centers[i * 3 + 2];

            if (
              Number.isFinite(x) &&
              Number.isFinite(y) &&
              Number.isFinite(z)
            ) {
              const key = collisionKey(
                Math.floor(x / cellSize),
                Math.floor(y / cellSize),
                Math.floor(z / cellSize)
              );

              const previous = cells.get(key);

              if (previous !== undefined) {
                cells.set(key, previous + 1);
              } else if (cells.size < COLLISION_MAX_CELLS) {
                cells.set(key, 1);
              } else {
                overflow = true;
                break;
              }
            }

            if (i % 16_384 === 0) {
              const message =
                `Anticollision — tentative ${attempt} : ` +
                `${Math.round(i / count * 100)} %`;

              setCollisionStatus(message);
              onProgress(i / count, message);

              await sleep(0);
              checkActive();
            }
          }

          checkActive();

          if (overflow) {
            cells.clear();
            buildingCells = null;
            cellSize *= 2;

            await sleep(0);
            checkActive();
            continue;
          }

          state.cells = cells;
          state.cellSize = cellSize;
          buildingCells = null;

          onProgress(1, "Grille anticollision prête");

          setCollisionStatus(
            `Grille prête : ${cells.size.toLocaleString()} cellules.`
          );

          return;
        }

        throw new Error(
          "Impossible de construire la grille dans la limite de cellules."
        );
      } finally {
        // Ne pas modifier l'état d'une nouvelle construction.
        if (id === generation) {
          buildingCells?.clear();
          buildingCells = null;
          state.building = false;
        }
      }
    })();

    buildPromise = task;

    // Nettoyage sans créer de rejet de Promise non observé.
    task.then(
      () => {
        if (buildPromise === task) buildPromise = null;
      },
      () => {
        if (buildPromise === task) buildPromise = null;
      }
    );

    return task;
  }

  function checkCurrentCollisionPosition(enabled) {
    if (!enabled || !state.cells) return;

    lastCheck = null;

    const blocked = intersectsDenseCells(
      cameraToCollisionSpace(getCamera().position)
    );

    setCollisionStatus(
      lastCheck?.reason === "margin-limit"
        ? "Marge trop grande pour la grille : réduis la marge."
        : blocked
          ? "Attention : caméra dans un obstacle ou sa marge."
          : "Anticollision active.",
      blocked
    );
  }

  function getSnapshot() {
    const camera = getCamera();
    const position = cameraToCollisionSpace(camera.position);

    return {
      sceneCount: getSceneCount(),
      sceneRadius: getSceneRadius(),
      gridAvailable: !!state.cells,
      building: state.building,
      occupiedCells: state.cells?.size || 0,
      cellSize: state.cellSize,
      threshold: getThreshold(),
      margin: getMargin(),
      flipUp: getFlipUp(),
      cameraWorld: camera.position.toArray(),
      cameraCollisionSpace: position.toArray(),
      target: getTarget().toArray(),
      counters: { ...counters },
      lastCheck,
    };
  }

  return {
    state,
    reset,
    buildGrid,
    navigateWithCollision,
    checkCurrentCollisionPosition,
    setCollisionStatus,
    getSnapshot,
  };
}
