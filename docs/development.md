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

Это установка пакета для разработки. Для сервера Router используются две
самостоятельные Functions из `dist/`; дополнительный помощник устанавливается
как обычная Workspace Model со своим standalone Tool. Пакет и checkout
на сервере для этих комплектов не нужны.

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

Сборка offline-комплекта Router не устанавливает зависимости:

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

## Комплект автора routing Skills

Отдельная сборка не меняет Functions Router и не скачивает зависимости:

```sh
python3 tools/build_authoring_agent.py
python3 tools/build_authoring_agent.py --destination /tmp/lhr-authoring
```

Результаты: standalone Workspace Tool `routing_skill_authoring.py`,
импортируемый Skill `routing-skill-authoring.zip` и общий комплект
`routing-skill-authoring-agent.zip` с промптом и инструкцией. Сборка встраивает
основной шаблон `docs/templates/routing-skill/SKILL.md` и рендерер из
`src/lite_handoff_router/routing_skill_renderer.py`. Переносимая копия скрипта
в `docs/skills/routing-skill-authoring/scripts/` должна совпадать с исходником;
копия шаблона в `references/` — с основным шаблоном. ZIP не включает Python-кэши,
порядок файлов и timestamps фиксированы для воспроизводимости.

Unit-проверки `tests/test_authoring.py`, `tests/test_routing_skill_renderer.py`
и `tests/test_skill_manifest.py` входят в обычный unittest discovery. Они
проверяют standalone-импорт, содержимое и воспроизводимость архивов, рендер,
страницы файлов и передачу названий агентов в Router.
`tests/native/test_authoring.py` входит в полную native suite и проверяет
штатные schemas/ZIP parser, права чтения/записи, отсутствие исполнения
чужого Tool-кода, сохранение Skills и конфликт версий в изолированной БД.
Эти проверки не заменяют диалог с выбранной LLM и приёмку вашей установки.
Порядок установки — [routing-author-agent.md](routing-author-agent.md).

Для отдельного native-прогона помощника без остальных контрактов Router:

```sh
PYTHONPATH=src:tools .venv/bin/python - <<'PY'
from native_environment import configure
configure("authoring")
from open_webui import config
import unittest
suite = unittest.defaultTestLoader.discover("tests/native", pattern="test_authoring.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
PY
```

Локальная проверка 2026-11-13: unit suite и отдельные контракты помощника
проходят; полный native runner не проходит на установленной сборке OWUI,
которая сообщает `0.0.0` и имеет несовпадения API/MCP с тестами Router.
Этот прогон не устанавливает совместимость всего Router с официальной 0.13.3.

Специализированный `delegate_task` использует штатный dispatch OWUI 0.13.3:
`process_chat_response` запускает вызовы этого имени через `asyncio.gather`
и сам связывает outputs с исходными call IDs. ID не передаётся callable.
Native contract `test_native_delegate_dispatch_owns_parallelism_and_call_ids`
проверяет перекрытие двух одинаковых назначений и оба сохранённых call IDs.
