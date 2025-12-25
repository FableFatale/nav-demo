import json
import os
import threading
import time
import math
import socket
import subprocess
import sys
import random
from flask import Flask, jsonify, send_file, request
from flask_cors import CORS
from flask_socketio import SocketIO

# 获取当前脚本所在的绝对路径，确保能找到 index.html
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins='*')

# track connected clients
CLIENTS_CONNECTED = 0

# Simulation mode: 'RUNNING' | 'PAUSED' | 'COMPLETE'
SIM_MODE = 'PAUSED'

# --- Global Event Buffer for Playback ---
CURRENT_TICK_EVENTS = []

def emit_event(event_type, msg):
    """Helper to emit event to socket AND record it for history"""
    evt_data = {'type': event_type, 'msg': msg}
    socketio.emit('event', evt_data)
    CURRENT_TICK_EVENTS.append(evt_data)

# --- 1. Locations & Config ---
LOCATIONS = {
    "A": {"x": -50, "y": 0, "z": 50},   # Base
    "B": {"x": 0, "y": 0, "z": 0},      # Search Center
    "C": {"x": 60, "y": 0, "z": -60},   # Patrol Point (Extended to cover T3)
    "T1": {"x": -20, "y": 0, "z": 20},
    "T2": {"x": 0, "y": 0, "z": 0},      # Center of B
    "T3": {"x": 60, "y": 0, "z": -60}    # Outer edge of C
}

# --- 2. Classes ---

class Human:
    def __init__(self, t_id, pos):
        self.id = t_id
        self.position = pos
        self.state = 'UNSEEN' # UNSEEN, DETECTED, CONFIRMED, RESCUED
        self.detected_by = [] # List of UAV IDs
        self.first_detected_time = None # Tick when first detected
        self.detected_since_tick = None # Alias for logic consistency

