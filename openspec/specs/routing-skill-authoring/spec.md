# routing-skill-authoring Specification

## Purpose

Подготовка и обновление routing Skills отдельной обычной Workspace Model
Open WebUI: изучение конфигурации агента с правом записи, согласование
профиля с автором, сборка по шаблону и сохранение штатными средствами OWUI.

## Requirements

### Requirement: Standalone authoring agent package

Комплект SHALL предоставлять системный промпт, рабочий Skill `routing-skill-authoring`, standalone Workspace Tool и инструкцию настройки обычной Workspace Model с native function calling. Исполнение рендерера SHALL происходить внутри Tool без обязательных `execute_code`, терминала, административного API-ключа, пакета Router или checkout на сервере. Помощник SHALL NOT требовать Router Preparation или собственной записи в `AGENTS` для прямого использования.

#### Scenario: Administrator installs the helper

- **WHEN** администратор устанавливает Tool, импортирует Skill и подключает их к обычной модели с готовым системным промптом
- **THEN** модель может готовить routing Skills без подключения Code Interpreter или терминала
- **AND** настройка Router Pipe остаётся отдельной от настройки помощника

### Requirement: Writable model discovery and inspection

Каталог SHALL возвращать только модели со штатным правом записи пользователя OWUI и пагинацией. Чтение конфигурации/прикреплённого Skill, рендер и сохранение SHALL заново проверять право записи на целевую модель. Пользователь SHALL загружаться из БД по ID серверной инъекции `__user__`. Переданная роль или права владельца помощника SHALL NOT заменять права пользователя чата.

#### Scenario: User has only model read access

- **WHEN** пользователю разрешено пользоваться моделью, но не редактировать её
- **THEN** модель не включается в каталог помощника
- **AND** прямой вызов чтения её конфигурации или рендера профиля отклоняется
- **AND** переданное в словаре пользователя значение `role=admin` не расширяет его права

#### Scenario: Native model write policies apply

- **WHEN** у пользователя есть владение моделью, индивидуальный или групповой write-grant либо разрешённый штатной политикой административный обход
- **THEN** поиск и чтение конфигурации используют это право по правилам OWUI

#### Scenario: Permission is revoked between calls

- **WHEN** после успешного изучения модели право записи пользователя отзывается
- **THEN** следующий вызов, использующий эту модель, повторяет проверку и отклоняется

#### Scenario: A source page contains no writable models

- **WHEN** на странице доступного каталога нет writable моделей, но следующие страницы существуют
- **THEN** возвращается пустой `items` и непустой `next_page`
- **AND** инструкции помощника требуют продолжить поиск по страницам

### Requirement: Resource inspection preserves actor permissions

Обзор SHALL включать сохранённый промпт, идентичность и настройки модели, каталоги прикреплённых ресурсов и наличие терминала. Тексты Skills и сохранённые схемы локальных Tools SHALL читаться с правами пользователя чата; write на модель SHALL NOT открывать приватные ресурсы. Недоступные ресурсы SHALL обозначаться как пробелы. Обзор SHALL NOT исполнять чужой Tool-код, читать его исходник/Valves или возвращать секреты подключений.

#### Scenario: Stored model configuration is inspected

- **WHEN** пользователь с write-доступом изучает модель
- **THEN** результат содержит ID, имя, базовую модель, активность, сохранённый системный промпт, capabilities, builtin categories, IDs Tools/Skills и признак привязанного терминала

#### Scenario: Writable agent has a private Skill

- **WHEN** пользователь имеет write на модель, но не имеет read на её Skill
- **THEN** обзор сообщает недоступность Skill и не возвращает его инструкции
- **AND** предоставление пользователю read на Skill открывает штатное чтение его файлов

#### Scenario: Local Tool source cannot safely be imported

- **WHEN** у пользователя есть read на прикреплённый Tool, исходник которого выбрасывает ошибку при импорте
- **THEN** обзор возвращает сохранённые схемы без исполнения исходника
- **AND** Python-код и Valves не включаются в результат обзора

### Requirement: Explicit inspection limits and paged Skill files

Обзор SHALL отличать сохранённую конфигурацию от доказанной работоспособности и обозначать неизученные ресурсы. `read_agent_skill_file` SHALL на каждом вызове проверять прикрепление Skill к writable модели и read-доступ пользователя, возвращать текст страницами с `next_offset` и отклонять нечитаемые/бинарные файлы.

#### Scenario: Configuration cannot establish effective runtime capabilities

- **WHEN** помощник возвращает обзор
- **THEN** ограничения указывают, что MCP/OpenAPI-схемы, содержимое Knowledge, наследуемые базовые промпты и динамические Filters не изучены
- **AND** наличие схем и настроек не считается проверкой работоспособности сервиса

#### Scenario: Skill is detached before the next page

- **WHEN** после чтения страницы рабочий Skill отсоединяется от модели
- **THEN** следующий вызов чтения отклоняется, даже если личный read на Skill сохранился

#### Scenario: Agent depends on an external Tool server

- **WHEN** модель содержит серверный Tool ID
- **THEN** обзор возвращает его статус `not_inspected`
- **AND** инструкции требуют уточнить существенные непроверенные возможности у автора

### Requirement: Author agreed routing profile

Рабочий Skill и промпт SHALL требовать изучить адресата и согласовать с автором оба типа примеров перед окончательным профилем. Профиль SHALL описывать выбор, исключения, входы, самостоятельное поручение, финальную обработку и результат. Протокол Router и рабочие алгоритмы SHALL оставаться в своих инструкциях. Существенные непроверенные возможности SHALL оставлять профиль черновиком до уточнения.

#### Scenario: Author requests a profile for another agent

