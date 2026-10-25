# Фактическая приёмка

## Inline Visualizer: исходный gate 2026-10-23

Одноразовый `owui-router-test`, OWUI 0.12.3; текущие два exports загружены
самостоятельно, checkout внутри контейнера не установлен. Согласованные
upstream Tool/Skill и отрицательный snapshot перечислены с полными SHA-256 в
[manifest](../tools/fixtures/inline_visualizer/manifest.json). Установщик
отклоняет изменённый файл до HTTP, без обращения к сети. Команды подготовки,
проверки и restore — [tools/README.md](../tools/README.md).

Первый успешный gate использовал неизменённый runtime Router:
Pipe SHA-256 `6b0c5a880e43c6efe6db2fe47c1cb3d79462c032805177c6450c28a093940063`,
Preparation `f399c2b10098b2a090868dbf15ce4047fbd884118fc155c6b4a275ca829ea6b1`.
В Preparation заменён только штатный конфигурационный реестр fixture AGENTS.
Измерительный suffix оригинального Tool передаёт настоящие callbacks и вызывает
оригинал один раз. Работники читают настоящий согласованный Skill через native
`view_skill`, затем опубликованную функцию (`visualize` / `render_visualization`).

| Проверка | Фактический результат |
| --- | --- |
| 2.1.1, реальный Safari остаётся в основном чате | Parent `c687b3db-03b9-48cf-8741-d8cfcdbf7459`; Tool pending на browser `execute` 8.283 с, connected=true; child заранее не открывался. Ограниченный Stop: 0 native tasks, последующий `VG_INSPECT_ONLY`: 0 tickets |
| 2.2.3, основной чат открыт | Parent `f08abea5-f2e7-4be6-bd98-e295dd945efd`; A child `674713a6-9468-4fec-a5a9-687cd663b049`; `visualize` 5.024 с без browser callback, один вызов; модель продолжила ответ, done=true, 1 child embed, 0 parent embeds |
| 2.2.3, исходная вкладка отключена без Stop | Parent `5ef868b4-0dbd-4c33-9212-86cb8d6b1447`; A child `8e85128d-3c4f-46c5-9b05-871ae3411125`; исходный request connected=true, перед Tool connected=false. Native caller вернул disconnected error; Tool 0.011 с, продолжение модели и 1 сохранённый child embed, 0 parent embeds |
| Отрисовка отдельно от backend | Обычный viewer без grants на Tool/Skill; Sources → A → «Открыть выполнение» первого parent. В `about:srcdoc` видны `VGFIX-123113be9500-1`, SVG `Local SVG 42`, Toggle; loader отсутствует. Нажатие меняет STATE=0→1 и синий круг на зелёный. AX и снимок экрана сохранены в CUA-записи этой приёмки |
| Reload первого child | Восстановлены та же метка, SVG и Toggle; повторное нажатие STATE=0→1. Provider counters до/после: vg-router=5, vg-s=1, vg-a=3; новых Tool start нет |

Результат исходного gate: **обновления внешних Tool/Skill достаточно; runtime
Router не требует исправления**. Патчи OWUI и JS-форк плагина не применялись.
Production-установка пользователя не изменялась. Последующие проверки изоляции,
другой сессии и финального комплекта учитываются отдельно ниже.

| Дополнительная проверка | Результат |
| --- | --- |
| A+B | Parent `ad2ce1e2-704e-48bc-931e-f1733250b482`; children `5027ea3b-5334-4b5b-b3b0-067cb3d22f79`, `c1b85915-a287-40b8-bb2b-d2db3da34426`; 5.033/5.038 с, overlap 5.026 с; метки `VGFIX-f7e5f607ca91-1/2`, обе кнопки работают |
| A+A | Parent `7e234e20-787f-40c7-8ae3-c17ab767a8c7`; children `471b8059-d9e5-480f-aed3-e1151fd54e86`, `96a02325-b92b-44df-9009-10a8591a0543`; 5.033/5.042 с, overlap 5.031 с; метки `VGFIX-2ec34627c906-1/2`, обе кнопки работают |
| Просмотр во время генерации | Parent `2b03664f-5ac3-464a-ba2e-10cd8f7cae1b`; child `7c61a4ea-14e4-4c41-9edb-c237211c43a7` открыт по source при done=false после Tool (5.022 с). Rendering visualization сменился на готовый `VGFIX-3495d965d5f4-1` после `touch continue-generation`; STATE=0→1. Worker counter остался vg-a=15, Tool start один |
| Другая сессия viewer | Новое обычное окно Safari с новой socket-сессией открыло первый child после отключения исходной вкладки. SVG и кнопка работают, counters до/после vg-router=5, vg-s=1, vg-a=3 |
| Доступ | Verify каждого parent подтвердил internal/type/parent chat+message/task_index, отсутствие детей в обычном списке/поиске и отказ чужому пользователю. Прямые GET приватных Tool/Skill viewer отклоняются |
| Обычный Visualizer handoff | Main `00c8aa97-2d63-46dc-87e0-bba9f71af108`: view_skill→lite_delegate→view_skill→visualize completed; child нет, main embed сохранён. После reload VGFIX-direct интерактивен. Live-main loader оставался до reload; live-main совместимость не подтверждена |
| Финальный gate и сеть | Parent `8cbd36b2-6cf3-401c-9d52-ce9ebefa3007`; A `dc32a0b6-5a06-4669-b24d-c32286618978`, B `6f5f1aec-2b56-42e8-987e-f19dd5131d9d`; 5.045/5.036 с, overlap 5.034 с. Оба открыты по source, SVG/Toggle/STATE=1 проверены. 47 ресурсов Network: 45 с 127.0.0.1 и два about:srcdoc; внешних запросов нет |
| Прежние группы с handoff | Финальная команда visualizer_existing_control.py: A+B→C `f3a6d2e5-bb2c-45e5-91d9-459b2cf3dc4e`, A+A→C `cdb72b7a-5b84-4425-b72f-3a7d374b2c6c`; файлы/доступ/статусы/ссылки проверены прежним verifier. Следующий Router ответ `1a163e60-b3e2-4d24-9b4a-1e9ab0e2e28e` без новых workers |

