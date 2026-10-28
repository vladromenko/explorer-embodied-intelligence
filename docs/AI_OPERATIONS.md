# Runbook главного агента Explorer

## Восстановление и выбор атомика

1. Прочитать root AGENTS → PROJECT_STATE → BACKLOG. Проверить hostname/cwd,
   `git status --short`, `git log -5`, worktrees и текущий release marker. Сверить
   нужные evidence, если state расходится с Git/live. Не повторять весь старый чат
   или master при достаточных canonical docs; при спорном требовании открыть
   соответствующий original master/protocol и учесть более позднее указание.
2. Найти Current task ID, собственный незавершённый diff и активные bounded jobs/
   агента. Не сбрасывать их, не стартовать дубликат и не продолжать старый job
   без проверки source/runtime pin/actual state. Dirty user files сохранить.
3. Выбрать один доступный атомик из BACKLOG. Перед реализацией коротко установить
   reused modules, новый thin adapter, нужные consumer contract tests и done
   evidence. Закрытый physical gate не обходить; продолжать независимое ПО.
4. Перед долгим/рискованным шагом — recoverable commit/state; сохранить invocation,
   source commit, лимиты и raw negatives. Для release нужен frozen source tree,
   appropriate targeted/integration checks и достаточная финальная regression. Старые proof
   bytes не переименовывать в новый PASS; изменившиеся executable pins перепроверять.

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

## Координация

Root владеет порядком, общими файлами/контрактами/миграциями, интеграцией, полной
регрессией и deployment. Максимум1 независимый subagent. Делегировать bounded
reuse audit, независимый тест либо новый thin module с явным ownership; один
writer на общий файл. Read-heavy поиск/аудит/независимые тесты можно параллелить
в пределах ресурсов. Write-heavy задачи — отдельные ветки/worktrees того же Git,
без параллельных правок live checkout или контроллера. После отчёта Root делает
review diff/evidence, merge/cherry-pick, нужную регрессию и checkpoint.

Не отправлять несколько агентов проверять одну гипотезу. Обычную реализацию
выполнять достаточным effort; максимальный — архитектура/root cause/final audit,
если клиент позволяет выбор. Документы не переключают фактическую модель/режим;
не утверждать неподтверждённое переключение. Предпочтение пользователя модели
сохраняется, пока он сам его не изменил.

## Закрытие атомика

- Сверить done criterion выбранного ID, исходники и реальные выходы. Из одного
  API/фикстуры/документа не выводить full skill/software-ready/physical success.
- Запустить риск-соразмерные проверки. Metadata: лёгкие contracts/checker;
  shared: обе ветви потребителей; control/ownership: races/foreign/late/failclosed;
  training: actual bounded updates/reload/new-PID resume только при необходимости.
  Не повторять full04–06/массовые audits без новой причины. Ресурсы ограничить;
  kernel cgroup peak отличается от иногда неверного systemd MemoryPeak summary.
- Обновить PROJECT_STATE, нужные function-level acceptance cells и статус/следующее
  действие BACKLOG. Изменение scope обосновать evidence, не старым обещанием.
- `python scripts/check_project_state.py`, `git diff --check`, commit с коротким
  результатом. Commit в PROJECT_STATE — проверенный code baseline, а не попытка
  записать self-referential hash собственного документа; docs commit виден в Git.
- Перейти к следующему полезному разрешённому ID. Частичный milestone/новый runbook
  не повод завершать общую работу. Если нужен человек, оставить precise physical
  item и продолжать независимые software задачи.

## Общение и недоступный узел

Во время работы краткое обновление по-русски не реже60s: что доказано, scope,
что осталось и следующий проверяемый шаг. После атомика: `commit / проверки /
что deployed / какой ID продолжаю`. Не просить повторную авторизацию обычных
обратимых программных действий; реальные motion grants/HOME/control не выполнять
агентом. Missing SSH key/узел не прерывает доступную локальную работу; сохранить
ошибку и использовать уже доступные read-only API/verified originals.

## Источники и release

Канонические [цели](../GOALS.md), [решения](DECISIONS.md), [physical queue](PHYSICAL_CHECKS.md),
[acceptance](ACCEPTANCE_MATRIX.md). Operational backup/restore/rollback детали —
[deploy runbook](../deploy/OPERATIONS_RU.md). Его команды — инструменты, не разрешение
на reboot/motor IO. Первый Pi4c update отдельно gated; не заменить drain ожиданием
нескольких секунд. В private HANDOFF можно хранить временные session IDs/paths,
но при расхождении актуальный PROJECT_STATE+Git+actual receipt имеют приоритет.
