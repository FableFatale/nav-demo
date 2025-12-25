import json
import math
import random

# --- Configuration ---
TICKS_TOTAL = 1000
UAV_COUNT = 3
UGV_COUNT = 2 # We'll use 2 UGVs to rescue 3 targets (one will double back)
TARGET_COUNT = 3

# Locations
LOC_A = {"x": -50, "y": 0, "z": 50}   # Base
LOC_B = {"x": 0, "y": 0, "z": 0}      # Search Area Center
LOC_C = {"x": 50, "y": 0, "z": -50}   # Rescue Point

# Speeds (units per tick)
SPEED_UAV = 1.5
SPEED_UGV = 0.8

# Thresholds
SEARCH_RADIUS = 30.0
DETECTION_DIST = 15.0

# --- Classes ---

class Agent:
    def __init__(self, id, type, start_pos):
        self.id = id
        self.type = type
        self.pos = start_pos.copy()
        self.state = "IDLE"
        self.target_id = None # For UGV rescue assignment
        self.path = [] # List of target positions
        
    def move_towards(self, target_pos, speed):
        dx = target_pos["x"] - self.pos["x"]
        dz = target_pos["z"] - self.pos["z"]
        dist = math.sqrt(dx*dx + dz*dz)
        
        if dist <= speed:
            self.pos["x"] = target_pos["x"]
            self.pos["z"] = target_pos["z"]
            return True # Reached
        else:
            ratio = speed / dist
            self.pos["x"] += dx * ratio
            self.pos["z"] += dz * ratio
            return False

class Target:
    def __init__(self, id, pos):
        self.id = id
        self.pos = pos
        self.state = "UNDISCOVERED" # UNDISCOVERED, FOUND, RESCUED
        self.found_by = None
        self.rescued_by = None

# --- Simulation ---

