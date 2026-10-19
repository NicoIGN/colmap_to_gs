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
  const state = {
    cells: null,
    cellSize: 0,
    building: false,
    lastWarningAt: 0,
  };

  const COLLISION_MAX_CELLS = 300000;
  const COLLISION_MAX_STEPS = 512;

  const debug = {
    enabled: false,
    lastCheck: null,
    counters: {
      calls: 0,
      bypassed: 0,
      accepted: 0,
      blocked: 0,
    },
  };

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
    state.cells = null;
    state.cellSize = 0;
    state.lastWarningAt = 0;
    debug.lastCheck = null;
    debug.counters.calls = 0;
    debug.counters.bypassed = 0;
    debug.counters.accepted = 0;
    debug.counters.blocked = 0;
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

    const threshold = getThreshold();

    const x = Math.floor(position.x / cellSize);
    const y = Math.floor(position.y / cellSize);
    const z = Math.floor(position.z / cellSize);

    return (cells.get(collisionKey(x, y, z)) || 0) >= threshold;
  }

  function collisionPathIsClear(fromWorld, toWorld) {
    const from = cameraToCollisionSpace(fromWorld);
    const to = cameraToCollisionSpace(toWorld);

    const distance = from.distanceTo(to);
    const cellSize = state.cellSize;

    const diagnostic = {
      fromWorld: fromWorld.toArray(),
      toWorld: toWorld.toArray(),
      fromCollisionSpace: from.toArray(),
      toCollisionSpace: to.toArray(),
      distance,
      cellSize,
      steps: 0,
      blockedAt: null,
      reason: null,
    };

    if (distance < 1e-12) {
      diagnostic.reason = "no-displacement";
      debug.lastCheck = diagnostic;
      return true;
    }

    const steps = Math.max(
      1,
      Math.ceil(distance / Math.max(cellSize * 0.5, 1e-6))
    );

    diagnostic.steps = steps;

    if (steps > COLLISION_MAX_STEPS) {
      diagnostic.reason = "step-limit";
      debug.lastCheck = diagnostic;
      return false;
    }

    const position = new THREE.Vector3();

    for (let i = 0; i <= steps; i++) {
      position.lerpVectors(from, to, i / steps);

      if (intersectsDenseCells(position)) {
        diagnostic.reason = "dense-cell";
        diagnostic.blockedAt = {
          sample: i,
          position: position.toArray(),
        };
        debug.lastCheck = diagnostic;
        return false;
      }
    }

    diagnostic.reason = "path-clear";
    debug.lastCheck = diagnostic;
    return true;
  }

  function reportCollisionBlock() {
    const now = performance.now();
    if (now - state.lastWarningAt < 1000) return;

    state.lastWarningAt = now;

    setCollisionStatus(
      "Mouvement bloqué : entrée dans une cellule dense."
    );
  }

  function navigateWithCollision(move, enabled) {
    debug.counters.calls++;

    if (!enabled || !state.cells) {
      debug.counters.bypassed++;
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
      debug.counters.accepted++;
      return true;
    }

    debug.counters.blocked++;

    camera.position.copy(previousPosition);
    target.copy(previousTarget);
    camera.up.copy(previousUp);
    camera.lookAt(target);
    camera.updateMatrixWorld(true);

    reportCollisionBlock();

    return false;
  }

  async function buildGrid() {
    const centers = getCenters();

    if (!centers?.length) {
      throw new Error("Aucune scène disponible.");
    }

    if (state.cells) return;

    state.building = true;

    const count = centers.length / 3;
    let cellSize = Math.max(getSceneRadius() / 160, 1e-6);
    const maxAttempts = 8;

    try {
      for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        const cells = new Map();
        let overflow = false;

        setCollisionStatus(
          `Construction de la grille — tentative ${attempt}…`
        );

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
            } else {
              if (cells.size >= COLLISION_MAX_CELLS) {
                overflow = true;
                break;
              }

              cells.set(key, 1);
            }
          }

          if (i > 0 && i % 100000 === 0) {
            setCollisionStatus(
              `Grille — tentative ${attempt} : ` +
              `${Math.round(i / count * 100)} %…`
            );

            await sleep(0);
          }
        }

        if (overflow) {
          cells.clear();
          cellSize *= 2;
          await sleep(0);
          continue;
        }

        state.cells = cells;
        state.cellSize = cellSize;

        let denseCells = 0;
        const threshold = getThreshold();

        for (const density of cells.values()) {
          if (density >= threshold) denseCells++;
        }

        setCollisionStatus(
          `Grille prête : ${cells.size.toLocaleString()} cellules · ` +
          `${denseCells.toLocaleString()} obstacles`
        );

        return;
      }

      throw new Error(
        "Impossible de construire la grille dans la limite mémoire."
      );
    } finally {
      state.building = false;
    }
  }

  function checkCurrentCollisionPosition(enabled) {
    if (!enabled || !state.cells) return;

    const camera = getCamera();
    const position = cameraToCollisionSpace(camera.position);

    if (intersectsDenseCells(position)) {
      setCollisionStatus(
        "Attention : caméra dans une cellule dense.",
        true
      );
    } else {
      setCollisionStatus(
        "Anticollision active (cellules denses)."
      );
    }
  }

  function getSnapshot() {
    const camera = getCamera();
    const target = getTarget();
    const cells = state.cells;

    let denseCells = 0;
    let maxDensity = 0;

    if (cells) {
      const threshold = getThreshold();

      for (const density of cells.values()) {
        if (density >= threshold) denseCells++;
        maxDensity = Math.max(maxDensity, density);
      }
    }

    return {
      optionChecked: null,
      sceneCount: getSceneCount(),
      sceneRadius: getSceneRadius(),
      gridAvailable: !!cells,
      occupiedCells: cells?.size || 0,
      denseCells,
      maxDensity,
      cellSize: state.cellSize,
      threshold: getThreshold(),
      margin: getMargin(),
      flipUp: getFlipUp(),
      cameraWorld: camera.position.toArray(),
      cameraCollisionSpace: cameraToCollisionSpace(
        camera.position
      ).toArray(),
      target: target.toArray(),
      cameraCurrentlyBlocked: cells
        ? intersectsDenseCells(
            cameraToCollisionSpace(camera.position)
          )
        : null,
      counters: { ...debug.counters },
      lastCheck: debug.lastCheck,
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
