# Spec Delta

## MODIFIED Requirements

### Requirement: Usable routing skills

Система SHALL публиковать агента только с существующим активным routing Skill, доступным владельцу публичной Router Workspace Model. Отдельный read-grant пользователя на Skill SHALL NOT требоваться. Непригодная запись SHALL исключаться с диагностикой причины; непригодность одного routing Skill SHALL NOT изменять разрешения остальных записей.

#### Scenario: Missing inactive or unreadable routing skill

- **WHEN** модель агента разрешена, но её routing Skill отсутствует, неактивен или недоступен владельцу публичной Router Workspace Model
- **THEN** агент исключается одновременно из персонального реестра, манифеста и `enum`
- **AND** администратору доступна диагностика непригодной записи

#### Scenario: Router would delegate to itself

- **WHEN** запись каталога указывает на публичную Router-модель или на модель, запускающую тот же Router Pipe
- **THEN** эта запись не публикуется как агент
- **AND** система сообщает об ошибке конфигурации без рекурсивного запуска

#### Scenario: Routing skill needs no separate user grant

- **WHEN** пользователю разрешена модель A, её активный routing Skill доступен владельцу публичной Router-модели, а пользователь не имеет отдельного read-grant на Skill
- **THEN** A остаётся в персональном реестре, routing-манифесте и enum управляющих Tools
- **AND** Router читает этот routing Skill с правами своего владельца
