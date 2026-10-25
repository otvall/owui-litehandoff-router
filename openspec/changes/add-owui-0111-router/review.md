# Ревью реализации — 2026-10-25

Проверены текущая реализация, новые файлы и proposal/specs/design/tasks.
Повторно прошли 66 unit/export-тестов. Три сценария ниже воспроизведены
отдельно; существующая матрица их не покрывает. Код по результатам ревью
пока не исправлялся. Задачи 3.1 и 3.2 открыты повторно: 15/17 выполнено.

## P2: обязательная OAuth-авторизация actor обходится custom header

Место: `src/lite_handoff_router/owui_adapter.py`,
`OWUI0111Adapter.server_headers`, проверка после `build_tool_server_headers`.

Воспроизведение: подключение OpenAPI/MCP с `auth_type=oauth_2.1` либо
`oauth_2.1_static`, custom `Authorization: Bearer <общий/owner token>`,
у actor отсутствует личный OAuth-токен. Native helper сначала получает
от OAuth manager `None`, затем накладывает custom headers. Проверка Router
принимает непустой итоговый заголовок и разрешает общий credential.
Custom Authorization также может заменить действующий токен actor.

Нарушен design §4: отсутствие обязательной пользовательской авторизации
должно давать ошибку без owner fallback. Подтверждено с настоящим helper
из официального образа 0.11.1, без изменения серверов и БД.

Для закрытия задачи 3.1: проверять наличие личной авторизации до наложения
custom headers; добавить отрицательные native cases для обоих OAuth modes
и проверку сохранения actor credentials при пользовательском Authorization.

## P2: global disable 0.11.1 проверяется после OpenAPI schema fetch

Место: `src/lite_handoff_router/owui_adapter.py`, `openapi_server`.

Воспроизведение: `ENABLE_PLUGINS=false`, активное OpenAPI-подключение и
модель с `server:<id>`. Router вызывает `get_tool_servers` до проверки
флага. Helpers 0.11.1 сами его не проверяют: schema fetch происходит,
затем подготовка отклоняется. В 0.12.3 native helpers имеют ранний guard.
Подтверждено точными native helpers 0.11.1 со счётчиком вместо сети:
один fetch перед отказом; Tool command не выполнялся.

Для закрытия задачи 3.1: проверять global gate до native resolution;
native regression должен подтверждать ноль schema/network вызовов.

## P2: Stop обычного handoff не закрывает зависшую MCP-сессию

Место: `src/lite_handoff_router/capabilities.py`, `McpSession`:
done callback вызывает `stop`, а SDK task ожидает `client.call_tool`.

Воспроизведение: parent создаёт сессию и ждёт Tool, заблокированный на
Event; отменить parent, не вызывать `session.close` вручную. Через 5,1 с
parent отменён, SDK task жива, `disconnect` не вызван, sentinel остаётся
в очереди. Закрытие происходит только после ответа remote Tool.
В обычном Pipe bounded `close` не подключён к этому lifecycle.

Это унаследованный дефект общего ядра для ordinary handoff, затрагивающий
обе версии. Он оставляет невыполненным обязательство задачи 3.2 нового
профиля о disconnect на Stop. Native-тест явно вызывает `session.close`
после отмены короткого Tool, поэтому не проверяет автоматическую очистку.

Для закрытия задачи 3.2: связать завершение/Stop запроса с ограниченным
закрытием MCP owner task, сохранив SDK enter/call/exit в одной задаче;
проверить зависший вызов без ручного cleanup из тестового сценария.
Удалённый процесс при этом может продолжаться по правилам backend.

## Готовность к архиву

`add-owui-0111-router` остаётся активным до исправления этих пунктов,
прохождения соответствующих regressions и обновления exports/evidence.
Исторические успешные HTTP/browser/native прогоны сохраняются как evidence,
но не подтверждают отсутствующие выше случаи.

`fix-parallel-visualizer-child-chat` и `add-selective-agent-terminals`
выполнены по 18/18 и могут архивироваться отдельно с синхронизацией их specs.
