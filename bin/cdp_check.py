#!/usr/bin/env python3
"""CDP end-to-end check for SDRADIO pages (real time, no virtual budget)."""
import asyncio, base64, json, subprocess, sys, time, urllib.request, websockets

PORT = 9224
URL = sys.argv[1]
SHOT = sys.argv[2]
WAIT = float(sys.argv[3]) if len(sys.argv) > 3 else 6

proc = subprocess.Popen([
    "google-chrome", "--headless", "--disable-gpu", "--no-sandbox",
    "--disable-dev-shm-usage", f"--remote-debugging-port={PORT}",
    "--window-size=1280,900", "about:blank"
], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2)

try:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list") as r:
        pages = json.loads(r.read())
    page = next(p for p in pages if p['type'] == 'page')
    ws_url = page['webSocketDebuggerUrl']

    async def run():
        async with websockets.connect(ws_url, max_size=50_000_000) as ws:
            mid = 0
            async def send(method, params=None):
                nonlocal mid
                mid += 1
                await ws.send(json.dumps({'id': mid, 'method': method, 'params': params or {}}))
                while True:
                    resp = json.loads(await ws.recv())
                    if resp.get('id') == mid:
                        return resp
            await send('Page.enable')
            await send('Page.navigate', {'url': URL})
            await asyncio.sleep(WAIT)
            expr = """
            (() => {
              const lcd = document.querySelector('.lcd-digits, #lcd-freq, .freq-digits');
              const c = document.getElementById('wf') || document.querySelector('canvas');
              let wfNonBlank = null;
              if (c) {
                const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
                let sum = 0;
                for (let i = 0; i < d.length; i += 397) sum += d[i] + d[i+1] + d[i+2];
                wfNonBlank = sum > 0;
              }
              return JSON.stringify({
                lcd: lcd ? lcd.textContent.trim() : null,
                sqlLed: !!document.querySelector('.led.lit, .led.small.lit'),
                online: document.body.textContent.includes('ONLINE'),
                wfNonBlank,
                bodyLen: document.body.textContent.length
              });
            })()"""
            r = await send('Runtime.evaluate', {'expression': expr, 'returnByValue': True})
            print('JS state:', r.get('result', {}).get('result', {}).get('value'))
            shot = await send('Page.captureScreenshot', {'format': 'png'})
            with open(SHOT, 'wb') as f:
                f.write(base64.b64decode(shot['result']['data']))
            print('screenshot ->', SHOT)
    asyncio.run(run())
finally:
    proc.terminate()
