import * as THREE from "three";

export function createNavigationSystem({
  canvas,
  camera,
  target,
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

  let mouseDrag = null;
  let wheelTimer = null;

  function clearNavigation() {
    const wasMoving =
      heldButtons.size > 0 ||
      !!mouseDrag?.moved ||
      [...pressedKeys].some((key) => !!actionForKey(key));

    pressedKeys.clear();

    heldButtons.forEach((button) => {
      button.classList.remove("held");
    });

    heldButtons.clear();
    mouseDrag = null;

    clearTimeout(wheelTimer);
    wheelTimer = null;

    if (wasMoving) requestGifCapture();
  }

  const commonKeys = {
    arrowleft: "orbitLeft",
    arrowright: "orbitRight",
    arrowup: "orbitUp",
    arrowdown: "orbitDown",
  };

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

  function rotateAroundAxis(axis, angle) {
    if (!angle || axis.lengthSq() < 1e-12) return;

    const normalizedAxis = axis.clone().normalize();

    if (isRotationLocked()) {
      const principalAxis = getPrincipalAxis();
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

  function orbitLook(dx, dy) {
    if (!dx && !dy) return;

    const direction = target.clone().sub(camera.position);

    if (direction.lengthSq() < 1e-12) return;

    const up = camera.up.clone().normalize();
    direction.normalize();

    if (dx) {
      const yawAxis = isRotationLocked()
        ? getPrincipalAxis().clone().normalize()
        : new THREE.Vector3(0, 1, 0)
            .applyQuaternion(camera.quaternion)
            .normalize();

      let yawAngle = dx;

      if (isRotationLocked() && yawAxis.dot(up) < 0) {
        yawAngle = -yawAngle;
      }

      const yaw = new THREE.Quaternion()
        .setFromAxisAngle(yawAxis, yawAngle);

      direction.applyQuaternion(yaw);
      up.applyQuaternion(yaw);
    }

    if (dy && !isRotationLocked()) {
      const right = new THREE.Vector3()
        .crossVectors(direction, up)
        .normalize();

      if (right.lengthSq() > 1e-12) {
        const pitch = new THREE.Quaternion()
          .setFromAxisAngle(right, dy);

        direction.applyQuaternion(pitch);
        up.applyQuaternion(pitch);
      }
    }

    const distance = Math.max(
      camera.position.distanceTo(target),
      getSceneRadius() * 1e-4
    );

    target.copy(camera.position).add(
      direction.normalize().multiplyScalar(distance)
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

  function dollyToTarget(pixels) {
    const offset = target.clone().sub(camera.position);
    const distance = offset.length();

    if (distance < 1e-12) return;

    const speed = getSpeedValue() / 10;
    const worldStep =
      getSceneRadius() * 0.03 * speed;

    const delta =
      THREE.MathUtils.clamp(pixels / 100, -4, 4) * worldStep;

    const direction = offset.normalize();
    const move = direction.multiplyScalar(-delta);

    camera.position.add(move);
    target.add(move);
  }

  function fitToView(sceneBox) {
    if (!sceneBox) return;

    const sceneCenter = getSceneCenter();

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

    const forward = direction.clone().normalize().negate();
    const up = camera.up.clone().normalize();

    const right = new THREE.Vector3()
      .crossVectors(forward, up)
      .normalize();

    const trueUp = new THREE.Vector3()
      .crossVectors(right, forward)
      .normalize();

    const half = sceneBox.getSize(new THREE.Vector3())
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

    const forward = target.clone()
      .sub(camera.position)
      .normalize();

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
    camera.position.copy(getSceneCenter()).add(offset);

    syncCamera();
    requestGifCapture();
  }

  function installMouseControls() {
    canvas.style.touchAction = "none";

    canvas.addEventListener("contextmenu", (event) => {
      event.preventDefault();
    });

    canvas.addEventListener("pointerdown", (event) => {
      if (!getCanNavigate() || mouseDrag || event.button > 2) {
        return;
      }

      event.preventDefault();
      clearNavigation();

      if (document.activeElement instanceof HTMLElement) {
        document.activeElement.blur();
      }

      canvas.setPointerCapture(event.pointerId);

      mouseDrag = {
        id: event.pointerId,
        button: event.button,
        x: event.clientX,
        y: event.clientY,
        moved: false,
      };
    });

    canvas.addEventListener("pointermove", (event) => {
      if (!mouseDrag || mouseDrag.id !== event.pointerId) {
        return;
      }

      if (!event.buttons || !getCanNavigate()) {
        const moved = mouseDrag.moved;
        mouseDrag = null;

        if (moved) requestGifCapture();

        return;
      }

      const dx = event.clientX - mouseDrag.x;
      const dy = event.clientY - mouseDrag.y;

      mouseDrag.x = event.clientX;
      mouseDrag.y = event.clientY;

      if (dx || dy) mouseDrag.moved = true;

      navigateWithCollision(() => {
        if (mouseDrag.button === 0 && !event.shiftKey) {
          orbitLook(-dx * 0.005, -dy * 0.005);
        } else {
          panPixels(dx, dy, canvas.clientHeight);
        }
      }, true);

      syncCamera();
      requestGifCapture();
    });

    const release = (event) => {
      if (mouseDrag?.id !== event.pointerId) return;

      const moved = mouseDrag.moved;
      mouseDrag = null;

      if (moved) requestGifCapture();
    };

    [
      "pointerup",
      "pointercancel",
      "lostpointercapture",
    ].forEach((type) => {
      canvas.addEventListener(type, release);
    });

    canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      if (!getCanNavigate()) return;

      const pixels = event.deltaMode === 1
        ? event.deltaY * 16
        : event.deltaMode === 2
          ? event.deltaY * canvas.clientHeight
          : event.deltaY;

      navigateWithCollision(() => {
        dollyToTarget(pixels);
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
    document.querySelectorAll("[data-hold]").forEach((button) => {
      function end(key) {
        if (!heldButtons.delete(key)) return;

        if (![...heldButtons.values()].includes(button)) {
          button.classList.remove("held");
        }

        requestGifCapture();
      }

      async function begin(key) {
        if (!getCanNavigate() || heldButtons.has(key)) return;

        heldButtons.set(key, button);
        button.classList.add("held");

        if (
          button.dataset.hold === "orbitPrincipalPositive" ||
          button.dataset.hold === "orbitPrincipalNegative"
        ) {
          const selected = await selectPointInView(key, button);

          if (!selected && heldButtons.get(key) === button) {
            end(key);
          }
        }
      }

      button.addEventListener("pointerdown", (event) => {
        if (event.button !== 0 || !getCanNavigate()) return;

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
    window.addEventListener("keydown", (event) => {
      if (
        !getCanNavigate() ||
        isEditing(event.target) ||
        event.ctrlKey ||
        event.metaKey ||
        event.altKey
      ) {
        return;
      }

      const key = event.key.toLowerCase();

      if (key === "r") {
        event.preventDefault();
      } else if (actionForKey(key) || key === "shift") {
        event.preventDefault();
        pressedKeys.add(key);
      }
    });

    window.addEventListener("keyup", (event) => {
      const key = event.key.toLowerCase();
      const moving =
        pressedKeys.has(key) && !!actionForKey(key);

      pressedKeys.delete(key);

      if (moving) requestGifCapture();
    });

    window.addEventListener("blur", clearNavigation);

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) clearNavigation();
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
      if (action) actions.add(action);
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
    dollyToTarget,
    fitToView,
    setView,
    recenterTarget,
    frame,
  };
}
