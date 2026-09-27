"""Pending native Codex requests, scoped to their live client and Anam conversation."""
import time
import uuid
from jsonschema import Draft202012Validator

_pending = {}
_COMMANDS = {'item/commandExecution/requestApproval', 'execCommandApproval'}
_FILES = {'item/fileChange/requestApproval', 'applyPatchApproval'}
_INPUTS = {'item/tool/requestUserInput', 'tool/requestUserInput'}
_SUPPORTED = _COMMANDS | _FILES | _INPUTS | {'item/permissions/requestApproval', 'mcpServer/elicitation/request'}

def register(client, request, identity, conversation):
    method = request['method']
    if method not in _SUPPORTED:
        client.respond_error(request['id'], -32601, f'Unsupported app-server request: {method}')
        return None
    token = uuid.uuid4().hex
    params = request.get('params') or {}
    event = {'type':'approval_required', 'provider':'codex', 'approval_id':token,
             'identity':identity, 'conversation_id':conversation, 'method':method,
             'message':params.get('reason') or params.get('message') or 'Codex needs your decision to continue.',
             'details':{k:params[k] for k in ('command','cwd','changes','permissions','requestedSchema','questions','url','networkApprovalContext') if k in params},
             'decisions':['accept','acceptForSession','decline','cancel'] if method in _COMMANDS | _FILES | {'item/permissions/requestApproval'} else ['accept','decline','cancel']}
    _pending[token] = {'client':client,'request':request,'identity':identity,
                       'conversation':conversation,'created':time.time(),'event':event}
    return event

def resolve(token, decision, identity, conversation, content=None):
    item = _pending.get(token)
    if not item or not item['client'].is_alive():
        _pending.pop(token,None)
        raise ValueError('This request is no longer pending')
    if item['identity'].casefold() != identity.casefold() or item['conversation'] != conversation:
        raise ValueError('This request belongs to another conversation')
    if decision not in item['event']['decisions']:
        raise ValueError('Unsupported approval decision')
    method = item['request']['method']
    params = item['request'].get('params') or {}
    accepted = decision in ('accept','acceptForSession')
    if method in _COMMANDS | _FILES:
        response = {'decision':decision}
    elif method == 'item/permissions/requestApproval':
        response = {'permissions':params.get('permissions',{}) if accepted else {},
                    'scope':'session' if decision == 'acceptForSession' else 'turn'}
    elif method in _INPUTS:
        answers = content if accepted else {}
        if not isinstance(answers,dict):
            raise ValueError('Answers must be an object')
        questions = params.get('questions') or []
        for question in questions if accepted else []:
            answer = answers.get(question['id'],{}).get('answers')
            if not isinstance(answer,list) or not answer or not all(isinstance(x,str) for x in answer):
                raise ValueError('Answer each question before continuing')
        response = {'answers':answers}
    else:
        if accepted and params.get('requestedSchema'):
            if not Draft202012Validator(params['requestedSchema']).is_valid(content):
                raise ValueError('The response does not match the requested form')
        response = {'action':'accept' if accepted else 'cancel' if decision=='cancel' else 'decline',
                    'content':content if accepted else None}
    item['client'].respond(item['request']['id'], response)
    _pending.pop(token,None)
    return {'ok':True}

def pending(identity='', conversation=''):
    return [item['event'] for item in _pending.values() if item['client'].is_alive()
            and (not identity or item['identity'].casefold()==identity.casefold())
            and (not conversation or item['conversation']==conversation)]

def clear_client(client):
    for token,item in list(_pending.items()):
        if item['client'] is client:
            _pending.pop(token,None)


def clear_request(client, request_id):
    for token, item in list(_pending.items()):
        if item['client'] is client and item['request']['id'] == request_id:
            _pending.pop(token, None)
