// Matériaux de superposition (GPS, vecteurs, COLMAP) masqués ou non par les splats.
// Chaque groupe a sa case à cocher ; un matériau enregistré reçoit l'état courant de son groupe.
const groups = new Map();

function groupState(name) {
  if (!groups.has(name)) groups.set(name, { enabled: false, materials: new Set() });
  return groups.get(name);
}

export function registerOverlayMaterial(material, group) {
  const state = groupState(group);
  material.depthTest = state.enabled;
  state.materials.add(new WeakRef(material));
  return material;
}

export function setOverlayOcclusion(group, enabled) {
  const state = groupState(group);
  state.enabled = enabled;
  for (const ref of state.materials) {
    const material = ref.deref();
    if (!material) {
      state.materials.delete(ref);
      continue;
    }
    if (material.depthTest !== enabled) material.depthTest = enabled;
  }
}
