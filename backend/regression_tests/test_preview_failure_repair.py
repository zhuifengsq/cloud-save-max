"""Offline repair touches only the known transport-error misclassification."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_repair_dry_run_backup_and_idempotence(tmp_path):
    source, backup = tmp_path / 'fake.db', tmp_path / 'backup.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE invalid_share_links (shareurl TEXT PRIMARY KEY,message TEXT)')
        db.execute('CREATE TABLE share_preview_batch_cache (shareurl TEXT PRIMARY KEY,ok INTEGER,message TEXT)')
        db.executemany('INSERT INTO invalid_share_links VALUES (?,?)', [
            ('transient', '账号 fake: request error'), ('invalid', '分享链接已确认失效'),
            ('unknown', 'unknown error')])
        db.executemany('INSERT INTO share_preview_batch_cache VALUES (?,?,?)', [
            ('transient', 0, '账号 fake: request error'), ('invalid', 0, '分享链接已确认失效'),
            ('success', 1, 'request error')])
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'repair_preview_failures.py'
    def run(*args):
        result = subprocess.run([sys.executable, str(script), '--database', str(source), *args],
            capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    assert run() == {'invalid_links': 1, 'negative_cache': 1, 'applied': False}
    with sqlite3.connect(source) as db:
        assert db.execute('SELECT count(*) FROM invalid_share_links').fetchone()[0] == 3
    assert run('--apply', '--backup', str(backup))['applied'] is True
    with sqlite3.connect(backup) as db:
        assert db.execute('SELECT count(*) FROM invalid_share_links').fetchone()[0] == 3
    with sqlite3.connect(source) as db:
        assert db.execute('SELECT shareurl FROM invalid_share_links ORDER BY shareurl').fetchall() == [('invalid',), ('unknown',)]
        assert db.execute('SELECT shareurl FROM share_preview_batch_cache ORDER BY shareurl').fetchall() == [('invalid',), ('success',)]
    assert run() == {'invalid_links': 0, 'negative_cache': 0, 'applied': False}
