"""Actual official minimal MuJoCo API, no CAD/render/step/controller/model."""
import pytest

from explorer_shared.contact_diagnosis import signed_world_force


@pytest.mark.parametrize('object_first',[False,True])
def test_actual_contact_force_sign_on_free_object_matches_generalized_constraint_force(object_first):
    mujoco=pytest.importorskip('mujoco')
    import numpy as np
    object_xml='<body name="object" pos="0 0 .017"><freejoint/><geom name="object_geom" type="box" size=".02 .02 .01" mass=".0126"/></body>'
    support_xml='<body name="support" pos="0 0 .005"><geom name="support_geom" type="box" size=".05 .05 .005"/></body>'
    bodies=object_xml+support_xml if object_first else support_xml+object_xml
    model=mujoco.MjModel.from_xml_string('<mujoco><option integrator="discrete" cone="elliptic"/><worldbody>'+bodies+'</worldbody></mujoco>')
    data=mujoco.MjData(model);mujoco.mj_forward(model,data)
    object_id=model.geom('object_geom').id;total=np.zeros(3);sides=set();wrench=np.zeros(6)
    for index,contact in enumerate(data.contact[:data.ncon]):
        assert object_id in contact.geom
        side=list(contact.geom).index(object_id);sides.add(side)
        mujoco.mj_contactForce(model,data,index,wrench)
        total+=signed_world_force(contact.frame.tolist(),wrench.tolist(),object_side=side)
    assert sides==({0} if object_first else {1})
    assert total[2]>0 and np.allclose(total,data.qfrc_constraint[:3],rtol=1e-11,atol=1e-11)
    assert data.time==0  # no dynamics or original-source replay


def test_actual_official_discrete_integrator_enum_is_numerical_integrator_not_contact_flag():
    mujoco=pytest.importorskip('mujoco')
    model=mujoco.MjModel.from_xml_string('<mujoco><option integrator="discrete"/></mujoco>')
    assert int(model.opt.integrator)==int(mujoco.mjtIntegrator.mjINT_DISCRETE)==4
    assert mujoco.mjtIntegrator(int(model.opt.integrator)).name=='mjINT_DISCRETE'
