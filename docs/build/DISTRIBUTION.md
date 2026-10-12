# Bundled distribution and clean installation

The development wheel contains the Python supervisor, production React UI, local
font/icon licenses and an explicit copy of the public JVM streaming fixture.
It runs outside the repository; Node and a source checkout are not runtime
requirements. This remains version `0.1.0.dev0`, not a published release. The verified archives
below contain the final code and UI from this local delivery trial.

## Build from a checkout

Python 3.12+, npm and uv are required for this step. Run:

```sh
python3 scripts/build_distribution.py --output /tmp/autoresearch-distribution
```

The script runs `npm ci`, `npm run build`, then `uv build --sdist --wheel`.
`--skip-install` can reuse an existing `web/node_modules`; it still rebuilds the
production UI. Direct `uv build` also works after an explicit UI build, and
refuses a missing UI instead of silently producing an API-only wheel.

The PEP 517 backend stages only package Python modules, project metadata/license,
compiled UI files and eight named public fixture files. It does not copy the
checkout wholesale or write generated resources into the source package.
User-owned `meta/`, campaign databases, measurements, caches, original plugin
files, other examples, frontend source and `node_modules` are absent from the
archives. The compiled UI is required runtime content. Symlinks, hidden UI inputs,
source maps, unexpected UI file types and oversized assets fail the build.

The source archive includes compiled UI assets, the public fixture and the build
backend. A wheel can therefore be built from that archive without Node/npm:

```sh
uv build /tmp/autoresearch-distribution/autoresearch_workbench-0.1.0.dev0.tar.gz \
  --wheel --out-dir /tmp/autoresearch-rebuilt
```

Rebuilding frontend source requires the repository checkout. Build inputs use the
npm lockfile; Python runtime dependencies retain the version ranges in
`pyproject.toml`. Wheel ZIP timestamps are not normalized: matching runtime files
do not imply byte-identical archive hashes.

## Install and start

The supported supervisor hosts are macOS/Linux with Python 3.12+. Git is required
for campaign repositories. Isolated measurements additionally require Docker and
an explicitly prepared image; installing the application does not install those.

```sh
uv venv /tmp/autoresearch-runtime --python 3.12
uv pip install --python /tmp/autoresearch-runtime/bin/python \
  /tmp/autoresearch-distribution/autoresearch_workbench-0.1.0.dev0-py3-none-any.whl
mkdir -p /tmp/autoresearch-home
cd /tmp/autoresearch-home
/tmp/autoresearch-runtime/bin/autoresearch serve --data-dir ./data
```

Open `http://127.0.0.1:8765`. Install-time Python dependencies can require network
access; serving the bundled UI uses local assets. The server binds loopback by
default. Existing authentication and remote-binding restrictions still apply.

An explicit `AUTORESEARCH_WEB_DIST` directory remains authoritative. Otherwise the
server uses bundled `_web` assets, then the checkout's `web/dist` for source or
editable installations. An invalid explicit override produces the existing
API-only fallback, making configuration errors visible.

The installed CLI can create the public fixture without locating the checkout:

```sh
/tmp/autoresearch-runtime/bin/autoresearch demo /tmp/public-jvm-fixture
/tmp/autoresearch-runtime/bin/autoresearch init /tmp/public-jvm-fixture/campaign.json
/tmp/autoresearch-runtime/bin/autoresearch list
```

`demo` creates a standalone Git repository; `init` imports its relative repository
path correctly. These commands do not approve or run benchmarks. Review the
fixture, protocol and available resources before execution.

## Observed clean-install proof, 12 October 2026

The final source passed **366 Python tests**, **44 UI tests**, Ruff lint and
format checks covering 38 Python files, and the production UI build. One upstream
Starlette TestClient deprecation warning remains. The final packaging commands
were `npm --prefix web run build` and
`uv build --sdist --wheel --out-dir /tmp/autoresearch-distribution-verified-20261012`.
The existing locked dependencies were reused; this last build did not reinstall
npm packages. Earlier `npm ci` reported zero vulnerabilities.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `autoresearch_workbench-0.1.0.dev0-py3-none-any.whl` | 475727 | `9e1f9ffbdbff025a582cd20d6619ac69ae1c8e3a4a074399dbe47a6c2fa1631c` |
| `autoresearch_workbench-0.1.0.dev0.tar.gz` | 466207 | `e4b9f740d0e5029765ece384d15931ca6ad690102583440bcdd3d7f45f7ee1c1` |

These files are in `/tmp/autoresearch-distribution-verified-20261012/`.
Earlier archives and receipts remain separate historical snapshots. Independent
inspection verified 47 wheel files and 55 source-archive files, with 41 matching
runtime files, exact allowlists and no links, private validation material or user
data. The final source archive rebuilt offline with `PATH=/usr/bin:/bin`, an
absolute uv executable and Python 3.12, without Node/npm. Its 41 runtime files
match the original wheel byte for byte. ZIP timestamps differ, so the rebuilt
wheel archive hash differs without a runtime-content change. The independent
receipt and build log are `/tmp/autoresearch-sdist-verified-20261012/proof.json`
and `build.log` in the same directory.

The installation smoke command was:

```sh
python3 scripts/verify_distribution.py \
  /tmp/autoresearch-distribution-verified-20261012/autoresearch_workbench-0.1.0.dev0-py3-none-any.whl \
  --output /tmp/autoresearch-clean-install-verified-20261012
```

The verifier created a new Python 3.12 virtual environment outside the checkout,
installed the wheel and dependencies, removed Python/source/UI/auth environment
overrides, and started the installed CLI from a new empty working directory with
a new empty data directory. All nine checks passed:

- Actual HTML, fourteen linked JS/CSS/font/icon assets and both license files over HTTP.
- The installed module inside the new environment and CLI help/capabilities.
- An initially empty campaign API, then installed `demo`, `init`, `list` and
  `status` commands agreeing with the API on the fixture revision and campaign.
- A draft campaign with no experiments, no benchmark execution and zero model spend.

The receipt `/tmp/autoresearch-clean-install-verified-20261012/proof.json` retains
commands, dependency versions, module/data paths, asset hashes and explicit checks.
Its wheel hash matches the archive above. `campaign.json`, `index.html` and
`server.log` retain the observed outputs, including clean server shutdown.
These local temporary receipts are not included in Git or wheels.

The distribution regression tests cover public input selection, rejected
missing/unexpected/symlinked UI, source-archive staging, installed resource
resolution, explicit overrides and bundled fixture creation. Their final results
are included in the full suite above. To repeat the focused checks:

```sh
uv run pytest product_tests/test_distribution.py product_tests/test_api.py
ruff check packaging scripts/build_distribution.py scripts/verify_distribution.py \
  product_tests/test_distribution.py product/autoresearch/api.py product/autoresearch/cli.py
```

No Docker benchmark, model call or external publication ran during the installation
proof. This validates packaging and local runtime on one macOS host; it does not
establish Linux/Windows installation, remote-browser authentication or a portable,
dependency-complete environment for arbitrary application benchmarks.
