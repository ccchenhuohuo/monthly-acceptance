"""Rebuild obligations and independent candidates from complete recorded query populations."""
from collections import defaultdict
from acceptance_core import business_key, canonical, digest, months, read, shift, write
from acceptance_queries import plan


def number(v):return float(v) if v is not None else None

def delta(b,c):
    if b is None or c is None:return None
    return float(c)-float(b)

def rate(b,c):
    if b is None or c is None:return None
    if float(b)==0:return 0.0 if float(c)==0 else None
    return (float(c)-float(b))/abs(float(b))

def changed(b,c,threshold):
    r=rate(b,c)
    return r is not None and abs(r)>=threshold or b==0 and c not in (None,0)

def ratio(a,b):return a/abs(b) if b else None


def rebuild(run):
    m=run.manifest;pol=run.policy;jobs=plan(m);datasets=[];sources={};errors=[]
    for j in jobs:
        try:
            rows=run.job_rows(j['job_id']);datasets.append((j,rows))
            sources[j['job_id']]=run.job_record(j['job_id'])['evidence_ids']
        except Exception as exc:errors.append({'job_id':j['job_id'],'family':j['family'],'site':j['site'],'reason':str(exc)})
    if errors:raise ValueError('基础任务未完整成功：'+canonical(errors))
    cats={};bands=defaultdict(dict);mov={};raw=defaultdict(dict);checks=[];candidates={};sample_rows=[]
    def add(kind,site,path,month,comparison,facts,source_jobs,priority='normal',level=None):
        cid='cand-'+business_key(kind,site,path,month,comparison)
        candidates[cid]={'candidate_id':cid,'kind':kind,'site':site,'level':level,'path':path,'month':month,
            'comparison':comparison,'priority':priority,'facts':facts,'source_jobs':sorted(set(source_jobs)),
            'evidence_ids':sorted({e for j in source_jobs for e in sources[j]}),'status':'open'}
    for j,rows in datasets:
        f=j['family'];site=j['site'];jid=j['job_id']
        for r in rows:
            if f=='category':cats[(site,j['level'],r['path'],r['month_dt'][:7])]=(r,jid)
            elif f=='bands':bands[(site,j['level'],r['path'],r['month_dt'][:7])][int(r['band'])]=(r,jid)
            elif f=='movement':mov[(site,j['level'],r['path'],r['month_dt'][:7],j['lag'],j['entity'])]=(r,jid)
            elif f=='raw_paths':raw[(site,r['path'])][r['month_dt'][:7]]=(r,jid)
            elif f=='mapping':
                bad={k:r[k] for k in ['raw_duplicate_rows','std_duplicate_rows','std_only_keys','value_mismatch_keys'] if r.get(k)}
                checks.append({'check_id':business_key('mapping',site,r['month_dt']),'family':'mapping','site':site,'month':r['month_dt'][:7],
                    'state':'signal' if bad else 'checked','facts':r,'source_jobs':[jid]})
                if bad:add('identity_mapping',site,'[]',r['month_dt'][:7],None,bad,[jid],'high',0)
            elif f=='fingerprint':
                if r.get('invalid_keys') or r.get('invalid_rows'):
                    add('invalid_values_'+j['entity'],site,'[]',r['month_dt'][:7],None,r,[jid],'high',0)
            elif f=='sample':
                sample_rows.append({'sample_id':'sample-'+business_key(site,r['path'],r['product_id']),'site':site,'month':m['data_month'],
                    **r,'source_jobs':[jid],'evidence_ids':sources[jid]})
    for j,rows in datasets:
        if j['family']=='fingerprint':
            observed={r['month_dt'][:7] for r in rows}
            for mm in months(m['starts'][j['site']],m['data_month']):
                ok=mm in observed
                checks.append({'check_id':business_key('arrival',j['site'],j['entity'],mm),'family':'arrival','site':j['site'],'month':mm,'state':'observed' if ok else 'no_observation','source_jobs':[j['job_id']]})
                if not ok:add('missing_country_month_'+j['entity'],j['site'],'[]',mm,None,{'expected_month':mm},[j['job_id']],'high',0)
    axes=sorted({k[:3] for k in cats})
    for site,level,path in axes:
        existing=sorted(k[3] for k in cats if k[:3]==(site,level,path));first,last=existing[0],existing[-1]
        for mm in months(m['starts'][site],m['data_month']):
            key=(site,level,path,mm);current=cats.get(key)
            checks.append({'check_id':business_key('result',*key),'family':'four_results','site':site,'level':level,'path':path,'month':mm,
                'state':'observed' if current else 'no_observation','source_jobs':[current[1]] if current else []})
            if not current:
                if mm>=first:
                    add('std_presence_gap',site,path,mm,None,{'last_observed':last,'first_observed':first,'meaning':'无观测；迁移/约定有效期尚需核对'},[cats[(site,level,path,last)][1]],'high' if mm==m['data_month'] else 'normal',level)
                continue
            c,cj=current;bdata=bands.get(key,{})
            for metric in ['spus','units','amount']:
                values=[r[metric] for r,_ in bdata.values()]
                observed_total=sum(float(v) for v in values if v is not None)
                expected=c[metric]
                tolerance=max(pol['amount_tolerance'],abs(float(expected or 0))*1e-9) if metric=='amount' else 0
                if expected is not None and abs(observed_total-float(expected))>tolerance:
                    add('band_conservation',site,path,mm,None,{'metric':metric,'category':expected,'band_sum':observed_total},[cj]+[jj for _,jj in bdata.values()],'high',level)
            if c.get('missing_product_rows') or c.get('invalid_rows'):
                add('invalid_category_values',site,path,mm,None,{'missing_product_rows':c.get('missing_product_rows'),'invalid_rows':c.get('invalid_rows')},[cj],'high',level)
            for comparison,lag in pol['comparisons'].items():
                bm=shift(mm,-lag);base=cats.get((site,level,path,bm));check={'check_id':business_key('comparison',*key,comparison),
                    'family':comparison,'site':site,'level':level,'path':path,'month':mm,'base_month':bm,
                    'state':'comparable' if base else ('outside_window' if bm<m['starts'][site] else 'no_baseline'),
                    'source_jobs':sorted({cj,base[1]}) if base else [cj]}
                checks.append(check)
                if not base:continue
                b,bj=base;signals=[];facts={'base_month':bm,'current_month':mm,'metrics':{}}
                for metric,threshold in [('spus','spu_change'),('units','units_change'),('amount','amount_change')]:
                    facts['metrics'][metric]={'base':b[metric],'current':c[metric],'delta':delta(b[metric],c[metric]),'rate':rate(b[metric],c[metric])}
                    if changed(b[metric],c[metric],pol['signals'][threshold]):signals.append(metric+'_change')
                bbands=bands.get((site,level,path,bm),{});shifts=[];sj=[cj,bj]
                for band in sorted(set(bbands)|set(bdata)):
                    br,brj=bbands.get(band,({'spus':0},None));cr,crj=bdata.get(band,({'spus':0},None))
                    pp=100*(float(cr['spus'])/float(c['spus'])-float(br['spus'])/float(b['spus'])) if b['spus'] and c['spus'] else None
                    if pp is not None and abs(pp)>=pol['signals']['band_share_pp']:shifts.append({'band':band,'base_spus':br['spus'],'current_spus':cr['spus'],'share_change_pp':pp})
                    sj.extend(v for v in [brj,crj] if v)
                if shifts:signals.append('price_band_shift');facts['band_shifts']=shifts
                for entity in ['spu','brand']:
                    mr=mov.get((site,level,path,mm,lag,entity))
                    if not mr:
                        raise ValueError('完整贡献任务缺少可比单元：'+canonical([site,level,path,mm,lag,entity]))
                    v,mj=mr;sj.append(mj);movement={}
                    for metric in ['units','amount']:
                        positive=float(v['positive_'+metric] or 0);negative=float(v['negative_'+metric] or 0)
                        gross=positive-negative;net=positive+negative
                        cancellation=1-abs(net)/gross if gross else 0
                        gross_ratio=ratio(gross,float(v['base_'+metric] or 0))
                        movement[metric]={'positive':positive,'negative':negative,'gross':gross,'cancellation':cancellation,'gross_to_base':gross_ratio}
                        if gross_ratio is not None and gross_ratio>=pol['signals']['gross_change_ratio'] and cancellation>=pol['signals']['cancellation_ratio']:
                            signals.append(entity+'_'+metric+'_offset')
                        for side,cat in [('base',b),('current',c)]:
                            actual=v[side+'_'+metric];expected=cat[metric]
                            if actual is not None and expected is not None and abs(float(actual)-float(expected))>max(.01,abs(float(expected))*1e-8):
                                raise ValueError('贡献与类目总量不守恒：'+canonical([site,path,mm,entity,metric,side]))
                    union=float(v['base_entities'] or 0)+float(v['entered'] or 0)
                    churn=(float(v['entered'] or 0)+float(v['exited'] or 0))/union if union else 0
                    movement['churn']={'entered':v['entered'],'exited':v['exited'],'base_entities':v['base_entities'],'current_entities':v['current_entities'],'ratio':churn}
                    if churn>=pol['signals']['churn_ratio']:signals.append(entity+'_churn')
                    facts[entity+'_movement']=movement
                if signals:
                    facts['signals']=sorted(set(signals));country=cats.get((site,0,'[]',mm));exposure={}
                    if country:
                        for metric in ['spus','units','amount']:exposure[metric]=ratio(float(c[metric] or 0),float(country[0][metric] or 0))
                    facts['country_exposure']=exposure
                    priority='high' if level==0 or max([abs(v) for v in exposure.values() if v is not None]+[0])>=pol['investigation']['parent_exposure'] else 'normal'
                    add('result_change',site,path,mm,comparison,facts,sj,priority,level)
    for (site,path),observed in sorted(raw.items()):
        first=min(observed);last=max(observed)
        for mm in months(first,m['data_month']):
            checks.append({'check_id':business_key('raw_presence',site,path,mm),'family':'raw_presence','site':site,'path':path,'month':mm,
                'state':'observed' if mm in observed else 'no_observation','source_jobs':[observed[last][1]]})
            if mm not in observed:
                add('raw_presence_gap',site,path,mm,None,{'first_observed':first,'last_observed':last,'meaning':'已观测原始路径的缺口，不能直接解释为停采'},[observed[last][1]],'high' if mm==m['data_month'] else 'normal')
    contract_path=run.path/'inputs/project/scope-contract.json'
    if contract_path.exists():
        import json
        contract=read(contract_path)
        for entry in contract['entries']:
            site=entry['site']
            if site not in m['sites']:continue
            prefix=entry['path_prefix'];start=entry.get('effective_from');end=entry.get('effective_to') or m['data_month']
            if not isinstance(prefix,list) or not prefix:raise ValueError('约定路径必须为非空前缀数组')
            matched={}
            for (rs,rpath),observed in raw.items():
                if rs==site and json.loads(rpath)[:len(prefix)]==prefix:
                    for mm,pair in observed.items():matched.setdefault(mm,[]).append(pair)
            for mm in months(m['starts'][site],m['data_month']):
                state='effective_period_unknown' if not start else ('outside_contract_period' if not start<=mm<=end else ('observed' if mm in matched else 'no_observation'))
                cj=[j['job_id'] for j in jobs if j['family']=='raw_paths' and j['site']==site]
                checks.append({'check_id':business_key('contract',site,entry['id'],mm),'family':'contract','site':site,'path':canonical(prefix),'month':mm,'state':state,'source_jobs':cj})
                if state=='no_observation':add('contract_scope_missing',site,canonical(prefix),mm,None,{'contract_entry':entry['id'],'effective_from':start,'effective_to':end,'source':contract.get('source'),'meaning':'约定有效期内未观测到该原始前缀，需核实归属/更名与实际交付'},cj,'high')
    candidate_list=sorted(candidates.values(),key=lambda c:c['candidate_id'])
    cases=defaultdict(list)
    for c in candidate_list:
        import json
        p=json.loads(c['path']);root=p[0] if p else '国家总量'
        group='原始范围' if c['kind'] in ['raw_presence_gap','contract_scope_missing'] else ('身份与数值' if c['kind'].startswith(('identity','invalid','missing_country','band_conservation')) else str(root))
        cases[(c['site'],group)].append(c)
    case_list=[]
    for (site,group),cs in sorted(cases.items()):
        case_list.append({'case_id':'case-'+business_key(site,group),'site':site,'group':group,'candidate_ids':[c['candidate_id'] for c in cs],
            'candidate_count':len(cs),'high_count':sum(c['priority']=='high' for c in cs),'months':sorted({c['month'] for c in cs}),
            'kinds':sorted({c['kind'] for c in cs}),'source_jobs':sorted({j for c in cs for j in c['source_jobs']})})
    return {'checks':sorted(checks,key=lambda c:c['check_id']),'candidates':candidate_list,'cases':case_list,'samples':sorted(sample_rows,key=lambda s:s['sample_id'])}


