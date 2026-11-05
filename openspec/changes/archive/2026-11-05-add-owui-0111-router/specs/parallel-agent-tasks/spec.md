# Spec Delta

## ADDED Requirements

### Requirement: Parallel execution is available only in its supported release profile

Возможности параллельного исполнения этой спецификации SHALL предоставляться профилем 0.12.3 с существующими ограничениями. Профиль 0.11.1 SHALL NOT публиковать `lite_parallel` или запускать native дочерние задания через Router. Настройки OWUI, административные Valves, клиентские поля и история SHALL NOT включать параллельную работу в профиле 0.11.1.

#### Scenario: Legacy configuration contains positive parallel limits

- **WHEN** на OWUI 0.11.1 включены штатные сабагенты и сохранены положительные parallel Valves Router
- **THEN** Router предоставляет обычный `lite_delegate` для разрешённого реестра, но не schema или callable `lite_parallel`
- **AND** дочерние задачи и чаты Router не создаются

#### Scenario: A legacy request replays a parallel call

- **WHEN** в запросе, старой истории или аргументах модели 0.11.1 появляется попытка вызвать параллельное исполнение
- **THEN** неподдержанный вызов получает явную ошибку и не выполняет dispatch
- **AND** сохранённый текст не восстанавливает ticket, разрешение на группу или вложенное делегирование

#### Scenario: The current profile retains native parallel execution

- **WHEN** на OWUI 0.12.3 установлен его комплект и параллельная работа разрешена действующими настройками
- **THEN** A+B→C и A+A→C, доступ к собственным ресурсам, полный пакет результатов, файлы, ссылки на дочерние чаты и Stop/deadline сохраняют существующее поведение
- **AND** отдельный комплект 0.11.1 не меняет default лимитов или доступность `lite_parallel` в 0.12.3
