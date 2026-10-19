import * as THREE from "three";

export function createGifRecorder({
  getCanvas,
  canNavigate,
  isLoading,
  renderNow,
}) {
  const GIF_MAX_FRAMES = 60;
  const GIF_MAX_WIDTH = 1280;
  const GIF_MAX_HEIGHT = 720;
  const GIF_SETTLE_MS = 600;

  // Échantillonnage pendant une rotation continue.
  const GIF_SAMPLE_INTERVAL_MS = 100;
  const GIF_SAMPLE_MIN_ANGLE_DEG = 6;
    
  const state = {
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
    lastSampleAt: 0,
    lastSampleQuaternion: null,
  };

  let encoderPromise = null;

  function setStatus(message, error = false) {
    const element = document.getElementById("gifStatus");
    element.textContent = message;
    element.classList.toggle("error", error);
    element.classList.toggle(
      "recording",
      state.recording && !error
    );
  }

  function clearDownload() {
    const link = document.getElementById("gifDownload");

    if (state.downloadUrl) {
      URL.revokeObjectURL(state.downloadUrl);
      state.downloadUrl = null;
    }

    link.hidden = true;
    link.removeAttribute("href");
  }

  function updateUI(sceneReady) {
    const blocked =
      isLoading() ||
      state.encoding ||
      state.finalizing;

    document.getElementById("gifStart").disabled =
      blocked || !sceneReady || state.recording;

    document.getElementById("gifStop").disabled =
      blocked ||
      (!state.recording && !state.frames.length);

    document.getElementById("gifCancel").disabled =
      blocked ||
      (!state.recording && !state.frames.length);

    document.getElementById("gifDelay").disabled =
      blocked || state.recording;
  }

  function requestCapture(signature) {
    if (!state.recording) return;

    state.pendingCapture = true;
    state.changedAt = performance.now();
    state.observedSignature = signature;
  }

  function captureFrame() {
    const source = getCanvas();
    const output = document.createElement("canvas");

    output.width = state.width;
    output.height = state.height;

    const context = output.getContext("2d", {
      willReadFrequently: true,
    });

    if (!context) {
      throw new Error(
        "Impossible de créer le canvas de capture."
      );
    }

    context.fillStyle = "#10131b";
    context.fillRect(0, 0, output.width, output.height);

    const scale = Math.min(
      output.width / source.width,
      output.height / source.height
    );

    const width = source.width * scale;
    const height = source.height * scale;

    context.drawImage(
      source,
      (output.width - width) / 2,
      (output.height - height) / 2,
      width,
      height
    );

    state.frames.push(
      context.getImageData(
        0,
        0,
        output.width,
        output.height
      ).data
    );

    if (state.frames.length >= GIF_MAX_FRAMES) {
      state.recording = false;

      setStatus(
        `Limite de ${GIF_MAX_FRAMES} images atteinte. ` +
        "Cliquer sur « Terminer et exporter »."
      );
    } else {
      setStatus(
        `● Capture active — ${state.frames.length} position(s) · ` +
        `${state.width} × ${state.height} px`
      );
    }
  }

    function markCaptured(signature, cameraQuaternion, now) {
      state.lastCapturedSignature = signature;
      state.lastSampleAt = now;

      if (cameraQuaternion) {
        state.lastSampleQuaternion = state.lastSampleQuaternion
          ? state.lastSampleQuaternion.copy(cameraQuaternion)
          : cameraQuaternion.clone();
      }
    }
    
    function processCapture({
      sceneReady,
      documentHidden,
      navigationIsActive,
      signature,
      continuous = false,
      cameraQuaternion = null,
    }) {
      if (
        !state.recording ||
        !sceneReady ||
        isLoading() ||
        documentHidden
      ) {
        return;
      }

      try {
        const now = performance.now();

        if (signature !== state.observedSignature) {
          state.observedSignature = signature;
          state.changedAt = now;
          state.pendingCapture = true;
        }

        // Rotation continue : capturer selon le temps et l’angle.
        if (continuous && cameraQuaternion) {
          const elapsed = now - state.lastSampleAt;

          const angle = state.lastSampleQuaternion
            ? THREE.MathUtils.radToDeg(
                state.lastSampleQuaternion.angleTo(cameraQuaternion)
              )
            : Infinity;

          if (
            elapsed >= GIF_SAMPLE_INTERVAL_MS &&
            angle >= GIF_SAMPLE_MIN_ANGLE_DEG &&
            signature !== state.lastCapturedSignature
          ) {
            captureFrame();
            markCaptured(signature, cameraQuaternion, now);
          }

          // pendingCapture reste actif : la position finale
          // sera capturée après relâchement et stabilisation.
          return;
        }

        // Autres mouvements : capture après stabilisation.
        if (
          !state.pendingCapture ||
          navigationIsActive() ||
          now - state.changedAt < GIF_SETTLE_MS
        ) {
          return;
        }

        state.pendingCapture = false;

        if (signature === state.lastCapturedSignature) {
          return;
        }

        captureFrame();
        markCaptured(signature, cameraQuaternion, now);
      } catch (error) {
        console.error("Capture GIF :", error);

        state.recording = false;

        setStatus(
          `Capture interrompue : ${error?.message || error}. ` +
          "Les images déjà capturées restent exportables.",
          true
        );
      }
    }

  function loadEncoder() {
    if (!encoderPromise) {
      encoderPromise = import(
        "https://cdn.jsdelivr.net/npm/gifenc@1.0.3/+esm"
      ).catch((error) => {
        encoderPromise = null;
        throw error;
      });
    }

    return encoderPromise;
  }

  function start(sceneReady) {
    if (!canNavigate() || state.recording) return;

    const canvas = getCanvas();

    if (!canvas.width || !canvas.height) {
      setStatus("La vue 3D n’est pas prête.", true);
      return;
    }

    const scale = Math.min(
      1,
      GIF_MAX_WIDTH / canvas.width,
      GIF_MAX_HEIGHT / canvas.height
    );

    clearDownload();

    state.width = Math.max(1, Math.round(canvas.width * scale));
    state.height = Math.max(
      1,
      Math.round(canvas.height * scale)
    );
    state.delay = Number(
      document.getElementById("gifDelay").value
    );
    state.frames = [];
    state.lastCapturedSignature = null;
    state.observedSignature = null;
    state.pendingCapture = false;
    state.recording = true;
      state.lastSampleAt = 0;
      state.lastSampleQuaternion = null;

    setStatus("● Capture active — attente de stabilisation…");
    updateUI(sceneReady);
  }

  async function stopAndExport({
    sceneReady,
    nextFrame,
  }) {
    if (
      state.encoding ||
      state.finalizing ||
      isLoading()
    ) {
      return;
    }

    state.finalizing = true;
    updateUI(sceneReady);

    try {
      if (state.recording && !document.hidden) {
        await new Promise((resolve) =>
          setTimeout(resolve, GIF_SETTLE_MS + 50)
        );
        await nextFrame();
      }
    } finally {
      state.recording = false;
      state.pendingCapture = false;
      state.finalizing = false;
    }

    if (!state.frames.length) {
      setStatus(
        "Aucune image capturée. Démarrer la capture puis " +
        "laisser la caméra immobile pendant 600 ms."
      );

      updateUI(sceneReady);
      return;
    }

    state.encoding = true;
    updateUI(sceneReady);

    const frames = state.frames;
    const { width, height, delay } = state;

    try {
      setStatus("Chargement de l’encodeur GIF…");

      const {
        GIFEncoder,
        quantize,
        applyPalette,
      } = await loadEncoder();

      const gif = GIFEncoder();

      for (let index = 0; index < frames.length; index++) {
        setStatus(
          `Encodage du GIF : ${index + 1} / ${frames.length}…`
        );

        await new Promise((resolve) => setTimeout(resolve, 0));

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

      clearDownload();

      state.downloadUrl = URL.createObjectURL(blob);

      const link = document.getElementById("gifDownload");
      const timestamp = new Date()
        .toISOString()
        .replace(/[:.]/g, "-");

      link.href = state.downloadUrl;
      link.download = `vue-3d-${timestamp}.gif`;
      link.textContent = "Télécharger à nouveau le GIF";
      link.hidden = false;
      link.click();

      state.frames = [];

      setStatus(
        `GIF prêt : ${frames.length} image(s) · ` +
        `${width} × ${height} px · ` +
        `${(blob.size / 1024 / 1024).toFixed(2)} Mo`
      );
    } catch (error) {
      console.error("Export GIF :", error);

      setStatus(
        `Export impossible : ${error?.message || error}. ` +
        "Les images sont conservées : tu peux réessayer.",
        true
      );
    } finally {
      state.encoding = false;
      updateUI(sceneReady);
    }
  }

  function cancel(sceneReady) {
    if (state.encoding || state.finalizing) return;

    const hadRecording =
      state.recording || state.frames.length > 0;

    state.recording = false;
    state.frames = [];
    state.pendingCapture = false;
    state.lastCapturedSignature = null;
    state.observedSignature = null;
      state.lastSampleAt = 0;
      state.lastSampleQuaternion = null;

    if (hadRecording) {
      setStatus("Enregistrement annulé.");
    }
      state.lastSampleAt = 0;
      state.lastSampleQuaternion = null;

    updateUI(sceneReady);
  }

  return {
    state,
    updateUI,
    requestCapture,
    processCapture,
    start,
    stopAndExport,
    cancel,
    clearDownload,
  };
}
