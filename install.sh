#!/bin/bash
# jimemo installer: one clone, wired into every harness on this machine.
# `git pull` in the clone updates every harness that points at it — there
# is exactly one copy of the code and one copy of the skill, never a
# fork-per-harness (see the fresheyes lesson: three unsynced copies of the
# same skill in Claude/Codex/Amplifier drifted out of sync and had to be
# patched three separate times; symlinks avoid that class of bug entirely).
#
# The CLI entry point is NOT a symlink (jimemo#p0nk). A symlink to the
# launcher runs whatever `python3` the CALLING shell resolves -- so the
# interpreter this script verified at install time and the one `jimemo`
# later ran with could differ, and on a machine whose interactive shell
# selects an older python3 the installed command refused in exactly the
# shells it was installed for. Instead this script picks ONE interpreter,
# verifies it by running it, and writes ~/.local/bin/jimemo as a small
# shell script that runs the launcher with that interpreter's absolute
# path. The skill registrations stay symlinks.
#
# Portable to the bash 3.2 shipped on macOS: no associative arrays, no
# `mapfile`, no `${var,,}`, and no `set -u` (bash 3.2 treats a reference
# to an empty array under `set -u` as an unbound-variable error). Works on
# a minimal PATH: dirname, readlink, mkdir, ln, rm, chmod, mv, cat.

set -eo pipefail

