# Spec Delta

## ADDED Requirements

### Requirement: Release profiles have explicit supported capabilities

Система SHALL поддерживать профиль 0.11.1 с обычным handoff, локальными Tools, серверными OpenAPI/MCP, текстовыми Skills и необязательным серверным терминалом агента. Профиль 0.12.3 SHALL сохранять свой существующий набор возможностей. Профиль SHALL определяться установленным комплектом и версией OWUI; клиентские поля и UserValves SHALL NOT менять его.

#### Scenario: Legacy router executes an ordinary handoff

- **WHEN** на OWUI 0.11.1 установлены соответствующие Functions и пользователь вызывает разрешённого агента через `lite_delegate`
- **THEN** агент продолжает тот же чат со своими промптом, параметрами, Tools и Skills
- **AND** следующее реальное сообщение снова начинает цикл Router

#### Scenario: Legacy model has private local and server resources

- **WHEN** агент 0.11.1 настроен с приватными локальными Tools, OpenAPI/MCP и Skills, разрешёнными владельцу модели, а пользователь имеет доступ только к модели
- **THEN** агент получает полный настроенный набор с политикой `model-bound-resource-access`
- **AND** реальные локальные вызовы сохраняют пользователя и его UserValves, внешние вызовы — его credentials и идентификацию
- **AND** отдельные grants пользователю не выдаются, а обязательный недоступный ресурс вызывает атомарную ошибку подготовки

#### Scenario: Legacy requests cannot select another profile

- **WHEN** клиент или UserValves 0.11.1 указывают другую версию либо пытаются включить возможности профиля 0.12.3
- **THEN** действующий профиль и его ограничения сохраняются
- **AND** настройки разрешённых локальных Tools не становятся способом поменять профиль Router

#### Scenario: Current release keeps its existing behavior

- **WHEN** общий код обновлён, а на OWUI 0.12.3 используется его штатный комплект и прежняя конфигурация
- **THEN** сохраняются обычный handoff, три Skill readers, серверные Tools/MCP, параллельные задания, выборочные терминалы и ранее поддержанный Inline Visualizer в дочернем чате
- **AND** подключение поддержки 0.11.1 не отключает эти возможности и не требует перенастройки существующих Valves или `AGENTS`

### Requirement: Version-specific exports reject mismatched servers

Каждый установочный комплект SHALL явно указывать целевую версию OWUI и проверять точное соответствие 0.11.1 либо 0.12.3 до подготовки возможностей и генерации. Чужая или неподдержанная версия SHALL вызывать понятную ошибку без автоматического выбора другого профиля. Получение версии SHALL работать и при отсутствии package metadata в официальном Docker-образе.

#### Scenario: Legacy exports are installed on the current release

- **WHEN** комплект 0.11.1 запускается на OWUI 0.12.3
- **THEN** обе Functions сообщают несовпадение версии до исполнения ресурсов или вызова LLM
- **AND** комплект 0.12.3 аналогично отклоняет сервер 0.11.1

#### Scenario: Version metadata is unavailable in a source image

- **WHEN** официальный образ не содержит package metadata установленного OWUI
- **THEN** проверка использует штатную версию сервера и сохраняет точное соответствие комплекту
- **AND** соседняя неподдержанная patch-версия не принимается как совместимая без отдельной проверки

### Requirement: Legacy agents use optional model-bound terminals

Агент 0.11.1 SHALL получать терминал только из необязательной серверной привязки `meta.lite_handoff.terminal_id` с теми же границами доступа через модель. Отсутствующая, пустая или `null` привязка SHALL выключать терминал. Подключение, авторизация и сессии SHALL сохранять штатную политику 0.11.1; Router и другой агент SHALL NOT наследовать терминал.

#### Scenario: A terminal agent is followed by an ordinary agent

- **WHEN** в циклах Router 0.11.1 выбран A с привязкой T, а затем B без привязки
- **THEN** A выполняет команды и чтение файлов на T, а B работает только со своими Tools и Skills
- **AND** серверные инструкции, активный terminal ID и callables A не становятся возможностями B либо Router

#### Scenario: Parent selection differs from model binding

- **WHEN** пользователь выбрал в основном чате доступное подключение T1, а модель A привязана к T2
- **THEN** A использует только T2 с контекстом фактического основного чата
- **AND** знание terminal ID, URL или client metadata не предоставляет новое подключение