class UAV:
    def __init__(self, uav_id, start_pos):
        self.id = uav_id
        self.type = 'UAV'
        self.velocity = {'x': 0, 'y': 0, 'z': 0} # Add velocity for smoothing
        self.state = 'IDLE' # IDLE, TAKEOFF, PATROL, REPORTING, RETURN, LANDING
        self.position = start_pos.copy()
        self.target_pos = None
        self.role = 'LEADER' if uav_id == 'UAV1' else 'FOLLOWER'
        self.hover_start_tick = None
        
        # Patrol Route: A -> B -> C -> A
        self.patrol_route = ['B', 'C', 'A']
        self.current_route_index = 0

    def update(self):
        if self.state == 'IDLE':
            pass
        elif self.state == 'TAKEOFF':
            # Takeoff to height 10 (per spec)
            target_h = 10
            if self.position['y'] < target_h:
                self.position['y'] += 1
            else:
                self.state = 'PATROL'
                
        elif self.state == 'PATROL':
            # Route Logic: Fly to current route point
            target_key = self.patrol_route[self.current_route_index]
            base_target = LOCATIONS[target_key]
            
            self.target_pos = {
                'x': base_target['x'] + random.uniform(-2, 2), 
                'y': 10, 
                'z': base_target['z'] + random.uniform(-2, 2)
            }
            
            self.move_to(self.target_pos)
            
            # Check if reached route point
            if self.distance_to(self.target_pos) < 5:
                # Move to next point
                self.current_route_index = (self.current_route_index + 1) % len(self.patrol_route)

            # Detection Logic (Perception Layer)
            for t in TARGETS:
                # Use 2D distance (ignore altitude) for detection
                dist_2d = self.distance_to_2d(t.position)
                if t.state == 'UNSEEN' and dist_2d < 10:
                    # 1. Trigger HUMAN_DETECTED event
                    t.state = 'DETECTED'
                    t.detected_since_tick = TICK
                    t.first_detected_time = TICK
                    if self.id not in t.detected_by:
                        t.detected_by.append(self.id)
                    
                    # 2. UAV State Change
                    self.state = 'REPORTING'
                    self.target_pos = {'x': t.position['x'], 'y': 10, 'z': t.position['z']} # Hover above
                    self.hover_start_tick = TICK
                    
                    emit_event('HUMAN_DETECTED', f'{self.id} 发现目标 {t.id} (UNSEEN -> DETECTED)')
                    break
                elif t.state == 'DETECTED' and dist_2d < 5:
                     # Already detected, just add self to detected_by if not present (Collaborative Sensing)
                    if self.id not in t.detected_by:
                        t.detected_by.append(self.id)

        elif self.state == 'REPORTING':
            # Hover above target for 60 ticks
            if self.target_pos:
                self.move_to(self.target_pos)
                
                if TICK - self.hover_start_tick > 60: 
                    # Finished reporting. 
                    # Spec: "Return to PATROL or execute RETURN (if strategy so)"
                    # Strategy: Continue PATROL to maintain coverage unless Mission Complete.
                    self.state = 'PATROL'
                    # Note: UAV does NOT decide confirmation. It just reports and moves on.
        
        elif self.state == 'RETURN':
            # Return to Base A (Hover Point)
            base_a = LOCATIONS['A']
            self.target_pos = {'x': base_a['x'], 'y': 10, 'z': base_a['z']}
            self.move_to(self.target_pos)
            
            if self.distance_to(self.target_pos) < 5:
                self.state = 'LANDING'

        elif self.state == 'LANDING':
            # Descend to Ground
            base_a = LOCATIONS['A']
            self.target_pos = {'x': base_a['x'], 'y': 0, 'z': base_a['z']}
            self.move_to(self.target_pos)
            
            if self.position['y'] < 0.5:
                self.state = 'IDLE'

    def move_to(self, target):
        # Steering Behavior: Seek + Arrive
        dest = target
        curr = self.position
        
        # Desired velocity
        dx = dest['x'] - curr['x']
        dy = dest['y'] - curr['y']
        dz = dest['z'] - curr['z']
        dist = math.sqrt(dx*dx + dy*dy + dz*dz)
        
        max_speed = 1.0
        steering_factor = 0.05 # Inertia factor (lower = more inertia/smoothness)
        
        if dist < 0.1:
            self.position = dest.copy()
            self.velocity = {'x': 0, 'y': 0, 'z': 0}
            return

        # Arrive: Slow down when close
        target_speed = max_speed
        slow_radius = 10.0
        if dist < slow_radius:
            target_speed = max_speed * (dist / slow_radius)
            
        # Normalize desired
        desired_vx = (dx / dist) * target_speed
        desired_vy = (dy / dist) * target_speed
        desired_vz = (dz / dist) * target_speed
        
        # Steering force = desired - velocity
        steer_x = desired_vx - self.velocity['x']
        steer_y = desired_vy - self.velocity['y']
        steer_z = desired_vz - self.velocity['z']

        # Separation: Avoid crowding
        sep_x, sep_y, sep_z = 0, 0, 0
        count = 0
        
        # Only apply separation if far from target
        if dist > 5.0:
            for other in AGENTS.values():
                if other.id != self.id:
                    d = self.distance_to(other.position)
                    if d > 0 and d < 2.0: # Separation radius
                        # Push away
                        diff_x = self.position['x'] - other.position['x']
                        diff_y = self.position['y'] - other.position['y']
                        diff_z = self.position['z'] - other.position['z']
                        # Weight by distance
                        sep_x += diff_x / d
                        sep_y += diff_y / d
                        sep_z += diff_z / d
                        count += 1
        
        if count > 0:
            sep_x /= count
            sep_y /= count
            sep_z /= count
            # Normalize and scale
            sep_len = math.sqrt(sep_x**2 + sep_y**2 + sep_z**2)
            if sep_len > 0:
                sep_x = (sep_x / sep_len) * max_speed
                sep_y = (sep_y / sep_len) * max_speed
                sep_z = (sep_z / sep_len) * max_speed
                # Steering for separation
                sep_x -= self.velocity['x']
                sep_y -= self.velocity['y']
                sep_z -= self.velocity['z']
                
                # Add to total steering (weight separation higher)
                steer_x += sep_x * 2.0
                steer_y += sep_y * 2.0
                steer_z += sep_z * 2.0
        
        # Apply steering to velocity
        self.velocity['x'] += steer_x * steering_factor
        self.velocity['y'] += steer_y * steering_factor
        self.velocity['z'] += steer_z * steering_factor
        
        # Update position
        self.position['x'] += self.velocity['x']
        self.position['y'] += self.velocity['y']
        self.position['z'] += self.velocity['z']

    def distance_to_2d(self, target_pos):
        return math.sqrt((self.position['x'] - target_pos['x'])**2 + 
                         (self.position['z'] - target_pos['z'])**2)

    def distance_to(self, target_pos):
        return math.sqrt((self.position['x'] - target_pos['x'])**2 + 
                         (self.position['y'] - target_pos['y'])**2 + 
                         (self.position['z'] - target_pos['z'])**2)

