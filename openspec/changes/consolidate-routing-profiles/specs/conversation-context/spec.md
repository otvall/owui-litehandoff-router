# Spec Delta

## MODIFIED Requirements

### Requirement: Routing skill catalog uses XML without changing scope

Каталог Router SHALL использовать `routing_skills` с записями `<skill>` без атрибутов и дочерними `id`, `agent_id`, `agent_name`, `name`, `description` в этом порядке. Для общего Skill SHALL добавляться `path` после `description`. XML `id` SHALL отображать внутренний `skill_id`, а `agent_id` SHALL передаваться инструментам делегирования. Значения SHALL экранироваться. Каталог SHALL NOT расширять права или заменять API-схемы Tools.

#### Scenario: Two independent routing skills are available

- **WHEN** реестр содержит A и B с отдельными routing Skills
- **THEN** XML содержит две записи с точными ID и описаниями
- **AND** `agent_name` используется как имя агента, `name` остаётся именем Skill
- **AND** записи не содержат `path`, а инструкции требуют `view_skill` перед передачей

#### Scenario: Catalog contains special characters or is empty

- **WHEN** название содержит XML-символы либо пользователю не разрешён ни один агент
- **THEN** XML остаётся корректным и не создаёт ложных записей
- **AND** Router может сообщить об отсутствии агентов без вызова делегирования

#### Scenario: Baseline does not include folder routing

- **WHEN** администратор очищает `routing_skill_id` и возвращается к сохранённому `AGENTS`
- **THEN** XML снова использует соответствия Model ID → Skill ID и штатное чтение через `view_skill`
- **AND** записи каталога не содержат `path`

#### Scenario: Routing profiles are stored in one skill

- **WHEN** общий Skill содержит разрешённые профили A и B и профиль недоступного C
- **THEN** каталог содержит только записи A и B с общим ID и отдельными `path`
- **AND** инструкции используют `id` и `path` для чтения точного профиля

### Requirement: Historical skill instructions do not become active instructions

Справка Router SHALL отмечать загрузку Skill и его ID без полных исторических инструкций. Из завершённых запросов полные результаты Skill readers SHALL исключаться из нативной истории моделей. Текущие загрузки собственных Skills SHALL сохраняться в допустимой Tool-цепочке с проверкой актуального набора. История и сводка SHALL NOT возвращать маршрутизационные профили агентам.

#### Scenario: Another agent loaded a working skill

- **WHEN** в завершённом запросе B загрузил Skill через `view_skill`, чтение файла или поиск по файлам
- **THEN** Router получает сведения о загрузке без полного текста инструкций B
- **AND** A не получает служебную загрузку Skill B в собственной Tool-истории

#### Scenario: A current skill read is needed for execution

- **WHEN** A загрузил разрешённый собственный Skill в текущем запросе
- **THEN** результат остаётся доступен A на следующем продолжении
- **AND** при следующем пользовательском сообщении актуальные инструкции подготавливаются заново

#### Scenario: The same agent returns after a skill update

- **WHEN** A выбран в новом пользовательском запросе после изменения ранее прочитанного Skill
- **THEN** полная старая загрузка Skill не восстанавливается из завершённого запроса как нативный Tool result
- **AND** A получает актуальный каталог и при необходимости читает действующие инструкции заново

#### Scenario: Router read a shared profile

- **WHEN** Router загрузил файл общего маршрутизационного Skill
- **THEN** полный результат присутствует только в допустимой текущей Tool-цепочке Router
- **AND** сводка не включает содержимое этого чтения, а контекст агента не получает ни профиль, ни служебный путь к нему
