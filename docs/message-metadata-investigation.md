# Служебная история в message.meta — OWUI 0.12.3

Дата: 2026-10-22. Базовая реализация Router — коммит `ed020ce`.
Это проверка механизма хранения, не реализация нового контракта `lite_delegate`.
Исходники Router, exports и OpenSpec этим исследованием не изменены.

Последующее сравнение с референсом показало альтернативу без постоянного audit
в `message.meta`: последовательность завершённых delegate exchanges позволяет
восстанавливать исполнителя из самой Tool-истории при соблюдении ограничений
на смешанные вызовы. См. [reference-delegate-investigation.md](reference-delegate-investigation.md).
Приведённый ниже механизм хранения технически пригоден, но не является
обязательным условием короткого результата Tool.

## Вывод

В обычном сохранённом чате можно возвращать из Tool только ID агента,
а сведения для разбора истории хранить отдельно в `message.meta` существующей БД OWUI.
Отдельная БД, сервис или изменение схемы OWUI для этого не нужны.
Понадобятся явное чтение метаданных текущей ветки и запись с сохранением чужих ключей.

Данные `meta` не показаны в обычном интерфейсе чата и раскрытом результате Tool.
Они присутствуют в ответе chat API: это отделение служебных данных от отображения,
а не секретное от владельца чата хранилище.

## Проверка HTTP и UI

На существующем Docker OWUI **0.12.3** созданы отдельные временные Pipe, Filter
и скрытая модель. Установленные LHR Functions не менялись. Пробный Pipe выдавал
scripted SSE; модель LLM и реальный handoff не вызывались. Native middleware,
исполнение callable, HTTP endpoint, fan-out и сохранение сообщений не подменялись.

Callable записал namespace `lite_handoff_probe` в `meta` своего assistant-сообщения
через `Chats.upsert_message_to_chat_by_id_and_message_id()` и вернул `agent-a`.
После Tool-продолжения и завершения ответа проверены:

| Проверка | Результат |
| --- | --- |
| Tool действительно исполнен | `completed`, результат ровно `agent-a` |
| В результате Tools нет служебного sentinel и audit | Подтверждено по сохранённому `output` |
| Метаданные пережили финальную запись middleware | Namespace остался в БД и chat API |
| Следующее сообщение — новый HTTP request | Сервер прочитал тот же record из выбранной ветки по родительским связям |
| Подготовленные сообщения Pipe | Sentinel отсутствует на первом входе, продолжении и следующем ходе |
| Safari, раскрытие Tool result | Ввод: `agent_id agent-a`; вывод: `agent-a`; служебные поля не показаны |
| Safari после перезагрузки страницы | Тот же компактный результат и оба сообщения доступны |

Чат: `873f4d01-b59f-4f2a-ba55-cda87c95b5c7`.
Результаты без credentials: [JSON](../.cache/message-meta-probe-results.json).
Временные Functions и модель удалены; тестовый чат сохранён как свидетельство.
Временный файл административного токена удалён.

## Контракт хранения и особые записи

- `ChatMessage.meta` — штатная JSON-колонка. Нормализованная таблица сохраняет поле,
  когда оно присутствует в обновлении. [chat_messages.py](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/models/chat_messages.py#L133-L239).
- `Chats.upsert_message_to_chat_by_id_and_message_id()` обновляет embedded history
  и нормализованное сообщение. При передаче `meta` произвольные старые ключи
  автоматически не объединяются: специальное объединение предусмотрено для
  `voice`/`task_id`. Нужно сначала сохранить остальные ключи.
  [Правило merge](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/models/chats.py#L1073-L1095),
  [запись сообщения](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/models/chats.py#L1326-L1362).
- DB replay первоначально читает `meta`, но `process_messages_with_output()` убирает
  его до вызова Filter/Pipe. Поэтому скрытые сведения не попадут в контекст LLM
  автоматически, и их нужно читать отдельно из нужной ветки чата.
  [Replay](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L1876-L1902),
  [преобразование сообщений](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L1930-L2000).
- Обычная финальная запись streaming loop обновляет `done`/`output`/`usage`,
  сохраняя существующий `meta`.
  [Финальная запись](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L6281-L6304).

Дополнительно на том же временном сообщении выполнены настоящие native helpers:

1. Voice-only обновление сохранило custom namespace.
2. `pause_for_tool_approval()` без явного включения namespace **удалил** его:
   функция записывает собственный `meta` целиком.
3. Включение namespace в переданный helper `metadata['tool_approval']` сохранило record.
   Это проверка записи helper, не доказательство полного approval-resume Router.

[Источник approval-записи](https://github.com/open-webui/open-webui/blob/v0.12.3/backend/open_webui/utils/middleware.py#L3311-L3353).
После проверки исходные content/output/meta/done временного сообщения восстановлены.

## Что это означает для упрощения Router

Достаточно добавить в OWUI adapter чтение provenance из выбранной ветки и запись
namespace текущего ответа, затем научить разбор истории использовать эти данные
вместо большого результата delegate. Текущая авторизация и pending-передача
остаются в доверенном request scope. Контракт Tool может возвращать только Model ID.

Для обычного saved-chat пути это локальное изменение, но не замена одного `return`:
нужно сохранить разделение истории, обработать ошибку записи и не принять
клиентские данные за подтверждение текущей передачи. Поддержка старых маркеров,
регенерация/ветвление, временные/API-чаты без записи в БД и полный approval-resume
переработанного Router этой пробой не проверены. Режим подтверждений требует
отдельного сохранения namespace; нельзя обещать, что произвольный `meta`
переживает любую штатную запись без дополнительной интеграции.
