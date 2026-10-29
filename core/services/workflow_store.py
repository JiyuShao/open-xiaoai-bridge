"""规则、任务及确认提案的私有存储；消费确认是原子操作。"""
import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
import uuid


class WorkflowStore:
    def __init__(self, path):
        if not isinstance(path,str) or not path.strip():
            raise ValueError("workflow_store_requires_private_path")
        self.path=Path(path).expanduser()

    def _run(self, operation, args):
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path.touch(mode=0o600,exist_ok=True)
        with closing(sqlite3.connect(self.path,timeout=5)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS objects (id TEXT PRIMARY KEY, kind TEXT, version INTEGER, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, session TEXT, action TEXT, payload TEXT, expires REAL, state TEXT)')
            db.execute('BEGIN IMMEDIATE')
            if operation=='pending':
                return [{'proposal_id':row[0],'action':row[1],'proposal':json.loads(row[2])} for row in db.execute(
                    "SELECT id,action,payload FROM proposals WHERE session=? AND state='pending' AND expires>=? ORDER BY rowid",(args[0],time.time()))]
            if operation=='list':
                return [dict(id=row[0],kind=row[1],version=row[2],**json.loads(row[3])) for row in db.execute('SELECT * FROM objects WHERE kind=? ORDER BY id',args)]
            if operation=='get':
                row=db.execute('SELECT * FROM objects WHERE id=?',args).fetchone()
                return dict(id=row[0],kind=row[1],version=row[2],**json.loads(row[3])) if row else None
            if operation=='put':
                identifier,kind,payload,expected=args
                row=db.execute('SELECT version FROM objects WHERE id=?',(identifier,)).fetchone()
                if (row[0] if row else 0)!=expected:
                    raise ValueError('object_version_conflict')
                db.execute('INSERT OR REPLACE INTO objects VALUES (?,?,?,?)',(identifier,kind,expected+1,json.dumps(payload,ensure_ascii=False)))
            elif operation=='delete':
                identifier,expected=args
                if db.execute('DELETE FROM objects WHERE id=? AND version=?',(identifier,expected)).rowcount!=1:
                    raise ValueError('object_version_conflict')
            elif operation=='supersede':
                db.execute("UPDATE proposals SET state='superseded' WHERE session=? AND state='pending'",args)
            elif operation=='propose':
                session,action,payload=args
                identifier=uuid.uuid4().hex
                db.execute("UPDATE proposals SET state='superseded' WHERE session=? AND state='pending'",(session,))
                db.execute('INSERT INTO proposals VALUES (?,?,?,?,?,?)',(identifier,session,action,json.dumps(payload,ensure_ascii=False),time.time()+300,'pending'))
                return identifier
            elif operation=='consume':
                identifier,session,text,allowed=args
                row=db.execute('SELECT session,action,payload,expires,state FROM proposals WHERE id=?',(identifier,)).fetchone()
                if not row or row[0]!=session:
                    raise ValueError('proposal_not_owned_by_session')
                if row[1] not in allowed:
                    raise ValueError('proposal_kind_does_not_match_confirmation_tool')
                if row[4]!='pending':
                    return {'state':row[4],'action':row[1],'payload':json.loads(row[2])}
                accepted=text.strip() in ('确认','同意','是的','确认执行')
                state='consumed' if accepted and row[3]>=time.time() else 'rejected'
                db.execute('UPDATE proposals SET state=? WHERE id=?',(state,identifier))
                return {'state':state,'action':row[1],'payload':json.loads(row[2]),'new':True}
        return None

    async def call(self, operation, *args):
        return await asyncio.to_thread(self._run,operation,args)