# ---------------------------------------------------------------------
# repo root: the directory containing this script, resolved through any
# symlinks, so the installer works regardless of cwd or how it was
# invoked.
# ---------------------------------------------------------------------
resolve_repo_root() {
    src="${BASH_SOURCE[0]}"
    while [ -h "$src" ]; do
        dir="$(cd -P "$(dirname "$src")" && pwd)"
        src="$(readlink "$src")"
        case "$src" in
            /*) ;;
            *) src="$dir/$src" ;;
        esac
    done
    cd -P "$(dirname "$src")" && pwd
}
REPO_ROOT="$(resolve_repo_root)"

CLI_SOURCE="$REPO_ROOT/jimemo"
SKILL_SOURCE="$REPO_ROOT/skill"

if [ ! -f "$CLI_SOURCE" ] || [ ! -f "$SKILL_SOURCE/SKILL.md" ]; then
    echo "install.sh: error: $REPO_ROOT doesn't look like a jimemo checkout" \
        "(missing jimemo or skill/SKILL.md)" >&2
    exit 1
fi

CLI_TARGET="$HOME/.local/bin/jimemo"
CLAUDE_TARGET="$HOME/.claude/skills/jimemo"
CODEX_TARGET="$HOME/.codex/skills/jimemo"

# Amplifier has no config-file registration step for personal skills: its
# tool-skills module discovers any directory containing a SKILL.md under
# $AMPLIFIER_SKILLS_DIR (if set) or ~/.amplifier/skills, symlinks
# included. So "register with Amplifier" is exactly the same symlink
# trick, gated on Amplifier actually being present on this machine.
AMPLIFIER_DETECTED=0
if [ -n "${AMPLIFIER_SKILLS_DIR:-}" ]; then
    AMPLIFIER_DETECTED=1
elif [ -d "$HOME/.amplifier" ]; then
    AMPLIFIER_DETECTED=1
fi
AMPLIFIER_SKILLS_DIR="${AMPLIFIER_SKILLS_DIR:-$HOME/.amplifier/skills}"
AMPLIFIER_TARGET="$AMPLIFIER_SKILLS_DIR/jimemo"

# The interpreter candidates, tried in this order; the first one that
# passes the checks below is bound. `python3` first keeps the documented
# contract (arrange PATH, run the installer) and today's result wherever
# `python3` is already fine; the versioned names are the two minor series
# the CI matrix tests, closest to the floor first. Every candidate is
# verified by RUNNING it -- `python3.13` on PATH says nothing about whether
# it is 3.13.3 or 3.13.6.
PYTHON_CANDIDATES="python3 python3.13 python3.14"

# `--python PATH` beats `JIMEMO_PYTHON=PATH`; with either, only that
# interpreter is tried.
PYTHON_OVERRIDE="${JIMEMO_PYTHON:-}"
PYTHON_OVERRIDE_FROM=''
if [ -n "$PYTHON_OVERRIDE" ]; then
    PYTHON_OVERRIDE_FROM='JIMEMO_PYTHON'
fi

# The first lines of an entry point this script wrote. `--uninstall` and
# `jimemo doctor` recognise the file by the marker and read the two value
# lines after it; keep the three spellings in step with
# src/jimemo/_entry_point.py.
ENTRY_POINT_MARKER="# jimemo-entry-point:"

DRY_RUN=0
UNINSTALL=0

NL='
'

print_usage() {
    cat <<'EOF'
Usage: install.sh [--dry-run] [--uninstall] [--python PATH] [--help]

Installs the jimemo CLI as ~/.local/bin/jimemo and registers the jimemo
skill with every agent harness that reads a filesystem skills dir (Claude
Code -- and pi, which reads the same dir; Codex; Amplifier if present).
Claude Desktop / Cowork loads skills app-side, not from disk, so it gets
the CLI only (which works in its local-agent mode). One clone; `git pull`
updates every harness.

The CLI entry point is a small shell script bound to ONE Python
interpreter, chosen here and verified by running it: Python >= 3.13.6, a
final release. It runs that interpreter by absolute path, so the installed
`jimemo` does not depend on which python3 the calling shell finds.
Candidates, tried in order; the first supported one is bound:

    python3  python3.13  python3.14

If that interpreter is later removed or replaced by one below the floor,
`jimemo` says so in one line; re-run ./install.sh to bind another.

  --python PATH   bind this interpreter instead of trying the candidates
                  (or JIMEMO_PYTHON=PATH in the environment; the flag wins).
                  It is verified the same way, and nothing else is tried.
  --dry-run       print every action without doing it
  --uninstall     remove exactly what this script wrote -- the entry point
                  and the skill symlinks; the clone is untouched
  -h, --help      show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --uninstall) UNINSTALL=1 ;;
        --python)
            if [ $# -lt 2 ] || [ -z "$2" ]; then
                echo "install.sh: --python needs a path" >&2
                print_usage >&2
                exit 1
            fi
            PYTHON_OVERRIDE="$2"
            PYTHON_OVERRIDE_FROM='--python'
            shift
            ;;
        --python=*)
            PYTHON_OVERRIDE="${1#--python=}"
            if [ -z "$PYTHON_OVERRIDE" ]; then
                echo "install.sh: --python needs a path" >&2
                print_usage >&2
                exit 1
            fi
            PYTHON_OVERRIDE_FROM='--python'
            ;;
        -h|--help)
            print_usage
            exit 0
            ;;
        *)
            echo "install.sh: unknown option: $1" >&2
            print_usage >&2
            exit 1
            ;;
    esac
    shift
done

note() {
    echo "note     $*"
}

# ---------------------------------------------------------------------
# link_one TARGET SOURCE LABEL
#
# Idempotent: ln -sfn replaces an existing symlink outright (no nesting,
# no stacking). A non-symlink already at TARGET is left alone -- it's
# not ours to clobber.
# ---------------------------------------------------------------------
link_one() {
    target="$1"
    source="$2"
    label="$3"
    parent="$(dirname "$target")"

    if [ -e "$target" ] && [ ! -L "$target" ]; then
        echo "warn     $target already exists and is not a symlink -- leaving it alone ($label)" >&2
        return 0
    fi

    if [ "$DRY_RUN" = "1" ]; then
        if [ ! -d "$parent" ]; then
            echo "[dry-run] would create directory: $parent"
        fi
        echo "[dry-run] would link: $target -> $source ($label)"
        return 0
    fi

    mkdir -p "$parent"
    ln -sfn "$source" "$target"
    echo "linked   $target -> $source ($label)"
}

# ---------------------------------------------------------------------
# unlink_one TARGET SOURCE LABEL
#
# Only removes TARGET if it is a symlink pointing at exactly SOURCE --
# i.e. a symlink this installer (this repo) created. A symlink pointing
# elsewhere, or a real file/dir, is left alone and reported.
# ---------------------------------------------------------------------
unlink_one() {
    target="$1"
    source="$2"
    label="$3"

    if [ -L "$target" ]; then
        current="$(readlink "$target")"
        if [ "$current" = "$source" ]; then
            if [ "$DRY_RUN" = "1" ]; then
                echo "[dry-run] would remove: $target ($label)"
            else
                rm "$target"
                echo "removed  $target ($label)"
            fi
        else
            echo "skip     $target is a symlink to $current, not $source -- leaving it alone ($label)" >&2
        fi
    elif [ -e "$target" ]; then
        echo "skip     $target exists and is not a symlink -- leaving it alone ($label)" >&2
    fi
}

# ---------------------------------------------------------------------
# The interpreter checks. probe_python asks an interpreter for its version
# and applies the floor; executable_of asks it where it lives; try_python
# chains them and binds the result. Every rejection prints ONE line naming
# the candidate and the reason, so a user whose candidates all fail sees
# why each one did, not just that none did.
# ---------------------------------------------------------------------

# probe_python CMD LABEL -- 0 if CMD is a final release at or above the
# floor (PY_VER then holds its dotted version); 1, with a reason on
# stderr, otherwise.
#
# The floor has a MICRO component: what lint needs from html.parser landed
# in 3.13.4 and 3.13.6, so a major/minor check would accept a 3.13.3 that
# still reads attributes differently than a browser (jimemo#gaga; the
# per-release measurement is in src/jimemo/__init__.py). Kept in the bash
# 3.2 style the rest of this file uses: no arrays, plain -lt.
probe_python() {
    _cmd="$1"
    _label="$2"

    # Ask once, for all four fields. `|| PY_PARTS=''` keeps an interpreter
    # that exits non-zero from killing the script silently under `set -e`:
    # without it the user gets an exit status and no message at all. The
    # sentinel is appended only on exit 0 and, more to the point, keeps the
    # trailing newlines that command substitution would otherwise strip --
    # "3 13 6 final 3.13.6\n\n" is two lines, not a clean answer.
    PY_PARTS="$("$_cmd" -c 'import sys; print("%d %d %d %s %s" % (sys.version_info[0], sys.version_info[1], sys.version_info[2], sys.version_info[3], sys.version.split()[0]))' 2>/dev/null && printf '%s' '<<install.sh>>')" \
        || PY_PARTS=''
    case "$PY_PARTS" in
        *'<<install.sh>>') PY_PARTS="${PY_PARTS%'<<install.sh>>'}" ;;
        *) PY_PARTS='' ;;
    esac

    # `read` consumes ONE line, so extra lines would be silently dropped
    # and a wrapper that prints a plausible first line could get an
    # unsupported interpreter installed. Exactly one newline-terminated
    # line is the only shape accepted: two newlines anywhere is "more than
    # one line", and no newline at all is not the answer `print` gives, so
    # it falls through to the could-not-read refusal. `wc` is deliberately
    # not used, since install.sh must work on a minimal PATH.
    case "$PY_PARTS" in
        *"$NL"*"$NL"*)
            echo "install.sh: $_label: printed more than one line when" \
                "asked for its version. jimemo requires Python >= 3.13.6." >&2
            return 1 ;;
        *"$NL") PY_PARTS="${PY_PARTS%"$NL"}" ;;
        *) PY_PARTS='' ;;
    esac

    # `read` rather than `set --`: it neither glob-expands the fields (an
    # unquoted `set -- $PY_PARTS` would expand a `*` in the version string
    # against the cwd) nor clobbers the script's positional parameters.
    # The sixth variable catches EXTRA words, so the field count is exact.
    PY_MAJOR=''; PY_MINOR=''; PY_MICRO=''; PY_LEVEL=''; PY_VER=''; PY_EXTRA=''
    IFS=' ' read -r PY_MAJOR PY_MINOR PY_MICRO PY_LEVEL PY_VER PY_EXTRA <<EOF
$PY_PARTS
EOF

    # A floor check must fail CLOSED on anything it cannot read. Without
    # this, an interpreter that exits 0 but reports something unusable
    # leaves PY_MICRO non-numeric or unrepresentable; every `[ … -lt … ]`
    # then fails with status 2, the whole `if` below evaluates false
    # (`set -e` does not apply inside an `if` condition), and the install
    # PROCEEDS. Each component must therefore be 1-4 plain digits -- digits
    # alone is not enough, because bash 3.2's `test` rejects an integer it
    # cannot represent the same way it rejects a word, so
    # `3 13 99999999999999999999` would install.
    for _py_part in "$PY_MAJOR" "$PY_MINOR" "$PY_MICRO"; do
        case "$_py_part" in
            ''|*[!0-9]*|?????*)
                echo "install.sh: $_label: could not read $_label's version" \
                    "(got '$PY_PARTS'). jimemo requires Python >= 3.13.6." >&2
                return 1 ;;
        esac
    done
    # All four fields and no more: a partial or overlong answer is not the
    # answer we asked for, and guessing the rest is how a floor check ends
    # up trusting a version nobody reported.
    if [ -z "$PY_LEVEL" ] || [ -z "$PY_VER" ] || [ -n "$PY_EXTRA" ]; then
        echo "install.sh: $_label: could not read $_label's version" \
            "(got '$PY_PARTS'). jimemo requires Python >= 3.13.6." >&2
        return 1
    fi

    # A PRE-RELEASE is refused whatever its numbers say. CPython 3.14.0b1
    # compares (3, 14, 0) >= the floor while its html.parser still fails
    # all three checks jimemo depends on; gh-69426 landed in 3.14.0b2. A
    # version number cannot say which pre-release carries which backport.
    if [ "$PY_LEVEL" != "final" ]; then
        echo "install.sh: $_label: is $PY_VER, a pre-release" \
            "(releaselevel $PY_LEVEL); jimemo requires a FINAL release of" \
            "Python >= 3.13.6, because a pre-release's version number does" \
            "not say which parser fixes it carries." >&2
        return 1
    fi

    if [ "$PY_MAJOR" -lt 3 ] \
        || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 13 ]; } \
        || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -eq 13 ] && [ "$PY_MICRO" -lt 6 ]; }; then
        echo "install.sh: $_label: is $PY_VER; jimemo requires Python >= 3.13.6." >&2
        return 1
    fi
    return 0
}

# executable_of CMD LABEL -- prints CMD's own idea of where it lives
# (sys.executable), which is what gets bound. Not the PATH lookup result:
# a version-manager shim at that path picks an interpreter per working
# directory on every run, which is exactly the silent substitution the
# entry point exists to rule out. The answer must be a non-empty ABSOLUTE
# path on exactly one line with no control characters -- Python documents
# that sys.executable may be empty or None, and a relative answer would be
# resolved through PATH later, the search the launcher refuses to do.
executable_of() {
    _cmd="$1"
    _label="$2"

    # Command substitution strips trailing newlines, so "path\n\n" would
    # read as one clean line. The sentinel keeps them: it is only appended
    # when the interpreter exited 0, so its absence is the failure signal.
    _out="$("$_cmd" -c 'import sys; print(sys.executable or "")' 2>/dev/null && printf '%s' '<<install.sh>>')" \
        || _out=''
    case "$_out" in
        *'<<install.sh>>') _out="${_out%'<<install.sh>>'}" ;;
        *)
            echo "install.sh: $_label: exited non-zero when asked for sys.executable" >&2
            return 1 ;;
    esac
    case "$_out" in
        *"$NL"*"$NL"*)
            echo "install.sh: $_label: printed more than one line when asked" \
                "for sys.executable" >&2
            return 1 ;;
        *"$NL") _out="${_out%"$NL"}" ;;
        *)
            echo "install.sh: $_label: printed no line when asked for sys.executable" >&2
            return 1 ;;
    esac
    case "$_out" in
        '')
            echo "install.sh: $_label: reported no sys.executable, so there is" \
                "no path to bind" >&2
            return 1 ;;
        *[[:cntrl:]]*)
            echo "install.sh: $_label: reported a sys.executable containing a" \
                "control character; refusing to bind it" >&2
            return 1 ;;
        /*) ;;
        *)
            echo "install.sh: $_label: reported a relative sys.executable" \
                "('$_out'); only an absolute path can be bound" >&2
            return 1 ;;
    esac
    printf '%s\n' "$_out"
}

# try_python PATH LABEL -- probe, locate, and probe the located path again
# so the path written into the entry point is the path that was verified.
# On success sets BOUND_PYTHON, BOUND_VERSION and BOUND_LABEL.
try_python() {
    _path="$1"
    _try_label="$2"
    probe_python "$_path" "$_try_label" || return 1
    _exe="$(executable_of "$_path" "$_try_label")" || return 1
    # Probe the located path again even when it spells the same as the
    # candidate: the path written into the entry point must be the path
    # that answered the version question LAST. An interpreter re-pointed
    # between the two questions (a venv rebased mid-install) would
    # otherwise be bound on the strength of a version it no longer has.
    probe_python "$_exe" "$_try_label -> $_exe" || return 1
    BOUND_PYTHON="$_exe"
    BOUND_VERSION="$PY_VER"
    BOUND_LABEL="$_try_label"
    return 0
}

# choose_python -- the override if one was given, else the first
# candidate that passes; exits 1 with every rejection already printed
# when nothing qualifies.
choose_python() {
    BOUND_PYTHON=''
    BOUND_VERSION=''
    BOUND_LABEL=''

    if [ -n "$PYTHON_OVERRIDE" ]; then
        _cmd="$PYTHON_OVERRIDE"
        case "$_cmd" in
            */*)
                # A path: say "not an executable file" rather than
                # "could not read its version (got '')" for a typo or a
                # directory.
                if [ ! -f "$_cmd" ] || [ ! -x "$_cmd" ]; then
                    echo "install.sh: error: $PYTHON_OVERRIDE_FROM names" \
                        "'$_cmd', which is not an executable file. jimemo" \
                        "requires Python >= 3.13.6." >&2
                    exit 1
                fi ;;
            *)
                _resolved="$(command -v "$_cmd" 2>/dev/null)" || _resolved=''
                if [ -z "$_resolved" ]; then
                    echo "install.sh: error: $PYTHON_OVERRIDE_FROM names" \
                        "'$_cmd', which is not on PATH. jimemo requires" \
                        "Python >= 3.13.6." >&2
                    exit 1
                fi
                _cmd="$_resolved" ;;
        esac
        if ! try_python "$_cmd" "$PYTHON_OVERRIDE_FROM $PYTHON_OVERRIDE"; then
            echo "install.sh: error: $PYTHON_OVERRIDE_FROM names" \
                "'$PYTHON_OVERRIDE', which is not a supported interpreter" \
                "(see above). When an interpreter is named, no other is" \
                "tried. jimemo requires Python >= 3.13.6, a final release." >&2
            exit 1
        fi
        return 0
    fi

    for _name in $PYTHON_CANDIDATES; do
        _resolved="$(command -v "$_name" 2>/dev/null)" || _resolved=''
        if [ -z "$_resolved" ]; then
            echo "install.sh: $_name: not found on PATH" >&2
            continue
        fi
        if try_python "$_resolved" "$_name"; then
            return 0
        fi
    done
    echo "install.sh: error: no supported Python found. Tried, in order:" \
        "$PYTHON_CANDIDATES (each rejection is listed above). jimemo" \
        "requires Python >= 3.13.6, a final release. Install it (macOS:" \
        "brew install python@3.13; Debian/Ubuntu: apt install python3.13)" \
        "and re-run ./install.sh, or name one: ./install.sh --python" \
        "/path/to/python3" >&2
    exit 1
}

