"""Wrangler seam: wraps ``npx wrangler ...`` invocations for the
``cloudflare`` publish backend (``cloudflare_backend.py``).

Auth: wrangler resolves its own Cloudflare API token -- from the
``CLOUDFLARE_API_TOKEN`` environment variable, or its own ``wrangler
login`` credential store. jimemo never reads, stores, forwards, or logs
a token itself: every method here just execs ``npx wrangler <subcommand>
...`` as a plain subprocess and lets wrangler's own process resolve auth
from its environment. If jimemo doesn't touch the token, it can't leak
the token.

The one thing wrangler cannot do is read or set a Pages project's
``deployment_configs.*.fail_open`` (Cloudflare's default,
true, serves the static files WITHOUT the tombstone middleware whenever
Functions cannot run, so purged hashes come back). Those two calls go to
the Pages project REST API through ``curl``, run via the same injectable
runner -- and curl, not jimemo, imports the token from ITS environment
(``--variable %CLOUDFLARE_API_TOKEN`` + ``--expand-header``, curl >= 8.3),
so the token is never in Python, in argv, or in a log; ``-q`` is curl's
first argument so no ``.curlrc`` can switch on ``--trace``. jimemo checks
only that the variable is PRESENT, never its value. Consequence: the
environment token is mandatory for every deploy of this backend --
``wrangler login`` credentials cannot call the project API.

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
import re
import shutil
import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

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

CF_API = "https://api.cloudflare.com/client/v4"

#: curl gained ``--variable`` / ``--expand-header`` in 8.3.0 (2023-09).
MIN_CURL = (8, 3, 0)

TOKEN_ENV_REQUIRED_MESSAGE = (
    "the fail-closed check needs CLOUDFLARE_API_TOKEN in the environment "
    "(wrangler login credentials cannot read the Pages project config); "
    "export it and retry"
)

CURL_TOO_OLD_MESSAGE = (
    "cloudflare backend needs curl >= 8.3 (found {found}) to read or set "
    "fail_open on the Pages project; upgrade curl and retry"
)

#: Cloudflare API error codes for a missing/invalid token (10000) or a
#: token without the needed scope (9106): the one refusal where the fix
#: is the token, so the message says so. Only the code is inspected.
AUTH_ERROR_CODES = frozenset({9106, 10000})

#: PATCH body that makes a Functions outage an outage, not a leak.
FAIL_CLOSED_BODY = json.dumps(
    {"deployment_configs": {"production": {"fail_open": False},
                            "preview": {"fail_open": False}}},
    separators=(",", ":"),
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
    subcommand the cloudflare backend needs: listing/creating Cloudflare
    Pages projects for setup, deploying a directory to Cloudflare Pages,
    and reading/writing/listing the tombstone KV namespace. Plus two
    Pages project-config calls (fail_open read/set) that wrangler has no
    subcommand for, made through ``curl`` with the same runner -- see the
    module docstring for why curl and not Python holds the token.

    Account scoping: CloudflareConfig carries an ``account_id``. None of
    these subcommands take an ``--account-id`` flag (checked against
    ``npx wrangler pages deploy --help`` / ``npx wrangler kv key {put,get,
    list} --help``), so it's threaded through via the
    ``CLOUDFLARE_ACCOUNT_ID`` environment variable instead -- the same
    variable wrangler already reads directly from its own environment,
    which is what makes it work when a friend's API token is scoped without
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
        curl: str = "curl",
    ):
        self._run = runner
        self._npx = npx
        self._curl = curl
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
        (cloudflare_backend.py, or the setup wizard) fail fast with
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

    def pages_project_names(self) -> List[str]:
        """Return the names of the account's Cloudflare Pages projects.

        Setup uses this before its first deploy so it can create a
        missing project explicitly. Current Wrangler prompts to create a
        missing Pages project during ``pages deploy --project-name``; that
        prompt fails under jimemo's captured, non-interactive subprocess.
        """
        result = self._invoke(
            ["pages", "project", "list", "--json"],
            "pages project list",
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise PublishError(
                f"wrangler pages project list: could not parse JSON output: {e}"
            )

        if isinstance(payload, list):
            projects = payload
        elif isinstance(payload, dict):
            projects = (
                payload.get("result")
                or payload.get("projects")
                or payload.get("items")
            )
        else:
            projects = None
        if not isinstance(projects, list):
            raise PublishError(
                "wrangler pages project list: expected a JSON list of projects"
            )

        return [
            item["name"]
            for item in projects
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ]

    def pages_project_create(
        self, project: str, branch: str = "main"
    ) -> CompletedProcess:
        """Create a Cloudflare Pages project with the production branch
        jimemo deploys to."""
        return self._invoke(
            [
                "pages", "project", "create", project,
                "--production-branch", branch,
            ],
            "pages project create",
        )

    def curl_version(self) -> Optional[Tuple[int, int, int]]:
        """(major, minor, patch) of the curl on PATH, or None when curl is
        missing, cannot be started (any OSError: not found, permission
        denied, exec-format error), or its ``--version`` line is
        unparseable."""
        try:
            result = self._run([self._curl, "-q", "--version"], None)
        except OSError:
            return None
        if result.returncode != 0:
            return None
        first = (result.stdout or "").splitlines()[:1]
        m = re.match(r"curl (\d+)\.(\d+)\.(\d+)", first[0]) if first else None
        if not m:
            return None
        return int(m.group(1)), int(m.group(2)), int(m.group(3))

    def _cf_api_argv(self, method: str, project: str,
                     body: Optional[str] = None) -> List[str]:
        argv = [
            self._curl, "-q", "-sS", "--max-time", "30",
            "--variable", "%CLOUDFLARE_API_TOKEN",
            "--expand-header", "Authorization: Bearer {{CLOUDFLARE_API_TOKEN}}",
            "-X", method, "-H", "Content-Type: application/json",
        ]
        if body is not None:
            argv += ["-d", body]
        argv.append(f"{CF_API}/accounts/{self.account_id}/pages/projects/{project}")
        return argv

    def _cf_api(self, method: str, project: str,
                body: Optional[str] = None) -> Dict[str, Any]:
        """GET/PATCH the Pages project via curl. Returns the API ``result``
        object. Every failure mode is a PublishError (a refusal upstream):
        no account id, no token in the environment (presence only -- the
        value is never read), curl missing, nonzero exit, non-JSON, or an
        envelope whose ``success`` is not the literal boolean true."""
        if not self.account_id:
            raise PublishError(
                "cloudflare backend needs account_id to read the Pages project "
                "config (set [publish.cloudflare].account_id in "
                "~/.jimemo/config.toml or re-run `jimemo publish setup`)"
            )
        if "CLOUDFLARE_API_TOKEN" not in os.environ:
            raise PublishError(TOKEN_ENV_REQUIRED_MESSAGE)
        try:
            result = self._run(self._cf_api_argv(method, project, body), None)
        except OSError as e:
            # Any process-start failure (not found, permission denied,
            # exec-format error) is a refusal, never a traceback. str(e)
            # carries only errno text and the executable name -- never
            # argv or the environment.
            raise PublishError(
                f"could not run curl for the fail-closed check "
                f"({e.__class__.__name__}: {e}); "
                f"{CURL_TOO_OLD_MESSAGE.format(found='none usable on PATH')}"
            )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise PublishError(
                f"curl {method} Pages project {project!r} failed "
                f"(exit {result.returncode}): {stderr}"
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise PublishError(
                f"Pages project API {method} {project!r}: could not parse JSON output: {e}"
            )
        if (not isinstance(payload, dict) or payload.get("success") is not True
                or not isinstance(payload.get("result"), dict)):
            errors = payload.get("errors") if isinstance(payload, dict) else payload
            hint = ""
            if isinstance(errors, list) and any(
                isinstance(e, dict) and e.get("code") in AUTH_ERROR_CODES
                for e in errors
            ):
                hint = "; check that CLOUDFLARE_API_TOKEN is valid and scoped Pages:Edit"
            raise PublishError(
                f"Pages project API {method} {project!r} did not answer "
                f"success=true: {errors!r}{hint}"
            )
        return payload["result"]

    @staticmethod
    def _open_flags(result: Dict[str, Any]) -> Dict[str, bool]:
        """Strict: an environment is CLOSED only when fail_open is the
        literal JSON boolean false (``is False``; 0 == False in Python).
        Missing keys, null, numbers, strings all read as open."""
        dc = result.get("deployment_configs")
        if not isinstance(dc, dict):
            dc = {}
        flags = {}
        for env in ("production", "preview"):
            cfg = dc.get(env)
            flags[env] = not (isinstance(cfg, dict) and cfg.get("fail_open") is False)
        return flags

    def pages_project_fail_open(self, project: str) -> Dict[str, bool]:
        """``{"production": is_open, "preview": is_open}`` for the project."""
        return self._open_flags(self._cf_api("GET", project))

    def pages_project_set_fail_closed(self, project: str) -> None:
        """PATCH fail_open=false on both environments, then re-read and
        verify; a PATCH that does not stick is a PublishError."""
        self._cf_api("PATCH", project, FAIL_CLOSED_BODY)
        flags = self.pages_project_fail_open(project)
        if flags["production"] or flags["preview"]:
            raise PublishError(
                f"Pages project {project!r} is still fail-open after the PATCH "
                f"(production={'open' if flags['production'] else 'closed'}, "
                f"preview={'open' if flags['preview'] else 'closed'})"
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
    a real KV namespace. ``fail_open`` mirrors the project's flags: a
    fresh mock is an already-provisioned (closed) project;
    ``pages_project_create`` opens both, like Cloudflare;
    ``fail_open=True`` starts open for refusal tests.
    """

    def __init__(
        self,
        deploy_stdout: str = "Deployment complete!\n",
        projects: Optional[List[str]] = None,
        fail_open: bool = False,
    ):
        self.calls: List[tuple] = []
        self._kv: Dict[str, str] = {}
        self._deploy_stdout = deploy_stdout
        self._projects = set(projects or [])
        self.fail_open: Dict[str, bool] = {
            "production": bool(fail_open), "preview": bool(fail_open)}

    def check_available(self) -> bool:
        self.calls.append(("check_available",))
        return True

    def pages_deploy(
        self, project: str, directory: Union[str, Path], branch: str = "main"
    ) -> CompletedProcess:
        self.calls.append(("pages_deploy", project, str(directory), branch))
        return CompletedProcess([], 0, stdout=self._deploy_stdout, stderr="")

    def pages_project_names(self) -> List[str]:
        self.calls.append(("pages_project_names",))
        return sorted(self._projects)

    def pages_project_create(
        self, project: str, branch: str = "main"
    ) -> CompletedProcess:
        self.calls.append(("pages_project_create", project, branch))
        self._projects.add(project)
        # Cloudflare's default for a new project: both environments open.
        self.fail_open = {"production": True, "preview": True}
        return CompletedProcess([], 0, stdout="", stderr="")

    def curl_version(self) -> Optional[Tuple[int, int, int]]:
        self.calls.append(("curl_version",))
        return (8, 7, 1)

    def pages_project_fail_open(self, project: str) -> Dict[str, bool]:
        self.calls.append(("pages_project_fail_open", project))
        return dict(self.fail_open)

    def pages_project_set_fail_closed(self, project: str) -> None:
        self.calls.append(("pages_project_set_fail_closed", project))
        self.fail_open = {"production": False, "preview": False}

    def kv_put(self, namespace_id: str, key: str, value: str) -> None:
        self.calls.append(("kv_put", namespace_id, key, value))
        self._kv[key] = value

    def kv_get(self, namespace_id: str, key: str) -> str:
        self.calls.append(("kv_get", namespace_id, key))
        return self._kv.get(key, "")

    def kv_list(self, namespace_id: str) -> List[Dict[str, Any]]:
        self.calls.append(("kv_list", namespace_id))
        return [{"name": name} for name in sorted(self._kv)]
