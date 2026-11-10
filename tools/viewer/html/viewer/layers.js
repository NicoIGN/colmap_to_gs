export function createLayerManager(container) {
  const layers = new Map();

  function render() {
    container.replaceChildren();
    for (const layer of layers.values()) {
      const label = document.createElement("label");
      label.className = "check";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = layer.visible;
      input.addEventListener("change", () => {
        layer.visible = input.checked;
        layer.setVisible(input.checked);
      });
      label.append(input, document.createTextNode(layer.name));
      container.appendChild(label);
    }
    container.parentElement.hidden = layers.size === 0;
  }

  return {
    add({ id, name, visible = true, setVisible }) {
      if (layers.has(id)) throw new Error(`Couche déjà enregistrée : ${id}`);
      const layer = { name, visible, setVisible };
      layers.set(id, layer);
      render();
      return () => {
        if (layers.get(id) === layer) {
          layers.delete(id);
          render();
        }
      };
    },
    clear() {
      layers.clear();
      render();
    },
    get size() {
      return layers.size;
    },
  };
}
