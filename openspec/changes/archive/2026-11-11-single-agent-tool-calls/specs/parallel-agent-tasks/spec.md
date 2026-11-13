# Spec Delta

## MODIFIED Requirements

### Requirement: The whole batch is validated before dispatch

`delegate_task` SHALL принимать ровно одно задание с `agent_id`, непустым `task` и необязательными `file_ids`. Вызовы одной фактической генерации SHALL образовывать группу от одного до административного предела заданий. Старый аргумент `tasks` SHALL отклоняться. Недопустимая структура, адресат или файл SHALL отклонять всю группу до dispatch. Параметры моделей и наборы ресурсов SHALL NOT задаваться аргументами задания.

#### Scenario: One entry is invalid

- **WHEN** группа содержит допустимый A и неизвестный Model ID либо Skill ID вместо B
- **THEN** ни одна задача группы не запускается
- **AND** ошибка указывает недопустимое задание

#### Scenario: File does not belong to the available inputs

- **WHEN** задание ссылается на файл вне доступных вложений родительского запроса
- **THEN** группа отклоняется до dispatch без получения этого файла

#### Scenario: One agent receives independent assignments

- **WHEN** несколько допустимых заданий адресованы одной модели
- **THEN** каждое задание получает отдельное исполнение и отдельный результат

#### Scenario: Legacy batch arguments

- **WHEN** вызов передаёт массив `tasks`
- **THEN** вся группа отклоняется до dispatch с ошибкой формата

#### Scenario: Four calls assign four tasks

- **WHEN** генерация содержит четыре допустимых вызова `delegate_task`
- **THEN** группа содержит ровно четыре задания, по одному на вызов

### Requirement: Administrative limits control availability

Система SHALL применять административный предел к числу вызовов `delegate_task` одной генерации и штатные ограничения OWUI. Предел SHALL принимать `0` либо целое число не меньше `1`. Значения по умолчанию SHALL составлять четыре задачи и 300 секунд. Значение предела задач `0` SHALL отключать функцию. При выключенных сабагентах или временном основном чате группа SHALL NOT запускаться.

#### Scenario: Administrator disables parallel tasks

- **WHEN** административный предел равен `0`
- **THEN** `delegate_task` не предлагается Router
- **AND** самостоятельный ответ и обычный handoff остаются доступны

#### Scenario: Batch exceeds the configured maximum

- **WHEN** модель передаёт больше заданий, чем допускает действующий предел
- **THEN** группа отклоняется целиком без скрытого усечения списка

#### Scenario: The current chat is temporary

- **WHEN** для запроса отсутствует поддерживаемый сохранённый основной чат
- **THEN** параллельные задачи не запускаются
- **AND** попытка вызова получает понятную причину недоступности

#### Scenario: One task is enabled

- **WHEN** предел равен `1` и Router выдаёт один допустимый вызов
- **THEN** одна задача исполняется как полная группа

### Requirement: Parallel execution waits for the whole group

Система SHALL запускать независимые задачи конкурентно с учётом действующих лимитов OWUI и выполнять полноценные цепочки Tools. Каждый `delegate_task` SHALL возвращать результат своего задания после терминального исхода всей группы. Общий пакет SHALL сохранять порядок Tool calls генерации независимо от порядка завершения. Конкурентный dispatch и привязку Tool results к исходным call IDs SHALL обеспечивать штатный Tool loop OWUI 0.13.3.

#### Scenario: Two independent tasks overlap

- **WHEN** доступны два места исполнения, а обе задачи требуют нескольких продолжений после Tools
- **THEN** интервалы фактического исполнения A и B пересекаются
- **AND** Router получает группу после завершения обеих цепочек

#### Scenario: Second task finishes first

- **WHEN** B завершился раньше A
- **THEN** итоговая группа содержит результат A перед B, если такой порядок был во входном списке
- **AND** завершение B само по себе не запускает C

#### Scenario: Four calls execute concurrently

- **WHEN** четыре допустимых вызова выданы одной генерацией и доступны четыре места исполнения
- **THEN** интервалы исполнения четырёх работников пересекаются и каждый Tool call получает собственный результат

### Requirement: Only one separate group is accepted per user request

Система SHALL принимать не более одной запущенной группы до handoff в текущем запросе. Генерация группы SHALL содержать только вызовы `delegate_task`. Смешение с другим Tool, совместный handoff, вторая группа и вложенная делегация SHALL отклоняться до запуска или передачи. Каждый исходный вызов SHALL исполняться не более одного раза; неоднозначная идентичность вызовов SHALL отклонять группу.

#### Scenario: Parallel work and handoff share a generation

- **WHEN** одна фактическая генерация содержит `delegate_task` и `lite_delegate`
- **THEN** группа не запускается и handoff не принимается

#### Scenario: Historical skill read is grouped with a later call

- **WHEN** OWUI объединил завершённое чтение Skill с отдельной последующей генерацией `delegate_task`
- **THEN** историческая группировка не считается смешанным текущим вызовом

#### Scenario: Worker attempts another delegation

- **WHEN** задача выполняется у A либо финальный handoff уже состоялся
- **THEN** маршрутизирующие инструменты не предоставляются этому исполнителю
- **AND** он не запускает вложенную группу через Router

#### Scenario: Another tool shares the generation

- **WHEN** генерация содержит `delegate_task` и другой Tool
- **THEN** ни одна задача группы не запускается

#### Scenario: Later generation attempts a second group

- **WHEN** после принятой группы Router выдаёт новый `delegate_task` в том же пользовательском запросе
- **THEN** новый работник не запускается

