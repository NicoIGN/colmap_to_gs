export function createLoadProgress(container) {
  const host = document.createElement("div");

  Object.assign(host.style, {
    position: "absolute",
    inset: "0",
    display: "none",
    alignItems: "center",
    justifyContent: "center",
    background: "rgba(8, 12, 20, 0.72)",
    zIndex: "1000",
    padding: "24px",
    boxSizing: "border-box",
    pointerEvents: "auto",
  });

  if (getComputedStyle(container).position === "static") {
    container.style.position = "relative";
  }

  const panel = document.createElement("div");

  Object.assign(panel.style, {
    width: "min(440px, 100%)",
    padding: "20px",
    borderRadius: "12px",
    background: "#171e2c",
    color: "#fff",
    boxShadow: "0 12px 48px #0008",
    font: "14px system-ui, sans-serif",
  });

  panel.setAttribute("role", "status");
  panel.setAttribute("aria-live", "polite");

  const label = document.createElement("div");
  label.style.marginBottom = "12px";

  const bar = document.createElement("progress");
  bar.max = 1;

  Object.assign(bar.style, {
    width: "100%",
    height: "18px",
    accentColor: "#69a7ff",
  });

  const detail = document.createElement("div");

  Object.assign(detail.style, {
    marginTop: "10px",
    opacity: "0.8",
    fontSize: "12px",
  });

  panel.append(label, bar, detail);
  host.append(panel);
  container.append(host);

  function update(message, fraction = null, extra = "") {
    host.style.display = "flex";
    label.textContent = message;

    if (Number.isFinite(fraction)) {
      const value = Math.max(0, Math.min(1, fraction));

      bar.value = value;
      detail.textContent =
        `${Math.round(value * 100)} %` +
        (extra ? ` · ${extra}` : "");
    } else {
      // Progression indéterminée : ne pas afficher un faux pourcentage.
      bar.removeAttribute("value");
      detail.textContent = extra || "Traitement en cours…";
    }

    bar.setAttribute("aria-label", message);
  }

  function hide() {
    host.style.display = "none";
  }

  return {
    update,
    hide,
    dispose() {
      host.remove();
    },
  };
}