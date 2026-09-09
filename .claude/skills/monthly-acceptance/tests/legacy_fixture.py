"""Explicit 3.x fixtures. New-protocol tests use acceptance_core.create_run directly."""
from pathlib import Path
import tempfile,shutil,json
import acceptance_core

def create_run(base,skill,data_month,related=None):
    with tempfile.TemporaryDirectory(prefix='acceptance-legacy-fixture-') as tmp:
        target=Path(tmp)/'skill'
        shutil.copytree(skill,target,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
        p=target/'policies/default.json';v=json.loads(p.read_text());v['version']='3.3.0'
        v.pop('architecture',None);v.pop('question_workflow',None);v.pop('expected_platforms',None)
        v['case_workflow']['version']='3.3.0';p.write_text(json.dumps(v,ensure_ascii=False))
        old=acceptance_core.VERSION
        try:
            acceptance_core.VERSION='3.3.0'
            return acceptance_core.create_run(base,target,data_month,related)
        finally:acceptance_core.VERSION=old
