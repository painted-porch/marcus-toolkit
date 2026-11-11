# Changelog

All notable changes to marcus-runner are recorded here.
The format follows Keep a Changelog, and versions follow Semantic Versioning.

## [Unreleased]

### Added
- The first cut of the toolbox, lifted from the Minecraft plumbing (`painted-porch/marcus-minecraft`, `plumbing/`) and the Marcus experiment runners, with the defects their runs found fixed once (painted-porch/marcus #914).
`client` (the MCP-over-HTTP client, with `call()` raising when a reply cannot be read), `pricing` (one price table), `usage` (usage rows in pi's transcript layout and in a one-row-per-call layout any runtime can write), `watchdog` (the stop line re-read on every poll), `sampler` (the board read from outside, applying the drain verdict's rule), `integrity` (tree and committed-tree hashes, the version record), `server` (a server under test with every model-provider key stripped, the model off with an empty key, a port-held refusal, a readiness probe and an owner check), `seed` (`create_project(options.tasks)` with the known traps refused before the call), `record` (the run record), `measure` (pulls, the idle-poll ratio, the frontier series, board counts from a copy), `agent` (a pull-loop shell in Python), and `livecheck` (the toolbox's own live check against a real Marcus checkout).
- `node/`: the same client in Node, `mcp-http.js` and `board.js`, lifted from marcus-minecraft's `src/marcus/` and reduced to what a runtime without a game body needs.
