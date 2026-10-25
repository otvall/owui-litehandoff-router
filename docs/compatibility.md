# OWUI compatibility

Поддерживается только **Open WebUI 0.12.3**, Python 3.11/3.12. Все обращения
к внутренним API находятся в `src/lite_handoff_router/owui_adapter.py`.
Проверка версии выполняется до исполнения. Для pip используется package metadata,
для официального Docker-образа без metadata — штатная `open_webui.env.VERSION`.
Оба варианта допускают только 0.12.3. Другие версии требуют отдельной проверки.

## Inline Visualizer (2026-10-23)

Подтверждены Tool **2.2.3** и согласованный Skill из snapshot
`bcc224db138b6901a8d4b99e7f8a8859bea5629b` Classic298/open-webui-plugins,
OWUI 0.12.3 (Docker Python 3.11.17), Safari 27.0.1. URLs и SHA-256 —
[manifest](../tools/fixtures/inline_visualizer/manifest.json). В Skill не требуется
отдельное поле версии. Функция 2.2.3 — `visualize`; `render_visualization` 2.1.1
используется только в отрицательном контроле и не считается совместимой.

**Runtime Router и оба exports не менялись.** Обновления Tool/Skill достаточно
для проверенного child-chat пути. Native Tool возвращается за 5.02–5.05 с без
callback основного чата; при отключённой настоящей сессии — за 0.011 с.
Message-level embed сохраняется у child, модель продолжает HTML/SVG, родитель
сохраняет прежние статусы/ссылки без embed работника. A+B и A+A сохраняют
независимые визуализации.

Подтверждены открытие по штатной ссылке, loader→готовый SVG во время генерации,
reload и новая socket-сессия того же пользователя без повторных model/Tool calls.
Кнопка меняет DOM и цвет SVG. Viewer без прямых grants на ресурсы видит своё
выполнение; постороннему child API отказывает, internal дети отсутствуют в истории.

Условия: пользовательские iframe scripts/same-origin, Tool/Skill только у
выбранного агента, локальный HTML/SVG, Valves `security_level=offline`,
`chime=false`. Router не включает same-origin. Network Inspector показал только
локальный OWUI и `about:srcdoc`; backend работал с подготовленным loopback provider.
CDN, внешние данные, другие версии плагина/OWUI и браузеры не подтверждены.

Обычные A+B→C/A+A→C и следующий самостоятельный ответ Router повторно прошли.
Отдельный Visualizer handoff в main сохранил embed; интерактивность проверена
после загрузки сохранённого ответа. До reload live-main iframe оставался на
loader; live-main rendering этим change не гарантируется. Проверка live-просмотра
данного change относится к дочернему выполнению.
Команды, IDs и раздельные backend/browser свидетельства —
[acceptance.md](acceptance.md), [browser evidence](visualizer-browser-evidence.md)
и [JSON](visualizer-evidence.json).

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

## Native parallel gate (2026-10-22)

Мост `subagents.delegate(background=False)` → публичная Router Pipe → заранее
выбранный работник проверен в отдельном Docker OWUI 0.12.3. Прототип находится
в `tools/parallel_gate*.py`; это экспериментальные Functions, не продуктовые
exports. Он не меняет `CHAT_COMPLETION_HANDLER` и `app.state.MODELS`.

У каждого дочернего запроса свой ASGI scope, registry callable и MCP-клиент.
Переданный через серверный `chat_context` ticket связывается с владельцем,
родительскими и дочерними chat/message IDs в Preparation. Router LLM в дочерних
задачах не вызывается. A/B выполняют девять настоящих Tool calls с native
continuations: три собственных Skill readers, три отказа чтения постороннего
Skill, локальный lookup, MCP и создание файла. Промпты и температуры 0.2/0.4
проверяет HTTP-провайдер; интервалы lookup перекрываются на 1.005 секунды.

Native foreground summary ограничен настройкой `subagents.max_output`.
Сборщик читает связанное сохранённое сообщение: получено 4211 символов вместо
120 символов summary, оба CSV скачаны владельцем через native files endpoint.
`done=False`, в том числе ожидание подтверждения, не считается успехом даже
при строковом ответе native delegate «Sub-agent produced no output».

Поддерживаемый режим параллельного эксперимента — Tools approval `full`.
Режим `ask` отклоняется до dispatch; принудительного переключения на `full`
нет. Отдельная проверка с включённым native approval подтвердила сохранение
паузы и отсутствие выполнения ожидающего Tool. Возобновление паузы через
новый запрос не входит в выбранный протокол.