def analyze(run):
    import acceptance_panorama as panorama
    if panorama.enabled(run):
        v=panorama.build(run)
        return {k:v[k] for k in ("fact_cells","observed_cells","source_origins")}
    value=rebuild(run)
    for name,rows in value.items():
        target=run.path/(name+'.json')
        if target.exists() and read(target)!=rows:raise ValueError('独立观察已冻结，重建结果不一致：'+name)
        write(target,rows)
    run.event('independent_scan.frozen',counts={k:len(v) for k,v in value.items()},candidate_hash=digest(value['candidates']))
    # v3.2 keeps historical judgments behind an explicit concrete-discovery
    # freeze. A path-level trigger is never reported as historical recall.
    from acceptance_coverage import enabled as coverage_enabled
    if coverage_enabled(run):
        return {k:len(v) for k,v in value.items()}
    # Open historical issues are joined only after the current independent pool has been frozen.
    source=run.path/'inputs/project/open-issues.json'
    historical=[]
    if source.exists():
        for issue in read(source):
            if issue.get('status')=='closed':continue
            historical.append({**issue,'review_state':'pending','independent_candidate_ids':[
                c['candidate_id'] for c in value['candidates'] if c['site']==issue.get('site') and c['path']==issue.get('path') and (not issue.get('kind') or c['kind']==issue['kind'])]})
    write(run.path/'historical-recheck.json',{'source_available':source.exists(),'items':historical,
        'note':'未提供开放问题清单时，历史已知问题召回率未知；旧报告不自动转为事实'})
    return {k:len(v) for k,v in value.items()}
