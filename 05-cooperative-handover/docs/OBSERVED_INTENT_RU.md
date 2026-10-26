# Текущий наблюдаемый допуск05

`explorer.cooperative_observed_service.CooperativeObservedService` —
ENGINEERING_GLUE поверх существующих native permission providers. Конструктор
и обычный snapshot не обращаются к устройствам и не импортируют модели.

Новый сеанс сначала создаётся как `observation_only` metadata intent. Точный
текущий confirmed Spec, source Skill/model hashes, native роли, объект, процесс
координатора и эпоха связываются в серверном контексте. Старые записи, shadow
результаты, начатый протокол или использованная эпоха не становятся разрешением
для исполнения. Прежнее общее разрешение Nano без нового подтверждения для
этого intent также не подходит.

Все команды центрального сервиса требуют `session_id`, `expected_epoch` и
серверный authenticated actor. Browser передаёт только выданные сервером choice
IDs, не native grant/token/URL/path:

| operation | дополнительные поля |
|---|---|
| `observed_refresh` | нет; явно читает текущие native status |
| `observed_challenge` | `source_setup_choice_id` из `observed_setup.setup_choices[role]` |
| `observed_authorize` | `challenge_choice_id`, `displayed_jpeg_sha256`, `observing`, `user_observed_test_confirmed`; Nano: `neutral`, `space_clear`; Pi: `robot_workspace_checked` |
| `bind_observed_test` | `observed_choice_id` из `bind_choice_id` после обеих USER grants |
| `observed_display` | Pi `permission_choice_id` из `permission_choices.receiver` |
| `observed_observe` | `observation_choice_id`, `displayed_jpeg_sha256`, `observing=true` |
| `observed_revoke` | точный `permission_choice_id`; только metadata CAS revoke |

`observed_challenge`/`observed_display` возвращают opaque choice ID и
`frame_url=/api/cooperative/observed-frame/{id}`. Центральный authenticated binary
handler вызывает `service.observed_frame(id, authenticated_actor=...)` и
возвращает bytes/headers без JSON/base64. Используется только исходный pinned
JPEG: SHA, native frame ID и boot ID проверяются. Перед authorize/observe этот
точный кадр должен быть действительно выдан тому же оператору; current preview
другой камеры не заменяет его. Native challenge и локальная связь choice
действуют максимум2с, clocks Pi/Nano/NX не приравниваются.

Pi renewal берёт существующий `/station/observation` и его
`/station/observations/{id}/frame`, сверяет native boot/calibration с текущим
grant. Новая camera capture не открывается. Только явное `observed_observe`
передаёт точный показанный кадр в общий provider. Свежая камера, warm worker,
прошлый checkbox или background polling сами ничего не продлевают. Используется
существующий `camera.stale_s`; motor leases/watchdogs не меняются. Nano native
operator lease остаётся отдельным текущим USER input контрактом.

`bind_observed_test` сохраняет metadata binding без смены protocol epoch и
без begin/resume/select/input/action/lease либо подключения transport. До двух
реальных scoped transport callbacks и текущих native leases
`execution_authority.explicit_user_observed_test_current` остаётся false.
Native begin, resume и attach — отдельные явные USER действия. Готовый compute
actor не означает физического допуска или освоенного навыка.

STOP сначала инвалидирует cooperative epoch, затем отзывает только связанные
точные native permission IDs/epochs; закрытие координатора делает такой же
metadata cleanup. Поздний authorize/observe повторно проверяет epoch под тем
же workflow lock и CAS отзывает только свой старый grant. Старый cleanup не
затрагивает новое разрешение другого теста. Никакие physical commands этим
helper не посылаются; фактическая проверка движения остаётся отдельным
пользовательским наблюдаемым испытанием.

Проверки: `tests/test_cooperative_observed_service.py` использует настоящий
temporary workflow/Store, native FastAPI activation и общий Pi permission
provider с fake original Station. Проверяются original JPEG, два native clocks,
неподмена actor, metadata-only bind, source/session/grant fences, поздний ответ
после STOP, fresh exact-frame observation, старый revoke и shutdown. Аппаратного
исполнения эти тесты не подтверждают.
