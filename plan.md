# TOM — Personal AI Assistant

## Master Engineering & Architecture Plan

> **Project codename:** TOM
> **Architecture principle:** Python Brain + Rust Engine + Specialized AI Models + Controlled Tools
> **Primary goal:** Build a modular, local-first, voice-enabled AI assistant capable of reasoning, using tools, understanding vision, remembering useful information, and automating computer/system tasks.

---

# 1. Vision

TOM is not intended to be a simple chatbot.

TOM should behave as a **personal AI operating layer** capable of:

* Understanding natural language
* Listening for a wake word
* Converting speech to text
* Reasoning about tasks
* Planning multi-step actions
* Calling tools
* Controlling the computer
* Inspecting system resources
* Searching and managing files
* Browsing the web
* Understanding screenshots/images
* Generating or analyzing media
* Maintaining useful long-term memory
* Recovering from failures
* Asking for confirmation before dangerous actions
* Monitoring its own performance
* Running primarily on local hardware

The system should remain modular so individual models, tools, or subsystems can be replaced without redesigning TOM.

---

# 2. Core Architecture

                              TOM
                               │
                     ┌─────────┴─────────┐
                     │                   │
                 Python                 Rust
                  BRAIN                ENGINE
                     │                   │
                     │          ┌────────┼────────┐
                     │          │        │        │
                     │        Audio    System    IPC
                     │        Wake     Monitor  Events
                     │        Word     Hotkeys
                     │
       ┌─────────────┼───────────────────────────────┐
       │             │               │               │
       ▼             ▼               ▼               ▼
    Router          LLM           Memory           Tools
       │          Qwen3 8B      SQLite/Vector       │
       │             │                               │
       │             │                         ┌─────┼─────┐
       │             │                         │     │     │
       ▼             ▼                         ▼     ▼     ▼
   Intent/Model    Reasoning                 System Files Web
     Routing       Planning
                     │
                     ▼
                   Vision
                     │
                  VLM/CV


---

# 3. Fundamental Design Principles

## 3.1 TOM must not equal the LLM

The LLM is one component of TOM.

TOM CORE
│
├── Planner
├── Router
├── Memory
├── Tool Manager
├── Permission Manager
├── Model Manager
├── Context Manager
├── Error Recovery
├── Observability
└── LLM


The LLM provides reasoning and language intelligence.

The surrounding architecture provides:

* State
* Memory
* Tools
* Security
* Planning
* Execution
* Validation
* Recovery
* Scheduling

---

# 4. Python vs Rust Responsibilities

## 4.1 Python — TOM Brain

Python owns high-level intelligence and orchestration.

Python
│
├── TOM Orchestrator
├── Intent Router
├── Model Router
├── Planner
├── LLM providers
├── Memory
├── Tool registry
├── Tool orchestration
├── Web
├── APIs
├── Vision orchestration
├── Agent workflows
├── Permission decisions
├── Error recovery
├── Configuration
└── Telemetry

Python should answer:

> "What should TOM do?"

---

# 5. Rust — tom-engine

Rust is TOM's **nervous system / always-on engine**.

tom-engine
│
├── Audio processing
├── Wake-word detection
├── Microphone management
├── System monitoring
├── CPU monitoring
├── GPU monitoring
├── RAM monitoring
├── Battery monitoring
├── Process monitoring
├── Network monitoring
├── Hotkeys
├── Keyboard primitives
├── Mouse primitives
├── Low-level computer utilities
├── Event handling
├── IPC server
└── High-speed utilities

Rust should answer:

> "What is happening on the machine, and what low-level operation should be performed?"

Rust should remain lightweight and capable of running continuously.

---

# 6. Python ↔ Rust Communication

Initial implementation:

Python TOM
    │
    │ JSON messages
    │
    ▼
Local IPC
    │
    ▼
Rust tom-engine

Start with the simplest reliable mechanism.

Possible implementations:

1. Unix sockets
2. Named pipes
3. Local TCP
4. WebSocket
5. gRPC

### Initial recommendation

Use a simple local JSON-based IPC protocol.

Do not introduce gRPC or complex distributed infrastructure during MVP.

Example request:

```json
{
  "id": "req_001",
  "method": "system.gpu_temperature",
  "params": {}
}
```

Example response:

```json
{
  "id": "req_001",
  "success": true,
  "data": {
    "temperature_c": 61
  }
}
```

The IPC protocol should eventually be versioned.

---

# 7. TOM Request Pipeline

A normal request should follow:

User
 │
 ▼
Wake Word / Text Input
 │
 ▼
STT
 │
 ▼
TOM Orchestrator
 │
 ▼
Intent Router
 │
 ├───────────────┐
 │               │
 ▼               ▼
Simple         Complex
 │               │
 ▼               ▼
Rust           Planner
Tool              │
 │                ▼
 │              LLM
 │                │
 └────────┬───────┘
          ▼
      Tool Manager
          │
          ▼
      Tool Result
          │
          ▼
      Validation
          │
          ▼
        LLM
          │
          ▼
         TTS
          │
          ▼
        User

---

# 8. Intent Routing

Intent routing determines **what kind of task the user is requesting**.

Example:

```text
"What is my GPU temperature?"
             │
             ▼
          SYSTEM
             │
             ▼
       Rust tool
```

Example:

```text
"Explain why my GPU is running hot."
             │
             ▼
          AGENT
             │
             ▼
       Rust telemetry
             │
             ▼
        Qwen3 8B
```

Example:

```text
"What is wrong with this screenshot?"
             │
             ▼
          VISION
             │
             ▼
           VLM
```

Intent categories should initially include:

```text
SYSTEM
COMPUTER
FILES
WEB
VISION
AI
PERSONAL
CHAT
AGENT
UNKNOWN
```

---

# 9. Cancellation / Interruption

TOM must support cancellation and user interruption as a first-class capability.

A user should be able to interrupt an active operation with commands such as:

* "Stop"
* "Cancel"
* "Never mind"
* "Stop talking"
* "Cancel that task"

Cancellation must not simply stop audio playback. It should propagate through the active task and stop or safely terminate work that is still running.

### Cancellation Architecture

```text
User
 ↓
Wake Word / Voice / Input
 ↓
Cancellation Detector
 ↓
Task Manager
 ↓
Cancellation Signal
 │
 ├── LLM generation
 ├── TTS generation
 ├── Tool execution
 ├── Agent workflow
 ├── Web request
 ├── Vision processing
 └── Background task
```

Cancellation should use a shared task/request cancellation mechanism rather than independent stop flags inside every subsystem.

### Cancellation Rules

Every long-running or interruptible operation should receive a cancellation context/token.

Examples:

```text
LLM request
Tool execution
Web request
Agent loop
Vision analysis
STT processing
TTS generation
Background indexing
Model loading
```

Operations should periodically check whether cancellation has been requested.

When cancellation occurs:

```text
RUNNING
   ↓
CANCELLATION_REQUESTED
   ↓
CLEANUP
   ↓
CANCELLED
```

Cancellation should be cooperative wherever possible.

TOM must not forcibly terminate processes or threads unless a safe hard-stop mechanism is explicitly supported.

### Voice Interruption

Voice interaction should support:

```text
TOM speaking
     ↓
User says "stop"
     ↓
Wake word / interruption detection
     ↓
Cancel current response
     ↓
Stop TTS playback
     ↓
Cancel remaining generation where possible
     ↓
Return TOM to listening/idle state
```

The goal is to make TOM feel interruptible rather than waiting for the current response to finish.

### Cancellation and Tools

Tool cancellation depends on the tool.

Safe-to-cancel operations:

* Web requests
* Search
* AI generation
* File indexing
* Long-running analysis

Operations that may require completion or rollback:

* File moves
* File copies
* Database transactions
* System configuration changes
* Other state-changing operations

