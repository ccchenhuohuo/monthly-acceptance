"""Typed scope checks shared by constraint ownership and quantitative evidence."""
import json

from acceptance_core import canonical, shift


def path(value):
    value=json.loads(value) if isinstance(value,str) else value
    if not isinstance(value,list):raise ValueError('证据路径不是完整数组')
    return value


def within_paths(actual, expected):
    return all(any(p[:len(root)]==root for root in expected) for p in actual)


def constraint_cells(run, constraint):
    """An omitted dimension means the complete applicable set, never any member."""
    scope=constraint['scope'];site=constraint['site'];rule=constraint['rule']
    platforms=([scope['platform']] if scope.get('platform') else
               scope.get('platforms') or scope.get('unexpected_platforms') or
               run.policy.get('expected_platforms',{}).get(site,[]))
    if not platforms:raise ValueError('失败约束缺冻结平台全集，不能认领完成')
    layers=([scope['layer']] if scope.get('layer') else
            ['raw'] if rule=='contract' else
            ['std'] if 'path' in scope else ['raw','std'])
    target=path(scope.get('path',scope.get('path_prefix',[])))
    path_layer='raw' if 'path_prefix' in scope else 'std' if 'path' in scope else None
    periods={constraint['month']}
    if rule in ('movement_conservation','level_shift'):
        # A cross-month statement is not owned by looking at one side of it.
        periods.add(shift(constraint['month'],-int(scope['comparison'].split(':')[0])))
    return {(site,pl,layer,mm,path_layer,canonical(target))
            for pl in platforms for layer in layers for mm in periods}


def unit_covers(unit,cell,family):
    site,platform,layer,mm,path_layer,target=cell
    if (unit['site']!=site or platform not in unit['platforms'] or layer not in unit['source_layers']
            or mm not in unit['months'] or family not in unit['required_checks']):return False
    if unit['paths']==[[]]:return True
    return path_layer==unit['path_layer'] and within_paths([path(target)],unit['paths'])


def constraint_coverage(run,constraint,units):
    required=constraint_cells(run,constraint)
    covered={cell for cell in required if any(unit_covers(u,cell,constraint['family']) for u in units)}
    return required,covered


def row_scope(context,row):
    """Derive membership from verified query semantics, never locator claims."""
    job=context['job'];family=job['family'];spec=job.get('scope_spec',{})
    site=job['site']
    if row.get('site') not in (None,site):raise ValueError('证据国家与原查询不一致')
    periods=set(job.get('months',[]))
    mm=row.get('month_dt',row.get('month'))
    if mm is not None:
        mm=str(mm)[:7]
        if periods and mm not in periods:raise ValueError('证据月份与原查询不一致')
        periods={mm}
    if not periods:raise ValueError('证据缺可核验月份范围')
    platforms=set(spec.get('platforms') or context['platforms'])
    if 'platform' in row:
        if row['platform'] not in platforms:raise ValueError('证据平台与原查询不一致')
        platforms={row['platform']}
    elif 'platform' in job.get('key_fields',[]):raise ValueError('原始记录缺查询平台键')
    if not platforms:raise ValueError('证据缺冻结平台范围')
    layer=job.get('layer') or job.get('entity')
    if layer not in ('raw','std'):
        layer='raw' if family=='raw_paths' else 'std' if family in ('category','bands','sample','movement') else None
    layers={layer} if layer else {'raw','std'} if family=='mapping' else set()
    if not layers:raise ValueError('证据数据层无法由查询证明')
    if row.get('source_layer') is not None and row['source_layer'] not in layers:
        raise ValueError('证据数据层与原查询不一致')
    if family.startswith('question_'):
        if job.get('mode')=='lineage':
            path_layer=layer
            value=row.get('std_path' if layer=='std' else 'raw_path',row.get('path'))
            paths=[path(value)] if value is not None else [[]]
        elif job.get('mode')=='details' and spec['path_layer']==layer:
            path_layer=layer;paths=[path(row.get('path'))]
            if not within_paths(paths,spec['paths']):raise ValueError('证据路径与原查询选择范围不一致')
        else:
            # Raw rows selected by a standard subtree use the exact EXISTS
            # selector's scope; their physical raw path is a different taxonomy.
            path_layer=spec['path_layer'];paths=spec['paths']
    elif 'path' in row:
        path_layer=layer;paths=[path(row['path'])]
    else:path_layer=None;paths=[[]]
    return {'site':site,'months':periods,'platforms':platforms,'source_layers':layers,
            'path_layer':path_layer,'paths':paths}


def evidence_within(actual,target):
    if actual['site']!=target['site']:return False
    if any(not set(actual[k])<=set(target[k]) for k in ('months','platforms','source_layers')):return False
    if target['paths']==[[]]:return True
    return actual['path_layer']==target['path_layer'] and within_paths(actual['paths'],target['paths'])
