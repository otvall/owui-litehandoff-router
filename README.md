# OWUI Lite Handoff Router

Один Router для Open WebUI **0.12.3**: самостоятельный ответ или передача
текущего запроса разрешённому агенту через routing Skill и `lite_delegate`.
Для составной задачи `lite_parallel` собирает независимые результаты A/B,
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
.venv/bin/python tools/run_native_tests.py
```

Реализованный план и критерии приёмки находятся в
`openspec/changes/archive/2026-10-23-add-parallel-agent-tasks/`.
