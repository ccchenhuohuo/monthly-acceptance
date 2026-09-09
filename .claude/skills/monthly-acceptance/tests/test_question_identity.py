"""Execute complete anchored identity queries on adversarial source records.

Only table names, the STRING cast, and null-safe equality are adapted to SQLite.
The same SQL projection, joins, grouping and pagination execute unchanged.
"""
import copy
import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from acceptance_core import canonical
from acceptance_queries import paginated,question_jobs,verify_question_job


TARGET=['音频','麦克风','无线麦克风']
OTHER=['配件','连接配件','转接头']


@pytest.fixture
def manifest():
    policy=json.loads((Path(__file__).resolve().parents[1]/'policies/default.json').read_text())
    return {'sites':['cn','US'],'starts':{'cn':'2024-01','US':'2024-01'},
            'data_month':'2024-07','policy':policy}


@pytest.fixture
def db():
    conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
    text_fields=['site','platform','month_dt','product_id','sku_id','product_title','product_title_cn',
                 'sku_title','std_brand_name','product_url','page_price','page_sales','product_listing_time',
                 *['category_'+str(i) for i in range(1,6)],'sub_category',
                 *['stdcategory'+str(i) for i in range(1,4)]]
    cols={**{name:'TEXT' for name in text_fields},**{name:'REAL' for name in ['count','discount_sales','discount_price']}}
    for layer in ('raw','std'):
        conn.execute('CREATE TABLE '+layer+' ('+','.join('`'+k+'` '+v for k,v in cols.items())+')')
    yield conn
    conn.close()


def row(**changes):
    item={'site':'cn','platform':'Taobao','month_dt':'2024-05-01','product_id':'old-parent','sku_id':'moving',
          'product_title':'','product_title_cn':None,'sku_title':'One pack','std_brand_name':'Example',
          'product_url':'https://example.invalid/product','discount_price':10,'page_price':'10',
          'page_sales':'1 sold','product_listing_time':'2024-01-01','discount_sales':10,'count':1,
          **dict(zip(['stdcategory1','stdcategory2','stdcategory3'],TARGET)),
          'category_1':'Electronics','category_2':'Audio','category_3':None,'category_4':None,
          'category_5':None,'sub_category':'Microphones'}
    item.update(changes);return item


def add(db,item,layers=('raw','std')):
    for layer in layers:
        db.execute('INSERT INTO '+layer+' ('+','.join('`'+k+'`' for k in item)+') VALUES ('
                   +','.join('?' for _ in item)+')',list(item.values()))


def unit(**changes):
    value={'site':'cn','platforms':['Taobao'],'source_layers':['raw','std'],'path_layer':'std','paths':[TARGET],
           'event_months':['2024-06'],'background_months':[],'anchor_months':['2024-05'],'anchor_paths':[TARGET]}
    value.update(changes);return value


def execute(db,manifest,sql):
    sql=sql.replace(manifest['policy']['raw_table'],'raw').replace(manifest['policy']['std_table'],'std')
    sql=re.sub(r'\bAS STRING\b','AS TEXT',sql).replace('<=>','IS')
    return [dict(r) for r in db.execute(sql)]


def results(db,manifest,scope=None):
    return [r for j in question_jobs(manifest,scope or unit(),'lineage') for r in execute(db,manifest,j['sql'])]


def outside(**changes):
    return row(**{**dict(zip(['stdcategory1','stdcategory2','stdcategory3'],OTHER)),
                  'sub_category':'Adapters',**changes})