# ---------------------------------------------------------------------
# The entry point.
# ---------------------------------------------------------------------

# sq STRING -- STRING single-quoted for sh, `'` spelled `'\''`. A
# character loop rather than a substitution pattern: bash 3.2's
# `${var//x/y}` and a replacement containing quotes and backslashes do not
# mix readably, and this is the one place a quoting bug would put a wrong
# path into every later `jimemo` run.
sq() {
    _s="$1"
    _o=''
    _i=0
    _n=${#_s}
    while [ "$_i" -lt "$_n" ]; do
        _c="${_s:$_i:1}"
        if [ "$_c" = "'" ]; then
            _o="$_o'\\''"
        else
            _o="$_o$_c"
        fi
        _i=$((_i + 1))
    done
    printf "'%s'" "$_o"
}

# read_entry_point_header FILE -- sets ENTRY_HEADER to FILE's first 4096
# characters; 1 when FILE holds a NUL byte in that span, i.e. a binary and
# not a script this installer wrote. Plain bash, no grep/head/python:
# `--uninstall` must work on a minimal PATH and on a machine whose python3
# is below the floor. `read -d ''` stops at a NUL and `-n` bounds the read,
# so a huge unterminated line costs 4 KiB, and bash's habit of silently
# DROPPING NUL bytes on an ordinary `read` cannot turn a binary that
# happens to contain the marker text into our file. Measured on bash 3.2:
# status 0 with fewer than 4096 characters means a NUL stopped the read;
# status 1 means EOF came first with no NUL; status 0 at exactly 4096 is a
# full, clean header.
read_entry_point_header() {
    ENTRY_HEADER=''
    if IFS= read -r -d '' -n 4096 ENTRY_HEADER < "$1"; then
        if [ "${#ENTRY_HEADER}" -ne 4096 ]; then
            return 1
        fi
    fi
    return 0
}

# parse_entry_point_header FILE -- sets ENTRY_MARKER=1 when the marker is
# among FILE's first ten lines and ENTRY_LAUNCHER to the launcher recorded
# after it (empty when none). 1 when FILE cannot be ours at all (binary).
parse_entry_point_header() {
    ENTRY_MARKER=0
    ENTRY_LAUNCHER=''
    read_entry_point_header "$1" || return 1
    _count=0
    while IFS= read -r _line || [ -n "$_line" ]; do
        case "$_line" in
            "$ENTRY_POINT_MARKER"*) ENTRY_MARKER=1 ;;
            "# launcher: "*)
                if [ "$ENTRY_MARKER" = 1 ] && [ -z "$ENTRY_LAUNCHER" ]; then
                    ENTRY_LAUNCHER="${_line#"# launcher: "}"
                fi ;;
        esac
        _count=$((_count + 1))
        if [ "$_count" -ge 10 ]; then
            break
        fi
    done <<EOF
