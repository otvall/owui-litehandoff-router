#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
result=0

run_native() {
    release=$1
    digest=$2
    docker run --rm --network none \
        --mount "type=bind,source=$root,target=/repo,readonly" \
        --tmpfs /tmp:rw,exec,size=1024m \
        -e PYTHONPATH=/app/backend -e PYTHONDONTWRITEBYTECODE=1 \
        -w /app/backend --entrypoint sh \
        "ghcr.io/open-webui/open-webui@$digest" -ec '
            mkdir /tmp/checkout
            cp -R /repo/src /repo/tests /repo/tools /repo/dist /repo/docs /repo/openspec \
                /repo/README.md /repo/pyproject.toml /tmp/checkout
            python /tmp/checkout/tools/run_native_tests.py --suite full --data-root /tmp/native --expect-version "$1"
        ' sh "$release"
}

run_native 0.13.3 sha256:8faf917243afee55e042b10ddc08ed6ae6fa9c62522c7e14d81e7cf2922325ae || result=1
run_native 0.13.5 sha256:f5c36d2d6c39fa6dac100ef6907d741b9571ab3d7be2368a06fa6fc13c35e25f || result=1
exit "$result"
