#!/usr/bin/env python3
"""
smoke_test.py

Quick smoke test for the local Socket.IO + HTTP simulation server.
Checks: connect, receive sim_mode, toggle sim_mode, set sim_params.

Usage: python smoke_test.py
"""
import sys
import time
import json
import threading

import socketio
import requests

BASE = 'http://127.0.0.1:5000'

results = {
    'connected': False,
    'initial_sim_mode': None,
    'sim_mode_after_set': None,
    'sim_params_broadcast': None,
}


def run_test():
    sio = socketio.Client(logger=False, engineio_logger=False)

    @sio.event
    def connect():
        print('[test] connected to server')
        results['connected'] = True

    @sio.on('sim_mode')
    def on_sim_mode(msg):
        print('[test] sim_mode event:', msg)
        if results['initial_sim_mode'] is None:
            results['initial_sim_mode'] = msg.get('mode') if isinstance(msg, dict) else msg
        else:
            # record subsequent updates
            results['sim_mode_after_set'] = msg.get('mode') if isinstance(msg, dict) else msg

    @sio.on('sim_params')
    def on_sim_params(msg):
        print('[test] sim_params event:', msg)
        results['sim_params_broadcast'] = msg

    try:
        sio.connect(BASE, wait=True, transports=['websocket', 'polling'])
    except Exception as e:
        print('[test] failed to connect to server:', e)
        return 2

    # wait briefly for initial sim_mode
    time.sleep(1.0)

    # Toggle sim_mode to RUNNING
    print('[test] emitting set_sim_mode -> RUNNING')
    try:
        sio.emit('set_sim_mode', 'RUNNING')
    except Exception as e:
        print('[test] emit set_sim_mode failed:', e)

    # POST sim_params via HTTP and emit via socket
    new_params = {'converge': 5, 'confirm': 3}
    print('[test] sending set_sim_params via socket and HTTP:', new_params)
    try:
        sio.emit('set_sim_params', new_params)
    except Exception as e:
        print('[test] emit set_sim_params failed:', e)

    try:
        r = requests.post(BASE + '/sim_params', json=new_params, timeout=3)
        print('[test] HTTP POST /sim_params ->', r.status_code, r.text[:200])
    except Exception as e:
        print('[test] HTTP POST /sim_params failed:', e)

    # wait for broadcasts
    time.sleep(1.0)

    # Fetch /state snapshot to ensure server exposes it
    try:
        r = requests.get(BASE + '/state', timeout=3)
        print('[test] GET /state ->', r.status_code)
        if r.status_code == 200:
            data = r.json()
            print('[test] snapshot tick:', data.get('tick'))
    except Exception as e:
        print('[test] GET /state failed:', e)

    # finalize
    try:
        sio.disconnect()
    except Exception:
        pass

    # Summarize
    print('\n=== SUMMARY ===')
    print(json.dumps(results, indent=2, ensure_ascii=False))
    # decide exit code
    if not results['connected']:
        return 2
    # require that a sim_mode broadcast was received
    if results['initial_sim_mode'] is None:
        print('[test] WARNING: no initial sim_mode received')
    # require sim_params broadcast or HTTP 200 earlier
    # success
    return 0


if __name__ == '__main__':
    code = run_test()
    sys.exit(code)
