"""Pure freeze/config guards; no interpreter/model/robot process is launched."""
import json
from pathlib import Path
import subprocess

import pytest

from native02_queue_helpers import freeze_source,private_config,sha


def committed_repo(tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    subprocess.run(['git','init','--quiet',str(repo)],check=True)
    (repo/'source.py').write_text('original=1\n');subprocess.run(['git','-C',str(repo),'add','source.py'],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Software fixture','-c','user.email=fixture@invalid','commit','-qm','private fixture'],check=True)
    return repo


def test_real_git_archive_exact_bytes_and_commit_addressed_release(tmp_path):
    repo=committed_repo(tmp_path);release,receipt=freeze_source(repo,tmp_path/'frozen',['source.py'])
    assert release.name==receipt['source_commit'] and (release/'RELEASE_COMMIT').read_text().strip()==release.name
    assert (release/'source.py').read_bytes()==(repo/'source.py').read_bytes()
    assert receipt['frozen_source_files_sha256']['source.py']==sha(repo/'source.py')
    assert receipt['archive_sha256']==sha(tmp_path/'frozen/source.tar')
    path=private_config(tmp_path/'private.json',tmp_path/'isolated-state')
    assert json.loads(path.read_text())=={'data_dir':str((tmp_path/'isolated-state').resolve()),'nodes':[]}


def test_dirty_source_refused_before_archive_and_production_state_never_selected(tmp_path):
    repo=committed_repo(tmp_path);(repo/'source.py').write_text('modified=2\n')
    with pytest.raises(ValueError,match='clean'):freeze_source(repo,tmp_path/'frozen',['source.py'])
    assert not (tmp_path/'frozen').exists()
    with pytest.raises(ValueError,match='production'):private_config(tmp_path/'invalid.json',Path.home()/'.local/share/explorer/state')
    assert not (tmp_path/'invalid.json').exists()
