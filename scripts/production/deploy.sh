#!/usr/bin/env bash
set -euo pipefail

root=/opt/ukeire-dojo
service=ukeire-dojo.service
health=http://127.0.0.1:8765
artifact_ref=refs/remotes/origin/production-build
cd "$root"

exec 9>"$root/.deploy.lock"
flock -n 9 || exit 0
[[ -L current ]]
previous=$(readlink -f current)

remote_artifact=$(git ls-remote origin refs/heads/production-build)
[[ -n "$remote_artifact" ]] || exit 0
git fetch --no-tags origin \
    +refs/heads/main:refs/remotes/origin/main \
    +refs/heads/production-build:"$artifact_ref"
artifact=$(git rev-parse "$artifact_ref")
revision=$(git rev-parse "$artifact^")
[[ $(git show "$artifact:web/dist/build-revision.txt") == "$revision" ]]
git merge-base --is-ancestor "$revision" refs/remotes/origin/main

if [[ "$previous" == "$root" ]]; then
    running_revision=$(git rev-parse HEAD)
else
    running_revision=$(basename "$previous")
fi
[[ "$revision" != "$running_revision" ]] || exit 0
git merge-base --is-ancestor "$running_revision" "$revision"
[[ ! -f "$root/releases/$revision.failed" ]] || exit 0

stage=$(mktemp -d "$root/releases/.stage.XXXXXX")
release="$root/releases/$revision"
activated=false

switch_to() {
    ln -s "$1" "$stage/current"
    mv -Tf "$stage/current" "$root/current"
}

wait_for_health() {
    local expected=$1
    for (( attempt=0; attempt<20; attempt++ )); do
        if curl --fail --silent --max-time 2 "$health/api/stats" >/dev/null &&
            { [[ -z "$expected" ]] || [[ $(curl --fail --silent --max-time 2 \
                "$health/build-revision.txt") == "$expected" ]]; }; then
            return 0
        fi
        sleep 1
    done
    return 1
}

cleanup() {
    local status=$?
    if [[ $activated == true ]]; then
        touch "$release.failed" || true
        echo "Release $revision failed. Restoring $previous." >&2
        local previous_marker=''
        if [[ "$previous" != "$root" ]]; then previous_marker=$running_revision; fi
        if ! switch_to "$previous" ||
            ! sudo -n systemctl restart "$service" ||
            ! wait_for_health "$previous_marker"; then
            echo "Rollback failed for $service." >&2
        fi
    fi
    rm -rf -- "$stage"
    return "$status"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

if [[ ! -d "$release" ]]; then
    mkdir "$stage/release"
    git archive "$revision" ukeire_dojo tools/live-match/src | tar -x -C "$stage/release"
    git archive "$artifact" web/dist | tar -x -C "$stage/release"
    ln -s "$root/vendor" "$stage/release/vendor"
    test -s "$stage/release/web/dist/index.html"
    # 릴리스별로 컴파일 결과를 보관해 되돌릴 때 예전 코드와 클래스가 그대로 짝을 이룬다.
    (cd "$stage/release"; python3 -c 'from ukeire_dojo.rooms import ensure_live_match_compiled; ensure_live_match_compiled()')
    mv -T "$stage/release" "$release"
fi

switch_to "$release"
activated=true
sudo -n systemctl restart "$service"
wait_for_health "$revision"
activated=false
echo "Deployed $revision."