TOM must not assume that every tool can be safely interrupted halfway through execution.

Tools should therefore declare whether they support cancellation.

Example:

```text
Tool
├── cancellable: true
├── timeout
└── risk level
```

### Cancellation and Agents

If an agent task is cancelled:

```text
Planner
 ↓
Step 1 ✓
 ↓
Step 2 ✓
 ↓
Step 3 → cancellation requested
 ↓
Stop further steps
 ↓
Cleanup
 ↓
Task = CANCELLED
```

The agent must not continue executing additional planned tools after cancellation.

### Cancellation and Background Work

Background operations must have lower priority than interactive user tasks.

When resources become constrained or the user requests an interactive operation:

```text
Background Task
      ↓
Preempt / Pause / Yield
      ↓
Interactive Task
      ↓
Complete
      ↓
Resume Background Task
```

Not every background task needs to be preempted immediately. The Resource & Model Lifecycle Manager determines whether a task should pause, yield, continue, or be cancelled.

### Cancellation Safety

Cancellation must:

* Stop further tool calls
* Stop further agent steps
* Stop TTS playback
* Cancel model generation where supported
* Cancel network requests where supported
* Release temporary resources
* Release model/resource reservations when appropriate
* Preserve useful telemetry
* Leave persistent data in a valid state
* Never bypass the Permission Layer
* Never leave TOM believing a cancelled task completed successfully

### Important Rule

> **Cancellation stops future work; it does not automatically undo work that has already happened.**

If an operation has already changed persistent state, the relevant subsystem must determine whether rollback is possible and safe.

Cancellation events should be observable through TOM's event system:

```text
CancellationRequested
TaskCancelled
OperationCancelled
```

The cancellation subsystem must integrate with:

* Orchestrator
* Task Manager
* Agent system
* Tool Executor
* Model providers
* Voice pipeline
* Vision pipeline
* Web system
* Resource & Model Lifecycle Manager
* Telemetry
* Internal event system

# 10. Task State

TOM should maintain an explicit lifecycle state for every user task.

A task is not simply "running" or "finished".

Task state allows TOM to understand:

* What is currently happening
* What step is being executed
* Whether TOM is waiting
* Whether confirmation is required
* Whether the task failed
* Whether the task was cancelled
* Whether the task completed successfully

### Task Lifecycle

```text
CREATED
   ↓
ROUTING
   ↓
PLANNING
   ↓
EXECUTING
   ↓
WAITING
   ↓
COMPLETING
   ↓
COMPLETED
```

Failure path:

```text
Any active state
      ↓
    FAILED
```

Cancellation path:

```text
Any cancellable active state
      ↓
CANCELLATION_REQUESTED
      ↓
    CANCELLED
```

Permission path:

```text
EXECUTING
    ↓
WAITING_FOR_CONFIRMATION
    ↓
Permission granted → EXECUTING
Permission denied  → CANCELLED / FAILED
```

### Core Task States

```text
CREATED
ROUTING
PLANNING
EXECUTING
WAITING
WAITING_FOR_CONFIRMATION
COMPLETING
COMPLETED
FAILED
CANCELLATION_REQUESTED
CANCELLED
```

### Task Context

Each task should have a unique task ID.

Conceptually:

```text
Task
├── task_id
├── parent_task_id
├── status
├── created_at
├── updated_at
├── current_step
├── request
├── route
├── progress
├── cancellation_state
├── waiting_reason
├── error
└── metadata
```

`parent_task_id` allows TOM to represent tasks created by another task or agent workflow.

### Task State Ownership

The Orchestrator / Task Manager owns task lifecycle state.

Individual subsystems report events and results but should not independently redefine the global task state.

For example:

```text
Tool Executor
      ↓
ToolCompleted event
      ↓
Task Manager
      ↓
Task state updated
```

### Task State and Events

Task state changes should generate typed events.

Examples:

```text
TaskCreated
TaskRoutingStarted
TaskRoutingCompleted
TaskPlanningStarted
TaskExecutionStarted
TaskWaiting
ConfirmationRequested
ConfirmationGranted
ConfirmationDenied
TaskCompleting
TaskCompleted
TaskFailed
CancellationRequested
TaskCancelled
```

This allows telemetry, UI, debugging, and other subsystems to observe task progress without directly modifying task state.

### Task State and Recovery

Task state must work together with the Error Recovery system.

Example:

```text
EXECUTING
    ↓
ToolFailed
    ↓
Recovery
 ├── Retry
 ├── Alternative Tool
 ├── Re-plan
 ├── Ask User
 └── Abort
```

If recovery succeeds:

```text
Recovery
   ↓
EXECUTING
```

If recovery cannot continue:

```text
Recovery
   ↓
FAILED
```

### Important Rule

> **Every active TOM task must have an explicit state, and every state transition must be observable and intentional.**

TOM should never silently move from an active task to a completed state without a validated result.


# 11. Model Routing

Intent routing and model routing are different.

### Intent routing

Determines:

> What should TOM do?

### Model routing

Determines:

> Which model should perform the AI portion?

Architecture:

```text
                    TOM Router
                        │
        ┌───────────────┼────────────────┐
        │               │                │
        ▼               ▼                ▼
      TOOLS          REASONING         VISION
        │               │                │
        ▼               ▼                ▼
      Rust          Qwen3 8B           VLM
```

---

# 12. Tiny Router

### Target

**Qwen3 1.7B**

The tiny model acts as TOM's **global intent and routing layer**.

It should primarily perform:

* Intent classification
* High-level route selection
* Simple task classification
* Simple tool-category selection
* Structured JSON generation
* Very simple commands

The router should **not** be responsible for complex reasoning, planning, vision-model selection, memory policy, permission decisions, or arbitrary tool execution.

Its job is to answer:

> **"What kind of capability does this request require?"**

The router does not need to be the smartest model in TOM.

### Example: System Request

```json
{
  "route": "system",
  "task": "get_cpu_temperature"
}
```

The Tool Registry / Tool Manager then determines the exact implementation.

### Example: Reasoning Request

```json
{
  "route": "reasoning",
  "task": "explain_system_status"
}
```

The request is passed to the main reasoning model:

```text
Qwen3 1.7B
      ↓
route = reasoning
      ↓
Qwen3 8B
      ↓
Planner / Agent / Tools
```

### Example: Vision Request

```json
{
  "route": "vision",
  "task": "analyze_screenshot"
}
```

The request is passed to the Vision Manager:

```text
Qwen3 1.7B
      ↓
route = vision
      ↓
Vision Manager
      ├── Fast → Ministral 3 3B
      ├── Primary → Gemma 4 E4B
      └── Deep → Ministral 3 8B
```

The tiny router **does not choose the VLM directly**.

### Router Responsibilities

The router may determine:

* `system`
* `computer`
* `files`
* `web`
* `vision`
* `ai`
* `personal`
* `memory`
* `reasoning`
* `agent`
* `chat`
* `unknown`

It may also identify simple operations when useful, but the final tool resolution remains under TOM's Tool Registry / Tool Manager.

### Router Design Principles

The router should be optimized for:

* Low latency
* High classification accuracy
* Reliable structured output
* Deterministic behavior
* Low resource usage
* Minimal context requirements
* Fast failure handling

The router should remain **narrow and predictable**.

It should not become a miniature general-purpose agent.

### Important Architectural Rule

> **Qwen3 1.7B decides the high-level route. TOM's specialized subsystems decide what happens next.**

Therefore:

```text
Tiny Router
     ↓
High-level route
     ↓
Specialized subsystem
     ↓
Exact tool / model / workflow
```

This keeps the router lightweight while allowing TOM's capabilities to grow without continuously retraining or redesigning the router.


# 13. Main Reasoning Model

## Initial model

**Qwen3 8B**

Initial target:

```text
Qwen3 8B
5-bit quantization
```

The model should handle:

* Reasoning
* Planning
* Conversation
* Coding
* Tool selection
* Tool-call interpretation
* Structured output
* Agent workflows
* Instruction following
* Complex analysis

The model must remain replaceable.

Do not hard-code TOM around Qwen.

Use a model provider abstraction:

```text
LLMProvider
│
├── QwenProvider
├── GemmaProvider
├── MistralProvider
└── CloudProvider
```

Model configuration should live outside application logic.

Example:

```yaml
models:

  router:
    provider: local
    model: router-model

  reasoning:
    provider: local
    model: qwen3-8b
    quantization: q5

  vision:
    provider: local
    model: vision-model

  cloud:
    provider: cloud
    model: optional
```

## Local Model Runtime & Experimentation

TOM may use **LM Studio (Bionic)** during development and experimentation.

LM Studio/Bionic is an **experimentation and development runtime**, not a permanent architectural dependency.

TOM's model layer must remain decoupled from the inference runtime.

Keep these concepts explicitly separate:

* **Model selection**: Choosing the model identity for a task.
* **Model configuration**: Declaring context length, temperature, and quantization parameters.
* **Inference runtime**: The underlying engine serving model weights.
* **Resource management**: Allocating VRAM, RAM, and scheduling concurrency.

TOM should benchmark candidate models and runtimes using practical measurements:

* Model load/switch time
* TTFT (Time to First Token)
* Tokens/sec (generation throughput)
* CPU usage
* RAM usage
* VRAM usage
* GPU utilization
* Context/KV-cache memory impact
* Concurrent model behavior
* Model switching overhead
* End-to-end perceived latency

The final inference runtime must be selected later based on measured performance, resource usage, compatibility, and deployment requirements.

---

# 14. Model Evaluation

Do not choose models only from public benchmark scores.

Build a **TOM Benchmark Suite**.

Measure:

## LLM

1. TTFT
2. Tokens/sec
3. Context length
4. VRAM usage
5. RAM usage
6. Model loading time
7. CPU usage
8. GPU usage
9. Reasoning quality
10. Instruction following
11. Tool calling
12. Function calling
13. Structured JSON reliability
14. Coding ability
15. Hallucination rate
16. Multilingual performance
17. Agent task success rate

A model with slightly weaker reasoning but significantly better tool calling may be better for TOM.

---

# 15. Vision System

Vision should be separated into:

```text
Fast perception
       +
Deep visual reasoning
```

Architecture:

Camera / Screenshot / Image
            │
            ▼
      Fast CV Layer
            │
      ┌─────┴─────┐
      │           │
   Simple       Complex
      │           │
      ▼           ▼
   OpenCV       Vision Manager
   YOLO              │
   SAM        ┌──────┼──────┐
              ▼      ▼      ▼
             Fast  Primary  Deep
              │      │       │
             M3 3B Gemma 4  M3 8B

---

# 16. VLM Candidates

Initial candidates:

### Local

* Gemma E4B - 4 or 5 bit quantization based on best result choose (Primary VLM)
* Ministral 3 8B - 4 or 5 bit quantization based on best result choose (High-quality VLM)
* Ministral 3 3B - 5 or 6 bit quantization based on best result choose (Fast/light VLM)

### Cloud 

* Llama 4 Scout
* Other cloud multimodal models

- TOM should always assk before switiching to cloud option

---

# 17. VLM Evaluation

Measure:

1. Image resolution
2. Visual token usage
3. OCR accuracy
4. Screenshot understanding
5. Small-text recognition
6. Object recognition
7. UI understanding
8. Spatial reasoning
9. Visual reasoning
10. Latency
11. VRAM usage
12. RAM usage

Especially benchmark:

```text
VS Code screenshots
Terminal screenshots
Browser screenshots
Error messages
Diagrams
Documents
Photos
System UI
```

---

# 18. Fast Computer Vision

Use traditional/efficient CV where possible.

Candidates:

```text
OpenCV
YOLO
SAM
```

The VLM should not process every frame.

Preferred architecture:

```text
Camera
  │
  ▼
Fast CV
  │
  ├── Nothing important → ignore
  │
  └── Something detected
              │
              ▼
             VLM
              │
              ▼
          TOM reasoning
```

This reduces:

* GPU usage
* latency
* power consumption
* unnecessary model calls

---

# 19. Voice Input

Pipeline:

```text
Microphone
    │
    ▼
Wake-word detector
    │
    │ "TOM"
    ▼
Speech capture
    │
    ▼
Whisper-family STT
    │
    ▼
Text
    │
    ▼
TOM
```

---

# 20. Wake Word

Wake word:

```text
"TOM"
```

Wake-word detection should run independently from STT.

The wake-word system should remain lightweight and preferably live inside `tom-engine`.

Goals:

* Low CPU usage
* Low false-positive rate
* Low false-negative rate
* Low latency
* Offline operation

Target:

```text
Wake detection <100 ms
```

---

# 21. Speech-to-Text

Initial family:

**Whisper large-v3-turbo**

in the intial stage give user the option to test the model in pure CPU or with GPU so i can tune it according to the performance in the later iteration

Evaluate:

1. WER
2. Latency
3. CPU usage
4. GPU usage
5. Noise handling
6. Accent handling
7. Multilingual support
8. Streaming support
9. Model memory usage

---

# 22. Voice Output

Architecture:

TTS: Kokoro 82M - run on the CPU by keeping it in the RAM
├── FP16
└── INT8

Measure:

* Time to first audio
* Total generation time
* Voice quality
* Naturalness
* CPU/GPU usage
* Streaming capability

Target:

```text
TTS start <300 ms
```

where practical.

---

# . Memory Architecture

Memory should not mean:

> "Store everything forever."

TOM needs a **controlled, structured, searchable, and user-governed memory system**.

Memory is a subsystem owned by TOM, not by an individual LLM.

```text
Memory
│
├── Short-term conversation
│
├── Long-term memory
│
├── Semantic memory
│
├── User preferences
│
├── Task history
│
└── Important events
```

### 21.1 Memory Stack

TOM will use a hybrid memory architecture:

```text
                    TOM Memory Manager
                           │
                 ┌─────────┴─────────┐
                 │                   │
              SQLite               Qdrant
                 │                   │
        Structured storage     Semantic retrieval
        Source of truth        Embeddings / vectors
                 │                   │
                 └─────────┬─────────┘
                           │
                    Memory Context
                           │
                    Context Builder
                           │
                        LLM / Agent
```

### 22.1 SQLite — Source of Truth

SQLite will store the authoritative representation of TOM's memories.

Examples:

```text
Memory
├── memory_id
├── memory_type
├── content
├── created_at
├── updated_at
├── importance
├── source
├── user_confirmed
├── expires_at
└── metadata
```

SQLite will be responsible for:

* Structured memories
* User preferences
* Important facts
* Task history
* Memory metadata
* Timestamps
* Memory lifecycle
* User-approved memories
* Deletion and modification
* Memory auditing

SQLite remains the **source of truth** even when Qdrant is used for semantic retrieval.

### 22.2 Qdrant — Semantic Memory

Qdrant will provide semantic/vector search over memories.

```text
Memory
   │
   ▼
Embedding
   │
   ▼
Qdrant
   │
   ▼
Semantic similarity search
```

Qdrant allows TOM to retrieve memories based on meaning rather than exact keyword matches.

For example:

> "What was that image enhancement project I worked on?"

can retrieve memories referring to an image upscaler even if the user's current wording does not exactly match the original memory.

Qdrant stores the semantic index and references the corresponding SQLite memory using a stable `memory_id`.

```text
SQLite
memory_id = 1842
      │
      │
      └──────────────► Qdrant
                       vector
                       memory_id = 1842
```

Qdrant is therefore a **retrieval layer**, not the authoritative memory store.

### 22.3 Memory Manager

