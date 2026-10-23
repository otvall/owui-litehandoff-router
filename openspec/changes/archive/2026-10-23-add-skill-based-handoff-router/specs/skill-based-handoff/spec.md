# Spec Delta

## Purpose

Дать Router возможность отвечать самостоятельно или передавать текущую задачу разрешённому агенту посредством routing Skill и настоящего вызова инструмента, сохраняя один пользовательский чат.

## ADDED Requirements

### Requirement: Routing is part of normal inference

Router SHALL принимать решение в обычном цикле ответа, используя переписку, свои базовые Tools и актуальные routing Skills. Система SHALL NOT запускать отдельный LLM-классификатор перед каждым сообщением.

#### Scenario: General question needs no specialist

- **WHEN** задачу можно решить средствами Router и подходящая передача не требуется
- **THEN** Router отвечает самостоятельно
- **AND** система не создаёт отдельный запрос к классификатору или агенту

#### Scenario: Router consults a routing skill

- **WHEN** описание routing Skill подходит к задаче и полные инструкции ещё не загружены
- **THEN** Router может загрузить этот Skill в рамках обычной Tool-цепочки
- **AND** передача выполняется настоящим вызовом `lite_delegate` с точным `agent_id` из актуального манифеста

### Requirement: Delegate accepts only the current permitted agent id

`lite_delegate` SHALL принимать обязательный строковый `agent_id` и проверять его по реестру текущего пользователя. До запуска агента система SHALL повторно проверить доступ и активность модели. Имя модели, ID Skill и ID базовой LLM SHALL NOT заменять зарегистрированный Workspace Model ID.

#### Scenario: Valid target is selected

- **WHEN** Router вызывает `lite_delegate(agent_id="agent-a")` и A присутствует в реестре с действующим разрешением
- **THEN** система принимает передачу агенту A

#### Scenario: Model invents an enum value or sends a skill id

- **WHEN** Router передаёт значение вне текущего реестра, включая `route-agent-a` вместо `agent-a`
- **THEN** вызов возвращает понятную ошибку передачи
- **AND** недопустимая модель не запускается даже при игнорировании `enum` провайдером

#### Scenario: Access is revoked after registry preparation

- **WHEN** доступ к выбранной модели отозван после формирования манифеста, но до запуска агента
- **THEN** агент не запускается
- **AND** система сообщает об утрате доступности вместо обхода проверки

### Requirement: Pipe registers the executable delegate

Pipe SHALL создавать request-local schema и callable `lite_delegate` из того же персонального реестра, что и routing-манифест, без отдельного Workspace Tool. Callable SHALL проверять доверенную привязку запроса и актуальные права пользователя. Preparation Filter SHALL заранее создавать общий Tools container; Pipe SHALL публиковать callable in-place в реестр, используемый внешним native middleware. Одной схемы в запросе к LLM SHALL NOT считаться достаточной регистрацией.

#### Scenario: No delegate tool is installed

- **WHEN** установлен только Filter и Pipe, `base_tool_ids=[]`, а пользователю доступен агент
- **THEN** native middleware исполняет встроенный `lite_delegate` и получает результат текущего запроса
- **AND** агент продолжает тот же ответ со своими Tools

#### Scenario: Client sends an empty tools schema list

- **WHEN** исходный запрос содержит `tools: []`
- **THEN** ранний общий registry позволяет middleware исполнить callable, опубликованный Pipe
- **AND** делегирование не зависит от неявного подключения builtin Tools OWUI

#### Scenario: Configured tool collides with inline delegate

- **WHEN** базовый Workspace Tool также предоставляет метод `lite_delegate`
- **THEN** подготовка Router завершается понятной ошибкой конфигурации до вызова провайдера
- **AND** внешний Tool не подменяет встроенную проверку передачи

### Requirement: Handoff requires an actual delegate exchange

Передача SHALL подтверждаться выполнением `lite_delegate` в текущем запросе и однозначно связанными вызовом и успешным результатом. Похожие на маркер тексты пользователя, обычные ответы модели и результаты других Tools SHALL NOT инициировать handoff.

