# Фактический Router prompt из OWUI в Docker

Снято 2026-11-17 в официальном `ghcr.io/open-webui/open-webui:v0.13.3-slim`.
Создан отдельный контейнер без сети и рабочих томов, с одноразовой SQLite БД.
Пользовательские контейнеры не запускались и не менялись.

В БД через native Models/Skills/Functions API созданы Router, три агента,
их routing Skills, собственные рабочие Skills агентов и три обычных Skill.
Каждая Workspace Model доступна тестовому actor через штатные grants.
Доступ к настроенным Skills использует владельца Router; chat-only Skill
принадлежит actor. Оба актуальных standalone экспорта загружены через
`load_function_module_by_id`, зарегистрированы и активированы.

Сначала выполнен Preparation.inlet, затем реальный
`open_webui.utils.chat.generate_chat_completion`, который проходит через Pipe
и снова через native completion dispatch. Перехвачен аргумент `form_data`
на границе `generate_openai_chat_completion`; подменён только provider transport
и доставка socket events. Живая GLM не вызывалась. HTTP endpoint и UI не
использовались: это прямой native backend-вызов внутри Docker.

Основной системный промпт взят целиком из
`docs/templates/router-system-prompt.md`. Для этого примера parallel выключен
через `max_parallel_tasks=0`; вызовов рабочих Tools и прошлой истории нет.

Файлы:

- `system.txt` — точное содержимое единственного API-сообщения system, без сокращений.
- `request.json` — полный перехваченный запрос модели, без внутренних metadata/callables.
- `setup.json` — конфигурация примера, источники обычных Skills и имена Tools.

Обычные Skills: `report-style` из административных Valves, `fact-check` из
карточки Router, `concise-answer` из `body.skill_ids` текущего запроса.
Повторный выбор `report-style` через чат не дублирует запись и не меняет её
owner authority. Рабочие Skills агентов отсутствуют в Router-каталоге.

Проверено: одно system, 3 routing и 3 ordinary записи, порядок каталогов,
точные agent IDs в enum `lite_delegate`, отсутствие полных инструкций Skills,
неизменный последний вопрос пользователя и четыре callable schemas отдельно
в `tools`. Имя модели `glm-5.3-flash` здесь идентифицирует перехваченный маршрут,
не подтверждает доступ к реальной модели или качество её ответа.