class UGV:
    def __init__(self, ugv_id, start_pos):
        self.id = ugv_id
        self.type = 'UGV'
        self.velocity = {'x': 0, 'y': 0, 'z': 0}
        self.state = 'STANDBY' # STANDBY, DISPATCH, RESCUING, RETURNING
        self.position = start_pos.copy()
        self.target_human_id = None
        self.target_pos = None
        self.rescue_timer = 0

    def update(self):
        if self.state == 'STANDBY':
            pass # Wait for system dispatch

        elif self.state == 'DISPATCH':
            if self.target_pos:
                self.move_to(self.target_pos)
                if self.distance_to(self.target_pos) < 5: # Increased arrival threshold (was 2)
                    self.state = 'RESCUING'
                    self.rescue_timer = 0
                    emit_event('RESCUE_START', f'{self.id} 到达位置，开始救援 {self.target_human_id}')

        elif self.state == 'RESCUING':
            self.rescue_timer += 1
            if self.rescue_timer >= 40: # 40 ticks duration
                # Mark target as rescued
                for t in TARGETS:
                    if t.id == self.target_human_id:
                        t.state = 'RESCUED'
                        emit_event('TARGET_RESCUED', f'{self.id} 成功救援 {t.id} (CONFIRMED -> RESCUED)')
                        break
                
                self.state = 'RETURNING'
                self.target_human_id = None
                self.target_pos = LOCATIONS['A'] # Return to base

        elif self.state == 'RETURNING':
            self.move_to(LOCATIONS['A'])
            if self.distance_to(LOCATIONS['A']) < 5: # Increased standby threshold (was 2)
                self.state = 'STANDBY'

    def move_to(self, target):
        # Steering Behavior: Seek + Arrive (2D for UGV)
        dest = target
        curr = self.position
        
        dx = dest['x'] - curr['x']
        dz = dest['z'] - curr['z']
        dist = math.sqrt(dx*dx + dz*dz)
        
        max_speed = 0.5
        steering_factor = 0.05
        
        if dist < 0.1:
            self.position['x'] = dest['x']
            self.position['z'] = dest['z']
            self.velocity = {'x': 0, 'y': 0, 'z': 0}
            return

        target_speed = max_speed
        slow_radius = 5.0
        if dist < slow_radius:
            target_speed = max_speed * (dist / slow_radius)
            
        desired_vx = (dx / dist) * target_speed
        desired_vz = (dz / dist) * target_speed
        
        steer_x = desired_vx - self.velocity['x']
        steer_z = desired_vz - self.velocity['z']
        
        self.velocity['x'] += steer_x * steering_factor
        self.velocity['z'] += steer_z * steering_factor
        
        self.position['x'] += self.velocity['x']
        self.position['z'] += self.velocity['z']

    def distance_to(self, target_pos):
        return math.sqrt((self.position['x'] - target_pos['x'])**2 + 
                         (self.position['z'] - target_pos['z'])**2)

# --- 3. Initialization ---
def init_simulation():
    global AGENTS, TICK, MISSION_PHASE, HISTORY, CURRENT_TICK_EVENTS, TARGETS, SIM_MODE
    AGENTS = {}
    TICK = 0
    MISSION_PHASE = "READY"
    SIM_MODE = "PAUSED" # Force pause on init
    HISTORY = []
    CURRENT_TICK_EVENTS = []
    
    # Reset Targets
    TARGETS = [
        Human("T1", LOCATIONS["T1"]),
        Human("T2", LOCATIONS["T2"]),
        Human("T3", LOCATIONS["T3"])
    ]

    # Create Agents
    # UAVs at Base A
    AGENTS["UAV1"] = UAV("UAV1", LOCATIONS["A"])
    AGENTS["UAV2"] = UAV("UAV2", {"x": LOCATIONS["A"]["x"] + 2, "y": 0, "z": LOCATIONS["A"]["z"] + 2})
    AGENTS["UAV3"] = UAV("UAV3", {"x": LOCATIONS["A"]["x"] - 2, "y": 0, "z": LOCATIONS["A"]["z"] - 2})
    
    # UGVs at Base A
    AGENTS["UGV1"] = UGV("UGV1", {"x": LOCATIONS["A"]["x"] + 5, "y": 0, "z": LOCATIONS["A"]["z"]})
    AGENTS["UGV2"] = UGV("UGV2", {"x": LOCATIONS["A"]["x"] - 5, "y": 0, "z": LOCATIONS["A"]["z"]})

    print("Simulation Initialized.")