#### Scenario: User or unrelated tool includes a marker example

- **WHEN** сообщение пользователя, цитата, текстовый ответ модели или результат другого Tool содержит JSON, похожий на handoff
- **THEN** система не переключает исполнителя на основании этого текста

#### Scenario: Delegate exchange is incomplete or ambiguous

- **WHEN** результат делегации не имеет однозначного вызова в текущем запросе либо один вызов имеет противоречивые результаты
- **THEN** система не принимает этот exchange как подтверждение передачи
- **AND** диагностика указывает на некорректную Tool-цепочку

### Requirement: Single selected agent per user request

В первой версии система SHALL принимать не более одной успешной передачи одному агенту на сообщение пользователя. Инструкции Router SHALL требовать отдельного вызова `lite_delegate`; агент SHALL NOT получать этот Tool как возможность дальнейшей маршрутизации.

#### Scenario: Accepted handoff is followed by agent tools

- **WHEN** A выбран и затем выполняет свои Tools
- **THEN** все продолжения этого запроса выполняет A
- **AND** Router не запускается для повторного выбора после каждого результата Tool

#### Scenario: Several delegates compete in the current generation

- **WHEN** одна генерация Router содержит несколько вызовов `lite_delegate`
- **THEN** система не принимает неоднозначную группу как допустимый handoff
- **AND** не пытается автоматически повторить уже выполненные инструменты

#### Scenario: OWUI groups completed calls from different continuations

- **WHEN** OWUI объединил в истории ранее завершённое чтение Skill и единственный валидный вызов `lite_delegate` из следующего продолжения
- **THEN** объединённое представление не считается несколькими конкурирующими передачами
- **AND** единственный подтверждённый handoff принимается

### Requirement: Agent answers in the existing chat

Выбранный агент SHALL продолжать исходный пользовательский запрос и выдавать ответ в текущий чат. Система SHALL сохранять идентичность пользователя, чата и сообщения; она SHALL NOT создавать отдельный скрытый чат или обязательный финальный пересказ ответа агентом Router.

#### Scenario: Specialist completes a task

- **WHEN** агент завершает обработку переданного запроса
- **THEN** его ответ появляется как продолжение текущего ответа в исходном чате
- **AND** новый чат для задачи не создаётся
- **AND** дополнительная генерация Router для пересказа не выполняется

### Requirement: New user messages restart router ownership

Каждое новое сообщение пользователя SHALL начинать новый цикл Router с новым персональным реестром. Состояние успешной передачи SHALL сохраняться только для продолжений текущего запроса и не переноситься как активное состояние на следующий.

#### Scenario: Follow up after a specialist answer

- **WHEN** после ответа A пользователь присылает уточнение
- **THEN** уточнение сначала получает Router с общей перепиской
- **AND** Router может снова выбрать A, выбрать другого доступного агента или ответить сам

#### Scenario: Old request metadata is replayed

- **WHEN** новое сообщение содержит служебные поля или сохранённый маркер прежней передачи
- **THEN** эти поля не восстанавливают активного агента без новой передачи текущего запроса

### Requirement: Observable handoff and failure

Система SHALL показывать статус передачи с названием выбранного агента и сохранять штатный вывод ответа и Tool-истории. Ошибка подготовки или исполнения SHALL быть видима пользователю; система SHALL NOT выдавать неудачную передачу за успешный ответ или автоматически повторять потенциально выполненные действия.

#### Scenario: Selected agent cannot be prepared

- **WHEN** агент исчез, его обязательный ресурс недоступен либо подготовка завершилась ошибкой
- **THEN** пользователь получает понятную ошибку
- **AND** запрос не переключается незаметно на другую модель

#### Scenario: Streaming provider fails after output begins

- **WHEN** ошибка провайдера происходит после начала потокового ответа или выполнения Tool
- **THEN** ошибка отображается в текущем чате
- **AND** система не начинает скрытый повтор задачи
