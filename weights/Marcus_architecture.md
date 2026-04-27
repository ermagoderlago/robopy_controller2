# MARCUS ARCHITECTURE v5.1
## Cloud-Native Cognitive Home Companion – Technical Specification

**Status**: Ready for Implementation  
**Version**: 5.1  
**Date**: 2025-02-13  
**Target**: Raspberry Pi 5 (8GB) + OAK-D + ROS 2 Humble + Gemini 2.0 Cloud  
**Philosophy**: Maximum reasoning power, acceptable latency, radical honesty  

---

## 1. OPERATIONAL STATE MACHINE

### 1.1 States & Transitions

```
┌─────────────┐
│   ONLINE    │ ← Internet OK, Gemini responsive
│  <2.5s lat  │
└──────┬──────┘
       │ latency > 2.5s for 3 consecutive API calls
       ↓
┌─────────────┐
│  DEGRADED   │ ← Rate limited OR slow Gemini responses
│  2.5-5s     │
└──────┬──────┘
       │ latency > 5s OR API error rate > 20% for 10s
       ↓
┌─────────────┐
│  OFFLINE    │ ← No internet OR Gemini unreachable for >30s
│   ∞ latency │
└─────────────┘
```

### 1.2 Transitions Definition

**WHO MONITORS TRANSITIONS**: ROS node `connectivity_manager.py`

**HOW**:
```python
class ConnectivityManager:
    def __init__(self):
        self.state = "ONLINE"
        self.latency_window = deque(maxlen=5)  # Last 5 API calls
        self.error_window = deque(maxlen=10)   # Last 10 API results
        self.state_changed_at = time.time()
        
    def monitor(self):
        """Called every API response"""
        latency = time_since_request()
        is_error = response.status != 200
        
        self.latency_window.append(latency)
        self.error_window.append(is_error)
        
        avg_latency = mean(self.latency_window)
        error_rate = sum(self.error_window) / len(self.error_window)
        
        # Transition logic
        if self.state == "ONLINE":
            if avg_latency > 2.5 and len(self.latency_window) == 5:
                self.transition_to("DEGRADED")
        
        elif self.state == "DEGRADED":
            if avg_latency < 1.5:  # Recovered
                self.transition_to("ONLINE")
            elif avg_latency > 5.0 or error_rate > 0.2:
                self.transition_to("OFFLINE")
        
        elif self.state == "OFFLINE":
            if latency < 3.0 and not is_error:  # Single recovery attempt
                self.transition_to("DEGRADED")
```

### 1.3 Behavior per State

#### ONLINE (Normal Operation)
```yaml
Input Processing:
  - Accept user input immediately
  - Retrieve from ChromaDB (top-5 episodic + semantic)
  - Send to Gemini with full context
  
Output:
  - Natural language + JSON actions
  - Execute HA commands immediately
  - Log decision to trace_db
  
Queue:
  - Process requests FIFO
  - No queue, respond immediately

Notification:
  - None (normal)
```

#### DEGRADED (Slow or Rate-Limited)
```yaml
Input Processing:
  - Queue complex requests (latency > 2s estimated)
  - Process simple commands locally if whitelisted
  - Skip non-urgent RAG retrievals (use cached results)
  
Output:
  - Prioritize: mandatory requests > optional > suggestions
  - Say: "Sono un po' lento, ma sto ragionando..."
  
Queue:
  - FIFO + priority flag
  - timeout = 30s (discard if not processed)
  
Simple Commands (Local, <500ms):
  - "accendi luce" → reflex + Vosk + whitelist
  - "che ora è?" → local (no Gemini)
  - Multi-step planning → queue for Gemini

Notification:
  - Alert user: "Sto funzionando lentamente, scusa"
```

#### OFFLINE (No Internet)
```yaml
Input Processing:
  - REJECT complex requests
  - Accept ONLY whitelisted simple commands
  - Whitelist: lights on/off, tapparelle open/close, time queries
  
Output:
  - Simple commands: execute locally
  - Complex requests: deny with "sono offline"
  
Task Persistence:
  - Save multi-step task to SQLite (see section 2)
  - Timestamp: when did we go offline?
  - Status: which step were we on?
  
Queue:
  - Clear queue (no point queueing if offline)
  
Notification:
  - "Ho perso internet. Funziono solo con comandi semplici."
  - When reconnecting: "Riaperta connessione!"

Whitelist (Hardcoded):
  - device="tapparelle" + operation in ["open", "close", "stop"]
  - device="luci" + operation in ["on", "off"]
  - info="time" or "date"
  - NO multi-step, NO HA sensors, NO memory access
```

