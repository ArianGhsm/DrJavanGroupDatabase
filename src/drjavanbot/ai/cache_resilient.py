from __future__ import annotations
import json,sqlite3,time
from .cache import ResponseCache
from .models import AnswerResult
class ResilientResponseCache(ResponseCache):
    def get(self,key:str)->AnswerResult|None:
        now=time.time()
        with self._connect() as con:
            row=con.execute("SELECT payload_json,expires_at FROM response_cache WHERE cache_key=?",(key,)).fetchone()
            if row is None: self._increment_counter(con,"misses"); return None
            if float(row["expires_at"])<=now: con.execute("DELETE FROM response_cache WHERE cache_key=?",(key,)); self._increment_counter(con,"misses"); return None
            try:
                payload=json.loads(row["payload_json"])
                if not isinstance(payload,dict): raise ValueError
                answer=AnswerResult.from_dict(payload)
            except (json.JSONDecodeError,TypeError,ValueError,KeyError): con.execute("DELETE FROM response_cache WHERE cache_key=?",(key,)); self._increment_counter(con,"misses"); return None
            con.execute("UPDATE response_cache SET hits=hits+1 WHERE cache_key=?",(key,)); self._increment_counter(con,"hits"); return answer.with_runtime(cache_hit=True,ai_calls=0)
    def _connect(self):
        con=sqlite3.connect(self.path,timeout=5); con.row_factory=sqlite3.Row; con.execute("PRAGMA busy_timeout=5000"); con.execute("PRAGMA journal_mode=WAL"); con.execute("PRAGMA synchronous=NORMAL"); return con
