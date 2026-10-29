"""规则与任务的工具表，确认只读取当前用户原话。"""


def definitions():
    names=(
        ('ha_rules','列出本系统创建的场景与自动化',{},()),
        ('ha_rule_propose','创建或修改场景/自动化提案；复述后等待用户下一轮确认',{'rule':{'type':'object'},'rule_id':{'type':'string'}},('rule',)),
        ('ha_rule_delete','提议删除本系统规则，等待下一轮确认',{'rule_id':{'type':'string'}},('rule_id',)),
        ('ha_rule_confirm','确认提案；不能替用户确认',{'proposal_id':{'type':'string'}},('proposal_id',)),
        ('ha_scene_run','执行已确认的本系统场景并验证设备状态',{'rule_id':{'type':'string'}},('rule_id',)),
        ('ha_tasks','列出定时任务及失败/错过记录',{},()),
        ('ha_task_propose','创建或修改定时提案；when支持N小时后、N分钟后、今天HH:MM、每天HH:MM；kind为device/reminder/history_report',{'task':{'type':'object'},'task_id':{'type':'string'}},('task',)),
        ('ha_task_delete','提议删除明确ID的定时任务',{'task_id':{'type':'string'}},('task_id',)),
        ('ha_task_confirm','确认定时提案；必须读取用户当前轮明确同意',{'proposal_id':{'type':'string'}},('proposal_id',)),
    )
    result=[]
    for name,description,properties,required in names:
        if name=='ha_rule_propose':
            properties['rule']={'type':'object','properties':{
                'kind':{'type':'string','enum':['scene','automation']},'name':{'type':'string'},'phrase':{'type':'string'},
                'actions':{'type':'array','items':{'oneOf':[{'type':'object','properties':{'entity_id':{'type':'string'},'action':{'type':'string'}},'required':['entity_id','action'],'additionalProperties':False},{'type':'object','properties':{'announce':{'type':'string'}},'required':['announce'],'additionalProperties':False}]}},
                'trigger':{'type':'object','description':'kind=state/entity_id/state；numeric_state/entity_id/above；time/at=HH:MM；event/entity_id/event（配置允许的事件）'},
                'conditions':{'type':'array','items':{'type':'object','description':'kind=state/entity_id/state或numeric_state/entity_id/above'}}},
                'required':['kind','name','actions'],'additionalProperties':False}
        if name=='ha_task_propose':
            properties['task']={'type':'object','properties':{'kind':{'type':'string','enum':['device','reminder','history_report']},'when':{'type':'string'},
                'text':{'type':'string'},'actions':{'type':'array','items':{'type':'object','properties':{'entity_id':{'type':'string'},'action':{'type':'string'}},'required':['entity_id','action'],'additionalProperties':False}},
                'entity_id':{'type':'string'},'period':{'type':'string'}},'required':['kind','when'],'additionalProperties':False}
        result.append({'type':'function','function':{'name':name,'description':description,
                      'parameters':{'type':'object','properties':properties,'required':list(required),'additionalProperties':False}}})
    return result


async def invoke(home,name,args,session,user_text):
    rules,tasks=home.workflows,home.tasks
    if not isinstance(args,dict):
        raise ValueError('invalid_tool_arguments')
    expected={definition['function']['name']:definition['function']['parameters'] for definition in definitions()}
    schema=expected.get(name)
    if not schema or set(args)-set(schema['properties']) or not set(schema['required'])<=set(args):
        raise ValueError('home_workflow_tool_not_allowed')
    if name=='ha_rules':
        return await rules.list_rules()
    if name=='ha_rule_propose':
        return await rules.propose_rule(session,args['rule'],args.get('rule_id'))
    if name=='ha_rule_delete':
        return await rules.propose_rule_delete(session,args['rule_id'])
    if name=='ha_rule_confirm':
        return await rules.confirm(session,args['proposal_id'],user_text)
    if name=='ha_scene_run':
        return await rules.run_scene(args['rule_id'],session)
    if name=='ha_tasks':
        return await tasks.list()
    if name=='ha_task_propose':
        return await tasks.propose(session,args['task'],args.get('task_id'))
    if name=='ha_task_delete':
        return await tasks.propose_delete(session,args['task_id'])
    return await tasks.confirm(session,args['proposal_id'],user_text)
