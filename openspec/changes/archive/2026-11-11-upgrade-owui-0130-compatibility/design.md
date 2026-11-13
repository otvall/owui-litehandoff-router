# Design

## Context

См. `proposal.md` о мотивации. Адаптер, build header и native requirements закреплены на 0.12.5. `OWUIAdapter.__init__` проверяет wheel metadata, а для официального source image — `open_webui.env.VERSION`; несовпадение останавливает работу до доступа к ресурсам. Это подтверждённый статический блокер, но не доказанная единственная причина пользовательского terminal-сбоя: его установка и лог ошибки в рамках proposal не исследованы.

Терминальный путь уже реализован отдельно от браузерного выбора: `terminal_binding` → подготовка capabilities → `terminal_access/server/headers/tools` → native Tool loop. Есть unit-контракты `tests/test_terminals.py`, `tests/test_terminal_execution.py`, native-контракты `tests/native/test_terminals.py` и продуктовый gate `tools/terminal_acceptance.py`. Ряд native-тестов подменяет транспорт; они не заменяют реальную команду в Open Terminal.

Прочитаны первичные исходники тега v0.13.0:

- [tools.py](https://github.com/open-webui/open-webui/blob/v0.13.0/backend/open_webui/utils/tools.py): сохраняются `get_terminal_servers`, `get_terminal_cwd`, `get_terminal_system_prompt`, `execute_tool_server`, helpers binding/refresh и `get_builtin_tools`; штатные terminal Tools возвращают `(tools, system_prompt)`, имеют `type=terminal`, `tool_id=terminal:<id>` и `user_shell_tools`.
- [terminals.py](https://github.com/open-webui/open-webui/blob/v0.13.0/backend/open_webui/utils/terminals.py): контекст chat ID применяется к orchestrator (`server_type`/`policy_id`), raw Open Terminal использует legacy shared runtime; появились/существуют загрузка terminal AGENTS.md, terminal skill fingerprints и фильтрация пользовательских shell Tools.
- [middleware.py](https://github.com/open-webui/open-webui/blob/v0.13.0/backend/open_webui/utils/middleware.py): сохраняется `build_terminal_file_tool_result`; подготовка payload и продолжения обрабатывают terminal metadata и Skills.
- [subagents.py](https://github.com/open-webui/open-webui/blob/v0.13.0/backend/open_webui/utils/subagents.py) и [filter.py](https://github.com/open-webui/open-webui/blob/v0.13.0/backend/open_webui/utils/filter.py): сохраняются delegate и filter pipeline. Наличие имён не доказывает совместимость всех аргументов и контекста; это проверяется native suite на закреплённом релизе.

## Goals / Non-Goals

**Goals:** сохранить существующую границу адаптера и контракты изоляции, проверить обе автономные Functions на неизменённом OWUI 0.13.0, подтвердить реальное терминальное исполнение и корректные файловые результаты.

**Non-Goals:** поддержка нескольких релизов, патчи OWUI/Open Terminal, смена модели прав доступа, интерактивные пользовательские shells, terminal Skills/AGENTS.md как новый источник инструкций, миграция production DB. Текущий отдельный change о контексте parallel workers не включается в этот план.

## Decisions

1. **Информационная версия разработки вместо version guard.** По уточнению пользователя удалить runtime constant, проверку distribution/source-image версии и обязательное поле `required_open_webui_version`. Заголовки обоих exports содержат только `developed_for_open_webui_version: 0.13.0`. Native dependency pin служит воспроизводимости тестов; runner выводит фактическую версию, fixtures не требуют её совпадения. Исторические результаты остаются привязаны к реально проверенной версии. Отдельные release profiles не добавляются.

2. **Адаптация через существующий OWUIAdapter.** Сопоставить фактически вызываемые сигнатуры/форматы моделей, skill readers, headers, фильтров, delegate, completion и file results с закреплённым 0.13.0; менять только подтверждённые несовпадения. Альтернатива — переписать orchestration или вызывать штатный terminal resolver от владельца модели — увеличивает объём и может подменить исполнителя владельцем. Владельцу проверяется подключение, actor предоставляет пользовательские токены и `X-User-Id`; chat ID берётся из фактического сохранённого исполнения.

3. **Сначала доказуемый terminal regression, затем исправление.** В apply зафиксировать прежний отказ на 0.13.0, проверить автономные exports, после переноса guard запустить существующие native terminal contracts. Если terminal smoke остаётся красным, локализовать конкретный этап: discovery/ACL → auth/context → command dispatch → native result → continuation. Исправление подкрепить тестом на соответствующем реальном seam. Не объявлять изменённый upstream API причиной только по факту обновления версии.

4. **Сохранить серверный scope при новых native metadata.** Проверить основной и дочерний payload, continuation и native refresh на отсутствие наследования чужого terminal ID, Skills, fingerprints и добавленных инструкций. Новые upstream helpers не подключать автоматически: широкая native discovery может раскрыть ресурсы вне модели. При необходимости очищать только управляемые terminal поля; не стирать обычную историю и данные других подсистем. Альтернатива — целиком передать терминал штатной подготовке — не гарантирует модельную привязку и existing owner/actor contract.

5. **Приёмка двух уровней.** Unit/native suite проверяет контракты и отрицательные случаи, настоящий Open Terminal gate — уникальную команду создания файла, его чтение, display result и продолжение. Проверять обычный handoff, A(T)+B(no terminal)→C(T2), A+A с дочерними chat IDs, смену агента и свежий Router. Проверка работает без terminal pane; отдельно в браузере проверить сохранённый результат/файл и отсутствие повторного исполнения при открытии/reload. Зафиксировать версию/image digest Open Terminal, тип подключения и auth; не выдавать loopback fixture transport за настоящий backend.

## Risks / Trade-offs

- [Есть только статическое свидетельство version guard, production-сбой не воспроизведён] → явный красный/зелёный terminal smoke на disposable 0.13.0 до заявления об исправлении.
- [Raw Open Terminal и orchestrator имеют разную политику контекстов] → тестировать raw backend и context contract orchestrator отдельно; не требовать выделенного runtime от raw backend и не объявлять chat ID изоляцией ФС.
- [Подмена транспорта скрывает auth/file-result несовместимость] → реальные команды и файлы на закреплённом backend плюс native refresh/revocation checks.
- [Изменение native pipeline раскрывает terminal Skills или чужую привязку] → отрицательные проверки Router, агента без терминала и переключений, включая fingerprint metadata.
- [Два несогласованных exports или старое disposable окружение] → детерминированная пересборка пары, импорт без checkout, проверка действительной версии до запуска suite, отдельный data root 0.13.0.
- [Пересечение с активным изменением контекста workers] → сохранить его артефакты и текущую реализацию; повторить его имеющиеся проверки на новом релизе без переопределения его требований.

## Migration Plan

1. Подготовить отдельный OWUI 0.13.0 и закреплённый Open Terminal, сохранить текущую пару exports/Valves и конфигурацию тестовых подключений. Production instance не использовать для воспроизведения.
2. Получить регрессионное свидетельство, перенести адаптер и pins, выполнить unit/native и terminal/product gates.
3. Пересобрать оба exports, проверить их автономность и согласованную metadata, опубликовать новые инструкции и фактические результаты. Сохранить существующие AGENTS, model IDs и `terminal_id`.
4. При установке заменить согласованную пару Filter/Pipe на сервере 0.13.0. Проверить короткий handoff с реальной командой и файлом.
5. Для отката вернуть прежнюю пару только вместе с совместимым OWUI 0.12.5 и его snapshot по штатной процедуре OWUI. Старый комплект на 0.13.0 и downgrade уже мигрированной БД без snapshot не считать откатом.

## Open Questions

- Версия Open Terminal и точный текст ошибки пользовательской установки пока неизвестны. Для воспроизводимой приёмки закрепляется доступный тестовый backend и сохраняются его версия/config; если сбой специфичен для другой установки, добавить её обезличенное воспроизведение перед закрытием соответствующей регрессии. Это не меняет целевой релиз и контракт существующего серверного подключения.

Уточнение после реализации: runtime version guard удалён по запросу пользователя; первоначальные отказы guard выше описывают историческое воспроизведение, а не итоговый контракт.


## Archive reconciliation — 2026-11-11

Переход 0.13.0 завершён и подтверждён историческим evidence; текущая версия
разработки уже 0.13.3. Перед синхронизацией delta формулирует проверенный релиз
и информационную metadata без вечного закрепления 0.13.0. Это не заявляет
поддержку произвольных версий и не меняет исходные результаты приёмки.
