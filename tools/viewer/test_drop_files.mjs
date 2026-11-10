import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("./html/viewer/drop-files.js", import.meta.url), "utf8");
const { collectDroppedFiles, hasColmapModel } = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
);

function fileEntry(path, text = path) {
  const name = path.split("/").at(-1);
  return {
    isFile: true,
    isDirectory: false,
    name,
    fullPath: `/${path}`,
    webkitRelativePath: path,
    file(resolve) {
      resolve({ name, size: text.length, type: "", lastModified: 1,
        arrayBuffer: async () => new TextEncoder().encode(text).buffer });
    },
  };
}

function directoryEntry(path, children) {
  let read = false;
  return {
    isFile: false,
    isDirectory: true,
    name: path.split("/").at(-1),
    fullPath: `/${path}`,
    createReader() {
      return { readEntries(resolve) {
        resolve(read ? [] : (read = true, children));
      } };
    },
  };
}

function item(entry) {
  return { kind: "file", webkitGetAsEntry: () => entry };
}

test("recursive directory drop locates a nested COLMAP triplet", async () => {
  const nested = directoryEntry("blocks/block_002", [
    fileEntry("blocks/block_002/cameras.bin"),
    fileEntry("blocks/block_002/images.bin"),
    fileEntry("blocks/block_002/points3D.bin"),
    fileEntry("blocks/block_002/readme.txt"),
  ]);
  const result = await collectDroppedFiles([item(directoryEntry("blocks", [nested]))], []);
  assert.equal(result.hasDirectory, true);
  assert.equal(result.files.length, 4);
  assert.ok(hasColmapModel(result.files));
  assert.deepEqual(result.files.map((file) => file.webkitRelativePath).sort(), [
    "blocks/block_002/cameras.bin", "blocks/block_002/images.bin",
    "blocks/block_002/points3D.bin", "blocks/block_002/readme.txt",
  ]);
});

test("incomplete or split COLMAP triplets are not mistaken for a model", async () => {
  const files = [fileEntry("model/cameras.bin"), fileEntry("other/images.bin"),
    fileEntry("model/points3D.bin")];
  assert.equal(hasColmapModel(files), false);
  const noPoints = [fileEntry("model/cameras.bin"), fileEntry("model/images.bin")];
  assert.equal(hasColmapModel(noPoints), false);
});

test("single files fall back to the dropped FileList", async () => {
  const dropped = new Blob(["splat"]);
  Object.defineProperty(dropped, "name", { value: "scene.splat" });
  const result = await collectDroppedFiles([], [dropped]);
  assert.deepEqual(result, { files: [dropped], hasDirectory: false });
  assert.ok(result.files[0] instanceof Blob);
  assert.equal(hasColmapModel(result.files), false);
});

test("single-file drag keeps the native File instead of an entry wrapper", async () => {
  const dropped = new Blob(["splat"]);
  Object.defineProperty(dropped, "name", { value: "scene.splat" });
  const result = await collectDroppedFiles([
    {
      kind: "file",
      webkitGetAsEntry: () => fileEntry("scene.splat"),
      getAsFile: () => dropped,
    },
  ], []);
  assert.deepEqual(result.files, [dropped]);
  assert.ok(result.files[0] instanceof Blob);
});

test("directory reader errors are surfaced", async () => {
  const broken = {
    isFile: false,
    isDirectory: true,
    fullPath: "/broken",
    createReader: () => ({ readEntries(_resolve, reject) { reject(new Error("denied")); } }),
  };
  await assert.rejects(collectDroppedFiles([item(broken)], []), /denied/);
});