Браузер **Safari 27.0.1**, обычный fixture viewer, scripts/same-origin включены
только как условие стенда. Рабочие ресурсы принадлежат другому обычному owner.
Детали отрисовки/сети — [AX-запись](visualizer-browser-evidence.md), снимки экрана
сохранены в CUA-записи приёмки. Метрики и IDs без credentials —
[visualizer-evidence.json](visualizer-evidence.json).

Для reload используйте кнопку Safari: Cmd+R в main OWUI перехватился как
регенерация и дополнительно повторил direct-контроль. Эти вызовы не входят
в доказательство отсутствия повторов при просмотре children.

Проверки: сборка `python3 tools/build_functions.py` без изменения exports,
**47/47** обычных тестов, **29/29** native на локальном Python 3.12.10.
Docker использовал Python 3.11.17 и самостоятельные exports без checkout.
После сохранения свидетельств удалены 23 Visualizer fixture-чата вместе с новыми
ресурсами/тремя пользователями и четыре новых ordinary-control parent с их native
children. До удаления native tasks=0; audit: tickets=0, viewer sockets=0.
Provider/config и UI settings восстановлены и сверены с snapshot; свой provider
8769 остановлен. Production не менялась.

Дата: **2026-10-22**. Изменение `add-skill-based-handoff-router`:
сценарии **7.1–7.3 выполнены** с детерминированным тестовым провайдером;
перенос delegate в Pipe отдельно проверен, как описано ниже.
Качество маршрутизации настоящей LLM этой приёмкой не оценивалось.

## Окружения

- Helpers: macOS, Python **3.12.10**, установленный **open-webui==0.12.3**,
  отдельная SQLite БД `.native-data/helpers`.
- Сквозной стенд: официальный образ `ghcr.io/open-webui/open-webui:v0.12.3`,
  контейнер `owui-router-test`, отдельный volume `owui-router-test-data`,
  интерфейс `http://127.0.0.1:8765`, Safari.
- Внутри контейнера: HTTP-провайдер `127.0.0.1:8766/v1` и настоящий MCP
  Streamable HTTP endpoint `127.0.0.1:8766/mcp/`. Установленный MCP SDK OWUI
  выполняет connect/list/call/disconnect без замены transport.
- При исходной приёмке в контейнер скопированы три файла `dist/` и тестовые
  скрипты; после переноса delegate используются только два файла Functions.
  `importlib.util.find_spec("lite_handoff_router") is None`: исходный пакет
  проекта для исполнения exports не установлен.

В официальном образе нет package metadata `open-webui`: он запускает исходники.
Проверка версии исправлена для этого варианта и использует штатную
`open_webui.env.VERSION`. Версии, отличные от 0.12.3, по-прежнему отклоняются.

## Автоматические проверки

| Проверка | Результат |
| --- | --- |
| Обычные тесты | **27/27** после переноса: протокол, trusted binding inline callable, коллизия delegate, подготовка, enum, изоляция, атомарность, история, guarded readers и сборка |
| Native helpers/loop | **13/13** с настоящими импортами OWUI и отдельной БД; дополнительно проверен Docker-вариант определения версии |
| Самостоятельность exports | Идентичная повторная сборка, компиляция и загрузка в изолированном Python без checkout в путях импорта |
| HTTP + native loop | Две группы/два обычных пользователя одновременно выполняют свои routing/working Skills, локальный Tool и MCP |
| Личность исполнения | Локальный Tool и настоящий MCP получают ID текущего пользователя; A и B не используют идентичность владельца ресурсов |
| Персональный реестр | Пользователь группы A не получает B в delegate enum; запрос профиля B не запускает недоступного B |
| Новый запрос | A → B → самостоятельный Router в одном сохранённом чате, с новым `message_id` на каждом ходе |
| Изменение прав | Доступ к A отозван после публикации реестра, перед выполнением delegate. Передача завершается ошибкой, рабочие Tool/MCP A не запускаются. Grant восстановлен |
| Ошибки | Провайдер, переполнение и обязательный недоступный Tool сохраняют явную ошибку в чате |
| Ошибка после действия | `profile_lookup` выполнен один раз; после ошибки провайдера нет повторного действия или fallback к Router |
| Установка проекта | `pip install --no-index --no-build-isolation .` работает с заранее установленными зависимостями |
| OpenSpec | Строгая валидация изменения проходит |