Stop и общий deadline проверены отдельно при dispatch и внутри Tool.
После каждого прогона нет native child tasks, tickets и открытых MCP-сессий.
Cleanup должен переживать повторную отмену: native Stop может отменить и
дочернюю задачу, и ожидающего родителя. Ticket отзывается до асинхронных
операций; завершение cleanup ожидается через shield. Native события `source`
для одного родительского сообщения сериализуются, иначе конкурентные записи
могут потерять один источник.

Ссылки сохраняются native событием `source` с Markdown-документом
`[Открыть выполнение](/c/<id>)`. В стандартном просмотре Markdown они кликабельны
после Stop/reload. Относительный `source.url` сам по себе не становится ссылкой
в заголовке citation modal OWUI. Никаких изменений frontend или отдельной папки
не требуется. Браузерные наблюдения и команды — в [acceptance.md](acceptance.md).

Для удалённого дочернего чата native API отказывает в чтении, а интерфейс
возвращает на главную без уведомления. Это принятое поведение штатного
[loadChat в OWUI 0.12.3](https://github.com/open-webui/open-webui/blob/v0.12.3/src/lib/components/chat/Chat.svelte#L2706-L2716),
подтверждённое в Safari обычным пользователем. Просмотр не восстанавливает чат
и не повторяет Tools; frontend-патч не нужен.

## Условия продуктового lite_parallel

Дополнение политики ресурсов сначала проверено отдельным native gate
`tools/resource_access_gate.py` на собственной БД `.native-data/resource-access-gate`
и временном loopback MCP. Два обычных пользователя без grants на ресурсы
использовали приватный Tool с разными собственными UserValves, три Skill reader
после настоящего refresh и MCP с пользовательскими OAuth/forwarded identity.
Прямой доступ к ресурсам отклонён; отсутствие OAuth не использует токен владельца.
Точки адаптации 0.12.3: ACL и UserValves в `get_tools`, ACL и построение заголовков
в `connect_mcp_server`, отдельный контекст владельца только у Skill readers.
Тем же gate повторно проверен продуктовый адаптер с настоящим MCP transport;
установка двух exports с новой политикой подтверждена отдельно в отчёте приёмки.

Актуальные два exports используют подтверждённый мост, без дополнительных
runtime-пакетов. Параллельная работа требует сохранённого основного чата,
пользователя с актуальными правами на модели, `subagents.enable=true`, режима Tools approval
`full` и отключённого context compaction. Административные defaults — четыре
задания и 300 секунд; `max_parallel_tasks=0` отключает новый Tool и сохраняет
обычный handoff. Фактическая конкурентность зависит также от native лимитов
OWUI и мощности провайдера. При неподдерживаемом режиме Tool не публикуется,
а попытка старого callable всё равно проходит новую проверку условий.

Общий deadline отменяет незавершённых работников. Cleanup ждёт освобождения
ресурсов с отдельным ограничением 15 секунд, MCP graceful close — пять секунд
с последующей отменой в задаче владельца SDK. Привязка chat metadata и запись
ссылки тоже защищены от повторной отмены: нельзя оставлять незавершённую SQLite
транзакцию при native каскадном удалении. Это проверено на SQLite WAL официального
образа. Child tasks останавливаются штатным endpoint, включая pending approvals.

Состояние и tickets существуют в процессе живого родительского запроса.
Рестарт, фоновое resume и перенос группы между процессами не поддерживаются.
Сохранённые чаты/результаты остаются доступными как данные. Контракт
`lite_delegate` v2 не менялся. Ошибка или неполный исход группы запрещает handoff;
контекстная ошибка C передаётся без пересказа, усечения и повторения работников.

Ресурсы загружаются по правам владельца соответствующей Workspace Model,
а UserValves, __user__, пользовательские OAuth/session auth и forwarded headers
остаются у пользователя чата. Версионный адаптер использует native plugin loader,
cache, административные Valves и binder локальных Tools, отдельно проверяя ACL
владельца и загружая только UserValves исполнителя. Даже ошибочные UserValves
владельца не влияют на запуск. Для MCP разделены ACL и построение заголовков
`connect_mcp_server`; глобальные функции и каталоги OWUI не подменяются.
OpenAPI серверные Tools используют native спецификации, фильтры имён,
построение заголовков и исполнитель с тем же разделением ролей.

Результаты: 44 обычные проверки, 25 native helpers/loop, HTTP A+B→C и A+A→C
для двух групп, файлы, destination inlets, Stop/deadline/cascade delete,
просмотр обеих групп в Safari. Все вызовы внутреннего OWUI API находятся в
`owui_adapter.py`; native тест отдельно проверяет эту границу и version guard.
