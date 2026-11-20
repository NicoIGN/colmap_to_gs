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

      label.textContent =
        `${distance.toPrecision(3)} m` +
        (snapped.kind ? ` · ${snapped.kind}` : "");

      label.setAttribute("x", labelPosition.x);
      label.setAttribute("y", labelPosition.y - 14);
      label.style.display = "";

      const axis = getPrincipalAxis().clone().normalize();
      const delta = snapped.point.clone().sub(start);

      const dVertical = Math.abs(delta.dot(axis));
      const dHorizontal = Math.sqrt(
        Math.max(0, delta.lengthSq() - dVertical * dVertical)
      );

      const projA = projectedOf(start);
      const projB = projectedOf(snapped.point);

      const hasProjectedCoordinates =
        projA !== null &&
        projB !== null &&
        Number.isInteger(projA.epsg) &&
        projA.epsg === projB.epsg;

      const lines = [];

      if (hasProjectedCoordinates) {
        // Ne pas afficher les coordonnées locales en doublon.
        lines.push(
          `Coordonnées projetées · EPSG:${projA.epsg}`,
          `A: ${formatProjected(projA)}`,
          `B: ${formatProjected(projB)}`
        );
      } else {
        lines.push(
          "Coordonnées locales",
          `A: (${start.x.toFixed(3)}, ` +
            `${start.y.toFixed(3)}, ${start.z.toFixed(3)})`,
          `B: (${snapped.point.x.toFixed(3)}, ` +
            `${snapped.point.y.toFixed(3)}, ` +
            `${snapped.point.z.toFixed(3)})`
        );
      }

      lines.push(
        `D : ${distance.toPrecision(4)} m`,
        `Dh: ${dHorizontal.toPrecision(4)} m`,
        `Dv: ${dVertical.toPrecision(4)} m`
      );

      while (infoText.firstChild) {
        infoText.removeChild(infoText.firstChild);
      }

      const margin = 12;
      const pad = 10;
      const lineHeight = 15;

      lines.forEach((line, index) => {
        const tspan = document.createElementNS(SVG_NS, "tspan");

        tspan.setAttribute("x", String(pad));
        tspan.setAttribute("dy", index === 0 ? "0" : String(lineHeight));
        tspan.textContent = line;

        infoText.appendChild(tspan);
      });

      const rect = canvas.getBoundingClientRect();
      const containerRect = container.getBoundingClientRect();

      const canvasLeft = rect.left - containerRect.left;
      const canvasTop = rect.top - containerRect.top;
      const canvasRight = canvasLeft + rect.width;

      const textWidth =
        Math.max(...lines.map((line) => line.length)) * 6.9;

      const boxWidth = textWidth + pad * 2;
      const boxHeight = lines.length * lineHeight + pad * 2;

      const boxX = Math.max(
        canvasLeft + margin,
        canvasRight - boxWidth - margin
      );

      const boxY = canvasTop + margin;

      infoGroup.setAttribute(
        "transform",
        `translate(${boxX}, ${boxY})`
      );

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
/*
    function onPointerDown(event) {
      if (!event.shiftKey) return;

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
          console.warn("[MEASUREMENT DEBUG] Aucun centre sous le clic", {
            clientX: event.clientX,
            clientY: event.clientY,
            pickTolerancePx,
            centerCount: Math.floor(centers.length / 3),
          });

          setStatus("Mesure : aucun point trouvé sous le clic.");
          return;
        }

        // Comportement de mesure inchangé.
        const barycenter = findBarycenter(seed, centers, matrix);
        const role = start ? "B" : "A";

        if (!start) {
          start = barycenter;
          setStatus("Mesure : cliquer sur l’extrémité.");
        } else {
          rawEnd = barycenter;
          setStatus(
            "Mesure : recliquer pour modifier l’extrémité ; " +
            "relâcher Shift pour effacer."
          );
        }

        // Diagnostic indépendant : une erreur ici ne bloque pas la mesure.
        let groupOpened = false;

        try {
          console.group(`[MEASUREMENT DEBUG] Point ${role}`);
          groupOpened = true;

          const xyz = (p) => ({ x: p.x, y: p.y, z: p.z });
          const finiteVector = (p) =>
            [p.x, p.y, p.z].every(Number.isFinite);

          // THREE stocke les Matrix4 en column-major.
          // Fournir aussi des lignes lisibles pour éviter toute ambiguïté.
          const rowsOf = (m) => {
            const e = m.elements;
            return [
              [e[0], e[4], e[8], e[12]],
              [e[1], e[5], e[9], e[13]],
              [e[2], e[6], e[10], e[14]],
              [e[3], e[7], e[11], e[15]],
            ];
          };

          const determinant = matrix.determinant();
          const invertible =
            matrix.elements.every(Number.isFinite) &&
            Number.isFinite(determinant) &&
            determinant !== 0;

          const inverse = invertible ? matrix.clone().invert() : null;
          const localSeed = inverse
            ? seed.clone().applyMatrix4(inverse)
            : null;
          const localBarycenter = inverse
            ? barycenter.clone().applyMatrix4(inverse)
            : null;

          const snapped = rawEnd ? snapEndpoint(start, rawEnd) : null;
          const selectedPoint = snapped ? snapped.point : start;

          // Référence QGIS facultative, définie dans la console.
          const referenceInput = window.__measurementReference;
          const reference =
            referenceInput &&
            Number.isInteger(referenceInput.epsg) &&
            Number.isFinite(referenceInput.x) &&
            Number.isFinite(referenceInput.y)
              ? {
                  epsg: referenceInput.epsg,
                  x: referenceInput.x,
                  y: referenceInput.y,
                  h: Number.isFinite(referenceInput.h)
                    ? referenceInput.h
                    : null,
                }
              : null;

          const report = {
            version: 1,
            time: new Date().toISOString(),
            role,
            click: {
              clientX: event.clientX,
              clientY: event.clientY,
              pickTolerancePx,
            },
            scene: {
              centerCount: Math.floor(centers.length / 3),
              centerArrayType: centers.constructor.name,
              sceneRadius: getSceneRadius(),
              neighborhoodRadiusRatio,
              neighborhoodRadius: Math.max(
                getSceneRadius() * neighborhoodRadiusRatio,
                1e-9
              ),
              principalAxis: xyz(getPrincipalAxis()),
              flipUpChecked:
                document.getElementById("flipUp")?.checked ?? null,
            },
            camera: {
              position: xyz(camera.position),
              quaternion: camera.quaternion.toArray(),
              up: xyz(camera.up),
              fov: camera.fov,
              zoom: camera.zoom,
              near: camera.near,
              far: camera.far,
            },
            model: {
              determinant,
              matrixRows: rowsOf(matrix),
              inverseRows: inverse ? rowsOf(inverse) : null,
            },
            selection: {
              seedDisplay: xyz(seed),
              seedModel: localSeed ? xyz(localSeed) : null,
              barycenterDisplay: xyz(barycenter),
              barycenterModel: localBarycenter
                ? xyz(localBarycenter)
                : null,
              seedToBarycenterDistance: seed.distanceTo(barycenter),
              seedToBarycenterVector: xyz(
                barycenter.clone().sub(seed)
              ),
              startDisplay: xyz(start),
              rawEndDisplay: rawEnd ? xyz(rawEnd) : null,
              effectiveDisplay: xyz(selectedPoint),
              snapKind: snapped?.kind ?? "",
              snapDistance: rawEnd
                ? rawEnd.distanceTo(selectedPoint)
                : 0,
            },
            reference,
            variants: [],
          };

          function evaluate(name, candidate, hypothesis) {
            const entry = {
              name,
              hypothesis,
              input: xyz(candidate),
              projected: null,
              error: null,
            };

            try {
              if (!finiteVector(candidate)) {
                throw new Error("Coordonnées d’entrée non finies.");
              }

              // Cloner pour ne jamais modifier les points de mesure.
              const result = getAbsoluteCoordinates(candidate.clone());

              if (!result) {
                throw new Error(
                  "Aucune coordonnée absolue retournée : géoréférencement absent ?"
                );
              }

              if (![result.x, result.y, result.h].every(Number.isFinite)) {
                throw new Error("Coordonnées projetées non finies.");
              }

              entry.projected = {
                epsg: result.epsg ?? null,
                x: result.x,
                y: result.y,
                h: result.h,
              };
            } catch (error) {
              entry.error = String(error?.message ?? error);
            }

            report.variants.push(entry);
            return entry;
          }

          // Référence de comparaison = résultat réellement utilisé par la mesure.
          const current = evaluate(
            "ACTUEL",
            selectedPoint,
            "Point affiché après accrochage, conversion actuelle."
          );

          evaluate(
            "GRAINE",
            seed,
            "Centre retenu par le picking, avant barycentre."
          );

          evaluate(
            "BARYCENTRE",
            barycenter,
            "Point retenu avant accrochage horizontal/vertical."
          );

          if (inverse) {
            evaluate(
              "ENTREE_M_INVERSE",
              selectedPoint.clone().applyMatrix4(inverse),
              "Retirer une matrice d’affichage avant le convertisseur actuel."
            );

            report.model.roundtripError = barycenter.distanceTo(
              localBarycenter.clone().applyMatrix4(matrix)
            );
          }

          evaluate(
            "ENTREE_M_SUPPLEMENTAIRE",
            selectedPoint.clone().applyMatrix4(matrix),
            "Ajouter une matrice d’affichage avant le convertisseur actuel."
          );

          evaluate(
            "ENTREE_FLIP_X_180",
            new THREE.Vector3(
              selectedPoint.x,
              -selectedPoint.y,
              -selectedPoint.z
            ),
            "Tester une inversion Y/Z supplémentaire."
          );

          evaluate(
            "ENTREE_PERMUTATION_Y_Z",
            new THREE.Vector3(
              selectedPoint.x,
              selectedPoint.z,
              selectedPoint.y
            ),
            "Tester une confusion Y-up / Z-up ; permutation avec réflexion."
          );

          evaluate(
            "ENTREE_ROTATION_X_90",
            new THREE.Vector3(
              selectedPoint.x,
              -selectedPoint.z,
              selectedPoint.y
            ),
            "Tester une rotation supplémentaire de +90° autour de X."
          );

          evaluate(
            "ENTREE_ROTATION_X_MOINS_90",
            new THREE.Vector3(
              selectedPoint.x,
              selectedPoint.z,
              -selectedPoint.y
            ),
            "Tester une rotation supplémentaire de -90° autour de X."
          );

          function difference(a, b) {
            const dx = a.x - b.x;
            const dy = a.y - b.y;

            return {
              dx,
              dy,
              dh: Number.isFinite(b.h) ? a.h - b.h : null,
              horizontal: Math.hypot(dx, dy),
            };
          }

          for (const entry of report.variants) {
            const p = entry.projected;
            if (!p) continue;

            if (
              current.projected &&
              Number.isInteger(p.epsg) &&
              p.epsg === current.projected.epsg
            ) {
              entry.deltaFromCurrent = difference(p, current.projected);
            }

            if (reference && p.epsg === reference.epsg) {
              // Convention : calculé - référence QGIS.
              entry.errorToReference = difference(p, reference);
            }
          }

          console.log("Sélection :", report.selection);
          console.log("Scène :", report.scene);
          console.log("Caméra :", report.camera);
          console.log("Matrice modèle — lignes :", report.model.matrixRows);
          console.log("Inverse — lignes :", report.model.inverseRows);

          console.table(
            report.variants.map((entry) => ({
              variante: entry.name,
              EPSG: entry.projected?.epsg ?? "",
              X: entry.projected?.x ?? null,
              Y: entry.projected?.y ?? null,
              H: entry.projected?.h ?? null,
              deltaX_actuel: entry.deltaFromCurrent?.dx ?? null,
              deltaY_actuel: entry.deltaFromCurrent?.dy ?? null,
              deltaH_actuel: entry.deltaFromCurrent?.dh ?? null,
              distanceXY_actuel:
                entry.deltaFromCurrent?.horizontal ?? null,
              erreurX_QGIS: entry.errorToReference?.dx ?? null,
              erreurY_QGIS: entry.errorToReference?.dy ?? null,
              erreurXY_QGIS:
                entry.errorToReference?.horizontal ?? null,
              erreur: entry.error ?? "",
            }))
          );

          if (report.selection.seedToBarycenterDistance > 1) {
            console.warn(
              "[MEASUREMENT DEBUG] Le barycentre déplace le point de plus de 1 m.",
              {
                distance: report.selection.seedToBarycenterDistance,
                radius: report.scene.neighborhoodRadius,
              }
            );
          }

          if (!reference) {
            console.info(
              "Pas de référence QGIS : les deltas comparent les variantes " +
              "au calcul actuel, pas à la position réelle."
            );
          }

          console.info(
            "Ces variantes testent le chemin d’affichage/conversion. " +
            "Les conventions Spirula ne sont pas testées sans scene_transform.json."
          );

          // Snapshot texte : les valeurs ne changent pas après le clic.
          const json = JSON.stringify(report, null, 2);
          window.__measurementDebugLast = json;

          console.log("[MEASUREMENT DEBUG] RAPPORT JSON\n" + json);
          console.info(
            "Dernier rapport disponible dans window.__measurementDebugLast"
          );
        } catch (error) {
          console.error(
            "[MEASUREMENT DEBUG] Échec du diagnostic ; mesure conservée :",
            error
          );
        } finally {
          if (groupOpened) console.groupEnd();
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
 */
    
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
