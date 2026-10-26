# Автономное обучение

Интерфейс: `/experiments/01` панели Explorer. `explorer_sim.pilot` создаёт
реальную сцену MuJoCo с исходными CAD, обучает SB3 PPO, сохраняет teacher rollouts,
обучает официальный LeRobot ACT и независимо выполняет student в симуляторе.
Неуспешные teacher episodes сохраняются, но не становятся экспертными BC-целями.
Состояние actor — RGB и оценка команд; привилегированная цель доступна teacher
и evaluator, не student.

`explorer_sim.world_model` обучает следующий RGB/state и сравнивает прогноз с
action-agnostic и persistence на отдельных development episodes. Это offline
прогноз, а не контроллер движений. См. [контракт](docs/OFFLINE_WORLD_MODEL_RU.md).

Текущий пилот — fixed-base reach. Носок, полный цикл конфеты, реальная
деформируемая ткань, масштабирование workers и контролируемые fault/repair
исследования пока не завершены. Научные и физические успехи не присваиваются
по результату этого пилота. Доказательства находятся в `research/experiments/`
и `_registry/evidence/` корня репозитория.