---

## 2. TASK PERSISTENCE & TIMELINE AWARENESS

### 2.1 Task Schema (SQLite)

```sql
CREATE TABLE multi_step_tasks (
    task_id TEXT PRIMARY KEY,
    task_name TEXT,
    created_at TIMESTAMP,
    last_gemini_sync TIMESTAMP,
    total_steps INTEGER,
    current_step INTEGER,
    
    -- Serialized task state
    task_json JSON,  -- Full task definition
    
    -- Risk tracking
    risk_level TEXT DEFAULT 'low',  -- low | medium | high | critical
    risk_reason TEXT,
    
    -- Timeline
    resumption_note TEXT,  -- What to tell user when resuming
    
    status TEXT DEFAULT 'running',  -- running | paused_offline | completed | failed
    
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE task_steps (
    step_id TEXT PRIMARY KEY,
    task_id TEXT,
    step_number INTEGER,
    action_type TEXT,  -- navigate, machine_on, wait_brew, etc
    
    -- Step state
    status TEXT DEFAULT 'pending',  -- pending | running | completed | failed
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    
    -- Timeline tracking (CRITICAL for offline recovery)
    expected_duration_seconds INTEGER,  -- How long should this step take?
    timeout_seconds INTEGER,  -- Fail if takes longer
    
    -- Serialize action
    action_json JSON,
    
    FOREIGN KEY(task_id) REFERENCES multi_step_tasks(task_id)
);
```

### 2.2 Task Persistence Workflow

**ONLINE - User requests: "Porta caffè in salotto"**
```python
# 1. Create task
task_id = uuid.uuid4()
task = {
    "task_id": task_id,
    "task_name": "coffee_delivery",
    "created_at": now(),
    "last_gemini_sync": now(),
    "steps": [
        {
            "step_number": 1,
            "action_type": "navigate",
            "target": "kitchen",
            "expected_duration": 10,
            "timeout": 30
        },
        {
            "step_number": 2,
            "action_type": "machine_on",
            "device": "coffee_machine",
            "expected_duration": 180,
            "timeout": 300
        },
        {
            "step_number": 3,
            "action_type": "navigate",
            "target": "living_room",
            "expected_duration": 10,
            "timeout": 30
        }
    ],
    "current_step": 0,
    "status": "running"
}

# 2. Save to SQLite
save_task(task)

# 3. Execute step by step
for i, step in enumerate(task["steps"]):
    step_started = time.time()
    execute_step(step)
    task["steps"][i]["status"] = "completed"
    task["current_step"] = i + 1
    save_task(task)  # Update SQLite
```

**[INTERNET DROPS AT STEP 2 - Coffee machine is ON, brewing]**
```python
# Task is saved with:
# - current_step = 2 (machine_on completed)
# - steps[1].started_at = T0 (3 minutes ago)
# - steps[1].expected_duration = 180s
# - STATUS = "paused_offline"
```

**[RECONNECT 2 MINUTES LATER]**
```python
# 1. Connectivity manager detects reconnection
on_reconnect():
    # 2. Load suspended task from SQLite
    task = load_task(task_id)
    
    # 3. CRITICAL: Calculate elapsed time
    time_elapsed = now() - task["steps"][1]["started_at"]  # ~120s
    time_remaining = 180 - 120  # ~60s
    
    # 4. Assess risk
    if time_elapsed > expected_duration * 0.8:  # >144s for 180s task
        task["risk_level"] = "high"
        task["risk_reason"] = "caffettiera accesa da 120s, caffe rischia di bruciare"
    
    # 5. Create resumption note
    task["resumption_note"] = f"""
    Riaperta connessione! Ero a metà del caffè.
    La macchinetta è accesa da {time_elapsed}s.
    Il caffè dovrebbe essere pronto tra ~{time_remaining}s.
    Continuo?
    """
    
    # 6. Pass to Gemini WITH risk context
    say(task["resumption_note"])
    pass_to_gemini({
        "event": "task_resumed",
        "task": task,
        "risk_level": task["risk_level"],
        "time_elapsed": time_elapsed
    })
```