TOM will expose a dedicated `Memory Manager` between the AI agents and the databases.

```text
LLM / Agent
     │
     ▼
Memory Manager
     │
 ┌───┴────┐
 ▼        ▼
SQLite   Qdrant
```

The Memory Manager is responsible for:

* Storing memories
* Retrieving memories
* Updating memories
* Deleting memories
* Creating embeddings
* Searching semantic memory
* Filtering memories
* Applying memory policies
* Resolving Qdrant results back to SQLite records
* Preventing unauthorized memory access
* Controlling what information reaches an LLM

LLMs should **never receive unrestricted database access**.

### 22.4 Pydantic AI Integration

Pydantic AI will provide the typed and validated interface between TOM's AI agents and memory operations.

Memory operations should use explicit schemas rather than arbitrary database queries.

Conceptually:

```text
Pydantic AI Agent
       │
       ▼
Typed Memory Tool
       │
       ▼
Memory Manager
       │
 ┌─────┴─────┐
 ▼           ▼
SQLite     Qdrant
```

Example operations:

```text
memory.search
memory.get
memory.store
memory.update
memory.forget
memory.recent
memory.preferences
```

All tool inputs and outputs should be validated using Pydantic models.

This ensures that LLM-generated tool calls cannot directly construct arbitrary SQL queries or uncontrolled database operations.

### 22.5 Qwen3-1.7B Memory Access

The Qwen3-1.7B model acts as TOM's **global router**.

It should have controlled access to memory through the Memory Manager.

It should **not** directly access SQLite or Qdrant.

                         Qwen3-1.7B
                         Global Router
                              │
                       "What kind of task?"
                              │
          ┌───────────┬───────┼────────┬───────────┐
          │           │       │        │           │
          ▼           ▼       ▼        ▼           ▼
       SYSTEM      REASONING VISION   FILES       WEB
          │           │       │
          │           ▼       ▼
          │       Qwen3-8B  Vision Manager
          │           │       │
          │           │       ├── Ministral 3 3B
          │           │       ├── Gemma 4 E4B
          │           │       └── Ministral 3 8B
          │           │
          │           ▼
          │       Memory Manager
          │          │      │
          │          ▼      ▼
          │       SQLite   Qdrant
          │
          ▼
      Tool Manager

For example:

```text
User:
"What TTS engine did I decide to use for TOM?"
```

The router can identify this as a memory-dependent request:

```json
{
  "route": "memory",
  "operation": "search",
  "query": "TTS engine selected for TOM"
}
```

Pydantic validates the request.

The Memory Manager performs the search.

Relevant memories are returned to TOM.

The router or main reasoning model can then use those memories to produce the final response.

### 22.7 Memory Retrieval Flow

```text
User
 │
 ▼
Qwen3-1.7B
 │
 ├── No memory required ───────────────► Normal routing
 │
 └── Memory required
          │
          ▼
   Memory Tool Request
          │
          ▼
   Pydantic Validation
          │
          ▼
    Memory Manager
          │
     ┌────┴────┐
     ▼         ▼
  SQLite    Qdrant
     │         │
     └────┬────┘
          ▼
   Relevant Memories
          │
          ▼
    Context Builder
          │
          ▼
     Qwen3-8B / VLM
```

Only **relevant memory** should be inserted into the model context.

TOM should never dump the entire memory database into an LLM prompt.

### 22.8 Memory Creation Flow

TOM should also be capable of identifying information that may be useful to remember.

```text
User
 │
 ▼
Qwen3-1.7B / Qwen3-8B
 │
 ▼
Memory Candidate
 │
 ▼
Memory Policy
 │
 ├── Reject
 │
 ├── Temporary memory
 │
 └── Long-term memory
          │
          ▼
       SQLite
          │
          ▼
      Embedding
          │
          ▼
       Qdrant
```

Memory creation should therefore be a **policy-controlled operation**, not an automatic "save everything" mechanism.

---

# 23. Memory Policy

TOM must explicitly define:

> **What should be remembered, for how long, and under what conditions?**

Memory should be useful rather than exhaustive.

### 23.1 What Should Be Remembered?

Potential long-term memories include:

* Stable user preferences
* Frequently used settings
* Important project context
* Repeated workflows
* User-approved facts
* Useful task history
* Important events
* Persistent configuration preferences
* Recurring interaction patterns that provide genuine value

Examples:

```text
"User prefers Kokoro for local TTS."

"User's TOM project uses Rust for low-level system operations."

"User prefers local models before cloud fallbacks."
```

These memories can make TOM's responses more consistent and natural across conversations.

### 23.2 What Should NOT Be Remembered?

TOM should generally avoid storing:

* Temporary conversation noise
* One-time commands
* Unimportant small talk
* Secrets
* Passwords
* API keys
* Authentication tokens
* Credentials
* Unnecessary private information
* Information that has no foreseeable future value

Sensitive information should never be stored simply because an LLM encountered it.

### 23.3 User-Controlled Memory

Memory must ultimately belong to the user.

TOM should eventually support commands such as:

> "What do you remember about me?"

> "Remember that I prefer Kokoro for TTS."

> "Forget that."

> "Forget everything about my image upscaler project."

> "Show me what you have stored about my preferences."

Memory should be:

```text
Memory
├── Inspectable
├── Editable
├── Deletable
├── Searchable
└── User-controlled
```

### 23.4 Memory Types

TOM should distinguish between different memory lifetimes.

```text
Memory
│
├── Ephemeral
│   └── Current interaction
│
├── Short-term
│   └── Recent conversation context
│
├── Long-term
│   └── Persistent useful information
│
├── Preference
│   └── Stable user choices
│
├── Task
│   └── Historical task information
│
└── Event
    └── Important occurrences
```

Different memory types may have different retention and retrieval policies.

### 23.5 Memory Importance

Memories should have an importance score or classification.

```text
LOW
 │
 ├── Temporary
 │
 ├── Useful
 │
 ├── Important
 │
 └── Critical
```

This allows TOM to prioritize valuable memories and prevent the memory database from becoming unnecessarily large.

### 23.6 Memory Expiration

Not every memory should live forever.

Temporary memories may have an expiration time:

```text
memory
├── created_at
├── updated_at
└── expires_at
```

Expired memories can be automatically removed or archived according to TOM's memory policy.

### 23.7 Memory Safety Boundary

The following rule is mandatory:

> **LLMs may request memory operations, but they do not own the memory system.**

```text
Qwen3-1.7B
     │
     ▼
Typed Tool Request
     │
     ▼
Pydantic Validation
     │
     ▼
Permission / Memory Policy
     │
     ▼
Memory Manager
     │
 ┌───┴────┐
 ▼        ▼
SQLite   Qdrant
```

This prevents an LLM from:

* Executing arbitrary SQL
* Searching unrestricted private data
* Deleting memories without authorization
* Storing secrets
* Modifying memory structures
* Bypassing TOM's security policies

### 23.8 Core Principle

TOM's memory should make the assistant feel **continuous and context-aware**, not invasive.

The objective is not:

> "TOM remembers everything."

The objective is:

> **"TOM remembers the things that are useful, forgets the things that aren't, and gives the user complete control over what it remembers."**


# 24. Tool Architecture

Tools must be explicit and controlled.

```text
Tool Registry
│
├── System
├── Computer
├── Files
├── Web
├── AI
└── Personal
```

Every tool should expose:

```text
name
description
input schema
output schema
permission level
timeout
risk level
```

---

# 24. SYSTEM Tools

```text
SYSTEM
│
├── CPU information
├── CPU temperature
├── RAM information
├── GPU information
├── GPU temperature
├── GPU utilization
├── Battery
├── Processes
├── Disk usage
└── Network
```

Most low-level system tools should be implemented through Rust.

---

# 25. COMPUTER Tools

