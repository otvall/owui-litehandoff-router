# Routing Skills в одном Skill-контейнере

Ветка `feat/folder-routing-skills` поддерживает многофайловый формат Skills OWUI:
один Workspace Skill хранит индекс и все профили роутинга как дополнительные файлы.
Отдельные Workspace Skills на каждого агента не требуются.

1. Установите обе пересобранные Functions из `dist/`.
2. Создайте активный Workspace Skill, например `routing-catalog`.
3. Добавьте в него `SKILL.md`, `routing.json` и файлы профилей.
4. Дайте владельцу модели Router доступ к чтению этого Skill.
5. В Valves Router Preparation укажите его фактический ID в `routing_skill_id`.

Пример структуры:

```text
SKILL.md
routing.json
profiles/forecast.md
profiles/research.md
```

`SKILL.md` объясняет назначение каталога и чтение профилей через
`read_skill_file`. Файлы `profiles/*.md` содержат обычные routing Skills:
когда выбирать агента, когда не выбирать и ожидаемый результат.
Их YAML не используется для определения адресата.

`routing.json`:

```json
{
  "forecast-agent": {
    "path": "profiles/forecast.md",
    "name": "route-forecast",
    "description": "Прогнозирование по временным рядам."
  },
  "research-agent": {
    "path": "profiles/research.md",
    "name": "route-research",
    "description": "Исследование источников и подготовка обзоров."
  }
}
```

Ключи — точные Workspace Model IDs. Пути — существующие относительные пути
файлов внутри Skill. Индекс читается сервером на новом запросе; полные профили
Router читает по необходимости через штатный `read_skill_file(id=..., path=...)`.
В манифесте каждый агент получает своё описание и `skill_path` при общем `skill_id`.
Для передачи используются прежние `lite_delegate` и `delegate_task` с Model ID.
Проверки прав пользователя на модели и владельца Router на Skill сохраняются.
Рабочие Skills агентов по-прежнему задаются в карточках моделей.

При непустом `routing_skill_id` источником каталога служит только этот Skill;
`AGENTS` не объединяется с ним. Пустое значение возвращает прежний режим `AGENTS`.
Неправильный индекс или отсутствующий профиль прерывает подготовку запроса.
Недоступные модели исключаются из персонального каталога как раньше.
Изменения файлов сохраняйте штатным редактором OWUI с контролем версии.
