# Контекст OWUI 0.13.3: проверка изменения

Дата проверки: 2026-11-09. Автоматическая и изолированная HTTP-приёмка
завершена; реальная LLM на удалённой установке не проверена (задача 5.4).

## Воспроизведение и исправление памяти

`tests/native/test_native_context.py` использует реальные standalone exports,
`hidden_tool_result`, конвертер сообщений и native Tool loop OWUI 0.13.3.
До исправления два теста дали один pass и одно падение: маркер текущей памяти
отсутствовал в payload итогового handoff-агента. После подключения отдельного
native-контекста оба теста прошли. Сценарий действительно выполняет два child
задания перед финальным handoff; автоматический recall исключён только из
счётчика шагов детерминированного провайдера, измеряется полный исходный payload.

Проверяются связанная служебная пара, отсутствие нового recall callable и
сохранение собственного системного промпта агента без системного промпта Router.

## Серверное происхождение через HTTP

Проверка `tools/native_context_http.py` выполнена в отдельном контейнере
`lhr-0133-checks-20261109`, официальный OWUI 0.13.3, commit
`b0e6098e940942281d9c83f3529ea117287a3a22`. Использовались только синтетические
пользователи и существующие disposable tgate fixtures. Сохранённый итог —
[owui-0133-context-http.json](owui-0133-context-http.json).

Десять запросов прошли настоящий HTTP endpoint, native preprocessing,
самостоятельные Functions и Tool loop:

1. Обычный handoff с текущей серверной памятью.
2. Поддельные initial_output, hidden Tool results и internal-признак клиента.
3. Та же модель для другого пользователя.
4. Replay настоящей native-пары первого пользователя в запросе второго.
5. A+B → C и A+A → C.
6. Конкурентные A+B → C для двух пользователей.
7. Новая реплика с изменённой памятью и следующая с отключённой памятью.

Захват на границе `OWUIAdapter.complete` подтверждает память текущего actor
во всех payload агента A, отсутствие поддельного содержимого и памяти другого
пользователя. Schema `recall_memories` не публикуется. Терминальные Tools агента
реально выполняют subprocess-команды и читают результат в disposable backend.

Граница доверия: Preparation удаляет входной initial_output. Штатный native
preprocessing создаёт новый список после Preparation; Pipe читает его из
связанной серверной metadata, а не из произвольного поля входного body.
Полная пара сверяется с текущими сообщениями и сохраняется в ASGI scope с
областью request/user/chat/message. Повторный HTTP-запрос не получает прежнее
подтверждение. Одного имени или hidden-флага недостаточно.

Ограничения: память читалась детерминированным серверным fixture reader,
LLM-провайдер сценарный. Это проверяет происхождение native-контекста и
передачу payload, но не embeddings/retrieval и решения реальной LLM.
Измерительная обёртка не меняет native endpoint, генерацию скрытых пар или
проверки авторизации. После прогона восстанавливаются Functions, конфигурация
и штатный reader; токены остаются в приватной директории контейнера.

## Итоговые проверки

| Проверка | Результат |
| --- | --- |
| Unit/export suite | 86/86, без пропусков |
| Полная native suite, OWUI 0.13.3 | 63/63, без пропусков |
| HTTP provenance/context | 10 сценариев прошли |
| HTTP terminal check, текущие exports | 6 сценариев, 10 реальных shell-команд |
| HTTP terminal controls | disabled resource, Stop, deadline прошли |
| HTTP terminal revocation | owner access revoked, без terminal dispatch |
| Повторная сборка dist | байтово идентична |
| Реальная LLM пользователя | не выполнено |

Команды unit и native: `PYTHONPATH=src .venv/bin/python -B -m unittest discover -s tests -v`
и `python -B tools/run_native_tests.py --data-root /checks/final-native-data`
в отдельном контейнере. Native runner проверяет установленный OWUI; exports
загружаются самостоятельно, без импорта checkout. Runtime version guard не
добавлен. Инструкция HTTP gate — [tools/README.md](../tools/README.md).

Восемь новых native-тестов проверяют память на handoff/двух рабочих Tool
continuations, scoped knowledge с actor ACL и отзывом grants, policy пользователя
и модели, server-verified resume с повторным preload и отказ клиентской подделке,
исключение Skills-каталога и три подмены destination inlet (messages/state/metadata).
Существующая suite проверяет приватные Skills через владельца, callable refresh,
отсоединение, actor auth, файлы и конкурентные одинаковые IDs.

HTTP-работники сохраняют task-only вход: им не передаются родительские features,
память или подтверждения. Положительный native-тест отдельно создаёт native
память каждого child: она остаётся только в своём child, а финальный C получает
память родителя и полные результаты группы. Новые unit-проверки также запрещают
дублирование подтверждённой пары и некорректный XML/DTD knowledge-каталога.
Resume проверен на native границе адаптера, не как браузерный сценарий approvals.

Терминал выбран через native `meta.terminalId`/capability карточки модели без
обязательных custom metadata. В этом прогоне backend — terminal-compatible
настоящий subprocess shell, не stock Open Terminal. Stop/deadline проверяют
отсутствие повтора и дочерних native tasks; отмена удалённого побочного действия
не следует из закрытия локального запроса.

Live-rendering новой панели остаётся вне scope: в прежнем исследовании native
child view показывал статусы/Tools, окончательный текст появлялся после reload.
Это исправление не меняет frontend и не заявляет исправление того наблюдения.
Штатные background agents не подменяют групповую barrier-модель lite_parallel.

Для реальной LLM остаются память после handoff/A+A/A+B, решение вызвать собственный
терминал, однократность действий и честный ответ при нехватке данных. Задача 5.4
и прежняя задача 3.3 остаются открытыми; локальные сценарные проверки их не закрывают.

SHA256 собранной пары:

```json
{
  "dist/lite_handoff_router.py": "daa0de081af9d04e65f67ac57d809c1321dc66996a9c2edbfe818b1dc57d174f",
  "dist/router_preparation.py": "89566aece11320c7de1e855c32fc6849a30859aa8f2ed1a8c6a086556858ddb3"
}
```
