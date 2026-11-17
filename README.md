# OWUI Lite Handoff Router

Router для Open WebUI **0.13.3**: самостоятельный ответ или передача
текущего запроса разрешённому агенту через routing Skill и `lite_delegate`.
Для составной задачи `delegate_task` собирает независимые результаты A/B,
после чего Router может передать итоговую работу C.
Агент продолжает тот же чат; новое сообщение снова получает Router.
Пользователю достаточно прав на модели: настроенные Tools/Skills/MCP загружаются
с правами владельца соответствующей Workspace Model. Личность пользователя,
его UserValves, OAuth и доступ к чатам/обычным файлам сохраняются.

Установочный комплект: один Filter и один Pipe со встроенными управляющими Tools. Runtime использует
библиотеки OWUI и доступные внутри контура модели/сервисы.

`tool_result_history_limit` в Router Preparation сохраняет N свежих рабочих
Tool results в контексте модели, заменяя старые плашкой без изменения чата.
История передач остаётся в отдельных Tool-сообщениях, Router получает один
system с XML-каталогом routing Skills. [Пример запроса](docs/context.md#пример-третьего-запроса-router).
Каталоги разделены на `routing_skills` и штатный по форме `available_skills`;
обычные Skills Router доступны из конфигурации, карточки модели и выбора в чате.

- [Установка и настройка](docs/setup.md)
- [Промпт Router, routing Skills и общие ресурсы](docs/router-configuration.md)
- [Разработка и подготовка зависимостей](docs/development.md)
- [Проверки совместимости](docs/compatibility.md)
- [Контекст и обмен результатами](docs/context.md)
- [Разделение Skills](docs/skills.md)
- [Агент для создания routing Skills](docs/routing-author-agent.md)
- [Фактическая приёмка](docs/acceptance.md)

```sh
python3 tools/build_functions.py
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/run_native_tests.py
```

Реализованный план и критерии приёмки находятся в
`openspec/changes/archive/2026-10-23-add-parallel-agent-tasks/`.

Разработано и проверено под Open WebUI 0.13.3. Для Router установите Functions
`dist/router_preparation.py` и `dist/lite_handoff_router.py`.
Отдельный Tool `dist/routing_skill_authoring.py` устанавливается по инструкции помощника.
Версия указана информационно в metadata; runtime не ограничивает запуск по номеру версии.
При обновлении сохраните и восстановите `AGENTS` и Valves обеих Functions.