init_simulation()

def build_state():
    agent_states = []
    for agent in AGENTS.values():
        agent_states.append({
            "id": agent.id,
            "type": agent.type,
            "state": agent.state,
            "x": agent.position["x"],
            "y": agent.position["y"],
            "z": agent.position["z"],
            "role": getattr(agent, 'role', '')
        })
    
    target_states = []
    for t in TARGETS:
        target_states.append({
            "id": t.id,
            "state": t.state,
            "x": t.position["x"],
            "y": t.position["y"],
            "z": t.position["z"],
            "detected_by": t.detected_by # Include for UI Collaborative Task view
        })

    # Include events in the state snapshot for playback consistency
    state_events = list(CURRENT_TICK_EVENTS) 
    
    return {
        "tick": TICK,
        "mission_phase": MISSION_PHASE,
        "sim_mode": SIM_MODE,
        "agents": agent_states,
        "targets": target_states,
        "events": state_events
    }

def background_simulator():
    global TICK, SIM_MODE, MISSION_PHASE, CURRENT_TICK_EVENTS
    print("Background simulator started.")
    while True:
        time.sleep(0.2) # 5 TPS (Slower)
        
        # --- DEBUG LOG ---
        if TICK % 20 == 0:
             print(f"[Heartbeat] Mode: {SIM_MODE}, Tick: {TICK}, Phase: {MISSION_PHASE}")
        # -----------------

        try:
            if SIM_MODE == 'RUNNING':
                TICK += 1
                CURRENT_TICK_EVENTS = [] # Clear events for this tick
                
                # Mission Logic Transition
                if MISSION_PHASE == "READY" and TICK > 0:
                    MISSION_PHASE = "PATROL"
                    # Trigger UAV Takeoff
                    for agent in AGENTS.values():
                        if agent.type == 'UAV' and agent.state == 'IDLE':
                            agent.state = 'TAKEOFF'

                # Update Agents
                for agent in AGENTS.values():
                    agent.update()

                # --- Decision Layer (System Logic) ---
                
                # 1. Target Confirmation Logic
                for t in TARGETS:
                    if t.state == 'DETECTED':
                        # Condition A: Time threshold (> 40 ticks)
                        time_condition = (TICK - t.first_detected_time) > 40
                        # Condition B: Multi-UAV confirmation (>= 2 UAVs)
                        multi_uav_condition = len(t.detected_by) >= 2
                        
                        if time_condition or multi_uav_condition:
                            t.state = 'CONFIRMED'
                            reason = "超时确认" if time_condition else "多机确认"
                            emit_event('TARGET_CONFIRMED', f'系统确认目标 {t.id} ({reason})')
                            
                            # Update Phase if needed
                            if MISSION_PHASE == "PATROL":
                                MISSION_PHASE = "RESCUE"

                # 2. UGV Dispatch Logic (Priority: Earliest Discovery First)
                # Filter confirmed targets that are not yet assigned/rescued
                confirmed_targets = [t for t in TARGETS if t.state == 'CONFIRMED']
                
                # Sort by first_detected_time (Earliest First)
                confirmed_targets.sort(key=lambda x: x.first_detected_time)

                for t in confirmed_targets:
                    # Check if already assigned
                    is_assigned = False
                    for agent in AGENTS.values():
                        if agent.type == 'UGV' and agent.target_human_id == t.id:
                            is_assigned = True
                            break
                    
                    if not is_assigned:
                        # Find free UGV
                        free_ugv = None
                        for agent in AGENTS.values():
                            if agent.type == 'UGV' and agent.state == 'STANDBY':
                                free_ugv = agent
                                break
                        
                        if free_ugv:
                            free_ugv.state = 'DISPATCH'
                            free_ugv.target_human_id = t.id
                            free_ugv.target_pos = t.position
                            emit_event('UGV_DISPATCHED', f'系统调度 {free_ugv.id} 前往救援 {t.id} (最早发现优先)')

                # -------------------------------------
                
                # Check Mission Progress
                all_rescued = all(t.state == 'RESCUED' for t in TARGETS)
                # Tightened check: Must be closer to base (< 5)
                all_ugvs_home = all((a.state == 'STANDBY' or (a.state == 'RETURNING' and a.distance_to(LOCATIONS["A"]) < 5)) for a in AGENTS.values() if a.type == 'UGV')
                
                if all_rescued:
                    # If all humans are rescued, recall UAVs
                    for agent in AGENTS.values():
                        if agent.type == 'UAV' and agent.state not in ['RETURN', 'IDLE', 'LANDING']:
                            agent.state = 'RETURN'
                            emit_event('UAV_RETURN', f"{agent.id} 任务结束，正在返航")

                # Check if UAVs are home (Tightened distance < 5)
                all_uavs_home = all(a.state == 'IDLE' for a in AGENTS.values() if a.type == 'UAV')

                # Stop UAVs if they are home
                if all_rescued:
                    for agent in AGENTS.values():
                        if agent.type == 'UAV' and agent.state == 'RETURN' and agent.distance_to(LOCATIONS["A"]) < 2:
                            agent.state = 'IDLE'

                if all_rescued and all_ugvs_home and all_uavs_home and MISSION_PHASE != "COMPLETE":
                    MISSION_PHASE = "COMPLETE"
                    SIM_MODE = "COMPLETE" # Stop simulation
                    emit_event('MISSION_COMPLETE', "所有目标已救援，全员返航，任务完成！")

                # Broadcast State
                state = build_state()
                HISTORY.append(state)
                # print(f"Emitting state tick {TICK}") # Optional verbose log
                socketio.emit('state', state)
        except Exception as e:
            print(f"Error in simulation loop: {e}")
            import traceback
            traceback.print_exc()
            try:
                socketio.emit('event', {'type': 'ERROR', 'msg': f"后端错误: {str(e)}"})
            except:
                pass

