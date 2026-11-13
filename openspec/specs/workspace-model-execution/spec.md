# workspace-model-execution Specification

## Purpose

Запускать выбранного агента с настройками его Workspace Model, автоматически и изолированно подготавливая возможности для OWUI v0.13.3 в закрытом контуре.

## Requirements

### Requirement: Router base capabilities come from administrative valves

Система SHALL получать настраиваемые базовые Tools и Skills Router только из административных Valves `base_tool_ids` и `base_skill_ids`. Их подключение SHALL NOT требовать attachments в карточке Router или создавать в чате выбранные пользователем ресурсы. Клиентские параметры и UserValves SHALL NOT изменять этот набор. Встроенный `lite_delegate` SHALL добавляться Pipe отдельно при непустом персональном реестре; default обоих списков SHALL быть `[]`.

#### Scenario: Empty router attachments still provide the configured base

- **WHEN** списки Tools и Skills в карточке Router пусты, а администратор указал базовые IDs в Valves Router Preparation
- **THEN** Pipe подключает эти базовые ресурсы при обработке запроса
- **AND** они не отображаются как выбранные пользователем подключения в поле ввода или переключателях ресурсов чата
- **AND** фактические вызовы Tools и чтение Skills могут отображаться в истории ответа

#### Scenario: User tries to override the base through request data

- **WHEN** пользователь передаёт пустые или другие списки базовых ресурсов через запрос, пользовательские настройки или переключатели
- **THEN** Router сохраняет набор из административных Valves
- **AND** IDs базовых ресурсов не записываются в пользовательский выбор ради их подключения

#### Scenario: Administrator changes the base configuration

- **WHEN** администратор меняет `base_tool_ids` или `base_skill_ids` перед новым сообщением пользователя
- **THEN** новое сообщение использует актуальный набор из Valves
- **AND** предыдущий набор не восстанавливается из истории или карточки Router

#### Scenario: No base skills are configured

- **WHEN** `base_skill_ids` пуст, но пользователю доступен агент из `AGENTS` с пригодным routing Skill
- **THEN** Router получает этот routing Skill из персонального реестра
- **AND** отсутствие базовых Skills не отключает маршрутизационные Skills

### Requirement: Selected workspace model owns inference settings

Система SHALL применять системный промпт, базовую LLM и параметры генерации выбранной Workspace Model. Развёрнутые настройки Router SHALL NOT перекрывать настройки агента. На продолжениях Tool calls SHALL сохраняться тот же источник настроек без накопления копий системного промпта.

#### Scenario: Router and agent have different temperatures and prompts

- **WHEN** у Router и A заданы разные системные промпты, `temperature` и `top_p`
- **THEN** вызов A использует промпт и значения A
- **AND** поведенческие инструкции Router отсутствуют в рабочем системном контексте A

#### Scenario: Agent continues after a tool result

- **WHEN** A вызывается повторно после результата своего Tool
- **THEN** A сохраняет собственные параметры и системный промпт
- **AND** системные инструкции A не дублируются с каждым продолжением

### Requirement: Capabilities are attached automatically

Система SHALL автоматически подготавливать Tools, настроенные серверные MCP-подключения, рабочие Skills и явно привязанное серверное терминальное подключение выбранного агента из его Workspace Model. При отсутствии терминальной привязки терминальные возможности SHALL отсутствовать. Обязательное прикрепление дополнительного preparation-фильтра к каждому агенту SHALL NOT требоваться.

#### Scenario: Administrator configures an existing agent

- **WHEN** у A в карточке выбраны Tools и Skills, а A добавлен в каталог Router
- **THEN** handoff автоматически предоставляет A его настроенные возможности
- **AND** администратор не копирует IDs рабочих ресурсов в каталог Router

#### Scenario: Model attachments change

- **WHEN** перед следующим вызовом A администратор заменил его рабочий Skill или отсоединил Tool
- **THEN** подготовка A использует актуальные attachments
- **AND** отсоединённый ресурс не восстанавливается из старого состояния Router

#### Scenario: Administrator binds a terminal only to one agent

- **WHEN** серверная конфигурация A привязана к T, а у B терминальная привязка отсутствует
- **THEN** обычный, параллельный и финальный запуск A автоматически получают серверные Tools T
- **AND** B продолжает работать без терминала, без дополнительного Filter и изменений `AGENTS`

### Requirement: User toggles do not change model capabilities

Набор возможностей в запросах через Router SHALL определяться конфигурацией выбранной модели и штатными серверными ограничениями. Переключатели, списки ресурсов и упоминания Skills из пользовательского запроса SHALL NOT отключать обязательный ресурс или добавлять неприкреплённый ресурс.

#### Scenario: Client attempts to disable required attachments

- **WHEN** пользовательский запрос содержит пустые либо изменённые списки Tools/Skills для A
- **THEN** A получает разрешённый настроенный набор своей модели
- **AND** изменение пользовательского списка не отключает обязательные attachments

#### Scenario: Client attempts to add another agent resources