$ENTRY_HEADER
EOF
    return 0
}

# owned_entry_point FILE -- 0 when FILE is a regular file carrying the
# marker in its header.
owned_entry_point() {
    [ -f "$1" ] || return 1
    parse_entry_point_header "$1" || return 1
    [ "$ENTRY_MARKER" = 1 ]
}

# write_entry_point TARGET
#
# Ours to replace: a symlink (today's install, whatever it points at --
# `ln -sfn` replaced any symlink too) or a regular file carrying the
# marker. Anything else is left alone with a warning; the skills still get
# linked.
write_entry_point() {
    target="$1"
    parent="$(dirname "$target")"

    if [ -e "$target" ] || [ -L "$target" ]; then
        if [ ! -L "$target" ] && ! owned_entry_point "$target"; then
            echo "warn     $target already exists and is not a symlink or a jimemo entry point -- leaving it alone (jimemo CLI)" >&2
            return 0
        fi
    fi

    # The header records both paths one per line and the wrapper prints
    # them in its diagnostics, so a control character in either (a
    # newline above all) would corrupt the record. Refuse, fail closed.
    case "$BOUND_PYTHON$CLI_SOURCE" in
        *[[:cntrl:]]*)
            echo "install.sh: error: refusing to write an entry point: the" \
                "interpreter path or the checkout path contains a control" \
                "character (python: '$BOUND_PYTHON', checkout:" \
                "'$CLI_SOURCE')." >&2
            exit 1 ;;
    esac

    if [ "$DRY_RUN" = "1" ]; then
        if [ ! -d "$parent" ]; then
            echo "[dry-run] would create directory: $parent"
        fi
        echo "[dry-run] would write entry point: $target (python: $BOUND_PYTHON, launcher: $CLI_SOURCE)"
        return 0
    fi

    mkdir -p "$parent"
    # A fixed temp name, so a test can plant a collision. Whatever sits
    # there -- a leftover from an interrupted run, or a symlink that a
    # plain `>` would follow and truncate -- is removed as a path first,
    # never written through.
    tmp="$target.tmp"
    rm -f "$tmp"
    if [ -e "$tmp" ] || [ -L "$tmp" ]; then
        echo "install.sh: error: could not clear $tmp before writing the entry point" >&2
        exit 1
    fi
    # The variable lines go through printf '%s' so nothing in a path is
    # expanded; the fixed body is a QUOTED heredoc, which expands nothing
    # at all. The wrapper's own messages use printf, never echo, because
    # POSIX leaves echo implementation-defined on a backslash operand.
    {
        printf '%s\n' '#!/bin/sh'
        printf '%s\n' '# jimemo entry point, written by install.sh. Re-run install.sh to change it.'
        printf '%s\n' "$ENTRY_POINT_MARKER 1"
        printf '%s\n' "# python: $BOUND_PYTHON"
        printf '%s\n' "# launcher: $CLI_SOURCE"
        printf '%s\n' "JIMEMO_PYTHON=$(sq "$BOUND_PYTHON")"
        printf '%s\n' "JIMEMO_LAUNCHER=$(sq "$CLI_SOURCE")"
        cat <<'EOF'
if [ ! -x "$JIMEMO_PYTHON" ]; then
    printf '%s\n' "jimemo: the Python this entry point is bound to is gone ($JIMEMO_PYTHON) -- re-run install.sh in the jimemo checkout to bind another" >&2
    exit 1
fi
if [ ! -f "$JIMEMO_LAUNCHER" ]; then
    printf '%s\n' "jimemo: the jimemo checkout this entry point was installed from is gone ($JIMEMO_LAUNCHER) -- re-run install.sh from a jimemo checkout" >&2
    exit 1
fi
JIMEMO_ENTRY_POINT="$0"
export JIMEMO_ENTRY_POINT
exec "$JIMEMO_PYTHON" "$JIMEMO_LAUNCHER" "$@"
EOF
    } > "$tmp"
    chmod 755 "$tmp" || { rm -f "$tmp"; exit 1; }
    # A symlink is removed first: `mv` onto a symlink that points at a
    # directory would move the new file INTO that directory and leave the
    # symlink installed, and `rm -f` takes a symlink as a symlink. A
    # regular file (an earlier entry point) is renamed over in place, so a
    # re-install never leaves a moment with no `jimemo` at all.
    if [ -L "$target" ]; then
        rm -f "$target"
    fi
    mv -f "$tmp" "$target"
    echo "wrote    $target (entry point -> $BOUND_PYTHON, $BOUND_VERSION)"
}