```text
COMPUTER
│
├── Open application
├── Close application
├── Focus application
├── Screenshot
├── Keyboard
├── Mouse
└── Window management
```

Computer-control operations should be carefully permissioned.

---

# 26. FILE Tools

```text
FILES
│
├── Search
├── Read
├── Create
├── Rename
├── Move
├── Copy
├── Delete
└── Organize
```

File operations require strong permission boundaries.

Destructive operations should require confirmation.

---

# 27. WEB Tools

```text
WEB
│
├── Search
├── Browse
├── Retrieve
└── Extract
```

The web layer should be independent of the LLM.

The LLM decides what information it needs.

The web tool performs the actual retrieval.

---

# 28. AI Tools

```text
AI
│
├── Image generation
├── Image analysis
└── Transcription
```

These should be exposed as tools rather than hard-coded into the reasoning model.

---

# 29. PERSONAL Tools

```text
PERSONAL
│
├── Reminders
├── Calendar
├── Notes
└── Personal workflows
```

These should eventually integrate with external services through controlled APIs.

---

# 30. Tool Calling Architecture

The LLM should never directly execute arbitrary system commands.

Bad:

```python
os.system(llm_generated_command)
```

Never use this architecture.

Preferred:

```text
LLM
 │
 ▼
Structured tool request
 │
 ▼
Tool Registry
 │
 ▼
Permission Layer
 │
 ▼
Tool Executor
 │
 ▼
Result validation
 │
 ▼
LLM
```

Example:

```json
{
  "tool": "system.gpu_temperature",
  "arguments": {}
}
```

---

# 31. Permission & Security Layer

Every tool receives a risk classification.

```text
Permission Layer
       │
 ┌─────┼──────┐
 ▼     ▼      ▼
SAFE  ASK    BLOCK
```

### SAFE

Examples:

* CPU information
* GPU temperature
* Battery status
* Current time

### ASK USER

Examples:

* Delete file
* Send message
* Install software
* Close important application
* Modify system settings

### BLOCK

Examples:

* Dangerous unrestricted operations
* Access to protected secrets
* Arbitrary destructive commands

---

# 32. Confirmation Model

For risky operations:

```text
TOM:
"I found 3 duplicate files.
Would you like me to delete them?"
```

Only after confirmation:

```text
User → Yes
      ↓
Permission Layer
      ↓
Tool
```

Confirmation should be explicit.

---

# 33. Secrets

Never expose secrets to the LLM unnecessarily.

Secrets include:

* API keys
* Passwords
* Tokens
* SSH credentials
* Authentication cookies
* Private keys

Use:

```text
Environment variables
+
Secure secret storage
```

The model should receive:

```text
"authenticated service available"
```

rather than:

```text
"API key = XXXXX"
```

---

# 34. Agent Planning

Complex tasks should use a planner.

```text
User request
     │
     ▼
Planner
     │
     ▼
Plan
     │
 ┌───┼─────────┐
 ▼   ▼         ▼
Tool Tool      Tool
 1    2         3
 │    │         │
 └────┴─────────┘
       │
       ▼
    Validate
       │
       ▼
    Complete
```

Example:

> "Find why my laptop is slow."

Possible plan:

```text
1. Check CPU usage
2. Check RAM usage
3. Check GPU usage
4. Check running processes
5. Check disk usage
6. Analyze results
7. Recommend action
```

---

# 35. Agent Reliability

Every tool call should be validated.

```text
Tool call
   │
   ▼
Success?
 ┌─┴─┐
Yes  No
 │    │
 ▼    ▼
Next Error Handler
       │
       ├── Retry
       ├── Alternative tool
       ├── Re-plan
       ├── Ask user
       └── Abort
```

Failures should never silently disappear.

---

# 36. Timeouts

Every external or potentially blocking operation should have a timeout.

Examples:

```text
LLM request
Web request
Tool execution
IPC request
STT
TTS
File operation
External API
```

No tool should be able to hang TOM indefinitely.

---

# 37. Concurrency & Resource Scheduling

The development laptop has limited shared resources:

```text
RTX 4060 Laptop
8 GB VRAM

16 GB RAM
Intel i9-13900H
```

Therefore TOM must explicitly understand **resource contention, model residency, concurrency, and workload priority**.

Not every model should run on the GPU.

TOM should classify workloads according to their resource requirements.

### 37.1 Resource Classes

```text
CPU / RAM workloads
├── Rust Engine
├── Wake Word
├── VAD
├── Whisper Large-v3-Turbo
├── Kokoro-82M
├── SQLite
├── Qdrant
└── TOM orchestration

GPU workloads
├── Qwen3-1.7B Router
├── Qwen3-8B Reasoning
├── Vision Models
├── Image Generation
└── GPU-accelerated Computer Vision
```

Whisper's final CPU/GPU placement should be determined through benchmarking during the initial development phase rather than assumed in advance.

Kokoro should normally remain CPU-based because its small model size does not justify consuming scarce GPU VRAM.

### 37.2 GPU Contention

Potential GPU workloads include:

```text
Qwen3-1.7B
Qwen3-8B
Vision Models
Whisper
Image Generation
Computer Vision
```

TOM must **not assume that all GPU workloads can safely execute simultaneously**.

The 8 GB VRAM budget is particularly important because multiple loaded models can exceed available VRAM even when individual models fit comfortably.

The Resource Manager must therefore consider:

* Current VRAM usage
* Model memory requirements
* Model quantization
* Context/KV-cache memory
* Current GPU workload
* CPU/RAM availability
* Model loading/unloading cost
* Inference priority
* Latency requirements
* Whether concurrent inference is safe
* Thermal and power conditions

### 37.3 Concurrency Policy

TOM should distinguish between:

```text
Parallel
    → Workloads can safely run simultaneously.

Queued
    → Workload waits for a resource.

Preemptible
    → Background workload can be paused or delayed.

Exclusive
    → Workload requires temporary exclusive GPU access.
```

For example:

```text
User asks TOM a question
        │
        ▼
Qwen3-1.7B Router
        │
        ▼
Qwen3-8B inference
        │
        ├── Background image generation?
        │       ↓
        │    QUEUED
        │
        └── VLM request?
                ↓
             QUEUED /
             SWITCHED
```

Interactive user requests should normally have higher priority than background AI workloads.

### 37.4 Resource Priority

Initial priority should be approximately:

```text
1. Voice interaction
2. User's active request
3. Router
4. Main reasoning
5. Vision
6. Computer vision
7. Background AI tasks
8. Maintenance / indexing
```

The exact priority system should remain configurable.

### 37.5 Model Residency

Models should have explicit lifecycle states:

```text
UNLOADED
    ↓
LOADING
    ↓
READY
    ↓
BUSY
    ↓
IDLE
    ↓
UNLOADING
    ↓
UNLOADED
```

The Resource Manager should determine which models remain resident.

For example:

```text
GPU
├── Qwen3-1.7B → normally resident
│
└── One major model
    ├── Qwen3-8B
    └── OR VLM
```

TOM should avoid permanently keeping every large model loaded.

### 37.6 CPU Fallback

Models and workloads should define whether CPU execution is supported.

Example:

```text
Whisper
├── Preferred: CPU
└── Fallback: GPU

Kokoro
└── CPU

Qwen3-8B
└── GPU preferred

VLM
└── GPU preferred
```

The Resource Manager may select a fallback when the preferred resource is unavailable.

---

# 38. Resource & Model Lifecycle Manager

TOM should eventually contain a centralized **Resource Manager / Model Scheduler** responsible for coordinating CPU, RAM, VRAM, GPU workloads, model residency, and concurrency.

```text
                         TOM Resource Manager
                                  │
                    ┌─────────────┴─────────────┐
                    │                           │
                   CPU                         GPU
                    │                           │
          ┌─────────┼─────────┐          ┌──────┴──────┐
          │         │         │          │             │
        Rust     Whisper    Kokoro     Router       Active Model
                                           │       Qwen3 / VLM
                                           │
                                           ▼
                                      VRAM Manager
```