Команды локальных проверок:

```sh
.venv/bin/python tools/build_functions.py
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/run_native_tests.py
.venv/bin/python -m pip install --no-index --no-build-isolation .
openspec validate add-skill-based-handoff-router --strict
```

В helper-тесте `tests/native/test_chain.py` заменены только транспорт генерации
и браузерная доставка событий. Сквозной Docker-прогон использует настоящие HTTP,
MCP, маршруты приложения, native Tool loop и сохранение в БД.

## Перенос lite_delegate внутрь Pipe

На том же Docker OWUI **0.12.3** установлены только два актуальных exports.
Старый Workspace Tool `lhr_delegate` удалён из тестового стенда, в Valves
Preparation заданы `base_tool_ids=[]`, `base_skill_ids=[]`. Исходная установка
с отдельным Tool сохранена в приватном backup перед обновлением.

Повторная HTTP-приёмка прошла через настоящий `/api/chat/completions`,
saved-chat fan-out, native middleware и БД. Проверено:

- Отдельный delegate Tool отсутствует; middleware исполняет callable из Pipe.
  Проверяется не только статус вызова: результат содержит серверный marker
  с точными `agent_id`, `request_id` и `nonce`.
- Два обычных пользователя параллельно получают свои персональные реестры,
  routing/working Skills, локальные Tools и MCP с верной личностью исполнения.
- Явный исходный `tools: []` не мешает передаче и Tools агента: ранний общий
  registry создаёт Preparation, его наличие не зависит от builtin Tools OWUI.
- A → B → самостоятельный Router проходят в одном чате
  `d0c548ce-2e81-42fc-84af-b57bf4621925`.
- Отзыв доступа между discovery и delegate останавливает передачу до
  рабочих Tools агента; grant восстановлен. Чат ошибки:
  `a64a49af-8c22-4567-9dd1-b1c04db5bcd6`.
- Ошибки провайдера, контекста, обязательного ресурса и ошибка после Tool
  сохраняются без повторного действия и скрытого fallback.
- Snapshot/restore двух Functions и Valves выполнен; после восстановления
  Router → inline delegate → A → Tool → MCP → ответ прошёл в чате
  `6660acf8-f489-408f-9d50-4b853a3842c6`.

Результаты: `.cache/inline-results.json`. Native тест двух exports дополнительно
сопоставляет результат delegate с реальным pending-состоянием запроса, проверяет
два последовательных Tools, обновление промпта/температуры и A → B → Router.
Обычные тесты проверяют изоляцию enum, отказ старого callable после нового
binding и диагностику коллизии с отдельным delegate Tool.

Провайдер остаётся детерминированным: это проверка интеграции и исполнения,
а не качества решений LLM. Браузерный и offline-прогоны ниже относятся к
исходному комплекту из трёх файлов; после переноса отдельно не повторялись.
Saved-chat approval pause/resume в новом HTTP request также не проверен.

## Браузер исходного комплекта

В Safari выполнены запросы `profile a browser acceptance`,
`profile b browser acceptance`, затем `general question browser acceptance`.
Один чат содержит ответы `specialist-a`, `specialist-b`, `General answer`.
После повторного открытия сохранённая переписка и Tool-история остаются доступны.

Проверено:

- В селекторе доступен только видимый **LHR Router**; агенты и базовые модели скрыты.
- В меню интеграций Delegate, локальные Tools и MCP имеют переключатели **off**.
  Автоматические подключения не записаны в выбранные ресурсы чата.
- Статусы **«Передача: LHR Agent A/B»** сохранены с соответствующим ответом.
- История включает routing Skill → delegate → working Skill → локальный Tool
  → MCP; операции завершены в одном сообщении. Финальный ответ Router
  не пересказывает ответ агента.
- Ошибки провайдера, переполнения и обязательного ресурса видны в интерфейсе.
  Ошибка после Tool также отображается, без повторного исполнения действия.
- Сохранённый чат пользователя A с отзывом доступа открыт в браузере:
  видна ошибка delegate, без ответа и рабочих Tools агента.

Основной браузерный чат:
`6a7dab69-25a8-4b53-8140-c0a7930b53f6`.
Чат offline-прогона A → B → Router:
`b38a55e8-c970-4173-b49f-870940526938`.
Это локальные тестовые данные; доступ определяется правами OWUI.

## Offline и восстановление исходного комплекта

Контейнер временно отключён от `bridge` через `docker network disconnect`.
Проверены пустой `NetworkSettings.Networks={}`, отсутствие IPv4-маршрутов
в `/proc/net/route` и ошибка внешнего connect **101 / Network unreachable**.
В таком состоянии повторно импортированы три самостоятельных exports и выполнен
полный HTTP-прогон: две группы параллельно, A → B → Router, MCP, персональный
реестр, ошибки и отзыв прав перед handoff. Все сценарии прошли без внешней сети.
После проверки исходное подключение `bridge` восстановлено.

