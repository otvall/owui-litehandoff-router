Build the two independent Functions with `python tools/build_functions.py`.
## Selective terminal acceptance

Use only `owui-router-test` (OWUI 0.12.3). Copy `terminal_acceptance.py`,
`terminal_gate_provider.py`, `parallel_gate.py` and current `dist/` into
`/tmp/lhr-terminal-gate/`. The provider binds container loopback 8770 and also
provides a terminal-compatible backend: it actually executes subprocess shell
commands and reads their files. It is a disposable test backend, not a stock
Open Terminal installation. Only its `work/` files are removed by cleanup.

```sh
docker exec -e TERMINAL_DISPOSABLE=owui-router-test -e PYTHONPATH=/app/backend -w /app/backend owui-router-test python /tmp/lhr-terminal-gate/terminal_acceptance.py bootstrap
docker exec -d owui-router-test python /tmp/lhr-terminal-gate/terminal_gate_provider.py
docker exec -e TERMINAL_DISPOSABLE=owui-router-test -e PYTHONPATH=/app/backend -w /app/backend owui-router-test python /tmp/lhr-terminal-gate/terminal_acceptance.py install
docker exec -e TERMINAL_DISPOSABLE=owui-router-test -e PYTHONPATH=/app/backend -w /app/backend owui-router-test python /tmp/lhr-terminal-gate/terminal_acceptance.py check
docker exec -e TERMINAL_DISPOSABLE=owui-router-test -e PYTHONPATH=/app/backend -w /app/backend owui-router-test python /tmp/lhr-terminal-gate/terminal_acceptance.py controls
docker exec -e TERMINAL_DISPOSABLE=owui-router-test -e PYTHONPATH=/app/backend -w /app/backend owui-router-test python /tmp/lhr-terminal-gate/terminal_acceptance.py revocation
```

The gate installs separate `tgate` models and the two exports with only `AGENTS`
configured. Two actors have model access and no direct grants on the agents'
terminals, working Skills or ordinary Tool. A third fixture connection is
directly selectable and checks that parent selection does not override model
bindings. Private credentials and configuration snapshots stay in a mode-0600
state file inside the container. `check` records actual marker/file reads,
handoff, mixed/same-agent groups and concurrent users. `controls` covers a
disabled resource, Stop/deadline and late subprocess results. Browser inspection
uses the saved report chat IDs and does not require a terminal pane.

For the Inline Visualizer regression, also copy the pinned `tools/fixtures/`
directory beside the scripts and run the `visualizer` action. It verifies the
2.2.3 Tool/Skill checksums and attaches them only to the ordinary B fixture,
then checks a real saved child embed. Open the recorded child in the browser
and verify its SVG and toggle. The `revocation` action removes the fixture
owner's terminal grant temporarily, verifies no command is dispatched and
restores the connection in `finally`.

After collecting evidence and closing fixture browser sessions, restore shared
settings and delete only these fixture users/resources and their test files:

```sh
docker exec -e TERMINAL_DISPOSABLE=owui-router-test -e PYTHONPATH=/app/backend -w /app/backend owui-router-test python /tmp/lhr-terminal-gate/terminal_acceptance.py restore
docker exec -e TERMINAL_DISPOSABLE=owui-router-test -e PYTHONPATH=/app/backend -w /app/backend owui-router-test python /tmp/lhr-terminal-gate/terminal_acceptance.py cleanup
```

Stop the fixture provider process after cleanup. Keep sanitized evidence in the
repository; never copy the private state or admin token into it. A repeated
installation needs a fresh fixture directory after the previous cleanup.
No build-time downloads are performed.

## Inline Visualizer native/browser gate

Use only disposable `owui-router-test`, OWUI **0.12.3**. Prepare its existing
OWUI dependencies and `fixtures/inline_visualizer/` before the offline run.
The manifest records upstream URLs, commits, versions and SHA-256; every asset
is checked before any HTTP request. A changed hash is rejected. The two current
exports are independent Functions; the checkout package is not installed.

