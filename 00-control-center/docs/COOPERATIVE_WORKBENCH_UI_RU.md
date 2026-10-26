# Самостоятельная веб-панель кооперативного workflow05

`static/cooperative-workbench.js` не зависит от app.js/station.js и не регистрирует
глобальные обработчики. Экспорты доступны как browser globals и CommonJS для
Node-тестов:

```javascript
const client = createCooperativeWorkbenchClient({ get: api, post, onChange: render });
root.innerHTML = cooperativeWorkbenchPanel(client.snapshot());
const unbind = bindCooperativeWorkbench(root, client, onError);
await client.refresh(); // GET, допустим при открытии страницы.
```

Вызвать `unbind()` перед заменой root / уходом со страницы. Сам constructor,
renderer и bind не делают запросов. Только refresh делает GET. POST отправляется
только явным нажатием / submit пользователя. Нет authorize, lease, motor,
телопортации, автоматического запуска модели или входа в физический режим.
Root владеет монтажом script/route, authenticated/CSRF HTTP wrappers и deploy.

## Договорённость с CooperativeService

GET `/api/cooperative`, а после явного выбора сессии
`/api/cooperative?session_id=<server-issued>`:
`{workbench:{skills:[{spec,confirmed}],sessions:[...]}, catalogue:{phases},
native_choices:{giver,receiver}, pending_evidence, cameras:{giver,receiver},
native_episode_choices, model_adapters_bound}`. Каждая session имеет свой
`legal_events` и `readiness`. При выборе сессии bind делает только дополнительный
GET; последующие refresh сохраняют query, чтобы получить её настоящие originals.

Выборы native навыков содержат `choice_id`, `reference` с точными версиями/SHA,
`phase_ids`, `native_variants`, `models`, `confirmed`. UI не конструирует действие
по имени страницы и не подменяет отсутствующий provider06 на02. Для снимков
используются только server-provided same-origin `/api/...` URLs. Это реальные
источники, не доказательство свежести экспозиции или общего clock двух камер.

POST `/api/cooperative/{operation}` возвращает `{result,snapshot}`:

- propose_skill: текстовые name/object_description/receiver_goal_description,
  outcome_criteria массив строк из формы, mode, giver/receiver_choice_id;
  optional giver/receiver_model_id и явные phase_map из native dropdowns.
  Пустые mapping entries не отправляются. Whole-skill scope решает server choice,
  соответствия не угадываются. Подтверждение — отдельная confirm_skill с точным
  skill_id/version/spec_sha256, не продолжение propose автоматически.
- create_session: выбранный skill_id/version/spec_sha256, человеческие имена
  scene_id/object_instance_id, исходный split (включая sealed_test), фактические
  input modes, domain, native_variants и explicit stationary_chassis_confirmed.
  По умолчанию observation_only. Physical domain отсутствует в выборе, если
  отдельная текущая observed authority не возвращена сервером.
- Все session mutations привязаны к server-issued session_id и expected_epoch.
  record_stage/record_finish дополнительно включают **текущий recording_id**.
  Это исключает поздний finish старой записи в той же сессии.
- register_zone отправляет только явные boolean наблюдения. Сервер получает и
  сохраняет оригинальные снимки; пользователь не вводит матрицы/пути/JSON.
  Ручная проверка зоны не заменяет метрическую регистрацию для политики.
- record_start/stage/finish — явные metadata-операции. Finish по умолчанию unknown;
  успех требует ручного независимого наблюдения. Для native ROSMASTER отдельно
  запрашиваются исходные фазы, видимость предмета/цели и полный native-итог,
  передаваемые в `outcome.native_roles.giver.review` существующему источнику.
  Ничего не выводится из FSM, таймера, закрытия захвата или текста VLM.
- step: event только из server legal_events, с epoch. STOP проходит, даже пока
  другой запрос ожидает ответа. После STOP старая сессия не возобновляется.
- confirm_evidence: конкретный challenge_id, два отдельных explicit boolean,
  optional note. Actor приходит только от авторизованного сервера. Срок не
  сравнивается с browser clock. Неподтверждение — явный STOP, не выдуманный hold.
- review_recording: last_recording_id, полный исход и отдельные phase_outcomes,
  original object identity и `native_episode_choice_ids:{giver,receiver}` из
  серверных списков. Ручного ввода ID нет. Unknown допустим и не становится BC.
  Ссылки на исходные native показы предоставляет сервер; текущая миниатюра не
  является просмотром всего записанного эпизода. SO101 full phase intervals
  по-прежнему проверяются в существующем source04 workflow.
- aggregate, training_plan, shadow — отдельные кнопки. План не объявляется уже
  поставленной задачей обучения. Shadow disabled без двух закреплённых моделей
  и реальных server model/observation adapters; browser RGB/state не отправляет.

## Состояние и гонки

Редактирование не вызывает POST. Текст/выборы и STOP-latch сохраняются в optional
sessionStorage (`options.storage`, `storageKey` могут быть переданы root).
Периодический GET не стирает черновик. Current-observation confirmations не
переживают перезагрузку страницы; новый recording/source original сбрасывает
предыдущие success labels. Смена source choice очищает только её model/phase map.

Mutation generation и GET sequence отбрасывают поздние ответы. STOP немедленно
ставит локальную блокировку без ложного утверждения, что сервер уже остановлен.
Сетевой сбой сохраняет неопределённость и запрет возобновления; серверный STOPPED
не оживает после нового экземпляра клиента. SHA drift неизменяемой версии требует
повторного выбора, не тихой перепривязки. Epoch, recording ID, origin choices и
все фактические допуски повторно проверяет сервер — клиент не выдаёт разрешений.

Node-тесты — программные HTTP/DOM fixtures. Они не выполняют движения, не
подтверждают физическое мастерство и не заменяют конечную проверку root в Firefox
после монтажа настоящего API.

### Реальная постановка двух native training jobs

После появления service.train добавлена отдельная explicit кнопка enqueue.
Нужны успешный агрегат с SHA и ready native training plan **этой же точной версии**.
Предложение/подтверждение/GET/проверка плана не ставят jobs автоматически.
`POST /api/cooperative/train {skill_id,version,settings:{}}` вызывает существующих
двух native owners; сервер заново проверяет все допуски. Результат `jobs/errors`
показывается отдельно для ROSMASTER9D и SO1016D, включая частичную постановку.
Он не называется двумя готовыми моделями, слитым dataset/head или обученным
координатором. Повтор/потерянный ответ дедуплицирует долговечный service receipt,
не оптимистическая логика browser.

Новый finish/review сбрасывает browser preflight; смена immutable spec не может
перенести ready из другого навыка. Source review и аппаратная квалификация остаются
отдельными фактами. Ни enqueue, ни model-ready не разрешают физическое исполнение.
