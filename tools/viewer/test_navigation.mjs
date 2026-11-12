import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = (await readFile(new URL("./html/viewer/navigation.js", import.meta.url), "utf8"))
  .replace('import * as THREE from "three";', `
    const THREE = {
      MathUtils: { degToRad: (degrees) => degrees * Math.PI / 180 },
      Vector3: class {
        constructor(x = 0, y = 0, z = 0) { Object.assign(this, { x, y, z }); }
        applyQuaternion() { return this; }
        multiplyScalar(n) { this.x *= n; this.y *= n; this.z *= n; return this; }
        addScaledVector(v, n) {
          this.x += v.x * n; this.y += v.y * n; this.z += v.z * n; return this;
        }
        add(v) { return this.addScaledVector(v, 1); }
        distanceTo(v) { return Math.hypot(this.x - v.x, this.y - v.y, this.z - v.z); }
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

test("right drag pans without Ctrl, keeps middle drag and honors gesture cancellation", (t) => {
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
    t.after(() => {
      globalThis.document = previousDocument;
      globalThis.HTMLElement = previousHTMLElement;
    });
    globalThis.document = { activeElement: null };
    globalThis.HTMLElement = class {};

    const canvas = new Target();
    Object.assign(canvas, {
      style: {}, clientWidth: 800, clientHeight: 600, captured: false,
      setPointerCapture() { this.captured = true; },
      hasPointerCapture() { return this.captured; },
      releasePointerCapture() { this.captured = false; },
    });
    const camera = {
      position: new TestVector3(0, 0, 10), quaternion: {}, fov: 50,
    };
    const target = new TestVector3();
    let moves = 0;
    const navigation = createNavigationSystem({
      canvas, camera, target,
      getCanNavigate: () => true,
      getSceneRadius: () => 1,
      getSpeedValue: () => 10,
      navigateWithCollision: (move, enabled) => { assert.equal(enabled, true); moves++; move(); },
      syncCamera: () => {},
      requestGifCapture: () => {},
    });
    navigation.installMouseControls();
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
});

test("selection zoom clamps to the canvas and ignores tiny or degenerate selections", () => {
  const viewport = { left: 100, top: 50, width: 800, height: 600 };
  assert.equal(selectionZoom({ x: 0, y: 0 }, { x: 1000, y: 1000 }, viewport).scale, 1);
  assert.equal(selectionZoom({ x: 100, y: 50 }, { x: 103, y: 300 }, viewport), null);
  assert.equal(selectionZoom({ x: 100, y: 50 }, { x: 800, y: 50 }, viewport), null);
  assert.equal(selectionZoom({ x: 0, y: 0 }, { x: 100, y: 100 },
    { ...viewport, width: 0 }), null);
});
