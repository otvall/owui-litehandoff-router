Build the independent Functions/Tool with `python tools/build_functions.py`.
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

To repeat offline acceptance, disconnect the container from its Docker network,
run setup/checks, and reconnect it afterwards. The provider and MCP stay on
loopback inside the same container. No project source package is installed there.
See `docs/acceptance.md` for actual results and boundaries.
