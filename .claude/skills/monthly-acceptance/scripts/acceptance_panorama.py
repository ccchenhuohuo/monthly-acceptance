"""Complete facts and objective checks, independent of change candidates.

Partitions are reading units, never automatically investigation obligations.
All data comes from verified planned queries; optional annotations only aid reading.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import json
import math

from acceptance_core import Run, canonical, digest, read, write, months, shift, now
from acceptance_queries import plan
from acceptance_protocol import current, protocol

FAMILIES = ('completeness','duplication','identity','conservation','time_series','price_bands','historical_regression')


def enabled(value):
    m = getattr(value, 'manifest', value)
    return ((current(m) or tuple(int(x) for x in m.get('method_version','0.0').split('.')[:2]) >= (4,0))
            and m['policy'].get('question_workflow',{}).get('enabled',False))


def home(run): return run.path/'panorama'


def _p(value): return json.loads(value) if isinstance(value,str) else list(value or [])


def _close(a,b):
    return a is not None and b is not None and abs(float(a)-float(b)) <= max(.01,abs(float(b))*1e-8)


def _read(run):
    """Read every frozen baseline family; unavailable results stay unavailable."""
    jobs = plan(run.manifest)
    registered = read(run.path/'plan.json') if (run.path/'plan.json').exists() else {}
    if registered.get('jobs') != jobs or registered.get('hash') != digest(jobs):
        raise ValueError('全景查询计划与冻结应查范围不同')
    errors = run.verify_inputs()+run.verify_events()
    if errors: raise ValueError('全景来源完整性失败：'+canonical(errors))
    datasets, receipts = {}, {}
    for job in jobs:
        jid=job['job_id']; r=run.job_record(jid)
        receipt={'job':job,'state':'not_executed','row_count':None,'evidence_ids':[], 'errors':[]}
        try:
            from acceptance_validate import verify_job
            errors=verify_job(run,job)
            if errors:raise ValueError(canonical(errors))
            data=run.job_rows(jid)
            datasets[jid]=data
            receipt.update(state='success',row_count=len(data),evidence_ids=r['evidence_ids'],
                record_sha256=digest(r),origin=r.get('execution_origin','native_query'),
                started_at=r.get('started_at'),finished_at=r.get('finished_at'))
        except (ValueError,OSError,KeyError,TypeError) as exc:
            receipt.update(state='not_executed' if not r else 'query_failed',errors=[str(exc)])
        receipts[jid]=receipt
    return jobs,datasets,receipts


def _constraint(kind,site,mm,scope,refs,facts,state='passed',family=None):
    family=family or {'arrival':'completeness','platform_arrival':'completeness','mapping':'duplication',
        'valid_identity':'identity','valid_values':'conservation','band_conservation':'price_bands',
        'hierarchy_conservation':'conservation','movement_conservation':'conservation','contract':'completeness'}[kind]
    key=[kind,site,mm,scope]
    return {'check_id':'check-'+digest(key)[:24], 'rule':kind,'family':family,'site':site,'month':mm,
        'scope':scope,'state':state,'source_jobs':sorted(set(refs)),'facts':facts,
        'requires_resolution':state=='failed'}


def derive(run,jobs,data,receipts):
    """Pure fact/constraint derivation. Never reads candidates.json."""
    cats={}; bands=defaultdict(list); movement={}; raw={}; fp={}; mapping={}; platform={}; checks=[]
    by_family=defaultdict(list)
    for j in jobs:
        jid=j['job_id'];by_family[(j['site'],j['family'])].append(jid)
        for n,row in enumerate(data.get(jid,[])):
            mm=str(row.get('month_dt',''))[:7];ref={'job_id':jid,'row_index':n,'record_sha256':digest(row)}
            family=j['family'];site=j['site']
            if family=='category':cats[(site,canonical(_p(row['path'])),mm)]=(row,ref)
            elif family=='bands':bands[(site,canonical(_p(row['path'])),mm)].append((row,ref))
            elif family=='movement':movement[(site,canonical(_p(row['path'])),mm,j['lag'],j['entity'])]=(row,ref)
            elif family=='raw_paths':raw[(site,canonical(_p(row['path'])),mm)]=(row,ref)
            elif family=='fingerprint':fp[(site,j['entity'],mm)]=(row,ref)
            elif family=='mapping':mapping[(site,mm)]=(row,ref)
            elif family=='platform':platform[(site,j['entity'],str(row.get('platform')),mm)]=(row,ref)
    children=defaultdict(list); movement_refs=defaultdict(set)
    for (site,path,mm),v in cats.items():
        parts=_p(path)
        if parts:children[(site,canonical(parts[:-1]),mm)].append(v)
    for key,v in movement.items():movement_refs[key[:3]].add(v[1]["job_id"])
    facts=[]
    for site in run.manifest['sites']:
        grid=months(run.manifest['starts'][site],run.manifest['data_month'])
        for layer in ('raw','std'):
            js=[j['job_id'] for j in jobs if j['family']=='fingerprint' and j['site']==site and j['entity']==layer]
            for mm in grid:
                pair=fp.get((site,layer,mm));available=all(receipts[j]['state']=='success' for j in js)
                checks.append(_constraint('arrival',site,mm,{'layer':layer},js,{'observed':pair is not None},
                    'unavailable' if not available else 'passed' if pair else 'failed'))
                if pair:
                    r=pair[0]
                    for rule,field in [('valid_identity','invalid_keys'),('valid_values','invalid_rows')]:
                        checks.append(_constraint(rule,site,mm,{'layer':layer},js,{field:r.get(field)},
                            'unavailable' if r.get(field) is None else 'failed' if r[field] else 'passed'))
        for mm in grid:
            pair=mapping.get((site,mm));js=by_family[(site,'mapping')]
            fields=('raw_duplicate_rows','std_duplicate_rows','std_only_keys','value_mismatch_keys')
            bad={f:pair[0].get(f) for f in fields if pair and pair[0].get(f)}
            checks.append(_constraint('mapping',site,mm,{},[pair[1]['job_id']] if pair else js,
                {'violations':bad,'raw_only_keys':pair[0].get('raw_only_keys') if pair else None,
                 'raw_only_requires_retained_scope':True},'failed' if bad else 'passed' if pair else 'unavailable'))
        for path in sorted({p for s,p,m in cats if s==site}):
            for mm in grid:
                pair=cats.get((site,path,mm));r=pair[0] if pair else None
                movements={str(lag)+':'+entity:movement[(site,path,mm,lag,entity)][0]
                    for lag in run.policy['comparisons'].values() for entity in ('spu','brand')
                    if (site,path,mm,lag,entity) in movement and shift(mm,-lag)>=run.manifest['starts'][site]}
                fact={'site':site,'path':_p(path),'level':len(_p(path)),'month':mm,
                    'observation':'observed' if r else 'unobserved','values':r,
                    'bands':[x[0] for x in bands.get((site,path,mm),[])], 'movements':movements,
                    'source':pair[1] if pair else None,
                    'source_jobs':sorted({x[1]['job_id'] for x in bands.get((site,path,mm),[])}|
                        ({pair[1]['job_id']} if pair else set())|
                        movement_refs[(site,path,mm)])}
                facts.append(fact)
                if not r:continue
                unknown=r.get('invalid_rows') is None or r.get('missing_product_rows') is None
                checks.append(_constraint('valid_values',site,mm,{'path':_p(path)},[pair[1]['job_id']],
                    {k:r.get(k) for k in ('invalid_rows','missing_product_rows')},'unavailable' if unknown else 'failed' if r['invalid_rows'] or r['missing_product_rows'] else 'passed'))
                br=bands.get((site,path,mm),[])
                sums={k:sum(float(x[0][k]) for x in br if x[0].get(k) is not None) for k in ('spus','units','amount')}
                complete=bool(br) and all(x[0].get(k) is not None for x in br for k in ('spus','units','amount'))
                checks.append(_constraint('band_conservation',site,mm,{'path':_p(path)},fact['source_jobs'],
                    {'category':{k:r.get(k) for k in sums},'band_sum':sums},
                    'unavailable' if not complete else 'passed' if all((_close(sums[k],r.get(k)) if k=='amount' else sums[k]==r.get(k)) for k in sums) else 'failed'))
                child=children[(site,path,mm)]
                if len(_p(path))<3:
                    sums={k:sum(float(x[0][k]) for x in child if x[0].get(k) is not None) for k in ('units','amount')}
                    checks.append(_constraint('hierarchy_conservation',site,mm,{'path':_p(path)},
                        [pair[1]['job_id']]+[x[1]['job_id'] for x in child],
                        {'parent':{k:r.get(k) for k in sums},'children_sum':sums,'spu_addition_forbidden':True},
                        'unavailable' if not child else 'passed' if all((_close(sums[k],r.get(k)) if k=='amount' else sums[k]==r.get(k)) for k in sums) else 'failed'))
                for name,mv in movements.items():
                    lag=int(name.split(':')[0]);b=cats.get((site,path,shift(mm,-lag)))
                    if not b:continue
                    matches=all(_close(mv.get(side+'_'+metric),record.get(metric))
                        for side,record in [('base',b[0]),('current',r)] for metric in ('units','amount'))
                    checks.append(_constraint('movement_conservation',site,mm,{'path':_p(path),'comparison':name},
                        fact['source_jobs']+[b[1]['job_id']],{'comparison':mv},'passed' if matches else 'failed'))
        expected=run.policy.get('expected_platforms',{}).get(site,[])
        js=by_family[(site,'platform')]
        for layer in ('raw','std'):
            layer_jobs=[j['job_id'] for j in jobs if j['family']=='platform' and j['site']==site and j['entity']==layer]
            for platform_id in expected:
                for mm in grid:
                    pair=platform.get((site,layer,platform_id,mm))
                    complete=bool(layer_jobs) and all(receipts[x]['state']=='success' for x in layer_jobs)
                    checks.append(_constraint('platform_arrival',site,mm,{'layer':layer,'platform':platform_id},layer_jobs,
                        {'observed':pair is not None},'unavailable' if not complete else 'passed' if pair else 'failed'))
        observed_platforms={pl for s,layer,pl,mm in platform if s==site}
        if observed_platforms-set(expected):
            checks.append(_constraint('platform_arrival',site,run.manifest['data_month'],{'unexpected_platforms':sorted(observed_platforms-set(expected))},js,{'expected':expected},'failed'))
        if not expected:
            checks.append(_constraint('platform_arrival',site,run.manifest['data_month'],{'expected_platforms':[]},js,
                {'missing':'frozen expected platform set'},'unavailable'))
    # Authoritative effective contracts, not the set of already observed paths.
    contract_file=run.path/'inputs/project/scope-contract.json'
    contracts=read(contract_file).get('entries',[]) if contract_file.exists() else []
    for entry in contracts:
        site=entry['site']
        if site not in run.manifest['sites']:continue
        prefix=entry.get('path_prefix',[]);start=entry.get('effective_from');end=entry.get('effective_to') or run.manifest['data_month']
        for mm in months(run.manifest['starts'][site],run.manifest['data_month']):
            js=by_family[(site,'raw_paths')];complete=bool(js) and all(receipts[j]['state']=='success' for j in js)
            observed=any(s==site and m==mm and _p(p)[:len(prefix)]==prefix for s,p,m in raw)
            state='unavailable' if not start or not complete else 'not_applicable' if not start<=mm<=end else 'passed' if observed else 'failed'
            checks.append(_constraint('contract',site,mm,{'contract_id':entry['id'],'path_prefix':prefix},js,
                {'observed':observed,'effective_from':start,'effective_to':end},state))
    for site in run.manifest['sites']:
        if contract_file.exists() and not read(contract_file).get('complete_leaf_list'):
            checks.append(_constraint('contract',site,run.manifest['data_month'],{'contract_id':'complete_leaf_list'},[],{'missing':'complete authoritative expected leaf list'},'unavailable'))
        if not any(e['site']==site for e in contracts):
            checks.append(_constraint('contract',site,run.manifest['data_month'],{'contract_id':None},[],
                {'missing':'authoritative category contract; never-observed leaf nodes not enumerable'},'unavailable'))
    raw_facts=[{'site':s,'path':_p(p),'month':m,'values':v[0],'source':v[1]} for (s,p,m),v in sorted(raw.items())]
    platform_facts=[{'site':s,'layer':layer,'platform':pl,'month':mm,'values':v[0],'source':v[1]} for (s,layer,pl,mm),v in sorted(platform.items())]
    return {'facts':facts,'raw_paths':raw_facts,'platforms':platform_facts,'checks':checks,
        'country_layers':[{'site':s,'layer':layer,'month':mm,'values':v[0],'source':v[1]} for (s,layer,mm),v in sorted(fp.items())]}


def build(run):
    run.writable();jobs,data,receipts=_read(run);derived=derive(run,jobs,data,receipts)
    binding={'inputs_sha256':digest(run.manifest['inputs']),'policy_sha256':run.manifest['policy_hash'],
        'jobs':{jid:{k:r.get(k) for k in ('state','record_sha256')} for jid,r in receipts.items()}}
    partitions=[]
    for site in run.manifest['sites']:
        for family in FAMILIES:
            relevant=[c for c in derived['checks'] if c['site']==site and c['family']==family]
            # References into one fact archive, no duplication of large evidence ledgers.
            part={'partition_id':site+':'+family,'site':site,'family':family,
                'months':months(run.manifest['starts'][site],run.manifest['data_month']),
                'paths':sorted({canonical(f['path']) for f in derived['facts'] if f['site']==site}),
                'check_count':len(relevant),'checks_file':'panorama/constraint-checks.jsonl', 'check_states':dict(Counter(c['state'] for c in relevant)),
                'reading_status':'unread','facts_file':'panorama/facts-'+site+'.json',
                'raw_paths_file':'panorama/raw-paths-'+site+'.json','platforms_file':'panorama/platforms-'+site+'.json',
                'source_binding':digest(binding),
                'limitations':['Historical judgments are revealed after independent discovery; regressions tracked separately.'] if family=='historical_regression' else []}
            partitions.append(part)
        for field,name in [('facts','facts'),('raw_paths','raw-paths'),('platforms','platforms'),('country_layers','country-layers')]:
            write(home(run)/(name+'-'+site+'.json'),[v for v in derived[field] if v['site']==site])
    check_file=home(run)/'constraint-checks.jsonl'
    temporary=check_file.with_suffix('.jsonl.tmp')
    with temporary.open('w') as out:
        for c in derived['checks']:out.write(canonical(c)+'\n')
    temporary.replace(check_file)
    index={'protocol':protocol(run,'4.0'),'run_id':run.manifest['run_id'],'binding':binding,'binding_sha256':digest(binding),
        'baseline_jobs':receipts,'partitions':partitions,
        'constraints':[c for c in derived['checks'] if c['state'] in ('failed','unavailable')],
        'constraint_states':dict(Counter(c['state'] for c in derived['checks'])),
        'constraint_count':len(derived['checks']),'constraint_file':'panorama/constraint-checks.jsonl',
        'fact_cells':len(derived['facts']),'observed_cells':sum(f['observation']=='observed' for f in derived['facts']),
        'ordinary_observations_create_obligations':False,
        'source_origins':dict(Counter(r.get('origin','unavailable') for r in receipts.values())),
        'limitations':['Aggregate conservation does not establish real sales or product identity.',
            'Unobserved paths require effective scope and identity evidence; raw-only keys are not automatically loss.',
            'Platform and snapshots absent from the saved sources remain coverage gaps.']}
    index['derived_files']={str(p.relative_to(run.path)):digest(p.read_bytes()) for p in home(run).glob('*.json') if p.name.startswith(('facts-','raw-paths-','platforms-','country-layers-'))}
    index['derived_files'][str(check_file.relative_to(run.path))]=digest(check_file.read_bytes())
    write(home(run)/'index.json',index)
    run.event('panorama.built',binding_sha256=index['binding_sha256'],index_sha256=digest(index),
        fact_cells=index['fact_cells'],partitions=len(partitions),constraints=index['constraint_count'])
    return index


def load(run,verify=True):
    value=read(home(run)/'index.json')
    events=[json.loads(x) for x in run._event_snapshot().splitlines() if json.loads(x)['type']=='panorama.built']
    if not events or digest(value)!=events[-1]['index_sha256']:raise ValueError('全景索引缺版本绑定或已改变')
    if verify:
        jobs,data,receipts=_read(run)
        binding={'inputs_sha256':digest(run.manifest['inputs']),'policy_sha256':run.manifest['policy_hash'],
            'jobs':{jid:{k:r.get(k) for k in ('state','record_sha256')} for jid,r in receipts.items()}}
        if digest(binding)!=value['binding_sha256']:raise ValueError('全景证据版本或完整性改变，须重新生成并判读受影响分区')
        for rel,hashed in value['derived_files'].items():
            if digest((run.path/rel).read_bytes())!=hashed:raise ValueError('全景派生事实变更：'+rel)
    return value


def resolve(run,ref,*,index=None,rows_cache=None):
    """Locate an exact original row of a complete verified query."""
    index=index or load(run,False);jid=ref.get('job_id')
    if jid not in index['baseline_jobs']:raise ValueError('引用非本全景原始查询')
    cache=rows_cache if rows_cache is not None else {}
    if jid not in cache:cache[jid]=run.job_rows(jid)
    rows=cache[jid];n=ref.get('row_index')
    if type(n)!=int or not 0<=n<len(rows) or digest(rows[n])!=ref.get('record_sha256'):
        raise ValueError('全景原始行定位/哈希错误')
    return rows[n]


def overview(run):
    index=load(run)
    return {'binding_sha256':index['binding_sha256'],'fact_cells':index['fact_cells'],
        'observed_cells':index['observed_cells'],'partitions':index['partitions'],
        'constraint_states':index.get('constraint_states',dict(Counter(c['state'] for c in index['constraints']))),
        'failed_constraints':[c for c in index['constraints'] if c['state']=='failed'],
        'unavailable_checks':[c for c in index['constraints'] if c['state']=='unavailable'],
        'source_origins':index['source_origins']}
