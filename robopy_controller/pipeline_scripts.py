
# Embedded Script Nodes for OAK-D Lite (Myriad X Compatible)
# NOTE: No numpy, No cv2. Only standard library (math, json, re, time).

# --- Script 1: Spatial Bucketing Keypoints ---
# Pure Python Implementation
SCRIPT_SPATIAL_BUCKETING = """
import time

def bucket_keypoints(flat_data):
    GRID_SIZE = 6
    MAX_PER_CELL = 40
    WIDTH = 1280
    HEIGHT = 720
    
    cell_w = WIDTH / GRID_SIZE
    cell_h = HEIGHT / GRID_SIZE
    
    buckets = []
    for _ in range(GRID_SIZE * GRID_SIZE):
        buckets.append([])
        
    num_kps = len(flat_data) // 3
    
    for i in range(num_kps):
        offset = i * 3
        x = flat_data[offset]
        y = flat_data[offset+1]
        s = flat_data[offset+2]
        
        cx = int(x / cell_w)
        cy = int(y / cell_h)
        if cx >= GRID_SIZE: cx = GRID_SIZE - 1
        if cy >= GRID_SIZE: cy = GRID_SIZE - 1
        
        idx = cy * GRID_SIZE + cx
        buckets[idx].append((x, y, s))
        
    final_kps = []
    
    for cell in buckets:
        if len(cell) > 0:
            cell.sort(key=lambda item: item[2], reverse=True)
            count = 0
            for item in cell:
                if count >= MAX_PER_CELL: break
                final_kps.append(item[0]) # x
                final_kps.append(item[1]) # y
                final_kps.append(item[2]) # s
                count += 1
                
    return final_kps

while True:
    kps_msg = node.io['keypoints'].get()
    raw_data = kps_msg.getData()
    filtered_data = bucket_keypoints(raw_data)
    out_buf = Buffer(len(filtered_data) * 4)
    out_buf.setData(filtered_data)
    node.io['out_kps'].send(out_buf)
"""

# --- Script 2: Delta Encoding (Disabled) ---
SCRIPT_DELTA_ENCODING = None 

# --- Script 3: Adaptive ROI (Disabled) ---
SCRIPT_ADAPTIVE_ROI = None

# --- Script 4: Motion Triggered YOLO ---
SCRIPT_MOTION_YOLO = """
import time

class MotionTrigger:
    def __init__(self):
        self.prev = None
        self.counter = 0
        
    def check(self, frame_bytes):
        self.counter += 1
        if self.counter % 30 == 0:
            self.prev = frame_bytes
            return True
            
        if self.prev is None:
            self.prev = frame_bytes
            return True
            
        diff_sum = 0
        threshold = 30
        changed_pixels = 0
        total_pixels = len(frame_bytes)
        
        for i in range(0, total_pixels, 4):
            d = abs(frame_bytes[i] - self.prev[i])
            if d > threshold:
                changed_pixels += 1
                
        self.prev = frame_bytes
        ratio = changed_pixels / (total_pixels / 4)
        return ratio > 0.05

trigger = MotionTrigger()

while True:
    frame_msg = node.io['preview'].get()
    raw_bytes = frame_msg.getData()
    
    if trigger.check(raw_bytes):
        trig = Buffer(1)
        trig.setData([1])
        node.io['trigger'].send(trig)
    else:
        pass
"""

# --- Script 5: Sync Buffer ---
SCRIPT_SYNC_BUFFER = """
import time

d_buf = None
k_buf = None
y_buf = None

while True:
    if d_buf is None: d_buf = node.io['depth'].tryGet()
    if k_buf is None: k_buf = node.io['kp'].tryGet()
    if y_buf is None: y_buf = node.io['yolo'].tryGet()
    
    if d_buf and k_buf:
        seq_d = d_buf.getSequenceNum()
        seq_k = k_buf.getSequenceNum()
        
        if seq_d == seq_k:
            node.io['out_depth'].send(d_buf)
            node.io['out_kp'].send(k_buf)
            if y_buf: 
                node.io['out_yolo'].send(y_buf)
                y_buf = None
            d_buf = None
            k_buf = None
            
        elif seq_d < seq_k:
             d_buf = None
        else:
             k_buf = None
             
    time.sleep(0.001)
"""
