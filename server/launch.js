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
const exe = process.platform === "win32" ? "uv.exe" : "uv";
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
// fs.copyFile/cpSync fail here (EPERM/UNKNOWN from Windows CopyFile2), so copy by
// reading and writing each file.
function copyTree(from, to) {
  if (fs.statSync(from).isDirectory()) {
    fs.mkdirSync(to, { recursive: true });
    for (const name of fs.readdirSync(from)) {
      if (name === "__pycache__" || name.endsWith(".egg-info")) continue;
      copyTree(path.join(from, name), path.join(to, name));
    }
  } else {
    fs.writeFileSync(to, fs.readFileSync(from));
  }
}

function prepareProject() {
  const version = JSON.parse(fs.readFileSync(path.join(source, "manifest.json"), "utf8")).version;
  const target = path.join(base, `app-${version}`);
  if (fs.existsSync(path.join(target, ".copied"))) return target;
  // Claude may start two copies at once; each fills its own folder, the first rename wins.
  const temp = path.join(base, `.tmp-${process.pid}-${Date.now()}`);
  fs.mkdirSync(temp, { recursive: true });
  for (const name of ["pyproject.toml", "uv.lock", ".python-version", "README.md", "LICENSE", "manifest.json", "src", "third_party"]) {
    const from = path.join(source, name);
    if (fs.existsSync(from)) copyTree(from, path.join(temp, name));
  }
  fs.writeFileSync(path.join(temp, ".copied"), version);
  try {
    fs.rmSync(target, { recursive: true, force: true });
    fs.renameSync(temp, target);
    log(`copied program files to ${target}`);
  } catch (e) {
    fs.rmSync(temp, { recursive: true, force: true });
    if (!fs.existsSync(path.join(target, ".copied"))) throw e;
  }
  return target;
}

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
// Claude Desktop may leave ${...} placeholders unreplaced (seen on Windows: "${DOCUMENTS}/Inkbridge").
function documentsDir() {
  for (const d of [path.join(home, "OneDrive", "Documents"), path.join(home, "OneDrive", "문서"), path.join(home, "Documents")]) {
    if (fs.existsSync(d)) return d;
  }
  return path.join(home, "Documents");
}
function fill(value) {
  return value.replace(/\$\{(DOCUMENTS|HOME|DESKTOP|DOWNLOADS)\}/g, (_, k) =>
    k === "DOCUMENTS" ? documentsDir() : k === "HOME" ? home : path.join(home, k === "DESKTOP" ? "Desktop" : "Downloads"));
}
const cliArgs = process.argv.slice(2).map(fill).filter((a) => !a.includes("${"));
const env = {
  ...process.env,
  UV_PYTHON_INSTALL_DIR: path.join(base, "python"),
  UV_PROJECT_ENVIRONMENT: path.join(base, "venv"),
  UV_CACHE_DIR: path.join(base, "uv-cache"),
  UV_PYTHON_PREFERENCE: "only-managed",
  UV_LINK_MODE: "copy",
  PYTHONUTF8: "1",
  PYTHONUNBUFFERED: "1",
};
for (const k of Object.keys(env)) {
  if (k.startsWith("SDOCX_BRIDGE_") && typeof env[k] === "string") {
    env[k] = fill(env[k]);
    if (!env[k] || env[k].includes("${")) delete env[k];
  }
}
const args = ["run", "--directory", project, "--extra", "audio", "--quiet", "sdocx-bridge", ...cliArgs];
log(`start: ${uv} ${args.join(" ")}`);
// The MCP messages are relayed through pipes instead of handing our stdin/stdout to the
// child: under Claude Desktop's built-in Node on Windows the inherited stdin reached
// Python already closed, so the server saw end-of-input and quit with code 0.
const child = spawn(uv, args, { env, stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
let toServer = 0, fromServer = 0;
process.stdin.on("data", (d) => { toServer += d.length; child.stdin.write(d); });
process.stdin.on("end", () => { log(`Claude closed the connection (${toServer} bytes in, ${fromServer} out)`); child.stdin.end(); });
child.stdin.on("error", () => {});
child.stdout.on("data", (d) => { fromServer += d.length; process.stdout.write(d); });
child.stderr.on("data", (d) => {
  process.stderr.write(d);
  try { fs.appendFileSync(logFile, d); } catch (_) {}
});
child.on("error", (e) => { log(`could not start uv: ${e.message}`); process.exit(1); });
child.on("exit", (code, signal) => {
  log(`exited with ${code ?? signal} (${toServer} bytes in, ${fromServer} out)`);
  process.exit(code ?? (signal ? 1 : 0));
});
for (const s of ["SIGINT", "SIGTERM"]) process.on(s, () => child.kill(s));
