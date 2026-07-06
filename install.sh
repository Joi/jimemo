#!/bin/bash
# jimemo installer: one clone, symlinked into every harness on this
# machine. `git pull` in the clone updates every harness that points at
# it — there is exactly one copy of the code and one copy of the skill,
# never a fork-per-harness (see the fresheyes lesson: three unsynced
# copies of the same skill in Claude/Codex/Amplifier drifted out of sync
# and had to be patched three separate times; symlinks avoid that class
# of bug entirely).
#
# Portable to the bash 3.2 shipped on macOS: no associative arrays, no
# `mapfile`, no `${var,,}`, and no `set -u` (bash 3.2 treats a reference
# to an empty array under `set -u` as an unbound-variable error).

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

DRY_RUN=0
UNINSTALL=0

print_usage() {
    cat <<'EOF'
Usage: install.sh [--dry-run] [--uninstall] [--help]

Symlinks the jimemo CLI onto PATH and registers the jimemo skill with
every agent harness found on this machine (Claude Code/Cowork, Codex,
and Amplifier if present). One clone; `git pull` updates every harness.

  --dry-run     print every action without doing it
  --uninstall   remove exactly the symlinks this script creates
  -h, --help    show this help
EOF
}

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help)
            print_usage
            exit 0
            ;;
        *)
            echo "install.sh: unknown option: $arg" >&2
            print_usage >&2
            exit 1
            ;;
    esac
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

install_all() {
    link_one "$CLI_TARGET" "$CLI_SOURCE" "jimemo CLI"
    link_one "$CLAUDE_TARGET" "$SKILL_SOURCE" "Claude Code / Cowork skill"
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
        echo "Done. Next:"
        echo "  jimemo doctor   # sanity-check the install"
        echo "  jimemo --help   # full command reference"
    fi
}

uninstall_all() {
    unlink_one "$CLI_TARGET" "$CLI_SOURCE" "jimemo CLI"
    unlink_one "$CLAUDE_TARGET" "$SKILL_SOURCE" "Claude Code / Cowork skill"
    unlink_one "$CODEX_TARGET" "$SKILL_SOURCE" "Codex skill"
    unlink_one "$AMPLIFIER_TARGET" "$SKILL_SOURCE" "Amplifier skill"

    if [ "$DRY_RUN" != "1" ]; then
        echo
        echo "Done. jimemo's symlinks have been removed; the clone at"
        echo "$REPO_ROOT is untouched."
    fi
}

if ! command -v python3 >/dev/null 2>&1; then
    echo "install.sh: error: python3 not found on PATH. jimemo requires Python >= 3.9." >&2
    exit 1
fi

PY_MAJOR="$(python3 -c 'import sys; print(sys.version_info[0])')"
PY_MINOR="$(python3 -c 'import sys; print(sys.version_info[1])')"
PY_VER="$(python3 -c 'import platform; print(platform.python_version())')"

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]; }; then
    echo "install.sh: error: python3 is $PY_VER; jimemo requires Python >= 3.9." >&2
    exit 1
fi

if [ "$UNINSTALL" = "1" ]; then
    uninstall_all
else
    install_all
fi
