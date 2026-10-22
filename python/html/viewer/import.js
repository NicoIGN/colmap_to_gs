import * as pc from "playcanvas";

const ROW_BYTES = 32;
const ROWS_PER_CHUNK = 16_384;
const SH_C0 = 0.28209479177387814;

const CONVERTER_URL =
  "https://esm.sh/@playcanvas/splat-transform@3.10.1?external=playcanvas";

const PROPERTY_NAMES = [
  "x",
  "y",
  "z",
  "scale_0",
  "scale_1",
  "scale_2",
  "f_dc_0",
  "f_dc_1",
  "f_dc_2",
  "opacity",
  "rot_0",
  "rot_1",
  "rot_2",
  "rot_3",
];

let converterPromise = null;

const yieldToBrowser = () =>
  new Promise((resolve) => setTimeout(resolve, 0));

function abortError() {
  return new DOMException("Import interrompu.", "AbortError");
}

function formatBytes(bytes) {
  return `${(bytes / 1024 / 1024).toFixed(1)} Mio`;
}

async function loadConverter() {
  if (!converterPromise) {
    converterPromise = import(CONVERTER_URL).catch((error) => {
      converterPromise = null;
      throw error;
    });
  }

  return converterPromise;
}

export function isSupportedSceneName(name) {
  return /\.(sog|ply|splat|ksplat)$/i.test(name);
}

