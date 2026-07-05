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
  - Installs the bundled middleware/``_headers``/root index (source:
    ``publish/cloudflare/``) INTO the persistent local state directory
    ``cloudflare_backend.py`` deploys on every future ``publish()`` call
    (``~/.jimemo/cloudflare/<project>/`` -- see ``_default_state_dir``,
    imported from cloudflare_backend.py so the two modules can never
    disagree on the path). This is critical, not cosmetic:
    ``CloudflarePublisher.publish()`` always redeploys the ENTIRE state
    directory wholesale (a Pages deploy replaces the whole production
    tree -- see that module's docstring). If the middleware only lived in
    this repo's ``publish/cloudflare/`` and setup deployed THAT directory
    instead, the very next ordinary ``publish()`` call would redeploy the
    state directory in its place -- silently dropping the Functions
    bundle and making ``?purge`` start 405ing with no warning. That is
    exactly the incident this design ports its security model away from
    (notes-ito-com's own `check_deploy_freshness()` exists because of a
    close call in the same shape). Cloudflare only picks up Functions
    from a ``functions/`` directory at the deploy root, so the layout
    installed is ``<state_dir>/functions/_middleware.js``,
    ``<state_dir>/_headers``, ``<state_dir>/index.html`` -- NOT a flat
    copy of ``publish/cloudflare/``, whose ``_middleware.js`` sits at its
    own root purely as a distributable source file.
  - DOES drive ``pages_deploy``, for the initial deploy, against a
    throwaway ALLOWLISTED copy of that state directory -- built via
    cloudflare_backend.py's own ``_build_deploy_dir`` (imported, not
    reimplemented), the exact same helper ``publish()``/``gc()`` use for
    every later call. Never the raw state directory itself, and never
    ``publish/cloudflare/`` directly: this wizard is exactly the place a
    friend re-runs setup against a state directory they already synced
    from another machine (see the single-machine section below), which
    may already hold strays -- ``.git/``, ``.DS_Store``, sync-conflict
    copies -- that must never be deployed. Wrangler creates the Pages
    project automatically on this call if the account doesn't already
    have one by that name, so no separate "create project" call is
    needed.
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

Single-machine limitation (loudly, on purpose)
-----------------------------------------------
Unlike notes-ito-com's git-committed ``public/`` tree, the state
directory this backend deploys from is local-only: no git, no sync, no
`check_deploy_freshness()`-style guard against a stale/empty copy. This
wizard and docs/publish-setup.md both say so explicitly: publish from
ONE machine per Cloudflare project, or sync
``~/.jimemo/cloudflare/<project>/`` (e.g. via git or Dropbox) across
every machine that publishes to it. Publishing from a second machine
whose local state directory is missing prior hashes will deploy over
them -- there is no reliable, seam-available signal to detect this
automatically (``kv_list`` only enumerates tombstoned hashes, not what a
live deploy is currently serving, and there's no HTTP client here to ask
the deployed site directly), so this wizard does not attempt an
automatic freshness check; it only documents the limitation.