#### Scenario: A legacy terminal is unavailable or authorization is missing

- **WHEN** заданный терминал отсутствует, выключен, запрещён владельцу, конфликтует с capability модели либо требует отсутствующую пользовательскую авторизацию
- **THEN** возвращается явная ошибка без генерации с неполным набором
- **AND** терминал не подменяется другим подключением или авторизацией владельца

#### Scenario: Binding is revoked after callable preparation

- **WHEN** между подготовкой и вызовом терминального Tool 0.11.1 удалена привязка, изменён владелец или отозван доступ к ресурсу
- **THEN** устаревший callable отклоняется до отправки команды, включая native refresh
- **AND** Stop прекращает локальное ожидание и новые вызовы без повтора уже переданной команды или остановки общего backend

## MODIFIED Requirements

### Requirement: Skill reads enforce the configured scope

Чтение Skills SHALL проверять принадлежность текущему набору перед штатной проверкой прав OWUI. В профиле 0.12.3 это SHALL действовать для `view_skill`, `read_skill_file` и `grep_skill_files`; в профиле 0.11.1 — для штатного `view_skill` текстового Skill. Проверка SHALL сохраняться при реальном исполнении, native refresh и продолжениях. Отсутствующие файловые readers 0.11.1 SHALL NOT эмулироваться или требоваться для подготовки.

#### Scenario: Model requests another readable skill by known id

- **WHEN** A вызывает любую поддержанную операцию чтения для доступного пользователю Skill B, не входящего в набор A
- **THEN** операция возвращает ошибку ограничения модели
- **AND** содержимое B не передаётся A

#### Scenario: Skill file is read after callable refresh

- **WHEN** в OWUI 0.12.3 вызываемая функция обновляется между `view_skill` и чтением файла
- **THEN** проверка допустимого Skill сохраняется
- **AND** разрешённый Skill читается со штатными ограничениями файлов и страниц

#### Scenario: Legacy model reads a configured text skill

- **WHEN** у A в OWUI 0.11.1 прикреплён штатный Skill с текстовым содержимым
- **THEN** подготовка предоставляет ограниченный `view_skill`, который возвращает текст разрешённого Skill
- **AND** отсутствие `read_skill_file` и `grep_skill_files` не вызывает ошибку подготовки и эти readers не публикуются

#### Scenario: Legacy skill refresh cannot bypass model scope

- **WHEN** штатный loop обновляет callable `view_skill` в OWUI 0.11.1, а A запрашивает чужой Skill либо ранее отсоединённый Skill
- **THEN** ограничение модели и актуальная проверка доступа остаются действующими
- **AND** клиентские упоминания и сохранённая история не расширяют каталог

### Requirement: Standalone offline installation on the supported version

Комплект SHALL устанавливаться как один Filter и один Pipe со встроенным `lite_delegate` в каждой поддержанной версии OWUI — 0.11.1 и 0.12.3 — из соответствующих exports. Отдельный Workspace Tool делегирования и его read-grant SHALL NOT требоваться. После установки файлы SHALL работать без импорта модулей репозитория и доступа к интернету, используя заранее установленные зависимости и доступные внутри контура модели и сервисы.

#### Scenario: Installation into an isolated OWUI instance

- **WHEN** два установочных файла загружены в OWUI v0.12.3, а модели и ресурсы заранее настроены внутри контура
- **THEN** самостоятельный ответ и handoff выполняются без внешних загрузок и отдельного сервиса оркестрации
- **AND** отсутствие рабочего каталога этого репозитория на сервере не нарушает импорт Functions

#### Scenario: Legacy installation runs without the checkout

- **WHEN** соответствующие два exports загружены в изолированный OWUI 0.11.1 без исходного пакета Router
- **THEN** настоящий native loop выполняет handoff, локальный Tool, серверные OpenAPI/MCP, чтение Skill и команды выбранного терминала
- **AND** для этого не требуется импорт API, существующих только в 0.12.3, патч OWUI, миграция БД или загрузка зависимостей из интернета

#### Scenario: Building a legacy bundle preserves the current distribution

- **WHEN** собран дополнительный комплект 0.11.1
- **THEN** он находится отдельно от текущих двух exports 0.12.3
- **AND** существующие пути, имена и default сборки 0.12.3 остаются совместимыми