export function filenameFromUrl(url) {
  try {
    const leaf = new URL(url, location.href).pathname.split("/").pop();
    return decodeURIComponent(leaf || "scene.ply");
  } catch {
    return String(url).split("/").pop().split(/[?#]/)[0] || "scene.ply";
  }
}

/**
 * Adaptateur de lecture pour splat-transform.
 *
 * Les fichiers locaux sont lus par tranches de 1 Mio maximum.
 * readAll() reste disponible pour les formats dont le décodeur
 * exige une lecture complète, notamment certaines variantes KSplat.
 */
function createBlobReadSource(blob) {
  let closed = false;
  const streams = new Set();

  return {
    size: blob.size,
    seekable: true,

    read(start = 0, end = blob.size) {
      if (closed) throw abortError();

      if (
        !Number.isInteger(start) ||
        !Number.isInteger(end) ||
        start < 0 ||
        end < start ||
        end > blob.size
      ) {
        throw new Error("Plage de lecture invalide.");
      }

      let cursor = start;
      let streamClosed = false;

      const stream = {
        expectedSize: end - start,
        bytesRead: 0,

        async pull(target) {
          if (closed || streamClosed) throw abortError();

          const count = Math.min(
            target.byteLength,
            end - cursor,
            1024 * 1024
          );

          if (count <= 0) return 0;

          const bytes = await blob
            .slice(cursor, cursor + count)
            .arrayBuffer();

          if (closed || streamClosed) throw abortError();

          target.set(new Uint8Array(bytes));
          cursor += count;
          stream.bytesRead += count;

          return count;
        },

        async readAll() {
          const bytes = new Uint8Array(end - cursor);
          let offset = 0;

          while (offset < bytes.byteLength) {
            const count = await stream.pull(bytes.subarray(offset));

            if (!count) {
              throw new Error("Fin de fichier inattendue.");
            }

            offset += count;
          }

          return bytes;
        },

        close() {
          streamClosed = true;
          streams.delete(stream);
        },
      };

      streams.add(stream);
      return stream;
    },

    close() {
      closed = true;

      for (const stream of streams) {
        stream.close();
      }

      streams.clear();
      blob = null;
    },
  };
}

export function createSceneImporter({
  app,
  setStatus,
  onProgress = () => {},
}) {
  let currentAsset = null;
  let activeJob = null;
  let disposed = false;

  function report(message, fraction = null, detail = "") {
    setStatus(message);
    onProgress(message, fraction, detail);
  }

  function createJob() {
    const controller = new AbortController();
    const cleanups = new Set();

    return {
      signal: controller.signal,

      check() {
        if (disposed || controller.signal.aborted) {
          throw abortError();
        }
      },

      addCleanup(callback) {
        cleanups.add(callback);
        return () => cleanups.delete(callback);
      },

      cancel() {
        if (controller.signal.aborted) return;

        controller.abort();

        for (const callback of cleanups) {
          try {
            callback();
          } catch {
            // Le nettoyage doit se poursuivre.
          }
        }

        cleanups.clear();
      },
    };
  }

  function release() {
    activeJob?.cancel();
    activeJob = null;

    if (currentAsset) {
      currentAsset.unload();
      app.assets.remove(currentAsset);
      currentAsset = null;
    }
  }

  /**
   * Chargement natif PLY/SOG.
   *
   * Pour PLY, contents peut être une Response :
   * le parseur consomme directement son ReadableStream.
   */
  function loadNative({
    name,
    url,
    contents = null,
    job,
  }) {
    job.check();

    const file = {
      url,
      filename: name,
    };

    if (contents) {
      file.contents = contents;
    }

    const asset = new pc.Asset(name, "gsplat", file, {
      // Évite le tri Morton et ses allocations temporaires au chargement.
      // Cela peut sacrifier une optimisation d'accès mémoire au rendu.
      reorder: false,
    });

    currentAsset = asset;
    app.assets.add(asset);

    return new Promise((resolve, reject) => {
      let settled = false;
      let removeCancel = () => {};

      function cleanup() {
        asset.off("load", onLoad);
        asset.off("error", onError);
        asset.off("progress", onAssetProgress);

        // Ne pas conserver Response/File dans la description de l'asset.
        if (asset.file) {
          delete asset.file.contents;
        }

        removeCancel();
      }

      function finish(error) {
        if (settled) return;

        settled = true;
        cleanup();

        if (error) reject(error);
        else resolve(asset);
      }

      function onLoad() {
        try {
          job.check();
          finish();
        } catch (error) {
          finish(error);
        }
      }

      function onError(error) {
        finish(
          error instanceof Error ? error : new Error(String(error))
        );
      }

      function onAssetProgress(received, total) {
        if (job.signal.aborted) return;

        report(
          `Lecture PlayCanvas de ${name}…`,
          total > 0 ? received / total : null,
          formatBytes(received)
        );
      }

      removeCancel = job.addCleanup(() => finish(abortError()));

      asset.once("load", onLoad);
      asset.once("error", onError);
      asset.on("progress", onAssetProgress);

      try {
        app.assets.load(asset);
      } catch (error) {
        finish(error);
      }
    });
  }

  /**
   * Lecture directe du format .splat standard :
   *
   * 3 float32 position, 3 float32 échelle,
   * 4 uint8 RGBA, 4 uint8 quaternion.
   *
   * Pas de fichier PLY sérialisé et pas de Blob de sortie.
   */
  async function loadSplat(source, name, job) {
    if (
      typeof pc.GSplatData !== "function" ||
      typeof pc.GSplatResource !== "function"
    ) {
      throw new Error(
        "Cette version de PlayCanvas n’expose pas " +
        "GSplatData/GSplatResource. Mets à jour PlayCanvas."
      );
    }

    let totalBytes;
    let reader = null;
    let removeReaderCleanup = () => {};

    try {
      if (source instanceof Blob) {
        totalBytes = source.size;
      } else {
        report(`Téléchargement de ${name}…`);

        const response = await fetch(
          new URL(source, location.href).href,
          { signal: job.signal }
        );

        if (!response.ok || !response.body) {
          throw new Error(`Téléchargement impossible : HTTP ${response.status}`);
        }

        // La taille est nécessaire pour allouer une seule fois
        // les colonnes de données, sans agrandissements successifs.
        const length = Number(response.headers.get("content-length"));
        const encoding = response.headers.get("content-encoding");

        if (
          !Number.isSafeInteger(length) ||
          length <= 0 ||
          (encoding && encoding !== "identity")
        ) {
          await response.body.cancel();

          throw new Error(
            "Pour importer ce .splat distant sans copie complète, " +
            "le serveur doit fournir Content-Length sans compression HTTP. " +
            "Télécharge le fichier puis ouvre-le localement."
          );
        }

        totalBytes = length;
        reader = response.body.getReader();

        removeReaderCleanup = job.addCleanup(() => {
          void reader?.cancel().catch(() => {});
        });
      }

      job.check();

      if (!totalBytes || totalBytes % ROW_BYTES !== 0) {
        throw new Error(
          "Fichier .splat invalide : taille attendue multiple de 32 octets."
        );
      }

      const count = totalBytes / ROW_BYTES;

      report(
        `Allocation pour ${count.toLocaleString()} splats…`,
        null,
        "Sans PLY intermédiaire"
      );

      await yieldToBrowser();
      job.check();

      const properties = PROPERTY_NAMES.map((name) => ({
        name,
        type: "float",
        byteSize: 4,
        storage: new Float32Array(count),
      }));

      const columns = Object.fromEntries(
        properties.map((property) => [property.name, property.storage])
      );

      let offsetBytes = 0;
      let row = 0;
      let networkChunk = null;
      let networkOffset = 0;

      async function readNetworkBlock(length) {
        const block = new Uint8Array(length);
        let filled = 0;

        while (filled < length) {
          job.check();

          if (!networkChunk || networkOffset === networkChunk.byteLength) {
            const next = await reader.read();

            if (next.done) {
              throw new Error("Le fichier .splat distant est tronqué.");
            }

            networkChunk = next.value;
            networkOffset = 0;
          }

          const count = Math.min(
            length - filled,
            networkChunk.byteLength - networkOffset
          );

          block.set(
            networkChunk.subarray(networkOffset, networkOffset + count),
            filled
          );

          networkOffset += count;
          filled += count;
        }

        return block;
      }

      while (row < count) {
        job.check();

        const rows = Math.min(ROWS_PER_CHUNK, count - row);
        const length = rows * ROW_BYTES;

        const bytes = source instanceof Blob
          ? new Uint8Array(
              await source
                .slice(offsetBytes, offsetBytes + length)
                .arrayBuffer()
            )
          : await readNetworkBlock(length);

        job.check();

        const view = new DataView(
          bytes.buffer,
          bytes.byteOffset,
          bytes.byteLength
        );

        for (let j = 0; j < rows; j++) {
          const index = row + j;
          const offset = j * ROW_BYTES;

          const x = view.getFloat32(offset, true);
          const y = view.getFloat32(offset + 4, true);
          const z = view.getFloat32(offset + 8, true);

          const sx = view.getFloat32(offset + 12, true);
          const sy = view.getFloat32(offset + 16, true);
          const sz = view.getFloat32(offset + 20, true);

          if (
            !Number.isFinite(x) ||
            !Number.isFinite(y) ||
            !Number.isFinite(z) ||
            !Number.isFinite(sx) ||
            !Number.isFinite(sy) ||
            !Number.isFinite(sz) ||
            sx <= 0 ||
            sy <= 0 ||
            sz <= 0
          ) {
            throw new Error(
              `Données .splat invalides au Gaussian ${index}.`
            );
          }

          columns.x[index] = x;
          columns.y[index] = y;
          columns.z[index] = z;

          columns.scale_0[index] = Math.log(sx);
          columns.scale_1[index] = Math.log(sy);
          columns.scale_2[index] = Math.log(sz);

          columns.f_dc_0[index] =
            (bytes[offset + 24] / 255 - 0.5) / SH_C0;
          columns.f_dc_1[index] =
            (bytes[offset + 25] / 255 - 0.5) / SH_C0;
          columns.f_dc_2[index] =
            (bytes[offset + 26] / 255 - 0.5) / SH_C0;

          const alpha = Math.max(
            1e-6,
            Math.min(1 - 1e-6, bytes[offset + 27] / 255)
          );

          columns.opacity[index] = Math.log(alpha / (1 - alpha));

          const r0 = bytes[offset + 28] / 255 * 2 - 1;
          const r1 = bytes[offset + 29] / 255 * 2 - 1;
          const r2 = bytes[offset + 30] / 255 * 2 - 1;
          const r3 = bytes[offset + 31] / 255 * 2 - 1;

          const norm = Math.hypot(r0, r1, r2, r3);

          columns.rot_0[index] = norm ? r0 / norm : 1;
          columns.rot_1[index] = norm ? r1 / norm : 0;
          columns.rot_2[index] = norm ? r2 / norm : 0;
          columns.rot_3[index] = norm ? r3 / norm : 0;
        }

        row += rows;
        offsetBytes += length;

        report(
          `Décodage direct de ${name}…`,
          row / count,
          `${row.toLocaleString()} / ${count.toLocaleString()} splats`
        );

        await yieldToBrowser();
      }

      if (reader) {
        // Vérifier également qu'aucun octet supplémentaire n'a été reçu.
        if (
          networkChunk &&
          networkOffset < networkChunk.byteLength
        ) {
          throw new Error("La taille réelle du .splat dépasse Content-Length.");
        }

        const tail = await reader.read();

        if (!tail.done) {
          throw new Error("La taille réelle du .splat dépasse Content-Length.");
        }
      }

      networkChunk = null;

      job.check();

      report(
        "Création des ressources graphiques…",
        null,
        "Cette étape PlayCanvas peut momentanément figer l’interface."
      );

      await yieldToBrowser();
      job.check();

      const data = new pc.GSplatData([{
        name: "vertex",
        count,
        properties,
      }]);

      // Les tableaux utilisent les conventions PLY :
      // échelles logarithmiques, opacité avant sigmoid.
      const resource = new pc.GSplatResource(
        app.graphicsDevice,
        data,
        { prepareCenters: true }
      );

      try {
        job.check();

        const asset = new pc.Asset(name, "gsplat");
        asset.resource = resource;
        asset.loaded = true;

        currentAsset = asset;
        app.assets.add(asset);

        return asset;
      } catch (error) {
        resource.destroy();
        throw error;
      }
    } finally {
      removeReaderCleanup();

      if (reader) {
        try {
          await reader.cancel();
        } catch {
          // Une annulation ou une erreur réseau peut déjà l'avoir fermé.
        }

        reader.releaseLock();
      }
    }
  }

  /**
   * KSplat -> flux PLY -> parseur PlayCanvas.
   *
   * Aucun Blob PLY n'est créé.
   * Le décodeur KSplat peut néanmoins matérialiser ses données :
   * ce parcours n'a pas une consommation mémoire constante.
   */
  async function loadKsplat(source, name, job) {
    report("Chargement du décodeur KSplat…");

    const {
      readFile,
      writeSource,
      createChunkDataPool,
      UrlReadFileSystem,
    } = await loadConverter();

    job.check();

    const openedInputs = new Set();
    const urlFs = new UrlReadFileSystem();

    let sources = [];
    let writer = null;
    let producer = null;
    let removeWriterCleanup = () => {};

    const closeInputs = () => {
      for (const input of openedInputs) {
        input.close();
      }

      openedInputs.clear();
    };

    const removeInputCleanup = job.addCleanup(closeInputs);

    try {
      report(`Décodage de ${name}…`);

      sources = await readFile({
        filename: "input.ksplat",
        inputFormat: "ksplat",

        fileSystem: {
          async createSource() {
            job.check();

            const input = source instanceof Blob
              ? createBlobReadSource(source)
              : await urlFs.createSource(
                  new URL(source, location.href).href
                );

            try {
              job.check();
            } catch (error) {
              input.close();
              throw error;
            }

            openedInputs.add(input);
            return input;
          },
        },
      });

      job.check();

      if (sources.length !== 1 || !sources[0]) {
        throw new Error("Une seule scène KSplat est attendue.");
      }

      const stream = new TransformStream();
      writer = stream.writable.getWriter();

      removeWriterCleanup = job.addCleanup(() => {
        void writer.abort(abortError()).catch(() => {});
      });

      let written = 0;

      const output = {
        async mkdir() {},

        createWriter(filename) {
          if (filename !== "converted.ply") {
            throw new Error(`Sortie inattendue : ${filename}`);
          }

          return {
            get bytesWritten() {
              return written;
            },

            async write(bytes) {
              job.check();

              // Le convertisseur réutilise ses buffers.
              // Copier seulement le fragment courant.
              await writer.write(bytes.slice());

              written += bytes.byteLength;
              job.check();
            },

            async close() {
              await writer.close();
            },

            async abort() {
              await writer.abort(
                new Error("Conversion KSplat interrompue.")
              );
            },
          };
        },
      };

      // Producteur et consommateur doivent fonctionner simultanément :
      // TransformStream applique la contre-pression.
      producer = (async () => {
        try {
          await writeSource({
            filename: "converted.ply",
            outputFormat: "ply",
            source: sources[0],
            pool: createChunkDataPool(),
            options: {},
          }, output);
        } catch (error) {
          await writer.abort(error).catch(() => {});
          throw error;
        }
      })();

      const consumer = loadNative({
        name: name.replace(/\.ksplat$/i, ".ply"),
        url: "converted.ply",
        contents: new Response(stream.readable, {
          headers: {
            "Content-Type": "application/octet-stream",
          },
        }),
        job,
      }).catch(async (error) => {
        await writer.abort(error).catch(() => {});
        throw error;
      });

      const [asset] = await Promise.all([consumer, producer]);

      job.check();
      return asset;
    } finally {
      removeWriterCleanup();
      removeInputCleanup();

      if (writer) {
        await writer.abort().catch(() => {});
      }

      if (producer) {
        await producer.catch(() => {});
      }

      try {
        for (const item of sources) {
          await item.close();
        }
      } finally {
        closeInputs();
      }
    }
  }

  async function load(source, name) {
    if (disposed) throw abortError();

    if (!isSupportedSceneName(name)) {
      throw new Error("Formats acceptés : .sog, .ply, .splat ou .ksplat.");
    }

    release();

    const job = createJob();
    activeJob = job;

    let objectUrl = null;

    try {
      const extension = name.split(".").pop().toLowerCase();

      if (extension === "splat") {
        return await loadSplat(source, name, job);
      }

      if (extension === "ksplat") {
        return await loadKsplat(source, name, job);
      }

      report(`Chargement de ${name}…`);

      if (extension === "ply") {
        let response;

        if (source instanceof Blob) {
          response = new Response(source.stream(), {
            headers: {
              "Content-Length": String(source.size),
              "Content-Type": "application/octet-stream",
            },
          });
        } else {
          response = await fetch(
            new URL(source, location.href).href,
            { signal: job.signal }
          );

          if (!response.ok) {
            throw new Error(`Téléchargement impossible : HTTP ${response.status}`);
          }
        }

        job.check();

        return await loadNative({
          name,
          url: new URL(name, location.href).href,
          contents: response,
          job,
        });
      }

      // SOG : laisser le parseur natif gérer le conteneur.
      // Il n'y a ici aucun Blob de conversion, seulement le File d'origine.
      const url = source instanceof Blob
        ? (objectUrl = URL.createObjectURL(source))
        : new URL(source, location.href).href;

      return await loadNative({ name, url, job });
    } catch (error) {
      if (activeJob === job) {
        release();
      }

      throw error;
    } finally {
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }

      if (activeJob === job) {
        activeJob = null;
      }
    }
  }

  return {
    load,
    release,

    dispose() {
      disposed = true;
      release();
    },
  };
}
