import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = (await readFile(new URL("./html/viewer/navigation.js", import.meta.url), "utf8"))
  .replace('import * as THREE from "three";', `
    const THREE = {
      MathUtils: { degToRad: (degrees) => degrees * Math.PI / 180 },
      Vector3: class {
        constructor(x = 0, y = 0, z = 0) { Object.assign(this, { x, y, z }); }
        clone() { return new this.constructor(this.x, this.y, this.z); }
        copy(v) { Object.assign(this, { x: v.x, y: v.y, z: v.z }); return this; }
        sub(v) { return this.addScaledVector(v, -1); }
        lengthSq() { return this.dot(this); }
        length() { return Math.sqrt(this.lengthSq()); }
        normalize() { return this.multiplyScalar(1 / (this.length() || 1)); }
        dot(v) { return this.x * v.x + this.y * v.y + this.z * v.z; }
        crossVectors(a, b) {
          return this.copy({
            x: a.y * b.z - a.z * b.y,
            y: a.z * b.x - a.x * b.z,
            z: a.x * b.y - a.y * b.x,
          });
        }
        applyQuaternion(q) {
          if (!q.axis) return this;
          const v = this.clone(), cross = new this.constructor().crossVectors(q.axis, v);
          const cos = Math.cos(q.angle), sin = Math.sin(q.angle);
          return this.copy(v).multiplyScalar(cos).addScaledVector(cross, sin)
            .addScaledVector(q.axis, q.axis.dot(v) * (1 - cos));
        }
        multiplyScalar(n) { this.x *= n; this.y *= n; this.z *= n; return this; }
        addScaledVector(v, n) {
          this.x += v.x * n; this.y += v.y * n; this.z += v.z * n; return this;
        }
        add(v) { return this.addScaledVector(v, 1); }
        distanceTo(v) { return Math.hypot(this.x - v.x, this.y - v.y, this.z - v.z); }
      },
      Quaternion: class {
        setFromAxisAngle(axis, angle) { this.axis = axis.clone(); this.angle = angle; return this; }
      },
    };
    export const TestVector3 = THREE.Vector3;
  `);
const { selectionZoom, createNavigationSystem, TestVector3 } = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
);

test("selection zoom centers the rectangle without stretching its aspect ratio", () => {
  const viewport = { left: 100, top: 50, width: 800, height: 600 };
  const start = { x: 300, y: 200 };
  const end = { x: 500, y: 350 };
  const box = selectionZoom(start, end, viewport);
  assert.deepEqual(box, {
    x: -0.25, y: 0.25, scale: 0.25,
    left: 300, top: 200, width: 200, height: 150,
  });
  assert.deepEqual(selectionZoom(end, start, viewport), box);
  assert.equal(selectionZoom(start, { x: 500, y: 500 }, viewport).scale, 0.5);
});

