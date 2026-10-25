# Development

Целевые среды: Open WebUI **0.12.3** и **0.11.1**, Python 3.11 или 3.12. Runtime
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

Native runner всегда требует явную версию и использует отдельную БД
`.native-data/<версия>/helpers/webui.db`. Он проверяет точную native версию
до discovery и завершает проверку ошибкой при пропущенных cases:

```sh
.venv/bin/python tools/run_native_tests.py --owui-version 0.12.3 --suite smoke
.venv/bin/python tools/run_native_tests.py --owui-version 0.12.3
```

Для 0.11.1 используйте отдельный официальный образ с его собственными
зависимостями. Не устанавливайте их поверх `.venv` 0.12.3 и не подключайте
data volume другого сервера:

```sh
docker run --rm --entrypoint python -e PYTHONPATH=/app/backend \
  --mount type=bind,source="$PWD",target=/work,readonly \
  ghcr.io/open-webui/open-webui:v0.11.1 \
  /work/tools/run_native_tests.py --owui-version 0.11.1 \
  --suite smoke --data-root /tmp/lhr-native
```

Уберите `--suite smoke` для полной suite нужной версии. Smoke проверяет
версию, модель, payload, filter и binder; он не заменяет полную приёмку
Tools/Skills/MCP/terminal и native HTTP loop. Runner собирает временную
копию source с фиксированным профилем, не меняя checkout или серверные
настройки; `--data-root` задаёт только тестовую директорию БД.

Сборка двух независимых offline-комплектов не устанавливает зависимости:

```sh
python3 tools/build_functions.py
python3 tools/build_functions.py --owui-version 0.11.1
python3 tools/build_functions.py --owui-version 0.12.3 --destination /tmp/lhr-exports-0123
python3 tools/build_functions.py --owui-version 0.11.1 --destination /tmp/lhr-exports-0111
```

В каждом destination — ровно две `.py` Functions. Default Python API
`build(destination)` продолжает выбирать 0.12.3, второй аргумент задаёт target.
Неизвестный target отклоняется до создания/перезаписи outputs. Unit export
checks проверяют воспроизводимость, отсутствие конфликтующих inlined symbols,
холодный импорт без checkout и фиксированный профиль. Native export checks
дополнительно проверяют guard на реальной версии, включая отказ чужого комплекта.
Установка и rollback описаны в [setup.md](setup.md); downgrade БД не нужен.
