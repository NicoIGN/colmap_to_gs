import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = (await readFile(new URL("./html/viewer/navigation.js", import.meta.url), "utf8"))
  .replace('import * as THREE from "three";', "");
const { selectionZoom } = await import(
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

test("selection zoom clamps to the canvas and ignores tiny or degenerate selections", () => {
  const viewport = { left: 100, top: 50, width: 800, height: 600 };
  assert.equal(selectionZoom({ x: 0, y: 0 }, { x: 1000, y: 1000 }, viewport).scale, 1);
  assert.equal(selectionZoom({ x: 100, y: 50 }, { x: 103, y: 300 }, viewport), null);
  assert.equal(selectionZoom({ x: 100, y: 50 }, { x: 800, y: 50 }, viewport), null);
  assert.equal(selectionZoom({ x: 0, y: 0 }, { x: 100, y: 100 },
    { ...viewport, width: 0 }), null);
});
