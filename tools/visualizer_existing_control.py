"""Repeat the pre-existing product fixtures, then restore the provider config.

Only the prepared disposable container is accepted. Credentials are not printed.
"""

import asyncio
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,'/tmp/lhr-parallel-gate')
import parallel_gate as gate
import parallel_acceptance as product
import resource_acceptance as resources
own=Path('/tmp/lhr-visualizer-gate')
assert Path(__file__).resolve().parent == own
assert os.environ.get('VISUALIZER_DISPOSABLE') == 'owui-router-test'
assert gate.api('/api/version', token=(own/'admin-token').read_text().strip())['version'] == '0.12.3'
descriptor = os.open(gate.TOKEN, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(descriptor, 'w') as output:
    output.write((own/'admin-token').read_text())
state=json.loads(gate.STATE.read_text())
for key in ('user','user2'):
    user=state[key]
    user.update(gate.api('/api/v1/auths/signin',{'email':user['email'],'password':user['password']}))
gate.private_json(gate.STATE,state)
asyncio.run(resources.prepare_private_records(state))
before=gate.api('/openai/config')
try:
    original=json.loads((own/'visualizer-private-state.json').read_text())['snapshot']['openai']
    gate.api('/openai/config/update',original)
    gate.api('/api/models')
    product.check()
    evidence=json.loads((Path('/tmp/lhr-parallel-gate')/'product-evidence.json').read_text())
    gate.private_json(own/'existing-control-evidence.json',evidence)
finally:
    gate.api('/openai/config/update',before)
    gate.api('/api/models')
