"""Adapter-specific transfer strategies. Writes are submitted only once."""
from collections import Counter
from app.domain.drive_types import SavedFile, TransferResult


def _ok(response):
    return isinstance(response, dict) and str(response.get('code', 0)) in ('0', '200') and str(response.get('status', 200)) in ('0', '200')


def _failed(adapter, response, *, review_errno=False):
    response = response if isinstance(response, dict) else {}
    try:
        verdict = adapter.classify_share_failure(response) or {}
    except Exception:
        verdict = {}
    kind = str(verdict.get('kind') or 'unknown')
    # Baidu -7 at the save stage means review pending, unlike share lookup.
    if review_errno and kind in ('unknown', 'invalid') and (response.get('code') in (-7, '-7') or response.get('errno') in (-7, '-7') or '审核' in str(response.get('message') or '')):
        kind = 'pending'
    return TransferResult('pending' if kind == 'pending' else 'failed', kind=kind,
                          retryable=kind in ('pending', 'throttled'), message=str(verdict.get('message') or response.get('message') or ''))


class TransferStrategy:
    review_errno = False
    def submit(self, adapter, request, destination):
        return adapter.save_file([f.id for f in request.files], [f.token or '' for f in request.files],
                                 destination, request.share_id, request.share_token)

    def destination(self, adapter, request):
        return request.target_dir

    def finish(self, adapter, ids, request):
        return True

    def ids(self, data, queried):
        save_as = data.get('save_as') or {}
        return save_as.get('save_as_top_fids') if queried else data.get('save_as_top_fids')

    def transfer(self, adapter, request, read):
        try:
            destination = self.destination(adapter, request)
        except Exception:
            return TransferResult('failed', message='无法准备转存目录')
        if not destination:
            return TransferResult('failed', message='无法准备转存目录')
        try:
            response = self.submit(adapter, request, destination)
        except Exception:
            # A write may have committed before the connection failed. Never replay it.
            # Inspect the destination, but without a returned ID or a pre-write
            # identity we cannot safely attribute a matching name to this request.
            try:
                read('ls_dir', lambda: adapter.ls_dir(destination))
            except Exception:
                pass
            return TransferResult('pending', kind='pending', message='转存结果待确认')
        if isinstance(response, dict) and response.get('write_uncertain'):
            try:
                read('ls_dir', lambda: adapter.ls_dir(destination))
            except Exception:
                pass
            return TransferResult('pending', kind='pending', message='转存结果待确认')
        if not _ok(response):
            return _failed(adapter, response or {}, review_errno=self.review_errno)
        data = response.get('data') or {}
        job = str(data.get('task_id') or data.get('taskId') or response.get('task_id') or response.get('taskId') or '')
        queried = False
        if job and not data.get('_sync'):
            try:
                response = read('query_task', lambda: adapter.query_task(job))
            except Exception:
                return TransferResult('pending', task_id=job, kind='pending', message='转存结果待确认')
            if not _ok(response):
                return _failed(adapter, response or {})
            data = response.get('data') or {}
            if data.get('status') in (3, '3', 'failed', 'error'):
                return TransferResult('failed', task_id=job, message='转存任务失败')
            if data.get('status') not in (2, '2', None):
                return TransferResult('pending', task_id=job, kind='pending', message='转存任务处理中')
            queried = True
        ids = self.ids(data, queried)
        if not ids:
            return TransferResult('pending', task_id=job, kind='pending', message='转存结果待确认')
        ids = [str(i) for i in ids]
        try:
            listing = read('ls_dir', lambda: adapter.ls_dir(destination))
        except Exception:
            return TransferResult('pending', task_id=job, kind='pending', message='转存结果待确认')
        if not _ok(listing):
            return TransferResult('pending', task_id=job, kind='pending', message='转存结果待确认')
        items = (listing.get('data') or {}).get('list') or []
        by_name = {}
        for item in items:
            fid = str(item.get('fid') or item.get('fs_id') or item.get('file_id') or item.get('id') or item.get('fileId') or '')
            name = str(item.get('file_name') or item.get('server_filename') or item.get('fileName') or item.get('name') or item.get('title') or '')
            if fid in ids:
                by_name.setdefault(name, []).append(fid)
        counts = Counter(f.name for f in request.files)
        if any(counts[f.name] != 1 or len(by_name.get(f.name, [])) != 1 for f in request.files):
            return TransferResult('pending', task_id=job, kind='pending', message='无法唯一关联转存文件')
        files = tuple(SavedFile(f.id, by_name[f.name][0], f.name) for f in request.files)
        try:
            moved = self.finish(adapter, [f.id for f in files], request)
        except Exception:
            return TransferResult('pending', task_id=job, kind='pending', message='移动结果待确认')
        if isinstance(moved, dict) and moved.get('write_uncertain'):
            return TransferResult('pending', task_id=job, kind='pending', message='移动结果待确认')
        if isinstance(moved, dict):
            moved = _ok(moved)
        if not moved:
            return TransferResult('failed', task_id=job, message='移动文件失败')
        return TransferResult('completed', files=files, task_id=job, confirmed=True)


class NamedTransferStrategy(TransferStrategy):
    def submit(self, adapter, request, destination):
        return adapter.save_file([f.id for f in request.files], [f.token or '' for f in request.files],
                                 destination, request.share_id, request.share_token,
                                 file_names=[f.name for f in request.files])


class BaiduTransferStrategy(NamedTransferStrategy):
    review_errno = True


class TokenTransferStrategy(TransferStrategy):
    def transfer(self, adapter, request, read):
        if any(not item.token for item in request.files):
            return TransferResult('failed', kind='invalid', message='分享文件缺少转存令牌')
        return super().transfer(adapter, request, read)


class UCTransferStrategy(TokenTransferStrategy):
    def destination(self, adapter, request):
        return adapter.get_or_create_share_folder()

    def ids(self, data, queried):
        if queried:
            return (data.get('save_as') or {}).get('save_as_select_top_fids') or super().ids(data, queried)
        return super().ids(data, queried)

    def finish(self, adapter, ids, request):
        return adapter.move_files_to_target(ids, request.target_dir)
