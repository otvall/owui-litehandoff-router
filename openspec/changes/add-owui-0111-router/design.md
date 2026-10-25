# Design

## Context

Согласованный объём и мотивация находятся в `proposal.md`; контракты — в двух delta specs этого change. Это дополнительный профиль исполнения, а не перенос всей функциональности 0.12.3 на старый сервер.

Текущий код имеет подходящую границу: все обращения к OWUI сосредоточены в `owui_adapter.py`. Общая подготовка, история, routing receipt, атомарная публикация, scope guards и очередь MCP-сессии уже отделены от native API. Однако версия 0.12.3 зафиксирована в адаптере и builder; `skills.READERS` всегда требует три встроенных readers; Preparation всегда рассматривает параллельного работника и создаёт группу с положительным default.

Исследованы официальные исходники тега **v0.11.1**. Существенные отличия:

| Область | OWUI 0.11.1 | Текущая интеграция 0.12.3 |
| --- | --- | --- |
| Native ограничения Tools | `ENABLE_PLUGINS` | `ENABLE_TOOLS`, `ENABLE_TOOL_SERVERS` |
| Local Tool loader | `load_tool_module_by_id` возвращает два значения | Возвращает три значения |
| Кэш Tool module | Native dictionaries из `get_tools_cache` / `get_tool_contents_cache` | `set_tool_module_in_cache` |
| Группы | `get_groups_by_member_id(user_id, db=None)` | Есть `include_inherited` |
| Workspace Skills | Текст `content`, один `view_skill(id)` | Файловый формат и три readers; `authorized_skill` |
| MCP | Helper находится в middleware; `MCPClient()` и `connect(url, headers)` | API в tools, constructor callbacks и auth strategy |
| Заголовки серверных вызовов | `build_tool_server_headers` принимает `extra_params` для system OAuth | Получает системный OAuth через helper |
| Терминал | Есть registered connections, context/URL helpers и server Tools | Тот же базовый transport с дополнительными настройками |

