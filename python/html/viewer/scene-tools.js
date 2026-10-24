import * as THREE from "three";
import { readGeorefFile } from "./georef.js";

/**
 * Géoréférencement, GPX et encart d’informations.
 *
 * getState() retourne :
 * {
 *   ready, blocked, entity, box, radius, flipUp
 * }
 *
 * Les coordonnées du GPX passent par :
 * WGS84 -> projeté absolu -> modèle local -> monde affiché.
 */
export function createSceneTools({
  getState,
  camera,
  target,
  getPrincipalAxis,
  clearNavigation,
  resetMeasurement,
  syncCamera,
  requestCapture,
  refreshAppUI,
  setStatus,
}) {
  const $ = (id) => document.getElementById(id);
  const removers = [];

  let disposed = false;
  let serial = 0;
  let georef = null;
  let track = null;
  let follow = false;
  let auto = false;
  let progress = 0;

  const configuredSpeed = Number($("gpsAutoSpeed")?.value);
  let speed =
    Number.isFinite(configuredSpeed) && configuredSpeed > 0
      ? configuredSpeed
      : 1;

  const finiteVector = (point) =>
    point &&
    [point.x, point.y, point.z].every(Number.isFinite);

  function listen(element, type, handler) {
    if (!element) return;

    element.addEventListener(type, handler);
    removers.push(() => element.removeEventListener(type, handler));
  }

  function write(id, text) {
    const element = $(id);
    if (element) element.textContent = text;
  }

  function setExpanded(expanded) {
    const panel = $("sceneInfo");
    const button = $("toggleSceneInfo");
    const body = $("sceneInfoBody");

    panel?.setAttribute("aria-expanded", String(expanded));

    if (body) {
      body.hidden = !expanded;
    }

    if (button) {
      button.setAttribute("aria-expanded", String(expanded));
      button.setAttribute(
        "aria-label",
        expanded
          ? "Replier l’encart d’informations"
          : "Déplier l’encart d’informations"
      );
      button.textContent = expanded ? "−" : "+";
    }
  }

  listen($("toggleSceneInfo"), "click", () => {
    clearNavigation();

    const expanded =
      $("toggleSceneInfo")?.getAttribute("aria-expanded") !== "false";

    setExpanded(!expanded);
  });

  setExpanded(
    $("toggleSceneInfo")?.getAttribute("aria-expanded") !== "false"
  );

  function setSceneInfo({ name = null, count = 0 } = {}) {
    write("sceneFileName", name || "Aucun fichier chargé");
    write(
      "sceneSplatCount",
      count > 0 ? count.toLocaleString("fr-FR") : "—"
    );
  }

  function updateGeorefInfo() {
    if (!georef) {
      write("sceneGeorefInfo", "Aucune référence chargée");
      return;
    }

    const origin = georef.origin;

    write(
      "sceneGeorefInfo",
      [
        `EPSG:${georef.epsg} · mètres`,
        origin
          ? `Origine : ${origin.x.toFixed(3)}, ` +
            `${origin.y.toFixed(3)}, ${origin.h.toFixed(3)}`
          : "Origine non renseignée",
      ].join("\n")
    );
  }

  function removeDiagnostic() {
    $("gpsBBoxDetails")?.remove();
    // Nettoyage d’un diagnostic créé par l’ancienne version.
    $("gpsBBoxDiagnostic")?.remove();
  }

  function showDiagnostic(text, accepted) {
    let details = $("gpsBBoxDetails");

    if (!details) {
      details = document.createElement("details");
      details.id = "gpsBBoxDetails";

      const summary = document.createElement("summary");
      summary.textContent = "Diagnostic GPX / bbox";

      const pre = document.createElement("pre");
      pre.id = "gpsBBoxDiagnostic";

      Object.assign(pre.style, {
        whiteSpace: "pre-wrap",
        overflowWrap: "anywhere",
        fontSize: "11px",
        lineHeight: "1.5",
        maxHeight: "260px",
        overflowY: "auto",
        padding: "8px",
        border: "1px solid",
        borderRadius: "6px",
      });

      details.append(summary, pre);

      const parent =
        $("sceneInfoBody") || $("gpsSection") || $("panelBody");

      parent?.appendChild(details);
    }

    const pre = $("gpsBBoxDiagnostic");

    if (pre) {
      pre.textContent = text;
      pre.style.borderColor = accepted ? "#81c784" : "#e57373";
    }
  }

  function refreshUI() {
    if (disposed) return;

    const state = getState();
    const ready = Boolean(
      state.ready && georef && track?.pointsWorld.length >= 2
    );

    if ($("gpxInput")) {
      $("gpxInput").disabled =
        state.blocked || !state.ready || !georef;
    }

    for (const id of [
      "gpsFollowEnabled",
      "gpsAutoEnabled",
      "gpsAutoSpeed",
    ]) {
      if ($(id)) {
        $(id).disabled = state.blocked || !ready;
      }
    }

    if ($("gpsFollowEnabled")) {
      $("gpsFollowEnabled").checked = follow;
    }

    if ($("gpsAutoEnabled")) {
      $("gpsAutoEnabled").checked = auto;
    }
  }

  function resetTrack({ diagnostic = true } = {}) {
    track = null;
    follow = false;
    auto = false;
    progress = 0;

    if (diagnostic) removeDiagnostic();

    refreshUI();
  }

  function reset() {
    serial++;
    georef = null;
    resetTrack();
    setSceneInfo();
    updateGeorefInfo();
  }

  function getAbsoluteCoordinates(worldPoint) {
    const state = getState();

    if (!georef || !state.ready || !state.entity) return null;

    const inverse = new THREE.Matrix4().fromArray(
      state.entity.getWorldTransform().data
    );

    const determinant = inverse.determinant();

    if (
      !Number.isFinite(determinant) ||
      Math.abs(determinant) < 1e-12
    ) {
      throw new Error("Transformation du modèle non inversible.");
    }

    const model = new THREE.Vector3(
      worldPoint.x,
      worldPoint.y,
      worldPoint.z
    ).applyMatrix4(inverse.invert());

    const projected = georef.worldToProjected(
      model.x,
      model.y,
      model.z
    );

    if (
      !projected ||
      ![projected.x, projected.y, projected.h].every(Number.isFinite)
    ) {
      throw new Error("Coordonnées projetées invalides.");
    }

    return { epsg: georef.epsg, ...projected };
  }

  function parseGpx(text) {
    const document = new DOMParser().parseFromString(
      text,
      "application/xml"
    );

    if (
      document.getElementsByTagNameNS("*", "parsererror").length ||
      document.documentElement.localName !== "gpx"
    ) {
      throw new Error("Document GPX invalide.");
    }

    const segments = Array.from(
      document.getElementsByTagNameNS("*", "trkseg")
    ).filter(
      (segment) =>
        segment.getElementsByTagNameNS("*", "trkpt").length > 0
    );

    if (segments.length !== 1) {
      throw new Error(
        "Le GPX doit contenir exactement un segment non vide."
      );
    }

    const nodes = Array.from(
      segments[0].getElementsByTagNameNS("*", "trkpt")
    );

    if (nodes.length < 2) {
      throw new Error("Le GPX doit contenir au moins deux points.");
    }

    return nodes.map((node, index) => {
      const altitude = Array.from(node.children).find(
        (child) => child.localName === "ele"
      );

      const values = [
        node.getAttribute("lat"),
        node.getAttribute("lon"),
        altitude?.textContent,
      ];

      if (
        values.some(
          (value) => value == null || value.trim() === ""
        )
      ) {
        throw new Error(
          `Point GPX ${index + 1} : latitude, longitude ou altitude absente.`
        );
      }

      const [lat, lon, h] = values.map(Number);

      if (
        ![lat, lon, h].every(Number.isFinite) ||
        lat < -90 ||
        lat > 90 ||
        lon < -180 ||
        lon > 180
      ) {
        throw new Error(
          `Point GPX ${index + 1} : coordonnées invalides.`
        );
      }

      return { lat, lon, h };
    });
  }

  function buildTrack(sourcePoints) {
    const state = getState();

    if (!georef || !state.entity) {
      throw new Error("Scène géoréférencée indisponible.");
    }

    const matrix = new THREE.Matrix4().fromArray(
      state.entity.getWorldTransform().data
    );

    const pointsProjected = [];
    const pointsModel = [];
    const pointsWorld = [];
    const distances = [];

    let length = 0;

    sourcePoints.forEach((point, index) => {
      const projected = georef.geographicToProjected(
        point.lat,
        point.lon,
        point.h
      );

      const model = georef.projectedToWorld(
        projected.x,
        projected.y,
        projected.h
      );

      const modelPoint = new THREE.Vector3(
        model.x,
        model.y,
        model.h
      );

      const worldPoint = modelPoint.clone().applyMatrix4(matrix);

      if (!finiteVector(worldPoint)) {
        throw new Error(
          `Point GPX ${index + 1} : conversion monde invalide.`
        );
      }

      const previous = pointsWorld[pointsWorld.length - 1];

      if (previous) {
        const distance = previous.distanceTo(worldPoint);

        if (distance < 1e-6) return;

        length += distance;
      }

      pointsProjected.push(
        new THREE.Vector3(projected.x, projected.y, projected.h)
      );
      pointsModel.push(modelPoint);
      pointsWorld.push(worldPoint);
      distances.push(length);
    });

    if (pointsWorld.length < 2 || length < 1e-6) {
      throw new Error("Trajectoire GPX sans déplacement exploitable.");
    }

    return {
      sourcePoints,
      pointsProjected,
      pointsModel,
      pointsWorld,
      distances,
      length,
    };
  }

    function validateTrack(candidate) {
      const state = getState();
      const box = state.box;

      if (
        box.isEmpty() ||
        !finiteVector(box.min) ||
        !finiteVector(box.max)
      ) {
        throw new Error("BBox de scène vide ou invalide.");
      }

      const margin = Math.max(state.radius * 0.1, 0.5);
      const expanded = box.clone().expandByScalar(margin);
      const originalLength = candidate.length;
      const originalCount = candidate.pointsWorld.length;

      /*
       * Intersection segment / bbox par intervalles.
       * Retourne les paramètres d’entrée et de sortie dans [0, 1].
       */
      function clipSegment(a, b) {
        let enter = 0;
        let exit = 1;

        for (const axis of ["x", "y", "z"]) {
          const delta = b[axis] - a[axis];

          if (Math.abs(delta) < 1e-12) {
            if (
              a[axis] < expanded.min[axis] ||
              a[axis] > expanded.max[axis]
            ) {
              return null;
            }

            continue;
          }

          let near = (expanded.min[axis] - a[axis]) / delta;
          let far = (expanded.max[axis] - a[axis]) / delta;

          if (near > far) {
            [near, far] = [far, near];
          }

          enter = Math.max(enter, near);
          exit = Math.min(exit, far);

          if (enter > exit) return null;
        }

        return { enter, exit };
      }

      let current = null;
      let best = null;
      let passageCount = 0;

      function finishPassage() {
        if (
          current &&
          current.pointsWorld.length >= 2 &&
          current.length > 1e-6
        ) {
          passageCount++;

          if (!best || current.length > best.length) {
            best = current;
          }
        }

        current = null;
      }

      function appendPoint(index, t) {
        const interpolate = (points) =>
          points[index].clone().lerp(points[index + 1], t);

        const world = interpolate(candidate.pointsWorld);

        if (!current) {
          current = {
            pointsProjected: [],
            pointsModel: [],
            pointsWorld: [],
            distances: [],
            length: 0,
          };
        }

        const previous =
          current.pointsWorld[current.pointsWorld.length - 1];

        if (previous) {
          const distance = previous.distanceTo(world);

          if (distance < 1e-6) return;

          current.length += distance;
        }

        current.pointsProjected.push(
          interpolate(candidate.pointsProjected)
        );
        current.pointsModel.push(
          interpolate(candidate.pointsModel)
        );
        current.pointsWorld.push(world);
        current.distances.push(current.length);
      }

      for (let index = 0; index < originalCount - 1; index++) {
        const a = candidate.pointsWorld[index];
        const b = candidate.pointsWorld[index + 1];

        if (!finiteVector(a) || !finiteVector(b)) {
          throw new Error(
            `Segment GPX ${index + 1} : coordonnées non finies.`
          );
        }

        const clipped = clipSegment(a, b);

        if (
          !clipped ||
          (clipped.exit - clipped.enter) * a.distanceTo(b) < 1e-6
        ) {
          finishPassage();
          continue;
        }

        // Une entrée à l’intérieur du segment débute un nouveau passage.
        if (clipped.enter > 0) {
          finishPassage();
        }

        appendPoint(index, clipped.enter);
        appendPoint(index, clipped.exit);

        // Une sortie avant la fin du segment termine ce passage.
        if (clipped.exit < 1) {
          finishPassage();
        }
      }

      finishPassage();

      const vectorText = (vector) =>
        vector.toArray().map((value) => value.toFixed(3)).join(", ");

      const describeBox = (title, value) =>
        `${title}\n` +
        `min : ${vectorText(value.min)}\n` +
        `max : ${vectorText(value.max)}`;

      const originalBox = new THREE.Box3().setFromPoints(
        candidate.pointsWorld
      );

      if (!best) {
        showDiagnostic(
          [
            "GPX refusé : aucun tronçon de longueur exploitable",
            "n’intersecte la bbox 3D élargie.",
            `EPSG:${georef.epsg}`,
            `Origine : ${JSON.stringify(georef.origin)}`,
            "",
            describeBox("BBOX SCÈNE", box),
            "",
            describeBox("BBOX GPX COMPLET", originalBox),
            "",
            `Marge par face : ${margin.toFixed(3)} m`,
            "Vérifier le recalage et la référence verticale.",
          ].join("\n"),
          false
        );

        console.warn(
          "[GPX / découpage] Aucun passage dans la bbox 3D élargie.",
          {
            sceneMin: box.min.toArray(),
            sceneMax: box.max.toArray(),
            gpxMin: originalBox.min.toArray(),
            gpxMax: originalBox.max.toArray(),
            margin,
          }
        );

        return false;
      }

      /*
       * Remplacer la trajectoire utilisée pour le suivi.
       *
       * Ne pas remplacer candidate.sourcePoints :
       * on conserve le GPX original pour un recalcul après flipUp.
       */
      candidate.pointsProjected = best.pointsProjected;
      candidate.pointsModel = best.pointsModel;
      candidate.pointsWorld = best.pointsWorld;
      candidate.distances = best.distances;
      candidate.length = best.length;

      const retainedBox = new THREE.Box3().setFromPoints(
        candidate.pointsWorld
      );

      showDiagnostic(
        [
          "GPX accepté : tronçon découpé dans la scène.",
          `EPSG:${georef.epsg}`,
          `Origine : ${JSON.stringify(georef.origin)}`,
          "",
          `Parcours complet : ${originalLength.toFixed(1)} m`,
          `Tronçon conservé : ${candidate.length.toFixed(1)} m`,
          `Points conservés / interpolés : ${candidate.pointsWorld.length}`,
          `Passages continus détectés : ${passageCount}`,
          passageCount > 1
            ? "Seul le passage le plus long est utilisé."
            : "Un seul passage continu est utilisé.",
          "",
          describeBox("BBOX SCÈNE", box),
          "",
          describeBox("BBOX TRONÇON CONSERVÉ", retainedBox),
          "",
          `Marge par face : ${margin.toFixed(3)} m`,
          "Découpage dans le monde affiché, après matrice du modèle.",
          "La bbox scène utilise les percentiles 1 % / 99 %.",
          "Une intersection ne garantit pas un bon géoréférencement.",
        ].join("\n"),
        true
      );

      console.info("[GPX / découpage] Tronçon conservé.", {
        originalPoints: originalCount,
        retainedPoints: candidate.pointsWorld.length,
        originalLength,
        retainedLength: candidate.length,
        continuousPassages: passageCount,
        margin,
      });

      return true;
    }

  async function importFile(file) {
    if (disposed) return;

    const initial = getState();

    if (!initial.ready || !initial.entity || initial.blocked) {
      setStatus("Charge d’abord une scène disponible.", true);
      return;
    }

    const token = ++serial;
    const entity = initial.entity;

    const valid = () => {
      const current = getState();

      return (
        !disposed &&
        token === serial &&
        current.ready &&
        current.entity === entity
      );
    };

    try {
      if (/\.json$/i.test(file.name)) {
        setStatus(`Lecture de ${file.name}…`);

        const reference = await readGeorefFile(file);

        if (!valid()) return;

        georef = reference;
        resetTrack();
        updateGeorefInfo();
        clearNavigation();
        resetMeasurement();
        refreshAppUI();

        setStatus(
          `Référence associée — EPSG:${georef.epsg}. ` +
          "Tu peux importer le GPX WGS84."
        );

        return;
      }

      if (!georef) {
        throw new Error(
          "Associe le JSON de géoréférencement avant le GPX."
        );
      }

      setStatus(`Lecture de ${file.name}…`);

      const text = await file.text();

      if (!valid()) return;

      const candidate = buildTrack(parseGpx(text));

      // L’ancien suivi est arrêté avant de publier le nouveau.
      resetTrack();

      if (!validateTrack(candidate)) {
          setStatus(
            "GPX refusé : aucun passage exploitable dans la bbox 3D de la scène. " +
            "Consulte le diagnostic dans cet encart.",
            "warning"
          );

        refreshAppUI();
        return;
      }

      track = candidate;
      clearNavigation();
      refreshAppUI();

      if ($("gpsSection")) $("gpsSection").open = true;

      setStatus(
        `${file.name} — ${track.pointsWorld.length} points · ` +
        `${track.length.toFixed(1)} m.`,
        "success"
      );
    } catch (error) {
      if (!valid()) return;

      console.error("Géoréférencement / GPX :", error);
      setStatus(
        `Import impossible : ${error?.message || error}`,
        true
      );
      refreshAppUI();
    }
  }

  /**
   * Recalcul après modification de la matrice du modèle, dont flipUp.
   */
  function rebuildTrack() {
    if (!track) return;

    try {
      const candidate = buildTrack(track.sourcePoints);

      if (!validateTrack(candidate)) {
        resetTrack({ diagnostic: false });
          setStatus(
            "Suivi GPS arrêté : aucun passage dans la bbox après transformation.",
            "warning"
          );
      } else {
        track = candidate;
      }
    } catch (error) {
      resetTrack();
      setStatus(
        `Recalcul GPX impossible : ${error?.message || error}`,
        true
      );
    }

    refreshAppUI();
  }

  function sample(t) {
    const distance = THREE.MathUtils.clamp(t, 0, 1) * track.length;

    let low = 0;
    let high = track.distances.length - 1;

    while (high - low > 1) {
      const middle = (low + high) >> 1;

      if (track.distances[middle] <= distance) low = middle;
      else high = middle;
    }

    const start = track.distances[low];
    const end = track.distances[high];
    const fraction = (distance - start) / (end - start);

    return track.pointsWorld[low].clone().lerp(
      track.pointsWorld[high],
      fraction
    );
  }

  function frame(dt) {
    const state = getState();

    if (
      !track ||
      !state.ready ||
      state.blocked ||
      (!follow && !auto)
    ) {
      return;
    }

    if (auto) {
      progress = Math.min(
        1,
        progress + speed * Math.max(0, dt) / track.length
      );
    }

    const position = sample(progress);
    const lookDistance = Math.max(state.radius * 0.03, 2);
    const step = Math.min(1, lookDistance / track.length);

    let direction;

    if (progress < 1) {
      direction = sample(Math.min(1, progress + step)).sub(position);
    } else {
      direction = position.clone().sub(sample(Math.max(0, 1 - step)));
    }

    const up = getPrincipalAxis().clone().normalize();
    const eyeHeight = Math.max(state.radius * 0.01, 0.3);

    camera.position.copy(position).addScaledVector(up, eyeHeight);

    if (direction.lengthSq() > 1e-12) {
      target.copy(camera.position).addScaledVector(
        direction.normalize(),
        lookDistance
      );
    }

    syncCamera();
    requestCapture();

    if (auto && progress >= 1) {
      auto = false;
      refreshUI();
      setStatus("Fin de la trajectoire GPS.");
    }
  }

  listen($("gpxInput"), "change", async (event) => {
    const file = event.target.files?.[0];

    try {
      if (file) await importFile(file);
    } finally {
      event.target.value = "";
    }
  });

  listen($("gpsFollowEnabled"), "change", () => {
    follow = Boolean(track && $("gpsFollowEnabled").checked);
    clearNavigation();
    resetMeasurement();
    refreshUI();
  });

  listen($("gpsAutoEnabled"), "change", () => {
    auto = Boolean(track && $("gpsAutoEnabled").checked);

    if (auto) {
      progress = 0;
      clearNavigation();
      resetMeasurement();
    }

    refreshUI();
  });

  listen($("gpsAutoSpeed"), "input", () => {
    const value = Number($("gpsAutoSpeed").value);

    if (Number.isFinite(value) && value > 0) speed = value;

    if ($("gpsAutoSpeedValue")) {
      $("gpsAutoSpeedValue").value = String(speed);
    }
  });

  updateGeorefInfo();
  refreshUI();

  return {
    setSceneInfo,
    getAbsoluteCoordinates,
    importFile,
    refreshUI,
    rebuildTrack,
    frame,
    reset,

    dispose() {
      reset();
      disposed = true;

      for (const remove of removers) remove();
      removers.length = 0;
    },
  };
}
