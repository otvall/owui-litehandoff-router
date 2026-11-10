# Development

Целевая среда: Open WebUI **0.13.3**, Python 3.11 или 3.12. Runtime
использует стандартную библиотеку и библиотеки установленного OWUI;
дополнительный сервис и библиотеки оркестрации не требуются.

На машине с интернетом заранее подготовьте зависимости:

```sh
python3 -m venv .venv
.venv/bin/python -m pip download -r requirements-native.txt -d wheelhouse
```

Перенесите wheelhouse на ту же ОС/архитектуру с той же версией Python:

```sh
.venv/bin/python -m pip install --no-index --find-links wheelhouse -r requirements-native.txt
.venv/bin/python -m pip install --no-index --no-build-isolation .
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Это установка пакета для разработки. Для сервера OWUI используются два
самостоятельных файла `dist/`, которым этот пакет и checkout не нужны.

Native runner использует отдельную БД `.native-data/helpers/webui.db`.
Он выводит фактическую версию установленного OWUI и завершает проверку ошибкой
при пропущенных cases:

```sh
.venv/bin/python tools/run_native_tests.py --suite smoke
.venv/bin/python tools/run_native_tests.py
```

`--data-root /tmp/lhr-native` задаёт только тестовую директорию БД.
Smoke проверяет версию, модель, payload, filter, binder, standalone exports
и настоящий MCP transport. Полная suite дополнительно проверяет
Tools/Skills/terminal, историю, native streaming Tool loop и parallel dispatch.
HTTP/browser приёмка описана в [tools/README.md](../tools/README.md).

Сборка единственного offline-комплекта не устанавливает зависимости:

```sh
python3 tools/build_functions.py
python3 tools/build_functions.py --destination /tmp/lhr-exports
```

В destination — ровно две `.py` Functions для 0.13.3. Python API —
`build(destination=None)`. Выбора версии, `ReleaseProfile` и legacy exports нет.
Unit export checks проверяют воспроизводимость, совпадение `dist/` с исходниками,
отсутствие конфликтующих inlined symbols и холодный импорт без checkout.
Native export checks загружают собранные файлы с настоящими API OWUI 0.13.3.
Установка и rollback описаны в [setup.md](setup.md).
