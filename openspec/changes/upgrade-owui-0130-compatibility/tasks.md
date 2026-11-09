# Tasks

## 1. Воспроизводимое окружение и переход на 0.13.0

- [x] 1.1 Подготовить отдельный OWUI 0.13.0 и настоящий Open Terminal с закреплённой версией/image digest; записать тип подключения и обезличенную конфигурацию в новый раздел `docs/acceptance.md`. Проверка: версии backend подтверждены, production DB не используется, smoke-команда воспроизводима.
- [x] 1.2 На новом окружении запустить прежнюю пару exports и зафиксировать отказ version guard, затем после переноса guard повторить terminal smoke до исправлений terminal path; проверка: отчёт различает отказ загрузки и фактический отказ terminal dispatch/continuation, если он остаётся.
- [x] 1.3 Обновить metadata версии разработки, `requirements-native.txt` для воспроизводимых проверок и контракты сборки. После уточнения пользователя удалить runtime guard: обе Functions не читают версию и не отклоняют другие релизы, unit checks проходят.
- [x] 1.4 Обновить README и текущие инструкции native/offline установки для 0.13.0, описать замену обеих Functions и rollback с snapshot; проверить исполнимость команд и сохранить исторические evidence/архивы 0.12.5. Актуализировать Purpose workspace spec при реализации/синхронизации без изменения прочих требований.

## 2. Внутренние API и автономные Functions

- [x] 2.1 Сопоставить используемые адаптером сигнатуры и результаты OWUI 0.13.0 для моделей/ACL, Skills/readers, Tools/MCP/headers, inlet filters, completion и delegate/child chat с существующими native-контрактами; внести минимальные необходимые исправления. Проверка: `python tools/run_native_tests.py --suite smoke` проходит на действительной 0.13.0 без skipped cases, найденные несовпадения перечислены в отчёте.
- [x] 2.2 Проверить skill scope, owner/actor, callable refresh, коллизии имён и сохранение настроек модели на native continuations; для выявленных несовпадений добавить регрессионные проверки у действующего seam. Проверка: соответствующие unit/native tests сначала выявляют регрессию, после исправления проходят; актуальные ограничения отражены в `docs/router-configuration.md` при изменении настройки.
- [x] 2.3 Пересобрать оба exports командой `python tools/build_functions.py`, проверить детерминированность и загрузить пару в disposable OWUI без установленного checkout package. Проверка: обе Functions указывают 0.13.0, импортируются автономно, Router отвечает и выполняет обычный handoff с Tool-продолжением.

## 3. Open Terminal: команды, контексты и файловые результаты

- [x] 3.1 Если после version/API migration terminal smoke остаётся красным, локализовать отказ по этапам discovery/ACL, auth/context, command dispatch, native file result и continuation; добавить воспроизводящий тест перед исправлением. Проверка: тест воспроизводит конкретный отказ, а не только наличие Tools; если дополнительного отказа нет, сохранить это наблюдение без выдуманного исправления.
- [x] 3.2 Адаптировать terminal discovery/headers/callables/result handling к подтверждённым контрактам 0.13.0, сохранив привязку модели и owner/actor. Проверка: `tests/test_terminals.py`, `tests/test_terminal_execution.py`, `tests/native/test_terminals.py` и реальный smoke выполняют создание уникального файла, чтение, `display_file` и следующую генерацию; инструкции и cwd не накапливаются. Описать проверенный путь в terminal-разделе документации.
- [x] 3.3 Проверить raw Open Terminal и контекстную политику orchestrator, bearer/none/session/system OAuth headers и повторную авторизацию; исправить только доказанные несовпадения. Проверка: реальные команды покрывают тестовый backend, native-контракты покрывают поддерживаемые auth/context варианты, child использует свой chat ID; raw shared backend не требует искусственного runtime context.
- [x] 3.4 Проверить очистку terminal scope, включая новые native Skills/fingerprints/инструкции, на переключениях A(T)→B(no terminal)→Router и в native refresh; внести необходимые изменения с регрессионными тестами. Проверка: отсутствуют чужие callable, terminal Skills/AGENTS.md и навязанный клиентский terminal ID; пользовательские интерактивные shell Tools не подключаются.
- [x] 3.5 Проверить отзыв привязки/ACL, смену владельца, disabled/missing terminal, конфликт имён и отсутствующий обязательный saved context. Проверка: существующие и дополненные native tests подтверждают ошибку до provider/dispatch и отсутствие частично опубликованного набора.

## 4. Продуктовая приёмка и выпуск

- [x] 4.1 Обновить terminal acceptance fixture и её инструкции для disposable 0.13.0; выполнить обычный handoff, A(T)+B(no terminal)→C(T2), A+A, чтение файлов и новый Router turn без открытой terminal pane. Проверка: реальные сохранённые результаты содержат уникальные маркеры нужных подключений, два пользователя не подменяют actor/context; evidence различает настоящий Open Terminal и fixture transport.
- [x] 4.2 Выполнить terminal Stop/deadline/revocation controls и late-result checks. Проверка: после отмены нет новых команд, повтора уже отправленной команды или запуска финального агента; общий backend не останавливается, временные grants/settings восстановлены.
- [x] 4.3 Запустить полный unit suite и `python tools/run_native_tests.py --suite full` на закреплённой 0.13.0, затем существующие product regressions Tools/MCP/Skills/files и parallel context. Проверка: нет unexplained failures/skips; активное `fix-parallel-worker-task-context` и его артефакты не перезаписаны.
- [x] 4.4 В браузере открыть сохранённое terminal-выполнение и файловый результат, выполнить reload и подтвердить отсутствие повторного dispatch; проверить основной и дочерний чаты. Проверка: отдельное browser evidence подтверждает доступность результата пользователю и не подменяется backend-only выводом.
- [x] 4.5 Проверить окончательную согласованность runtime/pins/metadata обоих exports и инструкций, записать команды, версии, реальные результаты и ограничения в `docs/acceptance.md`; сохранить прежние evidence как исторические. Проверка: артефакты воспроизводимы, новая приёмка не объявляет неподтверждённые случаи успешными, `openspec validate upgrade-owui-0130-compatibility --strict` проходит.


## 5. Уточнение версии по запросу пользователя

- [x] 5.1 Удалить runtime version guard и обязательную metadata версии; оставить `developed_for_open_webui_version: 0.13.0`. Проверка: оба exports пересобраны, unit/export tests подтверждают отсутствие version lookup и обязательного поля.
- [x] 5.2 Обновить native runner, fixtures, документацию и delta spec для запуска без проверки номера версии. Проверка: статический поиск не находит OWUI_VERSION/SUPPORTED_OWUI и точных guards в runtime/fixtures; OpenSpec strict validation проходит.
