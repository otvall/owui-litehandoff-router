# Spec Delta

## MODIFIED Requirements

### Requirement: Shared dialogue follows the selected agent

При обычном handoff агент SHALL получать текущий запрос и общую видимую переписку; при включённом context-budgeting допустимый старый префикс SHALL передаваться обозначенным summary со свежим хвостом. Текущий вопрос и относящиеся к нему вложения SHALL сохраняться без пересказа, чужие системные инструкции SHALL исключаться. Параллельный работник SHALL получать самодостаточное поручение и выбранные вложения без автоматического наследования родительской переписки.

#### Scenario: User refers to another agent answer

- **WHEN** ранее B ответил пользователю, а новое уточнение Router передал A
- **THEN** A получает текст уточнения и общую переписку, включая ответ B дословно либо существенные сведения из него в обозначенном summary
- **AND** A не получает системный промпт или рабочие Skills B

#### Scenario: User supplied attachment survives handoff

- **WHEN** исходный запрос содержит поддерживаемый OWUI файл или изображение
- **THEN** контекст выбранного агента сохраняет ссылку или штатное представление этого вложения
- **AND** передача не заменяет исходный запрос только пересказом Router

#### Scenario: Parallel worker receives an isolated assignment

- **WHEN** Router запускает параллельного работника
- **THEN** работник получает необходимые факты в самодостаточном поручении и выбранные доступные вложения
- **AND** исходная переписка и её checkpoints не добавляются к его входу автоматически
- **AND** исходные сообщения сохраняются в основном чате для Router и последующего обычного handoff

### Requirement: Router can inspect completed work across the conversation

Router SHALL получать историю завершённой работы в хронологии исходных запросов. Результаты агента SHALL быть данными отдельного `tool`-сообщения исторического `lite_delegate` рядом с его вопросом; собственные вызовы Router SHALL оставаться реальными exchanges. Рабочие записи SHALL сохранять инструмент, аргументы, статус и результат либо плашку N. Общая справка в новом `user` или внутри последнего вопроса SHALL NOT добавляться. История SHALL NOT предоставлять чужие Tools.

#### Scenario: Relevant data was produced before the previous turn

- **WHEN** пользователь ссылается на результат Tool из более раннего запроса текущего чата
- **THEN** Router получает полный результат внутри соответствующей исторической передачи, если он входит в окно, либо плашку или допустимый summary старого блока на этапе суммаризации
- **AND** обзор не ограничивается автоматически только последним запросом
- **AND** оригинал сохраняется в исходной истории без обещания автоматического чтения роутером

#### Scenario: Historical tool is not a router capability

- **WHEN** исторический результат `lite_delegate` содержит данные специализированного Tool A
- **THEN** Router видит допустимое представление данных и автора выполнения
- **AND** этот Tool не появляется в текущем списке вызовов Router из-за своего присутствия в истории

### Requirement: Agent tool history is scoped by executor and current capabilities

Агент SHALL получать только свои однозначно связанные exchanges допустимых актуальных Tools; окно N и summary SHALL применяться после проверки области. Старые результаты SHALL заменяться плашками либо входить в сокращаемый блок по context-budgeting. Совпадение имени Tool SHALL NOT разрешать чужую историю. Трасса Router, handoff и чужие Skill-загрузки SHALL исключаться, в том числе из summary агента.

#### Scenario: Two agents use a tool with the same name

- **WHEN** A и B ранее вызывали разные инструменты с одинаковым именем `lookup`
- **THEN** A получает только принадлежащие A допустимые exchanges или их ограниченное представление
- **AND** результаты B не попадают в нативную Tool-историю или рабочее summary A

#### Scenario: Agent is selected again

- **WHEN** после нового сообщения Router снова выбирает A
- **THEN** A получает своё допустимое представление прошлых результатов и общей видимой переписки
- **AND** наличие других исполнителей между запросами A не меняет авторство этих результатов

#### Scenario: Historical tool was detached

- **WHEN** Tool A присутствует в истории, но больше не входит в актуальные возможности A
- **THEN** его нативный exchange исключается из подготовленного контекста A и несовместимый checkpoint не применяется
- **AND** исходный результат сохраняется в чате и допустимом справочном представлении Router

### Requirement: Preserve the current tool continuation

Полные валидные вызовы и результаты текущего исполнителя SHALL сохраняться в серверной истории. Между продолжениями модель SHALL получать структурно корректное представление с окном N и допустимым summary по context-budgeting. Служебные user-сообщения с изображением результата SHALL NOT считаться новым запросом. Сокращение представления SHALL NOT менять исполнителя или подтверждать handoff вместо серверного exchange.

#### Scenario: Agent performs multiple sequential tool calls

- **WHEN** A последовательно выполняет два своих Tools
- **THEN** последующие вызовы модели видят корректную текущую цепочку с полными свежими результатами, плашками или допустимым summary старых блоков согласно настройкам
- **AND** модель остаётся A до завершения пользовательского запроса

