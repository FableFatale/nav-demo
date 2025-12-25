#!/usr/bin/env python3
"""
extended_test.py

End-to-end test that:
 - Connects to the Socket.IO server
 - Ensures sim is RUNNING
 - Triggers a detection event (simulating a UAV) at the mission target
 - Polls /detections to verify assignment to a UGV and observe converge/confirm
 - Polls /state to validate UGV moves toward detection position
 - Writes a `test_report.json` with detailed observations

Usage: python extended_test.py
"""
import time
import json
import math
import sys
from pathlib import Path

import socketio
import requests

BASE = 'http://127.0.0.1:5000'
REPORT = Path('test_report.json')

def distance(a, b):
    return math.hypot(a['x'] - b['x'], a['z'] - b['z'])

def main():
    sio = socketio.Client(logger=False, engineio_logger=False)
    state_snapshot = None
    sim_mode = None

    @sio.on('state')
    def on_state(s):
        nonlocal state_snapshot
        state_snapshot = s

    @sio.on('sim_mode')
    def on_sim_mode(m):
        nonlocal sim_mode
        try:
            sim_mode = m.get('mode') if isinstance(m, dict) else str(m)
            print('[exttest] sim_mode ->', sim_mode)
        except Exception:
            pass

    print('[exttest] connecting socket...')
    try:
        sio.connect(BASE, transports=['websocket', 'polling'])
    except Exception as e:
        print('[exttest] socket connect failed:', e)
        return 2

    # give some time to receive sim_mode/state
    time.sleep(0.6)

    # ensure server is RUNNING for test
    if sim_mode != 'RUNNING':
        print('[exttest] requesting SIM_MODE RUNNING')
        try:
            sio.emit('set_sim_mode', 'RUNNING')
        except Exception:
            pass

    # fetch mission plan for target coordinates
    print('[exttest] fetching mission plan')
    try:
        r = requests.get(BASE + '/get_mission_plan', timeout=3)
        r.raise_for_status()
        mp = r.json()
        target = mp.get('locations', {}).get('C') or mp.get('locations') or {'x':50,'y':20,'z':-50}
    except Exception as e:
        print('[exttest] failed to fetch mission plan:', e)
        target = {'x':50,'y':20,'z':-50}

    # simulate a detection by uav_1 at the target position (or slightly offset)
    det_payload = {
        'agent_id': 'uav_1',
        'agent_type': 'UAV',
        'tick': int(time.time()),
        'x': target['x'] + 0.5,
        'y': target.get('y', 0),
        'z': target['z'] - 0.5,
        'target_id': 'C',
        'distance': 1.0
    }

    print('[exttest] emitting detection:', det_payload)
    try:
        sio.emit('detection', det_payload)
    except Exception as e:
        print('[exttest] emit detection failed:', e)

    # now poll /detections until we see an entry with assigned_ugv and then confirmed
    start = time.time()
    assigned = None
    det_entry = None
    report = {'detection_sent': det_payload, 'observations': []}

    timeout = 60.0
    while time.time() - start < timeout:
        try:
            r = requests.get(BASE + '/detections', timeout=2)
            if r.status_code == 200:
                data = r.json()
                arr = data.get('detections') or []
                # find matching detection (by agent_id and near tick/pos)
                for e in reversed(arr):
                    if e.get('agent_id') == det_payload['agent_id']:
                        det_entry = e
                        break
            if det_entry:
                assigned = det_entry.get('assigned_ugv')
                report['observations'].append({'time': time.time(), 'detection': det_entry})
                print('[exttest] found detection entry:', {'assigned': assigned, 'confirmed': det_entry.get('confirmed')})
                break
        except Exception as e:
            print('[exttest] polling detections failed:', e)
        time.sleep(0.8)

    if not det_entry:
        print('[exttest] detection entry not observed within timeout')
        report['result'] = 'fail_no_detection'
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        sio.disconnect()
        return 1

    # wait for assignment to appear (if not already)
    assigned_timeout = 40.0
    t0 = time.time()
    while time.time() - t0 < assigned_timeout:
        if det_entry.get('assigned_ugv'):
            assigned = det_entry.get('assigned_ugv')
            print('[exttest] assigned_ugv ->', assigned)
            break
        # refresh
        try:
            r = requests.get(BASE + '/detections', timeout=2)
            if r.status_code == 200:
                arr = r.json().get('detections') or []
                for e in reversed(arr):
                    if e.get('agent_id') == det_payload['agent_id']:
                        det_entry = e
                        break
        except Exception:
            pass
        time.sleep(0.8)

    if not det_entry.get('assigned_ugv'):
        print('[exttest] no assignment observed')
        report['result'] = 'fail_no_assignment'
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        sio.disconnect()
        return 2

    # Observe UGV movement: poll /state and check assigned UGV moves closer to detection pos
    assigned_id = det_entry.get('assigned_ugv')
    converge_start = time.time()
    moved_closer = False
    initial_dist = None
    converge_timeout = 60.0
    while time.time() - converge_start < converge_timeout:
        try:
            r = requests.get(BASE + '/state', timeout=2)
            if r.status_code == 200:
                s = r.json()
                agents = s.get('agents') or []
                # find assigned ugv and current detection pos
                ugv_state = None
                for a in agents:
                    if a.get('id') == assigned_id:
                        ugv_state = a
                        break
                if ugv_state:
                    dpos = det_entry.get('pos') or {'x': det_payload['x'], 'z': det_payload['z']}
                    ugv_pos = {'x': ugv_state.get('x'), 'z': ugv_state.get('z')}
                    curdist = math.hypot(ugv_pos['x'] - dpos['x'], ugv_pos['z'] - dpos['z'])
                    if initial_dist is None:
                        initial_dist = curdist
                    else:
                        if curdist < initial_dist - 0.5:
                            moved_closer = True
                    report.setdefault('ugv_positions', []).append({'time': time.time(), 'pos': ugv_pos, 'dist': curdist})
                    print('[exttest] assigned UGV dist ->', curdist)
                    # check confirmed flag on detection
                    try:
                        r2 = requests.get(BASE + '/detections', timeout=2)
                        arr = r2.json().get('detections') or []
                        for e in reversed(arr):
                            if e.get('agent_id') == det_payload['agent_id']:
                                det_entry = e
                                break
                        if det_entry.get('confirmed'):
                            print('[exttest] detection confirmed!')
                            report['result'] = 'success_confirmed'
                            REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
                            sio.disconnect()
                            return 0
                    except Exception:
                        pass
        except Exception as e:
            print('[exttest] GET /state failed:', e)
        time.sleep(1.0)

    # finished waiting
    report['moved_closer'] = moved_closer
    report['final_detection'] = det_entry
    if det_entry.get('confirmed'):
        report['result'] = 'success_confirmed'
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        sio.disconnect()
        return 0
    else:
        report['result'] = 'timeout_not_confirmed'
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        sio.disconnect()
        return 3


if __name__ == '__main__':
    code = main()
    print('\n[exttest] exit code', code)
    sys.exit(code)