Сохранены и обратно импортированы три exports вместе с административными Valves.
Содержимое файлов и Valves после восстановления совпадает с сохранённым;
затем снова прошёл запрос Router → A → локальный Tool → MCP → ответ.
Чаты для восстановления не удалялись.

Обычная процедура установки/отката описана в [setup.md](setup.md).
Образ OWUI и его зависимости были подготовлены заранее; их скачивание
внутри закрытого контура не требуется и не проверялось.

## Воспроизведение стенда

Скрипты и команды — в [tools/README.md](../tools/README.md):

```sh
docker exec owui-router-test python /tmp/lhr-acceptance/acceptance_setup.py setup
docker exec owui-router-test python /tmp/lhr-acceptance/acceptance_checks.py
docker exec owui-router-test python /tmp/lhr-acceptance/acceptance_setup.py snapshot
docker exec owui-router-test python /tmp/lhr-acceptance/acceptance_setup.py restore
```

Setup создаёт ресурсы с именами LHR, два тестовых аккаунта/группы, скрытых
`agent-a-lhr`/`agent-b-lhr`, их Skills и два MCP-подключения.
Router attachments пусты; `base_tool_ids=[]`,
`base_skill_ids=[]` задаются в административных Valves Preparation.
Словарь AGENTS в импортируемом Preparation связывает точные Model/Skill IDs.
Отдельный delegate Tool новый setup не устанавливает и удаляет устаревший
LHR fixture, если он присутствует. Snapshot перед setup сохраняет его для отката.

Тестовые credentials, исходная конфигурация и backup хранятся только
в приватных файлах внутри `/tmp/lhr-acceptance`, не в checkout или отчёте.
Файлы результатов без токенов скопированы в игнорируемые
`.cache/live-results.json`, `.cache/offline-results.json` и
`.cache/inline-results.json`.

Оставшийся этап перед эксплуатацией — отдельный прогон с настоящими
внутренними LLM и ресурсами организации: качество следования routing Skills,
выбор агентов и подходящие параметры конкретных моделей.

## Технический gate параллельных задач

Дата: **2026-10-22**, `add-parallel-agent-tasks`, задачи 1.1–1.7.
Проверен прототип поверх текущих exports, отдельно от продуктовой интеграции.
Использованы тот же изолированный Docker/volume и локальный детерминированный
провайдер `127.0.0.1:8768`, отдельный пользователь Parallel Gate User,
Fixtures `agent-a-pgate`/`agent-b-pgate`, скрытые из селектора, и посторонний
доступный пользователю Skill. Рабочая БД не подключалась; оба HTTP-провайдера
и MCP работают на loopback, offline mode отключает установку зависимостей.

| Проверка | Доказательство |
| --- | --- |
| Native запуск A/B и изоляция | Чат `4defa50b-7106-4278-95f8-46c69627f0c9`, overlap 1.005 с; девять calls каждого, свои параметры/промпты, три посторонних reader отклонены после refresh |
| Полный результат и файл | Child A `99fd3204-e5b4-4cb6-acc7-d3ef9ef396e6`, B `0536cd4e-35b1-4238-9615-6d89e971d406`; сохранено по 4211 символу, native summary по 120; CSV A/B скачаны владельцем |
| Stop внутри Tool | `48c753e3-0b3c-40e6-bf98-c8696d63c2e9`: две ссылки, ноль tickets/tasks, обе MCP закрыты |
| Stop при dispatch | `d36f9db4-ac97-47c7-b062-fa6a32d2c219`: обе ссылки сохранены, ноль tickets/tasks, MCP ещё не открывались |
| Deadline внутри Tool | `75cf9ce2-a46a-4b55-bb54-1823e300ede9`: оба `timed_out`, обе ссылки, ноль tickets/tasks, MCP закрыты |
| Deadline при dispatch | `3c67a28d-1452-460c-8fc8-4c3cd15d000f`: оба `timed_out`, обе ссылки, ноль tickets/tasks, MCP не открывались |
| Native pause не успех | `93404d1b-cc96-4a32-b41b-e743014aae6e`: оба `done=False`, сборщик вернул failed; ожидающие Tools не исполнены |
| Неподдерживаемый approval | `be1f51e6-e4ef-4dca-acc5-344e871248ac`: явная ошибка до создания любого child, настройка восстановлена |

В Safari открыты выполняющийся child
`c2984924-a111-4e3d-813f-2de0064bea19` (native `done=False` подтверждён перед
Stop) и завершённый `99fd3204-e5b4-4cb6-acc7-d3ef9ef396e6`. У завершённого
видны `FORECAST_A`, карточка `forecast-A.csv` и раскрываемые девять Tools,
включая `pgate_a_mcp_echo`. После reload остановленного основного чата
`71965535-8605-4edd-b4c7-9c35755ac759` открыта карточка источника и нажата ссылка
«Открыть выполнение»: переход в child
`a7c4c605-2465-4bc5-a2bc-1fc5a74de968` состоялся, Tool-история доступна.
В sidebar внутренних чатов нет, поиск `Gate task` показывает `No matches found`.
Браузер gate использовал существующую admin-сессию в режиме чтения чужих чатов;
отдельно owner API подтвердил отсутствие этих чатов в обычном списке и поиске
владельца даже после открытия. Браузерная приёмка продуктового A+B→C под обычным
пользователем остаётся задачей 5.5/6.2.

