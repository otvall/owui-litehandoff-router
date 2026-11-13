# Spec Delta

## MODIFIED Requirements

### Requirement: Standalone offline installation on the supported version

Комплект SHALL устанавливаться как один Filter и один Pipe со встроенным `lite_delegate` на проверенном релизе OWUI. Отдельный Workspace Tool делегирования и его read-grant SHALL NOT требоваться. После установки эти файлы SHALL работать без импорта модулей данного репозитория и без доступа к интернету, используя заранее установленные зависимости и доступные внутри контура модели и сервисы.

#### Scenario: Installation into an isolated OWUI instance

- **WHEN** два установочных файла загружены на проверенном релизе OWUI, а модели и ресурсы заранее настроены внутри контура
- **THEN** самостоятельный ответ и handoff выполняются без внешних загрузок и отдельного сервиса оркестрации
- **AND** отсутствие рабочего каталога этого репозитория на сервере не нарушает импорт Functions

## REMOVED Requirements

### Requirement: Single supported release

**Reason**: По уточнению пользователя запуск больше не ограничивается точной версией OWUI.
**Migration**: Удалить runtime guard и обязательную metadata версии, оставив информационную отметку версии разработки.

## ADDED Requirements

### Requirement: OWUI release metadata is informational

Установочные Functions SHALL указывать версию разработки OWUI только в информационной metadata `developed_for_open_webui_version`. Runtime SHALL NOT проверять номер установленной версии либо отклонять запуск из-за его несовпадения. Metadata SHALL NOT задавать обязательную минимальную версию OWUI.

#### Scenario: Adapter starts without version metadata

- **WHEN** адаптер создаётся без доступной distribution metadata OWUI
- **THEN** создание не требует чтения версии и не вызывает отказ из-за версии

#### Scenario: Both exports describe their development release

- **WHEN** собраны оба установочных файла
- **THEN** их заголовки содержат `developed_for_open_webui_version` с фактической версией разработки комплекта
- **AND** `required_open_webui_version` и runtime version guard отсутствуют