- **WHEN** помощник изучил настройки выбранной модели
- **THEN** он предлагает границы и оба набора примеров на основании этой модели
- **AND** перед окончательной сборкой ожидает согласования примеров автором
- **AND** отсутствие роли в самостоятельных поручениях или финальной обработке указывается явно
- **AND** возможности самого помощника не приписываются изучаемой модели

### Requirement: Deterministic rendering from structured fields

Рендер, сохранение и переносимый CLI SHALL использовать один алгоритм и основной шаблон. Рендерер SHALL проверять десять текстовых полей и два непустых массива примеров, отклонять отсутствующие/неизвестные поля, неверные типы и placeholders, экранировать YAML и включать примеры в нужные разделы. Сохранение SHALL собирать Markdown на сервере из полей без приёма произвольного готового Markdown.

#### Scenario: Invalid fields would produce an incomplete Skill

- **WHEN** поля не содержат негативных примеров либо включают незаменённый placeholder
- **THEN** рендер и сохранение отклоняются без частично сформированного Skill

### Requirement: Routing Skill target metadata

Созданный `SKILL.md` SHALL содержать YAML `name`/`description` Skill и вложенные `metadata.agent_id`/`metadata.agent_name` адресата. Рендерер SHALL брать адресата в YAML и тексте из одних полей и безопасно экранировать YAML-строки. Метаданные SHALL сохраняться в содержимом файла и SHALL NOT заменять `AGENTS`, штатные проверки доступа или актуальное имя модели в манифесте Router. Skills без этих полей SHALL сохранять работоспособность.

#### Scenario: Agent name differs from Skill name

- **WHEN** автор готовит Skill `route-forecast` для модели `forecast-agent` с именем «Прогнозист»
- **THEN** YAML содержит `name=route-forecast`, `metadata.agent_id=forecast-agent` и `metadata.agent_name=Прогнозист`
- **AND** адресат в тексте профиля совпадает со значениями метаданных
- **AND** после сохранения штатными средствами OWUI эти поля остаются в `SKILL.md`

#### Scenario: Target identity contains YAML punctuation

- **WHEN** имя или ID адресата содержит двоеточие, кавычки или обратный слеш
- **THEN** YAML parser восстанавливает исходные значения без искажения
- **AND** экранирование YAML не изменяет обычное имя и ID в Markdown-тексте

#### Scenario: Existing routing Skill lacks target metadata

- **WHEN** в `AGENTS` зарегистрирован пригодный Skill без `metadata.agent_id` и `metadata.agent_name`
- **THEN** runtime использует существующую связь и проверки без требования миграции Skill
- **AND** следующий рендер профиля по новому шаблону добавляет поля адресата

### Requirement: Native Skill persistence and version protection

Сохранение SHALL использовать штатные обработчики OWUI, actor-права и историю. Пустой `expected_version_id` SHALL означать только создание; обновление SHALL требовать write на Skill и актуальную версию. Новый Skill SHALL быть активным и приватным для автора. Обновление SHALL сохранять владельца, grants, активность и файлы кроме `SKILL.md`. Конфликт SHALL требовать перечитать изменения перед новой попыткой.

#### Scenario: Empty version requests creation only

- **WHEN** сохранение передаёт пустой `expected_version_id` и такой Skill ID уже существует
- **THEN** штатный обработчик отклоняет создание без перезаписи существующего Skill

#### Scenario: Existing routing Skill has attachments and grants

- **WHEN** помощник обновляет Skill с актуальной версией
- **THEN** новый профиль и карточные метаданные сохраняются с историей OWUI
- **AND** вложения кроме `SKILL.md`, права, владелец и активность остаются прежними

#### Scenario: Concurrent author saved a newer version

- **WHEN** помощник передаёт устаревший `expected_version_id`
- **THEN** OWUI возвращает конфликт версий
- **AND** текущие текст, вложения и версия не меняются

#### Scenario: Model write does not grant Skill write

- **WHEN** пользователь может редактировать адресата, но не существующий routing Skill
- **THEN** обновление этого Skill отклоняется без его изменения

### Requirement: Saving and Router registration are distinct

Успешное сохранение SHALL вернуть Skill ID, имя, версию, активность и пару Model ID → Skill ID. Tool SHALL NOT менять `AGENTS`, permissions или модели; состояние подключения SHALL быть непроверенным. Инструкция SHALL объяснять read-доступ владельца Router, добавление пары без удаления/дублирования записей и проверку позитивного, негативного и неоднозначного запросов.

#### Scenario: New Skill is saved successfully

- **WHEN** штатный обработчик подтвердил создание Skill
- **THEN** помощник сообщает сохранённые ID/версию и запись для реестра
- **AND** отдельно указывает ручной шаг подключения к Router
- **AND** успешное сохранение не выдаётся за проверенную доступность через Router

### Requirement: Reproducible offline distribution

Сборка SHALL выпускать standalone Python Tool, импортируемый ZIP рабочего Skill и общий ZIP с Tool, Skill, промптом и инструкцией без скачивания зависимостей. Шаблон и рендерер SHALL встраиваться из канонических исходников; порядок файлов и ZIP timestamps SHALL быть фиксированы. Дистрибутив SHALL NOT содержать Python-кэши. Автоматические проверки SHALL покрывать холодный импорт, воспроизводимость, штатные OWUI schemas и импорт ZIP; проверки native SHALL работать с изолированной БД.

#### Scenario: Same sources are built twice

- **WHEN** сборка выполняется повторно без изменения исходников
- **THEN** все три установочных артефакта совпадают побайтно
- **AND** Tool импортируется без checkout и пакет рабочего Skill принимается штатным parser OWUI
