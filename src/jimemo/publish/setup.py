"""``jimemo publish setup`` -- an interactive (and ``--dry-run``) wizard
that provisions the ``cloudflare`` publish backend for someone who
doesn't already have a publish site of their own (unlike Joi, who keeps
notes-publish/notes.ito.com authoritative via the ``command`` backend
instead -- see command_backend.py). Produces the
``[publish.cloudflare]`` section load_config() otherwise errors asking
for (config.py's ConfigError message literally says "run `jimemo
publish setup`").

What this wizard can and cannot automate, and why
--------------------------------------------------
wrangler.py's Wrangler seam is deliberately narrow: ``check_available``,
``pages_deploy``, ``kv_put``, ``kv_get``, ``kv_list`` -- exactly the five
operations cloudflare_backend.py needs for publish/purge/list/gc. There
is no "create a Pages project", "create a KV namespace", or "bind a KV
namespace to a Pages project" method, and this wizard does not add any:
those are one-time account-setup actions, not things the steady-state
publish/purge/list/gc path ever needs again, so growing the seam to cover
them would be permanent surface area for a one-time job.

Concretely, this wizard:
  - DOES drive ``pages_deploy`` to upload ``publish/cloudflare/`` (the
    ported middleware + ``_headers`` + root index) to the Pages project.
    Wrangler creates the Pages project automatically on this call if the
    account doesn't already have one by that name, so no separate
    "create project" call is needed.
  - Does NOT create a KV namespace or bind it to the Pages project as
    ``TOMBSTONES`` -- there is no wrangler-seam call for either. It
    prints the exact manual command / dashboard step for both, in every
    mode (not just --dry-run), and asks the human to supply the
    resulting account id and KV namespace id.
  - DOES run a best-effort post-deploy check: a kv_put/kv_get round trip
    against the namespace id the human supplied. This can only prove the
    KV namespace itself is reachable and writable -- it cannot prove the
    Pages Function's ``env.TOMBSTONES`` binding actually resolves to it,
    since that only shows up on a live HTTP request against the deployed
    site (see docs/publish-setup.md for the manual live-verification
    steps).

Secrets: this module never reads, stores, or prints the value of
CLOUDFLARE_API_TOKEN. It only checks whether that variable is *set*, to
fail fast with a clear message rather than letting the first wrangler
call fail confusingly; wrangler resolves its own auth from that same
environment variable.
"""
import os
from pathlib import Path
from typing import Optional

from .._paths import REPO_ROOT
from ..errors import PublishError
from .wrangler import NO_WRANGLER_MESSAGE

#: publish/cloudflare/ -- the middleware + _headers + index.html this
#: wizard deploys. Read via REPO_ROOT (from _paths.py) rather than a
#: path relative to this file, matching how tests/test_middleware_asset.py
#: locates the same directory.
CLOUDFLARE_ASSETS_DIR = REPO_ROOT / "publish" / "cloudflare"

DEFAULT_PROJECT_NAME = "jimemo-notes"

#: The KV binding name the ported middleware reads as `env.TOMBSTONES`
#: (publish/cloudflare/_middleware.js). MUST match exactly -- see
#: test_setup.py's test_kv_binding_name_matches_middleware, which cross-
#: checks this constant against the middleware source directly.
KV_BINDING_NAME = "TOMBSTONES"

#: Key this wizard's best-effort post-deploy check writes and reads back.
#: Namespaced so it can never collide with a real published hash (hashes
#: are exactly 24 lowercase hex characters; this is neither).
_SETUP_CHECK_KEY = "__jimemo_setup_check__"
_SETUP_CHECK_VALUE = "ok"

TOKEN_MISSING_MESSAGE = (
    "CLOUDFLARE_API_TOKEN is not set.\n"
    "\n"
    "Create a Cloudflare API token at "
    "https://dash.cloudflare.com/profile/api-tokens with scopes:\n"
    "    Account | Cloudflare Pages | Edit\n"
    "    Account | Workers KV Storage | Edit\n"
    "\n"
    "Then export it in your shell -- jimemo never stores this token; "
    "wrangler reads it\n"
    "directly from the environment:\n"
    "    export CLOUDFLARE_API_TOKEN=...\n"
    "\n"
    "Re-run `jimemo publish setup` once it's set."
)


