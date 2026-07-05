"""Wrangler seam: wraps ``npx wrangler ...`` invocations for the
``cloudflare`` publish backend (``cloudflare_backend.py``).

Auth: wrangler resolves its own Cloudflare API token -- from the
``CLOUDFLARE_API_TOKEN`` environment variable, or its own ``wrangler
login`` credential store. jimemo never reads, stores, forwards, or logs
a token itself: every method here just execs ``npx wrangler <subcommand>
...`` as a plain subprocess and lets wrangler's own process resolve auth
from its environment. If jimemo doesn't touch the token, it can't leak
the token.

Every wrangler invocation goes through an injectable runner (default:
a real subprocess, list-form argv, NEVER ``shell=True``) so tests can
swap in a fake without ever touching a real wrangler process or the
network. ``MockWrangler`` below goes a step further for backend tests:
it skips subprocess entirely and just records calls against an in-memory
KV dict, so ``cloudflare_backend.py`` tests never need a ``Wrangler`` +
fake-runner pair at all.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, Callable, Dict, List, Optional, Union

from ..errors import PublishError

Runner = Callable[[List[str], Optional[Dict[str, str]]], CompletedProcess]

#: Raised (as a PublishError) by any Wrangler method when the ``npx``
#: binary itself can't be found. Exported so cloudflare_backend.py can
#: raise the identical message from its own upfront check_available()
#: gate, rather than waiting for a subprocess to fail.
NO_WRANGLER_MESSAGE = (
    "cloudflare backend needs Node + wrangler; install Node "
    "(https://nodejs.org) or use the command backend"
)


def _run(argv: List[str], env: Optional[Dict[str, str]] = None) -> CompletedProcess:
    """Default runner: a real subprocess, list-form argv only, never
    shell=True. ``env=None`` (the default) means inherit the parent
    process's environment unchanged -- Wrangler only passes a non-None
    env when an account_id is configured, to layer
    CLOUDFLARE_ACCOUNT_ID on top of that inherited environment (see
    Wrangler._invoke)."""
    return subprocess.run(argv, capture_output=True, text=True, env=env)


class Wrangler:
    """Thin argv-building wrapper around ``npx wrangler``, one method per
    subcommand the cloudflare backend needs: deploying a directory to
    Cloudflare Pages, and reading/writing/listing the tombstone KV
    namespace.

    Account scoping: CloudflareConfig carries an ``account_id``. None of
    these subcommands take an ``--account-id`` flag (checked against
    ``npx wrangler pages deploy --help`` / ``npx wrangler kv key {put,get,
    list} --help``), so it's threaded through via the
    ``CLOUDFLARE_ACCOUNT_ID`` environment variable instead -- the same
    variable wrangler already reads directly from its own environment,
    and the same workaround notes-ito-com's ``bin/notes-publish`` applies
    for itself when a friend's API token is scoped without
    ``User:Memberships:Read`` and wrangler's own account-discovery call
    would otherwise fail or guess wrong. When ``account_id`` is set,
    every subprocess call this class makes runs with
    ``CLOUDFLARE_ACCOUNT_ID`` layered on top of the inherited environment
    (see ``_invoke``). It is never used to touch the Cloudflare API
    token itself, which stays wherever it already was -- inherited from
    the parent environment, untouched and unread by this module.
    """

    def __init__(
        self,
        runner: Runner = _run,
        npx: str = "npx",
        account_id: Optional[str] = None,
    ):
        self._run = runner
        self._npx = npx
        #: Non-secret Cloudflare account id. Public and mutable (not
        #: constructor-only) because setup.py's `jimemo publish setup`
        #: wizard only learns it partway through the wizard -- after
        #: this Wrangler is already constructed -- and sets it here once
        #: known, before the first real call (see setup.py's run_setup).
        self.account_id = account_id

    def check_available(self) -> bool:
        """True if ``npx`` resolves on PATH.

        A cheap presence check -- it does not invoke wrangler itself, so
        it needs no network access and no auth. Callers that skip this
        and call a method anyway still get NO_WRANGLER_MESSAGE from
        _invoke if npx turns out to be missing; this just lets a caller
        (cloudflare_backend.py, or Task 5's setup wizard) fail fast with
        the same clear message before doing any other work.
        """
        return shutil.which(self._npx) is not None

    def _invoke(self, argv: List[str], action: str) -> CompletedProcess:
        env = (
            dict(os.environ, CLOUDFLARE_ACCOUNT_ID=self.account_id)
            if self.account_id
            else None
        )
        try:
            result = self._run([self._npx, "wrangler"] + argv, env)
        except FileNotFoundError:
            raise PublishError(NO_WRANGLER_MESSAGE)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise PublishError(
                f"wrangler {action} failed (exit {result.returncode}): {stderr}"
            )
        return result

    def pages_deploy(
        self, project: str, directory: Union[str, Path], branch: str = "main"
    ) -> CompletedProcess:
        """Deploy every file under ``directory`` as the named project's
        ``branch`` deployment. ``branch`` defaults to "main" (the usual
        Cloudflare Pages production branch) -- explicit, not left to
        wrangler's own git-branch inference, since ``directory`` is
        jimemo's own local state directory rather than a git checkout.
        """
        return self._invoke(
            ["pages", "deploy", str(directory), "--project-name", project,
             "--branch", branch],
            "pages deploy",
        )

    def kv_put(self, namespace_id: str, key: str, value: str) -> None:
        """Write ``value`` under ``key`` in the KV namespace. ``--remote``
        is explicit so this always hits the real (production) namespace,
        never a `wrangler dev`-style local-persisted store."""
        self._invoke(
            ["kv", "key", "put", key, value, "--namespace-id", namespace_id,
             "--remote"],
            "kv key put",
        )

    def kv_get(self, namespace_id: str, key: str) -> str:
        """Read the value stored under ``key``. Raises PublishError (via
        _invoke) if wrangler exits non-zero for any reason, including a
        missing key -- callers that need "does this key exist" semantics
        should consult kv_list first (cloudflare_backend.list() does
        exactly that, only calling kv_get for keys kv_list already
        confirmed are present)."""
        result = self._invoke(
            ["kv", "key", "get", key, "--namespace-id", namespace_id,
             "--text", "--remote"],
            "kv key get",
        )
        return result.stdout.strip()

    def kv_list(self, namespace_id: str) -> List[Dict[str, Any]]:
        """Return every key in the namespace as wrangler's own JSON shape
        (a list of ``{"name": ..., "expiration": ..., "metadata": ...}``
        dicts; only "name" is guaranteed present)."""
        result = self._invoke(
            ["kv", "key", "list", "--namespace-id", namespace_id, "--remote"],
            "kv key list",
        )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise PublishError(
                f"wrangler kv key list: could not parse JSON output: {e}"
            )


class MockWrangler:
    """Test double standing in for Wrangler: no subprocess, no npx, no
    network. Records every call (as a tuple, call name first) in
    ``.calls`` and serves kv_put/kv_get/kv_list from an in-memory dict,
    so a purge followed by a list/gc round-trips realistically without
    a real KV namespace.
    """

    def __init__(self, deploy_stdout: str = "Deployment complete!\n"):
        self.calls: List[tuple] = []
        self._kv: Dict[str, str] = {}
        self._deploy_stdout = deploy_stdout

    def check_available(self) -> bool:
        self.calls.append(("check_available",))
        return True

    def pages_deploy(
        self, project: str, directory: Union[str, Path], branch: str = "main"
    ) -> CompletedProcess:
        self.calls.append(("pages_deploy", project, str(directory), branch))
        return CompletedProcess([], 0, stdout=self._deploy_stdout, stderr="")

    def kv_put(self, namespace_id: str, key: str, value: str) -> None:
        self.calls.append(("kv_put", namespace_id, key, value))
        self._kv[key] = value

    def kv_get(self, namespace_id: str, key: str) -> str:
        self.calls.append(("kv_get", namespace_id, key))
        return self._kv.get(key, "")

    def kv_list(self, namespace_id: str) -> List[Dict[str, Any]]:
        self.calls.append(("kv_list", namespace_id))
        return [{"name": name} for name in sorted(self._kv)]