Источники: [tools.py](https://github.com/open-webui/open-webui/blob/v0.11.1/backend/open_webui/utils/tools.py), [plugin.py](https://github.com/open-webui/open-webui/blob/v0.11.1/backend/open_webui/utils/plugin.py), [middleware.py](https://github.com/open-webui/open-webui/blob/v0.11.1/backend/open_webui/utils/middleware.py), [MCP client](https://github.com/open-webui/open-webui/blob/v0.11.1/backend/open_webui/utils/mcp/client.py), [Skills model](https://github.com/open-webui/open-webui/blob/v0.11.1/backend/open_webui/models/skills.py), [builtin view_skill](https://github.com/open-webui/open-webui/blob/v0.11.1/backend/open_webui/tools/builtin.py).

Сигнатуры model access, prompt/params, callable refresh и destination inlet API в проверенных местах совместимы. Это статическое исследование, не доказательство runtime-совместимости. Для реализации нужен отдельный native/HTTP прогон 0.11.1.

Основа — текущий checkout после `add-selective-agent-terminals`, а не только основные specs: предыдущий change реализован, но ещё не синхронизирован. Здесь изменяются другие существующие блоки `workspace-model-execution` — Skill reads и standalone installation; terminal requirements не дублируются и предыдущие артефакты не переписываются.

## Goals / Non-Goals

**Goals:**

- Сохранить единый протокол handoff, историю, реестр и проверки ресурсов, адаптируя native API через одну границу.
- Сделать поддерживаемые возможности свойством целевого комплекта, чтобы отсутствие API старой версии не выглядело ошибкой конфигурации исправного агента.
- Проверять обе версии независимо, с точными guards и воспроизводимыми standalone exports.

**Non-Goals:**

- Эмуляция файловых Skills, иерархии групп или auth features, которых нет в штатной 0.11.1.
- Параллельный dispatch/child chats и отдельный Visualizer compatibility gate для 0.11.1. Обычный локальный Tool не блокируется по названию плагина, но его отдельная совместимость этим change не обещается.
- Изменение native approvals, прямые grants пользователям, monkey patch OWUI, новая БД или оркестрационный сервис.
- Автоматическое преобразование модели/чата/Skill из БД 0.12.3 в БД 0.11.1 либо downgrade существующей инсталляции.

## Decisions

### 1. Общее ядро и два явных release profile

Добавить неизменяемое описание профиля с целевой версией, активными Skill readers и доступностью параллельной работы. Профиль выбирается сборкой, а установленная версия проверяется до загрузки возможностей. При несовпадении или неизвестной версии — ошибка, без проб несовместимых API и автоматического переключения.

В исходном пакете и default сборке сохраняется профиль 0.12.3. Комплект 0.11.1 содержит свой фиксированный профиль. Valves, `body`, metadata, UserValves и routing Skill не выбирают версию. Определение установленной версии сохраняет существующие пути package metadata / `open_webui.env.VERSION` для официального source image.

За существующей точкой входа `OWUIAdapter` выделить общую часть и реализации native различий 0.11.1/0.12.3. Все OWUI imports оставить в `owui_adapter.py`, сохранив проверяемую архитектурную границу. Описание профиля можно вынести в небольшой модуль без OWUI imports. У всех inlined symbols должны быть уникальные имена: builder удаляет относительные imports и собирает модули в одно пространство имён.

Отклонены: снятие version guard без адаптации; `try/except ImportError` с неявным выбором поведения; отдельная копия всего Router. Эти варианты скрывают несовместимость либо заставляют дважды исправлять историю, права и handoff.

### 2. Подготовка использует активные возможности профиля

В `prepare_model` заменить требование фиксированных трёх readers на активный набор адаптера. Полный список зарезервированных имён чтения можно сохранить как защитный superset; публикуемый и обязательный набор — `view_skill` для 0.11.1 и существующие три для 0.12.3. Не изменять глобальную константу во время запроса: конкурентные запросы не должны менять схемы друг друга.

Для 0.11.1 `skill()` получает актуальный `SkillModel`, проверяет активность и native owner/group ACL и возвращает обычный текстовый Skill. `view_skill(id)` используется штатный, обёрнутый существующим scope guard и pinned owner authority. Не строить искусственный файловый пакет и не добавлять фиктивные `read_skill_file`/`grep_skill_files`. Каталог по-прежнему определяется `base_skill_ids`/routing Skills у Router и `meta.skillIds` у агента.

В Preparation профиля 0.11.1 не входить в `prepare_worker`; создавать state с фактически отключённой группой независимо от positive сохранённых Valves. В общей подготовке не публиковать `lite_parallel` и не допускать dispatch по подставленной истории/receipt. Зарезервированные routing names и запрет вложенной делегации сохраняются. Профиль 0.12.3 использует прежний worker bridge и defaults 4/300.

Таким образом, отсутствие `chat_context` в native subagent dispatch 0.11.1 не требует нового доверенного моста: эта поверхность не входит в согласованный профиль.

### 3. Local Tool loader адаптируется без подмены пользователя

Сохранить текущую политику: модель проверяется от actor, ресурс — от owner; модуль получает `__user__` и UserValves actor. Для 0.11.1 разобрать native результат loader из двух значений и обновлять предоставленные native cache dictionaries тем же способом, что native `get_tools`, включая проверку изменившегося source content. Для 0.12.3 оставить его helper/cache semantics.

Доступ и глобальные flags читать из соответствующей native версии, а не вводить отсутствующие поля в `open_webui.env`. Вызовы Groups не передают `include_inherited` в 0.11.1; native ACL использует группы, которые поддерживает эта версия. Не эмулировать родительские группы и не выдавать grants для компенсации API-различий.

`bind_tool_context`, проверки отсоединения/смены владельца, effective messages и обновляемые files остаются общими. Новый профиль обязан пройти native refresh и destination-inlet проверки, а не только начальный вызов Tool.

### 4. OpenAPI/MCP сохраняют две роли и native auth целевой версии

Поддержать прежние `toolIds`: локальные IDs, `server:openapi:...`/поддерживаемый native server ID и `server:mcp:<id>`. Router использует базовый набор из административных Valves; агент — свой `meta.toolIds`. Фильтры имён функций, ACL и запреты подключения остаются действующими; обязательный ресурс не исчезает молча.

Для 0.11.1 native `build_tool_server_headers` вызывается с actor и доверенным `extra_params`. Системный OAuth получать для actor через штатный helper `utils.middleware.get_system_oauth_token`, передавая результат в ожидаемый slot; клиентская metadata не является источником токена. Сессионный token и cookies берутся из текущего execution request. Отсутствующая обязательная пользовательская авторизация вызывает явную ошибку, без owner fallback. Cookie/auth semantics проверяются относительно native 0.11.1, включая отличия от `forward_cookies` в 0.12.3.

MCP подключать через native `utils.mcp.client.MCPClient()` с `connect(url, headers)`; не передавать отсутствующие constructor callbacks или `auth`. Не вызывать готовый native `connect_mcp_server` от owner: он объединяет ACL и исполнителя. Проверку owner выполнить отдельно, а transport построить с actor. OAuth 2.1 получает token через штатный manager actor/server. В профиле 0.12.3 его callbacks/auth strategy сохраняются.

Общая `McpSession` остаётся владельцем enter/call/exit в одной задаче SDK и пределах одного запроса, пользователя и модели. Она не превращается в process-wide cache. Проверять реальный connect/call/disconnect с библиотекой каждого окружения, ошибки подготовки и Stop во время вызова; native 0.12.3 тест не доказывает поддержку SDK, поставляемого с 0.11.1.

### 5. Терминальная привязка и scope остаются общими

Переиспользовать `terminal_binding`, очистку унаследованного выбора, terminal guards и защиту callable/schema/type/ID после inlet. Поле остаётся `meta.lite_handoff.terminal_id`; его persistence поддерживается native `ModelMeta(extra='allow')` в 0.11.1. `AGENTS` не расширяется рабочими ресурсами. Конфликт с `capabilities.terminal=false`, недоступность owner, пустой набор server Tools или конфликт имён возвращают ошибку до публикации.

Native adapter 0.11.1 использует его registered terminal connections, `get_terminal_servers`, terminal context/URL/schema helpers и `execute_tool_server`. Global gate — соответствующий `ENABLE_PLUGINS`. Подготовка и повторные вызовы разделяют owner ACL и actor headers. Для terminal `bearer`, `session`, `system_oauth`, `none` сохраняются поддержанные native варианты; system OAuth передаётся в ожидаемые `extra_params`. Устаревший callable не переключается на новый terminal ID.

Основной chat ID используется для `X-Session-Id`, а context header вычисляет native policy. Других сред и session IDs Router не создаёт. Серверные инструкции/cwd добавляются в новый draft без накопления; интерактивные shell Tools, автоматический импорт терминальных Skills/AGENTS.md и terminal-file transfer остаются за границами уже реализованной модели.

Stop отменяет локальное ожидание и новые вызовы. Удалённый процесс может продолжиться по правилам backend; скрытых retries, удаления общей среды и обещания отката shell-действия нет. Путь терминала не заменяет обычный OWUI file ID и не расширяет файловый ACL пользователя.

### 6. Два комплекта exports с совместимой default сборкой

Расширить builder явным параметром целевой версии, например:

```sh
python3 tools/build_functions.py
python3 tools/build_functions.py --owui-version 0.11.1
```

Default продолжает создавать текущие `dist/router_preparation.py` и `dist/lite_handoff_router.py` для 0.12.3. Legacy-команда создаёт одноимённые файлы в `dist/owui-0.11.1/`, с отдельными заголовками и фиксированной целевой версией. Существующий Python-вызов `build(destination)` остаётся совместимым, новый аргумент имеет default 0.12.3. Неизвестный target отклоняется до перезаписи outputs.

Оба файла каждого комплекта содержат всё нужное ядро и адаптер без imports checkout. Наличие неиспользуемого кода другого профиля допустимо только при ленивых native imports: cold import и реальное исполнение на 0.11.1 не должны обращаться к отсутствующим API 0.12.3. Если отбор модулей при сборке используется для уменьшения bundle, он не должен порождать независимую копию ядра.

Названия Functions в legacy frontmatter должны явно указывать 0.11.1, чтобы оператор мог выбрать правильный комплект. Номер версии в заголовке не заменяет runtime guard: native minimum-version check сам по себе не обеспечивает точное совпадение.

### 7. Приёмка разделяется по версиям и реальному поведению

Нужны независимые native окружения/БД: существующие 0.12.3 тесты остаются в своём окружении; 0.11.1 запускается в отдельном official-image контейнере либо изолированном venv с его dependencies. Ни тестовый runner, ни Docker setup не переиспользуют рабочую БД или data volume 0.12.3. Выбор native suite явный; пропуск несовместимых 0.12.3 cases не должен выглядеть успешным выполнением новой 0.11.1 suite.

Для 0.11.1 проверить настоящий native Tool loop из **двух legacy exports без исходного пакета**:

- Router читает routing Skill, делает handoff в том же сохранённом чате; адресат читает свой текстовый Skill и выполняет локальный Tool с личными UserValves. Новое сообщение снова вызывает Router.
- Два actor используют приватные ресурсы одного owner: OpenAPI и настоящий loopback MCP, credentials/headers и свежие файлы не смешиваются; прямой доступ остаётся запрещённым.
- Выбранный терминальный агент выполняет реальную безопасную команду и читает созданный ею файл; обычный агент и Router не получают terminal capabilities, включая различающийся выбор родителя.
- Отсутствующий/disabled/отозванный ресурс, owner change, missing user auth, inlet-подмена и stale refresh отклоняются без частично подготовленной генерации. Stop не запускает новую команду или скрытый повтор.
- `lite_parallel` не предлагается и не исполняется при positive Valves, forged receipt и старой истории. Профиль не вызывает native child dispatch.
- Браузер показывает сохранённые Tool inputs/results обычного handoff без терминальной панели. Ни pixel QA, ни внешний OAuth authorization-server flow не считаются подтверждёнными одним mocked helper.

Для 0.12.3 пройти текущие unit/native проверки, A+B→C/A+A→C с реальными ресурсами и файловыми результатами, выборочные терминалы и сохранённый child embed Inline Visualizer 2.2.3 с проверкой интерактивности. Сборщик, guards и scope readers являются общей областью риска; одной legacy suite для регрессий недостаточно. Evidence разделяет actual shell/MCP transport, synthetic auth fixtures, браузерные наблюдения и ещё не проверенные окружения.

## Risks / Trade-offs

- [Одинаковые имена helpers скрывают разные сигнатуры и lifecycle] → Явные native реализации и контрактные тесты каждой версии, без fallback по исключениям.
- [Общее ядро может отключить reader или parallel Tool у 0.12.3] → Неизменяемые профили и обязательная двухверсионная матрица до завершения change.
- [Loader cache или global module могут смешать owner/actor данные] → Native cache хранит модуль/source, а пользовательский контекст закрепляется за callable запроса; конкурентный тест двух actor.
- [OAuth/cookie semantics различаются] → Использовать native helper целевой версии с actor, проверять отсутствие owner fallback и не копировать credentials из metadata клиента.
- [Native tests из другого релиза дают ложное ощущение совместимости] → Отдельные зависимости, БД, suite и export gate 0.11.1; обе версии фиксируются в evidence.
- [Несинхронизированный terminal change может потеряться при дальнейшем sync] → Не менять его requirement blocks; новый change опирается на реализованный код и добавляет version-specific требования в существующие capabilities.

## Migration Plan

1. Сохранить текущий комплект 0.12.3 и его настройки; собрать/проверить дополнительный комплект в отдельной тестовой OWUI 0.11.1 с собственным data volume.
2. Для 0.11.1 установить ровно два legacy файла, настроить прежние `orchestrator_model_id`, `AGENTS` и базовые административные ресурсы. Использовать нативные текстовые Skills этой инсталляции.
3. Привязать существующее terminal connection только нужным моделям; остальные оставить без поля. Настроить owner ACL и пользовательскую внешнюю авторизацию штатно, без новых прямых grants.
4. Установку на рабочую систему выполнять после review и acceptance; данный change не обновляет/понижает сервер OWUI и не переносит его БД между версиями.
5. Для rollback остановить активные запросы и восстановить сохранённые соответствующие версии Functions/Valves/metadata. Не загружать legacy bundle в 0.12.3 и не удалять общие terminal files. Тестовые настройки восстановить, удалить только собственные fixtures и остановить их loopback services.