def test_exited_anchor_objects_follow_all_paths_layers_and_parent_changes(db,manifest):
    add(db,row())
    add(db,outside(month_dt='2024-06-01',product_id='new-parent',discount_sales=20))
    add(db,outside(month_dt='2024-06-01',sku_id='new-child',discount_sales=30))
    add(db,outside(month_dt='2024-06-01',product_id='raw-destination',discount_sales=40),('raw',))
    add(db,row(month_dt='2024-06-01',product_id='new-target-parent',sku_id='new-target-child',discount_sales=500))
    # A discovered destination parent is not recursively promoted to a new
    # anchor: its unrelated children do not become part of the fixed population.
    add(db,outside(month_dt='2024-06-01',product_id='new-parent',sku_id='unrelated-child',discount_sales=600))
    rows=results(db,manifest)
    assert len(rows)==5
    assert {r['product_id'] for r in rows}=={'old-parent','new-parent','raw-destination'}
    assert next(r for r in rows if r['product_id']=='raw-destination')['source_layer']=='raw'
    moved=[r for r in rows if r['product_id']=='new-parent']
    assert all(r['matches_anchor_parent']==0 and r['matches_anchor_sku']==1
               and r['matches_anchor_original_key']==0 for r in moved)
    assert all(r['in_anchor_std_path']==0 for r in rows if r['source_layer']=='std')
    assert all(r['in_anchor_std_path'] is None for r in rows if r['source_layer']=='raw')


def test_anchor_expands_complete_original_parent_children_before_tracking(db,manifest):
    add(db,row())
    # These children were outside the selected standard path or raw-only at
    # the anchor date. They still belong to the complete anchored parent.
    add(db,outside(sku_id='outside-sibling'))
    add(db,outside(sku_id='raw-only-sibling'),('raw',))
    add(db,outside(month_dt='2024-06-01',product_id='moved-sibling',sku_id='outside-sibling'))
    add(db,outside(month_dt='2024-06-01',product_id='moved-raw-sibling',sku_id='raw-only-sibling'),('raw',))
    rows=results(db,manifest)
    assert len(rows)==3
    assert {r['product_id'] for r in rows}=={'moved-sibling','moved-raw-sibling'}
    assert all(r['matches_anchor_sku']==1 and r['matches_anchor_parent']==0 for r in rows)


def test_identity_never_crosses_platform_or_site_even_when_both_platforms_are_selected(db,manifest):
    add(db,row())
    add(db,row(platform='Tmall',product_id='tmall-anchor',sku_id='tmall-key'))
    add(db,outside(month_dt='2024-06-01',product_id='new-parent'))
    add(db,outside(month_dt='2024-06-01',platform='Tmall',discount_sales=700))
    add(db,outside(month_dt='2024-06-01',site='US',discount_sales=800))
    add(db,outside(month_dt='2024-06-01',platform='Taobao',product_id='tmall-anchor',sku_id='tmall-key',discount_sales=900))
    rows=results(db,manifest,unit(platforms=['Taobao','Tmall']))
    assert len(rows)==2
    assert {(r['site'],r['platform'],r['product_id']) for r in rows}=={('cn','Taobao','new-parent')}


@pytest.mark.parametrize('placeholder',[None,'',' ','0',' 0 '])
def test_placeholder_sku_stays_with_original_parent_and_exact_key(db,manifest,placeholder):
    add(db,row(sku_id=placeholder))
    add(db,outside(month_dt='2024-06-01',sku_id=placeholder))
    add(db,outside(month_dt='2024-06-01',product_id='unrelated-parent',sku_id=placeholder,discount_sales=999))
    other_placeholder='0' if placeholder!='0' else None
    add(db,outside(month_dt='2024-06-01',sku_id=other_placeholder,discount_sales=20))
    rows=results(db,manifest)
    assert len(rows)==4
    assert all(r['product_id']=='old-parent' and r['matches_anchor_parent']==1 and r['matches_anchor_sku']==0 for r in rows)
    assert all(r['matches_anchor_original_key']==int(r['sku_id']==placeholder) for r in rows)


