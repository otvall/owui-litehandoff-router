# Spec Delta

## MODIFIED Requirements

### Requirement: Standalone offline installation on the supported version

Комплект SHALL устанавливаться как один Filter и один Pipe со встроенным `lite_delegate` на OWUI 0.13.3 и 0.13.5. Отдельный Workspace Tool делегирования и его read-grant SHALL NOT требоваться. После установки эти файлы SHALL работать без импорта модулей данного репозитория и без доступа к интернету, используя заранее установленные зависимости и доступные внутри контура модели и сервисы.

#### Scenario: Installation into an isolated OWUI instance

- **WHEN** два установочных файла загружены на OWUI 0.13.3 и 0.13.5, а модели и ресурсы заранее настроены внутри контура
- **THEN** самостоятельный ответ и handoff выполняются без внешних загрузок и отдельного сервиса оркестрации
- **AND** отсутствие рабочего каталога этого репозитория на сервере не нарушает импорт Functions
