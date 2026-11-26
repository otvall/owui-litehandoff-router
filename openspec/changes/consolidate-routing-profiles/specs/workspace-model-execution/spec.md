# Spec Delta

## MODIFIED Requirements

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

- **WHEN** `base_skill_ids` пуст, но пользователю доступен агент с пригодным маршрутизационным профилем
- **THEN** Router может прочитать профиль из персонального реестра
- **AND** отсутствие базовых Skills не отключает маршрутизационные профили

### Requirement: Skill reads enforce the configured scope

Чтение через `view_skill`, `read_skill_file` и `grep_skill_files` SHALL проверять принадлежность Skill текущему набору перед штатной проверкой прав OWUI. Для общего Skill проверка SHALL дополнительно ограничивать все три операции общим `SKILL.md` и путями из персонального каталога Router. Эти условия SHALL действовать при реальном исполнении, включая обновление callable и продолжения.

#### Scenario: Model requests another readable skill by known id

- **WHEN** A вызывает любую операцию чтения для доступного пользователю Skill B, не входящего в набор A
- **THEN** операция возвращает ошибку ограничения модели
- **AND** содержимое B не передаётся A

#### Scenario: Skill file is read after callable refresh

- **WHEN** OWUI обновляет вызываемую функцию между `view_skill` и чтением файла
- **THEN** проверки допустимого Skill и пути сохраняются
- **AND** разрешённый файл читается со штатными ограничениями файлов и страниц

#### Scenario: Router tries to access a profile outside its catalog

- **WHEN** модели известен путь чужого профиля или к `agents.json`, либо она задаёт широкий шаблон поиска
- **THEN** прямое чтение этих путей отклоняется
- **AND** список файлов, поиск и их метаданные не раскрывают эти файлы
- **AND** проверка отклоняет пути с обходом каталога и проверяет доступ перед каждой страницей

#### Scenario: Another input refers to the shared skill

- **WHEN** общий Skill появляется в пользовательском выборе, упоминании, обычных attachments Router или агента или базовом списке
- **THEN** он не становится обычным рабочим Skill и не получает более широкий путь чтения с правами владельца
- **AND** агент не получает инструкции общего Skill через маршрутизационный контекст
