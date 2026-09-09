"""A fresh, byte-bound read snapshot for one verification operation.

This caches evidence, never an approval. Every dependency is read again before
the caller can commit. Ordinary JSON reads return fresh objects; evidence pages
are shared and their in-memory hashes are checked at the same exit boundary.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path


_active = ContextVar('acceptance_verification_snapshot', default=None)


def current_snapshot():
    return _active.get()


def _json_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read_bytes(path, *, loader=None):
    snapshot = current_snapshot()
    if snapshot is not None:
        return snapshot.bytes(path, loader=loader)
    return loader(Path(path)) if loader else Path(path).read_bytes()


def resolved_path(path):
    snapshot = current_snapshot()
    return snapshot.resolve(path) if snapshot is not None else Path(path).resolve()


class VerificationSnapshot:
    def __init__(self, run=None):
        self.run = run
        self._files = {}
        self._aliases = {}
        self._pages = {}
        self._runs = {}
        self._append_paths = set()
        self._token = None

    def __enter__(self):
        self._token = _active.set(self)
        try:
            if self.run is not None: self.pin_run(self.run)
        except BaseException:
            self._deactivate()
            raise
        return self

    def _deactivate(self):
        if self._token is not None:
            _active.reset(self._token)
            self._token = None

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None and self._token is not None: self.verify()
        finally:
            self._deactivate()

    def resolve(self, path):
        path = Path(path).absolute()
        if path not in self._aliases: self._aliases[path] = path.resolve()
        return self._aliases[path]

    def bytes(self, path, *, loader=None):
        path = Path(path).absolute()
        self.resolve(path)
        if path not in self._files:
            self._files[path] = [loader(path) if loader else path.read_bytes(), loader]
        elif loader is not None:
            # Event journals keep their shared append lock for the final reread.
            self._files[path][1] = loader
        return self._files[path][0]

    def pin_run(self, run):
        self.resolve(run.path)
        key = id(run)
        if key not in self._runs:
            saved = json.loads(self.bytes(run.path/'run.json'))
            if saved != run.manifest or run.policy != saved['policy']:
                raise ValueError('校验快照运行版本与已加载对象不一致')
            self._runs[key] = (run, _json_hash(run.manifest))

    def evidence(self, run, eid):
        self.pin_run(run)
        key = (run.path, eid)
        if key not in self._pages:
            meta_bytes = self.bytes(run.path/'records'/(eid+'.json'))
            meta = json.loads(meta_bytes)
            path = run.path/meta['path']
            if not self.resolve(path).is_relative_to(run.path/'evidence'):
                raise ValueError('证据引用或哈希无效：'+eid)
            blob = self.bytes(path)
            if hashlib.sha256(blob).hexdigest() != meta['sha256']:
                raise ValueError('证据引用或哈希无效：'+eid)
            value = json.loads(blob)
            self._pages[key] = (value, _json_hash(value),
                hashlib.sha256(meta_bytes).hexdigest(), meta['sha256'])
        return self._pages[key][0]

    def verify(self):
        for path, resolved in self._aliases.items():
            if path.resolve() != resolved:
                raise ValueError('校验快照来源路径发生变化：'+str(path))
        for path, (blob, loader) in self._files.items():
            current = loader(path) if loader else path.read_bytes()
            if current != blob:
                raise ValueError('校验快照依赖字节发生变化：'+str(path))
        for (owner, eid), (value, sha, _, _) in self._pages.items():
            if _json_hash(value) != sha:
                raise ValueError('校验快照证据缓存被修改：'+str(owner)+'/'+eid)
        for run, sha in self._runs.values():
            if _json_hash(run.manifest) != sha or run.policy != run.manifest['policy']:
                raise ValueError('校验快照运行对象被修改')

    def finish(self):
        """Recheck before authoritative writes, then leave the read phase."""
        try:
            self.verify()
        finally:
            self._deactivate()

    @contextmanager
    def event_appends(self, path):
        """Only the submitter's own evidence.saved appends may advance a journal."""
        self.verify()
        path = Path(path).absolute()
        self._append_paths.add(path)
        try:
            yield
        finally:
            self._append_paths.remove(path)

    def before_event_append(self, path, blob):
        path = Path(path).absolute()
        if path not in self._append_paths: return False
        if blob != self.bytes(path):
            raise ValueError('校验快照事件在合法导入前发生变化')
        return True

    def tracks_event_append(self, path):
        return Path(path).absolute() in self._append_paths

    def record_event_append(self, path, appended):
        path = Path(path).absolute()
        self._files[path][0] += appended


verification_snapshot = VerificationSnapshot
