"""Native wire contracts with no provider execution."""
import copy
import json
import pytest
from factory.agent_protocol import build_request,decode,protocol


def profile(provider):
    return {'provider':provider,'model':'fixture','profile':'qwen','thinking':True,'response_format':'prompt',
            'context_tokens':32768,'base_url':'http://127.0.0.1:8090/v1'}


def assistant(arguments='{}',**extra):
    return {'role':'assistant','content':None,'reasoning_content':'KEEP_ALL_PRIOR_REASONING',
        'tool_calls':[{'id':'first','type':'function','function':{'name':'check_artifact','arguments':arguments}}],**extra}


def test_deepseek_full_assistant_reasoning_survives_all_tool_turns():
    p=profile('deepseek');messages=[{'role':'user','content':'facts'},assistant(),
        {'role':'tool','tool_call_id':'first','name':'check_artifact','content':'result'},assistant()]
    _,body=build_request(p,messages,[],128,.2)
    assert body['thinking']=={'type':'enabled'}
    assert body['messages'][1]==messages[1] and body['messages'][3]==messages[3]
    assert body['messages'][2]=={'role':'tool','tool_call_id':'first','content':'result'}
    assert 'response_format' not in body
    assert messages[2]['name']=='check_artifact'


def test_ollama_native_arguments_and_tool_observations_round_trip():
    p=profile('ollama_native');p['base_url']='http://127.0.0.1:11434'
    route,body=build_request(p,[assistant(),{'role':'tool','tool_call_id':'first','name':'check_artifact','content':'result'}],[],128,.2)
    assert route=='/api/chat' and 'tool_choice' not in body
    assert body['messages'][0]['tool_calls'][0]['function']['arguments']=={}
    assert body['messages'][0]['thinking']=='KEEP_ALL_PRIOR_REASONING'
    assert body['messages'][1]=={'role':'tool','tool_name':'check_artifact','content':'result'}
    result=decode(p,{'model':'fixture','done':True,'done_reason':'stop','message':{'role':'assistant','thinking':'private','tool_calls':[{'function':{'name':'plan','arguments':{'steps':['one']}}}]},'eval_count':3},1)
    assert result['actions'][0]['arguments']=={'steps':['one']}
    assert result['assistant']['thinking']=='private'


@pytest.mark.parametrize('mutation',[
    lambda r:r.update(model='wrong-model'),
    lambda r:r['choices'][0].update(finish_reason='length'),
    lambda r:r['choices'][0]['message'].update(tool_calls=[]),
    lambda r:r['choices'][0]['message']['tool_calls'][0].pop('id'),
    lambda r:r['choices'][0]['message']['tool_calls'][0]['function'].update(arguments='{"value":NaN}'),
    lambda r:r['choices'][0]['message']['tool_calls'][0]['function'].update(arguments='[]'),
])
def test_incomplete_or_invalid_native_wire_is_not_treated_as_success(mutation):
    response={'model':'fixture','choices':[{'finish_reason':'tool_calls','message':assistant()}]}
    mutation(response)
    with pytest.raises((ValueError,TypeError)):decode(profile('deepseek'),response,1)


def test_protocol_selection_has_no_silent_native_to_json_repost():
    assert protocol(profile('llama_cpp'),'auto')=='native'
    assert protocol(profile('anthropic'),'auto')=='structured'
    with pytest.raises(ValueError):protocol(profile('anthropic'),'native')
