# Фактическая приёмка

Дата: **2026-10-22**. Изменение `add-skill-based-handoff-router`:
сценарии **7.1–7.3 выполнены** с детерминированным тестовым провайдером.
Качество маршрутизации настоящей LLM этой приёмкой не оценивалось.

## Окружения

- Helpers: macOS, Python **3.12.10**, установленный **open-webui==0.12.3**,
  отдельная SQLite БД `.native-data/helpers`.
- Сквозной стенд: официальный образ `ghcr.io/open-webui/open-webui:v0.12.3`,
  контейнер `owui-router-test`, отдельный volume `owui-router-test-data`,
  интерфейс `http://127.0.0.1:8765`, Safari.
- Внутри контейнера: HTTP-провайдер `127.0.0.1:8766/v1` и настоящий MCP
  Streamable HTTP endpoint `127.0.0.1:8766/mcp/`. Установленный MCP SDK OWUI
  выполняет connect/list/call/disconnect без замены transport.
- В контейнер скопированы только три файла `dist/` и тестовые скрипты.
  `importlib.util.find_spec("lite_handoff_router") is None`: исходный пакет
  проекта для исполнения exports не установлен.

В официальном образе нет package metadata `open-webui`: он запускает исходники.
Проверка версии исправлена для этого варианта и использует штатную
`open_webui.env.VERSION`. Версии, отличные от 0.12.3, по-прежнему отклоняются.

## Автоматические проверки

| Проверка | Результат |
| --- | --- |
| Обычные тесты | **25/25**: протокол, подготовка, enum, изоляция, атомарность, история, guarded readers и сборка |
| Native helpers/loop | **13/13** с настоящими импортами OWUI и отдельной БД; дополнительно проверен Docker-вариант определения версии |
| Самостоятельность exports | Идентичная повторная сборка, компиляция и загрузка в изолированном Python без checkout в путях импорта |
| HTTP + native loop | Две группы/два обычных пользователя одновременно выполняют свои routing/working Skills, локальный Tool и MCP |
| Личность исполнения | Локальный Tool и настоящий MCP получают ID текущего пользователя; A и B не используют идентичность владельца ресурсов |
| Персональный реестр | Пользователь группы A не получает B в delegate enum; запрос профиля B не запускает недоступного B |
| Новый запрос | A → B → самостоятельный Router в одном сохранённом чате, с новым `message_id` на каждом ходе |
| Изменение прав | Доступ к A отозван после публикации реестра, перед выполнением delegate. Передача завершается ошибкой, рабочие Tool/MCP A не запускаются. Grant восстановлен |
| Ошибки | Провайдер, переполнение и обязательный недоступный Tool сохраняют явную ошибку в чате |
| Ошибка после действия | `profile_lookup` выполнен один раз; после ошибки провайдера нет повторного действия или fallback к Router |
| Установка проекта | `pip install --no-index --no-build-isolation .` работает с заранее установленными зависимостями |
| OpenSpec | Строгая валидация изменения проходит |

Команды локальных проверок:

```sh
.venv/bin/python tools/build_functions.py
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/run_native_tests.py
.venv/bin/python -m pip install --no-index --no-build-isolation .
openspec validate add-skill-based-handoff-router --strict
```

В helper-тесте `tests/native/test_chain.py` заменены только транспорт генерации
и браузерная доставка событий. Сквозной Docker-прогон использует настоящие HTTP,
MCP, маршруты приложения, native Tool loop и сохранение в БД.

## Браузер

В Safari выполнены запросы `profile a browser acceptance`,
`profile b browser acceptance`, затем `general question browser acceptance`.
Один чат содержит ответы `specialist-a`, `specialist-b`, `General answer`.
После повторного открытия сохранённая переписка и Tool-история остаются доступны.

Проверено:

- В селекторе доступен только видимый **LHR Router**; агенты и базовые модели скрыты.
- В меню интеграций Delegate, локальные Tools и MCP имеют переключатели **off**.
  Автоматические подключения не записаны в выбранные ресурсы чата.
- Статусы **«Передача: LHR Agent A/B»** сохранены с соответствующим ответом.
- История включает routing Skill → delegate → working Skill → локальный Tool
  → MCP; операции завершены в одном сообщении. Финальный ответ Router
  не пересказывает ответ агента.
- Ошибки провайдера, переполнения и обязательного ресурса видны в интерфейсе.
  Ошибка после Tool также отображается, без повторного исполнения действия.
- Сохранённый чат пользователя A с отзывом доступа открыт в браузере:
  видна ошибка delegate, без ответа и рабочих Tools агента.

Основной браузерный чат:
`6a7dab69-25a8-4b53-8140-c0a7930b53f6`.
Чат offline-прогона A → B → Router:
`b38a55e8-c970-4173-b49f-870940526938`.
Это локальные тестовые данные; доступ определяется правами OWUI.

## Offline и восстановление

Контейнер временно отключён от `bridge` через `docker network disconnect`.
Проверены пустой `NetworkSettings.Networks={}`, отсутствие IPv4-маршрутов
в `/proc/net/route` и ошибка внешнего connect **101 / Network unreachable**.
В таком состоянии повторно импортированы три самостоятельных exports и выполнен
полный HTTP-прогон: две группы параллельно, A → B → Router, MCP, персональный
реестр, ошибки и отзыв прав перед handoff. Все сценарии прошли без внешней сети.
После проверки исходное подключение `bridge` восстановлено.

Сохранены и обратно импортированы три exports вместе с административными Valves.
Содержимое файлов и Valves после восстановления совпадает с сохранённым;
затем снова прошёл запрос Router → A → локальный Tool → MCP → ответ.
Чаты для восстановления не удалялись.

Обычная процедура установки/отката описана в [setup.md](setup.md).
Образ OWUI и его зависимости были подготовлены заранее; их скачивание
внутри закрытого контура не требуется и не проверялось.

## Воспроизведение стенда

Скрипты и команды — в [tools/README.md](../tools/README.md):

```sh
docker exec owui-router-test python /tmp/lhr-acceptance/acceptance_setup.py setup
docker exec owui-router-test python /tmp/lhr-acceptance/acceptance_checks.py
docker exec owui-router-test python /tmp/lhr-acceptance/acceptance_setup.py snapshot
docker exec owui-router-test python /tmp/lhr-acceptance/acceptance_setup.py restore
```

Setup создаёт ресурсы с именами LHR, два тестовых аккаунта/группы, скрытых
`agent-a-lhr`/`agent-b-lhr`, их Skills и два MCP-подключения.
Router attachments пусты; `base_tool_ids=["lhr_delegate"]`,
`base_skill_ids=[]` задаются в административных Valves Preparation.
Словарь AGENTS в импортируемом Preparation связывает точные Model/Skill IDs.

Тестовые credentials, исходная конфигурация и backup хранятся только
в приватных файлах внутри `/tmp/lhr-acceptance`, не в checkout или отчёте.
Файлы результатов без токенов скопированы в игнорируемые
`.cache/live-results.json` и `.cache/offline-results.json`.

Оставшийся этап перед эксплуатацией — отдельный прогон с настоящими
внутренними LLM и ресурсами организации: качество следования routing Skills,
выбор агентов и подходящие параметры конкретных моделей.
