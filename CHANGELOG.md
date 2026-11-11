# Changelog

All notable changes to marcus-toolkit are recorded here.
The format follows Keep a Changelog, and versions follow Semantic Versioning.

## [Unreleased]

### Added
- The first cut of the toolbox, lifted from the Minecraft plumbing (`painted-porch/marcus-minecraft`, `plumbing/`) and the Marcus experiment runners, with the defects their runs found fixed once (painted-porch/marcus #914).
`client` (the MCP-over-HTTP client, with `call()` raising when a reply cannot be read), `pricing` (one price table), `usage` (usage rows in pi's transcript layout and in a one-row-per-call layout any runtime can write), `watchdog` (the stop line re-read on every poll), `sampler` (the board read from outside, applying the drain verdict's rule), `integrity` (tree and committed-tree hashes, the version record), `server` (a server under test with every model-provider key stripped, the model off with an empty key, a port-held refusal, a readiness probe and an owner check), `seed` (`create_project(options.tasks)` with the known traps refused before the call), `record` (the run record), `measure` (pulls, the idle-poll ratio, the frontier series, board counts from a copy), `agent` (a pull-loop shell in Python), and `livecheck` (the toolbox's own live check against a real Marcus checkout).
- The deliberation layer, lifted from the Minecraft plumbing: `candidates` (the rules: two supporters with a majority promote, three declines evaporate, observers wait a veto window, a settling window for late objections), `views` (a decline needs an opportunity), `tending` (one pass over the board: promotion through `create_tasks` with the crew's words and a lineage line, closes on the candidate's own subject, a promoted task's status posted as it changes, proposals and corroborated observations turned into candidates, one event per change), `funnel` (the candidate funnel from the record), `gate` (the three-link PROVEN/PARTIAL/FAILED/NOT PROVEN check), and `tender` (the controller process, which can post candidates from a script in place of a planning model).
`node/candidates.js` reads candidates for an agent's bundle with the same marks.
The server config now switches the kernel's context feature on, because opinions and decisions are refused without it; and both clients register before any write, because the kernel records an opinion against the agent's registered project and refuses one from an agent it has not seen.
Proven live by the wargame's deliberation rehearsal on 2026-11-11: both chains PROVEN, one candidate per side ratified and worked to a closed task, two per side evaporated by declines, zero model calls.
- `node/`: the same client in Node, `mcp-http.js` and `board.js`, lifted from marcus-minecraft's `src/marcus/` and reduced to what a runtime without a game body needs.