### 38.1 Scheduler Responsibilities

The scheduler should determine:

* Which model receives GPU priority
* Which models remain loaded
* Which models are loaded on demand
* Which models should be unloaded
* Available VRAM
* Available RAM
* CPU utilization
* GPU utilization
* Whether concurrent inference is safe
* Whether a workload should be queued
* Whether CPU fallback should be used
* Background task priority
* Model switching strategy
* Model loading/unloading cost
* Resource reservation for interactive tasks

### 38.2 Model Selection vs Resource Scheduling

These should remain separate concepts.

```text
Model Router
    ↓
"What model should perform this task?"

Resource Manager
    ↓
"Can that model run right now, and where?"
```

For example:

```text
Vision request
     ↓
Vision Manager
     ↓
Select Gemma 4 E4B
     ↓
Resource Manager
     ↓
Check VRAM
     ↓
Qwen3-8B currently running?
     │
 ┌───┴────┐
 YES      NO
 │         │
Queue    Load VLM
 │
 ▼
Execute when resources are available
```

This separation prevents model selection logic from becoming tightly coupled to hardware management.

### 38.3 Whisper Placement

Whisper Large-v3-Turbo should initially support both CPU and GPU execution.

During the initial development phase TOM should provide a configurable option:

```text
Whisper Device
├── Auto
├── CPU
└── GPU
```

The initial development process should benchmark both options on the target laptop.

Metrics should include:

```text
CPU latency
GPU latency
Real-time factor
RAM usage
VRAM usage
CPU utilization
GPU utilization
Effect on Qwen3
Effect on VLM
Model loading time
```

After benchmarking, TOM should select the most appropriate default.

The chosen default should **not prevent future fallback**.

For example:

```text
Whisper
Default → CPU

If CPU unavailable / overloaded
        ↓
Optional GPU fallback
```

or, if benchmarking demonstrates that GPU is substantially better:

```text
Whisper
Default → GPU

If GPU is occupied
        ↓
CPU fallback
```

The final decision should be based on measured performance rather than theoretical model requirements.

### 38.4 Event-Driven Resource Management

The Resource Manager should react to events rather than continuously swapping models unnecessarily.

Example:

```text
Idle
 │
 ▼
Qwen3-1.7B resident
 │
 ▼
User speaks
 │
 ├── Whisper processes audio
 │
 ▼
Router
 │
 ▼
Reasoning request
 │
 ▼
Load Qwen3-8B
 │
 ▼
Inference
 │
 ▼
Qwen3-8B becomes IDLE
 │
 ▼
Remain loaded temporarily
 │
 ▼
Unload when VRAM is required elsewhere
```

This reduces unnecessary model loading and unloading.

### 38.5 Background Work

Background tasks must not interfere with interactive TOM usage.

Examples:

```text
Background
├── Embedding generation
├── Qdrant indexing
├── File indexing
├── Memory maintenance
├── Image processing
└── Model preparation
```

These should have lower priority and should yield resources when TOM receives an interactive request.

### 38.6 Resource-Aware Memory Management

TOM should track at minimum:

```text
CPU
├── utilization
├── temperature
└── available capacity

RAM
├── used
└── available

GPU
├── utilization
├── temperature
└── power

VRAM
├── used
├── available
└── reserved

Models
├── loaded
├── loading
├── busy
└── idle
```

This information can be provided by the Rust Engine to the Python Resource Manager.

### 38.7 Long-Term Goal

The scheduler should eventually make decisions such as:

```text
"VRAM is insufficient for the requested VLM."

→ Queue request

"Qwen3-8B is idle and VLM is required."

→ Unload Qwen3-8B

"Whisper CPU is currently overloaded."

→ Consider GPU fallback

"Image generation is running."

→ Reduce background inference priority

"User interaction detected."

→ Suspend background AI work
```

The objective is not maximum theoretical concurrency.

The objective is:

> **Reliable interactive performance while intelligently sharing limited hardware resources.**


# 39. Model Lifecycle

Models should support:

```text
LOAD
READY
BUSY
IDLE
UNLOAD
ERROR
```

Example:

```text
Qwen3
   │
   ▼
Loaded
   │
   ▼
Reasoning
   │
   ▼
Idle
   │
   ▼
Unload if VRAM required
```

---

# 40. Latency Budget

TOM should have measurable latency targets for interactive operations.

These are **engineering targets, not guarantees**.

Actual performance must be benchmarked on the target hardware and continuously monitored by TOM's telemetry system.

### 40.1 Initial Interactive Latency Targets

| Stage                      | Initial Target |
| -------------------------- | -------------: |
| Wake word detection        |        <100 ms |
| VAD / speech-end detection |    <100–200 ms |
| STT finalization           |        <500 ms |
| LLM TTFT                   |        <500 ms |
| Tool execution             |        <500 ms |
| TTS first audio            |        <300 ms |
| Perceived response start   |       ~1–2 sec |

These targets should be treated as **aspirational engineering goals** rather than hard requirements.

### 40.2 STT Measurement

The STT target should not mean that an entire spoken sentence must always be transcribed in under 500 ms.

For Whisper Large-v3-Turbo, TOM should measure:

```text
Speech ends
    ↓
STT processing
    ↓
Final transcription available
```

as well as:

* Time to first partial transcription, if streaming is supported
* Time to final transcription
* Real-time factor
* CPU/GPU utilization
* RAM usage
* VRAM usage
* Model loading time
* Effect on concurrent workloads

Whisper's final CPU/GPU placement should be determined through benchmarking during initial development.

### 40.3 Model Loading Latency

Model loading and unloading must be treated as part of the latency budget.

```text
Request
   ↓
Model already loaded?
   │
 ┌─┴──┐
YES   NO
 │     │
 │   Load model
 │     │
 └──┬──┘
    ↓
Inference
```

A model that produces fast inference but requires several seconds to load may still produce poor perceived performance.

TOM should therefore track:

```text
Model load time
Model unload time
Model warm-up time
Inference time
VRAM allocation time
```

The Resource Manager should attempt to keep frequently used models warm when sufficient resources are available.

### 40.4 Perceived Latency

TOM should optimize for **perceived latency**, not only total execution time.

For example:

```text
User finishes speaking
        ↓
Immediate processing feedback
        ↓
LLM begins generating
        ↓
First sentence available
        ↓
TTS begins speaking
        ↓
Remaining response continues generating
```

The user should not have to wait for the entire response before TOM begins speaking.

### 40.5 Latency Telemetry

Every major stage should expose measurable timing information.

```text
request_id
├── wake_word_ms
├── vad_ms
├── stt_first_ms
├── stt_final_ms
├── router_ttft_ms
├── llm_ttft_ms
├── tool_ms
├── tts_first_audio_ms
├── total_processing_ms
└── perceived_response_start_ms
```

These measurements should be available to TOM's observability system.

---

# 41. Streaming

TOM should eventually support streaming wherever technically practical.

The preferred interactive pipeline is:

```text
Speech
  ↓
Wake Word
  ↓
VAD
  ↓
Streaming / Incremental STT
  ↓
Qwen3-1.7B Router
  ↓
Qwen3-8B / Tool / VLM
  ↓
Streaming Tokens
  ↓
Speech Formatter
  ↓
Streaming TTS
  ↓
Audio Playback
```

Instead of:

```text
Wait for entire speech
        ↓
Complete STT
        ↓
Wait for entire LLM response
        ↓
Generate entire audio
        ↓
Speak
```

### 41.1 Streaming Goals

Streaming should minimize the time between:

```text
User finishes speaking
        ↓
TOM starts responding
```

and:

```text
LLM begins generating
        ↓
TOM begins speaking
```

