"""Browser input qualification uses Node/in-memory HTTP mocks, never the robot."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_browser_gamepad_native_wire_lifecycle_and_neutral_mock_contract():
    node=shutil.which('node')
    if node is None:pytest.skip('Node is required for the browser controller test harness')
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([node,str(root/'tests/browser/gamepad.test.cjs')],cwd=root,
        text=True,capture_output=True,timeout=20)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    proof=json.loads(result.stdout.strip().splitlines()[-1])
    assert proof['passed']==19
    assert proof['actualHardwareRequests']==proof['actualChassisMotion']==0
    assert proof['transport']=='in-memory mock only'