# remove_entry_point TARGET
#
# Removes exactly what this checkout's installer wrote: a symlink to this
# checkout's launcher (the pre-p0nk form) or a marker file whose recorded
# launcher is this checkout's. Anything else is reported and left.
remove_entry_point() {
    target="$1"
    label="jimemo CLI"

    if [ -L "$target" ]; then
        unlink_one "$target" "$CLI_SOURCE" "$label"
        return 0
    fi
    if [ -f "$target" ]; then
        if owned_entry_point "$target"; then
            recorded="$ENTRY_LAUNCHER"
            if [ "$recorded" = "$CLI_SOURCE" ]; then
                if [ "$DRY_RUN" = "1" ]; then
                    echo "[dry-run] would remove: $target ($label)"
                else
                    rm "$target"
                    echo "removed  $target ($label)"
                fi
            else
                echo "skip     $target is a jimemo entry point written from ${recorded:-an unrecorded checkout}, not $CLI_SOURCE -- leaving it alone ($label)" >&2
            fi
        else
            echo "skip     $target exists and is not a symlink or a jimemo entry point -- leaving it alone ($label)" >&2
        fi
    elif [ -e "$target" ]; then
        echo "skip     $target exists and is not a symlink -- leaving it alone ($label)" >&2
    fi
}