# Start Background Task
socketio.start_background_task(background_simulator)

# --- Routes ---
@app.route('/')
def index():
    return send_file(os.path.join(BASE_DIR, 'index.html'))

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/export_timeline')
def export_timeline():
    try:
        # Return the recorded history
        return jsonify(HISTORY)
    except Exception as e:
        print(f"Export error: {e}")
        return jsonify({"error": str(e)}), 500

@socketio.on('connect')
def handle_connect():
    global CLIENTS_CONNECTED
    CLIENTS_CONNECTED += 1
    print(f"Client connected. Total: {CLIENTS_CONNECTED}")
    # Reset simulation on new connection (Refresh = Reset)
    init_simulation()
    # Send initial state immediately
    socketio.emit('state', build_state())

@socketio.on('disconnect')
def handle_disconnect():
    global CLIENTS_CONNECTED
    CLIENTS_CONNECTED -= 1
    print(f"Client disconnected. Total: {CLIENTS_CONNECTED}")

@socketio.on('set_sim_mode')
def handle_set_mode(mode):
    global SIM_MODE
    print(f"[socket] Setting SIM_MODE to {mode}")
    SIM_MODE = mode
    socketio.emit('state', build_state())

@socketio.on('reset_simulation')
def handle_reset():
    print("[socket] Resetting simulation...")
    init_simulation()
    emit_event('RESET', "仿真已重置")
    socketio.emit('state', build_state())

if __name__ == '__main__':
    # Try port 5002 to avoid conflicts
    port = 5002
    try:
        # Kill existing process on port 5002 (Windows)
        subprocess.run(f"netstat -ano | findstr :{port}", shell=True)
        pass
    except:
        pass

    try:
        print(f"Starting server on port {port}...")
        socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
    except OSError:
        print(f"Port {port} failed to bind.")