#### Scenario: Concurrent callable entries share one execution

- **WHEN** loop одновременно вызывает несколько callable одной группы
- **THEN** каждый работник запускается ровно один раз; одинаковые задания с разными уникальными Tool call IDs остаются отдельными исполнениями

### Requirement: Results report complete and attributable outcomes

Штатный loop SHALL связывать Tool result с исходным call ID и возвращать только результат своего задания; дублировать ID в теле SHALL NOT требоваться. Подтверждённый общий пакет SHALL содержать общий исход и по каждому заданию агента, терминальный статус, полный доступный результат, файлы, существующую ссылку на чат и ошибку при её наличии. Незавершённое или приостановленное выполнение SHALL NOT считаться успехом. Служебные credentials и tickets SHALL NOT попадать в результат.

#### Scenario: One worker fails

- **WHEN** A завершился успешно, а B получил ошибку
- **THEN** группа возвращает неполный исход с сохранением результата A и причиной ошибки B
- **AND** результат A не исчезает из-за ошибки другой задачи

#### Scenario: Native call returns before a terminal message

- **WHEN** сохранённый ответ ещё не завершён или ожидает подтверждения
- **THEN** он не становится успешным прогнозом из-за наличия строки ответа


### Requirement: Parallel work remains part of normal router inference

Router SHALL выбирать между самостоятельным ответом, параллельными заданиями через `delegate_task` и финальным handoff через `lite_delegate` в обычном цикле генерации с routing Skills. Отдельный LLM-классификатор SHALL NOT требоваться.

#### Scenario: Forecasts precede a presentation

- **WHEN** пользователь просит два независимых профильных прогноза и презентацию по ним
- **THEN** Router может поручить прогнозы A и B через `delegate_task`
- **AND** после успешного получения обоих результатов передать презентацию C отдельным `lite_delegate`

#### Scenario: General question needs no parallel work

- **WHEN** пользователь задаёт общий вопрос
- **THEN** Router может ответить самостоятельно без запуска группы или агента

### Requirement: Parallel delegation is an executable inline capability

Pipe SHALL предоставлять согласованные schema и callable `delegate_task` без отдельного Workspace Tool. Он SHALL исполняться штатным Tool loop, сохранять проверку привязки к текущему запросу и публиковаться только Router. Пользовательские переключатели SHALL NOT менять этот набор.

#### Scenario: Router attachments are empty

- **WHEN** установлены Filter и Pipe, параллельная работа разрешена, а Tools/Skills в карточке Router пусты
- **THEN** настоящий вызов `delegate_task` запускает задания
- **AND** фактическое исполнение не зависит от отдельного Tool или пользовательского включения ресурса

#### Scenario: Configured tool collides with the reserved name

- **WHEN** базовый ресурс пытается предоставить другой `delegate_task`, кроме штатного builtin, который Router намеренно заменяет
- **THEN** подготовка возвращает ошибку настройки до запуска моделей

#### Scenario: Router replaces the native builtin

- **WHEN** OWUI подготовил штатный builtin `delegate_task` для Router
- **THEN** Router предоставляет вместо него один специализированный `delegate_task` с `agent_id`
- **AND** вызов использует выбранную Workspace Model и её ресурсы, без fallback к текущей модели Router


### Requirement: Final handoff requires a successful group
После запуска группы финальный `lite_delegate` SHALL приниматься только после успешного завершения всех заданий. При неполном исходе handoff SHALL блокироваться, а Router SHALL сообщать доступные результаты и причины. После успешной группы SHALL сохраняться возможность самостоятельного ответа Router. После отклонённого дополнительного запуска handoff SHALL блокироваться с сохранением успешных результатов. Правило SHALL действовать и при native-отказе аргументов до входа в callable.

#### Scenario: Presentation follows two successful forecasts

- **WHEN** A и B успешно завершили задания и Router выбрал доступного C
- **THEN** C запускается после обоих и продолжает ответ в основном чате
- **AND** дополнительный дочерний чат для финального handoff не создаётся

#### Scenario: One required forecast is missing

- **WHEN** B завершился ошибкой, отменой или таймаутом
- **THEN** попытка handoff к C возвращает ошибку незавершённой группы
- **AND** C не запускается на частичных данных

#### Scenario: Additional group is rejected after one success

- **WHEN** первая группа успешно завершена, а Router пытается запустить вторую группу следующей генерацией
- **THEN** второй запуск и последующий handoff отклоняются
- **AND** результаты первой группы сохраняются, а отказ не выдаётся за второй успешный результат

#### Scenario: Native dispatch rejects the additional assignment arguments

- **WHEN** native dispatch отклонил аргументы дополнительного наблюдаемого вызова до входа в callable
- **THEN** последующий handoff блокируется по серверному аудиту
- **AND** отказ не переносится в новый пользовательский запрос

### Requirement: Supported deployment remains self contained
Возможность SHALL работать на проверенном релизе OWUI из прежних двух самостоятельных Functions без отдельного Tool, сервиса, миграции БД и патча OWUI. После установки SHALL использоваться только заранее доступные зависимости, модели и сервисы закрытого контура. Совместимость SHALL подтверждаться настоящим Tool loop и браузером.

#### Scenario: Exported functions run without the checkout

- **WHEN** оба установочных файла загружены в изолированный тестовый OWUI проверенного релиза
- **THEN** A и B выполняют настоящие Tools параллельно, а C получает оба результата
- **AND** импорт исходного пакета из checkout и доступ к интернету не требуются
