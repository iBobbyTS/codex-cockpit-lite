# Live API compatibility

## Resolution

The initial Cloudflare 400 is fixed. ChatGPT OAuth traffic now targets the
managed Codex endpoints under `https://chatgpt.com/backend-api/codex`, trusted
headers replace case variants instead of creating duplicate `Authorization` or
`Originator` fields, query strings are preserved, and streaming keeps the
upstream client alive while forwarding raw compressed bytes. Decoded responses
drop stale transport metadata; the same sanitizer is also used by
`/v1/alpha/search`.

## Direct Cockpit evidence

All live requests used the isolated backend on `127.0.0.1:18844`, an isolated
Codex home, and `gpt-5.6-terra` unless the wire endpoint itself requires the
fixed image model.

- Basic Responses turn: `COCKPIT_BASIC_OK`.
- Native `/v1/alpha/search`: HTTP 200 with current Python-version results.
- `/v1/models?client_version=0.146.0`: HTTP 200 and eight models.
- `/v1/images/generations`: `gpt-image-2`, HTTP 200, valid 1402 by 1122 image.
- Remote Compact V2: one unchanged thread/model completed seed, compact, and
  follow-up with `RESULT:COMPACTION_PROTOCOL_OK`.
- All six collaboration operations completed: spawn, wait, list, send,
  follow-up, and interrupt.

## Rosetta to Cockpit evidence

The copied temporary Rosetta configuration used provider identity
`codex_rosetta` with display name `OpenAI`; Gateway request logs identified the
upstream as `Codex Cockpit`. No third-party model, Rosetta Tavily search, or
Pixel Images path is accepted as final evidence.

- Built-in and command-execution live-agent tasks passed.
- Terra `view_image` run `202607310153` returned the correct red, green, blue,
  and yellow quadrant ordering from a real input image.
- Native `web.run` run `202607310205` projected the live tool, forwarded search
  to Cockpit `/v1/alpha/search`, returned `docs.python.org`, and ended with
  `RESULT:NETWORK_SEARCH_OK`.
- Images run `202607310206` used parent model Terra, forwarded the fixed
  `gpt-image-2` request to Cockpit `/v1/images/generations`, saved a running-dog
  image, and successfully inspected the saved file with `view_image`.
- Same-session Terra Remote Compact V2 run `202607310150` completed with one
  native compaction trigger and continued in the same thread.
- Deferred tool discovery runs `202607310215` through `202607310221`, corrected
  local-skill run `202607310223`, and Namespace run `202607310224` all passed.
- Subagent runs `202607310214`, `202607310225`, `202607310226`,
  `202607310228`, `202607310229`, and `202607310230` passed all six native
  collaboration operations through Cockpit. The send-message lifecycle has one
  expected 499 when delivery cancels an active child wait; the same child then
  consumes the message and completes successfully.

## Retained control failures

- `202607310158` exposed stale gzip metadata on the decoded Cockpit search
  response. The shared downstream-header sanitizer fixed it.
- `202607310202` used a one-character placeholder credential that collided
  with ordinary search text under Rosetta's fail-closed credential redaction.
  The isolated config now uses a long placeholder; the safety rule was not
  weakened.
- `202607310222` launched Codex from the repository root instead of its isolated
  worktree, so the local fixture was absent. Corrected run `202607310223`
  passed; the original is classified as a runner error.

## Validation

- Cockpit `python3 scripts/check.py`: passed with backend `51/51`, frontend
  `60/60`, sidecar smoke test, Rust clippy, and Rust `10/10` tests. The bundled
  workspace Node was used because the Homebrew Node installation is missing
  `libsimdjson.26.dylib`.
- macOS bundle completed at
  `frontend/src-tauri/target/release/bundle/macos/Codex Cockpit Lite.app`.
- Rosetta focused Images tests: `50/50`.
- Rosetta full non-integration suite: `3764 passed, 4 skipped` using the bundled
  Node runtime.
- Rosetta Ruff check and `ty check src/`: passed. Full `make lint` remains
  blocked only because unchanged baseline file `gateway/stream_trace.py` would
  be reformatted; unrelated code was deliberately left untouched.
- Rosetta CodeGraph sync completed.
- Temporary Cockpit `18844` and Rosetta live-agent ports `18797-18803` were
  stopped. The user's existing Cockpit on `8844` remained running.