test("mouse navigation distinguishes pan, look, orbit and modifier release", (t) => {
    class Target {
      constructor() { this.listeners = new Map(); }
      addEventListener(type, handler) { this.listeners.set(type, handler); }
      dispatch(type, data = {}) {
        const event = {
          pointerId: 1, button: 2, buttons: 2, clientX: 0, clientY: 0,
          ctrlKey: false, shiftKey: false, altKey: false, metaKey: false,
          preventDefault() { this.defaultPrevented = true; },
          ...data,
        };
        this.listeners.get(type)?.(event);
        return event;
      }
    }
    const previousDocument = globalThis.document;
    const previousHTMLElement = globalThis.HTMLElement;
    const previousWindow = globalThis.window;
    t.after(() => {
      globalThis.document = previousDocument;
      globalThis.HTMLElement = previousHTMLElement;
      globalThis.window = previousWindow;
    });
    globalThis.document = new Target();
    globalThis.document.activeElement = null;
    globalThis.window = new Target();
    globalThis.HTMLElement = class {};

    const canvas = new Target();
    Object.assign(canvas, {
      style: {}, clientWidth: 800, clientHeight: 600, captured: false,
      setPointerCapture() { this.captured = true; },
      hasPointerCapture() { return this.captured; },
      releasePointerCapture() { this.captured = false; },
    });
    const camera = {
      position: new TestVector3(0, 0, 10), up: new TestVector3(0, 1, 0),
      quaternion: {}, fov: 50, lookAt() {}, updateMatrixWorld() {},
    };
    const target = new TestVector3();
    let moves = 0;
    let locked = false;
    const navigation = createNavigationSystem({
      canvas, camera, target,
      getCanNavigate: () => true,
      getSceneRadius: () => 1,
      getSpeedValue: () => 10,
      getKeyboardLayout: () => "azerty",
      isRotationLocked: () => locked,
      getPrincipalAxis: () => new TestVector3(0, 1, 0),
      navigateWithCollision: (move, enabled) => { assert.equal(enabled, true); moves++; move(); },
      syncCamera: () => {},
      requestGifCapture: () => {},
    });
    navigation.installMouseControls();
    navigation.installKeyboard();
    assert.equal(canvas.dispatch("contextmenu").defaultPrevented, true);

    canvas.dispatch("pointerdown");
    assert.equal(canvas.captured, true);
    canvas.dispatch("pointermove", { clientX: 10, clientY: 5 });
    assert.equal(moves, 1);
    assert.ok(camera.position.x < 0 && camera.position.y > 0);
    assert.equal(camera.position.x, target.x);
    assert.equal(camera.position.y, target.y);
    canvas.dispatch("pointermove", { clientX: 20, ctrlKey: true });
    canvas.dispatch("pointermove", { clientX: 30, ctrlKey: false });
    assert.equal(moves, 3);
    canvas.dispatch("pointerup");
    assert.equal(canvas.captured, false);

    canvas.dispatch("pointerdown", { button: 1, buttons: 4 });
    canvas.dispatch("pointermove", { button: 1, buttons: 4, clientX: 4 });
    assert.equal(moves, 4);
    canvas.dispatch("pointercancel");
    assert.equal(canvas.captured, false);
    canvas.dispatch("pointerdown", { shiftKey: true });
    assert.equal(canvas.captured, false);
    canvas.dispatch("pointerdown");
    canvas.dispatch("pointermove", { shiftKey: true, clientX: 5 });
    assert.equal(canvas.captured, false);
    assert.equal(moves, 4);

    const position = camera.position.clone();
    const oldTarget = target.clone();
    canvas.dispatch("pointerdown", { ctrlKey: true });
    canvas.dispatch("pointermove", { ctrlKey: true, clientX: 20, clientY: 10 });
    assert.deepEqual(camera.position, position);
    assert.notDeepEqual(target, oldTarget);
    assert.ok(Math.abs(target.distanceTo(position) - oldTarget.distanceTo(position)) < 1e-10);
    assert.equal(moves, 4);
    globalThis.window.dispatch("keyup", { key: "Control" });
    assert.equal(canvas.captured, false);

    locked = true;
    const lockedTarget = target.clone();
    canvas.dispatch("pointerdown", { ctrlKey: true });
    canvas.dispatch("pointermove", { ctrlKey: true, clientY: 20 });
    assert.ok(target.distanceTo(lockedTarget) < 1e-10);
    canvas.dispatch("pointermove", { ctrlKey: false, clientX: 20 });
    assert.equal(canvas.captured, false);

    locked = false;
    const orbitStart = camera.position.clone();
    const orbitTarget = target.clone();
    canvas.dispatch("pointerdown", { button: 0, buttons: 1 });
    canvas.dispatch("pointermove", { button: 0, buttons: 1, clientX: 15 });
    assert.notDeepEqual(camera.position, orbitStart);
    assert.deepEqual(target, orbitTarget);
    canvas.dispatch("pointerup", { button: 0, buttons: 0 });
});

test("selection zoom clamps to the canvas and ignores tiny or degenerate selections", () => {
  const viewport = { left: 100, top: 50, width: 800, height: 600 };
  assert.equal(selectionZoom({ x: 0, y: 0 }, { x: 1000, y: 1000 }, viewport).scale, 1);
  assert.equal(selectionZoom({ x: 100, y: 50 }, { x: 103, y: 300 }, viewport), null);
  assert.equal(selectionZoom({ x: 100, y: 50 }, { x: 800, y: 50 }, viewport), null);
  assert.equal(selectionZoom({ x: 0, y: 0 }, { x: 100, y: 100 },
    { ...viewport, width: 0 }), null);
});