```sh
python3 tools/visualizer_acceptance.py verify-assets
docker exec owui-router-test mkdir -p /tmp/lhr-visualizer-gate
docker cp tools/. owui-router-test:/tmp/lhr-visualizer-gate/
docker cp dist owui-router-test:/tmp/lhr-visualizer-gate/dist
docker exec -e VISUALIZER_DISPOSABLE=owui-router-test -e PYTHONPATH=/app/backend -w /app/backend owui-router-test python /tmp/lhr-visualizer-gate/visualizer_acceptance.py bootstrap
docker exec -d owui-router-test sh -c 'echo $$ > /tmp/lhr-visualizer-gate/provider.pid; exec python /tmp/lhr-visualizer-gate/visualizer_gate_provider.py > /tmp/lhr-visualizer-gate/provider.log 2>&1'
docker exec -e VISUALIZER_DISPOSABLE=owui-router-test -e PYTHONPATH=/app/backend -w /app/backend owui-router-test python /tmp/lhr-visualizer-gate/visualizer_acceptance.py install --version 2.2.3
```

Bootstrap signs a temporary session for the existing disposable admin, without
printing tokens. Credentials/settings stay in mode-0600 files inside that
directory. Install creates only `vg_*`/`vg-*` resources and three synthetic
users, snapshots shared provider/config and viewer settings, and enables the
declared iframe same-origin/scripts condition **only for the fixture viewer**.
The viewer receives model grants; the owner keeps private Tools/Skills. Router
and the simple worker have no Visualizer attachments. The audit Filter observes
connections/tickets. A measurement suffix calls the byte-identical upstream
implementation once and forwards native callbacks; it adds no renderer or RPC.

Sign in at `http://127.0.0.1:8765` as `lhr-visualizer-viewer@example.com` using
the password from private fixture state. Do not print session tokens or save
the password in the browser. Choose **Visualizer acceptance Router**. Submit
`VG_SINGLE: Create an interactive local SVG visualization with a button`.
Keep the main chat open until completion; do not open a child beforehand.

```sh
docker exec -e VISUALIZER_DISPOSABLE=owui-router-test owui-router-test python /tmp/lhr-visualizer-gate/visualizer_acceptance.py latest
docker exec -e VISUALIZER_DISPOSABLE=owui-router-test owui-router-test python /tmp/lhr-visualizer-gate/visualizer_acceptance.py verify
```

`verify --chat <main-id>` checks a specific saved run. Backend success stays
separate from rendering. In Sources → agent A → **Открыть выполнение**, check
SVG/unique marker, disappearance of loader, and Toggle (`STATE=0` → `STATE=1`,
blue → green). Capture AX/screenshot, reload using the browser's toolbar button
(OWUI may intercept Cmd+R as regeneration), and repeat. Compare provider
`/stats` and `tool-trace.jsonl` before/after viewing; counters must not grow.

For negative control install `--version 2.1.1` in the same fixture IDs (the
installer resets its incompatible `offline` Valve). Submit fresh
`VG_SINGLE NEGATIVE211` in the real browser, keep the main chat open and promptly
run `negative --chat <main-id>`. It confirms a real session pending on `execute`
for six seconds, then stops native tasks. Submit `VG_INSPECT_ONLY` and check the
audit record has zero tickets. Reinstall **2.2.3** afterwards. An invented
session ID is not a valid negative control.

For disconnection submit fresh `VG_DISCONNECT`, then navigate the original tab
to `about:blank` **without Stop**. The provider holds the Tool-call stream for
12 seconds so the real socket disconnects before language detection. Verify
the saved main ID afterwards, then open its child in a new session. A native
disconnected-caller error is distinct from browser acknowledgement.

Use `VG_PAIR` for A+B, `VG_REPEAT` for two A assignments, `VG_LIVE` for viewing
while generation after Tool is held. Before LIVE remove any old
`continue-generation` file, open its child, then release:

```sh
docker exec owui-router-test touch /tmp/lhr-visualizer-gate/continue-generation
```

The live hold is bounded to 120 seconds. `VG_DIRECT` checks ordinary handoff
with Visualizer in the main chat. Fragments use local SVG/JavaScript, no CDN,
fetch or images. Preserve local browser/backend transport during offline tests
and record browser network evidence separately.

For the existing pgate A+B→C/A+A→C fixtures prepared by the preceding sections,
repeat the ordinary group/handoff control. This refreshes only synthetic fixture
credentials and temporarily selects their local provider, restoring it in
`finally`:

```sh
docker exec -e VISUALIZER_DISPOSABLE=owui-router-test -e PYTHONPATH=/app/backend -w /app/backend owui-router-test python /tmp/lhr-visualizer-gate/visualizer_existing_control.py
```

