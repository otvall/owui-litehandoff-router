Build the two independent Functions with `python tools/build_functions.py`.
No build-time downloads are performed.

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
