import * as THREE from "three";

export function createMeasurementSystem({
  canvas,
  container,
  camera,
  getCenters,
  getModelMatrix,
  getSceneRadius,
  getPrincipalAxis,
  getAbsoluteCoordinates = () => null,
  canMeasure,
  clearNavigation,
  setStatus,
  pickTolerancePx = 4,
  neighborhoodRadiusRatio = 0.005,
  horizontalSnapAngleDeg = 5,
  verticalSnapAngleDeg = 5,
}) {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const yellow = "#ffff00";
  const listeners = [];

  let active = false;
  let disposed = false;
  let start = null;
  let rawEnd = null;

  const raycaster = new THREE.Raycaster();
  const point = new THREE.Vector3();
  const localPoint = new THREE.Vector3();

  const overlay = document.createElementNS(SVG_NS, "svg");
  overlay.setAttribute("aria-hidden", "true");

  Object.assign(overlay.style, {
    position: "absolute",
    inset: "0",
    width: "100%",
    height: "100%",
    pointerEvents: "none",
    zIndex: "2",
    overflow: "hidden",
  });

  function element(tag, attributes) {
    const node = document.createElementNS(SVG_NS, tag);

    for (const [name, value] of Object.entries(attributes)) {
      node.setAttribute(name, String(value));
    }

    overlay.appendChild(node);
    return node;
  }

  const segment = element("line", {
    stroke: yellow,
    "stroke-width": 2,
  });

  const correction = element("line", {
    stroke: yellow,
    "stroke-width": 1,
    "stroke-dasharray": "4 4",
  });

  const startMarker = element("circle", {
    r: 5,
    fill: yellow,
    stroke: "#111",
    "stroke-width": 1.5,
  });

  const endMarker = element("circle", {
    r: 5,
    fill: yellow,
    stroke: "#111",
    "stroke-width": 1.5,
  });

  const snappedMarker = element("circle", {
    r: 8,
    fill: "none",
    stroke: yellow,
    "stroke-width": 2,
  });

  const label = element("text", {
    fill: yellow,
    stroke: "#111",
    "stroke-width": 4,
    "paint-order": "stroke",
    "text-anchor": "middle",
    "font-size": 15,
    "font-family": "system-ui, sans-serif",
    "font-weight": 700,
  });

  // Encart infos (haut-droite)
  const infoGroup = element("g", {});

  const infoBox = document.createElementNS(SVG_NS, "rect");
  infoBox.setAttribute("rx", "8");
  infoBox.setAttribute("ry", "8");
  infoBox.setAttribute("fill", "rgba(0,0,0,0.55)");
  infoBox.setAttribute("stroke", yellow);
  infoBox.setAttribute("stroke-width", "1");
  infoGroup.appendChild(infoBox);

  const infoText = document.createElementNS(SVG_NS, "text");
  infoText.setAttribute("fill", yellow);
  infoText.setAttribute("font-size", "12");
  infoText.setAttribute("font-family", "system-ui, sans-serif");
  infoText.setAttribute("font-weight", "600");
  infoText.setAttribute("paint-order", "stroke");
  infoText.setAttribute("stroke", "#111");
  infoText.setAttribute("stroke-width", "3");
  infoGroup.appendChild(infoText);
  overlay.appendChild(infoGroup);

  container.appendChild(overlay);

  function listen(target, type, handler, options) {
    target.addEventListener(type, handler, options);
    listeners.push(() =>
      target.removeEventListener(type, handler, options)
    );
  }

  function hide(node) {
    node.style.display = "none";
  }

  function hideAll() {
    for (const node of overlay.children) hide(node);
  }

  function reset() {
    start = null;
    rawEnd = null;
    hideAll();
  }

  function deactivate() {
    active = false;
    reset();
  }

  function activate() {
    if (active || disposed || !canMeasure()) return;

    clearNavigation();
    reset();
    active = true;

    setStatus(
      "Mesure : cliquer sur le premier point, puis sur l’extrémité. " +
      "Relâcher Shift pour effacer."
    );
  }

  function isEditing(target) {
    return target instanceof Element &&
      !!target.closest(
        "input, textarea, select, [contenteditable]"
      );
  }

  function consume(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }

    function projectedOf(world) {
      try {
        const p = getAbsoluteCoordinates(world);

        if (
          !p ||
          !Number.isFinite(p.x) ||
          !Number.isFinite(p.y) ||
          !Number.isFinite(p.h)
        ) {
          return null;
        }

        return p;
      } catch {
        return null;
      }
    }

    function formatProjected(p) {
      return `X ${p.x.toFixed(3)}, Y ${p.y.toFixed(3)}, H ${p.h.toFixed(3)} m`;
    }

  // Chercher le centre de splat le plus proche en profondeur,
  // dans un petit cône correspondant à la tolérance en pixels.
  function findSeed(clientX, clientY, centers, matrix) {
    const rect = canvas.getBoundingClientRect();

    if (rect.width <= 0 || rect.height <= 0) return null;

    const ndc = new THREE.Vector2(
      ((clientX - rect.left) / rect.width) * 2 - 1,
      1 - ((clientY - rect.top) / rect.height) * 2
    );

    camera.updateMatrixWorld(true);
    raycaster.setFromCamera(ndc, camera);

    const { origin, direction } = raycaster.ray;
    const cameraForward = camera.getWorldDirection(
      new THREE.Vector3()
    );

    const unitsPerPixelAtUnitDepth =
      2 *
      Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) /
      (rect.height * camera.zoom);

    const offset = new THREE.Vector3();
    let nearestDepth = Infinity;
    let result = null;

    for (let i = 0; i + 2 < centers.length; i += 3) {
      point.set(centers[i], centers[i + 1], centers[i + 2]);

      if (!point.toArray().every(Number.isFinite)) continue;

      point.applyMatrix4(matrix);
      offset.copy(point).sub(origin);

      const depth = offset.dot(cameraForward);

      if (depth < camera.near || depth > camera.far) continue;

      const rayDepth = offset.dot(direction);

      if (rayDepth <= 0 || rayDepth >= nearestDepth) continue;

      const radius =
        pickTolerancePx * unitsPerPixelAtUnitDepth * depth;

      const distanceToRaySq = Math.max(
        0,
        offset.lengthSq() - rayDepth * rayDepth
      );

      if (distanceToRaySq <= radius * radius) {
        nearestDepth = rayDepth;
        result = point.clone();
      }
    }

    return result;
  }

  // Barycentre non pondéré de TOUS les centres dans la sphère.
  // Le calcul se fait dans les coordonnées monde affichées.
  function findBarycenter(seed, centers, matrix) {
    const radius = Math.max(
      getSceneRadius() * neighborhoodRadiusRatio,
      1e-9
    );

    const radiusSq = radius * radius;
    const sum = new THREE.Vector3();
    let count = 0;

    for (let i = 0; i + 2 < centers.length; i += 3) {
      localPoint.set(
        centers[i],
        centers[i + 1],
        centers[i + 2]
      );

      if (
        !Number.isFinite(localPoint.x) ||
        !Number.isFinite(localPoint.y) ||
        !Number.isFinite(localPoint.z)
      ) {
        continue;
      }

      point.copy(localPoint).applyMatrix4(matrix);

      if (point.distanceToSquared(seed) <= radiusSq) {
        sum.add(point);
        count++;
      }
    }

    return count ? sum.divideScalar(count) : seed.clone();
  }

  function snapEndpoint(a, b) {
    const axis = getPrincipalAxis().clone().normalize();
    const delta = b.clone().sub(a);
    const length = delta.length();

    if (length < 1e-12) {
      return { point: b.clone(), kind: "" };
    }

    const verticalAmount = delta.dot(axis);
    const vertical = axis.clone().multiplyScalar(verticalAmount);
    const horizontal = delta.clone().sub(vertical);

    // Écart angulaire à l’axe principal, dans les deux sens.
    const verticalAngle = THREE.MathUtils.radToDeg(
      Math.atan2(horizontal.length(), Math.abs(verticalAmount))
    );

    // Écart angulaire au plan perpendiculaire à l’axe principal.
    const horizontalAngle = THREE.MathUtils.radToDeg(
      Math.atan2(Math.abs(verticalAmount), horizontal.length())
    );

    if (verticalAngle <= verticalSnapAngleDeg) {
      return {
        point: a.clone().add(vertical),
        kind: "vertical",
      };
    }

    if (horizontalAngle <= horizontalSnapAngleDeg) {
      return {
        point: a.clone().add(horizontal),
        kind: "horizontal",
      };
    }

    return { point: b.clone(), kind: "" };
  }

  function project(world) {
    const projected = world.clone().project(camera);

    // Ne pas afficher un point derrière la caméra
    // ou hors des plans de clipping.
    if (
      !Number.isFinite(projected.x) ||
      !Number.isFinite(projected.y) ||
      !Number.isFinite(projected.z) ||
      projected.z < -1 ||
      projected.z > 1
    ) {
      return null;
    }

    const rect = canvas.getBoundingClientRect();
    const containerRect = container.getBoundingClientRect();

    return {
      x: rect.left - containerRect.left +
        (projected.x + 1) * rect.width / 2,
      y: rect.top - containerRect.top +
        (1 - projected.y) * rect.height / 2,
    };
  }

  function drawMarker(node, position) {
    if (!position) return;

    node.setAttribute("cx", position.x);
    node.setAttribute("cy", position.y);
    node.style.display = "";
  }

  function drawLine(node, a, b) {
    if (!a || !b) return;

    node.setAttribute("x1", a.x);
    node.setAttribute("y1", a.y);
    node.setAttribute("x2", b.x);
    node.setAttribute("y2", b.y);
    node.style.display = "";
  }

  function render() {
    hideAll();

    if (!active || !start) return;

    camera.updateMatrixWorld(true);

    const aScreen = project(start);
    drawMarker(startMarker, aScreen);

    if (!rawEnd) return;

    const snapped = snapEndpoint(start, rawEnd);
    const rawScreen = project(rawEnd);
    const endScreen = project(snapped.point);

    drawMarker(endMarker, rawScreen);
    drawLine(segment, aScreen, endScreen);

    if (
      snapped.kind &&
      snapped.point.distanceToSquared(rawEnd) > 1e-18
    ) {
      drawMarker(snappedMarker, endScreen);
      drawLine(correction, rawScreen, endScreen);
    }

    if (!aScreen || !endScreen) return;

    const midpoint = start.clone()
      .add(snapped.point)
      .multiplyScalar(0.5);

    const labelPosition = project(midpoint);
    if (!labelPosition) return;

    const distance = start.distanceTo(snapped.point);

    // Label principal inchangé : distance au-dessus du segment
    label.textContent =
      `${distance.toPrecision(3)} m` +
      (snapped.kind ? ` · ${snapped.kind}` : "");

    label.setAttribute("x", labelPosition.x);
    label.setAttribute("y", labelPosition.y - 14);
    label.style.display = "";

    // Encart infos complémentaires
    const axis = getPrincipalAxis().clone().normalize();
    const delta = snapped.point.clone().sub(start);
    const dVertical = Math.abs(delta.dot(axis));
    const dHorizontal = Math.sqrt(
      Math.max(0, delta.lengthSq() - dVertical * dVertical)
    );

    // Recréer les lignes de texte
    while (infoText.firstChild) {
      infoText.removeChild(infoText.firstChild);
    }

    const lines = [
      `A: (${start.x.toFixed(3)}, ${start.y.toFixed(3)}, ${start.z.toFixed(3)})`,
      `B: (${snapped.point.x.toFixed(3)}, ${snapped.point.y.toFixed(3)}, ${snapped.point.z.toFixed(3)})`,
      `D : ${distance.toPrecision(4)} m`,
      `Dh: ${dHorizontal.toPrecision(4)} m`,
      `Dv: ${dVertical.toPrecision(4)} m`,
    ];

      const projA = projectedOf(start);
      const projB = projectedOf(snapped.point);

      if (projA && projB) {
        lines.push(
          `EPSG:${projA.epsg}`,
          `A: ${formatProjected(projA)}`,
          `B: ${formatProjected(projB)}`
        );
      }

    lines.forEach((line, index) => {
      const tspan = document.createElementNS(SVG_NS, "tspan");
      tspan.setAttribute("x", "0");
      tspan.setAttribute("dy", index === 0 ? "0" : "1.25em");
      tspan.textContent = line;
      infoText.appendChild(tspan);
    });

    const rect = canvas.getBoundingClientRect();
    const containerRect = container.getBoundingClientRect();

    const canvasLeft = rect.left - containerRect.left;
    const canvasTop = rect.top - containerRect.top;
    const canvasRight = canvasLeft + rect.width;

    const margin = 12;
    const pad = 10;
    const lineHeight = 15;
    const textWidth =
      Math.max(...lines.map((line) => line.length)) * 6.9;
    const boxWidth = textWidth + pad * 2;
    const boxHeight = lines.length * lineHeight + pad * 2;

    const boxX = canvasRight - boxWidth - margin;
    const boxY = canvasTop + margin;

    infoGroup.setAttribute("transform", `translate(${boxX}, ${boxY})`);
    infoBox.setAttribute("x", "0");
    infoBox.setAttribute("y", "0");
    infoBox.setAttribute("width", String(boxWidth));
    infoBox.setAttribute("height", String(boxHeight));

    infoText.setAttribute("x", String(pad));
    infoText.setAttribute("y", String(pad + 11));

    infoGroup.style.display = "";
  }

  function onPointerDown(event) {
    if (!event.shiftKey) return;

    // Réserver Shift à la mesure et empêcher le drag habituel.
    consume(event);

    if (event.button !== 0 || !canMeasure()) return;

    activate();

    if (!active) return;

    const centers = getCenters();
    if (!centers?.length) return;

    try {
      const matrix = getModelMatrix();
      const seed = findSeed(
        event.clientX,
        event.clientY,
        centers,
        matrix
      );

      if (!seed) {
        setStatus("Mesure : aucun point trouvé sous le clic.");
        return;
      }

      const barycenter = findBarycenter(seed, centers, matrix);

      if (!start) {
        start = barycenter;
        setStatus("Mesure : cliquer sur l’extrémité.");
      } else {
        // Le premier point reste fixe pour tous les clics suivants.
        rawEnd = barycenter;
        setStatus(
          "Mesure : recliquer pour modifier l’extrémité ; " +
          "relâcher Shift pour effacer."
        );
      }

      render();
    } catch (error) {
      console.error("Mesure :", error);
      setStatus(
        `Mesure impossible : ${error?.message || error}`,
        true
      );
    }
  }

  listen(window, "keydown", (event) => {
    if (event.key !== "Shift") return;

    // Laisser Shift fonctionner normalement dans les champs.
    if (!active && isEditing(event.target)) return;

    activate();

    if (active) consume(event);
  }, true);

  listen(window, "keyup", (event) => {
    if (event.key !== "Shift") return;

    // event.shiftKey reste vrai si l’autre touche Shift est enfoncée.
    if (!event.shiftKey) {
      const wasActive = active;
      deactivate();

      if (wasActive) {
        consume(event);
        setStatus("Mesure effacée.");
      }
    }
  }, true);

  listen(canvas, "pointerdown", onPointerDown, true);

  listen(window, "blur", deactivate);

  listen(document, "visibilitychange", () => {
    if (document.hidden) deactivate();
  });

  hideAll();

  return {
    isActive: () => active,
    reset,
    render,

    dispose() {
      disposed = true;
      deactivate();
      listeners.forEach((remove) => remove());
      overlay.remove();
    },
  };
}
