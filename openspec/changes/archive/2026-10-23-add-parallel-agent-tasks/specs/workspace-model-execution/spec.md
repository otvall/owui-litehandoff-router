# Spec Delta

## MODIFIED Requirements

### Requirement: Native user permissions remain effective

Система SHALL сохранять пользователя запроса при проверках доступа к моделям, чатам и обычным файлам и при исполнении Tools. Настроенные Tools, Skills и MCP SHALL проверяться с правами владельца Workspace Model без отдельных read-grants пользователя. Доступ через модель SHALL NOT выдавать отдельные grants на ресурсы. Недоступность владельцу обязательного ресурса SHALL вызывать понятную ошибку подготовки вместо частично подготовленного запуска.

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
- **THEN** A запускается с полным настроенным набором Tools, Skills и MCP
- **AND** Tools сохраняют пользователя чата, его UserValves и пользовательскую авторизацию без выдачи новых grants
