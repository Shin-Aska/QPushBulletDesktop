"""Bound SMS drafting with temporary SQLite, fake wallet and mock OpenAI only."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update, delete

from pbdesk.api.app import create_app
from pbdesk.config import Settings
from pbdesk.credentials import service_token
from pbdesk.services.ai import AiError, AiService, save_settings
from pbdesk.storage.database import Store
from pbdesk.storage.sms import messages


def merge(store, count, *, account='account', device='phone', thread='thread', first=0, timestamp=None):
    values=[dict(message_id=str(i),direction='outgoing' if i % 2 else 'incoming',body=f'message-{i:03d}-END',
        timestamp=1000+i if timestamp is None else timestamp,addresses=['+15555550123'],name='Alice',
        decrypted=False,image_urls=['https://private.invalid/secret'],recipients=[]) for i in range(first,first+count)]
    store.merge_sms_thread(account,device,thread,values,'test')
    with store.engine.connect() as conn:
        return [dict(row) for row in conn.execute(select(messages).where(messages.c.account_id==account,
            messages.c.device_id==device,messages.c.conversation_id==thread)
            .order_by(messages.c.timestamp,messages.c.id)).mappings()]


def request(**values):
    return dict(account_id='account',source='sms',mode='draft_reply',device_id='phone',conversation_id='thread') | values


@pytest.fixture
def local(tmp_path,monkeypatch):
    settings=Settings(tmp_path)
    store=Store(settings)
    store.set_setting('pushbullet_account_id','account')
    monkeypatch.setattr('pbdesk.services.ai.read_ai_key',lambda *a:pytest.fail('Unexpected wallet read'))
    yield settings,store
    store.close()


def cloud_ai(local,monkeypatch,calls):
    settings,store=local
    save_settings(settings,configured=True)
    monkeypatch.setattr('pbdesk.services.ai.read_ai_key',lambda *a:'fake-test-key')
    def handler(value):
        calls.append(json.loads(value.content))
        return httpx.Response(200,json={'status':'completed','output':[{'type':'message',
            'content':[{'type':'output_text','text':'A reviewed draft'}]}]})
    return AiService(settings,lambda:store,lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(handler),**kw))


def test_default_latest20_ties_chronological_candidates_and_explicit_binding(local):
    rows=merge(local[1],23,timestamp=1000)
    ai=AiService(local[0],lambda:local[1],lambda **kw:pytest.fail('cloud'))
    preview=ai.prepare(request())
    assert [row['id'] for row in preview['candidates']]==[row['id'] for row in rows[-20:]]
    assert preview['selected_ids']==[row['id'] for row in rows[-20:]]
    assert preview['has_older_messages'] and preview['count']==20
    assert preview['tone']=='Natural' and preview['expires_in']==300
    assert all(set(row)=={'id','timestamp','direction','sender','body','body_truncated'} for row in preview['candidates'])
    assert preview['candidates'][0]['sender']=='You'  # message3 outgoing
    assert preview['candidates'][1]['sender']=='Alice'
    assert 'message-000-END' not in preview['text'] and 'Alice' not in preview['text']
    assert all('private.invalid' not in row['body'] for row in preview['candidates'])
    assert '_binding' not in preview
    bound=ai.prepared[preview['context_id']]
    assert bound.request['message_ids']==preview['selected_ids']
    assert bound.request['tone']=='Natural'


def test_unchecked_messages_never_enter_payload_and_tone_is_bound(local,monkeypatch):
    rows=merge(local[1],22)
    calls=[]
    ai=cloud_ai(local,monkeypatch,calls)
    selected=[row['id'] for row in rows[-20:][::2]]
    preview=ai.prepare(request(message_ids=list(reversed(selected)),tone='  Warm and concise  '))
    assert preview['selected_ids']==selected and len(preview['candidates'])==20
    assert preview['tone']=='Warm and concise'
    unchecked=next(row for row in rows[-20:] if row['id'] not in selected)
    with local[1].writing() as conn:
        conn.execute(update(messages).where(messages.c.id==unchecked['id']).values(body='NEVER LEAK REPLACEMENT'))
    result=asyncio.run(ai.generate(preview['context_id']))
    assert result['text']=='A reviewed draft' and result['count']==10
    assert len(calls)==1 and calls[0]['input']==preview['text']
    assert 'NEVER LEAK REPLACEMENT' not in calls[0]['input']
    assert '"Warm and concise"' in calls[0]['instructions']
    assert 'writing style only' in calls[0]['instructions']
    assert 'Do not use tools or perform actions' in calls[0]['instructions']
    assert calls[0]['store'] is False and 'tools' not in calls[0]
    for row in rows:
        assert (row['body'] in calls[0]['input'])==(row['id'] in selected)


@pytest.mark.parametrize('identifiers',[[],[True],[1.0],['1'],[1,1],list(range(21)),(1,),0])
def test_invalid_selection_rejected_before_wallet_or_cloud(local,identifiers):
    merge(local[1],21)
    ai=AiService(local[0],lambda:local[1],lambda **kw:pytest.fail('cloud'))
    with pytest.raises(AiError) as exc:
        ai.prepare(request(message_ids=identifiers))
    assert exc.value.status==422 and not ai.prepared


def test_foreign_or_outside_latest20_ids_never_substitute(local):
    rows=merge(local[1],21)
    other_phone=merge(local[1],1,device='other')
    other_thread=merge(local[1],1,thread='other')
    other_account=merge(local[1],1,account='other')
    ai=AiService(local[0],lambda:local[1],lambda **kw:pytest.fail('cloud'))
    for identity in [rows[0]['id'],other_phone[0]['id'],other_thread[0]['id'],other_account[0]['id'],-1,99999]:
        with pytest.raises(AiError) as exc:
            ai.prepare(request(message_ids=[rows[-1]['id'],identity]))
        assert exc.value.status==409
    assert not ai.prepared


@pytest.mark.parametrize('tone',['','  ','x'*201,None,42])
def test_invalid_tone_is_local_error(local,tone):
    merge(local[1],1)
    ai=AiService(local[0],lambda:local[1],lambda **kw:pytest.fail('cloud'))
    with pytest.raises(AiError) as exc:
        ai.prepare(request(tone=tone))
    assert exc.value.status==422 and not ai.prepared


def test_new_unselected_arrival_and_sender_metadata_do_not_join_or_invalidate(local,monkeypatch):
    rows=merge(local[1],19)
    calls=[]
    ai=cloud_ai(local,monkeypatch,calls)
    preview=ai.prepare(request())
    merge(local[1],1,first=19)
    with local[1].writing() as conn:
        conn.execute(update(messages).where(messages.c.id==rows[0]['id']).values(name='New local display label'))
    asyncio.run(ai.generate(preview['context_id']))
    assert len(calls)==1 and calls[0]['input']==preview['text']
    assert 'message-019-END' not in calls[0]['input']
    assert 'New local display label' not in calls[0]['input']


@pytest.mark.parametrize('change',['body','delete','dropout','tone','account'])
def test_selected_stale_scope_or_tone_rejected_before_key_cloud(local,change):
    rows=merge(local[1],20)
    ai=AiService(local[0],lambda:local[1],lambda **kw:pytest.fail('cloud'))
    save_settings(local[0],configured=True)
    preview=ai.prepare(request(message_ids=[rows[0]['id']]))
    if change=='dropout':
        merge(local[1],1,first=20)
    elif change=='tone':
        ai.prepared[preview['context_id']].request['tone']='Different tone'
    elif change=='account':
        local[1].set_setting('pushbullet_account_id','other')
    else:
        with local[1].writing() as conn:
            statement=update(messages).values(body='Changed selected text') if change=='body' else delete(messages)
            conn.execute(statement.where(messages.c.id==rows[0]['id']))
    with pytest.raises(AiError):
        asyncio.run(ai.generate(preview['context_id']))
    assert preview['context_id'] not in ai.prepared


def test_large_checked_rows_all_keep_a_line_and_input_remains_bounded(local,monkeypatch):
    rows=merge(local[1],20)
    with local[1].writing() as conn:
        for row in rows:
            conn.execute(update(messages).where(messages.c.id==row['id']).values(body=row['body']+'x'*30000))
    calls=[]
    ai=cloud_ai(local,monkeypatch,calls)
    preview=ai.prepare(request())
    assert preview['count']==20 and preview['truncated'] and len(preview['text'])<=24000
    assert all(len(row['body'])==24000 and row['body_truncated'] for row in preview['candidates'])
    for row in rows:
        assert row['body'] in preview['text']
    asyncio.run(ai.generate(preview['context_id']))
    assert calls[0]['input']==preview['text']


def test_summary_sms_keeps100_rows_and_new_draft_fields_do_not_silently_apply(local):
    merge(local[1],105)
    ai=AiService(local[0],lambda:local[1],lambda **kw:pytest.fail('cloud'))
    preview=ai.prepare(request(mode='summary'))
    assert preview['count']==100 and preview['truncated'] and len(preview['text'])<=24000
    assert 'candidates' not in preview and 'tone' not in preview and 'selected_ids' not in preview
    for extra in ({'message_ids':[]},{'tone':'Friendly'}):
        with pytest.raises(AiError) as exc:
            ai.prepare(request(mode='summary',**extra))
        assert exc.value.status==422


def test_http_strict_ids_and_trimmed_tone_never_read_wallet(tmp_path,monkeypatch):
    settings=Settings(tmp_path)
    monkeypatch.setattr('pbdesk.services.ai.read_ai_key',lambda *a:pytest.fail('wallet'))
    with TestClient(create_app(settings,demo=True)) as client:
        store=client.app.state.store
        store.set_setting('pushbullet_account_id','account')
        rows=merge(store,2)
        headers={'Authorization':'Bearer '+service_token(settings,'desktop')}
        for ids in ([],[True],[1.0],['1'],[rows[0]['id']]*2,list(range(21))):
            value=client.post('/api/v1/ai/prepare',headers=headers,json=request(message_ids=ids))
            assert value.status_code==422
        preview=client.post('/api/v1/ai/prepare',headers=headers,json=request(
            message_ids=[rows[0]['id']],tone='  '+'x'*200+'  '))
        assert preview.status_code==200 and preview.json()['tone']=='x'*200
        token=preview.json()['context_id']
        assert client.post('/api/v1/ai/generate',headers=headers,json={'context_id':token,'tone':'Other'}).status_code==422
        assert token in client.app.state.ai.prepared
        for tone in (' ', 'x'*201, None, 5):
            assert client.post('/api/v1/ai/prepare',headers=headers,json=request(tone=tone)).status_code==422


def test_selection_is_rechecked_after_waiting_for_cloud_lock(local,monkeypatch):
    rows=merge(local[1],1)
    calls,keys=[],[]
    ai=cloud_ai(local,monkeypatch,calls)
    monkeypatch.setattr('pbdesk.services.ai.read_ai_key',lambda *a:keys.append('read') or 'fake-test-key')
    preview=ai.prepare(request())
    async def run():
        async with ai._cloud_lock:
            task=asyncio.create_task(ai.generate(preview['context_id']))
            for _ in range(100):
                if keys:
                    break
                await asyncio.sleep(.005)
            assert keys==['read'] and not calls
            with local[1].writing() as conn:
                conn.execute(update(messages).where(messages.c.id==rows[0]['id']).values(body='Changed while queued'))
        with pytest.raises(AiError,match='changed'):
            await task
        assert not calls and not ai.busy
        await ai.close()
    asyncio.run(run())


def test_candidate_and_selected_body_sanitization_never_exposes_links_or_credentials(local):
    rows=merge(local[1],1)
    with local[1].writing() as conn:
        conn.execute(update(messages).where(messages.c.id==rows[0]['id']).values(
            body='Look https://private.invalid/?token=secret mail@example.com api-key=HIDDEN'))
    ai=AiService(local[0],lambda:local[1],lambda **kw:pytest.fail('cloud'))
    preview=ai.prepare(request())
    for value in (preview['text'],preview['candidates'][0]['body']):
        assert all(secret not in value for secret in ('private.invalid','token=secret','mail@example.com','HIDDEN'))
        assert '[link or address omitted]' in value and '[credential omitted]' in value


def test_selected_change_while_reading_key_is_rejected_before_cloud(local,monkeypatch):
    rows=merge(local[1],1)
    calls,keys=[],[]
    ai=cloud_ai(local,monkeypatch,calls)
    preview=ai.prepare(request())
    def delayed_key(*args):
        keys.append('read')
        with local[1].writing() as conn:
            conn.execute(update(messages).where(messages.c.id==rows[0]['id']).values(body='Changed during wallet wait'))
        return 'fake-test-key'
    monkeypatch.setattr('pbdesk.services.ai.read_ai_key',delayed_key)
    with pytest.raises(AiError,match='changed'):
        asyncio.run(ai.generate(preview['context_id']))
    assert keys==['read'] and not calls and not ai.busy


def test_clipped_selected_tail_still_binds_full_sanitized_body_before_key(local):
    rows=merge(local[1],1)
    with local[1].writing() as conn:
        conn.execute(update(messages).where(messages.c.id==rows[0]['id']).values(body='x'*30000+'original tail'))
    save_settings(local[0],configured=True)
    ai=AiService(local[0],lambda:local[1],lambda **kw:pytest.fail('cloud'))
    preview=ai.prepare(request())
    assert preview['truncated'] and 'original tail' not in preview['text']
    with local[1].writing() as conn:
        conn.execute(update(messages).where(messages.c.id==rows[0]['id']).values(body='x'*30000+'changed tail'))
    assert ai.context(request())['text']==preview['text']
    with pytest.raises(AiError,match='changed'):
        asyncio.run(ai.generate(preview['context_id']))
    assert not ai.busy
