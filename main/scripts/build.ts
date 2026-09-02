import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";

const targetAliases = {
  "darwin-arm64": "bun-darwin-arm64",
  "darwin-x64": "bun-darwin-x64",
  "linux-arm64": "bun-linux-arm64",
  "linux-x64": "bun-linux-x64",
} as const;

type TargetAlias = keyof typeof targetAliases;

function nativeAlias(): TargetAlias {
  if (process.platform !== "darwin" && process.platform !== "linux") {
    throw new Error(`Unsupported build platform: ${process.platform}`);
  }
  if (process.arch !== "arm64" && process.arch !== "x64") {
    throw new Error(`Unsupported build architecture: ${process.arch}`);
  }
  return `${process.platform}-${process.arch}`;
}

const requested = process.argv[2] || process.env.FIGAI_BUILD_TARGET || nativeAlias();
if (!(requested in targetAliases)) {
  throw new Error(
    `Unknown FigAi build target: ${requested}. Expected ${Object.keys(targetAliases).join(", ")}.`,
  );
}

const alias = requested as TargetAlias;
const output = resolve(process.env.FIGAI_BUILD_OUTPUT || "dist/figai");
mkdirSync(dirname(output), { recursive: true });

const result = Bun.spawnSync({
  cmd: [
    process.execPath,
    "build",
    "--compile",
    `--target=${targetAliases[alias]}`,
    `--outfile=${output}`,
    "src/index.ts",
  ],
  cwd: process.cwd(),
  env: process.env,
  stdout: "inherit",
  stderr: "inherit",
});

if (result.exitCode !== 0) process.exit(result.exitCode);
console.log(`Built ${alias}: ${output}`);
