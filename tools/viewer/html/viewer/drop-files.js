function readEntryFile(entry) {
  return new Promise((resolve, reject) => {
    entry.file((file) => {
      const relativePath = entry.fullPath.replace(/^\/+/, "");
      resolve({
        name: file.name,
        size: file.size,
        type: file.type,
        lastModified: file.lastModified,
        webkitRelativePath: relativePath,
        arrayBuffer: () => file.arrayBuffer(),
      });
    }, reject);
  });
}

function readDirectoryEntries(reader) {
  return new Promise((resolve, reject) => {
    reader.readEntries(resolve, reject);
  });
}

async function walkEntry(entry) {
  if (entry.isFile) return [await readEntryFile(entry)];
  if (!entry.isDirectory) return [];

  const reader = entry.createReader();
  const files = [];
  while (true) {
    const entries = await readDirectoryEntries(reader);
    if (!entries.length) return files;
    for (const child of entries) files.push(...await walkEntry(child));
  }
}

export async function collectDroppedFiles(items, fileList) {
  const files = [];
  let hasDirectory = false;
  for (const item of Array.from(items ?? [])) {
    if (item.kind !== "file") continue;
    const entry = item.webkitGetAsEntry?.();
    if (entry) {
      hasDirectory ||= Boolean(entry.isDirectory);
      files.push(...await walkEntry(entry));
    }
  }
  if (!files.length) files.push(...Array.from(fileList ?? []));
  return { files, hasDirectory };
}

export function hasColmapModel(files) {
  const groups = new Map();
  for (const file of files) {
    const path = (file.webkitRelativePath || file.name).replace(/\\/g, "/");
    const separator = path.lastIndexOf("/");
    const directory = separator < 0 ? "" : path.slice(0, separator);
    const name = separator < 0 ? path : path.slice(separator + 1);
    let names = groups.get(directory);
    if (!names) groups.set(directory, names = new Set());
    if (["cameras.bin", "images.bin", "points3D.bin"].includes(name)) names.add(name);
  }
  return Array.from(groups.values()).some(
    (names) => names.has("cameras.bin") && names.has("images.bin") && names.has("points3D.bin")
  );
}
