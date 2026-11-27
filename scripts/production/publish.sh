#!/usr/bin/env bash
set -euo pipefail

revision=$1
site=$2
ref=refs/heads/production-build
[[ $revision =~ ^[0-9a-f]{40}$ ]]
[[ $(cat web/dist/build-revision.txt) == "$revision" ]]
test -s web/dist/index.html

previous=$(git ls-remote origin "$ref" | cut -f1)
main=$(git ls-remote origin refs/heads/main | cut -f1)
if [[ "$main" != "$revision" ]]; then
    echo "A newer main revision is available. Skipping $revision."
    exit 0
fi

export GIT_INDEX_FILE
GIT_INDEX_FILE=$(mktemp -d)/index
trap 'rm -f "$GIT_INDEX_FILE"; rmdir "$(dirname "$GIT_INDEX_FILE")"' EXIT
git read-tree --empty
git add --force -- web/dist
tree=$(git write-tree)
export GIT_AUTHOR_NAME='github-actions[bot]' GIT_COMMITTER_NAME='github-actions[bot]'
export GIT_AUTHOR_EMAIL='41898282+github-actions[bot]@users.noreply.github.com'
export GIT_COMMITTER_EMAIL=$GIT_AUTHOR_EMAIL
artifact=$(git commit-tree "$tree" -p "$revision" -m "Build $revision")
git push --force-with-lease="$ref:$previous" origin "$artifact:$ref"

# 서버의 systemd 타이머와 실제 공개 HTTPS 응답까지 확인한다.
for (( attempt=0; attempt<24; attempt++ )); do
    if [[ $(curl --fail --silent --max-time 5 -H 'Cache-Control: no-cache' \
        "$site/build-revision.txt?revision=$revision") == "$revision" ]]; then
        echo "Production is serving $revision."
        exit 0
    fi
    sleep 10
done
echo "Production did not serve $revision in time." >&2
exit 1
