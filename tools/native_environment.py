"""Environment for disposable native checks; never uses an existing OWUI DB."""

from importlib.util import find_spec
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configure(name="helpers", owui_version="0.12.3", data_root=None):
    package = Path(find_spec("open_webui").origin).parent
    data = Path(data_root or ROOT / ".native-data") / owui_version / name
    data.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "DATA_DIR": str(data), "DATABASE_URL": "sqlite:///" + str(data / "webui.db"),
        "FRONTEND_BUILD_DIR": str(package / "frontend"),
        "STATIC_DIR": str(data / "static"), "OFFLINE_MODE": "true",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "WEBUI_SECRET_KEY": "isolated-router-tests-only",
        "ANONYMIZED_TELEMETRY": "false", "DO_NOT_TRACK": "true",
        "ENABLE_VERSION_UPDATE_CHECK": "false", "RAG_EMBEDDING_ENGINE": "openai",
    })


if __name__ == "__main__":
    configure("server")
    import asyncio
    import uvicorn
    from open_webui.main import app
    from open_webui.models.config import Config

    async def serve():
        # Configure only this disposable instance; no external providers.
        for key, value in {
            "openai.enable": True, "openai.api_base_urls": ["http://127.0.0.1:8766/v1"],
            "openai.api_keys": ["local-test"], "ollama.enable": False,
            "chat.context_compaction.enable": False,
        }.items():
            await Config.upsert({key: value})
        await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8765)).serve()

    asyncio.run(serve())