class SetupIO:
    """Prompt/output seam for run_setup(): the real CLI passes RealIO
    (wraps print()/input()); tests pass a fake that scripts prompt
    answers and records everything printed, so the wizard is fully
    testable without a real terminal."""

    def print(self, message: str = "") -> None:
        raise NotImplementedError

    def prompt(self, message: str, default: Optional[str] = None) -> str:
        raise NotImplementedError

    def confirm(self, message: str, default: bool = False) -> bool:
        raise NotImplementedError


class RealIO(SetupIO):
    """The real CLI's IO: stdlib print()/input()."""

    def print(self, message: str = "") -> None:
        print(message)

    def prompt(self, message: str, default: Optional[str] = None) -> str:
        suffix = f" [{default}]" if default else ""
        raw = input(f"{message}{suffix}: ").strip()
        return raw if raw else (default or "")

    def confirm(self, message: str, default: bool = False) -> bool:
        suffix = " [Y/n]" if default else " [y/N]"
        raw = input(f"{message}{suffix}: ").strip().lower()
        if not raw:
            return default
        return raw in ("y", "yes")


def _deploy_argv(project: str) -> str:
    return (
        f"npx wrangler pages deploy {CLOUDFLARE_ASSETS_DIR} "
        f"--project-name {project} --branch main"
    )


def _kv_put_argv(kv_namespace_id: str) -> str:
    return (
        f"npx wrangler kv key put {_SETUP_CHECK_KEY} {_SETUP_CHECK_VALUE} "
        f"--namespace-id {kv_namespace_id} --remote"
    )


def _kv_get_argv(kv_namespace_id: str) -> str:
    return (
        f"npx wrangler kv key get {_SETUP_CHECK_KEY} "
        f"--namespace-id {kv_namespace_id} --text --remote"
    )


def _print_intro(io: SetupIO) -> None:
    io.print(
        "jimemo publish setup\n"
        "=====================\n"
        "\n"
        "Provisions a free Cloudflare Pages site for the \"cloudflare\" "
        "publish backend:\n"
        "self-hosted unlisted-link publishing (mirrors notes.ito.com's "
        "model -- a 24-hex\n"
        "hash path is the access control, read and purge are symmetric, "
        "purging\n"
        "tombstones a hash rather than deleting it outright).\n"
        "\n"
        "You will need:\n"
        "  1. A Cloudflare account (the free tier is enough).\n"
        "  2. A Cloudflare API token scoped to Cloudflare Pages:Edit and "
        "Workers KV\n"
        "     Storage:Edit, exported as CLOUDFLARE_API_TOKEN in your "
        "shell. jimemo\n"
        "     never stores this token -- it stays in your environment / "
        "wrangler's own\n"
        "     credential store. Only non-secret ids (project name, "
        "account id, KV\n"
        "     namespace id, base URL) get written to ~/.jimemo/config.toml.\n"
        "  3. Node + npx (wrangler runs via `npx wrangler`).\n"
    )


def _print_kv_instructions(io: SetupIO, project: str) -> None:
    io.print(
        "\n"
        "Cloudflare KV namespace + binding (manual -- no wrangler-seam "
        "call automates\n"
        "this; see setup.py's module docstring for why):\n"
        "  a. Create a KV namespace, if you don't have one for this "
        "project yet:\n"
        f"       npx wrangler kv namespace create {project}-tombstones\n"
        "     Copy the \"id\" field from its output.\n"
        "  b. In the Cloudflare dashboard, open this Pages project's "
        "Settings ->\n"
        "     Functions -> KV namespace bindings, and bind that "
        "namespace under the\n"
        f"     exact binding name {KV_BINDING_NAME} -- the deployed "
        f"middleware reads it as\n"
        f"     env.{KV_BINDING_NAME}; a different name (or no binding) "
        "makes tombstone\n"
        "     checks silently no-op (open-fail, not fail-safe)."
    )


