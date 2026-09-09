#!/usr/bin/env python3
"""月度验收：当前协议使用 scan/analyze → panorama-read → question-register/collect → investigation-review。

旧命令仅供对应冻结协议；不适用的 3.x 入口在新运行明确拒绝。
"""
import argparse
import asyncio
import fcntl
import json
from pathlib import Path
from acceptance_core import Run,create_run,read,write,digest
from acceptance_queries import plan,drilldown
from acceptance_transport import connect,scan,call,execute_job
from acceptance_analysis import analyze
from acceptance_validate import validate
from acceptance_render import render
import acceptance_investigation as investigation


def require_jobs_success(records):
    failed=[r['job_id'] for r in records if r['execution_status']!='success']
    if failed:
        raise SystemExit('查询任务未全部成功；真实记录已保留，可继续重试。失败任务：'+', '.join(failed))


def attachment_payload(path):
    """Preserve original text and expose actual structured records to locators."""
    path=Path(path)
    raw=path.read_bytes()
    original=raw.decode('utf-8')
    suffix=path.suffix.lower()
    if suffix=='.json':content=json.loads(original)
    elif suffix in ('.jsonl','.ndjson'):
        content=[json.loads(line) for line in original.splitlines() if line.strip()]
    else:content=original
    return {'content':content,'original_text':original,'original_filename':path.name,
            'original_sha256':digest(raw),
            'text_lines':[{'line':number,'text':line} for number,line in enumerate(original.splitlines(),1)],
            'note':'Parsed structure is a faithful view of the attached file, not authentication of its claims or execution of a query.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    start=sub.add_parser('start');start.add_argument('--month',required=True);start.add_argument('--base',default='验收');start.add_argument('--related-run')
    for name in ['scan','finish-scan','analyze','status','validate','render','seal','verify-seal']:
        p=sub.add_parser(name);p.add_argument('run');p.add_argument('--mcp-config') if name in ['scan','finish-scan'] else None
    p=sub.add_parser('query');p.add_argument('run');p.add_argument('--sql-file',required=True);p.add_argument('--label',required=True);p.add_argument('--mcp-config')
    p=sub.add_parser('drill');p.add_argument('run');p.add_argument('--candidate-id',required=True);p.add_argument('--mcp-config')
    p=sub.add_parser('record');p.add_argument('run');p.add_argument('--file',required=True)
    p=sub.add_parser('attach');p.add_argument('run');p.add_argument('--file',required=True);p.add_argument('--kind',required=True);p.add_argument('--source',required=True)
    p=sub.add_parser('diagnose');p.add_argument('run');p.add_argument('--spec',required=True);p.add_argument('--mcp-config')
    p=sub.add_parser('investigation-proof');p.add_argument('run');p.add_argument('--candidate-id',required=True);p.add_argument('--file',required=True);p.add_argument('--output',required=True)
    p=sub.add_parser('archive-replay');p.add_argument('run');p.add_argument('--source',required=True)
    p=sub.add_parser('evidence-reuse');p.add_argument('run');p.add_argument('--source',required=True);p.add_argument('--job-ids-file',required=True)
    for name in ['coverage-plan','coverage-status','history-plan','sampling-plan','sampling-jobs','case-plan','case-status','machine-status']:
        p=sub.add_parser(name);p.add_argument('run')
    p=sub.add_parser('sampling-collect');p.add_argument('run');p.add_argument('--mcp-config');p.add_argument('--limit',type=int)
    p=sub.add_parser('independent-freeze');p.add_argument('run');p.add_argument('--file');p.add_argument('--provenance',default='deterministic_scan');p.add_argument('--history-blind',action='store_true')
    for name in ['history-compare','sampling-review','sampling-rule']:
        p=sub.add_parser(name);p.add_argument('run');p.add_argument('--file',required=True)
    p=sub.add_parser('sampling-register-findings');p.add_argument('run');p.add_argument('--coordinator',required=True);p.add_argument('--ids-file')
    for name in ['machine-collect','machine-diagnose']:
        p=sub.add_parser(name);p.add_argument('run');p.add_argument('--spec',required=True)
        if name=='machine-collect':p.add_argument('--mcp-config')
        else:p.add_argument('--claims');p.add_argument('--risk-bounds')
    p=sub.add_parser('case-classify');p.add_argument('run');p.add_argument('--coordinator',required=True);p.add_argument('--file',required=True)
    p=sub.add_parser('case-assess');p.add_argument('run');p.add_argument('--file',required=True);p.add_argument('--output')
    p=sub.add_parser('case-queue');p.add_argument('run');p.add_argument('--output')
    p=sub.add_parser('case-route');p.add_argument('run');p.add_argument('--coordinator',required=True);p.add_argument('--budget-exhausted',action='store_true');p.add_argument('--reason')
    for name in ['case-split','case-reopen']:
        p=sub.add_parser(name);p.add_argument('run');p.add_argument('--coordinator',required=True);p.add_argument('--case-id',required=True);p.add_argument('--file',required=True)
    p=sub.add_parser('case-acknowledge-review');p.add_argument('run');p.add_argument('--coordinator',required=True)
    g=p.add_mutually_exclusive_group(required=True);g.add_argument('--case-id');g.add_argument('--case-ids-file')
    for name in ['rule-shadow','rule-validate','rule-apply','rule-invalidate']:
        p=sub.add_parser(name);p.add_argument('run');p.add_argument('--reviewer' if name=='rule-validate' else '--coordinator',required=True)
        if name!='rule-shadow':p.add_argument('--rule-id',required=True);p.add_argument('--rule-version',required=True)
        if name!='rule-apply':p.add_argument('--file',required=True)
    p=sub.add_parser('investigation-progress');p.add_argument('run');p.add_argument('--coordinator',required=True);p.add_argument('--task-id',required=True);p.add_argument('--state',required=True,choices=['failed','timeout','capacity_pending','returned']);p.add_argument('--file',required=True)
    p=sub.add_parser('investigation-amend-scope');p.add_argument('run');p.add_argument('--coordinator',required=True);p.add_argument('--obligation-id',required=True);p.add_argument('--file',required=True)
    for name in ['investigation-plan','investigation-assign','investigation-submit','investigation-review','investigation-requeue','investigation-add']:
        p=sub.add_parser(name);p.add_argument('run');p.add_argument('--coordinator',required=True)
        if name=='investigation-assign':
            p.add_argument('--agent',required=True);g=p.add_mutually_exclusive_group(required=True);g.add_argument('--ids-file');g.add_argument('--next',action='store_true')
            p.add_argument('--reason')
        if name in ['investigation-submit','investigation-review','investigation-requeue']:p.add_argument('--task-id',required=True)
        if name in ['investigation-review','investigation-add']:p.add_argument('--file',required=True)
        if name=='investigation-requeue':p.add_argument('--reason',required=True)
    for name in ['investigation-status','checkpoint']:
        p=sub.add_parser(name);p.add_argument('run')
    for name in ('panorama-status','question-status'):
        p=sub.add_parser(name);p.add_argument('run')
    for name in ('panorama-read','question-register','baseline-review'):
        p=sub.add_parser(name);p.add_argument('run');p.add_argument('--actor',required=True);p.add_argument('--file',required=True)
    p=sub.add_parser('question-jobs');p.add_argument('run');p.add_argument('--obligation-id',required=True);p.add_argument('--mode',choices=['aggregate','cohorts','details','lineage'],default='aggregate');p.add_argument('--output',required=True)
    p.add_argument('--query-month',action='append');p.add_argument('--anchor-month',action='append');p.add_argument('--anchor-paths-file')
    p=sub.add_parser('question-collect');p.add_argument('run');p.add_argument('--jobs-file',required=True);p.add_argument('--mcp-config')
    args=parser.parse_args();skill=Path(__file__).resolve().parents[1]
    if args.command=='start':
        directory=create_run(args.base,skill,args.month,args.related_run);run=Run(directory);run.register_plan(plan(run.manifest))
        from acceptance_coverage import prepare as prepare_coverage
        prepare_coverage(run)
        print(directory);return
    run_path=Path(args.run).resolve()
    with (run_path/'.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('此运行正在执行另一个写操作。请等待；观察 events.jsonl 不需要锁。')
        # Read the event-chain tail only after obtaining the single-writer lock.
        run=Run(run_path)
        if args.command=='verify-seal':
            sealed=read(run.path/'sealed.json');errors=[]
            for path,sha in sealed['sha256'].items():
                p=(run.path/path).resolve()
                if not p.is_relative_to(run.path) or not p.exists() or digest(p.read_bytes())!=sha:errors.append(path)
            print(json.dumps({'file_integrity':not errors,'changed_or_missing':errors,'execution_state':sealed['execution_state'],'business_verdict':sealed['business_verdict']},ensure_ascii=False,indent=2));return
        from acceptance_panorama import enabled as question_enabled
        if question_enabled(run):
            retired={'case-assess','case-classify','case-route','case-split','case-reopen','case-acknowledge-review','machine-status',
                     'history-compare','sampling-review','sampling-rule','sampling-register-findings','investigation-proof','drill','record',
                     'case-queue','machine-collect','machine-diagnose','investigation-amend-scope','investigation-add'}
            if args.command in retired or args.command.startswith('rule-'):
                raise ValueError('此 3.x 入口不适用于新议题分母。请使用 question-register / question-collect / baseline-review / investigation-review。')
            if args.command=='case-plan':
                import acceptance_questions as questions
                print(json.dumps(questions.case_status(run),ensure_ascii=False,indent=2));return
        if args.command=='investigation-status':
            print(json.dumps(investigation.audit(run),ensure_ascii=False,indent=2));return
        if args.command in ['coverage-status','case-status','machine-status']:
            if args.command=='coverage-status':from acceptance_coverage import audit as module_audit
            elif args.command=='case-status':from acceptance_cases import audit as module_audit
            else:from acceptance_machine import audit as module_audit
            print(json.dumps(module_audit(run),ensure_ascii=False,indent=2));return
        run.writable()
        if run.verify_inputs():raise SystemExit('冻结输入已变更')
        if args.command in ('panorama-status','question-status','panorama-read','question-register','baseline-review','question-jobs','question-collect'):
            import acceptance_questions as questions
            import acceptance_panorama as panorama
            if not questions.enabled(run):raise ValueError('此入口仅适用于 全景议题协议')
            if args.command=='panorama-status':result=panorama.overview(run)
            elif args.command=='question-status':result=questions.case_status(run)
            elif args.command=='panorama-read':result=questions.record_reading(run,args.actor,read(args.file))
            elif args.command=='question-register':
                value=read(args.file);result=questions.register_many(run,args.actor,value) if isinstance(value,list) else questions.register(run,args.actor,value)
            elif args.command=='baseline-review':result=questions.record_baseline_review(run,args.actor,read(args.file))
            elif args.command=='question-jobs':
                from acceptance_queries import question_jobs
                unit=dict(questions.obligations(run)[args.obligation_id]['source_material']['scope_unit'])
                if args.query_month:unit['query_months']=args.query_month
                if args.anchor_month:unit['anchor_months']=args.anchor_month
                if args.anchor_paths_file:unit['anchor_paths']=read(args.anchor_paths_file)
                result=question_jobs(run.manifest,unit,args.mode)
                write(Path(args.output),result)
            else:
                from acceptance_queries import verify_question_job
                jobs=read(args.jobs_file)
                for j in jobs:verify_question_job(run.manifest,j)
                require_jobs_success(asyncio.run(scan(run,jobs,args.mcp_config)));result={'jobs':len(jobs)}
            print(json.dumps(result,ensure_ascii=False,indent=2));return
        if args.command=='archive-replay':
            from acceptance_replay import import_archive
            receipt=import_archive(run,args.source)
            print(json.dumps({'imported_jobs':len(receipt['imported_jobs']),'pending_changed_jobs':receipt['pending_changed_jobs'],'fresh_full_scan':False},ensure_ascii=False))
        elif args.command=='evidence-reuse':
            from acceptance_replay import import_planned_jobs
            receipt=import_planned_jobs(run,args.source,read(args.job_ids_file))
            print(json.dumps({'imported_jobs':len(receipt['imported_jobs']),
                'already_identical_jobs':len(receipt['already_identical_jobs']),'fresh_query_count':0},ensure_ascii=False))
        elif args.command in ['coverage-plan','history-plan','sampling-plan']:
            from acceptance_coverage import prepare,prepare_history,prepare_sampling
            value={'coverage-plan':prepare,'history-plan':prepare_history,'sampling-plan':prepare_sampling}[args.command](run)
            print(json.dumps({'saved':args.command,'sha256':value.get('sha256')},ensure_ascii=False))
        elif args.command in ['sampling-jobs','sampling-collect']:
            from acceptance_coverage import sampling_jobs
            jobs=sampling_jobs(run)
            if args.command=='sampling-jobs':print(json.dumps(jobs,ensure_ascii=False,indent=2))
            else:
                if args.limit is not None and args.limit<1:raise ValueError('limit须为正数；只限制本次执行批次，不缩减抽样分母')
                from acceptance_validate import verify_job
                remaining=[job for job in jobs if verify_job(run,job)]
                batch=remaining[:args.limit] if args.limit else remaining
                for job in batch:write(run.path/'records'/(job['job_id']+'-plan.json'),job)
                require_jobs_success(asyncio.run(scan(run,batch,args.mcp_config)))
        elif args.command=='independent-freeze':
            from acceptance_coverage import freeze_independent
            value=freeze_independent(run,read(args.file) if args.file else None,provenance=args.provenance,history_blind=args.history_blind)
            print(json.dumps({'saved':args.command,'sha256':value.get('sha256')},ensure_ascii=False))
        elif args.command in ['history-compare','sampling-review','sampling-rule']:
            from acceptance_coverage import compare_history,record_sample_review,register_sampling_rule
            value={'history-compare':compare_history,'sampling-review':record_sample_review,'sampling-rule':register_sampling_rule}[args.command](run,read(args.file))
            print(json.dumps(value,ensure_ascii=False,indent=2))
        elif args.command=='sampling-register-findings':
            from acceptance_coverage import register_sampling_findings
            value=register_sampling_findings(run,args.coordinator,read(args.ids_file) if args.ids_file else None)
            if (run.path/'investigations/cases/state.json').exists():
                from acceptance_cases import prepare as refresh_cases
                refresh_cases(run)
            print(json.dumps(value,ensure_ascii=False,indent=2))
        elif args.command=='case-plan':
            from acceptance_cases import prepare
            value=prepare(run)
            from acceptance_cases import summary
            print(json.dumps(summary(value),ensure_ascii=False,indent=2))
        elif args.command in ('case-assess','case-classify','case-queue'):
            import acceptance_triage as triage
            if args.command=='case-classify':
                result=triage.status(run,triage.classify(run,args.coordinator,read(args.file)))
            elif args.command=='case-assess':result=triage.preview(run,read(args.file))
            else:result=triage.status(run)
            if getattr(args,'output',None):
                write(args.output,result)
                shown={'output':str(Path(args.output).resolve()),'groups':len(result) if isinstance(result,list) else None}
                if isinstance(result,dict):shown.update({k:result[k] for k in ('case_count','classified_cases','original_obligations','applicable_investigation_obligations','facts_sha256','errors') if k in result})
            else:shown=result
            print(json.dumps(shown,ensure_ascii=False,indent=2))
        elif args.command=='case-route':
            from acceptance_cases import route
            value=route(run,actor=args.coordinator,budget_exhausted=args.budget_exhausted,reason=args.reason)
            from acceptance_cases import summary
            print(json.dumps(summary(value),ensure_ascii=False,indent=2))
        elif args.command in ['case-split','case-reopen']:
            import acceptance_cases as case_module
            value=read(args.file)
            if args.command=='case-split':result=case_module.split(run,args.coordinator,args.case_id,value['obligation_ids'],value['reason'],value['evidence_ids'])
            else:result=case_module.reopen(run,args.coordinator,args.case_id,value['reason'],value['evidence_ids'])
            print(json.dumps(case_module.summary(result),ensure_ascii=False,indent=2))
        elif args.command=='case-acknowledge-review':
            import acceptance_cases as case_module
            result=case_module.acknowledge_reviews(run,args.coordinator,
                read(args.case_ids_file) if args.case_ids_file else [args.case_id])
            print(json.dumps(case_module.summary(result),ensure_ascii=False,indent=2))
        elif args.command.startswith('rule-'):
            import acceptance_cases as case_module
            value=read(args.file) if hasattr(args,'file') else None
            if args.command=='rule-shadow':result=case_module.shadow_rule(run,args.coordinator,value['definition'],value.get('case_ids'))
            elif args.command=='rule-validate':result=case_module.validate_rule(run,args.reviewer,args.rule_id,args.rule_version,value)
            elif args.command=='rule-apply':result=case_module.apply_rule(run,args.coordinator,args.rule_id,args.rule_version)
            else:result=case_module.invalidate_rule(run,args.coordinator,args.rule_id,args.rule_version,value['reason'],value['evidence_ids'])
            print(json.dumps(result,ensure_ascii=False,indent=2))
        elif args.command=='investigation-progress':
            value=investigation.record_progress(run,args.coordinator,args.task_id,args.state,read(args.file))
            print(json.dumps(value,ensure_ascii=False,indent=2))
        elif args.command=='investigation-amend-scope':
            value=read(args.file)
            result=investigation.amend_contract_scope(run,args.coordinator,args.obligation_id,value['new_scope'],value['reason'],value['evidence_locators'])
            print(json.dumps(result,ensure_ascii=False,indent=2))
        elif args.command=='machine-collect':
            from acceptance_machine import collection_jobs,collection_trace_jobs
            spec=read(args.spec)
            async def collect():
                async with connect(run,args.mcp_config) as session:
                    # Scope first, then derive every identity shard from the
                    # complete target, keeping the run's shared SQL capacity.
                    for job in collection_jobs(run.manifest,spec):
                        write(run.path/'records'/(job['job_id']+'-plan.json'),job)
                        require_jobs_success([await execute_job(run,session,job)])
                    for job in collection_trace_jobs(run,spec):
                        write(run.path/'records'/(job['job_id']+'-plan.json'),job)
                        require_jobs_success([await execute_job(run,session,job)])
            asyncio.run(collect())
        elif args.command=='machine-diagnose':
            from acceptance_machine import diagnose
            value=diagnose(run,read(args.spec),claims=read(args.claims) if args.claims else None,risk_bounds=read(args.risk_bounds) if args.risk_bounds else None)
            print(json.dumps(value,ensure_ascii=False,indent=2))
        elif args.command=='scan':
            jobs=plan(run.manifest);run.register_plan(jobs)
            require_jobs_success(asyncio.run(scan(run,jobs,args.mcp_config)))
        elif args.command=='finish-scan':
            require_jobs_success(asyncio.run(scan(run,plan(run.manifest,True),args.mcp_config,force=True)))
        elif args.command=='analyze':print(json.dumps(analyze(run),ensure_ascii=False))
        elif args.command in ['status','validate']:
            v=validate(run,persist=args.command=='validate');print(json.dumps(v,ensure_ascii=False,indent=2))
        elif args.command=='record':
            value=read(args.file);decisions=value if isinstance(value,list) else [value]
            print(json.dumps([run.decide(d) for d in decisions],ensure_ascii=False))
        elif args.command=='attach':
            print(run.evidence(attachment_payload(args.file),kind=args.kind,source=args.source))
        elif args.command=='query':
            async def query():
                async with connect(run,args.mcp_config) as session:
                    eid,data=await call(run,session,Path(args.sql_file).read_text(),args.label)
                    print(json.dumps({'evidence_id':eid,'rows':data['row_count'],'note':'自由查询结果仅为证据；不增加基础覆盖计数。截断风险须自行明确。'},ensure_ascii=False))
            asyncio.run(query())
        elif args.command=='drill':
            c=next(c for c in read(run.path/'candidates.json') if c['candidate_id']==args.candidate_id)
            if c['kind']!='result_change':raise ValueError('此候选需要按事实编写溯源查询，不能自动做 SPU 增减分解')
            lag=run.policy['comparisons'][c['comparison']]
            j={'job_id':'drill-'+c['candidate_id'],'family':'drill','site':c['site'],'candidate_id':c['candidate_id'],
                'key_fields':['product_id'],'sql':drilldown(run.manifest,c['site'],c['path'],c['level'],c['month'],lag)}
            write(run.path/'records'/(j['job_id']+'-plan.json'),j)
            async def execute():
                async with connect(run,args.mcp_config) as session:return await execute_job(run,session,j)
            require_jobs_success([asyncio.run(execute())])
        elif args.command=='diagnose':
            from acceptance_diagnostics import diagnostic_job
            j=diagnostic_job(run.manifest,**read(args.spec))
            write(run.path/'records'/(j['job_id']+'-plan.json'),j)
            async def execute_diagnostic():
                async with connect(run,args.mcp_config) as session:return await execute_job(run,session,j)
            require_jobs_success([asyncio.run(execute_diagnostic())])
        elif args.command=='investigation-plan':
            value=investigation.prepare(run,args.coordinator)
            print(json.dumps({'obligations':len(value['payload']['obligations']),'sha256':value['sha256']},ensure_ascii=False))
        elif args.command=='investigation-proof':
            from acceptance_investigation_proof import verify_result_proof
            if not run.manifest.get('worker_task'):raise ValueError('请在获分派的子包中计算调查证明')
            candidate=next(c for c in read(run.path/'candidates.json') if c['candidate_id']==args.candidate_id)
            if candidate['kind']=='result_change':
                value=verify_result_proof(run.manifest,candidate,read(args.file),run,check_claims=False)
            elif candidate['kind'] in ('raw_presence_gap','std_presence_gap'):
                from acceptance_gap_proof import verify_gap_proof
                parent=Run(run.manifest['parent_run'])
                value=verify_gap_proof(run.manifest,candidate,read(args.file),run,parent.get_evidence,check_claims=False)
            else:raise ValueError('该类义务使用具体契约/原始证据调查，不适用此数值对象证明')
            output=Path(args.output).resolve()
            if not output.is_relative_to(run.path):raise ValueError('证明输出须保存于当前子包')
            write(output,value)
            run.event('investigation.proof.computed',candidate_id=candidate['candidate_id'],sha256=digest(value),cohort=value['cohort'])
            print(json.dumps({'output':str(output),'cohort':value['cohort'],'note':'机算影响已保存；原因与反证仍须独立主审核。'},ensure_ascii=False))
        elif args.command=='investigation-assign':
            print(json.dumps(investigation.assign(run,args.coordinator,args.agent,read(args.ids_file) if args.ids_file else None,assignment_reason=args.reason),ensure_ascii=False,indent=2))
        elif args.command=='investigation-submit':
            print(json.dumps(investigation.submit(run,args.coordinator,args.task_id),ensure_ascii=False))
        elif args.command=='investigation-review':
            print(json.dumps(investigation.review(run,args.coordinator,args.task_id,read(args.file)),ensure_ascii=False))
        elif args.command=='investigation-requeue':
            print(json.dumps(investigation.requeue(run,args.coordinator,args.task_id,args.reason),ensure_ascii=False))
        elif args.command=='investigation-add':
            print(json.dumps(investigation.add_obligations(run,args.coordinator,read(args.file)),ensure_ascii=False))
        elif args.command=='checkpoint':
            v=validate(run);render(run)
            from acceptance_observability import resume_state
            resume=resume_state(run)
            write(run.path/'resume-state.json',resume)
            event_id=run.event('investigation.checkpoint',execution_state=v['execution_state'],investigation=v.get('investigation'))
            write(run.path/'checkpoints'/(event_id+'.json'),{'validation':v,'report_sha256':digest((run.path/'报告.md').read_bytes()),'resume_sha256':digest(resume),'resume':resume,'note':'阶段存档，运行仍可继续；不代替最终核验或封存。'})
            print(json.dumps({'checkpoint':event_id,'final_delivery':False},ensure_ascii=False))
        elif args.command=='render':render(run)
        elif args.command=='seal':
            v=validate(run);render(run);run.seal(v)
            print(json.dumps({'sealed':True,'execution_state':v['execution_state'],'business_verdict':v['business_verdict']},ensure_ascii=False))

if __name__=='__main__':main()
