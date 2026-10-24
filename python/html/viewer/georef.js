import proj4 from "proj4";

const MAX_GEOREF_BYTES = 5 * 1024 * 1024;

export const WGS84_EPSG = 4326;

const EPSG_DEFS = {
  4326: "+proj=longlat +datum=WGS84 +no_defs",
  2154:
    "+proj=lcc +lat_1=49 +lat_2=44 +lat_0=46.5 " +
    "+lon_0=3 +x_0=700000 +y_0=6600000 " +
    "+ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs",
};

for (const [code, definition] of Object.entries(EPSG_DEFS)) {
  proj4.defs(`EPSG:${code}`, definition);
}

function requireNumber(value, label) {
  // Ne pas convertir silencieusement null ou "" en zéro.
  if (
    value === null ||
    value === undefined ||
    !["number", "string"].includes(typeof value) ||
    (typeof value === "string" && value.trim() === "")
  ) {
    throw new Error(`${label} manquant ou invalide.`);
  }

  const number = Number(value);

  if (!Number.isFinite(number)) {
    throw new Error(`${label} doit être un nombre fini.`);
  }

  return number;
}

function requireCoordinates(x, y, h) {
  return {
    x: requireNumber(x, "X"),
    y: requireNumber(y, "Y"),
    h: requireNumber(h, "Hauteur"),
  };
}

/**
 * Format attendu :
 *
 * output_reference: {
 *   type: "projected",
 *   epsg: 2154,
 *   units: "meters",
 *   origin_projected: { x, y, h }
 * }
 *
 * Convention :
 *   X projeté = x modèle + origine.x
 *   Y projeté = y modèle + origine.y
 *   H absolue = z modèle + origine.h
 *
 * Les fonctions nommées "...World" manipulent ici le repère LOCAL
 * du modèle, avant la matrice d’affichage PlayCanvas.
 *
 * La matrice d’affichage, notamment flipUp, est gérée dans app.js.
 */
export function parseGeoref(json) {
  const ref = json?.output_reference;

  if (!ref || typeof ref !== "object") {
    throw new Error("output_reference manquant.");
  }

  if (ref.type !== "projected") {
    throw new Error(
      "Référence attendue : type \"projected\". " +
      "Un repère local_ENU ne peut pas être traité comme un " +
      "repère projeté simplement recentré."
    );
  }

  const epsg = requireNumber(
    ref.epsg,
    "output_reference.epsg"
  );

  if (!Number.isInteger(epsg) || epsg <= 0) {
    throw new Error("output_reference.epsg invalide.");
  }

  const crsName = `EPSG:${epsg}`;
  const definition = proj4.defs(crsName);

  if (!definition) {
    throw new Error(
      `${crsName} non pris en charge : définition proj4 absente.`
    );
  }

  // Ce pipeline compare des coordonnées et distances en mètres.
  if (
    definition.projName === "longlat" ||
    definition.units !== "m"
  ) {
    throw new Error(
      `${crsName} : un SCR projeté en mètres est attendu.`
    );
  }

  const units = String(ref.units ?? "").trim().toLowerCase();

  if (!["m", "meter", "meters"].includes(units)) {
    throw new Error(
      "output_reference.units doit indiquer des mètres."
    );
  }

  const sourceOrigin = ref.origin_projected;

  if (
    !sourceOrigin ||
    typeof sourceOrigin !== "object" ||
    Array.isArray(sourceOrigin)
  ) {
    throw new Error(
      "output_reference.origin_projected manquant. " +
      "Indique l’origine réellement soustraite lors de l’export."
    );
  }

  if (sourceOrigin.epsg !== undefined) {
    const originEpsg = requireNumber(
      sourceOrigin.epsg,
      "origin_projected.epsg"
    );

    if (originEpsg !== epsg) {
      throw new Error(
        "L’EPSG de l’origine diffère de celui de la scène."
      );
    }
  }

  const origin = Object.freeze({
    x: requireNumber(sourceOrigin.x, "origin_projected.x"),
    y: requireNumber(sourceOrigin.y, "origin_projected.y"),
    h: requireNumber(sourceOrigin.h, "origin_projected.h"),
  });

  /**
   * GPX WGS84 -> coordonnées projetées absolues.
   * proj4 reçoit [longitude, latitude].
   *
   * La hauteur est conservée : aucune conversion verticale.
   */
  function geographicToProjected(lat, lon, h) {
    const latitude = requireNumber(lat, "Latitude");
    const longitude = requireNumber(lon, "Longitude");
    const height = requireNumber(h, "Hauteur");

    if (
      latitude < -90 ||
      latitude > 90 ||
      longitude < -180 ||
      longitude > 180
    ) {
      throw new Error("Latitude ou longitude hors limites.");
    }

    const [x, y] = proj4(
      `EPSG:${WGS84_EPSG}`,
      crsName,
      [longitude, latitude]
    );

    return requireCoordinates(x, y, height);
  }

  /**
   * Coordonnées projetées absolues -> WGS84.
   * La hauteur est conservée.
   */
  function projectedToGeographic(x, y, h) {
    const projected = requireCoordinates(x, y, h);

    const [lon, lat] = proj4(
      crsName,
      `EPSG:${WGS84_EPSG}`,
      [projected.x, projected.y]
    );

    if (
      !Number.isFinite(lat) ||
      !Number.isFinite(lon) ||
      lat < -90 ||
      lat > 90 ||
      lon < -180 ||
      lon > 180
    ) {
      throw new Error("Conversion vers WGS84 invalide.");
    }

    return { lat, lon, h: projected.h };
  }

  /**
   * Coordonnées projetées absolues -> coordonnées locales du modèle.
   */
  function projectedToWorld(x, y, h) {
    const projected = requireCoordinates(x, y, h);

    return requireCoordinates(
      projected.x - origin.x,
      projected.y - origin.y,
      projected.h - origin.h
    );
  }

  /**
   * Coordonnées locales du modèle -> coordonnées projetées absolues.
   */
  function worldToProjected(x, y, h) {
    const local = requireCoordinates(x, y, h);

    return requireCoordinates(
      local.x + origin.x,
      local.y + origin.y,
      local.h + origin.h
    );
  }

  return {
    epsg,
    origin,
    geographicToProjected,
    projectedToGeographic,
    projectedToWorld,
    worldToProjected,
  };
}

export async function readGeorefFile(file) {
  if (!file || !/\.json$/i.test(file.name)) {
    throw new Error("Un fichier de géoréférencement .json est attendu.");
  }

  if (file.size > MAX_GEOREF_BYTES) {
    throw new Error("Fichier de géoréférencement trop volumineux.");
  }

  let data;

  try {
    data = JSON.parse(await file.text());
  } catch {
    throw new Error("JSON illisible.");
  }

  return parseGeoref(data);
}
