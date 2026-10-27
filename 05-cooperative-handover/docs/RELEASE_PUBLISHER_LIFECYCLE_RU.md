# Root release до фактического original Publisher receipt

`tests/test_cooperative_release_lifecycle.py` использует один существующий FSM,
Root Store/Inbox/immutable CONTROL custody, FreshDispatchAuthority,
InstalledGrant policy transport, authenticated Source API и тот же fake Original
Publisher. Модель — явно тестовый callback, геометрия — существующий software
fixture; ML/физическое удержание и реальный путь руки этим не квалифицируются.

После отдельного USER CONTROL выполняется ровно одно предсказание. Его raw9
содержит шесть arm значений и ненулевые fixture base residuals. Ранее явно
зарегистрированный stationary coordinator constraint сохраняет raw9 и выдаёт
requested9 с base0; неизвестные каналы не обнуляются. Настоящий существующий
Publisher callback в fixture возвращает промежуточный gripper85 при intent80.
Source receipt сохраняет именно опубликованный85, исходный target SHA80 и
тот же установленный release grant. Attainment/контакт не выводятся из target.

Workflow достигает RELEASE с actual correlated native host-publication receipt;
`protocol_completed=False`, оба durable latch остаются armed. Повторного
`guard/authorize`, native lease renewal, второго executor или скрытого открытия
из CONTROL нет. Для продолжения нужен новый независимый transfer proof и
отдельный USER phase activation перед движением receiver.

Actual test: 1 PASS / 7.63 s. Первые setup failures требовали добавить отсутствующие
у базового mock Arm методы/config существующего OriginalArm fixture; продуктовые
код, Station.py и исходные настройки контроллера не менялись.
