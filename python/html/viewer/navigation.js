import * as THREE from "three";

export function createNavigationSystem({
  canvas,
  camera,
  target,
  inputBindings = {},
  getSceneRadius,
  getSceneCenter,
  getCanNavigate,
  getPrincipalAxis,
  getPrincipalAxisName,
  isRotationLocked,
  getSpeedValue,
  getKeyboardLayout,
  navigateWithCollision,
  syncCamera,
  requestGifCapture,
  selectPointInView,
  setStatus,
}) {
  const pressedKeys = new Set();
  const heldButtons = new Map();

  const lookBinding = {
    button: 0,
    modifier: "ctrlKey",
    ...inputBindings.look,
  };

  // event.key est normalisé en minuscules dans actionForKey().
  const commonKeys = Object.fromEntries(
    Object.entries({
      ArrowUp: "forward",
      ArrowDown: "backward",
      ArrowLeft: "left",
      ArrowRight: "right",
      ...inputBindings.arrows,
    }).map(([key, action]) => [key.toLowerCase(), action])
  );

  let mouseDrag = null;
  let wheelTimer = null;
  let lastSceneBox = null;

  // Dernière position connue de la souris sur le canvas (px CSS).
  // Sert à orienter la molette selon l’axe pointé.
  let lastPointer = {
    x: canvas.clientWidth * 0.5,
    y: canvas.clientHeight * 0.5,
  };

  let mouseControlsInstalled = false;
  let heldButtonsInstalled = false;
  let keyboardInstalled = false;

  function actionForKey(key) {
    const layout = getKeyboardLayout() === "azerty"
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

  function isEditing(element) {
    return element instanceof Element &&
      !!element.closest(
        "input, textarea, select, button, summary, [contenteditable]"
      );
  }

  function finishMouseDrag() {
    const drag = mouseDrag;

    if (!drag) return;

    // Effacer avant releasePointerCapture(), qui peut déclencher
    // lostpointercapture.
    mouseDrag = null;

    if (canvas.hasPointerCapture(drag.id)) {
      canvas.releasePointerCapture(drag.id);
    }

    if (drag.moved) {
      requestGifCapture();
    }
  }

  function clearNavigation() {
    const wasMoving =
      heldButtons.size > 0 ||
      [...pressedKeys].some((key) => !!actionForKey(key));

    pressedKeys.clear();

    heldButtons.forEach((button) => {
      button.classList.remove("held");
    });

    heldButtons.clear();
    finishMouseDrag();

    clearTimeout(wheelTimer);
    wheelTimer = null;

    if (wasMoving) {
      requestGifCapture();
    }
  }

  /**
   * Orbite explicite : déplace la caméra autour de target.
   * Utilisée par les boutons d’orbite encore présents.
   */
  function rotateAroundAxis(axis, angle) {
    if (!angle || axis.lengthSq() < 1e-12) return;

    const normalizedAxis = axis.clone().normalize();

    if (isRotationLocked()) {
      const principalAxis = getPrincipalAxis().clone().normalize();
      const alignment = Math.abs(
        normalizedAxis.dot(principalAxis)
      );

      if (alignment < 1 - 1e-8) return;
    }

    const rotation = new THREE.Quaternion()
      .setFromAxisAngle(normalizedAxis, angle);

    const offset = camera.position.clone()
      .sub(target)
      .applyQuaternion(rotation);

    camera.position.copy(target).add(offset);
    camera.up.applyQuaternion(rotation).normalize();

    camera.lookAt(target);
    camera.updateMatrixWorld(true);
  }

  /**
   * Rotation du regard sur place.
   *
   * dx et dy sont des angles en radians.
   * camera.position reste inchangée.
   * Aucun point de scène n’est sélectionné.
   */
  function orbitLook(dx, dy) {
    if (!dx && !dy) return;

    const direction = target.clone().sub(camera.position);
    const distance = direction.length();

    if (distance < 1e-12) return;

    const locked = isRotationLocked();
    const up = camera.up.clone().normalize();

    direction.normalize();

    if (dx) {
      const yawAxis = locked
        ? getPrincipalAxis().clone().normalize()
        : new THREE.Vector3(0, 1, 0)
            .applyQuaternion(camera.quaternion)
            .normalize();

      let yawAngle = dx;

      if (locked && yawAxis.dot(up) < 0) {
        yawAngle = -yawAngle;
      }

      const yaw = new THREE.Quaternion()
        .setFromAxisAngle(yawAxis, yawAngle);

      direction.applyQuaternion(yaw);
      up.applyQuaternion(yaw);
    }

    if (dy && !locked) {
      const right = new THREE.Vector3()
        .crossVectors(direction, up);

      if (right.lengthSq() > 1e-12) {
        right.normalize();

        const pitch = new THREE.Quaternion()
          .setFromAxisAngle(right, dy);

        direction.applyQuaternion(pitch);
        up.applyQuaternion(pitch);
      }
    }

    target.copy(camera.position).addScaledVector(
      direction.normalize(),
      distance
    );

    camera.up.copy(up.normalize());

    camera.lookAt(target);
    camera.updateMatrixWorld(true);
  }

  function panPixels(dx, dy, height) {
    const PAN_GAIN = 3;
    const speed = getSpeedValue() / 10;

    const distance = Math.max(
      camera.position.distanceTo(target),
      getSceneRadius() * 0.1
    );

    const unitsPerPixel =
      2 * distance *
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

    camera.position.add(delta);
    target.add(delta);
  }

  function translate(x, y, z, distance) {
    if (!x && !y && !z) return;

    const delta = new THREE.Vector3(x, y, -z)
      .normalize()
      .applyQuaternion(camera.quaternion)
      .multiplyScalar(distance);

    camera.position.add(delta);
    target.add(delta);
  }

  function dollyToCursor(pixels) {
    // Zoom/déplacement selon le rayon passant par la souris.
    const width = Math.max(canvas.clientWidth, 1);
    const height = Math.max(canvas.clientHeight, 1);

    const ndcX = (lastPointer.x / width) * 2 - 1;
    const ndcY = -(lastPointer.y / height) * 2 + 1;

    const worldPointOnRay = new THREE.Vector3(ndcX, ndcY, 0.5)
      .unproject(camera);

    const direction = worldPointOnRay
      .sub(camera.position)
      .normalize();

    if (direction.lengthSq() < 1e-12) return;

    const speed = getSpeedValue() / 10;
    const worldStep = getSceneRadius() * 0.03 * speed;

    const delta =
      THREE.MathUtils.clamp(pixels / 100, -4, 4) * worldStep;

    // delta > 0 (wheel down) => reculer ; delta < 0 => avancer.
    const move = direction.multiplyScalar(-delta);

    camera.position.add(move);
    target.add(move);
  }

  function fitToView(sceneBox) {
    if (!sceneBox || sceneBox.isEmpty()) return;

    // Mémoriser le cadrage pour le raccourci R.
    // Pas de test getCanNavigate() ici : app.js appelle aussi
    // cette fonction pendant le chargement initial.
    lastSceneBox = sceneBox.clone();

    const sceneCenter = lastSceneBox.getCenter(
      new THREE.Vector3()
    );

    const direction = camera.position.clone().sub(target);

    if (direction.lengthSq() < 1e-12) {
      direction.set(0, 0.6, 1);
    }

    direction.normalize();

    camera.up.addScaledVector(
      direction,
      -camera.up.dot(direction)
    );

    if (camera.up.lengthSq() < 1e-12) {
      camera.up.set(0, 0, 1);

      if (Math.abs(camera.up.dot(direction)) > 0.999) {
        camera.up.set(1, 0, 0);
      }

      camera.up.addScaledVector(
        direction,
        -camera.up.dot(direction)
      );
    }

    camera.up.normalize();

    const vFov = THREE.MathUtils.degToRad(camera.fov);
    const hFov = 2 * Math.atan(
      Math.tan(vFov / 2) * camera.aspect
    );

    const forward = direction.clone().negate();
    const up = camera.up.clone();

    const right = new THREE.Vector3()
      .crossVectors(forward, up)
      .normalize();

    const trueUp = new THREE.Vector3()
      .crossVectors(right, forward)
      .normalize();

    const half = lastSceneBox.getSize(new THREE.Vector3())
      .multiplyScalar(0.5);

    const extent = (axis) =>
      Math.abs(half.x * axis.x) +
      Math.abs(half.y * axis.y) +
      Math.abs(half.z * axis.z);

    const fitDistance = (
      Math.max(
        extent(trueUp) / Math.tan(vFov / 2),
        extent(right) / Math.tan(hFov / 2)
      ) + extent(forward)
    ) * 1.08;

    target.copy(sceneCenter);

    camera.position.copy(sceneCenter)
      .addScaledVector(direction, fitDistance);

    syncCamera();
    requestGifCapture();
  }

  function setView(kind) {
    if (!getCanNavigate()) return;

    clearNavigation();

    if (isRotationLocked()) {
      setStatus(
        "Orientation verrouillée : seules les rotations autour " +
        `de l’axe ${getPrincipalAxisName().toUpperCase()} sont autorisées.`
      );
      return;
    }

    const axes = {
      front: new THREE.Vector3(0, 1, 0),
      top: new THREE.Vector3(0, 0, 1),
      side: new THREE.Vector3(1, 0, 0),
    };

    const up = axes[kind]?.clone();
    if (!up) return;

    const forward = target.clone().sub(camera.position);

    if (forward.lengthSq() < 1e-12) return;

    forward.normalize();
    up.addScaledVector(forward, -up.dot(forward));

    if (up.lengthSq() < 1e-12) {
      setStatus(
        "Cet axe est parallèle à la visée : impossible " +
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
    if (!getCanNavigate()) return;

    clearNavigation();

    const offset = camera.position.clone().sub(target);

    target.copy(getSceneCenter());
    camera.position.copy(target).add(offset);

    syncCamera();
    requestGifCapture();
  }

  function installMouseControls() {
    if (mouseControlsInstalled) return;
    mouseControlsInstalled = true;

    canvas.style.touchAction = "none";

    canvas.addEventListener("contextmenu", (event) => {
      event.preventDefault();
    });

    canvas.addEventListener("pointerdown", (event) => {
      lastPointer.x = event.clientX;
      lastPointer.y = event.clientY;

      if (!getCanNavigate() || mouseDrag) return;

      // Priorité aux mesures : ne pas prévenir l’événement,
      // flouter un champ ou capturer le pointeur.
      if (event.shiftKey) return;

      if (event.altKey || event.metaKey) return;

      let mode = null;

      if (
        event.button === lookBinding.button &&
        event[lookBinding.modifier]
      ) {
        mode = "look";
      } else if (event.button === 1 || event.button === 2) {
        mode = "pan";
      }

      // Le clic gauche simple ne déclenche plus la navigation.
      if (!mode) return;

      event.preventDefault();
      clearNavigation();

      if (document.activeElement instanceof HTMLElement) {
        document.activeElement.blur();
      }

      mouseDrag = {
        id: event.pointerId,
        button: event.button,
        mode,
        x: event.clientX,
        y: event.clientY,
        moved: false,
      };

      canvas.setPointerCapture(event.pointerId);
    });

    canvas.addEventListener("pointermove", (event) => {
      lastPointer.x = event.clientX;
      lastPointer.y = event.clientY;

      const drag = mouseDrag;

      if (!drag || drag.id !== event.pointerId) return;

      const buttonMask = drag.button === 0
        ? 1
        : drag.button === 1
          ? 4
          : 2;

      if (
        !(event.buttons & buttonMask) ||
        !getCanNavigate() ||
        event.shiftKey ||
        event.altKey ||
        event.metaKey ||
        (
          drag.mode === "look" &&
          !event[lookBinding.modifier]
        )
      ) {
        finishMouseDrag();
        return;
      }

      event.preventDefault();

      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;

      drag.x = event.clientX;
      drag.y = event.clientY;

      if (!dx && !dy) return;

      drag.moved = true;

      if (drag.mode === "look") {
        // Radians par pixel CSS.
        // Rotation seule : aucune translation ni sélection de pivot.
        const sensitivity = 0.005;

        orbitLook(
          -dx * sensitivity,
          -dy * sensitivity
        );
      } else {
        navigateWithCollision(() => {
          panPixels(dx, dy, canvas.clientHeight);
        }, true);
      }

      syncCamera();
      requestGifCapture();
    });

    const release = (event) => {
      if (mouseDrag?.id !== event.pointerId) return;
      finishMouseDrag();
    };

    [
      "pointerup",
      "pointercancel",
      "lostpointercapture",
    ].forEach((type) => {
      canvas.addEventListener(type, release);
    });

    canvas.addEventListener("wheel", (event) => {
      lastPointer.x = event.clientX;
      lastPointer.y = event.clientY;

      if (!getCanNavigate()) return;

      // Laisser Ctrl + molette au navigateur.
      if (event.ctrlKey || event.metaKey || event.altKey) return;

      event.preventDefault();

      const pixels = event.deltaMode === 1
        ? event.deltaY * 16
        : event.deltaMode === 2
          ? event.deltaY * canvas.clientHeight
          : event.deltaY;

      navigateWithCollision(() => {
        dollyToCursor(pixels);
      }, true);

      syncCamera();
      requestGifCapture();

      clearTimeout(wheelTimer);

      wheelTimer = setTimeout(() => {
        wheelTimer = null;
        requestGifCapture();
      }, 250);
    }, { passive: false });
  }

  function installHeldButtons() {
    if (heldButtonsInstalled) return;
    heldButtonsInstalled = true;

    // Les deux anciennes grilles peuvent être absentes du HTML.
    // Seuls les boutons réellement présents sont raccordés.
    document.querySelectorAll("[data-hold]").forEach((button) => {
      function end(key) {
        if (!heldButtons.delete(key)) return;

        if (![...heldButtons.values()].includes(button)) {
          button.classList.remove("held");
        }

        requestGifCapture();
      }

      async function begin(key) {
        if (
          button.disabled ||
          !getCanNavigate() ||
          heldButtons.has(key)
        ) {
          return;
        }

        heldButtons.set(key, button);
        button.classList.add("held");

        if (
          button.dataset.hold === "orbitPrincipalPositive" ||
          button.dataset.hold === "orbitPrincipalNegative"
        ) {
          try {
            const selected = await selectPointInView(key, button);

            if (!selected && heldButtons.get(key) === button) {
              end(key);
            }
          } catch (error) {
            end(key);
            console.error("Sélection du pivot :", error);

            setStatus(
              `Sélection impossible : ${error?.message || error}`,
              true
            );
          }
        }
      }

      button.addEventListener("pointerdown", (event) => {
        if (
          event.button !== 0 ||
          button.disabled ||
          !getCanNavigate()
        ) {
          return;
        }

        event.preventDefault();

        button.focus({ preventScroll: true });
        button.setPointerCapture(event.pointerId);

        void begin(event.pointerId);
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
        if (!["Enter", " "].includes(event.key)) return;

        event.preventDefault();

        if (!event.repeat) {
          void begin(button);
        }
      });

      button.addEventListener("keyup", (event) => {
        if (!["Enter", " "].includes(event.key)) return;

        event.preventDefault();
        end(button);
      });

      button.addEventListener("blur", () => end(button));
    });
  }

  function installKeyboard() {
    if (keyboardInstalled) return;
    keyboardInstalled = true;

    window.addEventListener("keydown", (event) => {
      if (
        event.defaultPrevented ||
        event.isComposing ||
        !getCanNavigate() ||
        isEditing(event.target)
      ) {
        return;
      }

      if (event.ctrlKey || event.metaKey || event.altKey) {
        // Éviter de laisser un déplacement actif pendant
        // l’utilisation d’un raccourci système ou de Ctrl + glisser.
        const wasMoving = [...pressedKeys].some(
          (key) => !!actionForKey(key)
        );

        pressedKeys.clear();

        if (wasMoving) requestGifCapture();

        return;
      }

      const key = event.key.toLowerCase();

      if (key === "r") {
        event.preventDefault();

        if (!event.repeat && lastSceneBox) {
          clearNavigation();
          fitToView(lastSceneBox);
        }

        return;
      }

      if (actionForKey(key) || key === "shift") {
        event.preventDefault();
        pressedKeys.add(key);
      }
    });

    window.addEventListener("keyup", (event) => {
      const key = event.key.toLowerCase();

      const moving =
        pressedKeys.has(key) && !!actionForKey(key);

      pressedKeys.delete(key);

      // Arrêter le geste dès le relâchement de Ctrl,
      // même si la souris ne bouge pas ensuite.
      if (
        mouseDrag?.mode === "look" &&
        !event[lookBinding.modifier]
      ) {
        finishMouseDrag();
      }

      if (moving) {
        requestGifCapture();
      }
    });

    window.addEventListener("blur", clearNavigation);

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        clearNavigation();
      }
    });

    // Ne pas continuer à avancer quand on entre dans un champ
    // ou qu’on prend le focus sur une commande du panneau.
    document.addEventListener("focusin", (event) => {
      if (isEditing(event.target)) {
        clearNavigation();
      }
    });
  }

  function frame(deltaTime) {
    if (!getCanNavigate() || document.hidden) return;

    const dt = Math.min(deltaTime, 0.05);
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

    if (!actions.size) return;

    const axis = (positive, negative) =>
      Number(actions.has(positive)) -
      Number(actions.has(negative));

    const amount =
      dt *
      getSpeedValue() / 10 *
      (pressedKeys.has("shift") ? 3 : 1);

    navigateWithCollision(() => {
      // Compatibilité avec d’éventuels anciens boutons data-hold.
      // Les flèches clavier ne produisent plus ces actions.
      orbitLook(
        axis("orbitLeft", "orbitRight") * amount,
        axis("orbitDown", "orbitUp") * amount
      );

      const principalDirection = axis(
        "orbitPrincipalPositive",
        "orbitPrincipalNegative"
      );

      if (principalDirection) {
        rotateAroundAxis(
          getPrincipalAxis(),
          principalDirection * amount
        );
      }

      translate(
        axis("right", "left"),
        axis("up", "down"),
        axis("forward", "backward"),
        getSceneRadius() * amount * 0.6
      );
    }, true);

    syncCamera();
    requestGifCapture();
  }

  return {
    pressedKeys,
    heldButtons,
    clearNavigation,
    installMouseControls,
    installHeldButtons,
    installKeyboard,
    rotateAroundAxis,
    orbitLook,
    panPixels,
    translate,
    dollyToCursor,
    fitToView,
    setView,
    recenterTarget,
    frame,
  };
}