After preserving screenshots/AX and signing out/closing test browser sessions,
run the process audit, export sanitized evidence, and restore:

```sh
docker exec -e VISUALIZER_DISPOSABLE=owui-router-test owui-router-test python /tmp/lhr-visualizer-gate/visualizer_acceptance.py audit
docker exec -e VISUALIZER_DISPOSABLE=owui-router-test owui-router-test python /tmp/lhr-visualizer-gate/visualizer_acceptance.py export-evidence
docker exec -e VISUALIZER_DISPOSABLE=owui-router-test owui-router-test python /tmp/lhr-visualizer-gate/visualizer_acceptance.py restore
docker exec -e VISUALIZER_DISPOSABLE=owui-router-test owui-router-test python /tmp/lhr-visualizer-gate/visualizer_acceptance.py cleanup
```

Copy `visualizer-evidence.json` out before cleanup. Restore returns shared
provider/config and the viewer's original UI fields, including resetting new
fields. It preserves children and production resources. The separate `cleanup`
action requires exported evidence, restore and an audit with zero viewer sockets
and tickets; it deletes only this gate's new models/functions/resources/users
and their fixture chats. It does not delete previous pgate installations.
Optional manual control reports are included when present; absent ones are
listed in `controls_not_recorded`, never converted to a passed result. Browser
observations/screenshots remain a separate record from backend verification.
Stop only the fixture provider via its saved PID after checking its command line.
Delete only named fixture resources/chats after exporting evidence. Actual
backend and browser results belong in `docs/acceptance.md`.

`acceptance_provider.py` is a deterministic provider, not an LLM evaluation.
`acceptance_server.py` adds a real local MCP Streamable HTTP endpoint and binds
only `127.0.0.1:8766`. Both use the libraries already installed with OWUI.

For the disposable Docker instance `owui-router-test`, copy these three scripts,
`acceptance_checks.py` and `dist/` into `/tmp/lhr-acceptance/`. Supply its temporary
admin session token in `/tmp/lhr-acceptance/admin-token` with mode 0600. Start
`acceptance_server.py` inside that container, then run:

```sh
docker exec owui-router-test python /tmp/lhr-acceptance/acceptance_setup.py setup
docker exec owui-router-test python /tmp/lhr-acceptance/acceptance_checks.py
docker exec owui-router-test python /tmp/lhr-acceptance/acceptance_setup.py snapshot
docker exec owui-router-test python /tmp/lhr-acceptance/acceptance_setup.py restore
```

Setup is restricted to the fixed LHR fixture names and configures the local
provider, two users/groups, models, Skills, Tools and MCP connections. It saves
the original provider/configuration and test credentials in private files in
that directory. Use only a disposable test instance. Checks drive real HTTP
requests and native saved-chat processing, including parallel users, new turns,
provider/context/resource errors and revocation between discovery and handoff.
They briefly replace a test Valve/grant and restore it in `finally`.
Setup removes the obsolete `lhr_delegate` fixture Tool and sets empty base Tool
Valves. Snapshot the old installation before setup when testing an upgrade;
snapshot/restore supports both versions, including the optional legacy Tool.
Checks verify the inline callable's actual result and an explicit `tools: []`
request, in addition to confirming that the separate delegate Tool is absent.

To repeat offline acceptance, disconnect the container from its Docker network,
run setup/checks, and reconnect it afterwards. The provider and MCP stay on
loopback inside the same container. No project source package is installed there.
See `docs/acceptance.md` for actual results and boundaries.

## Parallel native gate

Run only in the disposable `owui-router-test` with the preceding local MCP
server already running on 8766. Copy `parallel_gate.py`, `parallel_gate_pipe.py`,
`parallel_gate_filter.py`, `parallel_gate_provider.py` and the current `dist/`
directory into `/tmp/lhr-parallel-gate/`. The gate installs separate pgate
Functions/models; it does not replace the LHR installation. `setup` snapshots
the original provider/settings/connections to a mode-0600 private state file.
An existing temporary admin token can be supplied in `admin-token` (0600), or
`bootstrap` can mint a short-lived session for the existing disposable admin.