### 2.3 Timeout & Escalation

```python
def check_task_timeouts():
    """Run every 30 seconds for all running tasks"""
    for task in get_running_tasks():
        current_step = task["steps"][task["current_step"]]
        
        if current_step["status"] == "running":
            elapsed = now() - current_step["started_at"]
            
            if elapsed > current_step["timeout"]:
                # TIMEOUT EXCEEDED
                task["status"] = "failed"
                task["risk_level"] = "critical"
                
                # Escalate to user
                say(f"Il task '{task['task_name']}' ha superato il timeout.")
                say(f"Dettagli: {current_step['action_type']} ha impiegato {elapsed}s")
                
                # Save failure state
                save_task(task)
```

---

## 3. MEMORY ARCHITECTURE – STRATIFIED WITH CONFLICT RESOLUTION

### 3.1 Three-Level Memory

```
┌──────────────────────────────────────────────┐
│ EPISODIC (ChromaDB)                          │
│ ├─ "2025-02-13 10:30: Marco ha bevuto caffe"│
│ ├─ "2025-02-13 15:00: Marco ha bevuto tè"   │
│ └─ "2025-02-13 18:00: Marco ha bevuto caffe"│
│ Retention: 30 days (then archive)            │
│ Retrieval: Top-5 semantic + metadata filter  │
└──────────────────────────────────────────────┘

┌──────────────────────────────────────────────┐
│ SEMANTIC (ChromaDB - Permanent)              │
│ ├─ "Marco preferisce caffè espresso"         │
│ ├─ "Marco evita caffeina dopo le 18:00"      │
│ └─ "Marco ama tapparelle chiuse al mattino"  │
│ Retention: Infinite                          │
│ Retrieval: Always 1-2 semantic per context   │
│ Confidence: 0.0-1.0, decays over time        │
└──────────────────────────────────────────────┘

┌──────────────────────────────────────────────┐
│ PROFILE (SQLite JSON)                        │
│ {                                            │
│   "name": "Marco",                           │
│   "face_id_hash": "abc123...",               │
│   "tone_preference": "informal",             │
│   "proactivity_level": 0.6,                  │
│   "daily_patterns": {...},                   │
│   "device_preferences": {...},               │
│   "last_updated": "2025-02-13"               │
│ }                                            │
│ Retention: Infinite                          │
│ Updated: Weekly by nightly compression job   │
└──────────────────────────────────────────────┘
```

### 3.2 Conflict Detection & Resolution

**Scenario: Episodic vs Semantic Conflict**
```python
# Retrieve
episodic = [
    {"timestamp": "2025-02-13 10:00", 
     "content": "Marco NON ha bevuto caffè", 
     "confidence": 0.95}
]

semantic = [
    {"created_at": "2025-02-10",
     "content": "Marco preferisce caffè espresso",
     "confidence": 0.85,
     "based_on_episodes": [15, 16, 17]}  # Links to source episodes
]

# Conflict detection
def detect_conflict(episodic, semantic):
    if semantic["created_at"] < episodic["timestamp"]:
        # Episodic is NEWER
        if CONTRADICTS(episodic, semantic):
            return {
                "conflict": True,
                "episodic_overrides": True,
                "semantic_downgrade_amount": 0.15
            }

# Apply resolution
conflict = detect_conflict(episodic[0], semantic[0])
if conflict["conflict"]:
    say("Noto che ultimamente non bevi caffè. La tendenza sta cambiando?")
    
    # Downgrade semantic confidence
    semantic[0]["confidence"] -= conflict["semantic_downgrade_amount"]
    
    # Expire narrative soon (48 hours)
    semantic[0]["validity_until"] = now() + 48*3600
    
    save_semantic(semantic[0])
```

### 3.3 Nightly Compression & Narrative Generation

**Time**: 00:30 (configurable)  
**Process**: Analyze last 24h of episodic memories, produce narratives

