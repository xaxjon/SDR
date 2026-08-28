#!/usr/bin/env bash
# SDRADIO startup: SDR daemon (WebSocket :8765) + PHP web UI (:8090).
cd "$(dirname "$0")"

if pgrep -f 'bin/sdrd[.]py' > /dev/null; then
  echo "sdrd already running"
else
  # add --icom-port /dev/ttyUSB0 --icom-model pcr1000 here to also drive an
  # ICOM PCR receiver (see README "External receivers")
  nohup python3 bin/sdrd.py --host 0.0.0.0 --port 8765 > /tmp/sdrd.log 2>&1 &
  echo "sdrd started (pid $!), log: /tmp/sdrd.log"
fi

if pgrep -f 'php -S 0.0.0.0:8090' > /dev/null; then
  echo "web server already running"
else
  nohup php -S 0.0.0.0:8090 -t public > /tmp/sdradio-web.log 2>&1 &
  echo "web UI started (pid $!), log: /tmp/sdradio-web.log"
fi

echo "Open http://$(hostname -I | awk '{print $1}'):8090/ in a browser"