install_all() {
    write_entry_point "$CLI_TARGET"
    link_one "$CLAUDE_TARGET" "$SKILL_SOURCE" "Claude Code skill (also read by pi)"
    link_one "$CODEX_TARGET" "$SKILL_SOURCE" "Codex skill"

    if [ "$AMPLIFIER_DETECTED" = "1" ]; then
        link_one "$AMPLIFIER_TARGET" "$SKILL_SOURCE" "Amplifier skill"
    else
        note "Amplifier not detected (no \$AMPLIFIER_SKILLS_DIR, no ~/.amplifier)." \
            "To register manually once it's installed: ln -s $SKILL_SOURCE ~/.amplifier/skills/jimemo" \
            "-- or just re-run this installer."
    fi

    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) note "$HOME/.local/bin is not on your PATH. Add to your shell rc:" \
               "export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
    esac

    if [ "$DRY_RUN" != "1" ]; then
        echo
        note "pi reads ~/.claude/skills, so it picks up the skill above -- no separate step." \
            "Claude Desktop (Cowork) does NOT load filesystem skills, but the jimemo CLI" \
            "works in its local-agent mode: tell it to run 'jimemo', or point it at AGENTS.md."
        echo
        echo "Done. Next:"
        echo "  jimemo doctor   # sanity-check the install (it names the bound interpreter)"
        echo "  jimemo --help   # full command reference"
    fi
}