def _post_deploy_binding_check(
    wrangler, kv_namespace_id: str, base_url: str, io: SetupIO, dry_run: bool
) -> None:
    """Best-effort check that the KV namespace itself is reachable and
    writable, via the same kv_put/kv_get the Wrangler seam already
    exposes. This is NOT a check that the Pages project's env.TOMBSTONES
    binding is wired correctly -- writing/reading a key directly against
    a KV namespace id says nothing about whether a *different* thing
    (the deployed Worker) has that namespace bound under a particular
    name. Proving the binding itself requires an actual HTTP request
    against the deployed site, which this wizard has no HTTP client to
    make -- see docs/publish-setup.md for the manual steps.
    """
    io.print("\nPost-deploy binding check (best-effort):")
    put_argv = _kv_put_argv(kv_namespace_id)
    get_argv = _kv_get_argv(kv_namespace_id)

    if dry_run:
        io.print(f"  [dry-run] would run: {put_argv}")
        io.print(f"  [dry-run] would run: {get_argv}")
        io.print(
            "  [dry-run] this only proves the KV namespace itself is "
            "reachable -- it\n"
            f"  cannot prove the Pages project's env.{KV_BINDING_NAME} "
            "binding is wired\n"
            "  correctly, since that requires a live HTTP request "
            "against the deployed site."
        )
        return

    io.print(f"  running: {put_argv}")
    wrangler.kv_put(kv_namespace_id, _SETUP_CHECK_KEY, _SETUP_CHECK_VALUE)
    io.print(f"  running: {get_argv}")
    readback = wrangler.kv_get(kv_namespace_id, _SETUP_CHECK_KEY)

    if readback != _SETUP_CHECK_VALUE:
        io.print(
            f"  WARNING: read back {readback!r}, expected "
            f"{_SETUP_CHECK_VALUE!r} -- the KV\n"
            "  namespace id or your API token's KV scope looks wrong. "
            "Publish/purge will\n"
            "  likely fail against this namespace until that's fixed."
        )
        return

    io.print(
        "  ok: the KV namespace is reachable and writable.\n"
        f"  NOTE: this does not yet prove env.{KV_BINDING_NAME} is bound "
        "in the Pages\n"
        "  project's Functions settings -- that only shows up on a live "
        "request. Verify\n"
        "  it end to end once, manually:\n"
        "    jimemo render ... | jimemo publish   # note the printed URL\n"
        "    jimemo publish purge <that URL>\n"
        "    curl -o /dev/null -w '%{http_code}\\n' <that URL>   "
        "# expect 404\n"
        f"  A 200 instead of 404 means the {KV_BINDING_NAME} binding is "
        "missing or\n"
        "  misnamed in the Pages project's dashboard settings."
    )