При дополнительных проверках исправлены две ошибки прототипа: ticket удалялся
после прерываемого await в cleanup, а конкурентные `source` events теряли одну
ссылку. Итоговые прогоны выше выполнены с отзывом ticket до await, защищённым
от повторной отмены cleanup и последовательной записью событий. Патч OWUI
не понадобился. Выбранный мост допускает переход к продуктовой интеграции;
это ещё не приёмка готового `lite_parallel` или сценария A+B→C.

Подготовка и воспроизведение — [tools/README.md](../tools/README.md).
Приватные credentials остаются внутри `/tmp/lhr-parallel-gate/`, отчёт
`gate-evidence.json` не содержит токенов. Изменения лимитов/approval в проверках
выполняются на disposable стенде и восстанавливаются в `finally`.

## Продуктовые exports: add-parallel-agent-tasks

На том же изолированном OWUI 0.12.3 установлены два собранных exports без
исходного пакета проекта. `lhr_parallel_preparation` содержит только настроенный
реестр A/B/C, `lhr_parallel_pipe` — актуальный export. В карточке Router пустые
Tools/Skills, клиент отправляет `tools: []`. Третий управляющий Tool не установлен.
Использованы два обычных пользователя двух native групп: первая имеет A/B/C,
вторая A/C. Локальный детерминированный Router читает routing Skill и реально
вызывает inline `lite_parallel`, затем отдельный `lite_delegate` к C.

| Проверка | Подтверждение |
| --- | --- |
| A+B→C первой группы | `bc0a2739-aa5e-45a2-a781-dd4e5f75f4da`, overlap 0.997 с; child `2cc5e34d-433a-4185-aee1-37b3061d401b` и `c92040d1-68c9-4311-8b1b-db0f635e3b4f` |
| A+A→C второй группы | `f23bf23c-4a83-49d5-b0cf-dc0d772de5eb`, overlap 0.999 с; разные child `85049f4e-f1ee-4da0-a7f8-7daafecce03f` и `edd60f5c-ac62-4668-9737-573157e87c6d`; B отсутствует в enum |
| Изоляция работников | Свои промпты, температуры 0.2/0.4, девять calls, свои три reader и отказ чужих трёх, MCP, CSV; destination inlets проверяют собственную модель/registry, включая continuations |
| Входные данные C | Оба полных ответа, авторы и CSV refs; C получает только свои Tools/Skills и температуру 0.1. Его Tool авторизует и читает CSV, создаёт доступную HTML-презентацию fixture в основном чате |
| Просмотр/файлы | Владелец читает child и скачивает результаты; другой обычный пользователь получает native отказ 401/404. URLs не содержат auth token; оба source сохраняются |
| Новое сообщение | В первой группе `d3140d69-b41b-42e8-85d0-b6f3eb008e93` отвечает Router, новых child не создано |
| Stop | `ec958535-ef57-416e-9a74-b13085748af0`: две сохранённые ссылки, child tasks остановлены, C не запущен |
| Deadline | `bd7b33b2-55ea-4b97-a2e2-97abe72e3e89`: общий `timed_out`, ссылки сохранены, нет child tasks или handoff |
| Native cascade delete | Родитель `3355b2b3-c2fd-48dd-8098-dd199c0b3169` удалён во время dispatch; оба child удалены и остановлены |
| Отключение `0` | `fbbc3899-4880-417f-ae24-02c4ff3875db`: параллельная возможность отсутствует; затем A → B → самостоятельный Router в том же чате без child |
| Обязательный ресурс B | `b98ad002-52bd-4e5d-b5b6-dcc71c8ce238`: полный результат A сохранён, B failed, группа partial, C не запущен; настройка B восстановлена |
| Переполнение C | `6faabd62-6b66-403f-8b88-edf1aa852ba8`: явная `Presentation context length exceeded`, без make_presentation и повторного запуска A/B |

Обычные тесты: **41/41**, native helpers/loop: **16/16**. Новые проверки
покрывают атомарную валидацию, настоящий generation audit, изоляцию пользователей,
подделку/повтор ticket, отзыв доступа, обратный порядок завершения, отдельные
задания одной модели, неполные/структурированные ответы, файлы и отмену.
Native тесты дополнительно подтверждают policy, version guard и скрытие
внутренних чатов из folder queries даже после чтения по ID.

При раннем прогоне delete прерывал запись namespace дочернего чата и оставлял
SQLite write lock. Привязка теперь завершается под защитой от повторной отмены;
добавлен регрессионный тест. После перезапуска только тестового контейнера
повторные Stop/deadline/delete прошли без патча OWUI. Read-only диагностика
процесса после прогона показала ноль tickets и активных worker/MCP-задач.

Браузер Safari, **обычные пользователи**:

