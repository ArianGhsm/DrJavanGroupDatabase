from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace

import pytest

from drjavanbot.telegram.app_v2 import TelegramBotApp
from drjavanbot.telegram.config import TelegramConfig
from drjavanbot.telegram.update_control import UpdateControl, UpdateStatus


ROOT = Path(__file__).resolve().parents[1]


class FakeAPI:
    def __init__(self):
        self.sent=[]; self.edited=[]; self.callbacks=[]
    def send_message(self, chat_id, text, **kw): self.sent.append((chat_id,text,kw)); return {}
    def edit_message_text(self, chat_id, message_id, text, **kw): self.edited.append((chat_id,message_id,text,kw)); return {}
    def answer_callback(self, callback_id, **kw): self.callbacks.append(callback_id)


class FakeState:
    def access_mode(self): return "owner_only"


class FakeServices:
    def __init__(self): self.update_requests=0; self.rollback_requests=0
    def ai_configured(self): return True
    def model(self): return "deepseek-v4-flash"
    def update_status(self): return UpdateStatus(state="idle")
    def request_software_update(self): self.update_requests+=1; return "abc123"
    def request_rollback(self): self.rollback_requests+=1; return "def456"


def _app():
    return TelegramBotApp(
        api=FakeAPI(), owner_id=42, services=FakeServices(), state=FakeState(),
        config=TelegramConfig(),
    )


def _callback(user: int, data: str):
    return {"id":"cb1","from":{"id":user},"data":data,"message":{"message_id":9,"chat":{"id":user,"type":"private"}}}


def _load_deploy_script(filename: str):
    path=ROOT/"deploy"/filename
    spec=importlib.util.spec_from_file_location(f"drjavan_{filename.replace('.', '_')}_test",path)
    module=importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(module)
    return module


def test_update_control_only_accepts_fixed_actions_and_atomic_request(tmp_path):
    control=UpdateControl(tmp_path/"data")
    request_id=control.request("update")
    payload=json.loads(control.request_path.read_text(encoding="utf-8"))
    assert payload["schema"]==1 and payload["action"]=="update" and payload["request_id"]==request_id
    assert control.status().state=="pending"
    with pytest.raises(RuntimeError): control.request("update")
    with pytest.raises(ValueError):
        UpdateControl(tmp_path/"other"/"data").request("shell:rm -rf /")


def test_update_control_reads_bounded_result(tmp_path):
    control=UpdateControl(tmp_path/"data")
    control.result_path.write_text(json.dumps({"state":"success","current_sha":"a"*40,"target_sha":"b"*40,"message":"ok"}),encoding="utf-8")
    status=control.status()
    assert status.state=="success" and status.target_sha=="b"*40


def test_owner_settings_expose_update_and_confirmation_requests_only_fixed_action():
    app=_app(); app._show_settings(42)
    keyboard=app.api.sent[-1][2]["reply_markup"]["inline_keyboard"]
    assert any(button["callback_data"]=="software_update" for row in keyboard for button in row)
    app._handle_callback(_callback(42,"software_update_confirm"))
    assert app.services.update_requests==1 and "درخواست آپدیت" in app.api.edited[-1][2]


def test_non_owner_cannot_trigger_update():
    app=_app(); app._handle_callback(_callback(7,"software_update_confirm"))
    assert app.services.update_requests==0 and "فقط برای مالک" in app.api.sent[-1][1]


def test_rollback_requires_owner_callback():
    app=_app(); app._handle_callback(_callback(42,"software_rollback_confirm"))
    assert app.services.rollback_requests==1


def test_privileged_updater_rejects_arbitrary_request_action(tmp_path, monkeypatch):
    module=_load_deploy_script("self_update.py"); request=tmp_path/"request.json"
    request.write_text(json.dumps({"schema":1,"request_id":"x","action":"arbitrary-command"}),encoding="utf-8")
    monkeypatch.setattr(module,"REQUEST_FILE",request)
    with pytest.raises(module.UpdateFailure): module._read_request()


