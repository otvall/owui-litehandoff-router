# OWUI compatibility

Разработано и проверено под **Open WebUI 0.13.0**, Python 3.11/3.12.
Все обращения к внутренним API находятся в `src/lite_handoff_router/owui_adapter.py`.
Обе Functions содержат информационную metadata `developed_for_open_webui_version`.
Номер установленной версии не проверяется и не блокирует запуск. На других
релизах совместимость определяется доступностью используемых API.

`ReleaseProfile`, адаптер 0.11.1, отдельные legacy exports и выбор версии
в builder/native runner удалены. Сохраняются три штатных Skill readers
(`view_skill`, `read_skill_file`, `grep_skill_files`), обычный `lite_delegate`
и параллельные задания с прежними defaults: 4 задания / 300 секунд.
Отключение параллельных заданий через `max_parallel_tasks=0` сохраняется.

## Терминал из карточки модели

Агент получает терминал из штатного `meta.terminalId` (Default terminal).
Выключение Terminal отключает эту привязку. Legacy `meta.lite_handoff.terminal_id`
сохраняется только как fallback, если `terminalId` отсутствует. Проверки владельца,
actor identity и отзыв подготовленных callable сохраняются.

## Переход с 0.12.5 на 0.13.0

Metadata обоих exports указывает версию разработки. Native dependency pin
закрепляет воспроизводимое тестовое окружение и не ограничивает runtime.
Прежний комплект отклонял 0.13.0 до терминального подключения; version guard удалён. Native suite запускается также в официальном
source image: runner учитывает frontend в `/app/build` и отсутствие wheel metadata.
Проверены native контракты моделей, filter pipeline, Skills, MCP, Tool refresh,
completion, delegate, child chat и terminal file results. При смене модели
удаляется также `chat_context.terminal_skill_fingerprints`; обычные поля
контекста сохраняются. Интерактивные user-shell Tools и terminal Skills/AGENTS.md
не становятся дополнительными возможностями агента.

Локальные Tools учитывают новый native флаг `is_active`: отключённый ресурс
блокируется при подготовке и при вызове ранее подготовленного callable, в том
числе после refresh. Административная конфигурация модели и её права в 0.13.0
обновляются раздельно: fixtures используют `/api/v1/models/model/access/update`
для grants, проверяя фактический отзыв перед handoff.

Фактические результаты нового релиза записываются отдельно в
[приёмке](acceptance.md). Нижеследующие результаты 0.12.5 исторические и не
заменяют проверку 0.13.0.

## Исторический переход с 0.12.3 на 0.12.5

