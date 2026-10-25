# OWUI Lite Handoff Router

Router для Open WebUI **0.12.3** и отдельный комплект **0.11.1**: самостоятельный ответ или передача
текущего запроса разрешённому агенту через routing Skill и `lite_delegate`.
В 0.12.3 для составной задачи `lite_parallel` собирает независимые результаты A/B,
после чего Router может передать итоговую работу C.
Агент продолжает тот же чат; новое сообщение снова получает Router.
Пользователю достаточно прав на модели: настроенные Tools/Skills/MCP загружаются
с правами владельца соответствующей Workspace Model. Личность пользователя,
его UserValves, OAuth и доступ к чатам/обычным файлам сохраняются.

Установочный комплект: один Filter и один Pipe со встроенными управляющими Tools. Runtime использует
библиотеки OWUI и доступные внутри контура модели/сервисы.

- [Установка и настройка](docs/setup.md)
- [Разработка и подготовка зависимостей](docs/development.md)
- [Проверки совместимости](docs/compatibility.md)
- [Контекст и обмен результатами](docs/context.md)
- [Разделение Skills](docs/skills.md)
- [Фактическая приёмка](docs/acceptance.md)

```sh
python3 tools/build_functions.py
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/run_native_tests.py --owui-version 0.12.3
```

Реализованный план и критерии приёмки находятся в
`openspec/changes/archive/2026-10-23-add-parallel-agent-tasks/`.

Комплект 0.11.1 поддерживает обычный handoff, локальные Tools, серверные
OpenAPI/MCP, текстовые Skills (`view_skill`) и терминал только у выбранных
моделей. Параллельные задания в нём отключены. Сборка:

```sh
python3 tools/build_functions.py --owui-version 0.11.1
```

Установите ровно два файла из `dist/owui-0.11.1/`. Для 0.12.3 прежняя команда
оставляет комплект в `dist/`. Каждый комплект отклоняет другую версию сервера;
checkout и Python-пакет Router на сервере не требуются. Команды независимых
native suites и порядок rollback — в документации установки/разработки.

По [ревью 0.11.1](openspec/changes/add-owui-0111-router/review.md) остаются
три замечания P2; задачи 3.1–3.2 открыты повторно. Этот change пока не готов к архиву.
