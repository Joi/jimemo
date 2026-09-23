"""Publish `mujin/review-evidence` as a check run under the App (kata jibot-code#q4av).

Runs on the bridge runner, from main's copy of the workflow, on three triggers
— none of which executes a pull request's YAML:

  pull_request_target   verify ONE pull request head against kata
  workflow_dispatch     the same, asked for by `repoman-submit` on a re-submission
  workflow_run (gate, requested, event merge_group)
                        verify every member of a merge group, then publish the
                        same check on the merge-group commit

It never checks out, imports or executes anything from the pull request. What it
reads from a pull request is text (the body) and identifiers, and both are
validated before they reach an argv.

The check is a CHECK RUN created with the App's installation token, because the
ruleset binds `mujin/review-evidence` to the App's integration_id: a status or
a check from any other identity does not satisfy it, which is what makes the
gate hold against a pull request that rewrites its own workflows.

Exit status: 0 when a check run was published (success OR failure — a published
failure is this job doing its work); 1 when nothing could be published, which
leaves the required check absent and the pull request unmergeable. Fail closed.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import pr_meta  # noqa: E402
import review_check  # noqa: E402
import app_token  # noqa: E402

API = "https://api.github.com"
CHECK_NAME = review_check.STATUS_CONTEXT
REVIEW_PY = os.path.join(HERE, "..", "vendor", "review.py")
MAX_PAGES = 20


KATA_TOKEN_PATH = "~/.mujin/kata-token"


class BridgeError(Exception):
    pass


def load_kata_token(path, environ):
    """Put the kata token into `environ` for the kata subprocess to inherit.

    It lives on the bridge runner's filesystem, not in GitHub: the fleet's kata
    token is shared, and the only job that can reach this file is main's copy
    of this workflow (the runner group is pinned to it). A file other accounts
    can read is refused, for the same reason as the App key.
    """
    path = os.path.expanduser(path)
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        raise BridgeError("the kata token is not at %s" % path)
    if mode & 0o077:
        raise BridgeError("the kata token at %s is mode %o; it must be 0600"
                          % (path, mode))
    with open(path, encoding="utf-8") as fh:
        token = fh.read().strip()
    if not token:
        raise BridgeError("the kata token file at %s is empty" % path)
    environ["KATA_AUTH_TOKEN"] = token


# --- GitHub, read side. One JSON document per page, parsed per page: a single
# json.loads over a paginated stream fails on the second page.
def _request(url, token, method="GET", body=None, opener=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": "Bearer %s" % token,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "Content-Type": "application/json"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise BridgeError("GitHub %s %s: HTTP %s" % (method, _path(url), exc.code))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise BridgeError("GitHub %s %s: %s" % (method, _path(url), type(exc).__name__))


def _path(url):
    return urllib.parse.urlsplit(url).path


def _pages(url, token, key=None, opener=None):
    """Every item, across pages. Raises rather than returning a partial list."""
    out = []
    sep = "&" if "?" in url else "?"
    for page in range(1, MAX_PAGES + 1):
        doc = _request("%s%sper_page=100&page=%d" % (url, sep, page), token,
                       opener=opener)
        items = doc.get(key) if key else doc
        if not isinstance(items, list):
            raise BridgeError("unexpected reply shape from %s" % _path(url))
        out.extend(items)
        if len(items) < 100:
            return out
    raise BridgeError("more than %d pages from %s — failing closed"
                      % (MAX_PAGES, _path(url)))


def pr_snapshot(repo, number, token, opener=None):
    """The pull request, read ONCE. Verification and publication both use this
    snapshot, so a push between two reads cannot move one commit's evidence
    onto another."""
    doc = _request("%s/repos/%s/pulls/%d" % (API, repo, int(number)), token,
                   opener=opener)
    head = (doc.get("head") or {}).get("sha")
    if not isinstance(head, str) or len(head) != 40:
        raise BridgeError("pull request #%s has no usable head sha" % number)
    return {"number": int(doc["number"]), "head_sha": head,
            "body": doc.get("body") or "", "state": doc.get("state")}


# --- The verdict for one pull request head.
def pr_verdict(snap, kata_bin, runner=None):
    """(conclusion, summary) — conclusion is 'success' or 'failure'."""
    meta, err = pr_meta.parse_body(snap["body"])
    if err:
        return "failure", "the pull request body is malformed: %s" % err
    if "ref" not in meta:
        # A pull request that names no kata issue has nowhere its review
        # evidence could be. That is a hand-opened pull request, and it does
        # not land through this lane.
        return "failure", ("the pull request body names no kata issue, so "
                           "there is no review evidence to read")
    # No waiver author is resolved yet, so every waiver is refused here: the
    # canary proves the reviewed path, and an unattributed waiver must not pass.
    state, why = review_check.verdict(meta["ref"], meta["project"],
                                      snap["head_sha"], kata_bin, REVIEW_PY,
                                      actor=None, runner=runner)
    return ("success" if state == "success" else "failure"), why


# --- The verdict for a merge group.
QUEUE_QUERY = """
query($owner:String!, $name:String!, $branch:String!) {
  repository(owner:$owner, name:$name) {
    mergeQueue(branch:$branch) {
      entries(first:100) {
        nodes { position state headCommit { oid } baseCommit { oid }
                pullRequest { number headRefOid commits { totalCount } } }
      }
    }
  }
}"""


def group_members(repo, base_branch, group_sha, token, opener=None):
    """[{number, head_sha, attributed}] — the pull requests a group commit carries.

    MEASURED on the canary, 2026-09-21: with the queue's REBASE method the
    group's commits are rebased copies, and `GET /commits/{sha}/pulls` attributes
    them to NO pull request. So membership comes from the merge queue itself:
    every entry names its own group commit, its position and its pull request,
    and a group commit carries its own entry plus every entry ahead of it.

    Two cross-checks, both fail-closed: the group must be in the queue at all,
    and the commits it adds to its base must number exactly what its members'
    pull requests hold — an extra commit belongs to nobody.

    The base of that comparison is the entry's own `baseCommit`, from the SAME
    snapshot as the membership. Comparing against the branch NAME races: if an
    entry ahead lands between the two requests, `main...group` shrinks while
    the expected count does not, and a valid group is failed. NOT YET MEASURED:
    what `baseCommit` is for an entry stacked on another (build concurrency
    above 1). Every repo starts at 1, where a group is one entry on main's tip;
    if a stacked entry's base turns out to be the entry ahead, this count fails
    closed and says so, which is the safe way to find out.

    The count depends on the queue's merge method, read from the branch's
    rules (`queue_merge_method`) — never from anything a pull request
    controls. REBASE adds each member's commits and nothing else. MERGE adds
    them plus one merge commit per member, so it is checked as a chain: from
    the group commit, walking first parents, each merge (last member first)
    has exactly two parents, the second is that member's head, and the walk
    ends exactly at the base. Any other method fails closed.
    """
    owner, name = repo.split("/", 1)
    doc = _request("%s/graphql" % API, token, method="POST", opener=opener,
                   body={"query": QUEUE_QUERY,
                         "variables": {"owner": owner, "name": name,
                                       "branch": base_branch}})
    if doc.get("errors"):
        raise BridgeError("the merge queue query failed")
    try:
        entries = doc["data"]["repository"]["mergeQueue"]["entries"]["nodes"]
    except (KeyError, TypeError):
        raise BridgeError("the merge queue query returned no queue for %s" % base_branch)
    method = queue_merge_method(repo, base_branch, token, opener=opener)
    mine = [e for e in entries if (e.get("headCommit") or {}).get("oid") == group_sha]
    if len(mine) != 1:
        raise BridgeError("commit %s is not the head of exactly one merge queue entry"
                          % group_sha[:8])
    ahead = [e for e in entries if e["position"] <= mine[0]["position"]]
    members = [{"number": e["pullRequest"]["number"],
                "head_sha": e["pullRequest"]["headRefOid"],
                "attributed": True} for e in sorted(ahead, key=lambda e: e["position"])]

    base_oid = (mine[0].get("baseCommit") or {}).get("oid")
    if not isinstance(base_oid, str) or len(base_oid) != 40:
        raise BridgeError("the queue entry for %s names no base commit" % group_sha[:8])
    cmp_url = "%s/repos/%s/compare/%s...%s" % (API, repo, base_oid, group_sha)
    commits = _pages(cmp_url, token, key="commits", opener=opener)
    added = len(commits)
    held = sum(e["pullRequest"]["commits"]["totalCount"] for e in ahead)
    fault = None
    if method == "REBASE":
        if added != held:
            fault = "%d commits added, members hold %d" % (added, held)
    elif method == "MERGE":
        if added != held + len(members):
            fault = ("%d commits added, members hold %d plus %d merge commits"
                     % (added, held, len(members)))
        else:
            fault = _merge_chain_fault(commits, members, group_sha, base_oid)
    else:
        fault = "merge method %s is not one this check verifies" % method
    if fault:
        members.append({"number": None, "head_sha": None, "attributed": False,
                        "commit": fault})
    return members


def queue_merge_method(repo, base_branch, token, opener=None):
    """The merge method of the branch's merge queue, from the branch's rules.

    Not GraphQL `mergeQueue.configuration`: MEASURED 2026-09-23, the workflow
    token gets FORBIDDEN on that field while the entries stay readable. The
    rules endpoint needs only metadata read. Exactly one method, named by the
    branch's merge_queue rule(s); anything else raises, and nothing is published.
    """
    url = "%s/repos/%s/rules/branches/%s" % (
        API, repo, urllib.parse.quote(base_branch, safe=""))
    methods = set()
    for rule in _pages(url, token, opener=opener):
        if not (isinstance(rule, dict) and isinstance(rule.get("type"), str)):
            raise BridgeError("the rules for %s hold a malformed rule" % base_branch)
        if rule["type"] == "merge_queue":
            params = rule.get("parameters")
            method = params.get("merge_method") if isinstance(params, dict) else None
            if not isinstance(method, str):
                raise BridgeError("the merge queue for %s names no merge method"
                                  % base_branch)
            methods.add(method)
    if len(methods) != 1:
        raise BridgeError("the rules for %s name %d merge queue methods, not one"
                          % (base_branch, len(methods)))
    return methods.pop()


def _merge_chain_fault(commits, members, group_sha, base_oid):
    """None when the added commits hold the MERGE chain, else what breaks it.

    Walks first parents from the group commit: one two-parent merge per member,
    last member first, whose second parent is that member's head; the walk must
    then stand exactly on the base. Every merge must be one of the added commits.
    """
    by_sha = {}
    for c in commits:
        if not (isinstance(c, dict) and isinstance(c.get("sha"), str)):
            return "the compare lists a commit with no sha"
        by_sha[c["sha"]] = c
    tip = group_sha
    for m in reversed(members):
        commit = by_sha.get(tip)
        if commit is None:
            return "%s is not a commit the group adds" % tip[:8]
        raw = commit.get("parents")
        parents = [p.get("sha") if isinstance(p, dict) else None
                   for p in (raw if isinstance(raw, list) else [])]
        if len(parents) != 2 or not all(isinstance(p, str) for p in parents):
            return ("%s is not a two-parent merge for pull request #%s"
                    % (tip[:8], m["number"]))
        if parents[1] != m["head_sha"]:
            return ("%s merges %s, not pull request #%s's head"
                    % (tip[:8], parents[1][:8], m["number"]))
        tip = parents[0]
    if tip != base_oid:
        return "the merge chain ends at %s, not the base %s" % (tip[:8], base_oid[:8])
    return None


def member_reviewed(repo, member, app_id, token, opener=None):
    """(ok, why): the member's head carries a successful check run, by the App."""
    url = "%s/repos/%s/commits/%s/check-runs?check_name=%s&app_id=%d&filter=latest" % (
        API, repo, member["head_sha"], urllib.parse.quote(CHECK_NAME, safe=""),
        int(app_id))
    runs = _pages(url, token, key="check_runs", opener=opener)
    # Belt and braces on the two filters: the binding is the whole point.
    runs = [r for r in runs if r.get("name") == CHECK_NAME
            and (r.get("app") or {}).get("id") == int(app_id)]
    if not runs:
        return False, ("pull request #%s has no %s check by the App on %s"
                       % (member["number"], CHECK_NAME, member["head_sha"][:8]))
    latest = max(runs, key=lambda r: r.get("id") or 0)
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        return False, ("pull request #%s's %s on %s is %s/%s"
                       % (member["number"], CHECK_NAME, member["head_sha"][:8],
                          latest.get("status"), latest.get("conclusion")))
    return True, ""


