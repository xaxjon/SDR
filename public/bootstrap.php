<?php
/* Lazy-start the SDR daemon: if nothing listens on the WebSocket port,
 * spawn sdrd.py in the background. Lets a bare `php -S` (or the web
 * service) bring the whole stack up when the first page is loaded. */
function sdrd_ensure_running(): void
{
    $fp = @fsockopen('127.0.0.1', 8765, $errno, $errstr, 1);
    if ($fp) {
        fclose($fp);
        return;
    }
    $base = dirname(__DIR__);
    @exec(sprintf(
        'nohup /usr/bin/python3 %s/bin/sdrd.py --host 0.0.0.0 --port 8765 > /tmp/sdrd.log 2>&1 &',
        escapeshellarg($base)
    ));
    // give it a moment to open the device before the browser's WS connects
    for ($i = 0; $i < 30; $i++) {
        usleep(100000);
        $fp = @fsockopen('127.0.0.1', 8765, $errno, $errstr, 1);
        if ($fp) {
            fclose($fp);
            return;
        }
    }
}
sdrd_ensure_running();
