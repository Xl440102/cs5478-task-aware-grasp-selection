"""Immutable atomic JSON records. Interrupted starts are never blindly retried."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .contacts import _plain


class StoreError(ValueError):
    pass


def canonical(value):
    return json.dumps(_plain(value),sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _unique(pairs):
    result={}
    for k,v in pairs:
        if k in result:raise StoreError('duplicate JSON key')
        result[k]=v
    return result


class EpisodeStore:
    """Atomic install by same-filesystem hard link: no overwrite/rename race.

    fsync data + directory before acknowledging success. Existing records are
    validated byte-for-byte at the semantic payload level. No pickle/eval.
    """
    def __init__(self,root):
        self.root=Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)

    def path(self,name):
        p=Path(name)
        if p.is_absolute() or '..' in p.parts or p.suffix!='.json':raise StoreError('unsafe record path')
        target=self.root/p
        if not target.resolve().is_relative_to(self.root) or target.is_symlink():raise StoreError('record path escapes store')
        return target

    def read(self,name):
        path=self.path(name)
        try:
            value=json.loads(path.read_text(),object_pairs_hook=_unique,
                parse_constant=lambda _:(_ for _ in ()).throw(StoreError('nonfinite JSON')))
            if set(value)!={'schema','sha256','payload'} or value['schema']!='immutable_pilot_record_v1' or digest(value['payload'])!=value['sha256']:
                raise StoreError('record checksum/schema mismatch')
            return value['payload']
        except FileNotFoundError:
            raise
        except (ValueError,TypeError,KeyError,UnicodeError) as exc:
            raise StoreError(f'corrupt record {name}: {exc}') from exc

    def write_once(self,name,payload,*,exclusive=False):
        path=self.path(name);path.parent.mkdir(parents=True,exist_ok=True)
        payload=_plain(payload)
        envelope=dict(schema='immutable_pilot_record_v1',sha256=digest(payload),payload=payload)
        encoded=canonical(envelope)
        fd,tmp=tempfile.mkstemp(prefix='.pending-',dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(encoded);stream.flush();os.fsync(stream.fileno())
            try:
                os.link(tmp,path)
            except FileExistsError:
                existing=self.read(name)
                if digest(existing)!=digest(payload):raise StoreError('existing record differs; refusing overwrite: '+name)
                if exclusive:return False
                return digest(payload)
            directory=os.open(path.parent,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
            return True if exclusive else digest(payload)
        finally:
            os.unlink(tmp)