### 41.2 LLM Streaming

Qwen3-8B should stream generated tokens when supported.

```text
Qwen3-8B
   │
   ├── token
   ├── token
   ├── token
   └── token
         ↓
   Sentence Buffer
         ↓
      TTS
```

TOM should not wait for the complete LLM response before starting TTS.

### 41.3 Streaming TTS

The TTS pipeline should support sentence/phrase-level streaming.

```text
LLM
 ↓
"Sure, I can..."
 ↓
Sentence Buffer
 ↓
Kokoro
 ↓
Audio
 ↓
Playback
```

While the first sentence is being spoken:

```text
LLM
 ↓
Generate next sentence
 ↓
TTS queue
```

This creates a producer/consumer pipeline.

### 41.4 Audio Pipeline

The Rust Engine should handle low-level audio operations:

```text
Kokoro
   ↓
Audio chunks
   ↓
Rust Audio Engine
   ↓
Playback Buffer
   ↓
Speaker
```

Rust should be responsible for:

* Audio buffering
* Playback
* Microphone handling
* Audio device management
* Low-level synchronization

Python should remain responsible for:

* STT orchestration
* LLM orchestration
* TTS orchestration
* Conversation state
* Memory
* Tool execution

### 41.5 Streaming and Resource Scheduling

Streaming must work together with the Resource Manager.

The scheduler should understand that an interactive streaming task is different from a background batch task.

For example:

```text
Interactive TOM conversation
        │
        ▼
HIGH PRIORITY
        │
        ├── STT
        ├── Router
        ├── Reasoning
        └── TTS
```

while:

```text
Background image generation
        │
        ▼
LOW PRIORITY
        │
        └── Queue / pause when necessary
```

### 41.6 Streaming Failure Fallback

If streaming is unavailable or fails, TOM must gracefully fall back to buffered processing.

```text
Streaming available
       │
      YES
       ↓
Streaming pipeline

       │
      NO
       ↓
Buffered pipeline
```

The system should never depend on streaming being available for correctness.

### 41.7 Final Principle

TOM should optimize for:

> **Fast time-to-first-response rather than simply minimizing total execution time.**

The goal is for TOM to feel responsive and conversational even when the complete task requires substantially more processing time.

# 42. Observability

TOM should contain a telemetry subsystem.

```text
TOM MONITOR
```

Example:

```text
LLM
├── Model: Qwen3 8B
├── TTFT: 312 ms
├── Generation: 42 tok/s
├── VRAM: 5.8 GB
└── Context: 4,820 tokens

VOICE
├── STT: 182 ms
└── TTS: 241 ms

TOOLS
├── Calls: 7
├── Success: 6
└── Failed: 1
```

---

# 43. Metrics

Track at minimum:

### LLM

* TTFT
* Tokens/sec
* Total latency
* Prompt tokens
* Output tokens
* Context size
* VRAM
* RAM
* Model load time

### STT

* WER
* Latency
* Audio duration
* CPU
* GPU

### TTS

* Time to first audio
* Total latency
* Audio duration

### Tools

* Number of calls
* Success rate
* Failure rate
* Average latency
* Timeout count

### Agent

* Task success rate
* Number of steps
* Number of retries
* Number of failed tools
* Number of user confirmations

---

# 44. Logging

Use structured logs.

Example:

```json
{
  "timestamp": "...",
  "component": "tool_manager",
  "event": "tool_call",
  "tool": "system.gpu_temperature",
  "latency_ms": 42,
  "success": true
}
```

Do not log secrets.

---

# 45. Configuration

Avoid hard-coded configuration.

Use configuration files.

Example:

```text
config/
├── models.yaml
├── tools.yaml
├── voice.yaml
├── permissions.yaml
├── system.yaml
└── tom.yaml
```

Configuration should control:

* Models
* Quantization
* Providers
* Voice
* Tools
* Permissions
* Timeouts
* Logging
* Resource limits
* Personality

---

# 46. Suggested Repository Structure

```text
TOM/
│
├── README.md
├── PLAN.md
├── LICENSE
├── pyproject.toml
├── Cargo.toml                    # Optional workspace manifest
│
├── python/
│   └── tom/
│       ├── __init__.py
│       ├── main.py
│       │
│       ├── core/
│       │   ├── orchestrator.py       # Main TOM execution loop
│       │   ├── context.py            # Runtime/request context
│       │   ├── planner.py            # Multi-step task planning
│       │   ├── router.py             # High-level routing coordination
│       │   ├── scheduler.py          # Task/resource scheduling
│       │   └── lifecycle.py          # TOM startup/shutdown lifecycle
│       │
│       ├── agents/
│       │   ├── base.py
│       │   ├── assistant.py          # Main Pydantic AI agent
│       │   ├── task_agent.py         # Complex/multi-step tasks
│       │   └── dependencies.py       # Typed agent dependencies
│       │
│       ├── models/
│       │   ├── base.py               # Common model interface
│       │   ├── router.py             # Qwen3-1.7B router
│       │   ├── reasoning.py          # Qwen3-8B
│       │   ├── vision.py             # VLM providers
│       │   ├── stt.py                # Whisper Large-v3-Turbo
│       │   ├── tts.py                # Kokoro-82M
│       │   └── providers/
│       │       ├── local.py
│       │       └── cloud.py
│       │
│       ├── resources/
│       │   ├── manager.py             # CPU/RAM/GPU/VRAM manager
│       │   ├── model_manager.py       # Model load/unload/warm-up
│       │   ├── gpu.py                 # GPU/VRAM information
│       │   ├── memory.py              # RAM information
│       │   └── policies.py            # Scheduling/resource policies
│       │
│       ├── memory/
│       │   ├── manager.py             # Single memory entry point
│       │   ├── sqlite.py              # Structured source of truth
│       │   ├── qdrant.py              # Semantic/vector retrieval
│       │   ├── embeddings.py          # Embedding generation
│       │   ├── policies.py            # What should/shouldn't be remembered
│       │   └── models.py              # Memory schemas/types
│       │
│       ├── tools/
│       │   ├── registry.py             # Tool registration/discovery
│       │   ├── executor.py             # Validated tool execution
│       │   ├── schemas.py              # Tool input/output schemas
│       │   │
│       │   ├── system.py               # CPU/RAM/GPU/battery/processes
│       │   ├── computer.py             # Apps/windows/input
│       │   ├── files.py                # File operations
│       │   ├── web.py                  # Search/browse/retrieve
│       │   ├── ai.py                   # AI/image generation utilities
│       │   ├── memory.py               # Memory tools
│       │   └── personal.py             # Calendar/reminders/notes
│       │
│       ├── security/
│       │   ├── permissions.py           # SAFE / ASK / BLOCK
│       │   ├── confirmation.py          # User confirmation flow
│       │   ├── policies.py              # Security policies
│       │   └── secrets.py               # API keys/credentials
│       │
│       ├── vision/
│       │   ├── pipeline.py              # Vision orchestration
│       │   ├── manager.py               # Fast/primary/deep VLM selection
│       │   ├── vlm.py                   # VLM interface
│       │   ├── ocr.py                   # OCR processing
│       │   └── cv.py                    # Optional classical CV
│       │
│       ├── voice/
│       │   ├── pipeline.py              # End-to-end voice pipeline
│       │   ├── stt.py                   # STT orchestration
│       │   ├── tts.py                   # TTS orchestration
│       │   ├── vad.py                   # Voice activity coordination
│       │   └── formatter.py             # LLM output → speech-safe text
│       │
│       ├── ipc/
│       │   ├── client.py                # Python ↔ Rust IPC
│       │   ├── protocol.py              # IPC schemas/protocol
│       │   └── errors.py
│       │
│       ├── telemetry/
│       │   ├── metrics.py               # Performance metrics
│       │   ├── logging.py               # Structured logging
│       │   ├── events.py                # Internal TOM events
│       │   └── tracing.py               # Request/task tracing
│       │
│       └── schemas/
│           ├── routing.py                # Router outputs
│           ├── tasks.py                  # Task definitions
│           ├── tools.py                  # Tool contracts
│           ├── memory.py                 # Memory contracts
│           ├── models.py                 # Model metadata
│           └── events.py                 # Event schemas
│
├── rust/
│   └── tom-engine/
│       ├── Cargo.toml
│       └── src/
│           ├── main.rs
│           │
│           ├── audio/
│           │   ├── capture.rs             # Microphone capture
│           │   ├── playback.rs            # Audio playback
│           │   ├── buffer.rs              # Audio buffering
│           │   └── device.rs              # Audio device management
│           │
│           ├── wakeword/
│           │   ├── detector.rs
│           │   └── model.rs
│           │
│           ├── vad/
│           │   └── detector.rs
│           │
│           ├── system/
│           │   ├── cpu.rs
│           │   ├── gpu.rs
│           │   ├── memory.rs
│           │   ├── battery.rs
│           │   ├── processes.rs
│           │   ├── network.rs
│           │   └── disk.rs
│           │
│           ├── input/
│           │   ├── keyboard.rs
│           │   ├── mouse.rs
│           │   └── hotkeys.rs
│           │
│           ├── ipc/
│           │   ├── server.rs
│           │   ├── protocol.rs
│           │   └── handlers.rs
│           │
│           └── events/
│               ├── event.rs
|               ├── types.rs
|               ├── subscription.rs
|               ├── errors.rs
|               └── dispatch.rs
│
├── config/
│   ├── tom.yaml
│   ├── models.yaml
│   ├── tools.yaml
│   ├── permissions.yaml
│   ├── voice.yaml
│   ├── memory.yaml
│   └── resources.yaml
│
├── tests/
│   ├── unit/
│   │   ├── core/
│   │   ├── agents/
│   │   ├── models/
│   │   ├── memory/
│   │   ├── tools/
│   │   ├── security/
│   │   └── resources/
│   │
│   ├── integration/
│   │   ├── python_rust/
│   │   ├── memory/
│   │   ├── tools/
│   │   └── models/
│   │
│   ├── agent/
│   │   ├── routing/
│   │   ├── planning/
│   │   ├── tool_use/
│   │   ├── memory/
│   │   └── recovery/
│   │
│   ├── voice/
│   ├── vision/
│   ├── security/
│   └── benchmarks/
│       ├── llm/
│       ├── vision/
│       ├── stt/
│       ├── tts/
│       ├── gpu/
│       ├── memory/
│       └── end_to_end/
│
├── scripts/
│   ├── setup.py
│   ├── download_models.py
│   ├── benchmark.py
│   ├── health_check.py
│   └── dev/
│
├── docs/
│   ├── architecture.md
│   ├── agents.md
│   ├── tools.md
│   ├── models.md
│   ├── memory.md
│   ├── voice.md
│   ├── vision.md
│   ├── security.md
│   ├── resources.md
│   ├── ipc.md
│   └── benchmarking.md
│
├── data/
│   ├── memory/
│   │   ├── tom.db                    # SQLite source of truth
│   │   └── migrations/
│   │
│   ├── logs/
│   ├── benchmarks/
│   ├── cache/
│   └── runtime/
│
└── models/
    └── .gitkeep
```