- **WHEN** запрос или история упоминает Tool либо Skill B, не прикреплённый к A
- **THEN** этот ресурс не добавляется в текущие возможности A только на основании упоминания

#### Scenario: Client attempts to disable native calling

- **WHEN** пользовательский запрос пытается переключить обязательный native function calling комплекта в legacy
- **THEN** выполнение через Router сохраняет установленный сервером native режим
- **AND** клиентский параметр не отключает delegate или обязательные Tools агента

### Requirement: Router and agents have separate capability sets

Router SHALL получать базовые Tools и Skills из административных Valves и routing Skills персонального реестра. После handoff агент SHALL получать свой набор из своей Workspace Model без маршрутизирующих Tools и инструкций Router. Переключение SHALL заменять набор возможностей, не объединяя модели.

#### Scenario: Router delegates to an agent with a specialized tool

- **WHEN** у Router доступны `lite_delegate` и базовый Tool, а у A — специализированный Tool
- **THEN** до передачи специализированный Tool A не становится доступным Router
- **AND** после передачи A не получает базовый Tool Router или `lite_delegate` без собственной допустимой настройки
- **AND** вложенная маршрутизация через `lite_delegate` остаётся отключённой в MVP

### Requirement: Skill discovery is scoped to the current model

Система SHALL ограничивать Skills текущим набором: у Router это `base_skill_ids` из Valves и разрешённые routing Skills; у агента — Skills его Workspace Model. Автоматический широкий каталог OWUI SHALL NOT добавлять остальные Skills пользователя. Ограничение SHALL применяться повторно на Tool-продолжениях.

#### Scenario: User can read skills belonging to two agents

- **WHEN** пользователю доступны Skills A и B, но выбранная модель содержит только A
- **THEN** модель получает каталог A
- **AND** B отсутствует в её каталоге, даже если штатный OWUI умеет его перечислить

#### Scenario: A skill is detached during a conversation

- **WHEN** Skill удалён из конфигурации выбранной модели до следующей подготовки
- **THEN** его каталог и управляемые инструкции не включаются в новый рабочий контекст
- **AND** прежняя загрузка Skill не расширяет актуальный набор

### Requirement: Skill reads enforce the configured scope

Чтение через `view_skill`, `read_skill_file` и `grep_skill_files` SHALL проверять принадлежность Skill текущему набору перед штатной проверкой прав OWUI. Проверка SHALL действовать при реальном исполнении, включая обновление callable и продолжения.

#### Scenario: Model requests another readable skill by known id

- **WHEN** A вызывает любую операцию чтения для доступного пользователю Skill B, не входящего в набор A
- **THEN** операция возвращает ошибку ограничения модели
- **AND** содержимое B не передаётся A

#### Scenario: Skill file is read after callable refresh

- **WHEN** OWUI обновляет вызываемую функцию между `view_skill` и чтением файла
- **THEN** проверка допустимого Skill сохраняется
- **AND** разрешённый Skill читается со штатными ограничениями файлов и страниц

### Requirement: Native user permissions remain effective

Система SHALL сохранять пользователя запроса при проверках доступа к моделям, чатам и обычным файлам и при исполнении Tools. Настроенные Tools, Skills, MCP и терминальные подключения SHALL проверяться с правами владельца Workspace Model без отдельных read-grants пользователя. Доступ через модель SHALL NOT выдавать отдельные grants на ресурсы. Недоступность владельцу обязательного ресурса SHALL вызывать понятную ошибку подготовки вместо частично подготовленного запуска.

#### Scenario: Model is accessible but its attached resource is not

- **WHEN** пользователю разрешена модель A, но её обязательный Tool или Skill недоступен владельцу Workspace Model
- **THEN** A не запускается с неполным набором
- **AND** пользователь получает ошибку недоступного ресурса, пригодную для обращения к администратору

#### Scenario: Tool checks user specific data access

- **WHEN** Tool A проверяет личность пользователя или использует его авторизацию MCP
- **THEN** он получает пользователя текущего запроса
- **AND** Router не подменяет его администратором или владельцем модели

#### Scenario: Configured resources need no separate user grants

- **WHEN** пользователю разрешена модель A, её обязательные ресурсы доступны владельцу Workspace Model, а пользователь не имеет отдельных read-grants на эти ресурсы
- **THEN** A запускается с полным настроенным набором Tools, Skills, MCP и явно привязанным терминалом при его наличии
- **AND** Tools сохраняют пользователя чата, его UserValves и пользовательскую авторизацию без выдачи новых grants

### Requirement: Destination preparation remains atomic

Ошибка подготовки выбранной модели SHALL NOT оставлять частично переключённые возможности. Прикреплённые inlet-фильтры модели SHALL получать подготовленный контекст; конфликт с обязательными ограничениями Skills или терминальной привязки SHALL вызывать ошибку до вызова провайдера. Недоступный настроенный терминал и конфликт имён его Tools SHALL NOT приводить к запуску с неполным набором или к молчаливой подмене другого Tool.

#### Scenario: Destination inlet filter sees its own prepared context

