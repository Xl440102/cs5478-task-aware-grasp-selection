"""Test-only historical source binding, never rewrite or relabel golden caches.

Uses real git archive + diff, hashes, dependency/asset checks. No verifier is
mocked. Environment routing is scoped and restored; run these tests serially.
Only historical static filter data are returned; no episode/selector is run.
"""
from contextlib import contextmanager
from functools import lru_cache
import atexit
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=4)
def snapshot(commit):
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('exact historical git commit required')
    tmp = tempfile.TemporaryDirectory(prefix='wrench-history-')
    atexit.register(tmp.cleanup)
    root = Path(tmp.name)
    data = subprocess.check_output(['git', 'archive', commit, 'grasp_a', 'grasp_b', 'assets'], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(root, filter='data')
    if (root/'grasp_a/contracts.py').read_bytes() != (ROOT/'grasp_a/contracts.py').read_bytes():
        raise ValueError('historical fixture needs a separately isolated contracts module')
    name = 'grasp_a._wrench_test_control_' + commit
    spec = importlib.util.spec_from_file_location(name, root / 'grasp_a/control.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return root, module


@contextmanager
def historical_filter_context(directory, scene='wide_strict_v2'):
    manifest = json.loads((Path(directory) / 'summary.json').read_text())
    root, control = snapshot(manifest['git_head'])
    git_dir = subprocess.check_output(['git', 'rev-parse', '--absolute-git-dir'], cwd=ROOT, text=True).strip()
    # Let the existing verifier's git diff examine the ACTUAL extracted tree.
    with patch.dict(os.environ, {'GIT_DIR': git_dir, 'GIT_WORK_TREE': str(root), 'GIT_OPTIONAL_LOCKS': '0'}):
        yield root, control.scene_randomization(scene)


def historical_filter(directory, task, seed, candidates, *, scene='wide_strict_v2'):
    from grasp_a.sequence_contracts import SequenceConfig
    from grasp_b.wrench_v2.evaluation_cli import verified_cached_filter
    with historical_filter_context(directory, scene) as (root, config):
        return verified_cached_filter(directory, task, seed, candidates,
            sequence_config=SequenceConfig(), scene_config=config, root=root)
