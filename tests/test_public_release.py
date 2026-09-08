from pathlib import Path
import hashlib,json,sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_public import audit_source,selected_files
from publish_github import validate_repo,asset_inventory,verify_remote_assets,PublishError,read_state

@pytest.mark.parametrize('value',['org/repo/extra','/repo','owner/../x','owner/name.git','owner/a b'])
def test_invalid_repository_names(value):
    with pytest.raises(PublishError):validate_repo(value)

def test_intended_repository():assert validate_repo('suwujin-code/soulx-studio')==('suwujin-code','soulx-studio')

def test_assets_verify_hash(tmp_path):
    data=b'reviewed release';(tmp_path/'preview.zip').write_bytes(data)
    (tmp_path/'ASSETS.json').write_text(json.dumps([{'name':'preview.zip','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}]))
    assert len(asset_inventory(tmp_path))==1
    (tmp_path/'preview.zip').write_bytes(b'changed')
    with pytest.raises(PublishError):asset_inventory(tmp_path)

def test_asset_path_traversal_refused(tmp_path):
    (tmp_path/'ASSETS.json').write_text(json.dumps([{'name':'../private.zip'}]))
    with pytest.raises(PublishError):asset_inventory(tmp_path)

def test_remote_asset_verification():
    expected=[{'name':'a.zip','bytes':12,'sha256':'a'*64}]
    verify_remote_assets(expected,[{'name':'a.zip','size':12,'state':'uploaded','digest':'sha256:'+'a'*64}])
    with pytest.raises(PublishError):verify_remote_assets(expected,[{'name':'a.zip','size':13,'state':'uploaded'}])
    with pytest.raises(PublishError):verify_remote_assets(expected,[{'name':'a.zip','size':12,'state':'uploaded','digest':'sha256:'+'b'*64}])

def test_unrelated_receipt_refused(tmp_path):
    p=tmp_path/'receipt.json';p.write_text(json.dumps({'repository':'someone/other'}))
    with pytest.raises(PublishError):read_state(p,'suwujin-code/soulx-studio')

def test_model_data_and_local_files_not_selected(tmp_path):
    (tmp_path/'workbench_data').mkdir();(tmp_path/'workbench_data'/'voice.wav').write_bytes(b'private')
    (tmp_path/'desktop-installer').mkdir();(tmp_path/'desktop-installer'/'payload.zip').write_bytes(b'generated')
    (tmp_path/'README.md').write_text('hello')
    assert [p.name for p in selected_files(tmp_path)]==['README.md']

def test_audit_does_not_echo_secret(tmp_path):
    (tmp_path/'scripts').mkdir();p=tmp_path/'scripts'/'example.py';secret='ghp_'+'X'*40;p.write_text('token='+repr(secret))
    report=audit_source(tmp_path)
    assert report['status']=='FAIL'
    assert any(i['category']=='github_token' for i in report['issues'])
    assert secret not in json.dumps(report)

def test_gitignore_keeps_model_source_but_excludes_downloaded_weights(tmp_path):
    import subprocess,shutil
    if not shutil.which('git'):pytest.skip('git not installed in test environment')
    source=Path(__file__).resolve().parents[1]
    shutil.copy2(source/'.gitignore',tmp_path/'.gitignore')
    subprocess.run(['git','init','-q'],cwd=tmp_path,check=True)
    def ignored(path):return subprocess.run(['git','check-ignore','-q',path],cwd=tmp_path).returncode==0
    assert not ignored('soulxpodcast/models/soulxpodcast.py')
    assert ignored('models/downloaded-weight.bin')
    assert ignored('pretrained_models/model.safetensors')
    assert ignored('workbench_data/project.sqlite3')

def test_local_publish_preparation_preserves_unicode_filenames(tmp_path):
    import subprocess,shutil
    from publish_github import ensure_local_git
    if not shutil.which('git'):pytest.skip('git not installed in test environment')
    (tmp_path/'README.md').write_text('public preview',encoding='utf8')
    (tmp_path/'frontend/dist').mkdir(parents=True)
    (tmp_path/'frontend/dist/前端预览.html').write_text('<html>preview</html>',encoding='utf8')
    ensure_local_git(tmp_path)
    files=subprocess.check_output(['git','ls-files','-z'],cwd=tmp_path).decode('utf8').split('\0')
    assert 'frontend/dist/前端预览.html' in files
    assert not subprocess.check_output(['git','remote','-v'],cwd=tmp_path).strip()