```python
def nightly_compression():
    """Run at 00:30 every day"""
    
    # 1. Retrieve episodic memories from last 24h
    episodes_24h = retrieve_episodic(
        time_range=[now()-86400, now()],
        limit=None
    )
    
    if len(episodes_24h) < 5:
        return  # Not enough data
    
    # 2. Ask Gemini to summarize
    narratives = gemini_call({
        "instruction": "Analizza questi episodi e crea narratives",
        "episodes": episodes_24h,
        "output_format": {
            "stories": ["narrazione della giornata"],
            "trends": [{"change": "...", "confidence": 0.8}],
            "suggestions": ["automazione da proporre"],
            "questions": ["incertezze da chiarire"]
        }
    })
    
    # 3. Save narratives with validity window
    for narrative in narratives["trends"]:
        save_semantic({
            "content": narrative["change"],
            "confidence": narrative["confidence"],
            "type": "narrative",
            "created_at": now(),
            "validity_until": now() + 14*86400,  # 2 weeks
            "source_episodes": [e["id"] for e in episodes_24h]  # Link back
        })
    
    # 4. Save suggestions for proactive interaction
    save_suggestions(narratives["suggestions"])
    
    # 5. Archive old episodic (>30 days)
    archive_old_episodic()
```

---

## 4. MULTI-USER WITH FACE RECOGNITION & UNCERTAINTY

### 4.1 Face Recognition Pipeline

```python
class FaceRecognitionEngine:
    def __init__(self):
        self.face_cache = {}  # {face_id_hash: user_profile}
        self.confidence_threshold = 0.80
        
    def recognize(self, frame):
        """
        Returns: {
            "recognized": True/False,
            "user_id": "user_1",
            "confidence": 0.92,
            "face_embedding": [...],
            "fallback_to_generic": False
        }
        """
        # 1. DeepSORT tracking (lightweight, ~20ms per frame)
        faces = deepSort.detect(frame)
        
        for face in faces:
            embedding = get_face_embedding(face)  # Lightweight model
            
            # 2. Match against known profiles
            matches = match_embedding_to_profiles(embedding)
            
            if matches:
                best_match = matches[0]
                confidence = best_match["confidence"]
                
                if confidence >= self.confidence_threshold:
                    # RECOGNIZED
                    return {
                        "recognized": True,
                        "user_id": best_match["user_id"],
                        "confidence": confidence,
                        "face_embedding": embedding,
                        "fallback_to_generic": False
                    }
                
                elif confidence >= 0.60:
                    # UNCERTAIN
                    return {
                        "recognized": False,
                        "confidence": confidence,
                        "likely_user": best_match["user_id"],  # For logging
                        "fallback_to_generic": True,
                        "ask_confirmation": True  # Ask "Chi sei?"
                    }
            
            else:
                # NOT RECOGNIZED
                return {
                    "recognized": False,
                    "confidence": 0,
                    "fallback_to_generic": True,
                    "ask_confirmation": True
                }
    
    def handle_uncertainty(self, face_result):
        """If confidence 0.60-0.80, ask user"""
        if face_result["ask_confirmation"]:
            likely = face_result.get("likely_user", "?")
            say(f"Ciao! Sei {likely}? Se no, dimmi il tuo nome.")
            
            # Listen for response
            name = listen_for_name()
            
            if name:
                # Create/update profile
                update_user_profile(name, face_result["face_embedding"])
```

### 4.2 User Profile Isolation

```python
# CRITICAL: Never pass face_id_hash to Gemini
# Use anonymous user_id instead

def build_context_for_gemini(face_result):
    if face_result["recognized"]:
        user_profile = get_user_profile(face_result["user_id"])
        
        # Map to ANONYMOUS user_id for Gemini
        gemini_context = {
            "user_id": "user_1",  # NOT the face hash
            "tone": user_profile["tone_preference"],
            "proactivity": user_profile["proactivity_level"],
            "name_to_call": user_profile["name"],  # For TTS only
            # NO face embeddings, NO face hashes
        }
    else:
        gemini_context = {
            "user_id": "guest_temporary",
            "tone": "neutral",
            "proactivity": 0.5,
            "name_to_call": "Friend",
            "note": "User not recognized, use generic profile"
        }
    
    return gemini_context
```

---

## 5. ESCALATION & DECISION POLICY

### 5.1 When to Ask vs When to Proceed

