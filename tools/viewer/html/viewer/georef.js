import proj4 from "proj4";

const MAX_GEOREF_BYTES = 5 * 1024 * 1024;

export const WGS84_EPSG = 4326;

const GEOGRAPHIC = "EPSG:4326";
const ECEF =
  "+proj=geocent +datum=WGS84 +units=m +no_defs";

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
  if (
    value === null ||
    value === undefined ||
    !["number", "string"].includes(typeof value) ||
    (typeof value === "string" && value.trim() === "")
  ) {
    throw new Error(`${label} manquant ou invalide.`);
  }

  const result = Number(value);

  if (!Number.isFinite(result)) {
    throw new Error(`${label} doit être un nombre fini.`);
  }

  return result;
}

function coordinates(x, y, h) {
  return {
    x: requireNumber(x, "X"),
    y: requireNumber(y, "Y"),
    h: requireNumber(h, "Hauteur"),
  };
}

function geographicCoordinates(lat, lon, h) {
  const result = {
    lat: requireNumber(lat, "Latitude"),
    lon: requireNumber(lon, "Longitude"),
    h: requireNumber(h, "Hauteur"),
  };

  if (
    result.lat < -90 ||
    result.lat > 90 ||
    result.lon < -180 ||
    result.lon > 180
  ) {
    throw new Error("Latitude ou longitude hors limites.");
  }

  return result;
}

function finiteTriple(values, label) {
  if (
    !values ||
    values.length < 3 ||
    !values.slice(0, 3).every(Number.isFinite)
  ) {
    throw new Error(`${label} : résultat non fini.`);
  }

  return values;
}

/**
 * Les fonctions "...World" manipulent le repère local du modèle,
 * AVANT la matrice d’affichage PlayCanvas.
 *
 * local_ENU :
 *   coordonnées modèle = East, North, Up.
 *
 * projected :
 *   coordonnées modèle = coordonnées projetées moins origine.
 *
 * Aucune Sim(3) supplémentaire n’est appliquée ici.
 */