- **WHEN** к A прикреплён дополнительный inlet-фильтр
- **THEN** он получает историю, системный контекст и возможности A
- **AND** его допустимые изменения учитываются в запросе модели и контексте её Tools

#### Scenario: Filter replaces a protected skill reader

- **WHEN** дополнительный фильтр подменяет обязательную операцию чтения Skills неограниченной версией
- **THEN** подготовка завершается ошибкой конфигурации до генерации
- **AND** чужие Skills не становятся доступны из-за подмены

#### Scenario: Configured terminal is unavailable

- **WHEN** привязка некорректна, подключение отсутствует, выключено, недоступно владельцу, не предоставляет разрешённых серверных Tools либо требует отсутствующий сохранённый контекст
- **THEN** подготовка возвращает понятную ошибку до вызова провайдера
- **AND** активные возможности Router или другого агента не заменяются частичным набором A

#### Scenario: Terminal tool name collides with an attached resource

- **WHEN** T и другой настроенный ресурс A предоставляют один и тот же публичный Tool name
- **THEN** подготовка сообщает конфликт до генерации без перезаписи callable

#### Scenario: Destination filter changes terminal authority

- **WHEN** inlet-фильтр меняет терминальную привязку или добавляет, удаляет либо подменяет защищённые терминальные Tools
- **THEN** подготовка завершается ошибкой до вызова провайдера
- **AND** это правило действует также для агента без терминала

### Requirement: Standalone offline installation on the supported version

Комплект SHALL устанавливаться как один Filter и один Pipe со встроенным `lite_delegate` на проверенном релизе OWUI. Отдельный Workspace Tool делегирования и его read-grant SHALL NOT требоваться. После установки эти файлы SHALL работать без импорта модулей данного репозитория и без доступа к интернету, используя заранее установленные зависимости и доступные внутри контура модели и сервисы.

#### Scenario: Installation into an isolated OWUI instance

- **WHEN** два установочных файла загружены на проверенном релизе OWUI, а модели и ресурсы заранее настроены внутри контура
- **THEN** самостоятельный ответ и handoff выполняются без внешних загрузок и отдельного сервиса оркестрации
- **AND** отсутствие рабочего каталога этого репозитория на сервере не нарушает импорт Functions

### Requirement: Published tool names resolve to their own callable

Каждый опубликованный Tool SHALL иметь уникальное имя длиной не более 64 символов, совпадающее с ключом его исполняемого реестра. Переименование при коллизии SHALL сохранять привязку к исходному ресурсу и операции. Система SHALL NOT публиковать дублирующиеся имена или направлять вызов в другой ресурс из-за коллизии.

#### Scenario: A local and an OpenAPI tool have the same name

- **WHEN** выбранная модель содержит локальный Tool и OpenAPI Tool с именем `lookup`
- **THEN** модель получает две схемы с различающимися именами и соответствующими ключами callable
- **AND** вызов каждого опубликованного имени исполняет именно его локальную или серверную операцию
- **AND** переименованный OpenAPI Tool передаёт серверу исходное имя операции `lookup`

#### Scenario: Two OpenAPI servers expose the same operation name

- **WHEN** два настроенных OpenAPI-сервера одной модели предоставляют операцию `lookup`
- **THEN** опубликованные имена однозначно различают инструменты этих серверов
- **AND** каждый callable обращается к своему серверу с его авторизацией и исходным именем операции
- **AND** изменение порядка подключения сохраняет соответствие схем исполняемому реестру

#### Scenario: Long tool names collide

- **WHEN** допустимые имена двух Tools длиной 64 символа совпадают либо добавление namespace сервера превышает этот предел
- **THEN** каждый опубликованный alias имеет длину не более 64 символов и совпадает с ключом callable
- **AND** сокращение сохраняет различимость Tools, в том числе при повторной коллизии уже сокращённого alias
- **AND** сервер получает исходное имя операции и исходные аргументы

#### Scenario: Preparation fails before publishing an ambiguous registry

- **WHEN** подготовка не может сформировать непротиворечивые имена и callable обязательных Tools
- **THEN** генерация выбранной модели не начинается с неоднозначным набором
- **AND** ранее опубликованные возможности не заменяются частично подготовленными

### Requirement: OWUI release metadata is informational

Установочные Functions SHALL указывать версию разработки OWUI только в информационной metadata `developed_for_open_webui_version`. Runtime SHALL NOT проверять номер установленной версии либо отклонять запуск из-за его несовпадения. Metadata SHALL NOT задавать обязательную минимальную версию OWUI.

#### Scenario: Adapter starts without version metadata

- **WHEN** адаптер создаётся без доступной distribution metadata OWUI
- **THEN** создание не требует чтения версии и не вызывает отказ из-за версии

#### Scenario: Both exports describe their development release

- **WHEN** собраны оба установочных файла
- **THEN** их заголовки содержат `developed_for_open_webui_version` с фактической версией разработки комплекта
- **AND** `required_open_webui_version` и runtime version guard отсутствуют
