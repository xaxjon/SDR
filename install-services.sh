#!/usr/bin/env bash
# Install SDRADIO as boot-time services. Run: sudo ./install-services.sh
set -e
cd "$(dirname "$0")"
cp systemd/sdrd.service systemd/sdradio-web.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now sdrd.service sdradio-web.service
echo
echo "SDRADIO now starts at boot:"
systemctl --no-pager --plain status sdrd.service sdradio-web.service | grep -E '●|Active'
echo
echo "Browse to http://$(hostname -I | awk '{print $1}'):8090/"
