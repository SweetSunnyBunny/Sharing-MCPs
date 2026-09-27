"""Rank tool intent while preserving exact names, schema constraints and provenance."""
import re
from jsonschema import Draft202012Validator

_STOP = set('a an the to for of my me please can you could would i want need use with in on'.split())
_GROUPS = [set(s.split()) for s in (
    'memory memories remember recall recollect qualia',
    'picture pictures image images photo photos screenshot',
    'message messages dm discord send reply',
    'file files document documents filesystem',
    'conversation conversations chat history transcript transcripts',
    'tools tool discover discovery capabilities catalog',
    'sound audio voice speech speak tts',
)]

def tokens(text):
    return set(re.findall(r'[a-z0-9]+', text.casefold())) - _STOP

def score(tool, query):
    if not query.strip():
        return 1
    name = tool['name'].casefold()
    if query.casefold().strip() == name:
        return 10000
    wanted = tokens(query)
    names = tokens(name + ' ' + tool['server'])
    desc = tokens(tool['description'])
    total = 0
    for word in wanted:
        aliases = {word}
        for group in _GROUPS:
            if word in group:
                aliases |= group
        if word in names:
            total += 30
        elif names & aliases:
            total += 12
        elif word in desc:
            total += 6
        elif desc & aliases:
            total += 2
    if query.casefold().strip() in name:
        total += 100
    return total

def _example(schema):
    if 'examples' in schema and schema['examples']:
        return schema['examples'][0]
    if 'default' in schema:
        return schema['default']
    if 'const' in schema:
        return schema['const']
    if schema.get('enum'):
        return schema['enum'][0]
    kind = schema.get('type')
    if kind == 'object':
        return {key:_example(schema.get('properties',{}).get(key,{})) for key in schema.get('required',[])}
    if kind == 'array':
        return [_example(schema.get('items',{})) for _ in range(min(schema.get('minItems',0),10))]
    if kind == 'integer' or kind == 'number':
        return schema.get('minimum',0)
    if kind == 'boolean':
        return False
    return 'example'

def present(tool, include_schema=True):
    schema = tool.get('inputSchema',{})
    result = dict(tool)
    result['required_arguments'] = schema.get('required',[])
    try:
        sample = _example(schema)
        valid = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).is_valid(sample)
    except Exception:
        valid = False  # A broken or external schema reference must not hide the catalog.
    if valid:
        result['example_arguments'] = sample
        result['example_note'] = 'Schema-valid illustration only; substitute real values and your active identity.'
    if not include_schema:
        result.pop('inputSchema',None)
        result['description'] = result['description'][:400]
    return result