```python
def decision_policy(action, context):
    """
    Decide: ASK user confirmation, or PROCEED autonomously?
    """
    
    # Get decision confidence from Gemini (it provides this)
    confidence = context["decision_confidence"]  # 0.0-1.0
    
    # Get time pressure
    time_available = estimate_time_to_deadline(action)  # seconds
    
    # Have we seen this action type before?
    historical_success = get_success_rate(action["type"])
    
    # Decision matrix
    if action["class"] == "DANGEROUS":
        # Serrature, forni, accensioni rischiose
        return "ALWAYS_ASK"
    
    elif action["class"] == "ROUTINE":
        # Accendere luci, chiudere tapparelle
        if confidence >= 0.85:
            return "PROCEED"
        elif confidence >= 0.70 and time_available > 5:
            return "ASK"
        else:
            return "USE_DEFAULT"
    
    elif action["class"] == "PROACTIVE_SUGGESTION":
        # "Vuoi che chiuda le tapparelle?"
        if confidence >= 0.75:
            return "SUGGEST_WITH_CONFIDENCE"
        elif confidence >= 0.60:
            return "SUGGEST_WITH_UNCERTAINTY"
        else:
            return "SKIP_SUGGESTION"
    
    elif action["class"] == "MULTI_STEP":
        # Task complesso con molti step
        if confidence >= 0.80:
            return "PROCEED_WITH_FEEDBACK"
        elif confidence >= 0.65 and time_available > 10:
            return "ASK_APPROVAL"
        else:
            return "ESCALATE_TO_USER"
    
    elif action["type"] not in ["accendi_luce", "chiudi_tapparella"]:
        # Nuovo pattern non visto prima
        return "ASK" if time_available > 5 else "WARN_AND_PROCEED"
```

### 5.2 Communication of Uncertainty

```python
# WRONG: "Chiudo le tapparelle"
# RIGHT: Include uncertainty markers

# High confidence (0.9+)
"Ho chiuso le tapparelle. Il sole era davvero intenso."

# Medium confidence (0.65-0.85)
"Mi sembra opportuno chiudere le tapparelle – il sole è forte. 
Se non va bene, basta dirmi."

# Low confidence (0.5-0.65)
"Forse conviene chiudere le tapparelle? Dipende da cosa stai facendo.
Dimmi se preferisci."

# Speculative
"Se ricordo bene, di solito chiudi le tapparelle quando piove.
Vuoi che lo faccia ora?"

# Temporal scadence
"Piove ora, ti suggerisco di chiudere le tapparelle. 
Se cambia meteo te lo comunico e le riapriam se vuoi."
```

---

## 6. LATENCY BUDGET BREAKDOWN

### 6.1 Target: Decision Latency <2.5s p95

```
Component                      Latency (p95)    Notes
─────────────────────────────────────────────────────────
Input capture (ASR)            0.2-1.0s         Google Cloud STT
                                                (varies by audio length)

Local preprocessing            0.05s            Text normalization,
(text norm, intent)                             intent extraction

Memory retrieval (RAG)         0.15s            ChromaDB top-5 episodic
                                                + semantic

Context building               0.1s             JSON assembly

Gemini API call                0.8-1.5s         Network + processing
(includes caching benefit)                      (50% faster with cache)

JSON parsing                   0.05s            Response parsing

HA action execution            0.3s             MQTT to Home Assistant

TTS synthesis                  1.0-2.0s         Google Cloud TTS
(parallel to processing)                        (can overlap)

─────────────────────────────────────────────────────────
TOTAL (sequential)             ~3.0-5.0s        ❌ EXCEEDS budget!

TOTAL (optimized parallel)     ~2.0-2.5s        ✅ Meets budget
```

### 6.2 Optimization Strategies (Meeting Budget)

**Parallelization**:
```python
# DON'T: Sequential
transcription → context_build → gemini_api → ha_action → tts

# DO: Parallel
┌─ transcription (1.0s)
│
├─ RAG retrieval (0.15s) [parallel to transcription]
│
├─ context build (0.1s) [after transcription]
│
├─ Gemini API (1.2s)
│
├─ TTS synthesis (1.5s) [PARALLEL to Gemini]
│
└─ HA action (0.3s) [after Gemini]

# Timeline: ~1.5s critical path (Gemini bottleneck)
# TTS happens in background while user hears response start
```

**Caching**:
```python
# System prompt cached (50k tokens)
# → Input token cost: 90% reduction
# → Latency: ~20% reduction (less API processing)
# → Savings: ~0.2s per request
```

**Selective Retrieval**:
```python
# ONLINE: Full RAG (0.15s)
# DEGRADED: Cached RAG results (0s, use previous)
# OFFLINE: No RAG (local fallback only)
```