@pytest.mark.parametrize('parent',[None,'',' '])
def test_invalid_parent_and_placeholder_anchor_is_retained_but_not_linked(db,manifest,parent):
    add(db,row(product_id=parent,sku_id=None))
    add(db,outside(month_dt='2024-06-01',product_id=parent,sku_id=None,discount_sales=999))
    rows=results(db,manifest,unit(background_months=['2024-05']))
    assert len(rows)==1
    record=rows[0]
    assert record['source_layer']=='std' and record['month_dt']=='2024-05-01'
    assert record['is_anchor_path_record']==1
    assert record['matches_anchor_parent']==record['matches_anchor_sku']==record['matches_anchor_original_key']==0


@pytest.mark.parametrize('parent',[None,'',' '])
def test_valid_sku_from_invalid_parent_anchor_still_traces_without_merging_other_missing_parents(db,manifest,parent):
    add(db,row(product_id=parent,sku_id='usable-source-key'))
    add(db,outside(month_dt='2024-06-01',product_id='resolved-parent',sku_id='usable-source-key'))
    # An invalid parent does not make all other missing-parent records siblings.
    add(db,outside(product_id=parent,sku_id='unrelated-key'))
    add(db,outside(month_dt='2024-06-01',product_id='unrelated-parent',sku_id='unrelated-key',discount_sales=999))
    rows=results(db,manifest)
    assert len(rows)==2
    assert {r['product_id'] for r in rows}=={'resolved-parent'}
    assert all(r['matches_anchor_parent']==0 and r['matches_anchor_sku']==1
               and r['matches_anchor_original_key']==0 for r in rows)


def test_valid_sku_matching_keeps_original_key_spelling(db,manifest):
    add(db,row(sku_id=' moving '))
    add(db,outside(month_dt='2024-06-01',product_id='exact-key-parent',sku_id=' moving '))
    add(db,outside(month_dt='2024-06-01',product_id='trimmed-key-parent',sku_id='moving',discount_sales=999))
    rows=results(db,manifest)
    assert len(rows)==2 and {r['product_id'] for r in rows}=={'exact-key-parent'}


def test_duplicate_anchor_members_never_amplify_source_rows_or_hide_title_variants(db,manifest):
    for _ in range(3):add(db,row())
    for _ in range(2):add(db,row(product_id='second-anchor-parent'))
    for _ in range(2):add(db,outside(month_dt='2024-06-01',product_id='new-parent',sku_title='One pack'))
    add(db,outside(month_dt='2024-06-01',product_id='new-parent',sku_title='Three pack',discount_price=30,discount_sales=30))
    add(db,outside(month_dt='2024-06-01',product_id='new-parent',product_title_cn='原始中文标题',sku_title='Child detail',discount_sales=40))
    jobs=question_jobs(manifest,unit(),'lineage')
    for job in jobs:
        rows=[]
        for offset in range(4):rows+=execute(db,manifest,paginated(job,1,offset))
        assert len(rows)==3 and sum(r['source_rows'] for r in rows)==4
        assert sum(r['amount'] for r in rows)==90
        assert all(r['total_rows']==3 for r in rows)
        assert len({canonical([r[k] for k in job['key_fields']]) for r in rows})==3
        assert {(r['title'],r['title_source']) for r in rows}=={
            ('One pack','sku_title'),('Three pack','sku_title'),('原始中文标题','product_title_cn')}
        assert all(r['product_title']=='' for r in rows)
        assert next(r for r in rows if r['title']=='One pack')['source_rows']==2
        assert 'LIMIT' not in job['sql'].upper() and 'MAX(' not in job['sql'].upper()


def test_empty_anchor_does_not_replace_itself_with_current_target_population(db,manifest):
    add(db,row(month_dt='2024-06-01'))
    assert results(db,manifest)==[]


def test_null_measurements_and_price_variants_remain_explicit(db,manifest):
    add(db,row())
    add(db,outside(month_dt='2024-06-01',product_id='new-parent',discount_sales=None,count=None,discount_price=None))
    rows=results(db,manifest)
    assert len(rows)==2
    assert all(r['amount'] is None and r['units'] is None and r['discount_price'] is None for r in rows)
    assert all(r['source_rows']==r['missing_amount_rows']==r['missing_units_rows']==r['invalid_value_rows']==1 for r in rows)


