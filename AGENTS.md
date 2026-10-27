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
   Запускай тесты по риску; один full frozen regression перед выпуском, без
   повторных широких аудитов без изменившихся исходников/evidence.
9. После milestone автоматически продолжай полезную разрешённую работу backlog.
   Физический gate одного опыта не запрещает независимую программную работу.
   Вопросы пользователю не подменяют доступную реализацию; не обходи закрытый gate.
10. Ведущий агент отвечает за порядок/state/integration. Максимум1 независимый
    субагент: ограниченный аудит/тест/изолированная задача; не несколько агентов
    на одну гипотезу. Read-heavy можно параллелить, write-heavy — только в
    отдельных ветках/worktrees. Никто кроме integrator не обновляет production.

Узкий repo skill: [explorer-project-operator](.agents/skills/explorer-project-operator/SKILL.md).
Автоматическое обнаружение текущим клиентом не проверено; правила выше обязательны.
