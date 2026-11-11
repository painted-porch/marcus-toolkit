# marcus-runner

The run-side toolbox that Marcus extensions share.

## What this is, briefly

[Marcus](https://github.com/painted-porch/marcus) coordinates AI agents through a shared task board: agents pull work from the board over MCP (the Model Context Protocol, the wire protocol for Marcus's tools), do it, and report back with evidence.
An *extension* is a separate program that seeds a board and runs agents against it for one purpose: verifying citations, verifying a numerical report, coordinating Minecraft agents, refereeing a wargame.

Every extension needs the same scaffolding around a run, and every extension so far rebuilt it.
This package is that scaffolding, built once (the inventory and the reasoning are in painted-porch/marcus issue #914):

| Module | What it does |
| --- | --- |
| `marcus_runner.server` | a **server under test**: a private Marcus started for one run with every model-provider key stripped from its environment and the model switched off with an empty key, so the run can spend nothing by construction; refuses a port someone else holds and proves the port's owner is this run's server |
| `marcus_runner.seed` | seeds a project with exactly the tasks named, through `create_project(options.tasks)`, and refuses before the call what the board would mis-handle: an unknown spec key, a `build`-family label, a dependency naming no task, a project name containing "test" |
| `marcus_runner.watchdog` | stops a run the moment its spend reaches the approved line; the line is re-read on every poll when it comes from a file |
| `marcus_runner.pricing`, `marcus_runner.usage` | one price table; usage rows read from pi's transcripts or from `<state>/<agent>/usage.jsonl`, one row per model call, which any runtime can write |
| `marcus_runner.sampler` | reads the board from outside every few seconds and applies the drain verdict's claimability rule, so frontier width has a time series |
| `marcus_runner.integrity` | hashes the files that execute, hashes the same files as committed, records a checkout's version |
| `marcus_runner.record` | the run record: `runs/<stamp>/`, `run-config.txt`, JSONL streams, copies |
| `marcus_runner.measure` | pulls and the idle-poll ratio, the kernel's frontier series, the sampler's series, board counts from a copy of `kanban.db` |
| `marcus_runner.client`, `marcus_runner.agent` | the MCP client and a pull-loop agent shell in Python |
| `node/` | the same client in Node: `mcp-http.js` and `board.js` |
| `marcus_runner.livecheck` | the toolbox's own live check against a real Marcus checkout |

Nothing in this package imports Marcus's own code.
It talks to a server over MCP and reads the files a run leaves behind, which is all an extension may do (`docs/architecture/extension-contract.md` in the Marcus repository).

## Install

```bash
pip install -e .            # from a checkout
pip install -e ".[dev]"     # with the test and lint tools
```

For the Node client, import `node/board.js` and `node/mcp-http.js` directly; they have no dependencies.

## The live check

```bash
python -m marcus_runner.livecheck --checkout ~/dev/marcus --out livecheck-runs
```

Starts a server under test from that checkout, seeds two tasks (the second depending on the first), runs one scripted agent through register, pull, report, pull, report, drain, reads the board from outside before and after, and writes `livecheck-runs/<stamp>/VERDICT.json`.
It passes only when every card closed, the agent heard the drain, every reply was read back, and the server log carries no model-provider host.
CI runs it against the Marcus commit pinned in `.github/workflows/ci.yml`.

## Where it came from

The Minecraft harness's plumbing (`painted-porch/marcus-minecraft`, `plumbing/`), the Marcus experiment runners (`dev-tools/experiments/runners/marcus_client.py`), and the AI-citation verifier's free rehearsal.
The defects those runs found are fixed here once: a stop line read only at process start, a placeholder key that fired one real call on startup, a task spec whose extra key broke every promotion, a blocked task counted as claimable.

## Versioning

Each release states the Marcus commit it was live-checked against.
The toolbox depends on Marcus's MCP tool names and reply shapes, not on its code; when those change, this package gets a release, not a patch to the kernel.