def test_anchor_and_return_scope_are_separate_and_month_shards_reuse_identical_selection(manifest):
    all_jobs=question_jobs(manifest,unit(background_months=['2024-05']),'lineage')
    june_jobs=question_jobs(manifest,unit(),'lineage')
    assert len(all_jobs)==4 and len(june_jobs)==2
    for job in all_jobs:
        assert verify_question_job(manifest,job)
        assert job['anchor_scope']['paths']==[TARGET] and job['anchor_scope']['months']==['2024-05']
        assert job['return_scope']['paths']==[[]]
        assert job['return_scope']['coverage_role']=='supplemental_identity'
        assert job['return_scope']['transitive_expansion'] is False
        assert job['scope_spec']['paths']==[TARGET]
    assert all(j in all_jobs for j in june_jobs)


@pytest.mark.parametrize('field,value',[
    ('anchor_scope',{'paths':[[]]}),('return_scope',{'coverage_role':'reconciliation'}),
    ('key_fields',['site','platform','month_dt']),('job_id','changed'),('sql','SELECT 1'),
])
def test_rebuild_rejects_changed_query_metadata_and_sql(manifest,field,value):
    job=question_jobs(manifest,unit(),'lineage')[0]
    changed=copy.deepcopy(job);changed[field]=value
    with pytest.raises(ValueError):verify_question_job(manifest,changed)


@pytest.mark.parametrize('field',['anchor_months','anchor_paths'])
def test_rebuild_rejects_deleted_anchor_definition(manifest,field):
    job=question_jobs(manifest,unit(),'lineage')[0]
    del job['scope_spec'][field]
    with pytest.raises(ValueError):verify_question_job(manifest,job)


@pytest.mark.parametrize('changes',[
    {'anchor_months':[]},{'anchor_months':['2024-13']},{'anchor_months':['2023-12']},
    {'anchor_months':'2024-05'},{'anchor_paths':[]},{'anchor_paths':[['a','b','c','d']]},
    {'anchor_paths':[{'path':TARGET}]},{'anchor_path_layer':'raw'},
    {'selection_mode':'same_month_subtree'},{'query_months':['2024-08']},
    {'platforms':['Other']},{'source_layers':['other']},{'paths':[]},
])
def test_lineage_requires_explicit_valid_frozen_selection(manifest,changes):
    with pytest.raises(ValueError):question_jobs(manifest,unit(**changes),'lineage')


@pytest.mark.parametrize('mode',['aggregate','cohorts','details'])
def test_existing_query_modes_ignore_new_optional_anchor_metadata(manifest,mode):
    ordinary=unit();ordinary.pop('anchor_months');ordinary.pop('anchor_paths')
    assert question_jobs(manifest,ordinary,mode)==question_jobs(manifest,unit(),mode)


@pytest.mark.parametrize('platforms',[['Taobao'],['Taobao','Tmall']])
def test_actual_native_complexity_validator_accepts_complete_paginated_lineage(manifest,platforms):
    # This integration regression uses the installed native validator, without
    # constructing a transport, opening a DB, or changing the configured limit.
    # Portable source-only environments may lack the optional native server.
    security=pytest.importorskip('doris_mcp_server.utils.security')
    sqlparse=pytest.importorskip('sqlparse')
    validator=security.SQLSecurityValidator({'max_query_complexity':100})
    scope=unit(platforms=platforms,background_months=['2024-05'],
               anchor_months=['2024-01','2024-02','2024-03','2024-04','2024-05'])
    jobs=question_jobs(manifest,scope,'lineage')
    assert len(jobs)==4
    for job in jobs:
        parsed=sqlparse.parse(paginated(job,5000,0))[0]
        result=asyncio.run(validator._check_query_complexity(parsed))
        assert result.is_valid,result.error_message
