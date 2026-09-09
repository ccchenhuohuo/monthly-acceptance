"""Actual SQL comparison preserves whitespace that is part of a data identity."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from acceptance_core import Run,digest,read,write
from acceptance_validate import verify_job
from test_execution import record


@pytest.fixture
def worker(tmp_path):
    write(tmp_path/'run.json',{'run_id':'sql-metadata-fixture','policy':{'page_size':2}})
    for name in ('records','evidence','queries'):(tmp_path/name).mkdir()
    return Run(tmp_path)


def recorded(worker,sql):
    job={'job_id':'metadata-job','family':'diagnostic','site':'cn','key_fields':['row_id'],'sql':sql}
    record(worker,job,[{'row_id':1}])
    return job


def actual_sql(worker,job,change):
    eid=worker.job_record(job['job_id'])['evidence_ids'][0]
    mp=worker.path/'records'/(eid+'.json');meta=read(mp)
    ep=worker.path/meta['path'];value=read(ep)
    value['response']['metadata']['query']=change(value['response']['metadata']['query'])
    write(ep,value);meta['sha256']=digest(ep.read_bytes());write(mp,meta)


@pytest.mark.parametrize('literal',[
    "'Audio  Accessories'", '"Audio  Accessories"', '`Audio  Accessories`',
    "'a''  b'", r"'a\'  b'", '"a""  b"', '`a``  b`',
    "'相机\n配件'", "'a\tb'", "'相机\u3000\u3000配件'",
])
def test_strings_and_quoted_identifiers_keep_their_exact_whitespace(worker,literal):
    job=recorded(worker,'SELECT '+literal+' AS value, 1 AS row_id')
    # The original regression normalized whitespace inside a path to the same
    # text. Each change below must remain a different executed query.
    import re
    actual_sql(worker,job,lambda sql:sql.replace(literal,re.sub(r'\s+',' ',literal)))
    assert any('实际 SQL 与请求不一致' in e for e in verify_job(worker,job))


@pytest.mark.parametrize('literal',[
    "'Audio  Accessories'", '"Audio  Accessories"', '`Audio  Accessories`',
    "'a''  b'", r"'a\'  b'", "'相机\n配件'",
])
def test_formatting_outside_literals_is_still_allowed(worker,literal):
    job=recorded(worker,'SELECT '+literal+' AS value, 1 AS row_id')
    actual_sql(worker,job,lambda sql:sql.replace('SELECT ','SELECT\n\t').replace(' AS value, ','\n AS\tvalue,  ')
               .replace(' ORDER BY ','\n\nORDER\tBY '))
    assert verify_job(worker,job)==[]


def test_one_server_added_terminal_semicolon_is_allowed(worker):
    job=recorded(worker,"SELECT 'part;  description' AS value, 1 AS row_id")
    actual_sql(worker,job,lambda sql:'\n '+sql+' ;\n')
    assert verify_job(worker,job)==[]


def test_multiple_statements_are_not_hidden_by_terminal_normalization(worker):
    job=recorded(worker,'SELECT 1 AS row_id')
    actual_sql(worker,job,lambda sql:sql+';;')
    assert verify_job(worker,job)


def test_unclosed_metadata_literal_is_rejected(worker):
    job=recorded(worker,"SELECT 'Audio  Accessories' AS value, 1 AS row_id")
    actual_sql(worker,job,lambda sql:sql.replace("' AS value",' AS value'))
    assert any('引号未闭合' in e for e in verify_job(worker,job))
