#!/usr/bin/env bash
# Give a session its own checkout, without re-downloading ~9.5 GB.
#
# Why
# ---
# Two agent/editor sessions in one working tree can silently overwrite each
# other's uncommitted edits — git only protects what is committed. A git
# worktree fixes that outright: separate files, one shared .git, so branches and
# commits stay visible to everyone.
#
# The catch is that a bare `git worktree add` produces a checkout that cannot
# run: the heavy pieces are gitignored and therefore absent.
#
#     .venv       ~7.6 G      models      ~465 M
#     tts_kokoro  ~0.35 G     artifacts   ~299 M   (the SQLite stores)
#
# So this symlinks the read-mostly ones (.venv, models, tts_kokoro) back to the
# primary checkout — they are gitignored, so the links can never be committed —
# and gives the worktree its OWN artifacts/, because two ELIs writing one set of
# SQLite stores is a different race that symlinking would create.
#
#   scripts/new_worktree.sh review-pr        # new branch off the current HEAD
#   scripts/new_worktree.sh hotfix main      # new branch off main
#   scripts/new_worktree.sh --list
#   scripts/new_worktree.sh --remove review-pr
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARENT="$(dirname "$ROOT")"
BASENAME="$(basename "$ROOT")"

# Gitignored, read-mostly, shared by symlink.
SHARED=(.venv models tts_kokoro)
# Gitignored, written at runtime — each worktree gets its own.
PRIVATE=(artifacts)

die() { echo "[worktree] ERROR: $*" >&2; exit 1; }

case "${1:-}" in
    --list|-l)
        git -C "$ROOT" worktree list
        exit 0
        ;;
    --remove|-r)
        name="${2:?usage: new_worktree.sh --remove <name>}"
        target="$PARENT/$BASENAME-$name"
        [ -d "$target" ] || die "no worktree at $target"
        force=""
        [ "${3:-}" = "--force" ] || [ "${3:-}" = "-f" ] && force="--force"
        # Ask git FIRST whether it will let this go. Unlinking up front and then
        # having git refuse leaves a stranded worktree with no .venv — worse than
        # the state we started in.
        if [ -z "$force" ] && [ -n "$(git -C "$target" status --porcelain 2>/dev/null)" ]; then
            die "$target has uncommitted changes; commit them, or re-run with --force"
        fi
        # Drop the symlinks before deletion so nothing can walk into the shared
        # .venv / models / tts_kokoro and delete the originals.
        for d in ${SHARED[@]+"${SHARED[@]}"}; do [ -L "$target/$d" ] && rm -f "$target/$d"; done
        git -C "$ROOT" worktree remove ${force:+$force} "$target" \
            || die "git refused to remove $target"
        echo "[worktree] removed $target"
        exit 0
        ;;
    "" | -h | --help)
        sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
esac

NAME="$1"
BASE="${2:-HEAD}"
[[ "$NAME" =~ ^[A-Za-z0-9._-]+$ ]] || die "name must be [A-Za-z0-9._-]: $NAME"

TARGET="$PARENT/$BASENAME-$NAME"
BRANCH="wt/$NAME"

[ -e "$TARGET" ] && die "$TARGET already exists"
git -C "$ROOT" show-ref --verify --quiet "refs/heads/$BRANCH" \
    && die "branch $BRANCH already exists — pick another name"

echo "[worktree] creating $TARGET on branch $BRANCH (from $BASE)"
git -C "$ROOT" worktree add -b "$BRANCH" "$TARGET" "$BASE" >/dev/null

for d in ${SHARED[@]+"${SHARED[@]}"}; do
    if [ -e "$ROOT/$d" ]; then
        ln -s "$ROOT/$d" "$TARGET/$d"
        echo "[worktree]   linked  $d -> $ROOT/$d"
    else
        echo "[worktree]   skipped $d (absent in the primary checkout)"
    fi
done

for d in ${PRIVATE[@]+"${PRIVATE[@]}"}; do
    mkdir -p "$TARGET/$d"
    echo "[worktree]   private $d (own copy — databases must not be shared)"
done

# Own databases, so this worktree can run without racing the primary.
if [ -x "$ROOT/.venv/bin/python" ]; then
    ( cd "$TARGET" && "$ROOT/.venv/bin/python" -m eli.core.init_data >/dev/null 2>&1 ) \
        && echo "[worktree]   databases initialised" \
        || echo "[worktree]   NOTE: database init skipped — run: .venv/bin/python -m eli.core.init_data"
fi

# The hooks live in .git/hooks of the MAIN checkout; worktrees share them, but
# install anyway in case this is a fresh clone that never ran the installer.
[ -x "$ROOT/scripts/install_git_hooks.sh" ] && bash "$ROOT/scripts/install_git_hooks.sh" >/dev/null 2>&1 || true

cat <<EOF

[worktree] ready

    cd $TARGET
    .venv/bin/python -m pytest tests/ -q

  Branch  : $BRANCH
  Shares  : ${SHARED[*]-}  (symlinked, gitignored — never committed)
  Own copy: ${PRIVATE[*]-}
  Remove  : scripts/new_worktree.sh --remove $NAME

  Point one session at this directory and the other at $ROOT; neither can
  now overwrite the other's uncommitted work.
EOF