```sh
docker exec -e PYTHONPATH=/app/backend -w /app/backend owui-router-test python /tmp/lhr-parallel-gate/parallel_gate.py bootstrap
docker exec -d owui-router-test python /tmp/lhr-parallel-gate/parallel_gate_provider.py
docker exec owui-router-test python /tmp/lhr-parallel-gate/parallel_gate.py setup
docker exec owui-router-test python /tmp/lhr-parallel-gate/parallel_gate.py install
docker exec owui-router-test python /tmp/lhr-parallel-gate/parallel_gate.py run
docker exec owui-router-test python /tmp/lhr-parallel-gate/parallel_gate.py verify
docker exec owui-router-test python /tmp/lhr-parallel-gate/parallel_gate.py stop
docker exec owui-router-test python /tmp/lhr-parallel-gate/parallel_gate.py dispatch-stop
docker exec owui-router-test python /tmp/lhr-parallel-gate/parallel_gate.py timeout
docker exec owui-router-test python /tmp/lhr-parallel-gate/parallel_gate.py dispatch-timeout
docker exec owui-router-test python /tmp/lhr-parallel-gate/parallel_gate.py approvals
```

Provider binds container loopback only, emits deterministic streaming Tool calls
and checks prompts, temperatures and capabilities. `verify` checks the actual
saved output, downloads owner files, and measures overlapping local Tool intervals.
Stop/deadline checks inspect native task cleanup and MCP session completion.
These are integration fixtures, not production APIs or routing quality tests.

## Product parallel acceptance

After the gate setup, copy `parallel_acceptance.py` to the same private directory
and replace the export contents with `docker cp dist/. owui-router-test:/tmp/lhr-parallel-gate/dist`.
Use the current `parallel_gate_provider.py` (Router/A/B/C) on loopback 8768.
The product installer uploads the two exports directly, configures only AGENTS,
adds C's presentation fixture and two groups with different model grants.

```sh
docker exec owui-router-test python /tmp/lhr-parallel-gate/parallel_acceptance.py install
docker exec owui-router-test python /tmp/lhr-parallel-gate/parallel_acceptance.py check
docker exec owui-router-test python /tmp/lhr-parallel-gate/parallel_acceptance.py lifecycle
docker exec owui-router-test python /tmp/lhr-parallel-gate/parallel_acceptance.py controls
```

`check` verifies full A+B→C and A+A→C, native file downloads, denied foreign
access and a fresh Router follow-up. `lifecycle` creates and deletes disposable
chats to check Stop/deadline/native cascade. `controls` temporarily changes a
test Valve or required B resource, restores it in `finally`, and checks errors
and same-chat A→B→Router with parallel disabled. C's fixture reads both authorized
CSV files and creates an HTML presentation. Browser verification is recorded
separately in `docs/acceptance.md`.

## Model-bound private resources

`resource_access_gate.py` runs against a separate `.native-data/resource-access-gate`
database with non-admin owners and two execution users without resource grants.
It starts an ephemeral loopback MCP endpoint, checks all three private Skill
readers after native refresh, execution-user UserValves/OAuth/forwarded headers,
and missing-user-OAuth rejection. It needs permission to bind a loopback socket:

```sh
.venv/bin/python tools/resource_access_gate.py
```

For the existing disposable Docker fixture, copy `resource_acceptance.py` into
`/tmp/lhr-parallel-gate/`, current `acceptance_server.py` into `/tmp/lhr-acceptance/`,
and rebuild/copy both exports. Restart only the local acceptance MCP/provider
fixtures with the updated scripts before running:

```sh
docker exec -e PYTHONPATH=/app/backend -w /app/backend owui-router-test python /tmp/lhr-parallel-gate/resource_acceptance.py install
docker exec owui-router-test python /tmp/lhr-parallel-gate/resource_acceptance.py exports
docker exec owui-router-test python /tmp/lhr-parallel-gate/resource_acceptance.py check
docker exec owui-router-test python /tmp/lhr-parallel-gate/resource_acceptance.py lifecycle
docker exec owui-router-test python /tmp/lhr-parallel-gate/resource_acceptance.py controls
```

Install makes only the fixed pgate resources private, assigns a non-admin agent
owner, configures distinct UserValves and seeds synthetic per-user OAuth sessions
for the two loopback MCP connections. It preserves model grants for both groups.
Credentials, synthetic OAuth sessions and evidence stay in the disposable
container; no real external OAuth flow or authorization server is tested.
`check` verifies native private API denial before/after model execution,
A+B→C/A+A→C, same-chat reselection, own files, UserValves and execution-user MCP
identity. Browser evidence remains a separate acceptance record.
