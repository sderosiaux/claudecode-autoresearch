# Autoresearch Workbench

Self-hosted, evidence-first software performance experiments. This development
package includes the Python supervisor, production browser UI and public JVM
streaming fixture. It is separate from the original Claude Code plugin.

Requires Python 3.12 or newer. Git is needed for campaign repositories. Docker
and a suitable image are needed for isolated benchmark execution; Node is not
needed to install or run this package. Strict paid Claude execution is currently
disabled because authoritative invoice limits have not been verified.

Install the wheel, then run `autoresearch serve --data-dir ./workbench-data` and
open http://127.0.0.1:8765. Use `autoresearch --help` for CLI commands.
`autoresearch demo ./jvm-demo` creates a committed public fixture; inspect its
README and campaign.json before importing or running it.

The source archive retains compiled UI assets and can build a wheel without
Node. To rebuild the UI from a repository checkout, run
`python3 scripts/build_distribution.py` after installing npm and uv.
