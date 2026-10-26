"""ENGINEERING_GLUE: semantic experiment identities and explicit numbering epochs.

Numbering v1 is historical; v2 is canonical. Callers reading an old signed/raw
record must supply its epoch rather than reinterpret an ambiguous numeric ID.
This module performs no migration or filesystem/database/hardware operation.
"""
from copy import deepcopy

EXPERIMENT_NUMBERING_VERSION=2
LEGACY_NUMBERING_VERSION=1

_EXPERIMENTS=(
    dict(id='01',slug='autonomous_learning',title='Автономное обучение',embodiment='ROSMASTER simulation',directory='01-autonomous-learning'),
    dict(id='02',slug='rosmaster_keyboard_learning',title='Показы на ROSMASTER',embodiment='whole ROSMASTER mobile manipulator',directory='02-demonstration-learning',action_dimension=9,primary_camera='nano-rgbd'),
    dict(id='03',slug='rosmaster_leader_learning',title='SO-101 → ROSMASTER',embodiment='ROSMASTER target; SO-101 leader input',directory='03-lerobot-teaching',action_dimension=9,primary_camera='nano-rgbd'),
    dict(id='04',slug='so101_source_learning',title='Две руки LeRobot · показ, обучение и повторение',embodiment='SO-101 leader and follower on Raspberry Pi',directory='04-so101-source-learning',primary_camera='pi-overview'),
    dict(id='05',slug='cooperative_handover',title='Совместная передача',embodiment='ROSMASTER and SO-101 receiver',directory='05-cooperative-handover'),
    dict(id='06',slug='cross_embodiment_transfer',title='Перенос навыка SO101 → ROSMASTER',embodiment='qualified source skill adapted to whole ROSMASTER',directory='06-cross-embodiment-transfer',action_dimension=9),
)
_BY_SLUG={row['slug']:row for row in _EXPERIMENTS}
_CURRENT={row['id']:row['slug'] for row in _EXPERIMENTS}
_LEGACY={'01':'autonomous_learning','02':'rosmaster_keyboard_learning','03':'rosmaster_leader_learning',
         '04':'cross_embodiment_transfer','05':'cooperative_handover','06':'so101_source_learning','personal':'so101_source_learning'}


def experiment_id(slug):
    if not isinstance(slug,str) or slug not in _BY_SLUG:raise ValueError('Unknown semantic experiment slug: '+str(slug))
    return _BY_SLUG[slug]['id']


def experiment_slug(value,*,numbering_version):
    if numbering_version not in (1,2) or isinstance(numbering_version,bool):raise ValueError('Explicit supported experiment numbering version required')
    if isinstance(value,str) and value in _BY_SLUG:return value
    if value=='personal':return 'so101_source_learning'
    mapping=_LEGACY if numbering_version==LEGACY_NUMBERING_VERSION else _CURRENT
    if not isinstance(value,str) or value not in mapping:raise ValueError('Unknown experiment identity for this numbering version')
    return mapping[value]


def canonical_experiment(value,*,numbering_version):
    return experiment_id(experiment_slug(value,numbering_version=numbering_version))


def experiment_identity(slug):
    return dict(project_id=experiment_id(slug),experiment_slug=slug,experiment_numbering_version=EXPERIMENT_NUMBERING_VERSION)


def experiment_directory(slug):
    experiment_id(slug)
    return _BY_SLUG[slug]['directory']


def registry_document():
    projects=[dict(row,route='/experiments/'+row['id'],physical_ready=False,experiment_numbering_version=EXPERIMENT_NUMBERING_VERSION) for row in _EXPERIMENTS]
    return dict(schema='explorer_projects_v2',experiment_numbering_version=EXPERIMENT_NUMBERING_VERSION,projects=deepcopy(projects),
        source_preservation='Historical scientific bytes and signed events retain original identity; hashed migration projections bind them to semantic slugs.',
        constraints=dict(chassis_motion='PROHIBITED_WITHOUT_USER_OBSERVED_TEST',arm_motion='SEPARATE_USER_OBSERVED_TEST_REQUIRED',controller_configuration='PRESERVE_ORIGINAL'))