---

## 7. BAYESIAN FEEDBACK LOOP – Learning from Mistakes

### 7.1 Action Feedback Schema (SQLite)

```sql
CREATE TABLE action_feedback (
    action_id TEXT PRIMARY KEY,  -- Hash of (action_type, context)
    action_name TEXT,
    context_hash INTEGER,         -- SimHash of embedding
    
    -- Bayesian parameters
    alpha FLOAT DEFAULT 1.0,       -- Success count
    beta FLOAT DEFAULT 1.0,        -- Failure count
    
    -- Conflict tracking
    contradiction_count INTEGER DEFAULT 0,
    
    -- Metadata
    timestamp TIMESTAMP,
    last_updated TIMESTAMP,
    
    INDEX(action_name, context_hash)
);
```

### 7.2 Learning from User Corrections

```python
def on_user_correction(action_id, feedback):
    """
    User says "No, that's wrong!" or "Yes, perfect!"
    Update Bayesian model
    """
    
    action = get_action(action_id)
    
    if feedback == "negative":  # User corrected us
        action.beta += 1  # Increase failures
        action.contradiction_count += 1
        
        # Recalculate confidence
        new_expected = action.alpha / (action.alpha + action.beta)
        
        # Log for analysis
        log_error({
            "action_id": action_id,
            "predicted_confidence": action.old_confidence,
            "new_confidence": new_expected,
            "correction_type": feedback
        })
    
    elif feedback == "positive":  # User validated
        action.alpha += 1  # Increase successes
        action.contradiction_count = max(0, action.contradiction_count - 1)
        
        new_expected = action.alpha / (action.alpha + action.beta)
    
    # Save updated model
    save_action(action)
```

### 7.3 Adaptive Confidence

```python
# After learning, MARCUS becomes more/less confident
def get_action_confidence(action):
    """
    As you get feedback, your confidence adapts
    """
    
    base_confidence = action.alpha / (action.alpha + action.beta)
    
    # Penalize contradictions
    contradiction_penalty = 0.05 * action.contradiction_count
    
    # Apply temporal decay (old feedback matters less)
    age_days = (now() - action.timestamp).days
    decay = exp(-(age_days / 30))  # Decay over 30 days
    
    final_confidence = base_confidence * decay - contradiction_penalty
    
    return max(0, min(1, final_confidence))  # Clamp to [0, 1]
```

---

## 8. DEVICE GROUPING FOR SCALABILITY

### 8.1 Hierarchical Device Context (NOT Flat List)

```python
# DON'T: Flat list of 20+ devices
ha_context = {
    "tapparelle_salone": {...},
    "tapparelle_camera": {...},
    "light_salone": {...},
    "light_camera": {...},
    # ... 15 more ...
}  # Too verbose for Gemini

# DO: Grouped by affordance
ha_context = {
    "window_coverings": {
        "devices": ["tapparelle_salone", "tapparelle_camera", ...],
        "state": {
            "salone": 40,  # % open
            "camera": 100,
            ...
        },
        "common_actions": ["open", "close", "set_position"]
    },
    
    "lighting": {
        "devices": ["luce_salone", "luce_camera", ...],
        "state": {
            "salone": {"on": True, "brightness": 80},
            ...
        },
        "common_actions": ["on", "off", "set_brightness"]
    },
    
    "climate": {
        "thermostat": {...},
        "humidifier": {...},
        "common_actions": ["set_temperature", "set_humidity"]
    }
}

# Gemini sees structured groups, not 20 individual devices
# Context token count: ~2x smaller
```

### 8.2 Selective Context Passing

```python
def build_ha_context(user_request):
    """Only pass relevant device groups to Gemini"""
    
    intent = extract_intent(user_request)
    
    if intent in ["close_blinds", "open_blinds", "adjust_blinds"]:
        context = {"window_coverings": ha_context["window_coverings"]}
    
    elif intent in ["lights_on", "lights_off", "brightness"]:
        context = {"lighting": ha_context["lighting"]}
    
    elif intent in ["warm", "cold", "temperature"]:
        context = {"climate": ha_context["climate"]}
    
    else:
        context = ha_context  # Full context for ambiguous requests
    
    return context
```

---

## 9. CONNECTIVITY & FAILURE MODES

