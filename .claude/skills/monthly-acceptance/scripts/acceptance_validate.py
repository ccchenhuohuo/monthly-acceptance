"""Independent structural/coverage validation; never treats a sealed file bundle as acceptance."""
import json
from collections import Counter
from pathlib import Path
from acceptance_core import canonical, digest, merge, read, write, event_snapshot
from acceptance_queries import paginated, plan
from acceptance_analysis import rebuild


def verify_job(run,j,record=None):
    def normalized_sql(value):
        # Only whitespace outside SQL strings/quoted identifiers is formatting.
        # Collapsing a literal changes category paths such as 'Audio  Accessories'.
        import re
        if not isinstance(value,str):raise ValueError('数据库实际 SQL 不是字符串')
        value=value.strip()
        if value.endswith(';'):value=value[:-1].rstrip()
        literal=r'''('(?:\\.|''|[^'\\])*'|"(?:\\.|""|[^"\\])*"|`(?:\\.|``|[^`\\])*`)'''
        parts=re.split(literal,value)
        for i in range(0,len(parts),2):
            if any(q in parts[i] for q in ("'",'"','`')):raise ValueError('数据库实际 SQL 引号未闭合')
            parts[i]=re.sub(r'[ \t\r\n\f\v]+',' ',parts[i])
        return ''.join(parts)
    errors=[];record=run.job_record(j['job_id']) if record is None else record
    if not record or record.get('execution_status')!='success':return ['missing_or_failed_job:'+j['job_id']]
    try:
        if record['sql_hash']!=digest(j['sql']) or record.get('key_fields')!=j['key_fields']:raise ValueError('任务 SQL 或键变更')
        if 'attempt_id' in record:
            from acceptance_job_versions import verify_execution
            verify_execution(run,record,j)
        rows=run.job_rows_from_record(record,j['job_id']);offset=0
        for eid in record['evidence_ids']:
            value=run.get_evidence(eid);req=value['request'];response=value['response'];size=run.policy['page_size']
            sql=paginated(j,size,offset)
            if req!= {'sql':sql,'query_hash':digest(sql),'offset':offset,'page_size':size}:raise ValueError('分页查询与计划不一致')
            actual=response.get('metadata',{}).get('query')
            if actual is None or normalized_sql(actual)!=normalized_sql(sql):raise ValueError('数据库实际 SQL 与请求不一致')
            page=response['data'];n=len(page)
            if any(int(r['total_rows'])!=len(rows) for r in page):raise ValueError('独立复核总行数不一致')
            if n>size or (offset+n<len(rows) and n!=size):raise ValueError('缺页或截断')
            offset+=n
        if offset!=len(rows):raise ValueError('分页总行数不一致')
    except Exception as exc:errors.append('invalid_job:'+j['job_id']+':'+str(exc))
    return errors


