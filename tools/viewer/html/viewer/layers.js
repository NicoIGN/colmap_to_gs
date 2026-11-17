export function createLayerManager(container) {
  const layers = new Map();

  function makeToggle(name, visible, onChange, className = "check") {
    const label = document.createElement("label");
    label.className = className;
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = visible;
    input.addEventListener("change", () => onChange(input.checked));
    label.append(input, document.createTextNode(name));
    return label;
  }

  function makeRemoveButton(layer) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "remove-btn";
    button.title = `Supprimer : ${layer.name}`;
    button.textContent = "×";
    button.addEventListener("click", () => removeLayer(layer));
    return button;
  }

  function removeLayer(layer) {
    if (layers.get(layer.id) !== layer) return;
    layers.delete(layer.id);
    layer.onRemove?.();
    render();
  }

  function render() {
    container.replaceChildren();
    for (const layer of layers.values()) {
      if (layer.children) {
        const group = document.createElement("div");
        group.className = "layer-group";
        const allVisible = layer.visible && layer.children.every((child) => child.visible);
        const partiallyVisible =
          layer.visible && layer.children.some((child) => child.visible) && !allVisible;
        const master = makeToggle(layer.name, allVisible, (visible) => {
          layer.visible = visible;
          if (visible) {
            for (const child of layer.children) child.visible = true;
          }
          updateGroup(layer);
          render();
        });
        const masterInput = master.querySelector("input");
        masterInput.indeterminate = partiallyVisible;
        const head = document.createElement("div");
        head.className = "layer-head";
        head.append(master, makeRemoveButton(layer));
        group.appendChild(head);

        const options = document.createElement("div");
        options.className = layer.compact ? "layer-group-options compact" : "layer-group-options";
        for (const child of layer.children) {
          const toggle = makeToggle(child.name, child.visible, (visible) => {
            child.visible = visible;
            child.setVisible(layer.visible && visible);
            render();
          });
          if (!child.setHighlight) {
            options.appendChild(toggle);
            continue;
          }
          const row = document.createElement("div");
          row.className = "layer-option";
          const button = document.createElement("button");
          button.type = "button";
          button.className = "highlight-btn";
          button.title = `Mettre en évidence : ${child.name}`;
          button.setAttribute("aria-pressed", String(child.highlighted));
          button.textContent = "◎";
          button.addEventListener("click", () => {
            child.highlighted = !child.highlighted;
            child.setHighlight(child.highlighted);
            render();
          });
          row.append(toggle, button);
          options.appendChild(row);
        }
        group.appendChild(options);
        container.appendChild(group);
      } else {
        const head = document.createElement("div");
        head.className = "layer-head";
        head.append(
          makeToggle(layer.name, layer.visible, (visible) => {
            layer.visible = visible;
            layer.setVisible(visible);
          }),
          makeRemoveButton(layer)
        );
        container.appendChild(head);
      }
    }
    container.parentElement.hidden = layers.size === 0;
  }

  function updateGroup(group) {
    for (const child of group.children) {
      child.setVisible(group.visible && child.visible);
    }
  }

  return {
    add({ id, name, visible = true, setVisible, onRemove }) {
      if (layers.has(id)) throw new Error(`Couche déjà enregistrée : ${id}`);
      const layer = { id, name, visible, setVisible, onRemove };
      layers.set(id, layer);
      render();
      return () => {
        if (layers.get(id) === layer) {
          layers.delete(id);
          render();
        }
      };
    },
    addGroup({ id, name, children, visible = true, compact = false, onRemove }) {
      if (layers.has(id)) throw new Error(`Couche déjà enregistrée : ${id}`);
      const group = {
        id,
        name,
        visible,
        compact,
        onRemove,
        children: children.map((child) => ({
          ...child,
          visible: child.visible ?? true,
          highlighted: false,
        })),
      };
      layers.set(id, group);
      updateGroup(group);
      render();
      return () => {
        if (layers.get(id) === group) {
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
