# Один Pipe вместо Preparation Filter и delegate Tool: исследование OWUI 0.12.3

Дата: 2026-10-22. Область: штатный streaming Tool loop Open WebUI, без изменения OWUI и без собственного цикла исполнения Tools внутри Pipe.

Историческое состояние исходников в этом отчёте — коммит `92fc3c9`, до переноса delegate. В дальнейшем inline delegate реализован и проверен с сохранением текущего Filter; результаты приведены в [acceptance.md](acceptance.md#перенос-lite_delegate-внутрь-pipe). Удаление самого Filter по-прежнему не реализовано.

**Вывод:** разместить реализацию delegate callable внутри Pipe технически можно. Однако простое перемещение `begin()` из Filter в `Pipe.pipe()` не сохраняет общий контракт. В штатной UI-ветке внешний loop и Pipe получают разные словари metadata. Если до вызова Pipe не появился общий `tools` registry, поздняя регистрация только в `__metadata__` или `request.state.metadata` не достигает registry внешнего loop. Это следует из исходников и подтверждено узкой HTTP-пробой основного агента. Регистрация callable внутри Pipe при уже существующем общем registry также подтверждена этой пробой; полная эквивалентность Router/Agent продукта одним Pipe не проверена.

Исследовательский агент выполнил чтение исходников и статическую сверку wheel. HTTP-пробу выполнил **основной агент**, её результаты вынесены в раздел 6. Проба использует scripted SSE вместо настоящей LLM; она не является проверкой переработанного Lite Handoff Router или полной браузерной приёмкой.

## 1. Основание и обозначения

Прочитаны установленные файлы из `.venv/lib/python3.12/site-packages/open_webui/`. Установленный distribution сообщает `Version: 0.12.3` в `open_webui-0.12.3.dist-info/METADATA:3`. Для пяти основных файлов проверено совпадение SHA-256 с их записями wheel `RECORD`; они не являются локальными изменёнными копиями. Открыты соответствующие первичные исходники официального тега `v0.12.3`:

| Файл OWUI | Существенные строки установленного файла | Первичный источник |
| --- | --- | --- |
| `main.py` | 1321–1365, 1635–1686, 1817–1864, 1910–1914 | [tag v0.12.3](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/main.py#L1321-L1365) |
| `functions.py` | 196–214, 219–262, 280–326 | [tag v0.12.3](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/functions.py#L196-L326) |
| `utils/chat.py` | 148–172, 280–282 | [tag v0.12.3](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/chat.py#L148-L172) |
| `utils/middleware.py` | 2021–3014, 3035–3139, 4100–4138, 5550–6065 | [tag v0.12.3](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L2021-L3014) |
| `utils/tools.py` | 296–342, 418–446 | [tag v0.12.3](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/tools.py#L296-L342) |

Ниже `R` — metadata, созданная HTTP endpoint; `M` — metadata отдельной UI-генерации; `F` — metadata после merge в `utils/chat.py`; `T` — вложенный исполняемый registry Tools. Это обозначения объектов для объяснения ссылок, а не новые API.

## 2. Доказанные факты: жизненный цикл и ссылки

### 2.1. В обычной UI-ветке `request.state.metadata` не равен metadata внешнего loop

Endpoint сначала присваивает `request.state.metadata = R` и `form_data['metadata'] = R` (`main.py:1635–1636`). Затем при наличии `session_id` и `chat_id` входит в fan-out. Для каждой записи модели он создаёт **новый** `M` через распаковку `R`, отдельно копирует `chat_context`, добавляет `message_id` и `task_id`; новый `model_form_data` получает именно `M` (`main.py:1817–1864`). Эта ветка применяется и при единственной выбранной модели: проверка не требует нескольких моделей. [Источник: endpoint и fan-out](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/main.py#L1635-L1686), [создание per-model metadata](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/main.py#L1817-L1864).

Внутри `process_chat()` в `process_chat_payload()` и затем в `build_chat_response_context()` передаётся `M` (`main.py:1667–1684`). `build_chat_response_context()` кладёт переданный объект непосредственно в `ctx['metadata']` (`middleware.py:3035–3056`); streaming handler читает его оттуда (`4100–4108`). В изученной цепочке OWUI не переназначает `request.state.metadata` на `M`. Следовательно, изначально `request.state.metadata is R`, а `ctx['metadata'] is M`. [Источник: response context](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L3035-L3057), [streaming handler](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L4100-L4108).

У legacy/direct-ветки другая топология: она добавляет `message_id` в исходную metadata и передаёт её в `process_chat()` без per-model копии (`main.py:1910–1914`). Поэтому helper-проверка или HTTP-запрос без UI fan-out может показать успешную root-публикацию, которая не доказывает её работу в обычном saved-chat UI. [Источник: legacy/direct](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/main.py#L1910-L1918).

### 2.2. Следующая копия отделяет metadata Pipe от `M`

`utils/chat.py:165–172` при наличии metadata в body создаёт `F` распаковкой body metadata и `request.state.metadata`; значения из `request.state.metadata` имеют приоритет. Это новая верхнеуровневая dict, без глубокого копирования вложенных значений. Присваивание изменяет `form_data['metadata']`, но не локальную переменную `metadata=M` у `process_chat()`. [Источник: metadata merge](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/chat.py#L165-L172).

`functions.py:219–226` затем поверхностно копирует весь `form_data` и извлекает из **своей копии** metadata. В `Pipe.pipe()` приходят `body` без поля metadata и отдельный `__metadata__=F`; доступные injections включают `__request__` и `__tools__`. При отсутствии ключа `tools` `__tools__` получает отдельную пустую dict из default, а не зарегистрированный в `M` контейнер (`functions.py:248–262`). Аргументы отбираются по сигнатуре Pipe (`196–204`). [Источник: body и injections](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/functions.py#L196-L262).

Таким образом, обычные аргументы Pipe не предоставляют прямую ссылку ни на внешний `form_data`, ни на `M`. Присваивание `body['metadata']` действует только на копию body. Переназначение `request.state.metadata` не переназначает уже сохранённую `ctx['metadata']`.

### 2.3. Streaming Pipe выполняется после построения внешнего context

Для streaming `generate_function_chat_completion()` создаёт `stream_content()` и возвращает `StreamingResponse`, а `execute_pipe()` находится внутри тела генератора (`functions.py:286–323`). Сам Pipe ещё не исполнен в момент возврата этого `StreamingResponse`. Endpoint после получения response строит context и передаёт response в `process_chat_response()` (`main.py:1673–1686`). Внешний streaming handler читает `ctx['metadata']` и лишь затем потребляет body response (`middleware.py:4100–4138`, `5550`). [Источник: ленивый Pipe](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/functions.py#L286-L323), [порядок endpoint](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/main.py#L1673-L1686), [потребление stream](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L5548-L5564).

Важно: `build_chat_response_context()` не замораживает отдельную копию Tools. Если позднее изменить доступный ему `M` или общий вложенный `T`, loop увидит изменение. Проблема заключается в доступности нужного объекта, а не в абстрактном запрете менять metadata после создания context.

### 2.4. Почему ранний `metadata['tools']` помогает

Если до merge уже существует `M['tools']=T`, поверхностное копирование сохраняет ссылку на `T` в `F`, если `R` не содержит другой `tools`, который её перекроет. Тогда in-place изменение `T` из Pipe наблюдаемо внешним loop. Если ключа не было, позднее присваивание `F['tools']=T` его не создаёт в `M`. Аналогично, позднее добавление ключа только в `R` не добавляет его в уже созданный `M`. Это прямое следствие перечисленных присваиваний и поверхностных копий.

| Ситуация перед первым Pipe | Что видит внешний `M` после поздней регистрации |
| --- | --- |
| `M['tools']` и `F['tools']` уже указывают на один `T`; Pipe меняет `T` in-place | Обновлённый исполняемый registry |
| В `M` Tools отсутствуют; Pipe добавляет только `F['tools']` | Ключ в `M` по-прежнему отсутствует |
| В UI `R` и `M` разные; Pipe добавляет только `R['tools']` | Ключ в `M` по-прежнему отсутствует |
| Legacy/direct: `R` является metadata context; Pipe меняет этот `R` | Изменение доступно этому context; это отдельная ветка |

Наличие раннего `T` иногда обеспечивает штатный preprocessing: middleware записывает `metadata['tools']` при непустом `tools_dict` (`2907–2910`), включая доступные builtins (`2811–2849`). Но запись условна; `use_builtin_tools` зависит от режима, `session_id` и capability (`2076–2079`), а категория/разрешения встроенных Tools дополнительно проверяются в `get_builtin_tools()` (`utils/tools.py:601–655`). Нельзя считать существование `T` безусловным свойством любого Pipe request. [Источник: ранний registry](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L2808-L2919), [выбор builtins](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L2074-L2083), [ограничения builtins](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/tools.py#L601-L655).

Есть явный запрос, который не получает такой seed от штатного resolver: `tools: []`. Middleware сохраняет `payload_tools` до Filter (`2062`), а всю server-side resolution выполняет только при `payload_tools is None` (`2642–2645`). Это также объясняет, почему изменение `body['tools']` поздним Filter не меняет уже снятый snapshot `payload_tools`. При исходном `tools: []` нельзя рассчитывать на builtins как неявный источник общего `T`. [Источник: snapshot](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L2059-L2064), [отключение server-side resolution](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L2640-L2654).

## 3. Доказанные факты: schemas и исполнение — разные контракты

`form_data['tools']` содержит публичные schemas для LLM (`middleware.py:2916–2919`). Внешний loop при разборе вызова использует **другой** канал: `resolve_tool_call(..., metadata.get('tools', {}))` (`5624–5652`). Затем `execute_tool_call()` снова получает Tool из metadata, ограничивает аргументы schema properties и для server-side Tool вызывает его `callable` (`3060–3092`, `3128–3136`). [Источник: schemas](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L2907-L2922), [dispatch внешнего loop](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L5624-L5652), [server-side execution](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L3060-L3139).

Следовательно, отправка `lite_delegate` schema провайдеру сама по себе не регистрирует исполняемый Tool в middleware. Для server-side delegate необходим Tool entry с `spec`, `callable` и доступными ему доверенными request/user/state. Сам callable не обязан поступать из отдельного upload типа Tools: исполнитель использует готовый entry и не требует `tool_id` на ветке server-side dispatch. Штатный loader создаёт такие entries и привязывает injections через async wrapper (`utils/tools.py:418–446`); при исполнении OWUI обновляет `__messages__`/`__files__` у поддерживающих это wrappers (`331–342` и `middleware.py:3128–3136`). [Источник: Tool entry](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/tools.py#L418-L446), [binding и refresh](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/tools.py#L296-L342).

**Вывод:** отдельный delegate Tool upload — вопрос упаковки и ранней доступности callable, а не обязательное условие самого native executor. Встроенный в Pipe callable можно передать executor, если сначала решена публикация именно в его registry.

## 4. Доказанные факты: ранние стадии не переносятся эквивалентно

### 4.1. Native mode выбирается раньше Pipe

Endpoint формирует `metadata['params']['function_calling']` из request params, настроек модели и default `native` (`main.py:1353–1364`). `process_chat_payload()` вычисляет `use_builtin_tools` ещё до Filter inlet (`middleware.py:2076–2079` против `2315–2322`). Далее до Pipe исполняются ветвления legacy/native для knowledge, web search, image generation, code interpreter и Tools (`2249–2263`, `2362–2405`, `2912–2928`). [Источник: initial mode](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/main.py#L1353-L1364), [ранний builtin mode](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L2074-L2083), [legacy/native ветки](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L2354-L2405).

Поэтому позднее присваивание `function_calling='native'` внутри Pipe не отменяет уже выполненные legacy-процедуры. Даже нынешний Preparation Filter расположен после раннего вычисления `use_builtin_tools`; его присваивание native не исправляет эту раннюю величину задним числом. Для точной гарантии native нужно обеспечивать на входе endpoint/в настройках Workspace Model, до payload preprocessing.

### 4.2. Pipe получает историю позже нынешнего Preparation Filter

Уже **до нынешнего Filter** OWUI может загрузить историю из DB (`2085–2113`), выполнить compaction (`2140–2161`), преобразовать structured output и отремонтировать пары Tools (`2169–2182`), обработать system prompt и изображения (`2184–2193`). Нельзя называть snapshot нашего Filter исходным HTTP payload до любого preprocessing. [Источник: история перед Filter](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L2069-L2193).

Однако Filter inlet (`2315–2322`) находится **до** feature/memory processing (`2326–2405`), skills context и удаления skill mentions (`2460–2619`), MCP instructions (`2678–2683`), file source context (`2933–2963`) и финального `normalize_messages_for_model()` (`3012`). Pipe запускается после всех этих стадий. [Источник: inlet](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L2303-L2327), [skills](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L2460-L2619), [конец payload processing](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L2933-L3014).

Наш `begin()` копирует `body['messages']` в `source` и `original_source`, фиксирует `user_boundary` (`src/lite_handoff_router/runtime.py:14–26`), а `refresh_source()` сохраняет этот префикс при native continuation (`history.py:98–108`). Перенос snapshot в Pipe меняет эти входные данные; позднее удаление system messages не обращает все изменения user/assistant/tool сообщений. Получить прежний snapshot из обычных аргументов Pipe без дополнительного раннего capture не гарантировано. [Наш runtime](../src/lite_handoff_router/runtime.py), [наша история](../src/lite_handoff_router/history.py).

### 4.3. Native continuation — повторный Pipe в том же запросе, не новый user turn

После Tool results middleware строит continuation с исходным `model_id`, `stream=True` и своей metadata (`5895–5901`), добавляет Tool messages, а затем вызывает `generate_chat_completion(..., bypass_system_prompt=True)` (`6035–6042`). Это повторно приводит в router Pipe через `utils/chat.py:280–282`; полный `process_chat_payload()` для каждой итерации этого loop не вызывается. [Источник: continuation envelope](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L5893-L5901), [следующий вызов модели](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L6035-L6065), [Pipe dispatch](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/chat.py#L280-L282).

Наш `begin()` каждый раз создаёт новые `request_id`, пустые `pending`, `active_agent=None`, `generation=0` и audit (`runtime.py:14–27`); `delegate()` записывает pending marker, а `confirmed_target()` требует согласованную завершённую пару callable/result (`protocol.py:56–94`). Поэтому вызывать `begin()` при каждом входе в Pipe нельзя: это стирает свидетельство handoff перед следующей итерацией. [Наш runtime](../src/lite_handoff_router/runtime.py), [наш протокол](../src/lite_handoff_router/protocol.py).

Инициализировать один раз на доверенный request lifecycle и сохранять state при native continuation — необходимое условие варианта Pipe-only. Сброс на следующем HTTP user request должен опираться на серверный scope/идентичность запроса, а не на клиентскую metadata receipt. UI Continue/approval resume в новом HTTP request — отдельный сценарий от continuation внутри одного streaming loop.

### 4.4. Approval resume может исполнять Tool ещё до первого Pipe

При обнаружении pending assistant response preprocessing вызывает `resume_tool_calls()` (`middleware.py:2988–2990`), а тот вызывает `execute_tool_call()` (`3228–3229`). Всё это происходит до `chat_completion_handler()` и первого Pipe (`main.py:1667–1673`). [Источник: вызов resume](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L2988-L2996), [исполнение resumed Tool](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L3219-L3238).

Поэтому delegate/agent Tools, существующие только в поздней регистрации Pipe предыдущего HTTP request, не становятся автоматически доступными новому approval-resume request. Ранний registry из builtins сам по себе не создаёт в нём `lite_delegate`. Полную поддержку сохранённых approvals нельзя доказывать одним успешным streaming handoff с режимом `full`.

Это ограничение нельзя представлять как доказанное отличие Pipe-only от нынешней реализации: нынешний Filter вызывает `begin()`, который создаёт пустой registry, а Router/Agent callables публикуются позднее в Pipe. Следовательно, и текущую схему нужно отдельно проверить на saved-chat approval resume; наличие Preparation Filter само по себе не доказывает эту поддержку (`runtime.py:28–43`, `entrypoints/lite_handoff_router.py:29–41`). [Наш runtime](../src/lite_handoff_router/runtime.py), [наш Pipe](../src/lite_handoff_router/entrypoints/lite_handoff_router.py).

## 5. Что именно обеспечивает существующая схема

| Наш файл | Ответственность, существенная для объединения |
| --- | --- |
| [`entrypoints/router_preparation.py`](../src/lite_handoff_router/entrypoints/router_preparation.py), строки 21–29 | Выполняет discovery и `begin()` на Filter inlet. |
| [`runtime.py`](../src/lite_handoff_router/runtime.py), строки 14–43 | Создаёт доверенный scope state; сохраняет корневую per-model metadata в `scope['lite_handoff_metadata']`; заранее создаёт Tools container; сохраняет source; удаляет selected tool/skill IDs; задаёт native для последующих стадий. |
| [`runtime.py`](../src/lite_handoff_router/runtime.py), строки 47–80 | Проверяет receipt против server scope; публикует инструменты in-place в Tools container корневой metadata и синхронизирует metadata. |
| `entrypoints/lite_delegate.py` из коммита `92fc3c9`, строки 8–17 | Предоставляет callable, который проверяет server binding и выполняет протокол pending handoff; позднее отдельный entrypoint удалён. |
| [`model_execution.py`](../src/lite_handoff_router/model_execution.py), строки 53–81, 95–115 | Загружает исполняемые capabilities; сегодня требует отдельно загруженный `lite_delegate` среди base Tools; ограничивает `agent_id`; строит schemas и registry; защищает reserved Tools от подмены inlet. |
| [`entrypoints/lite_handoff_router.py`](../src/lite_handoff_router/entrypoints/lite_handoff_router.py), строки 20–53 | Требует подготовленный state, сохраняет continuation source, выбирает подтверждённую цель, готовит модель, публикует registry и возвращает provider response внешнему loop. |

Если убрать только delegate upload, его callable и schema можно строить внутри router implementation, сохранив существующий ранний Filter. Для этого придётся заменить зависимость `prepare_model()` от загруженного `lite_delegate` и сохранить binding/validation; это локализованное изменение, не требующее замены native executor. Это вывод о направлении реализации, не выполненное изменение.

Один upload с классами `Pipe` и `Filter` не даёт двух штатных lifecycle registrations: загрузчик выбирает `Pipe` первым и возвращает type `pipe`; `Filter` проверяется через `elif` (`utils/plugin.py:296–304`). [Источник: выбор класса](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/plugin.py#L296-L304).

## 6. Узкая runtime-проверка, выполненная основным агентом

Основной агент выполнил HTTP-пробу на настоящем Docker Open WebUI 0.12.3. Создавались отдельные скрытые probe Models и Functions; запрос шёл через `/api/chat/completions` с сохранённым chat, `session_id` и одной моделью, то есть через настоящий endpoint fan-out. Пробный Pipe возвращал scripted SSE с вызовом `probe_echo`; callable записывал отдельный marker исполнения и возвращал `EXECUTED_42`. Следующий вход Pipe возвращал полученный Tool result. Штатные middleware и исполнитель не подменялись mocks. Код и результаты доступны в локальных артефактах: [probe script](../.cache/pipe-registration-probe.py), [results JSON](../.cache/pipe-probe-results.json).

| Регистрация внутри пробного Pipe | Раннее состояние | Записанных исполнений callable | Статус Tool в сохранённом output |
| --- | --- | ---: | --- |
| Заменить `__metadata__['tools']` новым dict | Builtins создали registry | 0 | `failed`, Tool not found |
| Изменить существующий registry in-place | Builtins создали registry | 1 | `completed`, `EXECUTED_42` |
| `setdefault` и in-place изменение | Исходный payload содержит `tools: []`, раннего registry нет | 0 | `failed`, Tool not found |
| То же плюс записать registry в `request.state.metadata` | Исходный payload содержит `tools: []`, раннего registry нет | 0 | `failed`, Tool not found |
| In-place изменение после микро-Filter с `metadata.setdefault('tools', {})` | Исходный payload содержит `tools: []` | 1 | `completed`, `EXECUTED_42` |

Во всех пяти случаях observations фиксируют разные identity Pipe metadata и `request.state.metadata`. В случае root-публикации на следующем входе Pipe **и Pipe metadata, и root уже содержат** `probe_echo`, но внешний loop всё равно возвращает Tool not found и execution marker отсутствует. Это практически различает registry, доступный Pipe/root, и registry внешнего per-model context. [Источник наблюдений: JSON](../.cache/pipe-probe-results.json).

Проба подтверждает узкий контракт регистрации и исполнения server-side callable: общий ранний container плюс in-place публикация работают; его позднее создание или замена не обеспечивают исполнение во внешнем loop. Это не только проверка отправленной schema: отдельно измерено фактическое исполнение callable. Она не проверяет discovery, настоящий `lite_delegate`, смену прав Router→Agent, scoped Skills, внешние Tools/MCP, reset нового user turn или approvals. Установленный Lite Handoff Router и его исходники пробой не изменялись. Созданные probe Functions/Models удалены основным агентом в `finally`; результаты сохранены как исследовательские артефакты. [Код probe и cleanup](../.cache/pipe-registration-probe.py).

## 7. Гипотезы и варианты, которые требуют дополнительных проверок

1. **Pipe-only при гарантированном раннем `T`.** Регистрация пробного callable по этому механизму подтверждена разделом 6. Возможность взять общую ссылку из `__metadata__['tools']`, зарегистрировать inline delegate in-place и отдавать provider stream штатному loop следует из того же контракта. Для полного продукта остаются проверки binding injections, смены Router→Agent registry, второго user turn и ошибок. Поддержка произвольного request без раннего registry этим вариантом не обеспечивается.
2. **Использовать только `request.state.metadata` как root.** Для legacy/direct топологии это может обеспечить публикацию; для обычного UI fan-out без `T` исходники показывают, почему этого недостаточно. Положительный helper-test без endpoint fan-out здесь не переносится на UI.
3. **Найти внешний `M` косвенно.** Изученные штатные аргументы Pipe не выдают его напрямую. Обход frame/closure/GC, monkey-patching middleware или изменение общих классов контейнеров был бы отдельным внутренним вмешательством, а не установленным контрактом Pipe API. Такое решение здесь не исследовано и не предлагается как подтверждённое.
4. **Добавить ранний hook в OWUI.** Если OWUI до metadata merge передавал бы Pipe ссылку на per-model context либо eagerly исполнял preparation hook, можно было бы инициализировать state и registry в одном package. В проверенной версии обычный `Pipe.pipe()` такого hook не предоставляет; это предполагаемое изменение OWUI.

Отдельный промежуточный вариант — оставить маленький Filter, который только создаёт/сохраняет общий Tools container, а discovery, state initialization и delegate implementation перенести в Pipe. Его пригодность для публикации пробного callable подтверждена разделом 6. Он сохраняет две точки установки и не решает автоматически проблему более раннего history snapshot или approval resume. Переработанная реализация Router по этому варианту здесь не создавалась.

Для всех вариантов native должен быть выбран до preprocessing, а история и approvals требуют самостоятельного решения. Перенести весь Tool loop внутрь Pipe можно обсуждать отдельно, но это меняет поставленное условие сохранения штатного внешнего loop.

## 8. Матрица проверок для решения о полном Router

Раздел 6 покрывает только отмеченные там probe cases. Для решения о полном Router нужна следующая матрица:

- Реальный endpoint fan-out с одной моделью, `session_id` и `chat_id`: различить `R`, `M`, `F`; записать identity и наличие Tools перед/после lazy Pipe.
- Ветку с `builtin_tools=False`, без selected Tools и без раннего `metadata['tools']`: отдельно показать schema у провайдера и фактическую доступность callable внешнему executor.
- Исходный request с `tools: []`: повторить отсутствие registry без зависимости от глобальной конфигурации встроенных Tools; затем сравнить с ранним микро-Filter, сохраняющим identity контейнера.
- Ветку с ранним registry: доказать `M['tools'] is F['tools']`, затем Router Tool → inline delegate → Agent Tool → финальный ответ без собственного loop.
- Legacy/direct: учесть как отдельный положительный/отрицательный контроль, не как замену UI-проверки.
- Два последовательных user turns и несколько native continuation одного turn: state сбрасывается между запросами и сохраняется внутри streaming loop.
- Input `legacy`: проверить, где именно изменился режим и какие ранние preprocessing стадии уже были выполнены.
- Saved-chat approval pause/resume в новом HTTP request: проверить доступность inline delegate и Agent callables до первого Pipe.
- Историю с skill mention, structured Tool output, изображениями и source context: сравнить snapshot текущего Filter со snapshot первого Pipe; отдельно проверить ограничения compaction.

По исходникам обоснован первый шаг «встроить delegate callable в router, сохранив ранний Preparation Filter». Удаление самого Filter требует дополнительного раннего механизма либо явно более узкого набора поддерживаемых запросов. Объявлять полную эквивалентность одного обычного Pipe существующей схеме без этих доказательств преждевременно.