#### Scenario: Tool produces an image represented as a user message

- **WHEN** OWUI добавляет изображение результата Tool в служебное user-сообщение
- **THEN** система сохраняет прежнюю связь с Tool-цепочкой; этот текстовый/HTML этап не вводит новый контракт раздельного скрытия агрегированных изображений
- **AND** Router не запускается заново только из-за роли `user` этого сообщения

#### Scenario: A handoff marker is absent from the compacted view

- **WHEN** модельное представление содержит плашку либо summary вместо старого handoff exchange
- **THEN** сервер определяет допустимый переход по полному источнику и своему runtime-состоянию
- **AND** текст summary и похожие клиентские данные не подтверждают переход

### Requirement: Context limits do not silently change conversation meaning

Система SHALL применять скрытие результатов и пересказ только по явной административной политике context-budgeting с обозначением в контексте модели. При невозможности вместить защищённые данные SHALL возвращаться понятная ошибка без повторного исполнения Tools. Самодостаточное поручение SHALL формироваться до запуска работника. Сырые чужие результаты SHALL NOT превращаться в собственную Tool-историю адресата, в том числе через summary.

#### Scenario: Context exceeds the provider limit

- **WHEN** после допустимого скрытия и суммаризации вход не помещается в бюджет либо провайдер отклоняет его по длине
- **THEN** пользователь получает ошибку ограничения контекста
- **AND** система не скрывает дополнительное усечение за успешным ответом и не повторяет выполненные действия

#### Scenario: Another agent needs a previously produced artifact

- **WHEN** A требуется результат B, представленный в общем ответе или доступном файле
- **THEN** A использует это общее представление с сохранением штатных прав
- **AND** отсутствующий в нём сырой Tool result B не подставляется автоматически как результат Tool A

#### Scenario: Parallel assignment exceeds the provider limit

- **WHEN** самодостаточное поручение с выбранными входными данными не помещается в контекст работника
- **THEN** система сообщает об ограничении контекста
- **AND** не сокращает исходное поручение или обязательные входы и не повторяет действия для обхода ошибки

#### Scenario: Context policy is disabled

- **WHEN** окно результатов и собственная суммаризация отключены
- **THEN** система не заменяет историю плашками или summary и не применяет прежние checkpoints
- **AND** штатная суммаризация OWUI не включается автоматически
- **AND** итоговый формат исторических handoff, единого system и XML-каталога сохраняется независимо от N

## ADDED Requirements

### Requirement: Historical handoff results preserve separate tool messages

Исторический handoff SHALL представляться существующим вызовом `lite_delegate` в `assistant.tool_calls` и отдельным `tool`-сообщением с тем же `tool_call_id`. Его строковое содержимое SHALL включать `agent_id`, фактический `status` и `tool_results` агента. Система SHALL NOT выдумывать вызов или объявлять принятие передачи завершением работы. Полный исходный exchange SHALL сохраняться для серверных проверок.

#### Scenario: Two different agents handled previous questions

- **WHEN** первые два запроса обработали A и B, а третий снова поступает Router
- **THEN** каждая историческая пара передачи находится после своего вопроса и перед соответствующим видимым ответом
- **AND** `tool_results` A не попадает в пакет B, а третий вопрос передаётся без добавленной общей справки

#### Scenario: Agent tools are nested data rather than Router calls

- **WHEN** A выполнил два рабочих Tools после передачи
- **THEN** обе записи находятся в массиве `tool_results` исторического результата `lite_delegate` с именами, аргументами, статусами и содержимым либо плашками
- **AND** отдельные вызовы этих Tools не приписываются Router и не добавляются в его executable schemas

#### Scenario: Agent execution failed after a successful transfer

- **WHEN** передача принята, но агент не завершил работу успешно
- **THEN** историческое представление не объявляет её `completed` на основании одного маркера приёма
- **AND** сохраняет доступные фактические результаты и подтверждённый неуспешный либо неизвестный исход

#### Scenario: Handoff result is needed for live server verification

- **WHEN** текущий `lite_delegate` ещё подтверждает передачу или сервер определяет авторство прошлых exchanges
- **THEN** используются полный оригинальный маркер и audit, а не преобразованное модельное содержимое
- **AND** повторные call IDs разных запросов не смешиваются

#### Scenario: There was no delegation

- **WHEN** Router сам выполнил Tools либо авторство завершённого exchange неизвестно
- **THEN** допустимые реальные пары сохраняются на своих местах без выдуманного `lite_delegate`
- **AND** неизвестные exchanges не присваиваются одному из агентов по догадке

### Requirement: Router emits one composed system message

Исходящий API-массив `messages` Router SHALL содержать одно `system`-сообщение, объединяющее собственный промпт, служебные правила и допустимые системные добавления. XML-каталоги Skills SHALL передаваться отдельным служебным Tool result, без копии в system. Исторические Tool results SHALL NOT переноситься в него. Отдельное API-поле `tools` SHALL сохраняться; модельный chat template вправе добавлять свои внутренние секции при сериализации.

