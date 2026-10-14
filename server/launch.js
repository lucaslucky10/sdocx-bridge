// Starts the Python MCP server with uv, using Claude Desktop's built-in Node.js.
// Claude's own "uv" extension runtime installs Python under %APPDATA%, where folder
// renames fail on some Windows installs (os error 17). Here uv keeps Python, its
// cache and the virtual environment in ~/.inkbridge instead.
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const home = os.homedir();
const base = path.join(home, ".inkbridge");
const source = path.resolve(__dirname, "..");
const logFile = path.join(base, "launch.log");
fs.mkdirSync(base, { recursive: true });
function log(msg) {
  const line = `${new Date().toISOString()} ${msg}\n`;
  process.stderr.write(line);
  try { fs.appendFileSync(logFile, line); } catch (_) {}
}

// Building the package writes temporary files next to the sources, and file renames
// inside Claude's app-data folder fail on some Windows installs (os error 17).
// So run from a copy of the sources in ~/.inkbridge/app-<version>.
function prepareProject() {
  const version = JSON.parse(fs.readFileSync(path.join(source, "manifest.json"), "utf8")).version;
  const target = path.join(base, `app-${version}`);
  const done = path.join(target, ".copied");
  if (!fs.existsSync(done)) {
    fs.rmSync(target, { recursive: true, force: true });
    fs.mkdirSync(target, { recursive: true });
    for (const name of ["pyproject.toml", "uv.lock", ".python-version", "README.md", "LICENSE", "manifest.json", "src", "third_party"]) {
      const from = path.join(source, name);
      if (fs.existsSync(from)) fs.cpSync(from, path.join(target, name), { recursive: true });
    }
    fs.writeFileSync(done, version);
    log(`copied program files to ${target}`);
  }
  return target;
}
const exe = process.platform === "win32" ? "uv.exe" : "uv";

function findUv() {
  if (process.env.INKBRIDGE_UV && fs.existsSync(process.env.INKBRIDGE_UV)) return process.env.INKBRIDGE_UV;
  const candidates = [];
  const appdata = process.env.APPDATA || path.join(home, "AppData", "Roaming");
  for (const root of [path.join(appdata, "Claude", "uv-runtime"), path.join(home, "Library", "Application Support", "Claude", "uv-runtime")]) {
    try {
      for (const d of fs.readdirSync(root).sort().reverse()) candidates.push(path.join(root, d, exe));
    } catch (_) {}
  }
  candidates.push(path.join(base, "bin", exe), path.join(home, ".local", "bin", exe), path.join(home, ".cargo", "bin", exe));
  for (const dir of (process.env.PATH || "").split(path.delimiter)) if (dir) candidates.push(path.join(dir, exe));
  return candidates.find((c) => { try { return fs.statSync(c).isFile(); } catch (_) { return false; } });
}

const uv = findUv();
if (!uv) {
  log("uv was not found. Install it from https://docs.astral.sh/uv/ and restart Claude.");
  process.exit(1);
}
let project;
try {
  project = prepareProject();
} catch (e) {
  log(`could not copy program files: ${e.stack || e}`);
  process.exit(1);
}
const env = {
  ...process.env,
  UV_PYTHON_INSTALL_DIR: path.join(base, "python"),
  UV_PROJECT_ENVIRONMENT: path.join(base, "venv"),
  UV_CACHE_DIR: path.join(base, "uv-cache"),
  UV_PYTHON_PREFERENCE: "only-managed",
  UV_LINK_MODE: "copy",
  PYTHONUTF8: "1",
};
const args = ["run", "--directory", project, "--extra", "audio", "--quiet", "sdocx-bridge", ...process.argv.slice(2)];
log(`start: ${uv} ${args.join(" ")}`);
// stdin/stdout carry the MCP messages; stderr is shown in Claude's log and kept in launch.log.
const child = spawn(uv, args, { env, stdio: ["inherit", "inherit", "pipe"], windowsHide: true });
child.stderr.on("data", (d) => {
  process.stderr.write(d);
  try { fs.appendFileSync(logFile, d); } catch (_) {}
});
child.on("error", (e) => { log(`could not start uv: ${e.message}`); process.exit(1); });
child.on("exit", (code, signal) => { log(`exited with ${code ?? signal}`); process.exit(code ?? (signal ? 1 : 0)); });
for (const s of ["SIGINT", "SIGTERM"]) process.on(s, () => child.kill(s));
