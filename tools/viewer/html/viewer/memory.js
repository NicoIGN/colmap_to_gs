const MiB = 1024 * 1024;

function readHeap() {
  try {
    const memory = performance.memory;

    if (!memory) return null;

    const used = Number(memory.usedJSHeapSize);
    const limit = Number(memory.jsHeapSizeLimit);

    if (
      !Number.isFinite(used) ||
      !Number.isFinite(limit) ||
      used <= 0 ||
      limit <= 0
    ) {
      return null;
    }

    return {
      used,
      limit,
      ratio: used / limit,
    };
  } catch {
    return null;
  }
}

export function createMemoryWatchdog({
  getSceneCount,
  onDisableCollision,

  maxCollisionSplats = 1_000_000,

  // navigator.deviceMemory est approximatif et peut être absent.
  maxHeapBytes = (
    Number(navigator.deviceMemory) > 0 &&
    Number(navigator.deviceMemory) <= 4
      ? 384
      : 768
  ) * MiB,

  maxHeapRatio = 0.70,
  intervalMs = 1000,
}) {
  let timer = null;
  let disposed = false;
  let checking = false;

  let collisionBlocked = false;
  let blockedReason = "";
  let lastHeap = null;

  function disableCollision(reason) {
    if (collisionBlocked) return;

    // Verrouiller avant le callback pour éviter toute réentrance.
    collisionBlocked = true;
    blockedReason = reason;

    onDisableCollision(reason);
  }

  function check(context = "surveillance") {
    if (disposed || checking) return collisionBlocked;

    checking = true;

    try {
      lastHeap = readHeap();

      if (collisionBlocked) return true;

      const count = Number(getSceneCount()) || 0;

      if (count > maxCollisionSplats) {
        disableCollision(
          "Anticollision désactivée par sécurité mémoire : " +
          `${count.toLocaleString()} splats dépassent le seuil de ` +
          `${maxCollisionSplats.toLocaleString()}.`
        );

        return true;
      }

      if (
        lastHeap &&
        (
          lastHeap.used >= maxHeapBytes ||
          lastHeap.ratio >= maxHeapRatio
        )
      ) {
        disableCollision(
          "Anticollision désactivée par le watchdog mémoire : " +
          `${Math.round(lastHeap.used / MiB)} Mio de heap JS utilisés ` +
          `(${Math.round(lastHeap.ratio * 100)} % de sa limite), ` +
          `étape « ${context} » .`
        );

        return true;
      }

      return false;
    } finally {
      checking = false;
    }
  }

  function start() {
    if (disposed || timer !== null) return;

    check("démarrage");

    timer = window.setInterval(() => {
      check();
    }, intervalMs);
  }

  function stop() {
    if (timer === null) return;

    window.clearInterval(timer);
    timer = null;
  }

  return {
    start,
    stop,
    check,

    get collisionBlocked() {
      return collisionBlocked;
    },

    get reason() {
      return blockedReason;
    },

    getSnapshot() {
      return {
        heapMeasurementAvailable: lastHeap !== null,
        heapUsedBytes: lastHeap?.used ?? null,
        heapLimitBytes: lastHeap?.limit ?? null,
        heapRatio: lastHeap?.ratio ?? null,

        collisionBlocked,
        reason: blockedReason,

        limits: {
          maxCollisionSplats,
          maxHeapBytes,
          maxHeapRatio,
        },
      };
    },

    dispose() {
      stop();
      disposed = true;
    },
  };
}
