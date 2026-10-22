const A = 6378137.0;
const E2 = 6.6943799901413165e-3;
const MAX_GEOREF_BYTES = 5 * 1024 * 1024;

// Le SCR du GeoPackage produit par le script de recalage.
export const GEOREF_EPSG = 4979; // lon, lat, hauteur ellipsoïdale

const toRadians = (degrees) => (degrees * Math.PI) / 180;

function geodeticToEcef(latDeg, lonDeg, height) {
  const lat = toRadians(latDeg);
  const lon = toRadians(lonDeg);
  const n = A / Math.sqrt(1 - E2 * Math.sin(lat) ** 2);

  return [
    (n + height) * Math.cos(lat) * Math.cos(lon),
    (n + height) * Math.cos(lat) * Math.sin(lon),
    (n * (1 - E2) + height) * Math.sin(lat),
  ];
}

function ecefToGeodetic(x, y, z) {
  const lon = Math.atan2(y, x);
  const p = Math.hypot(x, y);

  let lat = Math.atan2(z, p * (1 - E2));
  let height = 0;

  for (let i = 0; i < 6; i++) {
    const n = A / Math.sqrt(1 - E2 * Math.sin(lat) ** 2);
    height = p / Math.cos(lat) - n;
    lat = Math.atan2(z, p * (1 - (E2 * n) / (n + height)));
  }

  return {
    lon: (lon * 180) / Math.PI,
    lat: (lat * 180) / Math.PI,
    height,
  };
}

/**
 * Valide le contenu d'un georef_stats.json et retourne
 * { epsg, origin, enuToGeographic }.
 */
export function parseGeoref(json) {
  const origin = json?.output_reference?.origin_projected;

  if (!origin || typeof origin !== "object") {
    throw new Error(
      "georef_stats.json invalide : output_reference.origin_projected manquant."
    );
  }

  const epsg = Number(origin.epsg);
  const originX = Number(origin.origin_x);
  const originY = Number(origin.origin_y);
  const originH = Number(origin.origin_height);
  const M = origin?.local_en_to_xy?.M;

  if (
    !Number.isInteger(epsg) ||
    !Number.isFinite(originX) ||
    !Number.isFinite(originY) ||
    !Number.isFinite(originH) ||
    !Array.isArray(M) ||
    M.length !== 2 ||
    !Array.isArray(M[0]) ||
    !Array.isArray(M[1]) ||
    M[0].length !== 2 ||
    M[1].length !== 2
  ) {
    throw new Error("origin_projected invalide.");
  }

  const m00 = Number(M[0][0]);
  const m01 = Number(M[0][1]);
  const m10 = Number(M[1][0]);
  const m11 = Number(M[1][1]);

  if (![m00, m01, m10, m11].every(Number.isFinite)) {
    throw new Error("Matrice local_en_to_xy.M invalide.");
  }

  return {
    epsg,
    origin: { x: originX, y: originY, h: originH },
    // ENU(m) -> projeté(m)
    enuToProjected(e, n, u) {
      return {
        x: originX + m00 * e + m01 * n,
        y: originY + m10 * e + m11 * n,
        h: originH + u,
      };
    },
  };
}

/** Lit un File .json (déposé ou sélectionné) et retourne le géoréférencement. */
export async function readGeorefFile(file) {
  if (!/\.json$/i.test(file.name)) {
    throw new Error("Un fichier .json est attendu (georef_stats.json).");
  }

  if (file.size > MAX_GEOREF_BYTES) {
    throw new Error("Fichier JSON trop volumineux pour un georef_stats.json.");
  }

  let data;

  try {
    data = JSON.parse(await file.text());
  } catch {
    throw new Error("JSON illisible.");
  }

  return parseGeoref(data);
}