### 9.1 Graceful Degradation

```
Request Path:

User Input
    ↓
[ONLINE] → Full Gemini reasoning
    ↓
[DEGRADED] → Simple requests → local
           → Complex requests → queued
    ↓
[OFFLINE] → Whitelist only
           → Save task state
           → Notify user
```

### 9.2 Reconnection Recovery

```python
def on_reconnect():
    """Called when transitioning OFFLINE → DEGRADED/ONLINE"""
    
    # 1. Resume suspended tasks
    suspended = get_suspended_tasks()
    for task in suspended:
        # Calculate elapsed time
        elapsed = now() - task["last_sync"]
        
        # Check for timeouts
        if elapsed > task["timeout"]:
            task["risk_level"] = "critical"
        
        # Resume with context
        resume_task(task)
    
    # 2. Process queued requests
    process_queued_requests()
    
    # 3. Notify user
    say(f"Sono tornato online! Ho {len(suspended)} task sospesi da riprendere.")
```

---

## 10. SLO & OBSERVABILITY

### 10.1 Metrics (Prometheus-style)

```
marcus_decision_latency_seconds (histogram)
  p50, p90, p95, p99

marcus_gemini_api_latency_seconds (histogram)
  Including cache benefits

marcus_rag_retrieval_latency_ms (histogram)

marcus_error_rate (counter)
  By error type (api, ha, connectivity)

marcus_task_success_rate (gauge)
  % of tasks completed vs failed

marcus_uptime_seconds (gauge)
  Perceived uptime (not counting offline)

marcus_token_usage (counter)
  Input, output, cached tokens

marcus_memory_db_operations (histogram)
  Retrieval latency, write latency
```

### 10.2 Monitoring (Recommended Setup)

```yaml
# Prometheus scrape config
- job_name: 'marcus'
  scrape_interval: 15s
  static_configs:
    - targets: ['localhost:9090']  # ROS node exposing metrics

# Alerts (if using AlertManager)
- alert: MarcusHighLatency
  expr: marcus_decision_latency_seconds{quantile="0.95"} > 3.0
  for: 5m
  annotations:
    summary: "MARCUS decision latency exceeds 3s"

- alert: MarcusHighErrorRate
  expr: rate(marcus_error_rate[5m]) > 0.1
  for: 5m
  annotations:
    summary: "MARCUS error rate > 10%"

- alert: MarcusOfflineAlert
  expr: marcus_connectivity_state == "offline"
  for: 2m
  annotations:
    summary: "MARCUS offline for >2 minutes"
```

---

## 11. IMPLEMENTATION CHECKLIST

### Phase 1 (Week 1-2): Foundation
- [ ] State machine (ONLINE/DEGRADED/OFFLINE)
- [ ] Connectivity monitor with latency tracking
- [ ] SQLite schema for tasks & feedback
- [ ] Basic task persistence (save/load)

### Phase 2 (Week 3-4): Memory & Intelligence
- [ ] Memory retrieval (episodic + semantic)
- [ ] Conflict detection & resolution
- [ ] Nightly compression job (skeleton)
- [ ] Bayesian feedback tracking

### Phase 3 (Week 5-6): Multi-User & Safety
- [ ] Face recognition with uncertainty
- [ ] User profile isolation
- [ ] Escalation policy (ask vs proceed)
- [ ] Device grouping

### Phase 4 (Week 7-8): Optimization & Monitoring
- [ ] Latency profiling (each component)
- [ ] Prometheus metrics
- [ ] Context caching integration
- [ ] Load testing (100 requests, no memory leak)

### Phase 5 (Week 9-10): Testing & Deployment
- [ ] Unit tests (80% coverage)
- [ ] Integration tests (full flow)
- [ ] Chaos engineering (disconnect internet, etc)
- [ ] Production deployment & monitoring

---

## 12. FINAL NOTES

**This v5.1 closes all gaps from v5.0 feedback**:
- ✅ State machine fully defined
- ✅ Task persistence with timeline tracking
- ✅ Memory conflict resolution
- ✅ Escalation policy concrete
- ✅ Face recognition uncertainty handling
- ✅ Latency budget analyzed
- ✅ Learning loop Bayesian
- ✅ Device grouping for scale

**Ready for implementation**.

---

**End MARCUS ARCHITECTURE v5.1**