Secrets: this module never reads, stores, or prints the value of
CLOUDFLARE_API_TOKEN. It only checks whether that variable is *set*, to
fail fast with a clear message rather than letting the first wrangler
call fail confusingly; wrangler resolves its own auth from that same
environment variable.
"""
import os
import tempfile
from pathlib import Path
from typing import Optional

from ..config import valid_project_name
from ..errors import PublishError
from .cloudflare_backend import (
    CLOUDFLARE_ASSETS_DIR,
    _build_deploy_dir,
    _default_state_dir,
    _install_state_dir_assets,
)
from .wrangler import NO_WRANGLER_MESSAGE

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


def _deploy_argv(project: str, directory) -> str:
    return (
        f"npx wrangler pages deploy {directory} "
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


def _print_single_machine_warning(io: SetupIO, state_dir: Path) -> None:
    io.print(
        "\n"
        "IMPORTANT -- single-machine limitation:\n"
        f"Deploys come from the LOCAL state directory {state_dir}, not "
        "from git (unlike\n"
        "notes-ito-com's committed public/ tree). That directory is the "
        "one source of\n"
        "truth for what's currently deployed. Publish from ONE machine "
        "per project, or\n"
        f"sync {state_dir} (e.g. via git or Dropbox) across every "
        "machine that\n"
        "publishes to it. Publishing from a machine with a stale or "
        "empty copy of this\n"
        "directory will silently drop every previously-published link "
        "on its next deploy."
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
        "    jimemo render ... -o out.html\n"
        "    jimemo publish out.html   # note the printed URL\n"
        "    jimemo publish purge <that URL>\n"
        "    curl -o /dev/null -w '%{http_code}\\n' <that URL>   "
        "# expect 404\n"
        f"  A 200 instead of 404 means the {KV_BINDING_NAME} binding is "
        "missing or\n"
        "  misnamed in the Pages project's dashboard settings."
    )


def _validate_project_name(project: str) -> None:
    """Raise PublishError if ``project`` isn't a valid Cloudflare Pages
    project name (see config.py's valid_project_name(), shared with
    load_config()'s [publish.cloudflare] validation so a hand-edited
    config.toml can never accept a name this wizard would reject). Called
    once, before the project name is used to derive a state directory, a
    base URL, or config.toml content."""
    if not valid_project_name(project):
        raise PublishError(
            f"invalid Cloudflare Pages project name: {project!r} -- must "
            "be lowercase letters, digits, and hyphens only, and may not "
            'start or end with a hyphen (e.g. "jimemo-notes")'
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
    argv, every local file it would install) using placeholder/default
    values, calls no wrangler method, prompts for nothing, requires no
    CLOUDFLARE_API_TOKEN, touches no local filesystem state, and writes
    no config. Fully offline and deterministic.

    Otherwise: validates the token and wrangler are present, then --
    before touching the filesystem, deploying, or making any KV call --
    confirms overwriting config_path if it already exists (aborting
    cleanly if declined, so a friend re-running setup by mistake can
    never trigger a real deploy/KV write against their Cloudflare
    account just by declining the config write at the very end). Then
    prompts for a project name (default DEFAULT_PROJECT_NAME, validated
    against config.py's valid_project_name()), an account id, and a KV
    namespace id (printing manual instructions for the latter, since no
    wrangler-seam call can create/bind one -- see module docstring), installs the
    middleware/_headers/index.html into ``~/.jimemo/cloudflare/<project>/``
    (the SAME local state directory cloudflare_backend.py's
    CloudflarePublisher deploys on every future publish() call -- see
    module docstring for why this placement is load-bearing, not
    cosmetic), deploys an allowlisted copy of that directory (built via
    the same ``_build_deploy_dir`` helper ``publish()``/``gc()`` use --
    never the raw state directory, which may already hold synced strays
    if this is a re-run against a directory synced from another machine),
    runs a best-effort post-deploy KV check, and writes
    ~/.jimemo/config.toml.

    Raises PublishError if CLOUDFLARE_API_TOKEN is unset, wrangler/npx
    is unavailable, or (non-dry-run) a wrangler call fails or the
    project name is invalid.
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

    # Confirm BEFORE any side effect (asset install, deploy, KV call) --
    # not just before the final config write. Declining here must be a
    # clean, total no-op: the previous placement of this same confirm
    # (right before _write_config, at the very end) let the deploy + KV
    # round-trip already happen against a real Cloudflare account before
    # the human got a chance to say no.
    if not dry_run and config_path.exists():
        overwrite = io.confirm(
            f"{config_path} already exists. Overwrite?", default=False
        )
        if not overwrite:
            io.print("aborted: config not written")
            return

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

    # account_id is only known once collected here (unlike
    # CloudflarePublisher's steady-state construction, where it's already
    # in config.toml) -- set it on the Wrangler seam now so Step 3's
    # deploy and the post-deploy KV check below are scoped to the right
    # Cloudflare account for a multi-account token. A harmless no-op
    # attribute on test doubles (e.g. MockWrangler) that don't consult it.
    wrangler.account_id = account_id

    _validate_project_name(project)

    base_url = f"https://{project}.pages.dev"
    # Same helper cloudflare_backend.py's CloudflarePublisher uses by
    # default, imported rather than re-derived, so the two can never
    # disagree on where the deployed state lives.
    state_dir = _default_state_dir(project)

    _print_single_machine_warning(io, state_dir)

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
        f"\nStep 2: install the tombstone/purge middleware into {state_dir}\n"
        "(this directory -- not this repo's publish/cloudflare/ -- is what "
        "gets deployed,\n"
        "on this call and on every future `jimemo publish`; see module "
        "docstring)"
    )
    if dry_run:
        io.print(f"  [dry-run] would create {state_dir}/functions/_middleware.js")
        io.print(f"  [dry-run] would create {state_dir}/_headers")
        io.print(f"  [dry-run] would create {state_dir}/index.html")
    else:
        _install_state_dir_assets(state_dir)
        io.print(
            f"  installed functions/_middleware.js, _headers, index.html "
            f"into {state_dir}"
        )

    io.print(
        f"\nStep 3: deploy {state_dir} to Pages project {project!r}\n"
        "(deploys an allowlisted copy -- only functions/_middleware.js, "
        "_headers, index.html,\nand published hash directories -- never "
        "the raw state directory itself, which may\nalready hold synced "
        "strays like .git/ if this is a re-run against a state directory\n"
        "synced from another machine; see cloudflare_backend.py's "
        "_build_deploy_dir. This\nalso creates the project if your "
        "account doesn't have one by that name yet)"
    )
    if dry_run:
        io.print(
            f"  [dry-run] would deploy an allowlisted copy of {state_dir} "
            "(functions/_middleware.js, _headers, index.html, and "
            "published hash directories only -- .git/, .DS_Store, and "
            "other synced strays excluded) via: "
            f"{_deploy_argv(project, '<allowlisted-copy>')}"
        )
    else:
        # Never deploy state_dir directly -- the SAME allowlist
        # publish()/gc() use, via the shared helper, so a friend re-
        # running setup against a state directory already synced from
        # another machine (and possibly holding .git/, .DS_Store, etc.)
        # can never leak those files. See _build_deploy_dir's docstring.
        with tempfile.TemporaryDirectory(prefix="jimemo-deploy-") as tmp:
            deploy_dir = _build_deploy_dir(state_dir, Path(tmp))
            io.print(f"  running: {_deploy_argv(project, deploy_dir)}")
            wrangler.pages_deploy(project, deploy_dir)

    _post_deploy_binding_check(wrangler, kv_namespace_id, base_url, io, dry_run)

    io.print(f"\nStep 4: write {config_path}")
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

    _write_config(config_path, project, account_id, kv_namespace_id, base_url)
    io.print(f"  wrote {config_path}")
    io.print(
        f"\nDone. Try:\n"
        f"    jimemo render ... -o out.html\n"
        f"    jimemo publish out.html\n"
        f"Reminder: {state_dir} is this project's single source of truth "
        "for what's deployed -- see the single-machine note above."
    )
