# OWUI Lite Handoff Router

Один Router для Open WebUI **0.12.3**: самостоятельный ответ или передача
текущего запроса разрешённому агенту через routing Skill и `lite_delegate`.
Агент продолжает тот же чат; новое сообщение снова получает Router.

Установочный комплект: один Filter, один Pipe, один Tool. Runtime использует
библиотеки OWUI и доступные внутри контура модели/сервисы.

- [Установка и настройка](docs/setup.md)
- [Разработка и подготовка зависимостей](docs/development.md)
- [Проверки совместимости](docs/compatibility.md)
- [Контекст и границы MVP](docs/context.md)
- [Разделение Skills](docs/skills.md)
- [Фактическая приёмка](docs/acceptance.md)

```sh
python3 tools/build_functions.py
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/run_native_tests.py
```

Активный план и критерии приёмки находятся в
`openspec/changes/add-skill-based-handoff-router/`.
