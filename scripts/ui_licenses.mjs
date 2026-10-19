// Preserve installed runtime dependency notices with the static UI release.
import { readFile, readdir, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const lock = JSON.parse(
  await readFile(path.join(root, "package-lock.json"), "utf8"),
);
const sections = [];
for (const [location, entry] of Object.entries(lock.packages)) {
  if (!location.startsWith("node_modules/") || entry.dev || entry.devOptional)
    continue;
  const directory = path.join(root, location);
  let files;
  try {
    files = await readdir(directory);
  } catch (error) {
    if (entry.optional && error.code === "ENOENT") continue;
    throw error;
  }
  const manifest = JSON.parse(
    await readFile(path.join(directory, "package.json"), "utf8"),
  );
  const names = files
    .filter((name) => /^(licen[cs]e|copying)(\.|-|$)/i.test(name))
    .sort();
  const supplemental =
    manifest.name === "react-remove-scroll-bar" && manifest.version === "2.3.8"
      ? path.join(root, "third_party/react-remove-scroll-bar.LICENSE")
      : null;
  if (!names.length && !supplemental)
    throw new Error(
      `Runtime dependency lacks a packaged license notice: ${manifest.name}`,
    );
  const notices =
    supplemental && !names.length ? [await readFile(supplemental, "utf8")] : [];
  for (const name of names)
    notices.push(await readFile(path.join(directory, name), "utf8"));
  sections.push(
    `${manifest.name} ${manifest.version}\nLicense: ${manifest.license || entry.license || "see notice"}\n\n${notices.join("\n\n")}`,
  );
}
const destination = path.join(root, "ui/factory/dist");
await mkdir(destination, { recursive: true });
await writeFile(
  path.join(destination, "THIRD_PARTY_LICENSES.txt"),
  "AI Studio UI runtime dependency notices\n\n" +
    sections.join("\n\n" + "=".repeat(72) + "\n\n"),
);
console.log(
  `Preserved license notices for ${sections.length} UI runtime dependencies.`,
);
