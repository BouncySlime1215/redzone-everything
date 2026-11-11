#!/usr/bin/env bash
# Find the commit that broke one test, by running it (docs/memory/runbooks/debugging.md).
#
#   scripts/bisect_test.sh --good <ref> [--bad <ref>] [--needs <path>] <pytest-node-id> [night]
#
# Why: W1-RULES rule 5 (PLAN G-04). Bisect with an automated check, not by reading code.
# Each commit exits 0 good, 1 bad, 125 skip (git bisect's contract). Skip means the commit says nothing about
# the bug: collection error, missing test, or a missing --needs path (data/ survives checkouts).
# A test that does not exist in old commits: copy it outside the repo and pass its absolute path as the node id.
# night is exported to the test as RZ_NIGHT. PYTHON picks the interpreter (default python3).
# Runs one pytest at a time, leaves the checkout where it started, and refuses tracked uncommitted changes.

step() {
    local node=$1 night=$2 needs=$3
    if [ -n "$needs" ] && [ ! -e "$needs" ]; then
        echo "skip: $needs is missing"
        exit 125
    fi
    RZ_NIGHT=$night PYTHONDONTWRITEBYTECODE=1 "${PYTHON:-python3}" -m pytest -q -x --no-header -p no:cacheprovider "$node"
    case $? in
        0) exit 0 ;;
        1) exit 1 ;;
        *) exit 125 ;;   # 2 collection error or interrupted, 3 internal, 4 usage or no such file, 5 no tests
    esac
}

usage() {
    sed -n 2,4p "$0" >&2
    exit 2
}

if [ "${1:-}" = "--step" ]; then
    step "$2" "$3" "$4"
fi

good="" bad="HEAD" needs="" node="" night=""
while [ $# -gt 0 ]; do
    case $1 in
        --good) good=${2:-}; shift 2 || usage ;;
        --bad) bad=${2:-}; shift 2 || usage ;;
        --needs) needs=${2:-}; shift 2 || usage ;;
        -h|--help) usage ;;
        *)
            if [ -z "$node" ]; then node=$1; elif [ -z "$night" ]; then night=$1; else usage; fi
            shift
            ;;
    esac
done
[ -n "$good" ] && [ -n "$node" ] || usage

script=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")
cd "$(git rev-parse --show-toplevel)" || exit 2
if ! git diff --quiet HEAD; then
    echo "refused: commit or stash your changes first (bisect checks out other commits)" >&2
    exit 2
fi

# The script may not exist in old commits, so each step runs a copy from outside the checkout.
# An explicit XXXXXX template: GNU mktemp (Linux CI) rejects `-t rz_bisect` ("too few X's"); BSD accepts both.
copy=$(mktemp "${TMPDIR:-/tmp}/rz_bisect.XXXXXX") || exit 2
cp "$script" "$copy"
trap 'git bisect reset >/dev/null 2>&1; rm -f "$copy"' EXIT

git bisect start "$bad" "$good" >/dev/null || exit 2
log=$(git bisect run bash "$copy" --step "$node" "$night" "$needs" 2>&1)
code=$?
# git before 2.4x prints "is the first bad commit"; newer git (2.56 on CI) quotes the term: "is the first 'bad' commit".
if [ $code -eq 0 ] && printf '%s\n' "$log" | grep -Eq "is the first '?bad'? commit"; then
    culprit=$(git rev-parse refs/bisect/bad)
    echo "Bisected-to: $(git log -1 --format='%h %s' "$culprit")"
    exit 0
fi
printf '%s\n' "$log" | tail -15
echo "bisect did not name one commit (too many skips, or the test is not good at --good and bad at --bad)" >&2
exit 1
