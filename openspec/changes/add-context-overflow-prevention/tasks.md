# Tasks

Ветка `feat/tool-history-context-limits` основана на `main` (`3d684db`), без folder-routing коммита. Реализованы секции 1–4: окно текстовых/HTML результатов, исторические `lite_delegate` с отдельными tool messages, один system и XML routing Skills. Бюджеты, checkpoints и summary относятся к поздним этапам: согласованный текущий объём ограничен скрытием и новой упаковкой истории, обновление плана не запускает поздние механизмы.

Пересобраны обе Functions, проходят 131 unit-тест и 77 native-тестов в изолированной официальной OWUI 0.13.3. Официальный шаблон GLM проверен рендерингом. Отдельная HTTP-приёмка и смысловая GLM-приёмка не выполнены; подробности и границы подтверждения — в `docs/tool-history-context-validation.md`.

## 1. Administrative policy and runtime propagation

- [x] 1.1 Добавить Valves и чистое разрешение policy/budget по Workspace Model ID согласно design; проверить unit-тестами значения по умолчанию, N=null/0/positive, строгие типы, reserve, override и зависимости summary-настроек.
- [x] 1.2 Сохранить проверенный snapshot в `runtime.begin` и передать его работникам через серверный execution entry/`prepare_worker`; проверить два child одной модели, новый пользовательский запрос и невозможность подмены через body/metadata/task.
- [x] 1.3 Описать поля, выключенные defaults и последовательное включение в `docs/setup.md`; сверить примеры Valves с реальной валидацией без включения native compaction OWUI.

## 2. Implemented intermediate result window

- [x] 2.1 Расширить построение истории origin-картой, сохранив существующий отбор ownership/native/Skills; проверить `tests/test_history.py` и новые случаи повторных IDs, replay, ошибок и порядка четырёх вызовов одной группы.
- [x] 2.2 Реализовать базовое окно N над текущими exchanges и промежуточным reference JSON Router, отдельное окно агента/worker; проверить N=null/0/2, последовательные HTML-результаты и увеличение N без повтора. Переход к целевым handoff-пакетам проверяется отдельными открытыми задачами.
- [x] 2.3 Завершить проверку очистки дублирующих text/HTML полей скрытого результата при допустимых изменениях фильтров; проверить pairing/status/arguments, отсутствие маркера во всём provider payload и сохранение пользовательских вложений. Не расширять этап до неоднозначных изображений Tool results.
- [x] 2.4 Защитить текущие Skills/native и полный downstream packet, оставив `delegate_task` в обычном счётчике Router; проверить N меньше размера группы, фильтрацию native policy и отсутствие переноса чужих Skills/полномочий.
- [x] 2.5 Подключить окно после destination inlets и до callable binding, сохраняя full source для `confirmed_target` и parallel barriers; native-тестом проверить handoff, последовательные Tools, refreshed callable и совпадение эффективных `__messages__` с provider payload.
- [x] 2.6 Документировать базовый вариант окна в `docs/context.md` и настройках: model-only плашка, N, исключения, неизменность чата, размер/чтение в ответственности Tools; сверить примеры с тестами. Документация новой упаковки обновляется отдельно.

## 3. Historical handoff results and final window semantics

- [x] 3.1 Выделить подтверждённые границы исторических handoff и origins рабочих exchanges агента; проверить A -> B -> A, повторные IDs, сгруппированные генерации и запрет определять автора по одному имени Tool.
- [x] 3.2 Собрать модельную копию существующего lite_delegate и отдельный role=tool с исходным tool_call_id, строковым JSON agent_id/status/tool_results; тестами подтвердить хронологию трёх вопросов и неизменность исходного маркера/audit/чата.
- [x] 3.3 Определять status по terminal evidence вместо маркера приёма; проверить успех, ошибку после handoff, unknown при недостатке данных и пустой tool_results агента без Tools, не объявляя неизвестный исход completed.
- [x] 3.4 Убрать общую reference user-реплику и добавления справки к последнему вопросу; сохранить реальные прямые/неизвестные exchanges Router и отдельные delegate_task без выдуманного handoff; проверить отсутствие потери и двойной выдачи результатов.
- [x] 3.5 Исключить оболочки lite_delegate из N; считать вложенные рабочие записи вместе с прямыми результатами Router; проверить null/0/1/2, полные исторические результаты и их вытеснение новыми Tools, порядок batch и сохранение сведений о передачах при скрытых телах.
- [x] 3.6 Применять политику после inlets к прямым/вложенным результатам до binding; проверить scope/arguments, отсутствие копий скрытого HTML и невозможность обхода N через неподдерживаемую перестройку пакета.
- [x] 3.7 Сохранить собственную native-историю агента/worker без Router trace и чужих пакетов; native-тестом проверить роли/call IDs, текущий handoff ack, callable refresh и server barriers при N=0.
- [x] 3.8 Обновить docs/context.md, docs/setup.md и README примером трёх запросов: отдельные historical tool results, N по рабочим записям, текущий ack против исторического итога, неизменный последний user; сверить пример с provider payload.

