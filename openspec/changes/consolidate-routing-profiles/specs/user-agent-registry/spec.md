# Spec Delta

## MODIFIED Requirements

### Requirement: Explicit agent to routing skill catalog

При настроенном `routing_skill_id` система SHALL читать `agents.json` из активного Workspace Skill. Реестр SHALL связывать точный Workspace Model ID с относительным путём к Markdown-профилю в `profiles/` и кратким описанием. Когда настройка пуста, система SHALL использовать прежний словарь Model ID → Skill ID. Подключённый общий Skill SHALL быть единственным источником маршрутов. Runtime SHALL NOT генерировать профили при обработке запроса.

#### Scenario: Administrator registers an existing agent and skill

- **WHEN** общий Skill не настроен и в словаре указано `agent-a: route-agent-a`, причём оба объекта существуют
- **THEN** система использует модель `agent-a` и инструкции Skill `route-agent-a`
- **AND** имя и описание берутся из самого Skill
- **AND** рабочие Tools и Skills агента не дублируются в словаре

#### Scenario: Accessible but unregistered model

- **WHEN** пользователю доступна модель, отсутствующая в выбранном источнике реестра
- **THEN** модель не включается в каталог адресатов `lite_delegate` или `delegate_task`

#### Scenario: Administrator publishes the shared routing skill

- **WHEN** настройка указывает на общий Skill и его `agents.json` содержит запись `agent-a` с `path` и `description`
- **THEN** описание каталога и профиль берутся из указанной версии Skill
- **AND** Router Preparation использует только этот источник, даже если старый `AGENTS` заполнен

### Requirement: Usable routing skills

Система SHALL публиковать агента только с пригодным маршрутизационным профилем в активном Skill, доступном владельцу публичной Router Workspace Model. Отдельный read-grant пользователя SHALL NOT требоваться. В общем Skill запись и файл SHALL проверяться в одной версии. Ошибка одного профиля SHALL исключать соответствующую запись с диагностикой; недоступный или нечитаемый реестр SHALL исключать все его записи.

#### Scenario: Missing inactive or unreadable routing skill

- **WHEN** модель агента разрешена, но её отдельный routing Skill отсутствует, неактивен или недоступен владельцу публичной Router Workspace Model
- **THEN** агент исключается одновременно из реестра, манифеста и `enum`
- **AND** администратору доступна диагностика непригодной записи

#### Scenario: Router would delegate to itself

- **WHEN** запись каталога указывает на публичную Router-модель или на модель, запускающую тот же Router Pipe
- **THEN** эта запись не публикуется как агент
- **AND** система сообщает об ошибке конфигурации без рекурсивного запуска

#### Scenario: Routing skill needs no separate user grant

- **WHEN** пользователю разрешена модель A, её активный routing Skill доступен владельцу публичной Router-модели, а пользователь не имеет отдельного read-grant на Skill
- **THEN** A остаётся в персональном реестре, routing-манифесте и enum управляющих Tools
- **AND** Router читает разрешённый профиль с правами своего владельца

#### Scenario: One profile file is missing or invalid

- **WHEN** запись A ссылается на отсутствующий, пустой, нечитаемый или выходящий за пределы `profiles/` файл, а запись B корректна
- **THEN** A исключается с диагностикой
- **AND** B остаётся доступным пользователю с нужными правами

#### Scenario: The shared registry cannot be loaded

- **WHEN** общий Skill недоступен или `agents.json` отсутствует, не является корректным JSON-объектом либо содержит повторяющиеся ключи
- **THEN** система показывает администратору причину и строит пустой каталог без инструментов передачи
- **AND** Router сохраняет возможность ответить своими средствами
- **AND** прежний `AGENTS` не подставляется неявно

#### Scenario: The shared skill changes during a request

- **WHEN** администратор публикует новую версию Skill после подготовки каталога
- **THEN** Router читает профиль из версии каталога текущего запроса при действующих правах
- **AND** новый пользовательский запрос использует новую версию
- **AND** при недоступности закреплённой версии чтение завершается ошибкой

### Requirement: Aligned manifest and delegate schema

Система SHALL формировать манифест и `enum` аргумента `agent_id` обоих инструментов передачи из одного персонального реестра. Запись SHALL содержать ID и имя Skill, точный Workspace Model ID, имя Workspace Model и описание маршрута. Общий Skill SHALL также указывать путь к файлу профиля. При отсутствии имени модели SHALL использоваться `agent_id`.

#### Scenario: Skill id differs from agent id

- **WHEN** зарегистрировано `agent-a: route-agent-a` и агент доступен пользователю
- **THEN** манифест показывает `skill_id=route-agent-a` и `agent_id=agent-a`
- **AND** `agent_name` содержит название модели A отдельно от `name` routing Skill
- **AND** `enum` содержит `agent-a`
- **AND** загрузка `route-agent-a` возвращает написанные администратором инструкции

#### Scenario: No agent is currently available

- **WHEN** после проверок персональный реестр пуст
- **THEN** routing Skills не публикуются и инструменты передачи не предлагаются модели
- **AND** схема с пустым `enum` не отправляется провайдеру
- **AND** Router сохраняет возможность отвечать своими средствами

#### Scenario: Two agents share one skill

- **WHEN** в персональном реестре есть A и B с профилями в общем Skill
- **THEN** их записи показывают один ID Skill, отдельные пути и описания
- **AND** оба enum содержат точные Model ID A и B
