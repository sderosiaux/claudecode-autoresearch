"""Isolated provider process: credentials stay outside benchmark processes."""

import json
from pathlib import Path
import sys

from .provider import ClaudeProvider, ProviderError
from .store import Store


def main():
    try:
        request = json.load(sys.stdin)
        native = request["config"].get("kind") == "claude_code"
        if native:
            from .claude_code import ClaudeCodeProvider

            provider_type = ClaudeCodeProvider
        else:
            provider_type = ClaudeProvider
        provider = provider_type(request["config"], Store(request["database"]))
        proposal = provider.propose(
            request["campaign_id"],
            request["key"],
            request["context"],
            purpose=request["purpose"],
            **(
                {
                    "workspace": Path(request["workspace"]),
                    "artifact_dir": Path(request["artifact_dir"]),
                    "deadline": request["deadline"],
                }
                if native
                else {}
            ),
        )
        if native:
            from .engine import git

            workspace = Path(request["workspace"])
            for path in workspace.rglob("*"):
                if path.is_symlink():
                    raise ProviderError("Native agent produced an unsupported symlink")
            git(workspace, "add", "-N", "--", ".")
            patch = git(
                workspace, "diff", "--no-ext-diff", "--no-textconv", "HEAD", "--"
            )
            if len(patch.encode()) > 500000:
                raise ProviderError("Native agent patch exceeds review bound")
            proposal["patch"] = patch + "\n" if patch else ""
        print(json.dumps({"proposal": proposal}, allow_nan=False))
    except Exception as exc:
        # Controlled exceptions contain safe diagnostics; unexpected exception
        # reprs may include HTTP request bodies and are never emitted.
        print(
            json.dumps(
                {
                    "error": str(exc)
                    if isinstance(exc, (ProviderError, ValueError, KeyError))
                    else "Provider worker failed",
                    "type": type(exc).__name__,
                }
            )
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
