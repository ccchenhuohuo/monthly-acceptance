"""Run generated panorama SQL on adversarial cross-platform records offline."""
import copy
import hashlib
import re

import pytest
from test_question_identity import db, manifest, row, add, execute
from acceptance_queries import query, job, paginated


class Percentile:
    def __init__(self): self.values=[]; self.p=.5
    def step(self,value,p):
        if value is not None: self.values.append(value)
        self.p=p
    def finalize(self):
        if not self.values: return None
        return sorted(self.values)[int((len(self.values)-1)*self.p)]


def run(db,m,family,**args):
    db.create_aggregate('PERCENTILE_APPROX',2,Percentile)
    db.create_function('MD5',1,lambda x:hashlib.md5(x.encode()).hexdigest())
    db.create_function('CONCAT',-1,lambda *xs:''.join(str(x) for x in xs))
    db.create_function('CONCAT_WS',-1,lambda sep,*xs:sep.join(str(x) for x in xs if x is not None))
    db.create_function('GREATEST',2,max); db.create_function('LEAST',2,min)
    sql=query(m,family,'cn',**args)
    sql=re.sub(r'DATE_ADD\(CAST\(b.month_dt AS DATE\),INTERVAL (\d+) MONTH\)',r"DATE(b.month_dt,'+\1 months')",sql)
    sql=sql.replace('CAST(c.month_dt AS DATE)','DATE(c.month_dt)')
    return execute(db,m,sql)


def current(manifest):
    m=copy.deepcopy(manifest); m['method_version']='0.0.1'; m['architecture']='panorama_questions'; m['data_month']='2024-06'
    m['policy']['price_bands']['cn']=[20,50]; m['policy']['sample_per_category']=100
    return m


def test_same_id_two_platforms_keep_price_and_spu_independent(db,manifest):
    m=current(manifest)
    add(db,row(month_dt='2024-06-01',product_id='shared',sku_id=None,discount_sales=10))
    add(db,row(month_dt='2024-06-01',platform='Tmall',product_id='shared',sku_id=None,discount_sales=90))
    for family,args in [('fingerprint',{'entity':'std'}),('fingerprint',{'entity':'raw'}),('raw_paths',{}),('category',{'level':3})]:
        result=run(db,m,family,**args)[0]
        assert result['spus']==2, (family,result)
        assert result['amount']==100 and result['units']==2
    bands=run(db,m,'bands',level=3)
    assert {(r['band'],r['spus'],r['amount']) for r in bands}=={(0,1,10),(2,1,90)}
    samples=run(db,m,'sample')
    assert {(r['platform'],r['product_id'],r['amount']) for r in samples}=={('Taobao','shared',10),('Tmall','shared',90)}
    assert job(m,'sample','cn')['key_fields']==['path','platform','product_id']
    j=job(m,'sample','cn')
    pages=[execute(db,m,paginated(j,1,i))[0] for i in range(2)]
    assert {r['platform'] for r in pages}=={'Taobao','Tmall'}


def test_movement_does_not_stitch_platforms_but_brand_stays_site_wide(db,manifest):
    m=current(manifest)
    add(db,row(product_id='shared',sku_id=None,discount_sales=10))
    add(db,row(month_dt='2024-06-01',platform='Tmall',product_id='shared',sku_id=None,discount_sales=90))
    r=next(r for r in run(db,m,'movement',level=3,lag=1,entity='spu',year=2024) if r['month_dt']=='2024-06-01')
    assert (r['entered'],r['exited'],r['positive_amount'],r['negative_amount'])==(1,1,90,-10)
    b=next(r for r in run(db,m,'movement',level=3,lag=1,entity='brand',year=2024) if r['month_dt']=='2024-06-01')
    assert (b['entered'],b['exited'],b['positive_amount'],b['negative_amount'])==(0,0,80,0)


def test_delimiters_and_placeholder_skus_do_not_collide(db,manifest):
    m=current(manifest)
    for product,sku in [('a|b','c'),('a','b|c'),('other',None),('other',''),('other','0')]:
        add(db,row(month_dt='2024-06-01',product_id=product,sku_id=sku))
    p=run(db,m,'platform',entity='std')[0]
    assert (p['spus'],p['effective_skus'],p['placeholder_sku_rows'])==(3,2,3)
    add(db,row(month_dt='2024-06-01',platform='Taobao|a',product_id='b',sku_id=None))
    assert run(db,m,'fingerprint',entity='std')[0]['spus']==4
    assert run(db,m,'category',level=3)[0]['spus']==4


def test_missing_parent_rows_remain_unpriced_without_cross_platform_movement(db,manifest):
    m=current(manifest)
    add(db,row(product_id=None,sku_id=None))
    add(db,row(month_dt='2024-06-01',platform='Tmall',product_id=None,sku_id=None,discount_sales=90))
    c=run(db,m,'category',level=3)
    assert all(r['spus']==0 and r['priced_spus']==0 and r['missing_product_rows']==1 for r in c)
    assert sum(r['amount'] for r in c)==100
    r=next(r for r in run(db,m,'movement',level=3,lag=1,entity='spu',year=2024) if r['month_dt']=='2024-06-01')
    assert (r['entered'],r['exited'],r['positive_amount'],r['negative_amount'])==(1,1,90,-10)


@pytest.mark.parametrize('version',['4.0.0','4.0.1'])
def test_frozen_versions_retain_historical_aggregation(db,manifest,version):
    m=current(manifest); m['method_version']=version
    m.pop('architecture'); m['policy'].pop('architecture'); m['policy']['version']=version
    add(db,row(month_dt='2024-06-01',product_id='shared',discount_sales=10))
    add(db,row(month_dt='2024-06-01',platform='Tmall',product_id='shared',discount_sales=90))
    assert run(db,m,'fingerprint',entity='std')[0]['spus']==1
    assert run(db,m,'category',level=3)[0]['spus']==1
    assert len(run(db,m,'sample'))==1
    assert job(m,'sample','cn')['key_fields']==['path','product_id']
