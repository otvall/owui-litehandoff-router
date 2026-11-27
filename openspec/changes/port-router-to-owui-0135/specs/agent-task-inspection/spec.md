# Spec Delta

## ADDED Requirements

### Requirement: Open WebUI Threads expose router executions

На OWUI 0.13.5 владелец родительского чата SHALL видеть запущенные задачи Router в штатном **Status → Threads**, открывать соответствующий чат во вкладке OWUI и останавливать работающую задачу через штатное действие Stop.

#### Scenario: Owner opens a running worker from Threads

- **WHEN** в основном чате на OWUI 0.13.5 запущены A и B
- **THEN** в Threads доступны две отдельные записи, каждая открывает свой дочерний чат во вкладке OWUI
- **AND** повторное открытие той же записи фокусирует её вкладку, а просмотр не создаёт нового исполнения
- **AND** завершение задачи отражается в Threads, а подробный исход остаётся доступен в результате группы

#### Scenario: Owner stops a running worker from Threads

- **WHEN** владелец останавливает B из Threads до завершения группы
- **THEN** незавершённое выполнение B прекращается, а группа сообщает о его отмене
- **AND** Router не запускает C на основании неполной группы