def generate_mission():
    # Init Agents
    uavs = [Agent(f"uav_{i+1}", "UAV", LOC_A) for i in range(UAV_COUNT)]
    ugvs = [Agent(f"ugv_{i+1}", "UGV", LOC_A) for i in range(UGV_COUNT)]
    
    # Init Targets (Scattered around B)
    targets = []
    for i in range(TARGET_COUNT):
        angle = (i / TARGET_COUNT) * 2 * math.pi
        r = random.uniform(10, 25)
        t_pos = {
            "x": LOC_B["x"] + math.cos(angle) * r,
            "y": 0,
            "z": LOC_B["z"] + math.sin(angle) * r
        }
        targets.append(Target(f"target_{i+1}", t_pos))

    timeline = []
    events = []
    
    # Assign initial search targets for UAVs (spread out)
    for i, uav in enumerate(uavs):
        angle = (i / UAV_COUNT) * 2 * math.pi
        search_pos = {
            "x": LOC_B["x"] + math.cos(angle) * 20,
            "y": 0,
            "z": LOC_B["z"] + math.sin(angle) * 20
        }
        uav.path = [LOC_B, search_pos] # Go to B then specific search point
        uav.state = "MOVE_TO_B"

    # Main Loop
    for tick in range(TICKS_TOTAL):
        frame_agents = []
        frame_events = []
        
        # --- UAV Logic ---
        for uav in uavs:
            # State Machine
            if uav.state == "IDLE":
                pass # Stay at A
                
            elif uav.state == "MOVE_TO_B":
                if uav.move_towards(uav.path[0], SPEED_UAV):
                    uav.path.pop(0)
                    if not uav.path:
                        uav.state = "SEARCHING_B"
            
            elif uav.state == "SEARCHING_B":
                # Spiral / Random Search Movement
                angle = tick * 0.05 + (int(uav.id.split('_')[1]) * 2)
                radius = 20 + math.sin(tick * 0.02) * 10
                target_x = LOC_B["x"] + math.cos(angle) * radius
                target_z = LOC_B["z"] + math.sin(angle) * radius
                uav.move_towards({"x": target_x, "z": target_z}, SPEED_UAV * 0.5)
                
                # Check for detection
                for t in targets:
                    if t.state == "UNDISCOVERED":
                        dist = math.sqrt((uav.pos["x"]-t.pos["x"])**2 + (uav.pos["z"]-t.pos["z"])**2)
                        if dist < DETECTION_DIST:
                            # FOUND!
                            t.state = "FOUND"
                            t.found_by = uav.id
                            uav.state = "FOUND_TARGET" # Momentary state
                            
                            # Create Event
                            event = {
                                "type": "TARGET_FOUND",
                                "tick": tick,
                                "uav_id": uav.id,
                                "target_id": t.id,
                                "pos": t.pos
                            }
                            events.append(event)
                            frame_events.append(event)
                            
                            # Trigger UGV
                            # Find available UGV
                            avail_ugv = next((u for u in ugvs if u.state == "IDLE" or u.state == "WAITING_INFO"), None)
                            if avail_ugv:
                                avail_ugv.state = "MOVE_TO_C"
                                avail_ugv.target_id = t.id
                                avail_ugv.path = [LOC_C] # Go to C (Rescue point) - simplified logic: Go A -> C
                                # In reality, maybe go A -> Target -> C? 
                                # User said: "UGV_MOVE_A_TO_C" then "UGV_RESCUE"
                                # Let's assume rescue happens AT C (Target is brought there? Or UGV goes to target?)
                                # Re-reading: "UGV_MOVE_A_TO_C" -> "UGV_RESCUE" -> "UGV_RETURN_TO_A"
                                # And "C 救援点". So UGV goes to C to perform rescue.
                                # But target is at B. 
                                # Let's assume UGV goes A -> Target (at B) -> C -> A?
                                # Or simply A -> C (simulating remote rescue operation)
                                # User said: "A -> C (UGV 出发，高亮)"
                                # So let's stick to A -> C.
                            
            elif uav.state == "FOUND_TARGET":
                # Transition to Report/Return
                uav.state = "REPORT_TARGET"
                
            elif uav.state == "REPORT_TARGET":
                # Simulate reporting delay
                if random.random() < 0.1:
                    uav.state = "RETURN_A"
            
            elif uav.state == "RETURN_A":
                if uav.move_towards(LOC_A, SPEED_UAV):
                    uav.state = "IDLE"

            # Record Frame Data
            frame_agents.append({
                "id": uav.id,
                "type": "UAV",
                "x": uav.pos["x"], "y": 10, "z": uav.pos["z"],
                "state": uav.state
            })

        # --- UGV Logic ---
        for ugv in ugvs:
            if ugv.state == "IDLE":
                ugv.state = "WAITING_INFO" # Default start state
            
            elif ugv.state == "WAITING_INFO":
                pass # Wait for trigger
                
            elif ugv.state == "MOVE_TO_C":
                if ugv.move_towards(LOC_C, SPEED_UGV):
                    ugv.state = "RESCUE"
                    
            elif ugv.state == "RESCUE":
                # Simulate rescue time
                if random.random() < 0.05:
                    # Mark target rescued
                    if ugv.target_id:
                        t = next((t for t in targets if t.id == ugv.target_id), None)
                        if t: t.state = "RESCUED"
                    ugv.state = "RETURN_A"
                    
            elif ugv.state == "RETURN_A":
                if ugv.move_towards(LOC_A, SPEED_UGV):
                    ugv.state = "IDLE" # Ready for next?
                    # If there are more found targets pending rescue, could re-trigger.
                    # Simple logic: Check if any found but unrescued targets exist
                    pending = next((t for t in targets if t.state == "FOUND" and not t.rescued_by), None)
                    if pending:
                         # This UGV goes again?
                         # For simplicity, let's keep it simple.
                         pass

            frame_agents.append({
                "id": ugv.id,
                "type": "UGV",
                "x": ugv.pos["x"], "y": 0, "z": ugv.pos["z"],
                "state": ugv.state
            })

        # Check Mission Complete
        rescued_count = sum(1 for t in targets if t.state == "RESCUED")
        mission_state = "COMPLETE" if rescued_count == TARGET_COUNT else "IN_PROGRESS"

        timeline.append({
            "tick": tick,
            "agents": frame_agents,
            "events": frame_events, # New field
            "targets": [{"id": t.id, "state": t.state, "pos": t.pos} for t in targets], # Target states
            "mission_state": mission_state
        })
        
        if mission_state == "COMPLETE" and tick > 100: # Min duration
            break

    return timeline

if __name__ == '__main__':
    data = generate_mission()
    with open("mission.json", "w", encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Generated {len(data)} frames")