Проверены официальные [изменения 0.12.3 → 0.12.5](https://github.com/open-webui/open-webui/compare/v0.12.3...v0.12.5)
и исходники установленного `open-webui==0.12.5`. Существенные изменения:

- Native MCP использует `mcp==2.3.0` и `httpx2`. Адаптер продолжает вызывать
  штатный `MCPClient`; тестовые серверы переведены с `FastMCP` на `MCPServer`.
  Реальный loopback transport проверяет actor auth, фильтрацию функций,
  повторные вызовы, изоляцию двух пользователей и disconnect после отмены.
- Helper `add_terminal_display_file_inline_param` удалён. Схема терминального
  `display_file` теперь берётся через `get_builtin_tool_spec(display_file)`,
  как в [native tools.py 0.12.5](https://github.com/open-webui/open-webui/blob/v0.12.5/backend/open_webui/utils/tools.py).
  Параметр `page` по-прежнему не отправляется терминальному backend.
- `execute_tool_call` принимает сохранённый `function_call` с полями `name`,
  `arguments`, `call_id`. Native protocol tests используют этот формат;
  provider Tool calls по-прежнему наблюдаются в их исходном формате.
- Native `delegate` обновил передачу изображений, а сохранение Tool output
  добавило поля времени. Проверены прежний grouped fixture против актуального
  converter и streaming handoff через оба standalone exports.

Новые встроенные возможности OWUI не добавляются в модель автоматически:
набор ресурсов по-прежнему определяется настройками Router и Workspace Model.
Права на модели проверяются от actor, права на Tools/Skills/подключения — от
owner модели. UserValves, OAuth и обычные файлы сохраняют actor identity.

## Историческая автоматическая проверка 2026-11-05

Изолированное окружение: macOS arm64, Python 3.12.10, официальный wheel
`open-webui==0.12.5` с его зависимостями; отдельная свежая SQLite БД в `/tmp`.
Исходники установленного OWUI не изменялись, рабочая БД не использовалась.

```sh
python3 tools/build_functions.py
PYTHONPATH=src /tmp/lhr-0125-venv/bin/python -m unittest discover -s tests -v
/tmp/lhr-0125-venv/bin/python tools/run_native_tests.py --data-root /tmp/lhr-alias-native-20261105
```

Результат после `fix-router-mcp-and-tool-dispatch`: **73 unit/export и 51 native tests**, без пропусков. Проверены
standalone import и guards, реальный native streaming Tool loop обычного
handoff, промпты/параметры/inlet, приватные Tools/Skills и UserValves, refresh,
MCP transport, терминальные контракты/отмена, native parallel API/worker binding,
история и файловый доступ. Hashes установочных файлов и границы проверки —
[owui-0125-validation.json](owui-0125-validation.json).

Native chain tests заменяют транспорт генерации и доставку browser events
детерминированными fixtures. Терминальный backend в этой suite заменён
проверяемыми вызовами. Полный HTTP сценарий A+B→C, настоящий shell backend,
браузерная приёмка и Inline Visualizer на 0.12.5 в этом прогоне не выполнялись.
В новых случаях native Tool loop использует оба rebuilt exports: проверены
ordinary handoff, несколько MCP-продолжений, автоматическое закрытие после
успеха/ошибок/Stop, смена фильтра и коллизии OpenAPI. MCP transport настоящий;
OpenAPI discovery и HTTP transport заменены fixtures. Для проверки cleanup
параллельной группы native child dispatch заменён тестовым адаптером, при этом
код группы и MCP transport настоящие. Native parallel API и worker binding
дополнительно проверены отдельными contracts.

Старые HTTP/browser результаты не считаются подтверждением новой версии:
[исторические проверки](compatibility-history.md), [приёмка](acceptance.md).

## MCP lifecycle, фильтры и имена Tools

Сессия MCP живёт до окончания полного native запроса. Несколько генераций
Router или handoff-агента сохраняют пригодное соединение. Stop, ошибка и
окончательное завершение автоматически закрывают локальные MCP-задачи и native
клиенты; активное ожидание и очередь завершаются без повторного исполнения.
Общий бюджет закрытия — 5 секунд от первой остановки, включая незавершённое
открытие и ожидание SDK cancellation. Отмена работника и disconnect выполняются
параллельно под одним абсолютным deadline; повторный close не продлевает срок.
Повторный cleanup безопасен; ошибка disconnect диагностируется. Завершение
локального соединения не подтверждает отмену удалённого побочного действия.

Кэш транспорта хранит исходный каталог сервера. Текущий
`function_name_filter_list` применяется при каждой подготовке и после ожидания
в очереди перед отправкой операции. После асинхронной проверки прав повторно
проверяются отмена вызова и остановка сессии: отменённый вызов не отправляется,
даже пока владелец выполняет `finally`. Строка, список и правила allow/block
обрабатываются штатным OWUI 0.13.0 helper. Отозванный callable не отправляет
запрос; смена `identity` на `hidden` обновляет видимый набор без переподключения.
Пустой фильтр сохраняет штатное разрешение, пустой разрешённый набор обязательного
подключения вызывает ошибку подготовки. Owner ACL и actor auth проверяются
независимо от фильтра и не расширяются этим механизмом.

При коллизиях local/OpenAPI и OpenAPI/OpenAPI Tools имя копии schema меняется
вместе с ключом registry. Например, для `server:one` и `server:two` с операцией
`lookup` доступны `lookup` и `server_two_lookup`. Вызов второго имени обращается
к серверу `two` с исходной операцией `lookup` и авторизацией actor. Длинные
публичные имена сокращаются до 64 символов с хешем полного кандидата; коллизия
проверяется после сокращения, включая повторные коллизии. Исходное имя операции
и аргументы сохраняются. Общие схемы
native кэша не меняются; несовпадение имени и ключа после inlet вызывает отказ
подготовки до публикации. Зарезервированные routing/Skill/terminal имена сохраняют
прежние ограничения.

## Обновление установки

На проверенном сервере OWUI импортируйте согласованную пару
`dist/router_preparation.py` / `dist/lite_handoff_router.py`, сохранив `AGENTS`
и Valves. Оба файла содержат всё ядро; пакет Router на сервере не нужен.
Обновление Functions не меняет БД и не выдаёт новые grants.
Инструкции — [setup.md](setup.md).

## Проверка OWUI 0.13.3

Исправлена передача подтверждённого native-контекста после handoff и parallel.
Память учитывает текущую политику actor и адресата; knowledge и Skills остаются
в своих областях доступа. Точная версия сервера не блокирует запуск; наличие
нового формата определяется по серверным данным. Результаты и ограничения —
[отчёт](owui-0133-context-validation.md).

До импорта сохраните обе прежние Functions, их Valves и AGENTS. Импортируйте
оба файла dist из одного build, восстановите настройки, начните новый чат и
проверьте handoff, Tools и новую реплику. Для отката восстановите оба прежних
файла и настройки вместе. Миграция данных и новые grants не требуются.
Для native Files Tools проверьте capabilities и настройки Preload files:
предзагрузка содержимого и наличие callable чтения файлов — разные настройки;
attachment не создаёт отсутствующий Tool. Проверяйте фактические schemas у
адресата, разрешённые IDs и доступ actor к файлам.