def group_verdict(repo, base_branch, group_sha, queue_ref, app_id, token,
                  opener=None):
    members = group_members(repo, base_branch, group_sha, token, opener=opener)
    for m in members:
        if not m["attributed"]:
            return "failure", ("the merge group does not match its queue "
                               "entries (%s) — failing closed" % m.get("commit"))
        if not isinstance(m["head_sha"], str) or len(m["head_sha"]) != 40:
            return "failure", "pull request #%s has no head sha" % m["number"]
    # The queue ref names the LAST pull request in the group and the base
    # branch. It is a cross-check on the membership the API gave, never the
    # membership itself — and it is checked exactly: the last member, this base.
    last, ref_base = review_check.parse_queue_ref(queue_ref)
    if last is None or last != members[-1]["number"] or ref_base != base_branch:
        return "failure", ("the queue ref %r does not name this group's last "
                           "pull request (#%s) on %s — failing closed"
                           % (queue_ref, members[-1]["number"], base_branch))
    for m in members:
        ok, why = member_reviewed(repo, m, app_id, token, opener=opener)
        if not ok:
            return "failure", why
    return "success", "every member reviewed at its own head: %s" % ", ".join(
        "#%d@%s" % (m["number"], m["head_sha"][:8]) for m in members)


# --- GitHub, write side: the one thing the App token is used for.
def publish(repo, sha, conclusion, summary, check_token, opener=None):
    body = {"name": CHECK_NAME, "head_sha": sha, "status": "completed",
            "conclusion": conclusion,
            "output": {"title": "review evidence: %s" % conclusion,
                       "summary": summary[:60000]}}
    doc = _request("%s/repos/%s/check-runs" % (API, repo), check_token,
                   method="POST", body=body, opener=opener)
    return doc.get("id")


