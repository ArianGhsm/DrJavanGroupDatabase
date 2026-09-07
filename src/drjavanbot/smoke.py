from __future__ import annotations
import argparse,json,sqlite3
from drjavanbot.config import Settings
from drjavanbot.storage import database_health
from drjavanbot.telegram.api import TelegramAPI
def _fts5_available():
    con=sqlite3.connect(":memory:")
    try: con.execute("CREATE VIRTUAL TABLE smoke_fts USING fts5(text)"); return True
    except sqlite3.OperationalError: return False
    finally: con.close()
def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--telegram",action="store_true"); args=p.parse_args(argv); settings=Settings.from_env(require_runtime=args.telegram); db=settings.data_dir/"archive.sqlite3"; checks={"fts5":_fts5_available(),"archive_exists":settings.archive_dir.is_dir(),"index":database_health(db)}
    if args.telegram:
        me=TelegramAPI(settings.telegram_bot_token).get_me(); checks["telegram"]={"ok":bool(me.get("id")),"bot_id":me.get("id")}
    healthy=bool(checks["fts5"]) and bool(checks["archive_exists"]) and bool((checks["index"] or {}).get("healthy")) and (not args.telegram or bool(checks["telegram"]["ok"])); checks["healthy"]=healthy; print(json.dumps(checks,ensure_ascii=False,indent=2)); return 0 if healthy else 1
if __name__=="__main__": raise SystemExit(main())