uninstall_all() {
    remove_entry_point "$CLI_TARGET"
    unlink_one "$CLAUDE_TARGET" "$SKILL_SOURCE" "Claude Code skill (also read by pi)"
    unlink_one "$CODEX_TARGET" "$SKILL_SOURCE" "Codex skill"
    unlink_one "$AMPLIFIER_TARGET" "$SKILL_SOURCE" "Amplifier skill"

    if [ "$DRY_RUN" != "1" ]; then
        echo
        echo "Done. jimemo's entry point and skill symlinks have been removed; the"
        echo "clone at $REPO_ROOT is untouched."
    fi
}

# Uninstall is dispatched BEFORE the Python check on purpose. It removes
# only what this script wrote -- no python3 is involved -- and gating it
# on the floor would trap the exact user the floor affects: a stock Mac
# (python3 = 3.9.6) that pulls this change could no longer run
# `./install.sh --uninstall` to get its files back out. Raising a floor
# must never take away the way out (jimemo#gaga).
if [ "$UNINSTALL" = "1" ]; then
    uninstall_all
    exit 0
fi

# Pick and verify the interpreter before writing anything, in dry-run
# mode too: a machine with no supported Python gets the refusal and
# nothing else.
choose_python
echo "python   $BOUND_LABEL -> $BOUND_PYTHON ($BOUND_VERSION)"

install_all
