# Tasks

## 1. Профили версий и изолированные native проверки

- [x] 1.1 Ввести явные неизменяемые профили 0.11.1/0.12.3 и реализации за существующей границей `OWUIAdapter`, сохранив native imports в `owui_adapter.py`. Unit-тестами проверить default 0.12.3, точный version guard обеих Functions, fallback для source image, отказ неизвестной/чужой версии и невозможность смены профиля из клиентских полей или UserValves.
- [x] 1.2 Добавить явный выбор native suite/окружения и отдельной тестовой БД для 0.11.1, не меняя зависимости и данные существующего окружения 0.12.3. Пройти smoke-проверку native version/model/payload/filter/binder API на обеих версиях и документировать воспроизводимый запуск в `docs/development.md`; runner не должен выдавать пропущенные cases за проверенную совместимость.
- [x] 1.3 Подключить capability profile в Preparation и общую подготовку: для 0.11.1 не входить в worker binding и принудительно исключить параллельную группу/schema/callable. Проверить unit/native cases с positive Valves, включёнными native сабагентами, подставленной историей/receipt и сохранением default 4/300 у 0.12.3; описать различия профилей в `docs/compatibility.md`.

## 2. Локальные Tools и штатные текстовые Skills

- [x] 2.1 Адаптировать local loader/cache 0.11.1: два return values, native cache dictionaries и актуальный source content, соответствующий global flag и Groups API. Native-тестами проверить владельца ресурса, двух actor без прямых grants, их UserValves, обновление source, отсоединение/смену owner, callable refresh и отсутствие подмены пользователя; проверки 0.12.3 должны сохраняться.
- [x] 2.2 Сделать обязательный и публикуемый набор Skill readers зависимым от профиля, сохранив защищённые зарезервированные имена. Native-тестами 0.11.1 проверить реальный текстовый `view_skill(id)`, собственный каталог Router/агента, чужой/отсоединённый/неактивный Skill, refresh и inlet-подмену; отдельно подтвердить три readers и файловые ограничения 0.12.3 без эмуляции файловых Skills на 0.11.1.
- [x] 2.3 Дополнить `docs/setup.md` и `docs/skills.md` примерами native текстового Skill 0.11.1, прежними `AGENTS`/attachments и доступом через модель без grants на ресурсы. Проверить примеры native model/Skill roundtrip и сверить описание supported readers с тестами 2.1–2.2.

## 3. Серверные OpenAPI/MCP и выборочный терминал

- [ ] 3.1 Адаптировать OpenAPI resolution, global/connection flags и `build_tool_server_headers` 0.11.1 с actor и доверенным OAuth extra slot. Native-тестами проверить фильтры имён, owner/group ACL, bearer/session/system OAuth/none, cookies и заголовки двух actor, отсутствие обязательной авторизации и отклонение client/owner credential fallback; существующий OpenAPI путь 0.12.3 остаётся проверенным.
- [ ] 3.2 Подключить native `MCPClient()`/`connect(url, headers)` 0.11.1 к общей request-owned `McpSession`, сохранив callback/auth API 0.12.3. На настоящем loopback MCP в каждом native окружении проверить list/call/disconnect, owner ACL, actor OAuth/session headers, function filters, повторные вызовы и закрытие на ошибке/Stop в той же задаче SDK; сессии и credentials двух пользователей не смешиваются.
- [x] 3.3 Адаптировать native terminal transport/auth helpers 0.11.1, переиспользуя `meta.lite_handoff.terminal_id`, scope guards и атомарную подготовку. Unit/native-тестами проверить default off, модель с/без привязки, persistence metadata, parent override, capability conflict, missing/disabled/denied connection, actor auth и chat/context headers, native refresh, inlet-подмену, смену owner/привязки и отсутствие терминала у Router.
- [x] 3.4 Обновить `docs/setup.md`/`docs/compatibility.md` для серверных IDs, auth/cookie semantics целевой версии и терминальных ограничений. Сверить каждый поддержанный режим с тестами 3.1–3.3; явно указать прежние границы Stop, интерактивной панели, общего filesystem и передачи terminal paths вместо OWUI file IDs, не выдавая native fixtures за внешний OAuth flow.

## 4. Отдельные standalone exports

- [x] 4.1 Расширить `tools/build_functions.py` явным target версии и собрать две Functions 0.11.1 в `dist/owui-0.11.1/`, сохранив default/пути/имена 0.12.3 и Python API `build(destination)`. Export-тестами проверить воспроизводимость каждого target, заголовки/guards, уникальность inlined symbols, отказ неизвестного target без перезаписи outputs и import без checkout; в соответствующих native окружениях проверить отсутствие обращений к API чужой версии.
- [x] 4.2 Обновить README и `docs/setup.md`/`docs/development.md` с точными командами сборки/установки каждого комплекта, native текстовыми Skills, terminal binding и rollback. Проверить команды сборки на временных destinations и наличие ровно двух самостоятельных файлов каждого target; установка версии 0.11.1 не требует переноса/понижения БД или загрузки Python-пакета Router на сервер.

## 5. Интеграционная приёмка двух версий

- [x] 5.1 Подготовить отдельную disposable OWUI 0.11.1 без data volume рабочей или тестовой 0.12.3, два exports без исходного пакета, двух actor, non-admin owner и заранее доступные loopback OpenAPI/MCP/командный backend. Описать setup/restore/cleanup в `tools/README.md`; smoke-проверкой подтвердить версию, изоляцию данных, реальные SDK/HTTP endpoints и фактическое shell-исполнение безопасного маркера.
- [x] 5.2 Пройти native HTTP Tool loop 0.11.1: routing Skill→handoff в том же чате→local Tool/свой Skill/OpenAPI/MCP/terminal command+file read и следующее сообщение Router, включая двух одновременных actor. Проверить private direct access до/после, effective prompts/параметры/UserValves, вложения actor и реальный маркер файла; записать результаты и hashes legacy exports в отдельное acceptance evidence.
- [x] 5.3 В legacy gate пройти отрицательные сценарии missing/disabled/отозванного ресурса, owner change, missing actor auth, native refresh и inlet-подмены, попытки включения `lite_parallel` и Stop во время terminal/MCP вызова. Подтвердить отсутствие частичной публикации, child dispatch, новых команд, позднего возобновления и скрытых retries; сохранить проверяемые исходы в `docs/acceptance.md`.
- [x] 5.4 В браузере 0.11.1 открыть сохранённый обычный handoff с терминалом и без него, раскрыть Tool inputs/results и проверить отсутствие требования terminal pane. Записать browser evidence и ограничения в `docs/compatibility.md`, отделяя наблюдения UI/AX от пиксельной оценки и server-side assertions.
- [x] 5.5 На комплекте 0.12.3 повторить A+B→C/A+A→C с реальными Tools/MCP и файловыми результатами, выборочные терминалы и child embed Inline Visualizer Tool/Skill 2.2.3 с интерактивным контролем. Пройти общие unit/export проверки и обе native suites с явной версией, записать раздельное evidence и hashes; восстановить snapshots и удалить только созданные fixtures/services обеих сред. Завершение подтверждается успешной матрицей, чистым `git diff --check` и строгой валидацией change.

## Статус после ревью

Задачи 3.1 и 3.2 повторно открыты по трём подтверждённым замечаниям P2.
Подробности, воспроизведение и необходимые проверки — в [review.md](review.md).
Остальные 15 задач выполнены; change пока не готов к архивированию.