def test_updater_repo_and_service_targets_are_hard_coded_https_only():
    module=_load_deploy_script("self_update.py")
    assert module.EXPECTED_REPO=="ArianGhsm/DrJavanGroupDatabase"
    assert module.SERVICE=="drjavanbot.service"
    assert module.ALLOWED_REMOTES
    assert all(remote.startswith("https://github.com/ArianGhsm/DrJavanGroupDatabase") for remote in module.ALLOWED_REMOTES)
    assert not any(remote.startswith("git@") for remote in module.ALLOWED_REMOTES)


def test_systemd_units_are_isolated_request_driven_and_release_bound():
    bot=(ROOT/"deploy/drjavanbot.service").read_text(encoding="utf-8")
    updater=(ROOT/"deploy/drjavanbot-updater.service").read_text(encoding="utf-8")
    path_unit=(ROOT/"deploy/drjavanbot-updater.path").read_text(encoding="utf-8")
    assert "WorkingDirectory=/opt/drjavanbot/current" in bot
    assert "ExecStart=/opt/drjavanbot/current/.venv/bin/drjavanbot-bot" in bot
    assert "WorkingDirectory=/opt/drjavanbot/current" in updater
    assert "ExecStart=/usr/bin/python3 /opt/drjavanbot/current/deploy/self_update.py" in updater
    assert "PathExists=/var/lib/drjavanbot/update/request.json" in path_unit
    assert "RuntimeDirectory=drjavanbot-updater" in updater
    assert "TMPDIR=/run/drjavanbot-updater" in updater
    assert "Dent1402" not in bot+updater+path_unit and "VoiceMatn" not in bot+updater+path_unit


def test_env_example_is_shell_sourceable_with_unicode_space():
    command = 'set -a; . ./.env.example; set +a; printf "%s" "$DRJAVAN_ARCHIVE_DIR"'
    result=subprocess.run(["bash","-c",command],cwd=ROOT,text=True,capture_output=True,check=True)
    assert result.stdout=="گروه دکتر جوان"


def test_bootstrap_env_rewrite_is_quoted_idempotent_and_collapses_duplicates(tmp_path, monkeypatch):
    module=_load_deploy_script("bootstrap_self_update.py")
    env_file=tmp_path/"drjavanbot.env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=x\n"
        "DRJAVAN_ARCHIVE_DIR=/old/path one\n"
        "LOG_LEVEL=INFO\n"
        "DRJAVAN_ARCHIVE_DIR=/duplicate path\n",
        encoding="utf-8",
    )
    os.chmod(env_file,0o600)
    monkeypatch.setattr(module,"ENV_FILE",env_file)
    monkeypatch.setattr(module.os,"chown",lambda *args,**kwargs: None)
    module._rewrite_env_archive_path(); module._rewrite_env_archive_path()
    text=env_file.read_text(encoding="utf-8")
    expected='DRJAVAN_ARCHIVE_DIR="/opt/drjavanbot/current/گروه دکتر جوان"'
    assert text.count("DRJAVAN_ARCHIVE_DIR=")==1
    assert expected in text
    assert stat.S_IMODE(env_file.stat().st_mode)==0o600
    result=subprocess.run(
        ["bash","-c",f'set -a; . "{env_file}"; set +a; printf "%s" "$DRJAVAN_ARCHIVE_DIR"'],
        text=True,capture_output=True,check=True,
    )
    assert result.stdout=="/opt/drjavanbot/current/گروه دکتر جوان"


def test_updater_env_parser_accepts_quoted_unicode_space_and_rejects_unquoted_space(tmp_path, monkeypatch):
    module=_load_deploy_script("self_update.py")
    env_file=tmp_path/"runtime.env"
    monkeypatch.setattr(module,"ENV_FILE",env_file)
    env_file.write_text(
        'TELEGRAM_BOT_TOKEN=123:abc\n'
        'TELEGRAM_OWNER_ID=42\n'
        'DRJAVAN_DATA_DIR=/var/lib/drjavanbot/data\n'
        'DRJAVAN_CACHE_DIR=/var/cache/drjavanbot\n'
        'DRJAVAN_ARCHIVE_DIR="/opt/drjavanbot/current/گروه دکتر جوان"\n',
        encoding="utf-8",
    )
    env=module._load_env(); module._validate_runtime_env(env)
    assert env["DRJAVAN_ARCHIVE_DIR"]=="/opt/drjavanbot/current/گروه دکتر جوان"
    env_file.write_text("BROKEN=/path with space\n",encoding="utf-8")
    with pytest.raises(module.UpdateFailure,match="unquoted whitespace"):
        module._load_env()


