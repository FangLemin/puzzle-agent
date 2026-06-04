# PuzzleOps Python Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pure Python implementation of the PuzzleOps Agent prototype for France/Japan jigsaw content operations.

**Architecture:** Keep business logic in small Python modules, backed by simulated data and covered by tests. Use Python standard library `http.server` for a local backend-style UI so the project can run without JavaScript, Java, Node, or frontend build tools.

**Tech Stack:** Python 3.12, dataclasses, unittest/pytest, http.server, html escaping, simulated JSON-like in-memory data.

---

### Task 1: Core Domain Models And Tests

**Files:**
- Create: `/Users/fanglemin/puzzle-agent-python/tests/test_agents.py`
- Create: `/Users/fanglemin/puzzle-agent-python/puzzle_ops/models.py`
- Create: `/Users/fanglemin/puzzle-agent-python/puzzle_ops/data.py`
- Create: `/Users/fanglemin/puzzle-agent-python/puzzle_ops/agents.py`

- [ ] Write tests for country data isolation, regular demand rows, trial demand rows, holiday recommendations, editable fields, value prediction, weekly schedule positions, and analysis remarks.
- [ ] Run: `python3 -m pytest tests/test_agents.py -q`; expected result before implementation is import failure.
- [ ] Implement dataclasses, simulated country data, and agent service functions.
- [ ] Run tests again; expected result is all tests passing.

### Task 2: Pure Python Local Backend UI

**Files:**
- Create: `/Users/fanglemin/puzzle-agent-python/puzzle_ops/renderer.py`
- Create: `/Users/fanglemin/puzzle-agent-python/puzzle_ops/server.py`
- Create: `/Users/fanglemin/puzzle-agent-python/run_app.py`

- [ ] Render homepage, regular demand, trial demand, analysis, value master, schedule, and sync views from Python.
- [ ] Implement simple GET navigation and POST actions for country/view switching, adding demand rows, generating descriptions, applying value master, and editing table fields.
- [ ] Run: `python3 run_app.py`; expected local URL is `http://localhost:5188`.

### Task 3: Documentation And Verification

**Files:**
- Create: `/Users/fanglemin/puzzle-agent-python/README.md`

- [ ] Document how to run tests and start the local Python app.
- [ ] Verify no `.js`, `.java`, or frontend package files are required in the new project.
- [ ] Verify the app responds with HTTP 200 and key pages include the expected Chinese business fields.
