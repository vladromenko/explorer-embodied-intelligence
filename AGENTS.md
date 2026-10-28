# Explorer: обязательные правила работы

1. Это продолжение проекта. Сначала прочитай [PROJECT_STATE](docs/PROJECT_STATE.md),
   [BACKLOG](docs/BACKLOG.md), затем `git status`, `git log -5` и нужные evidence.
   При обрыве восстановись из Git+state; не начинай проект заново. Полный master
   перечитывай только для отсутствующего/спорного требования.
2. Актуальные прямые указания пользователя имеют приоритет. Исходные цели —
   [GOALS](GOALS.md); порядок и незакрытые ID — только BACKLOG. Частичный этап,
   инфраструктура, loss или зелёные mocks не завершают исходную задачу.
3. Во время текущей удалённой разработки агент **не двигает шасси или руки** и
   не выдаёт реальные native grants/HOME/begin/resume/policy/action/STOP.
   Реальные движения — отдельный текущий USER-observed тест. Read-only GET,
   свои data-only записи и изолированные fake/SIM проверки допустимы.
4. Сохраняй исходные6 ROSMASTER configs, firmware, limits, watchdogs и feedback OFF.
   Новая servo feedback-схема требует отдельного подтверждения. Frozen
   `shared/explorer_shared/so101_station.py` не меняется; исходный SHA
   `2d1915342cc96a9dd82b5364e24d083757c7beeaaeca7b0f77052c168f45a885`.
   Command estimate не называется measured_state. Не добавляй Python `continue`.
5. Переиспользуй shared core и существующих владельцев serial/camera; отличия —
   тонкие experiment adapters. Новая общая функция получает contract tests обоих
   consumers. Не копируй целиком API/recorder/converter/trainer/UI.
6. Разделяй implemented, mock/SIM/software-tested, deployed и physical acceptance.
   Не повышай физическую готовность без фактического evidence; сохраняй failures,
   unknown, setup failures, исходные SHA/IDs/splits и raw scientific originals.
7. Не затирай пользовательские изменения: перед правкой проверь diff/worktrees;
   не делай reset/clean/force-push. Один writer на общий файл, один integrator для
   общих контрактов/БД/deploy. Правила агентов и merge — [AI_OPERATIONS](docs/AI_OPERATIONS.md).
8. Перед долгим/рискованным действием и после атомарного milestone сохраняй
   checkpoint. Обновляй PROJECT_STATE/ACCEPTANCE_MATRIX/BACKLOG по evidence.
   Запускай тесты по риску; одна достаточная финальная regression на milestone, без
   повторных широких аудитов без изменившихся исходников/evidence.
9. После milestone автоматически продолжай полезную разрешённую работу backlog.
   Физический gate одного опыта не запрещает независимую программную работу.
   Вопросы пользователю не подменяют доступную реализацию; не обходи закрытый gate.
10. Ведущий агент отвечает за порядок/state/integration. Максимум1 независимый
    субагент: ограниченный аудит/тест/изолированная задача; не несколько агентов
    на одну гипотезу. Read-heavy можно параллелить, write-heavy — только в
    отдельных ветках/worktrees. Никто кроме integrator не обновляет production.

## MVP-FIRST / PHYSICAL-FEEDBACK-FIRST — обязательная политика

Действует для01–06 и всех будущих задач; последнее прямое указание пользователя
имеет приоритет над прежним требованием широких проверок перед каждым выпуском.

1. Сначала самый короткий сквозной путь: панель → нужные устройства → камеры и
   состояние → управление/запись → завершение и сохранение → проверяемый результат.
2. После целевых software tests и бездвигательного smoke немедленно развернуть
   базовый путь: `MVP_USER_TESTABLE` / `DEPLOYED_AWAITING_PHYSICAL_ACCEPTANCE`.
   Физическая проверка остаётся USER-observed; запреты движения агентом сохраняются.
3. До первой физической проверки новые recovery/race/SHA/provenance сценарии,
   абстракции, оптимизации, VLM, сложный hardening и многотысячные регрессии
   отнести в `DEFERRED_HARDENING`, если они не блокируют базовый путь.
4. Исключения до MVP: фактический blocker запуска; риск необратимой потери данных;
   изменение firmware/controller settings; два владельца одного servo bus;
   невозможность восстановить рабочий deployment. Исключения не разрешают новые
   motion limiters/feedback-схемы, меняющие плавность.
5. После USER physical test сначала исправлять обнаруженные дефекты. Гипотетические
   edge cases — после user-reported defects и базовых путей остальных приоритетных
   опытов. Закрытый physical gate не мешает следующей разрешённой software задаче.
6. Для каждой задачи вести `MVP BLOCKERS` отдельно от `DEFERRED HARDENING`;
   `NON_BLOCKING_HARDENING` — синоним второго списка. Не брать deferred, пока есть
   MVP blocker текущего опыта или следующий приоритетный опыт ещё не user-testable.
7. Во время реализации — целевые unit/integration tests; перед deploy один smoke;
   на milestone одна достаточная финальная regression. Полный suite не повторять
   после небольших правок; размер проверки определяется базовым изменённым путём.
8. Статус — что пользователь может нажать/увидеть/записать и что мешает физическому
   тесту. Счётчики тестов — только evidence, не главный показатель готовности.

Узкий repo skill: [explorer-project-operator](.agents/skills/explorer-project-operator/SKILL.md).
Автоматическое обнаружение текущим клиентом не проверено; правила выше обязательны.
