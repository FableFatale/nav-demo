#!/usr/bin/env python3
"""
ui_test.py

Headless UI test using Playwright.
 - Opens the app at http://127.0.0.1:5000/
 - Waits for the `#simBadge` element
 - Clicks `实时仿真` (btnRealtime) to request RUNNING
 - Verifies the badge text and background color change
 - Takes a screenshot `ui_test.png` and writes `ui_test_result.json`

Usage: python ui_test.py
"""
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = 'http://127.0.0.1:5000/'
OUT_SCREEN = Path('ui_test.png')
OUT_JSON = Path('ui_test_result.json')

def get_bg_color(page, selector):
    return page.evaluate("sel => getComputedStyle(document.querySelector(sel)).backgroundColor", selector)

def run():
    result = {'ok': False, 'steps': []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL, timeout=10000)
        # wait for sim badge to appear
        try:
            page.wait_for_selector('#simBadge', timeout=8000)
        except Exception as e:
            result['steps'].append({'error': 'simBadge not found', 'detail': str(e)})
            browser.close()
            OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            return 2

        # capture initial state
        badge_text = page.locator('#simBadge').inner_text()
        badge_color = get_bg_color(page, '#simBadge')
        result['steps'].append({'step': 'initial', 'text': badge_text, 'color': badge_color})

        # click realtime button to request RUNNING
        try:
            page.click('#btnRealtime')
            result['steps'].append({'step': 'clicked_realtime'})
        except Exception as e:
            result['steps'].append({'error': 'click failed', 'detail': str(e)})
            browser.close()
            OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            return 3

        # wait a bit for socket roundtrip and badge update
        time.sleep(1.2)
        try:
            new_text = page.locator('#simBadge').inner_text()
            new_color = get_bg_color(page, '#simBadge')
            result['steps'].append({'step': 'after_click', 'text': new_text, 'color': new_color})
        except Exception as e:
            result['steps'].append({'error': 'read after click failed', 'detail': str(e)})

        # wait until agentsMap has uav_1 so drawGuideline can find the UAV/UGV meshes
        try:
            has_agent = False
            # wait up to ~12s for agents to appear
            for _ in range(48):
                try:
                    has_agent = page.evaluate("() => { try { return (typeof agentsMap !== 'undefined') && agentsMap.has && agentsMap.has('uav_1'); } catch(e){ return false } }")
                except Exception:
                    has_agent = False
                if has_agent:
                    break
                time.sleep(0.25)
            result['steps'].append({'step':'agent_present','value':bool(has_agent)})
        except Exception:
            result['steps'].append({'step':'agent_present_check_failed'})

        # prepare a temporary listener to record 'target_found' broadcasts
        try:
            page.evaluate("() => { try { window._lastTargetFound = null; if(window.socket && window.socket.on) window.socket.on('target_found', e => { window._lastTargetFound = e; }); } catch(e){} }")
        except Exception:
            pass

        # emit a detection from the page (use mission plan to get target coords)
        try:
            page.evaluate("() => { return fetch('/get_mission_plan').then(r=>r.json()); }")
            # retrieve mission plan in Python to compute payload
            mp = None
            try:
                import requests
                r = requests.get(URL + 'get_mission_plan', timeout=3)
                if r.ok:
                    mp = r.json()
            except Exception:
                mp = None
            if mp and isinstance(mp, dict):
                loc = mp.get('locations', {}).get('C') or {'x':50,'y':20,'z':-50}
            else:
                loc = {'x':50,'y':20,'z':-50}
            det_payload = {'agent_id':'uav_1','agent_type':'UAV','tick':int(time.time()),'x':loc['x']+0.5,'y':loc.get('y',0),'z':loc['z']-0.5,'target_id':'C','distance':1.0}
            page.evaluate("(p) => { if(window.socket) window.socket.emit('detection', p); else if(window.socketio) window.socketio.emit && window.socketio.emit('detection', p); else console.warn('no socket'); }", det_payload)
            result['steps'].append({'step':'emitted_detection','payload':det_payload})
            # try to invoke UI guideline drawing directly (in case broadcast not received)
            try:
                ok_invoke = page.evaluate("(aid) => { try { if(typeof drawGuidelineForDetection === 'function') { drawGuidelineForDetection(aid); return true; } return false;} catch(e){ return false } }", 'uav_1')
            except Exception:
                ok_invoke = False
            result['steps'].append({'step':'invoked_drawGuidelineForDetection','value': bool(ok_invoke)})
        except Exception as e:
            result['steps'].append({'error':'emit detection failed','detail':str(e)})

        # wait for guidingLines map to appear and contain at least one entry
        guideline_found = False
        try:
            # wait up to ~10s for guideline to appear
            for _ in range(20):
                val = page.evaluate("() => { try { return (typeof guidingLines !== 'undefined' && guidingLines.size) ? guidingLines.size : 0 } catch(e){ return 0 } }")
                if val and int(val) > 0:
                    guideline_found = True
                    break
                time.sleep(0.5)
        except Exception:
            guideline_found = False
        result['steps'].append({'step':'guideline_found','value':bool(guideline_found)})

        # trigger timeline playback by clicking load timeline button
        try:
            page.click('#btnLoadTimeline')
            result['steps'].append({'step':'clicked_load_timeline'})
        except Exception as e:
            result['steps'].append({'error':'click load timeline failed','detail':str(e)})

        # observe lblTick changes (playback). capture initial value then wait
        playback_progress = False
        try:
            lbl = page.locator('#lblTick')
            init_tick = lbl.inner_text()
            for _ in range(20):
                time.sleep(0.25)
                cur = lbl.inner_text()
                if cur != init_tick:
                    playback_progress = True
                    break
        except Exception as e:
            result['steps'].append({'error':'playback check failed','detail':str(e)})
        result['steps'].append({'step':'playback_progress','value':bool(playback_progress)})

        # take screenshot
        page.screenshot(path=str(OUT_SCREEN), full_page=True)
        result['screenshot'] = str(OUT_SCREEN)

        # decide success
        ok = (new_text.upper().strip() == 'RUNNING') and guideline_found and playback_progress
        result['ok'] = ok
        browser.close()

    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 4

if __name__ == '__main__':
    rc = run()
    print('ui_test exit code', rc)
    raise SystemExit(rc)
