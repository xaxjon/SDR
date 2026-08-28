#!/usr/bin/env python3
"""Headless end-to-end test for sdrd.py: tune, audio, FFT, scan."""
import asyncio
import json
import sys
import wave

import websockets

WS = 'ws://127.0.0.1:8765'


async def main():
    audio = bytearray()
    fft_frames = 0
    statuses = []
    async with websockets.connect(WS, max_queue=256) as ws:
        await ws.send(json.dumps({'cmd': 'fft', 'enable': True}))
        await ws.send(json.dumps({'cmd': 'tune', 'freq': 108027000,
                                  'mode': 'wfm'}))
        await ws.send(json.dumps({'cmd': 'squelch', 'level': 0.0}))

        async def collect(seconds):
            nonlocal fft_frames
            end = asyncio.get_event_loop().time() + seconds
            while asyncio.get_event_loop().time() < end:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if isinstance(msg, bytes):
                    if msg[0] == 1:
                        audio.extend(msg[1:])
                    elif msg[0] == 2:
                        fft_frames += 1
                else:
                    statuses.append(json.loads(msg))

        await collect(4.0)
        print('audio bytes after 4s:', len(audio),
              '(%.1fs)' % (len(audio) / 2 / 48000))
        print('fft frames:', fft_frames)
        print('statuses (last 3):', statuses[-3:])

        # quick 3-channel scan across the FM station
        await ws.send(json.dumps({
            'cmd': 'scan',
            'channels': [
                {'f': 106480000, 'm': 'wfm', 'label': 'st1'},
                {'f': 108027000, 'm': 'wfm', 'label': 'st2'},
                {'f': 108213000, 'm': 'wfm', 'label': 'st3'}],
            'dwell_ms': 150, 'hold_ms': 800, 'threshold_db': 14}))
        hit_seen = None
        end = asyncio.get_event_loop().time() + 6.0
        while asyncio.get_event_loop().time() < end:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if not isinstance(msg, bytes):
                d = json.loads(msg)
                if d.get('type') == 'scan_hit':
                    hit_seen = d
                    break
        print('scan hit:', hit_seen)

    with wave.open('/tmp/ws_audio.wav', 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(48000)
        wf.writeframes(bytes(audio))
    print('wrote /tmp/ws_audio.wav')


asyncio.run(main())