def test_updater_runtime_env_rejects_non_numeric_owner():
    module=_load_deploy_script("self_update.py")
    with pytest.raises(module.UpdateFailure,match="numeric"):
        module._validate_runtime_env({
            "TELEGRAM_BOT_TOKEN":"x","TELEGRAM_OWNER_ID":"owner",
            "DRJAVAN_DATA_DIR":"/data","DRJAVAN_CACHE_DIR":"/cache",
        })


def test_updater_stage_gates_force_private_temp_and_pytest_basetemp(tmp_path, monkeypatch):
    module=_load_deploy_script("self_update.py")
    release=tmp_path/"release with space"; release.mkdir()
    stage=tmp_path/"stage"
    calls=[]
    def fake_run(cmd,**kwargs):
        calls.append((cmd,kwargs))
        if "reindex" in cmd:
            db=stage/"data"/"archive.sqlite3"; db.parent.mkdir(parents=True,exist_ok=True); db.touch()
        return SimpleNamespace(returncode=0,stdout="",stderr="")
    monkeypatch.setattr(module,"_run",fake_run)
    stage_db=module._run_stage_gates(release,stage,{"TELEGRAM_BOT_TOKEN":"","TELEGRAM_OWNER_ID":"42","DRJAVAN_DATA_DIR":"/data","DRJAVAN_CACHE_DIR":"/cache"})
    assert stage_db==stage/"data"/"archive.sqlite3"
    pytest_calls=[item for item in calls if "pytest" in item[0]]
    assert len(pytest_calls)==1
    cmd,kwargs=pytest_calls[0]
    assert "--basetemp" in cmd and str(stage/"pytest") in cmd
    env=kwargs["env"]
    assert env["TMPDIR"]==str(stage/"tmp") and env["TEMP"]==str(stage/"tmp") and env["TMP"]==str(stage/"tmp")
    assert env["DRJAVAN_ARCHIVE_DIR"]==str(release/"گروه دکتر جوان")


def test_sqlite_rollback_removes_new_database_when_none_existed_before(tmp_path):
    module=_load_deploy_script("self_update.py")
    db=tmp_path/"archive.sqlite3"; db.write_bytes(b"new")
    Path(str(db)+"-wal").write_bytes(b"wal")
    module._restore_sqlite(tmp_path/"missing-backup.sqlite3",db,existed_before=False)
    assert not db.exists() and not Path(str(db)+"-wal").exists()


def test_sqlite_rollback_requires_backup_when_database_preexisted(tmp_path):
    module=_load_deploy_script("self_update.py")
    db=tmp_path/"archive.sqlite3"; db.write_bytes(b"new")
    with pytest.raises(module.UpdateFailure,match="backup is missing"):
        module._restore_sqlite(tmp_path/"missing.sqlite3",db,existed_before=True)


def test_bootstrap_preserves_existing_release_history(tmp_path, monkeypatch):
    module=_load_deploy_script("bootstrap_self_update.py")
    update_dir=tmp_path/"update"; update_dir.mkdir()
    old="a"*40; middle="b"*40; new="c"*40
    (update_dir/"history.json").write_text(json.dumps([old,middle]),encoding="utf-8")
    previous=tmp_path/old; previous.mkdir(); (previous/".deploy_commit").write_text(old+"\n",encoding="utf-8")
    monkeypatch.setattr(module,"UPDATE_DIR",update_dir)
    monkeypatch.setattr(module.os,"chown",lambda *args,**kwargs: None)
    account=SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())
    module._prepare_update_state(account,new,previous)
    history=json.loads((update_dir/"history.json").read_text(encoding="utf-8"))
    assert history==[middle,old,new]


def test_updater_has_no_post_deploy_git_reset_hard():
    source=(ROOT/"deploy/self_update.py").read_text(encoding="utf-8")
    assert '"reset", "--hard"' not in source


def test_deployment_docs_never_show_unquoted_archive_path_with_spaces():
    docs=(ROOT/"DEPLOYMENT.md").read_text(encoding="utf-8")
    bad="DRJAVAN_ARCHIVE_DIR=/opt/drjavanbot/app/گروه دکتر جوان"
    assert bad not in docs
