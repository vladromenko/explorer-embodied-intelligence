# Явное ограничение stationary base в05

Фактическая finite CPU проверка двух моделей сохранила full9 предсказания с
ненулевыми base3 `[0.3275205791,-0.2608493567,0.3130290806]`, хотя исходные train
и development target base были нулевыми. Прежний native owner правильно
отклоняет такой nonzero base; warm inference не является разрешением шасси.

Новый pure `cooperative_action_constraint05.stationary_constraint(spec,session,
model)` связывает точный исходный cooperative Spec/SHA (`initial_base_mode`),
явное `stationary_chassis_confirmed=true`, run/object/epoch и выбранную native9
model/checkpoint. Без этого source-bound описания автоматической проекции нет.
Модель и данные ROSMASTER остаются full9, без arm-only режима и без unavailable
масок вместо настоящих значений.

Только в отдельном USER begin исходный native owner может получить metadata
`POST explorer/cooperative/constraint/register` через существующий bridge. Тело
содержит текущие native run/object/coordinator/auth IDs и полный immutable
`constraint`; оригинальные bytes не переписываются. Регистрация допустима только
в точном observed STOP до begin, не посылает никаких arm/base команд. Constructor
ничего не регистрирует. Старый runtime без capability/registration остаётся
неподходящим для новой projected policy; развёртывание требует отдельного
root zero-startup qualification и idle cutover.

`project_stationary_action(raw9,constraint)` сохраняет исходный raw9/digest,
возвращает requested9 с теми же arm6 и явно спроецированными requested base3
zero. Это запрос координатора, не измеренная неподвижность и не изменение
controller limit. Native input сверяет registered constraint, exact model,
authorization, raw9, requested9 и projection receipt до original executor.
При зарегистрированном ограничении пропустить raw/projection поля нельзя.
Реальная original base публикация запрашивается тем же прежним native методом;
нулевой base никогда не выводится из prohibition/unknown state.

В native host-publication receipt сохраняются raw policy9, его SHA,
requested9/SHA, all9 valid mask, projection rule и исходная native registration.
Transport подтверждает именно эти поля в настоящем correlated dispatch ring;
API ACK не считается dispatch, достижение/контакт не утверждаются. Исходный
prediction artifact warm job сохраняется отдельно и не редактируется.

Тесты `test_cooperative_action_constraint05.py` используют сохранённые численные
residuals, настоящий native HTTP ABI и fake original publisher. Проверены
metadata-only registration, отсутствие неявной проекции, exact source/model/
run/auth fences, неизменённые Ruckig lookahead/lease, raw и issued9 в actual mock
publication, сохранение девяти настоящих нулей с девятью true masks. На реальном
оборудовании API не вызывался; физическое исполнение не квалифицировано.
