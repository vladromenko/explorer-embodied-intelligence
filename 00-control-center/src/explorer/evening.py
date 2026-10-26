"""ENGINEERING_GLUE: evidence-scoped readiness for the owner's observed session."""
from explorer_shared.experiments import registry_document


def evening_status(probes):
    checks=[]
    def add(identity,title,status,detail,category='A'):
        checks.append(dict(id=identity,title=title,status=status,detail=detail,category=category))
    add('semantic_registry','Нумерация и роли опытов','READY','04 — обучение двух SO101; 06 — перенос навыка на ROSMASTER.')
    native=probes.get('native',{});source=probes.get('source',{});reference=probes.get('reference',{});vision=probes.get('vision',{})
    native_ok=native.get('available') is True and native.get('body',{}).get('arm_backend')=='existing_factory_ruckig_velocity'
    add('native_gateway','Штатный исполнитель и наблюдаемая сессия','READY' if native_ok else 'SOFTWARE_BLOCKER',
        'Исполнитель использует исходные Ruckig и настройки. Разрешение выдаётся отдельно.' if native_ok else 'Новый native backend ещё не отвечает. Развёртывание и проверка остаются работой Homebrain; ввод команд от пользователя не требуется.')
    source_body=source.get('body',{});wizard=source_body.get('calibration_wizard') or {};computed=(wizard.get('A') or {}).get('all_computable_A_resolved') is True
    add('leader_mapping_A','Исходная калибровка leader и CAD','READY' if computed else 'SOFTWARE_BLOCKER',
        'Известные исходные калибровки используются повторно.' if computed else 'Требуется подключить новый backend к известным калибровкам; повторная моторная калибровка пользователем не нужна.')
    if computed:
        remaining=wizard.get('remaining_B',[])
        add('leader_mapping_B','Захват leader и направления рук','PHYSICAL_ACTION' if remaining else 'READY',
            'В веб-мастере сохранить два положения захвата и подтвердить оси/ориентацию.' if remaining else 'Три наблюдения сохранены; физический навык этим ещё не подтверждён.','B')
    if native_ok:
        body=reference.get('body',{});valid=reference.get('available') is True and not body.get('blocked_by') and body.get('state_source')=='command_estimate'
        add('native_reference','Текущий boot и командный reference','READY' if valid else 'PHYSICAL_ACTION',
            'Сохраняется честная command-estimate; servo readback остаётся выключенным.' if valid else 'Под наблюдением пользователя восстановить штатный HOME только при недействительном reference, затем подтвердить свежий кадр заново.','B')
    vision_body=vision.get('body',{});vision_ok=vision.get('available') is True and vision_body.get('experiment_numbering_version')==2
    add('source_vision_backend','Камера Pi и источник геометрии','READY' if vision_ok else 'SOFTWARE_BLOCKER',
        'Источник изображения подключён; качество масок и геометрия проверяются отдельно.' if vision_ok else 'Требуется новый observer backend с текущей ролью source04.')
    if vision_ok:
        calibrated=vision_body.get('calibration',{}).get('valid') is True
        add('metric_table','Метрическая калибровка стола','READY' if calibrated else 'PHYSICAL_ACTION',
            'Измеренная калибровка действует для текущей камеры.' if calibrated else 'Распечатать ChArUco в масштабе 100%, проверить линейкой и показать мишень через веб-мастер. Неизвестные координаты остаются неподтверждёнными.','B')
    add('learned_object_skill','Навык с предметом и независимая проверка','PHYSICAL_ACTION',
        'Нужны полные реальные показы, обучение на принятых данных и отдельные наблюдаемые попытки модели. Технический hold, loss и API-ответ не подтверждают владение задачей.','C')
    return dict(schema='explorer_evening_readiness_v1',experiment_numbering_version=2,checks=checks,projects=registry_document()['projects'],
        original_goal_status='IN_PROGRESS',whole_task_completed=False,physical_commands_issued_by_status=0,
        chassis_default='PROHIBITED',controller_settings_modified=False)
