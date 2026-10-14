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
const project = path.resolve(__dirname, "..");
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
  console.error("Inkbridge: uv was not found. Install it from https://docs.astral.sh/uv/ and restart Claude.");
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
console.error(`Inkbridge: ${uv} ${args.join(" ")}`);
const child = spawn(uv, args, { env, stdio: "inherit", windowsHide: true });
child.on("error", (e) => { console.error(`Inkbridge: could not start uv: ${e.message}`); process.exit(1); });
child.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
for (const s of ["SIGINT", "SIGTERM"]) process.on(s, () => child.kill(s));