def main(env=None, opener=None, runner=None, mint=None):
    env = env if env is not None else os.environ
    repo = env["GITHUB_REPOSITORY"]
    read_token = env["GITHUB_TOKEN"]
    app_id = int(env["MUJIN_APP_ID"])
    installation_id = int(env["MUJIN_APP_INSTALLATION_ID"])
    kata_bin = env.get("KATA_BIN", "/opt/homebrew/bin/kata")
    event = env.get("BRIDGE_EVENT", "")

    try:
        if event in ("pull_request_target", "workflow_dispatch"):
            snap = pr_snapshot(repo, env["BRIDGE_PR_NUMBER"], read_token,
                               opener=opener)
            event_head = env.get("BRIDGE_PR_HEAD_SHA", "")
            if event_head and event_head != snap["head_sha"]:
                # The event is about a head the pull request has moved past.
                # The newer head has its own event; publishing here would put
                # a verdict on a commit this run did not verify.
                print("stale event: payload head %s, current head %s — leaving "
                      "it to the newer run" % (event_head[:8], snap["head_sha"][:8]))
                return 0
            if snap["state"] != "open":
                print("pull request #%d is %s — nothing to verify"
                      % (snap["number"], snap["state"]))
                return 0
            sha = snap["head_sha"]
            if runner is None:
                load_kata_token(env.get("MUJIN_KATA_TOKEN", KATA_TOKEN_PATH),
                                os.environ)
            conclusion, summary = pr_verdict(snap, kata_bin, runner=runner)
        elif event == "merge_group":
            sha = env["BRIDGE_GROUP_SHA"]
            conclusion, summary = group_verdict(
                repo, env.get("BRIDGE_BASE_BRANCH", "main"), sha,
                env.get("BRIDGE_QUEUE_REF", ""), app_id, read_token,
                opener=opener)
        else:
            raise BridgeError("unexpected trigger %r" % event)

        token = (mint or app_token.installation_token)(
            app_id, installation_id, repo.split("/", 1)[1])
        check_id = publish(repo, sha, conclusion, summary, token, opener=opener)
    except (BridgeError, app_token.TokenError, KeyError, ValueError) as exc:
        print("review-evidence: could not publish a verdict: %s" % exc,
              file=sys.stderr)
        return 1

    print("review-evidence: %s on %s (check run %s): %s"
          % (conclusion, sha[:8], check_id, summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