def _escape_toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _write_config(
    config_path: Path, project: str, account_id: str, kv_namespace_id: str,
    base_url: str,
) -> None:
    """Write ~/.jimemo/config.toml's [publish]/[publish.cloudflare]
    sections. NEVER writes a token -- see config.py's schema docstring
    and this module's own docstring."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "[publish]\n"
        'backend = "cloudflare"\n'
        "\n"
        "[publish.cloudflare]\n"
        f'project = "{_escape_toml_string(project)}"\n'
        f'account_id = "{_escape_toml_string(account_id)}"\n'
        f'kv_namespace_id = "{_escape_toml_string(kv_namespace_id)}"\n'
        f'base_url = "{_escape_toml_string(base_url)}"\n'
    )
    config_path.write_text(content)


def run_setup(dry_run: bool, wrangler, config_path: Path, io: SetupIO) -> None:
    """Run the cloudflare-backend setup wizard.

    In --dry-run: prints the full plan (every step, every wrangler
    argv) using placeholder/default values, calls no wrangler method,
    prompts for nothing, requires no CLOUDFLARE_API_TOKEN, and writes no
    config. Fully offline and deterministic.

    Otherwise: validates the token and wrangler are present, prompts for
    a project name (default DEFAULT_PROJECT_NAME), an account id, and a
    KV namespace id (printing manual instructions for the latter, since
    no wrangler-seam call can create/bind one -- see module docstring),
    deploys publish/cloudflare/ to the Pages project (which also creates
    the project on first use), runs a best-effort post-deploy KV check,
    and writes ~/.jimemo/config.toml (confirming first if it already
    exists).

    Raises PublishError if CLOUDFLARE_API_TOKEN is unset, wrangler/npx
    is unavailable, or (non-dry-run) a wrangler call fails.
    """
    _print_intro(io)

    if dry_run:
        io.print("[dry-run] would check: CLOUDFLARE_API_TOKEN is set")
    elif not os.environ.get("CLOUDFLARE_API_TOKEN"):
        raise PublishError(TOKEN_MISSING_MESSAGE)

    if dry_run:
        io.print("[dry-run] would check: npx/wrangler available on PATH")
    elif not wrangler.check_available():
        raise PublishError(NO_WRANGLER_MESSAGE)

    if dry_run:
        project = DEFAULT_PROJECT_NAME
        account_id = "<ACCOUNT_ID>"
        kv_namespace_id = "<KV_NAMESPACE_ID>"
        io.print(
            f"\n[dry-run] would prompt for a Cloudflare Pages project "
            f"name (default {DEFAULT_PROJECT_NAME!r}); using the default."
        )
        io.print(
            "[dry-run] would prompt for your Cloudflare account id; "
            f"using a placeholder ({account_id})."
        )
    else:
        io.print("\nStep 1: Cloudflare Pages project")
        project = io.prompt(
            "Cloudflare Pages project name", default=DEFAULT_PROJECT_NAME
        )
        account_id = io.prompt("Cloudflare account id")
        while not account_id:
            io.print("An account id is required.")
            account_id = io.prompt("Cloudflare account id")

    base_url = f"https://{project}.pages.dev"

    _print_kv_instructions(io, project)
    if dry_run:
        io.print(
            f"[dry-run] would prompt for the KV namespace id; using a "
            f"placeholder ({kv_namespace_id})."
        )
    else:
        kv_namespace_id = io.prompt("Cloudflare KV namespace id")
        while not kv_namespace_id:
            io.print("A KV namespace id is required.")
            kv_namespace_id = io.prompt("Cloudflare KV namespace id")

    io.print(
        f"\nStep 2: deploy {CLOUDFLARE_ASSETS_DIR} to Pages project "
        f"{project!r}\n"
        "(this also creates the project if your account doesn't have "
        "one by that\nname yet)"
    )
    deploy_argv = _deploy_argv(project)
    if dry_run:
        io.print(f"  [dry-run] would run: {deploy_argv}")
    else:
        io.print(f"  running: {deploy_argv}")
        wrangler.pages_deploy(project, CLOUDFLARE_ASSETS_DIR)

    _post_deploy_binding_check(wrangler, kv_namespace_id, base_url, io, dry_run)

    io.print(f"\nStep 3: write {config_path}")
    if dry_run:
        io.print(
            "  [dry-run] would write:\n"
            "    [publish]\n"
            '    backend = "cloudflare"\n'
            "\n"
            "    [publish.cloudflare]\n"
            f'    project = "{project}"\n'
            f'    account_id = "{account_id}"\n'
            f'    kv_namespace_id = "{kv_namespace_id}"\n'
            f'    base_url = "{base_url}"\n'
            "  (no token -- see module docstring)"
        )
        return

    if config_path.exists():
        overwrite = io.confirm(
            f"{config_path} already exists. Overwrite?", default=False
        )
        if not overwrite:
            io.print("aborted: config not written")
            return

    _write_config(config_path, project, account_id, kv_namespace_id, base_url)
    io.print(f"  wrote {config_path}")
    io.print(f"\nDone. Try: jimemo render ... | jimemo publish")