export function parseGeoref(json) {
  const ref = json?.output_reference;

  if (!ref || typeof ref !== "object") {
    throw new Error("output_reference manquant.");
  }

  const type = ref.type;

  if (type !== "local_ENU" && type !== "projected") {
    throw new Error(
      `Type de référence non supporté : ${String(type)}.`
    );
  }

  const units = String(ref.units ?? "").trim().toLowerCase();

  if (!["m", "meter", "meters"].includes(units)) {
    throw new Error("Le repère du modèle doit être en mètres.");
  }

  const projectedReference = ref.origin_projected;

  const epsg = requireNumber(
    type === "local_ENU"
      ? projectedReference?.epsg
      : ref.epsg,
    "EPSG projeté"
  );

  if (!Number.isInteger(epsg) || epsg <= 0) {
    throw new Error("Code EPSG invalide.");
  }

  const projectedCrs = `EPSG:${epsg}`;
  const definition = proj4.defs(projectedCrs);

  if (!definition) {
    throw new Error(
      `${projectedCrs} : définition proj4 absente.`
    );
  }

  if (
    definition.projName === "longlat" ||
    definition.units !== "m"
  ) {
    throw new Error(
      `${projectedCrs} doit être un SCR projeté en mètres.`
    );
  }

  /**
   * Projection horizontale.
   * La hauteur GPX brute est conservée, sans correction géoïde.
   */
  function geographicToProjected(lat, lon, h) {
    const point = geographicCoordinates(lat, lon, h);

    const [x, y] = proj4(
      GEOGRAPHIC,
      projectedCrs,
      [point.lon, point.lat]
    );

    return coordinates(x, y, point.h);
  }

  function projectedToGeographic(x, y, h) {
    const point = coordinates(x, y, h);

    const [lon, lat] = proj4(
      projectedCrs,
      GEOGRAPHIC,
      [point.x, point.y]
    );

    return geographicCoordinates(lat, lon, point.h);
  }

  let origin;
  let originGeographic = null;
  let geographicToWorld;
  let worldToGeographic;
  let projectedToWorld;
  let worldToProjected;

  if (type === "local_ENU") {
    if (
      !Array.isArray(ref.axes) ||
      ref.axes.length !== 3 ||
      ref.axes[0] !== "East" ||
      ref.axes[1] !== "North" ||
      ref.axes[2] !== "Up"
    ) {
      throw new Error(
        "Axes ENU attendus : [East, North, Up]."
      );
    }

    const geographicOrigin = ref.origin_lat_lon_height;

    if (
      !Array.isArray(geographicOrigin) ||
      geographicOrigin.length !== 3
    ) {
      throw new Error(
        "origin_lat_lon_height doit contenir [latitude, longitude, hauteur]."
      );
    }

    const validatedOrigin = geographicCoordinates(
      geographicOrigin[0],
      geographicOrigin[1],
      geographicOrigin[2]
    );

    originGeographic = Object.freeze(validatedOrigin);

    // Origine projetée calculée depuis l’origine géographique réelle.
    // Elle sert à l’information, pas à une simple soustraction ENU.
    origin = Object.freeze(
      geographicToProjected(
        validatedOrigin.lat,
        validatedOrigin.lon,
        validatedOrigin.h
      )
    );

    const radians = Math.PI / 180;
    const lat0 = validatedOrigin.lat * radians;
    const lon0 = validatedOrigin.lon * radians;

    const sinLat = Math.sin(lat0);
    const cosLat = Math.cos(lat0);
    const sinLon = Math.sin(lon0);
    const cosLon = Math.cos(lon0);

    // Même matrice que enu_rotation() dans le Python.
    const rotation = [
      [-sinLon, cosLon, 0],
      [-sinLat * cosLon, -sinLat * sinLon, cosLat],
      [cosLat * cosLon, cosLat * sinLon, sinLat],
    ];

    const originEcef = finiteTriple(
      proj4(
        GEOGRAPHIC,
        ECEF,
        [
          validatedOrigin.lon,
          validatedOrigin.lat,
          validatedOrigin.h,
        ]
      ),
      "Origine ECEF"
    );

    geographicToWorld = (lat, lon, h) => {
      const point = geographicCoordinates(lat, lon, h);

      const ecef = finiteTriple(
        proj4(
          GEOGRAPHIC,
          ECEF,
          [point.lon, point.lat, point.h]
        ),
        "Conversion WGS84 vers ECEF"
      );

      const delta = ecef.map(
        (value, index) => value - originEcef[index]
      );

      const enu = rotation.map(
        (row) =>
          row[0] * delta[0] +
          row[1] * delta[1] +
          row[2] * delta[2]
      );

      return coordinates(enu[0], enu[1], enu[2]);
    };

    worldToGeographic = (x, y, h) => {
      const point = coordinates(x, y, h);
      const enu = [point.x, point.y, point.h];

      // ENU -> ECEF : origine + transposée(rotation) * ENU.
      const ecef = originEcef.map(
        (value, axis) =>
          value +
          rotation[0][axis] * enu[0] +
          rotation[1][axis] * enu[1] +
          rotation[2][axis] * enu[2]
      );

      const [lon, lat, height] = finiteTriple(
        proj4(ECEF, GEOGRAPHIC, ecef),
        "Conversion ECEF vers WGS84"
      );

      return geographicCoordinates(lat, lon, height);
    };

    projectedToWorld = (x, y, h) => {
      const point = projectedToGeographic(x, y, h);

      return geographicToWorld(
        point.lat,
        point.lon,
        point.h
      );
    };

    worldToProjected = (x, y, h) => {
      const point = worldToGeographic(x, y, h);

      return geographicToProjected(
        point.lat,
        point.lon,
        point.h
      );
    };
  } else {
    // Compatibilité avec les scènes projetées simplement recentrées.
    if (
      !projectedReference ||
      typeof projectedReference !== "object"
    ) {
      throw new Error("origin_projected manquant.");
    }

    origin = Object.freeze(
      coordinates(
        projectedReference.x ?? projectedReference.origin_x,
        projectedReference.y ?? projectedReference.origin_y,
        projectedReference.h ??
          projectedReference.origin_height
      )
    );

    projectedToWorld = (x, y, h) => {
      const point = coordinates(x, y, h);

      return coordinates(
        point.x - origin.x,
        point.y - origin.y,
        point.h - origin.h
      );
    };

    worldToProjected = (x, y, h) => {
      const point = coordinates(x, y, h);

      return coordinates(
        point.x + origin.x,
        point.y + origin.y,
        point.h + origin.h
      );
    };

    geographicToWorld = (lat, lon, h) => {
      const point = geographicToProjected(lat, lon, h);

      return projectedToWorld(point.x, point.y, point.h);
    };

    worldToGeographic = (x, y, h) => {
      const point = worldToProjected(x, y, h);

      return projectedToGeographic(point.x, point.y, point.h);
    };
  }

  return {
    type,
    epsg,
    units: "meters",
    origin,
    originGeographic,

    geographicToProjected,
    projectedToGeographic,

    geographicToWorld,
    worldToGeographic,

    projectedToWorld,
    worldToProjected,

    matchedGpx: json.matched_gpx ?? null,
    requiresVisualValidation:
      json.requires_visual_validation === true,
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