def validate(run, persist=True):
    import acceptance_questions as questions
    if questions.enabled(run) and not run.manifest.get("worker_task"):return questions.validate(run,persist)
    errors=run.verify_inputs()+run.verify_events();limitations=[];expected=plan(run.manifest)
    from acceptance_investigation import enabled,audit,task_records
    modern = enabled(run) and not run.manifest.get('worker_task')
    # Large candidate pools repeatedly reference the same immutable evidence.
    # Verify each reference once here, then recheck every saved file at the end.
    verified_references=set()
    def verify_reference(eid):
        if eid not in verified_references:
            run.get_evidence(eid)
            verified_references.add(eid)
    # Reconstruct the policy and scope from frozen source inputs, not mutable success counters.
    import yaml
    source_policy=read(run.path/'inputs/skill/policies/default.json')
    override=run.path/'inputs/project/policy-overrides.json'
    if override.exists():source_policy=merge(source_policy,read(override))
    if source_policy!=run.policy:errors.append('policy_differs_from_frozen_source')
    config=yaml.safe_load((run.path/'inputs/project/config.yaml').read_text())
    starts={str(k):str(v)[:7] for k,v in config['站点起始月份'].items() if k not in config.get('暂不验收站点',[])}
    if starts!=run.manifest['starts'] or sorted(starts)!=run.manifest['sites']:errors.append('scope_differs_from_frozen_source')
    registered=read(run.path/'plan.json') if (run.path/'plan.json').exists() else {}
    if registered.get('jobs')!=expected or registered.get('hash')!=digest(expected):errors.append('plan_incomplete_or_changed')
    success=0
    for j in expected:
        je=verify_job(run,j);errors.extend(je)
        if not je:success+=1
    checked={};candidates=[];samples=[];population_verified=False
    if success==len(expected):
        try:
            rebuilt=rebuild(run)
            for name,rows in rebuilt.items():
                p=run.path/(name+'.json')
                if not p.exists() or read(p)!=rows:errors.append('missing_or_changed_'+name)
            candidates=rebuilt['candidates'];samples=rebuilt['samples'];population_verified=True
            checked=dict(Counter(c['family']+':'+c['state'] for c in rebuilt['checks']))
        except Exception as exc:errors.append('analysis_rebuild_failed:'+str(exc))
    else:errors.append('baseline_incomplete')
    # Fingerprints must bracket every data query, including diagnostic queries.
    end_jobs=plan(run.manifest,True);fingerprint_changes=[]
    for end in end_jobs:
        errors.extend(verify_job(run,end))
        begin=next(j for j in expected if j['family']=='fingerprint' and j['site']==end['site'] and j['entity']==end['entity'])
        try:
            before={r['month_dt']:r for r in run.job_rows(begin['job_id'])};after={r['month_dt']:r for r in run.job_rows(end['job_id'])}
            if set(before)!=set(after):fingerprint_changes.append(end['site']+':'+end['entity']+':months')
            for mm in set(before)&set(after):
                for k in ['nrows','spus','units','amount','invalid_keys','invalid_rows']:
                    a,b=before[mm][k],after[mm][k]
                    same=(a==b) if k!='amount' or a is None or b is None else abs(float(a)-float(b))<=max(run.policy['amount_tolerance'],abs(float(a))*1e-9)
                    if not same:fingerprint_changes.append(':'.join([end['site'],end['entity'],mm,k]))
        except Exception:pass
    if fingerprint_changes:errors.append('data_changed_during_run')
    ends=[run.job_record(j['job_id']) for j in end_jobs]
    if all(r and r.get('execution_status')=='success' for r in ends):
        first_end=min(r['started_at'] for r in ends)
        for line in run._event_snapshot().splitlines():
            e=json.loads(line)
            if e['type']=='tool.started' and e['time']>first_end and e.get('parent_id') not in {j['job_id'] for j in end_jobs}:
                errors.append('data_query_after_final_fingerprint');break
    # v3.1 approvals are the authoritative business record. Legacy dispositions
    # remain readable, but neither satisfy nor duplicate the investigation gate.
    dispositions={} if modern else run.dispositions()
    known={c['candidate_id']:c for c in candidates};pending=[];verdicts=[]
    for cid,c in known.items():
        d=dispositions.get(cid)
        if not d:pending.append(cid);continue
        try:
            for eid in d['evidence_ids']:verify_reference(eid)
            if not set(d['evidence_ids'])&set(c['evidence_ids']):raise ValueError('处置未引用该候选的基础证据')
            if d['status']=='low_impact' and not all(k in d.get('residual_impact',{}) for k in ['spus','bands','units','amount']):raise ValueError('停止依据缺少四项结果')
            if d['status'] in ['needs_evidence','confirmed_defect'] and d['business_verdict']=='pass':raise ValueError('未核实或缺陷不能放行')
            verdicts.append(d['business_verdict'])
            if d['status']=='needs_evidence':limitations.append('candidate_needs_evidence:'+cid)
        except Exception as exc:errors.append('invalid_disposition:'+cid+':'+str(exc))
    if pending and not modern:errors.append('undisposed_candidates:'+str(len(pending)))
    reviews=read(run.path/'sample-reviews.json') if not modern and (run.path/'sample-reviews.json').exists() else []
    seen={};sampleids={s['sample_id'] for s in samples}
    for review in reviews:
        for sid in review.get('sample_ids',[]):
            if sid in seen or (population_verified and sid not in sampleids):errors.append('unknown_or_duplicate_sample_review:'+sid)
            seen[sid]=review
        if review.get('status') not in ['checked','needs_evidence','defect'] or not review.get('reason') or not review.get('evidence_ids'):
            errors.append('invalid_sample_review')
        for eid in review.get('evidence_ids',[]):
            try:verify_reference(eid)
            except Exception as exc:errors.append(str(exc))
        if review.get('status')=='needs_evidence':limitations.append('sample_external_verification_pending')
        if review.get('status')=='defect':verdicts.append('reject')
    missing_samples=sampleids-set(seen)
    if missing_samples and not modern:errors.append('unreviewed_samples:'+str(len(missing_samples)))
    contract=run.path/'inputs/project/scope-contract.json'
    if not contract.exists():limitations.append('完整预期采集节点及有效期未结构化提供；从未出现的遗漏尚不能穷尽')
    elif not read(contract).get('complete_leaf_list'):limitations.append('约定范围仅包含已提供的前缀；完整叶节点清单缺失，不能穷尽从未出现的遗漏')
    if checked.get('contract:effective_period_unknown'):limitations.append('部分约定节点的有效年份或月份未确定')
    if not modern and not (run.path/'inputs/project/open-issues.json').exists():limitations.append('开放历史问题清单缺失；历史问题召回率未知')
    internal=run.path/'internal-reconciliation.json'
    if not modern and not internal.exists():limitations.append('内部本品数据未对拍')
    elif not modern and (read(internal).get('status')!='compared' or not read(internal).get('evidence_ids')):
        limitations.append('内部本品数据对拍尚未形成可复核结果')
    historical=read(run.path/'historical-recheck.json') if (run.path/'historical-recheck.json').exists() else {}
    if not modern and (run.path/'inputs/project/open-issues.json').exists():
        expected_issues={i['issue_id'] for i in read(run.path/'inputs/project/open-issues.json') if i.get('status')!='closed'}
        actual_issues={i.get('issue_id') for i in historical.get('items',[])}
        if expected_issues!=actual_issues:errors.append('historical_obligations_missing')
        for issue in historical.get('items',[]):
            if issue.get('review_state') not in ['rechecked','closed','not_applicable'] or not issue.get('reason') or not issue.get('evidence_ids'):
                errors.append('historical_recheck_pending')
            for eid in issue.get('evidence_ids',[]):
                try:verify_reference(eid)
                except Exception as exc:errors.append(str(exc))
    samplemap={s['sample_id']:s for s in samples}
    for sid,review in seen.items():
        if sid in samplemap and not set(review.get('evidence_ids',[]))&set(samplemap[sid]['evidence_ids']):errors.append('sample_review_without_source:'+sid)
    content_path=run.path/'report-content.json'
    if not content_path.exists():errors.append('report_content_missing')
    else:
        content=read(content_path);finding_candidates=set()
        from acceptance_report_content import errors as report_content_errors
        errors.extend(report_content_errors(content))
        import acceptance_triage as triage
        if triage.enabled(run):
            try:
                from acceptance_report_content import resolve_placements
                content, placement_errors = resolve_placements(content, triage.status(run))
                errors.extend(placement_errors)
            except (ValueError, KeyError, OSError, TypeError) as exc: errors.append('report_classification:'+str(exc))
        for item in content.get('audit_overview',[]) if isinstance(content.get('audit_overview',[]),list) else []:
            if not isinstance(item,dict) or not isinstance(item.get('evidence_ids',[]),list):continue
            for eid in item.get('evidence_ids',[]):
                try:verify_reference(eid)
                except Exception as exc:errors.append(str(exc))
        if not content.get('summary'):errors.append('report_summary_missing')
        for finding in content.get('findings',[]):
            if any(not finding.get(k) for k in ['title','scope','facts','counterevidence','assessment','action','evidence_ids']):errors.append('finding_fields_missing')
            for cid in finding.get('candidate_ids',[]):
                if cid not in known:errors.append('finding_unknown_candidate:'+cid)
                finding_candidates.add(cid)
            for eid in finding.get('evidence_ids',[]):
                try:verify_reference(eid)
                except Exception as exc:errors.append(str(exc))
        for cid,d in dispositions.items():
            if cid not in known:errors.append('disposition_unknown_candidate:'+cid)
            if d.get('status')=='confirmed_defect' and cid not in finding_candidates:errors.append('confirmed_defect_missing_in_report:'+cid)

    evidence_ids={json.loads(line).get('evidence_id') for line in run._event_snapshot().splitlines() if json.loads(line)['type']=='evidence.saved'}
    for eid in evidence_ids:
        try:run.get_evidence(eid)
        except Exception as exc:errors.append(str(exc))
    investigation_result=None
    if modern:
        investigation_result=audit(run,read(content_path) if content_path.exists() else {'findings':[]})
        errors.extend('investigation:'+e for e in investigation_result['errors'])
        if not investigation_result.get('applicable_investigation_complete', investigation_result['substantive_complete']):
            errors.append('business_investigation_incomplete')
        for verdict,count in investigation_result['business_verdicts'].items():
            if count:verdicts.append(verdict)
        # Child SQL queries must also precede the final parent fingerprint.
        if all(r and r.get('execution_status')=='success' for r in ends):
            first_end=min(r['started_at'] for r in ends)
            try:
                for task in task_records(run).values():
                    worker_path=run.path/task['worker_dir']
                    for line in event_snapshot(worker_path/'events.jsonl').splitlines():
                        event=json.loads(line)
                        if event['type']=='tool.started' and event['time']>first_end:
                            errors.append('worker_query_after_final_fingerprint:'+task['task_id']);break
            except (ValueError,FileNotFoundError) as exc:errors.append('worker_trace:'+str(exc))
    architecture={}
    from acceptance_coverage import enabled as coverage_enabled
    if coverage_enabled(run):
        # These independent gates are rebuilt from frozen inputs and raw
        # evidence. None may borrow a success flag from the queue or report.
        from acceptance_coverage import audit as coverage_audit
        from acceptance_cases import audit as case_audit
        from acceptance_machine import audit as machine_audit
        from acceptance_replay import audit as replay_audit
        for name,checker in [('mandatory_coverage',coverage_audit),('case_routing',case_audit),
                             ('machine_diagnostics',machine_audit),('archive_replay',replay_audit)]:
            try:
                value=checker(run)
            except Exception as exc:
                value={'errors':['audit_failed:'+str(exc)],'complete':False}
            architecture[name]=value
            errors.extend(name+':'+str(e) for e in value.get('errors',[]))
            if name=='archive_replay':limitations.extend(value.get('limitations',[]))
        cv=architecture['mandatory_coverage']
        if not cv.get('scan_complete'):errors.append('mandatory_scan_incomplete')
        if not cv.get('history_complete'):errors.append('historical_regression_incomplete')
        if not cv.get('sampling_complete'):errors.append('no_signal_sampling_incomplete')
        case=architecture['case_routing']
        if not case.get('mapping_complete'):errors.append('case_obligation_mapping_incomplete')
        # Formal per-obligation reviewed proofs remain the completion gate.
        # Machine or rule work alone cannot mark a substantive conclusion.
        from acceptance_observability import summarize
        architecture['resource_usage']=summarize(run)
    if investigation_result and investigation_result.get('deferred', 0):
        verdicts.append('qualified')  # routing completion is never unreserved data acceptance
    errors=sorted(set(errors));limitations=sorted(set(limitations))
    state='incomplete' if errors else ('limited' if limitations else 'complete')
    verdict='reject' if 'reject' in verdicts else ('undetermined' if errors or limitations or 'undetermined' in verdicts else ('qualified' if 'qualified' in verdicts else 'pass'))
    result={'run_id':run.manifest['run_id'],'execution_state':state,'business_verdict':verdict,'errors':errors,
        'limitations':limitations,'baseline_jobs':{'expected':len(expected),'verified':success},'coverage':checked,
        'candidate_population_verified':population_verified,
        'candidates':{'expected':len(candidates) if population_verified else None,'disposed':len(candidates)-len(pending) if population_verified else None,'pending':len(pending) if population_verified else None,'high':sum(c['priority']=='high' for c in candidates) if population_verified else None},
        'disposition_status_counts':dict(Counter(d['status'] for cid,d in dispositions.items() if cid in known)),
        'semantic_review_status_counts':dict(Counter(seen[sid]['status'] for sid in sampleids&set(seen))),
        'semantic_sample':{'expected':len(samples) if population_verified else None,'reviewed':len(sampleids&set(seen)) if population_verified else None},
        'fingerprint_changes':fingerprint_changes,'evidence_count':len(evidence_ids),
        'event_count':run.seq,'note':'结构核验不替代证据的业务判断；相同指纹不是事务快照。'}
    if architecture:
        result['architecture']=architecture
        result['completion_gates']={
            'mandatory_scan':architecture['mandatory_coverage'].get('scan_complete',False),
            'no_signal_sampling':architecture['mandatory_coverage'].get('sampling_complete',False),
            'historical_regression':architecture['mandatory_coverage'].get('history_complete',False),
            'obligation_mapping':architecture['case_routing'].get('mapping_complete',False),
            'substantive_investigation':bool(investigation_result and investigation_result.get('substantive_complete')),
            'applicable_investigation':bool(investigation_result and investigation_result.get('applicable_investigation_complete',investigation_result.get('substantive_complete'))),
            'full_delivery':not errors and not limitations,
            'unqualified_data_release':verdict=='pass' and not errors and not limitations}
    if investigation_result is not None:
        result['investigation']=investigation_result
        by_source=investigation_result['source_states']
        def reviewed_count(kind):
            states=by_source.get(kind,{})
            return states.get('verified',0)+states.get('reviewed_residual',0)+states.get('blocked_external',0)
        result['candidates'].update(disposed=reviewed_count('candidate') if population_verified else None,
            pending=len(candidates)-reviewed_count('candidate') if population_verified else None)
        result['semantic_sample']['reviewed']=reviewed_count('sample') if population_verified else None
        result['disposition_status_counts']=by_source.get('candidate',{})
        result['semantic_review_status_counts']=by_source.get('sample',{})
    if persist:
        write(run.path/'validation.json',result)
        run.event('validation.completed',execution_state=state,business_verdict=verdict,error_count=len(errors))
    return result
