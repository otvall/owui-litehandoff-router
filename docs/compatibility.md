# OWUI compatibility

Поддерживается только **Open WebUI 0.12.3**, Python 3.11/3.12. Все обращения
к внутренним API находятся в `src/lite_handoff_router/owui_adapter.py`.
Проверка версии выполняется до исполнения. Для pip используется package metadata,
для официального Docker-образа без metadata — штатная `open_webui.env.VERSION`.
Оба варианта допускают только 0.12.3. Другие версии требуют отдельной проверки.

Обычные тесты и тесты двух самостоятельных exports:

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Проверки с настоящими импортированными helpers установленного OWUI:

```sh
.venv/bin/python tools/run_native_tests.py
```

Runner создаёт отдельную БД `.native-data/`, использует frontend из wheel OWUI
и отключает загрузку ML-моделей. Рабочую БД пользователя он не подключает.
`tests/native/test_adapter.py` проверяет async-контракты, настоящее обновление
callable и штатную подготовку промпта/параметров для повторного вызова.
Это проверки helpers; сквозной native Tool loop и браузерная приёмка
учитываются отдельно в `docs/acceptance.md`.

Контракты сверены с [utils/tools.py](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/tools.py),
[utils/payload.py](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/payload.py),
[utils/chat.py](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/chat.py)
тега v0.12.3. Native continuation вызывает completion с
`bypass_system_prompt=True`; Pipe самостоятельно готовит промпт выбранной модели
и явно сохраняет этот флаг при вызове провайдера, чтобы не добавить его повторно.