- Первая группа: открыт основной чат `e7f6280e-b333-4c0b-84f1-8eebb7a843b4`.
  Видны presentation.html, четыре реальных вызова Router/C и следующий общий
  ответ Router. Из source «1. agent-a-pgate» открыто выполнение
  `ddced195-3eee-4bad-aae5-d5b16c93f048`, CSV и девять сохранённых Tools.
  Поиск по уникальному тексту задания не находит внутренний чат; кнопка Back
  возвращает в основной чат с обоими ответами.
- После reload остановленного продуктового чата
  `9e26c9fe-4d94-4f56-8f26-65d14482daa7` видны оба source; ссылка открыла child
  `38fa9ea2-af92-4a72-938d-9c055609efe8` с явным `Sub-agent cancelled`.
- Вторая группа: открыт `940d2972-0340-4015-9c98-f08a14dfaee0`. Видны файл
  презентации и разные источники «1. agent-a-pgate»/«2. agent-a-pgate».
  Вторая ссылка открыла своё задание `8304073a-743e-4b82-82b2-70b836ed3df8`,
  CSV и девять Tools. Скрытые A/B/C в селекторе для просмотра не требуются.
- У второй группы отдельно удалено старое выполнение
  `3f443acf-3ca5-4211-804d-847f1377330d`. Native API отказывает в чтении;
  повторное открытие сохранённого `/c/<id>` в Safari возвращает на главную
  без уведомления и повторного запуска. Это штатное поведение
  [OWUI 0.12.3](https://github.com/open-webui/open-webui/blob/v0.12.3/src/lib/components/chat/Chat.svelte#L2706-L2716),
  принятое пользователем для этой версии; frontend-патч не требуется.

Качество маршрутизации реальной LLM и содержательная ценность прогнозов этим
прогоном не оценивались. HTML-презентация — тестовый артефакт C; формат рабочих
файлов определяют существующие агенты пользователя.

Команды product приёмки — в [tools/README.md](../tools/README.md).
`product-evidence.json` и приватные полные записи хранятся внутри тестового
контейнера; пользовательские credentials в checkout не копируются.

## Ресурсы модели без grants пользователя

Дополнение `model-bound-resource-access` проверено отдельно от прежней политики.
Продуктовый адаптер прошёл `tools/resource_access_gate.py` на собственной БД
и настоящем временном loopback MCP: два неадминистративных исполнителя,
приватные Tool/Skill/подключение, разные UserValves владельца и пользователей,
три reader после native refresh, пользовательские OAuth/forwarded headers и
отказ при отсутствии пользовательского OAuth. Код OWUI не менялся.

В Docker установлены два новых exports без checkout. `resource_acceptance.py`
убрал grants обеих групп на Tools, Skills и MCP. Владельцем A/B/C и их рабочих
ресурсов стал отдельный обычный пользователь; владельцем публичного Router
и приватных routing Skills остался администратор тестового стенда. Обычные
пользователи сохранили только прежние model grants: A/B/C у первой группы,
A/C у второй. UserValves каждого рабочего Tool отличаются от настроек владельца.
Для локальных MCP созданы разные синтетические OAuth sessions пользователей
и владельца. Это проверяет выбор токена настоящим менеджером OWUI, а не внешний
OAuth login/refresh flow. Токены не записываются в трассу.

| Проверка новой политики | Подтверждение |
| --- | --- |
| A+B→C, первая группа | `054d1b3b-cda9-4966-a692-cf28ccd4964c`, overlap 0.990 с; A `48a50188-ffb5-48c6-a55e-9caba2004056`, B `a27ecd02-b999-44d9-815c-9789fb2a2651` |
| A+A→C, вторая группа | `01f801db-30c0-44fd-ac7a-33d0787e1843`, overlap 0.995 с; задания `c321f99a-e027-4798-839c-1fab546da708` и `f19db93d-68d6-4a1d-b891-3834eca07cd6`; B отсутствует в enum |
| UserValves и личность | Lookup/finish/C проверяют роль `user` и личную метку; настройка владельца не подходит этим проверкам. Все реальные вызовы завершились |
| MCP OAuth | В сохранённом MCP result каждого из четырёх работников `actor` соответствует пользователю родителя и `oauth_matches_actor=true` |
| Прямые ресурсы | До и после исполнения каталоги Tools/Skills не содержат приватных fixture-ресурсов, прямые GET отклоняются у обоих пользователей |
| Обычный handoff | `aabfdd13-05c8-4502-b8c2-1b12ce18bce6`: A→B→Router; `fcb60b52-4121-4401-8fb6-54a973fded41`: A→A→Router для второй группы, оба A действительно повторили свои Tools |
| Новое сообщение после C | `e5c40e31-44eb-44ff-9b2f-2733fa4047e4`: Router ответил сам, новых работников нет |
| Stop / deadline / delete | `12e0ce57-817d-491e-ba26-cc977df8eb6e`, `fa72ca1d-39a8-4ac0-aa04-039406932eaa`, `02fc70a4-ebce-441c-9f8f-bff1fa90a2be`: повторно проверены на финальных exports, native задачи остановлены, ссылки и каскад сохранены |
| Отключение / ошибка B / overflow C | `3eb61c8a-e891-4887-8357-b85db90f753d`, `c6e23476-09f2-4476-8a88-cf6286cb47da`, `c015def0-bf08-4f48-b1e5-87111d196888`: прежние ограничения и ошибки сохраняются на финальных exports |

В Safari использовано отдельное приватное окно с обычными учётными записями.
У первой группы в основном чате `ed8d0a90-7b3a-4b62-9467-fe42d7ae8cf0` видны presentation.html, реальные вызовы
Router/C и следующий общий ответ; source A открыл `dec88a3e-...`, где видны CSV
и девять Tools. У второй группы в `b50bb48a-f09c-4c52-987e-328d6db52d6b` открыты презентация, две разные карточки A и
второе выполнение `72614202-...` с CSV и девятью Tools. В меню интеграций обеих
групп виден только штатный интерпретатор; приватных Tools/Skills/MCP нет.
Пароли не сохранялись в браузере. Детали ранее проверенного штатного просмотра
и удалённых ссылок приведены выше.
После финальной замены загрузчика UserValves в Safari повторно открыт
`01f801db-30c0-44fd-ac7a-33d0787e1843`: сохранены presentation.html, итог C
и две ссылки на выполнения.

Итог: **44/44** обычных теста и **25/25** native helpers/loop. Новые проверки
используют неадминистративных владельцев и подтверждают права Router на routing
Skills, отказ посторонним Skills, отзыв разрешений, отсоединение ресурса, смену
или отсутствие владельца, глобальный запрет, private OpenAPI auth, сохранение
отказов прямых APIs и прав на обычные файлы/чаты. Отдельная регрессия подтверждает,
что некорректные UserValves владельца не мешают корректным настройкам исполнителя.
OpenAPI transport заменён
тестовым исполнителем; реальный MCP transport проверен gate и Docker-прогонами.
Качество реальной LLM не оценивалось. Команды — [tools/README.md](../tools/README.md);
`resource-evidence.json` и приватные записи остаются в тестовом контейнере.
## Selective agent terminals — 2026-10-25

Change `add-selective-agent-terminals`: 59 unit и 37 native проверок прошли;
два exports собраны без новых зависимостей и импортируются без checkout.
HTTP acceptance выполнен в `owui-router-test`, неизменённая OWUI 0.12.3,
через собственные `tgate` модели и два standalone Function exports.

Командный backend — совместимая disposable HTTP-фикстура на loopback 8770,
которая реально запускает subprocess shell и читает созданные файлы. Это
не stock Open Terminal и не проверка production-инсталляции. Детерминированным
является выбор Tools в LLM-provider, а не результат исполнения команды.

Шесть положительных HTTP-прогонов выполнили десять команд: обычный handoff
с другим выбранным терминалом родителя, A(terminal)+B(ordinary)→C(own terminal),
A+A→C, общий ответ Router и одновременные запросы двух пользователей. Проверены
реальные маркеры файлов, actor headers, `chat:<chat_id>` контексты, собственные
Skills и обычный Tool B. У обоих пользователей остался HTTP 403 при прямом
доступе к приватным терминалам, включая повторную проверку после исполнения.

Контроли: выключенное подключение, отзыв ACL владельца, Stop и deadline.
При отзыве A завершился ошибкой, результат B сохранился, группа получила
`partial` и C не запускался. После Stop/deadline backend мог завершить уже
переданный `sleep 5` и запись файла; новых команд/чтений, повторов и позднего
handoff не было. Дочерние native задачи завершились. Это проверяет локальную
отмену и границы backend, а не удалённое убийство shell-процесса.

В Safari открыты основной чат `f1bf8e8d-b1b0-4f3c-937b-16662eee8c70`, обычный
дочерний `f94a42af-8053-4976-97f4-78658ef8e0c5` и терминальный дочерний
`526df9e7-2d55-489a-bcaf-5fc74d9b9232`. Через native UI раскрыты результаты:
у B — `view_skill`/`tgate_echo`; у A — `view_skill`/`run_command`/`read_file`,
`exit_code=0` и совпавший `A_OK_…` маркер файла. Терминальная панель не открывалась.

Регрессия штатного Inline Visualizer Tool/Skill 2.2.3 с проверенными checksum:
один embed сохранён у B в дочернем чате
`5b350890-8046-4c2c-a78f-e49b33c9591d`, у родителя embed отсутствует. В Safari
AX-дерево показало SVG и `TG_VISUALIZER_REGRESSION`, реальный клик `Toggle regression`
изменил `STATE=0` на `STATE=1`. Штатные iframe prerequisites включались только
у одноразового пользователя. Native screenshot capture возвращал пустую
центральную область даже для обычного чата; пиксельная оценка не заявляется.
Браузерная проверка основана на AX-дереве, раскрытии результатов и реакции на клик.

Обезличенные результаты, версии и hashes: [terminal-evidence.json](terminal-evidence.json).
Приватные токены, пароли и snapshot конфигурации в репозиторий не копируются.

После прогона восстановлены исходные provider/config/terminal connections,
завершена браузерная сессия, удалены три собственных пользователя, их чаты,
модели, Functions, Tools, Skills и тестовые файлы; собственный backend остановлен.
Read-only проверка БД подтвердила отсутствие ресурсов с префиксом `tgate`.

## Комплект OWUI 0.11.1 (2026-10-25)

В отдельном `owui-router-0111-test` на `127.0.0.1:8767` проверены два legacy
exports без checkout/source package. Его БД и provider не используют данные
сервера 0.12.3. Fixtures: `tools/legacy_acceptance.py`,
`tools/legacy_gate_provider.py`; команды — в `tools/README.md`.

Шесть положительных HTTP runs: terminal handoff с отличающимся parent selection,
обычный handoff без терминала, самостоятельный Router, два одновременных actor
и следующее настоящее сообщение в том же чате после агента. Последнее снова
отвечает Router, без terminal tools или prompt агента.

В native Tool loop сохранены routing Skill → inline `lite_delegate` → собственный
текстовый Skill → local Tool → OpenAPI → реальный MCP. Терминальный A дополнительно
выполнил subprocess shell-команду записи уникального маркера и прочитал файл.
У B нет terminal schema/callable/prompt. Positive parallel Valves равны 8, native
subagents включены; `lite_parallel`/`delegate_task` не предлагаются.

Оба actor имеют доступ к моделям, но не к приватным Tools/Skills/terminal owner.
Прямые API остаются закрыты до и после. Local Tool проверен с личными UserValves
и свежим собственным OWUI attachment; другой actor не может прочитать файл.
OpenAPI/MCP принимают реальный session token текущего actor; backend проверяет
его совпадение, не сохраняя credential в отчёт. Промпты и температуры `.21`/`.41`
проверены provider assertions. Shell headers содержат actor и основной chat ID;
context равен `chat:<id>`. Автоматической копии attachments в terminal нет.

[Обезличенное backend evidence и SHA-256](owui-0111-evidence.json) фиксирует
положительные runs. Это scripted plumbing test, не LLM eval, не stock Open
Terminal и не внешний OAuth authorization flow. Native матрица отдельно
проверяет synthetic OAuth helpers, guards, error/Stop и SDK ownership:
20 cases для 0.11.1, 41 для 0.12.3 на этом этапе; общие unit/export — 65.
Browser inspection и завершающие controls/регрессии учитываются отдельно.

Legacy HTTP controls: 11 успешных cases в
[отдельном отчёте](owui-0111-controls.json). Missing local Tool, disabled server,
отозванный terminal owner ACL, реальная смена owner, отсутствующий actor system
OAuth и две inlet-подмены отклонены до первой генерации агента и terminal
команды. Native refresh внутри настоящего destination inlet не прочитал чужой
Skill. Сгенерированный `lite_parallel` получил явную ошибку; подтверждённый audit
блокирует продолжение до повторной генерации. Ни один run не создал child chat.

Stop проверен после начала настоящего MCP-вызова и shell-команды: спустя четыре
секунды task list пуст, новых генераций/команд/чтений нет и повтор не отправлен.
Удалённая shell-команда может завершиться после Stop; это не rollback backend.
Native SDK suite отдельно проверяет закрытие в той же задаче владельца.

Завершающая матрица change: **66 unit/export, 20 native 0.11.1, 41 native 0.12.3**,
без skipped cases; полные native suites повторены на пустых независимых БД.
Runner явно импортирует native config/migrations до записи первых fixtures.

На окончательных exports 0.12.3 повторены A+B→C и A+A→C с реальными local Tools,
MCP, двумя forecast CSV и итоговым HTML-файлом. Работа перекрывалась примерно
на секунду; чужой пользователь не читает файлы/children. После C снова ответил
Router. Выборочные терминалы: шесть runs, десять реальных shell-команд.
Child embed Visualizer 2.2.3 `f5ceb03e-c695-40ac-b524-957a3c569bc2` открыт в Safari;
AX показывает SVG, клик изменил `STATE=0` → `STATE=1`. Pixel capture остаётся
ограничением среды, отдельно от успешного UI interaction.

[Итоговая матрица с hashes и cleanup](owui-release-matrix.json) разделяет версии,
HTTP, SDK и browser evidence. Snapshot Functions/Valves/provider и synthetic
OAuth rows прежних pgate fixtures восстановлен; удалены только новые чаты и
файлы. Новые terminal fixtures/пользователи/файлы обеих версий отсутствуют
(read-only audit: ноль строк по каждому типу). Provider 8770 остановлен,
предыдущая terminal fixture directory возвращена на место; baseline 8766/8768
сохранены. Отдельный контейнер 0.11.1 и временные native БД удалены. Browser
viewer вышел из обеих сессий, тестовая вкладка оставлена `about:blank`.

Последующее ревью выявило три P2 вне этой тестовой матрицы: OAuth/custom
Authorization, schema fetch при global disable 0.11.1 и автоматическое закрытие
зависшей MCP-сессии при Stop ordinary handoff. Задачи 3.1–3.2 открыты повторно;
исторические успешные прогоны не означают готовность change к архиву.
Подробности — [review.md](../openspec/changes/add-owui-0111-router/review.md).
