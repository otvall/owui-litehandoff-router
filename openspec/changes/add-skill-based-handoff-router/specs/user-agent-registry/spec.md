# Spec Delta

## Purpose

Формировать для каждого пользователя согласованный каталог агентов и routing Skills на основании конфигурации администратора и штатных прав Open WebUI.

## ADDED Requirements

### Requirement: Explicit agent to routing skill catalog

Система SHALL принимать конфигурационный словарь Workspace Model ID агента → ID существующего routing Skill OWUI. Система SHALL использовать явно заданные соответствия без генерации routing Skills из описаний моделей.

#### Scenario: Administrator registers an existing agent and skill

- **WHEN** в словаре указано `agent-a: route-agent-a` и оба объекта существуют
- **THEN** система использует модель `agent-a` и инструкции Skill `route-agent-a`
- **AND** имя и описание routing Skill берутся из самого Skill
- **AND** рабочие Tools и Skills агента не дублируются в словаре

#### Scenario: Accessible but unregistered model

- **WHEN** пользователь имеет доступ к модели, отсутствующей в конфигурационном словаре
- **THEN** эта модель не включается в каталог адресатов `lite_delegate`

### Requirement: User scoped agent discovery

На каждом новом сообщении система SHALL формировать реестр только из настроенных активных агентов, разрешённых пользователю штатными проверками OWUI. Членство в нескольких группах, наследование групп и индивидуальные разрешения SHALL учитываться по правилам установленного OWUI.

#### Scenario: Different users have different permitted agents

- **WHEN** каталог содержит `agent-a` и `agent-b`, пользователю U1 разрешён только A, а U2 — только B
- **THEN** реестр U1 содержит только A, а реестр U2 — только B
- **AND** недоступные агенты не описываются в манифесте соответствующего пользователя

#### Scenario: Membership or model availability changes

- **WHEN** перед следующим сообщением пользователя изменены его разрешения или агент деактивирован
- **THEN** следующий реестр отражает актуальные права и активность
- **AND** успешная передача в предыдущем запросе не предоставляет новый доступ

### Requirement: Usable routing skills

Система SHALL публиковать агента только с существующим активным routing Skill, доступным пользователю для чтения. Непригодная запись SHALL исключаться с диагностикой причины; непригодность одного routing Skill SHALL NOT изменять разрешения остальных записей.

#### Scenario: Missing inactive or unreadable routing skill

- **WHEN** модель агента разрешена, но её routing Skill отсутствует, неактивен или недоступен пользователю
- **THEN** агент исключается одновременно из персонального реестра, манифеста и `enum`
- **AND** администратору доступна диагностика непригодной записи

#### Scenario: Router would delegate to itself

- **WHEN** запись каталога указывает на публичную Router-модель или на модель, запускающую тот же Router Pipe
- **THEN** эта запись не публикуется как агент
- **AND** система сообщает об ошибке конфигурации без рекурсивного запуска

### Requirement: Aligned manifest and delegate schema

Система SHALL формировать манифест routing Skills и `enum` обязательного строкового аргумента `lite_delegate.agent_id` из одного персонального реестра. Каждая запись манифеста SHALL содержать отдельные `skill_id`, `agent_id`, имя и описание Skill. `agent_id` SHALL быть точным Workspace Model ID.

#### Scenario: Skill id differs from agent id

- **WHEN** зарегистрировано `agent-a: route-agent-a` и агент доступен пользователю
- **THEN** манифест показывает `skill_id=route-agent-a` и `agent_id=agent-a`
- **AND** `enum` содержит `agent-a`, не подменяя его идентификатором Skill
- **AND** загрузка `route-agent-a` возвращает написанные администратором инструкции

#### Scenario: No agent is currently available

- **WHEN** после проверок персональный реестр пуст
- **THEN** routing Skills не публикуются и `lite_delegate` не предлагается модели
- **AND** схема с пустым `enum` не отправляется провайдеру
- **AND** Router сохраняет возможность отвечать своими средствами

### Requirement: Registry isolation between concurrent requests

Персональные реестр, манифест и схема передачи SHALL принадлежать конкретному запросу пользователя. Параллельные запросы SHALL NOT менять друг другу перечень агентов или общую установленную схему Tool.

#### Scenario: Concurrent users with disjoint access

- **WHEN** запросы U1 и U2 выполняются одновременно и разрешённые им агенты различаются
- **THEN** каждый вызов модели получает только собственные манифест и `enum`
- **AND** последующее выполнение запроса U1 не использует реестр U2

### Requirement: Hidden agents retain routing eligibility

Комплект SHALL поддерживать штатное скрытие агентов через `meta.hidden` при сохранении их активности и групповых разрешений. Скрытость SHALL NOT исключать разрешённого агента из реестра Router. Отдельный запрет прямого запуска модели через API не является требованием этой возможности.

#### Scenario: Normal model selector exposes the router

- **WHEN** администратор скрывает модели агентов и оставляет Router видимым
- **THEN** стандартный выбор модели в чате показывает Router и не предлагает скрытых агентов
- **AND** Router может передавать запрос скрытому агенту при наличии у пользователя разрешения

#### Scenario: Hiding does not grant permissions

- **WHEN** агент скрыт, но пользователю не разрешена его модель
- **THEN** агент не включается в персональный реестр
- **AND** установка `hidden` не заменяет проверку доступа