### Architectural ownership

The repository should follow this rule:

```text
                         TOM
                          │
                 ┌────────┴────────┐
                 │                 │
             Python              Rust
             "Brain"          "Engine"
                 │                 │
        ┌────────┼────────┐        │
        │        │        │        │
      Agents   Models    Tools   Hardware
        │        │        │
        │        │        ├── System
        │        │        ├── Files
        │        │        ├── Computer
        │        │        ├── Web
        │        │        └── Memory
        │        │
        │        ├── Router
        │        ├── Reasoning
        │        ├── Vision
        │        ├── STT
        │        └── TTS
        │
        └── Pydantic AI
                 │
                 └── Typed tools / structured outputs

Memory:
    SQLite ─────────────── Source of truth
       │
       └── Memory Manager
               │
             Qdrant
          semantic retrieval

Resources:
    Resource & Model Lifecycle Manager
                 │
        ┌────────┴────────┐
        CPU/RAM          GPU/VRAM
        │                   │
        ├── Rust            ├── Router
        ├── STT             ├── Reasoning
        ├── TTS             └── VLM
        ├── SQLite
        └── Qdrant
```

### Important design rules

1. **`core/` owns TOM's behavior.**
   Pydantic AI is used inside the agent/tool layer; it does not define the entire TOM architecture.

2. **`memory/manager.py` is the only normal entry point to memory.**
   Agents and models never directly access SQLite or Qdrant.

3. **SQLite is authoritative.**
   Qdrant is a semantic retrieval/indexing layer.

4. **`resources/` owns model lifecycle.**
   Model selection and resource availability remain separate decisions:

   ```text
   Model Router
       ↓
   "Which model?"
       ↓
   Resource Manager
       ↓
   "Can/where can it run?"
   ```

5. **`tools/` contains capabilities, not arbitrary execution.**
   Every tool has a schema, permission level, timeout and risk classification.

6. **`security/` sits outside the agent.**
   The LLM can request an action, but it cannot bypass permission checks.

7. **`models/` contains model adapters, not application logic.**
   TOM should be able to replace Qwen, Whisper, Kokoro or a VLM without rewriting the orchestrator.

8. **`voice/` orchestrates voice; Rust handles low-level audio.**

   ```text
   Rust
   ├── microphone
   ├── wake word
   ├── VAD
   └── playback

   Python
   ├── Whisper
   ├── routing
   ├── reasoning
   ├── speech formatting
   └── Kokoro
   ```

9. **`vision/manager.py` owns VLM selection.**
   Qwen3-1.7B decides that a request requires vision; the Vision Manager decides whether the task needs the fast, primary or deep VLM.

10. **`schemas/` defines contracts between subsystems.**
    Pydantic models should validate router outputs, tool calls, memory operations, IPC messages and internal events.

11. **`telemetry/` must never record secrets.**
    API keys, passwords, tokens and sensitive credentials must be filtered before logging.

12. **`data/` and `models/` are runtime assets, not source code.**
    Large model files and generated databases should never be committed to Git.

13. **Tests mirror architectural boundaries.**
    Every important subsystem should have isolated unit tests plus integration tests where components interact.

14. **The Resource Manager must assume the RTX 4060 has only 8 GB VRAM.**
    TOM should not assume that Qwen3, VLMs, Whisper, image generation and other GPU workloads can safely run simultaneously.

15. **Streaming is a first-class concern.**

    ```text
    STT → Router → LLM → Speech Formatter → TTS → Playback
      │       │       │          │             │
      └───────┴───────┴──────────┴─────────────┘
                     streaming
    ```

16. **Rust and Python communicate through a stable IPC contract.**
    Initially this can use local JSON messages. The protocol should be versioned so the Rust engine can evolve independently.

### Dependency direction

The intended dependency direction is:

```text
main
 ↓
core
 ↓
agents
 ↓
tools / memory / models
 ↓
providers / infrastructure
```

With cross-cutting systems:

```text
security ───────────────┐
resources ──────────────┤
telemetry ──────────────┼──→ core / agents / tools / models
schemas ────────────────┤
ipc ────────────────────┘
```

Avoid circular dependencies wherever possible.

The most important principle is:

> **TOM owns the architecture. Frameworks, models and databases are replaceable implementation components.**

```

This structure is now much closer to the **actual TOM architecture**, rather than just being a collection of Python modules. In particular, `agents/`, `resources/`, `memory/manager.py`, `schemas/`, and `models/providers/` are the important additions.
```

---