#### Scenario: Prompt and skill catalog were assembled separately

- **WHEN** подготовлены промпт Router, каталог Skills и дополнительные разрешённые системные инструкции
- **THEN** до отправки промпт и правила объединяются в один system, а XML-каталог остаётся отдельным tool-сообщением
- **AND** runtime-шаблон GLM формирует собственные Tools/Reasoning sections без ручного дублирования в промпте

#### Scenario: Agent is selected after Router

- **WHEN** Router передал запрос A
- **THEN** A сохраняет собственный системный контекст и scoped-каталог без каталога маршрутизации Router
- **AND** эта норма не требует выдачи `lite_delegate` агенту или изменения его модели

### Requirement: Routing skill catalog uses XML without changing scope

Каталог Router SHALL использовать `routing_skills` с записями `<skill>` без атрибутов и дочерними `id`, `agent_id`, `agent_name`, `name`, `description` в этом порядке. XML `id` SHALL отображать внутренний `skill_id` и передаваться в `view_skill(id=...)`; `lite_delegate` SHALL получать отдельный `agent_id`. Значения SHALL экранироваться; каталог SHALL NOT расширять права или заменять API-схемы Tools.

#### Scenario: Two independent routing skills are available

- **WHEN** реестр содержит A и B с их routing Skills
- **THEN** XML содержит две записи с точными ID и описаниями; инструкции требуют чтения Skill перед передачей
- **AND** `agent_name` используется как имя агента, `name` остаётся именем Skill

#### Scenario: Catalog contains special characters or is empty

- **WHEN** название содержит XML-символы либо пользователю не разрешён ни один агент
- **THEN** XML остаётся корректным и не создаёт ложных записей
- **AND** Router может сообщить об отсутствии агентов без вызова делегирования

#### Scenario: Baseline does not include folder routing

- **WHEN** используется база main без поддержки `skill_path`
- **THEN** XML использует существующий реестр Model ID → Skill ID и штатное чтение Skill
- **AND** change не добавляет папочный routing index или неподдерживаемое поле лишь ради примеров из обсуждения

### Requirement: Ordinary skill catalog follows native OWUI XML

Router SHALL выводить после `routing_skills` отдельный `<available_skills>` с записями `<skill>` и дочерними `id`, `name`, `description`. Оба блока SHALL присутствовать даже пустыми. Обычные Skills SHALL объединять конфигурацию, карточку Router и явный выбор в чате без дублей. Routing Skills SHALL NOT повторяться в обычном каталоге. Агент SHALL получать только свой `available_skills` без Router-каталога.

#### Scenario: User explicitly selects an ordinary chat skill

- **WHEN** пользователь выбирает Skill через `skill_ids` запроса либо native mention текущего user-сообщения
- **THEN** Router фиксирует выбор на текущий запрос и включает доступный активный Skill в `available_skills`
- **AND** Skill только из чата читается с правами пользователя, включая повторную проверку при native callable refresh; owner-доступ не подменяет отказ
- **AND** прошлые mentions и произвольный metadata-каталог не добавляют Skills; обычный выбор не разрешает делегирование

#### Scenario: A selected skill is also configured or used for routing

- **WHEN** один ID поступил из нескольких источников
- **THEN** обычный каталог содержит не более одной записи и сохраняет owner-права явно настроенного ресурса
- **AND** при наличии ID в routing-реестре он представлен только routing-записями с точными agent IDs

#### Scenario: Chat choice is not an agent grant

- **WHEN** Router передаёт запрос агенту либо запускает worker
- **THEN** адресат сохраняет только собственный набор Skills и не наследует chat-only Skills Router
- **AND** новый запрос заново определяет явный выбор; недоступный или неактивный выбранный Skill не читается с правами владельца Router

### Requirement: Current skill catalog is protected server tool context

Каждая подготовка SHALL передавать одну актуальную пару `assistant.tool_calls(list_available_skills)` → `tool` со строковым XML. Router SHALL получать `routing_skills` и `available_skills`, агент/worker — только собственный `available_skills`. Пара SHALL NOT расходовать N, предоставлять callable/schema или учитываться как действие модели либо чтение routing Skill. Исходный чат SHALL сохраняться.

#### Scenario: Old or wide native catalog exists

- **WHEN** исходная история содержит прежние либо native каталоги Skills
- **THEN** они исключаются из модельного представления и заменяются одной свежей scoped-парой
- **AND** текущий каталог не дублируется в system и не изменяет user-вопрос

#### Scenario: All working results are hidden

- **WHEN** N=0 и выполняется продолжение либо handoff
- **THEN** собственный актуальный каталог адресата остаётся полным, включая пустые блоки
- **AND** Tools получают ту же пару в __messages__, которую видит модель

#### Scenario: Inlet changes service context

- **WHEN** destination inlet удаляет, подменяет или дублирует служебную пару
- **THEN** подготовка отклоняется независимо от N
- **AND** неверный каталог не публикуется в executable registry
