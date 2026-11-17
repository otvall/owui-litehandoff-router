# Проверка окна результатов и истории Router

Дата: 2026-11-17. Проверяется текущий этап `add-context-overflow-prevention`:
окно N, исторические handoff-пакеты, единое API-сообщение system и XML Skills.
Бюджетирование, checkpoints и суммаризация этим отчётом не подтверждаются.

## Локальные проверки

```sh
python tools/build_functions.py
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -q
openspec validate add-context-overflow-prevention --strict
```

131 unit-тест: N=null/0/1/2, происхождение и порядок batch/call, повторные IDs,
неизменность source, сохранение user-вложений, отдельные окна исполнителей,
удаление дублирующего HTML после inlets, binding с теми же `__messages__`,
XML escaping/scope и порядок объединения system.

## Native OWUI

Официальный образ `ghcr.io/open-webui/open-webui:v0.13.3-slim`, версия 0.13.3,
commit `b0e6098e940942281d9c83f3529ea117287a3a22`. Одноразовый контейнер,
`--network none`, отдельный `/tmp` data-root, без рабочих томов и данных.
Checkout и оба экспорта копируются внутрь; запускается:

```sh
PYTHONPATH=/app/backend python tools/run_native_tests.py --suite full --data-root /tmp/lhr-context-native
```

Native suite загружает самодостаточные экспорты через OWUI loader, использует
настоящие output converter, saved chat, native streaming Tool loop и callable
refresh. Тестовый provider детерминирован; пользовательская установка не меняется.
В `test_chain.py` проверяются три запроса, два агента, отдельные исторические
Tool-сообщения, общий N=1 и единый system/XML. `test_handoff_history.py`
проверяет ошибку после handoff по сохранённой ветке, отсутствие доступа другого
actor и неизменность БД. В `test_parallel_context.py` окно N=0 проверяет
task-only workers, callable refresh, полный пакет финальному агенту и серверный
запрет handoff после отклонённой группы.

Результат: **77 native-тестов прошли, пропусков нет**. В этом же контейнере
отдельно прошли **131 unit-тест**, включая импорт экспортов из временного
каталога в isolated Python без `PYTHONPATH` и checkout в import path.

## Сериализация GLM

Загружен [официальный chat_template.jinja GLM-5.3-Flash](https://huggingface.co/zai-org/GLM-5.3-Flash/blob/main/chat_template.jinja).
SHA-256: `2614a3c896d53e265f564c23701173c0049f985dacfbae79fc4bcd9f5ba083f4`.
Запрос из `prepare_model` с тремя вопросами, двумя передачами и N=1 отрендерен
Jinja 3.1.6 с extension `loopcontrols` и JSON filter, принимающим `ensure_ascii`.
Строковые аргументы tool calls распарсены в словари на входе шаблона согласно
его контракту; API payload проекта сохраняет обычные JSON-строки аргументов.

Подтверждены: одно API system, два `<|observation|>`, два `<tool_response>`,
один routing-каталог, исходный порядок трёх вопросов, неизменный последний
user, отсутствие скрытого HTML и служебного receipt. Схемы переданы через
`tools`, native control tags создаёт сам шаблон. Дополнительные внутренние
system-секции шаблона reasoning/tools ожидаемы.

Это проверка сериализации, не смысловая приёмка самой модели. Запрос к живой
GLM-5.3-Flash, отдельная HTTP-приёмка через серверный endpoint и поздние механизмы
summary/checkpoints не выполнялись; соответствующие задачи остаются открытыми.

## Уточнение двух каталогов Skills

После уточнения XML проходит **134 unit-теста и 78 native-тестов** на том же
официальном образе OWUI 0.13.3 в отдельном контейнере без сети и рабочих томов.
`routing_skills` расположен первым, `available_skills` вторым; все поля —
дочерние элементы без атрибутов. XML `id` соответствует внутреннему `skill_id`.

Проверены порядок/экранирование/пустые блоки, общий routing Skill нескольких
агентов, отсутствие routing-дублей в ordinary-каталоге, body selection и native
mentions текущего user, игнорирование прежних mentions и metadata-каталога,
фиксация выбора на запрос и очистка при следующем запросе. Native streaming
тест обоих экспортов проверяет chat-selected Skill в Router и его отсутствие
после handoff. Реальные readers `view_skill/read_skill_file/grep_skill_files`
проходят callable refresh с подставленным owner, продолжают использовать actor
для chat-only Skill и отклоняют чтение после отзыва его доступа. Настроенные
ресурсы сохраняют owner authority; отдельные agent/worker scope не расширяются.