## 4. Composed Router system and XML routing catalog

- [x] 4.1 Сериализовать scoped routing-каталог XML-encoder в routing_skills с полями skill_id/agent_id/agent_name/name/description и отдельными base Skills; проверить специальные символы, пустой реестр, порядок и отсутствие неприкреплённых Skills.
- [x] 4.2 Сохранить guidance чтения routing Skill, ответа о возможностях и выбора agent_id; на main проверить view_skill(id=skill_id) и отсутствие новой зависимости от skill_path/routing.json/folder routing.
- [x] 4.3 Собрать одно исходящее system-сообщение Router из своего промпта, правил, каталогов и допустимых системных добавлений; проверить стабильный порядок, отсутствие raw Tool data, отдельное API-поле tools и сохранение собственного system/scope агента.
- [x] 4.4 Документировать XML и итоговый API-пример в docs/context.md/docs/setup.md; проверить официальным шаблоном GLM-5.3-Flash, что отдельные role=tool сериализуются в observation/tool_response, а схемы и control tags не дублируются вручную.

## 5. Later stage: complete request budget and diagnostics

- [ ] 5.1 Реализовать чистый оценщик полного входа и breakdown с exact/estimated режимом, overhead и multimodal reserve без сетевых загрузок; проверить сообщения, схемы Tools, прямые и вложенные handoff results, summary, мультимодальный резерв и исключение служебных cache/origin metadata.
- [ ] 5.2 Добавить budget gate после inlets и перед каждой рабочей генерацией, с разрешением B по получателю; проверить Router -> агент с меньшим бюджетом, worker override, budget-only режим и повторную оценку после Tool.
- [ ] 5.3 Ввести типизированные ошибки и обезличенные метрики объёма/плашек, сохраняя атомарную подготовку и cleanup; проверить огромный свежий результат, один лишь protected context, provider overflow и отсутствие повторного выполнения действий.
- [ ] 5.4 Документировать входной бюджет с резервом ответа, safety margin, ограничения оценщика и категории ошибок в `docs/setup.md`/`docs/context.md`; сверить формулы и примеры с unit-тестами.

## 6. Later stage: checkpoint identity and persistence

- [ ] 6.1 Добавить schema checkpoint, канонизацию origins/source окончательного представления и fingerprints policy/capabilities/inlet view; unit-тестами проверить неизменный префикс с новым хвостом, edit/fork, смену N, отключение ресурса и отсутствие самоинвалидации из-за cache/usage/timestamps.
- [ ] 6.2 Связать origins с persisted message/output позициями и выбирать закрытую границу записи; проверить повторные call IDs, отсутствие устойчивой привязки и request-local fallback для активных раундов/временного чата.
- [ ] 6.3 Реализовать чтение и merge `meta.lite_context.checkpoints` в адаптере под штатной транзакцией OWUI с повторной проверкой actor/lineage/hash и синхронизацией обоих хранилищ сообщений; native-тестом проверить reload после перезапуска, параллельные записи, сохранность output/чужих metadata и отказ устаревшему checkpoint.
- [ ] 6.4 Ограничить число/размер записей и обработать storage failure как диагностируемый request-local кэш; проверить eviction, ошибку записи, отсутствие grants/migration и игнорирование клиентского checkpoint.
- [ ] 6.5 Исключить namespace всех checkpoints из сообщений/metadata, доступных модели и Tools, независимо от включённости политики; проверить, что checkpoint другого view нельзя получить через raw metadata, а серверные записи не удалены.
- [ ] 6.6 Описать persisted/request-local режимы, invalidation и поведение при откате в `docs/context.md`; сверить документ с native concurrency/reload сценариями.

