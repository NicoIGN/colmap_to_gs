// Matériaux de superposition (GPS, vecteurs) : masqués par les splats quand
// l'option « Splats masquent les volumes 3D » est active.
const overlayMaterials = new Set();
let occludedBySplats = false;

export function registerOverlayMaterial(material) {
  material.depthTest = occludedBySplats;
  overlayMaterials.add(material);
  return material;
}

export function setOverlayOcclusion(enabled) {
  occludedBySplats = enabled;
  for (const material of overlayMaterials) {
    if (material.depthTest !== enabled) material.depthTest = enabled;
  }
}
