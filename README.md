# Industrial Separation Train Digital Twin

**Edge-Hardened Sensor Simulator for Mission-Critical Infrastructure**

A realistic offshore instrumentation simulator designed to model how industrial edge gateways behave in remote oil & gas environments — where reliability, determinism, and recoverability matter more than scale.

---

## The Problem

Testing industrial data systems with random values is meaningless.

Real facilities experience:

- Gradual pressure drift over hours
- Daily thermal cycling
- Mechanical degradation signatures
- Slug flow upsets
- Sensors freezing at believable values
- Control instability and oscillation

Without realistic telemetry, you cannot properly validate:

- Alarm logic
- Data buffering strategies
- Store-and-forward systems
- Edge analytics
- Failure detection pipelines

---

## The Solution

This project simulates **12 instruments across a 3-stage offshore separation train**:

**HP Separator → LP Separator → Test Separator → Export Pump → Heat Exchanger**

Each tag generates physically plausible signals and writes them to a local historian,
exposing telemetry through an API — exactly like a real industrial edge gateway.

---

## Purpose (IronClad Roadmap)

This repository is part of the **IronClad learning roadmap**, focused on building
self-healing edge infrastructure before moving into orchestration and distributed systems.

The goal is not to simulate sensors.  
The goal is to engineer **resilient containerized runtime patterns** used in real facilities.

---

## Architecture

| Real Facility Component | This Project             |
| ----------------------- | ------------------------ |
| PLC / RTU               | Sensor simulation loop   |
| Edge Gateway            | Docker container runtime |
| Local Historian         | SQLite database          |
| SCADA / MES             | REST API                 |
| Maintenance Watchdog    | Docker healthcheck       |

Container Hardening

This container is intentionally designed for edge deployment patterns.

    Runs as a non-root service account (least privilege)

    Uses python:3.12-slim to minimize footprint and attack surface

    SQLite configured in WAL mode for concurrent reads/writes without locking

    Persistent volume mounted at /data (container itself is stateless)

    Internal healthcheck validates API + data pipeline responsiveness

    Deterministic runtime — no external services required

    These are the same constraints faced by offshore or remote installations.

Simulated Behaviors

The simulator produces operationally realistic patterns:

    Pressure drift with control oscillation

    Ambient-driven temperature cycling

    Production decline curves

    Pump vibration growth with harmonic noise

    Sensor freeze (quality still “Good”)

    Slug flow surges

    Control valve sticking signatures

| Device   | Tag                | Type        | Unit    |
| -------- | ------------------ | ----------- | ------- |
| SEP-V100 | inlet_pressure     | Pressure    | PSI     |
| SEP-V100 | outlet_temperature | Temperature | °C      |
| SEP-V100 | oil_level          | Level       | %       |
| SEP-V200 | inlet_pressure     | Pressure    | PSI     |
| SEP-V200 | outlet_temperature | Temperature | °C      |
| SEP-V200 | gas_flow           | Flow        | MMSCFD  |
| SEP-V300 | inlet_pressure     | Pressure    | PSI     |
| SEP-V300 | water_level        | Level       | %       |
| PMP-P100 | discharge_pressure | Pressure    | PSI     |
| PMP-P100 | vibration          | Vibration   | mm/s    |
| PMP-P100 | oil_flow           | Flow        | bbl/day |
| HX-E100  | outlet_temperature | Temperature | °C      |

Quick Start
Build the Image
https://github.com/ejimaone/industrial-separation-simulator.git
cd separation-simulator
docker build -t separation-simulator .
docker run -p 8080:8080 -v $(pwd)/data:/data separation-simulator
curl http://localhost:8080/health
curl http://localhost:8080/readings
Host Directory → /data (mounted volume)
→ sensors.db (SQLite WAL)

| Variable         | Default | Description            |
| ---------------- | ------- | ---------------------- |
| LOG_LEVEL        | INFO    | Runtime logging level  |
| API_PORT         | 8080    | REST API port          |
| POLL_INTERVAL_MS | 1000    | Sensor update interval |
| CLEANUP_HOURS    | 24      | Retention window       |

Tech Stack

    Python 3.12 (minimal runtime)

    SQLite (local historian with WAL journaling)

    Docker (edge-style containerization)

Why SQLite Instead of Postgres?

Edge systems require:

    Zero maintenance

    Crash safety

    Predictable I/O

    No network dependency

SQLite matches real gateway deployments far better than client/server databases.

Repository Structure
src/ → simulation + API logic
config/ → instrument definitions
data/ → runtime database (volume mounted)
Dockerfile → hardened container build

License

MIT License
