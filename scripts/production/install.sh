#!/usr/bin/env bash
set -euo pipefail

root=/opt/ukeire-dojo
src=$(cd -- "$(dirname -- "$0")" && pwd)
dropin=/etc/systemd/system/ukeire-dojo.service.d
[[ -d "$root/.git" && -s "$root/web/dist/index.html" && -f "$root/data/bank.sqlite" ]]
if [[ ! -L "$root/current" ]]; then
    ln -s "$root" "$root/current"
fi
install -d -o ubuntu -g ubuntu "$root/releases"
install -m 0755 "$src/deploy.sh" /usr/local/sbin/ukeire-dojo-deploy
install -d "$dropin"
install -m 0644 "$src/ukeire-dojo-release.conf" "$dropin/10-release.conf"
install -m 0644 "$src"/ukeire-dojo-deploy.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ukeire-dojo-deploy.timer
