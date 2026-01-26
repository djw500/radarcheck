# Implementation Plan - Backend Formalization

## Phase 1: System Definition (Output-Driven Design) [checkpoint: ac0c3ab]
- [x] Task: Collaborative Design Session: Define Desired System Outputs.
    - [x] Sub-task: Analyze current dashboard (`/status`) and identify missing/desired metrics.
    - [x] Sub-task: Iterate with user to define the "Dream Dashboard" state (what *should* we see?).
    - [x] Sub-task: Document these requirements in `docs/architecture/backend_outputs.md`.
- [x] Task: Create `docs/architecture/job_queue_spec.md` defining SQLite schema and worker states based on the above requirements.
- [x] Task: Conductor - User Manual Verification 'System Definition' (Protocol in workflow.md)

## Phase 2: Queue Infrastructure (`jobs/`)
- [ ] Task: Initialize `jobs` package and SQLite connection.
    - [ ] Sub-task: Create `jobs/__init__.py` and `jobs/db.py`.
    - [ ] Sub-task: Write tests for DB schema creation.
- [ ] Task: Implement Job Management (Enqueue/Dequeue).
    - [ ] Sub-task: Create `jobs/manager.py`.
    - [ ] Sub-task: Write tests for `add_job` and `claim_job`.
    - [ ] Sub-task: Implement concurrency locking tests.
- [ ] Task: Conductor - User Manual Verification 'Queue Infrastructure' (Protocol in workflow.md)

## Phase 3: Worker & Orchestration
- [ ] Task: Implement Worker Runner.
    - [ ] Sub-task: Create `jobs/worker.py` (the process that polls/executes).
    - [ ] Sub-task: Implement error handling and state updates (Failed/Completed).
- [ ] Task: Migrate Existing Logic to Jobs.
    - [ ] Sub-task: Create job wrappers for `cache_builder.py` functions.
    - [ ] Sub-task: Create job wrappers for `tile_generator.py` functions.
- [ ] Task: Conductor - User Manual Verification 'Worker & Orchestration' (Protocol in workflow.md)

## Phase 4: Observability
- [ ] Task: Create Diagnostic API (`/api/status/deep`).
    - [ ] Sub-task: Read from SQLite to report queue health.
    - [ ] Sub-task: Verify integrity of recent runs.
- [ ] Task: Create CLI Health Check.
    - [ ] Sub-task: `python check_system.py` outputs detailed state.
- [ ] Task: Conductor - User Manual Verification 'Observability' (Protocol in workflow.md)

## Phase 5: Final Validation
- [ ] Task: Full System E2E Test.
    - [ ] Sub-task: Run full flow on test region.
    - [ ] Sub-task: Validate against definitions from Phase 1.
- [ ] Task: Conductor - User Manual Verification 'Final Validation' (Protocol in workflow.md)
