import assert from "node:assert/strict";
import test from "node:test";

class Element {
  constructor() {
    this.children = [];
    this.listeners = new Map();
    this.hidden = false;
    this.parentElement = { hidden: true };
  }

  replaceChildren(...children) {
    this.children = children;
  }

  append(...children) {
    this.children.push(...children);
  }

  appendChild(child) {
    this.children.push(child);
  }

  addEventListener(type, callback) {
    this.listeners.set(type, callback);
  }
}

globalThis.document = {
  createElement: () => new Element(),
  createTextNode: (text) => ({ text }),
};

const { createLayerManager } = await import("./html/viewer/layers.js");

test("layer manager adds, toggles, and unregisters visibility controls", () => {
  const container = new Element();
  const manager = createLayerManager(container);
  const visibility = [];
  const unregister = manager.add({
    id: "splat-1",
    name: "scene.splat",
    setVisible: (visible) => visibility.push(visible),
  });

  assert.equal(manager.size, 1);
  assert.equal(container.parentElement.hidden, false);
  const checkbox = container.children[0].children[0];
  assert.equal(checkbox.checked, true);
  checkbox.checked = false;
  checkbox.listeners.get("change")();
  assert.deepEqual(visibility, [false]);

  unregister();
  assert.equal(manager.size, 0);
  assert.equal(container.parentElement.hidden, true);
});

test("layer manager rejects duplicate identifiers", () => {
  const manager = createLayerManager(new Element());
  manager.add({ id: "same", name: "one", setVisible() {} });
  assert.throws(
    () => manager.add({ id: "same", name: "two", setVisible() {} }),
    /Couche déjà enregistrée/
  );
});