## 7. Later stage: bounded isolated summarizer

- [ ] 7.1 Добавить adapter-вызов выделенной summary model с проверкой actor access, отдельным request state и payload allowlist, без Router Pipe/Tools/native preprocessing; native-тестом проверить фактический запрос, отсутствие наследованных metadata/prompts/callables и неизменность родительского state.
- [ ] 7.2 Реализовать контракт summary и проверку ответа, input/output limits, общий deadline, max_chunks и отмену; проверить пустой/невалидный ответ, Tool calls, слишком большой блок, таймаут, Stop и отсутствие рекурсии/исполнения Tools.
- [ ] 7.3 Добавить выбор ограниченных порций из старого N-limited префикса и chaining предыдущего summary; тестами доказать отсутствие скрытых маркеров, raw Tool results, Skill/native content и foreign data во всех summary-запросах.
- [ ] 7.4 Описать конфигурацию summary model, её отдельные бюджеты/стоимость, требования доступа и bounded failure в `docs/setup.md`; проверить пример настроек на изолированном тестовом провайдере.

## 8. Later stage: compaction planning and effective context integration

- [ ] 8.1 Реализовать planner целых старых ходов и завершённых раундов текущего хода с protected tail, trigger/target и резервом summary; проверить неделимые historical delegate pairs и batches/reasoning, N=null, отсутствие безопасной границы, невозможность автоматического уменьшения N и сохранность свежих вложенных результатов внутри неделимых historical handoff pairs.
- [ ] 8.2 Встроить checkpoint reuse, summary reference и окончательный budget gate перед binding/publish; проверить идемпотентность, cache hit без повторного summary, продление покрытия и fallback при summary failure только в пределах B.
- [ ] 8.3 Проверить ошибки/отмену на стыке planner, persistence и рабочего вызова: checkpoint не публикуется после Stop или изменения исходного префикса, невалидная подготовка не переключает registry, выполненные Tools не повторяются; сохранить тестовые счётчики действий.
- [ ] 8.4 Добавить native streaming регрессии длинной текущей Tool-цепочки, A -> B -> A и двух worker одной модели с callable refresh; проверить полный source, scoped summaries, актуальный native context, свежие Skills и отсутствие cache metadata у Tools.
- [ ] 8.5 Обновить `docs/context.md`, README и `docs/compatibility.md` новым поведением summary и его ограничениями; проверить примеры защищённых данных, явного отказа и сохранения native compaction off.

## 9. Standalone and end-to-end acceptance

- [x] 9.1 Пересобрать обе Functions через `tools/build_functions.py`; проверить импорт экспортов без `src`/checkout и выполнить соответствующие unit/native suites в изолированной официальной OWUI 0.13.3, не подменяя её локальной старой `.venv`.
- [ ] 9.2 Выполнить HTTP-приёмку с тестовым provider и сохранённым чатом: три пользовательских вопроса с двумя агентами, N=1/2, отдельные historical tool messages и один system/XML; позднее budget-triggered summary, reload/edit/fork, parallel -> final agent, переполнение защищённого пакета и Stop; сохранить обезличенный отчёт с фактическими provider payload assertions и счётчиками выполнений.
- [ ] 9.3 Провести смысловую приёмку GLM-5.3-Flash на выборе агента по XML, привязке результатов к исходным вопросам и точных датах/IDs, различении решений/гипотез и продолжении работы после summary; сохранить результат либо явно оставить задачу незавершённой при отсутствии модели, не выдавая deterministic проверки за смысловые.
- [ ] 9.4 Проверить режим прежних Valves, отключение новой политики после checkpoints и откат согласованной пары Functions на тестовой установке; подтвердить восстановление исходного представления без потери сообщений, файлов и grants.

## Workflow follow-up

- После реализации и приёмки согласовать sync/archive пересекающихся context changes так, чтобы старые delta не восстановили прежний запрет summary или наследование родительской переписки работником.
- Рабочую установку обновлять только отдельным действием по инструкции миграции, сохранив предыдущие Functions и Valves; эта задача сама по себе не разрешает deployment.
