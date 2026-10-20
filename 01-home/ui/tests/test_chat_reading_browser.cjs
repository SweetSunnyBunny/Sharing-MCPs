// Run with ANAM_PLAYWRIGHT_MODULE pointing to an installed Playwright package.
// Uses an isolated headless browser and synthetic messages, never a live account.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.ANAM_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static/js/chat.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'static/css/anam.css'), 'utf8');
const html = `<div class="main-content"><div id="messages" class="messages-container" tabindex="0"></div><textarea id="input"></textarea></div>
<style>${css}\n*{animation:none!important;transition:none!important}body{margin:0}.main-content{height:420px;width:600px}.messages-container{height:360px;flex:none}.message-row{flex-shrink:0}.message-bubble{padding:12px}.message-content{white-space:pre-wrap}</style>`;
const setup = () => {
    window.App = { conversationId: 'a', currentIdentity: 'Claude', saveState() {}, ws: { streaming: false },
        _scheduleStreamRecovery() { window.recoveryPolls = (window.recoveryPolls || 0) + 1; },
        _clearStreamRecovery() { window.recoveryClears = (window.recoveryClears || 0) + 1; } };
    window.apiFetch = (...args) => fetch(...args);
    window.renderMarkdown = text => '<p>' + text + '</p>';
    window.formatTime = () => 'now';
    window.Voice = { createPlayButton: () => document.createElement('button') };
    Chat.container = document.getElementById('messages');
    Chat.input = document.getElementById('input');
    Chat.sendBtn = document.createElement('button');
    Chat._scrollPill = document.createElement('button');
    Chat.loadDraft = Chat.loadAttachmentChips = () => {};
    // Keep the real scroll, history, pagination, selection and streaming code;
    // replace decorative message composition with deterministic fixture markup.
    Chat.addMessage = function(role, content, identity, at, meta) {
        const row = document.createElement('div'); row.className = 'message-row';
        const bubble = document.createElement('div'); bubble.className = 'message-bubble';
        bubble.dataset.msgId = meta.id;
        const text = document.createElement('div'); text.className = 'message-content';
        text.textContent = content; bubble.append(text); row.append(bubble); this.container.append(row);
        this.scrollToBottom();
    };
    Chat._initReadingControls();
    window.historyPayload = (id = 'a', start = 0, end = 40) => ({conversation_id: id,
        messages: Array.from({length: end-start}, (_, n) => ({id: String(start+n), role:'assistant',
            content: `Message ${start+n}\n${'Words to select and read. '.repeat(12)}`})), has_more: start > 0});
    window.readPosition = () => ({top: Chat.container.scrollTop, following: Chat._isNearBottom,
        gap: Chat.container.scrollHeight - Chat.container.scrollTop - Chat.container.clientHeight});
};
(async () => {
    const browser = await chromium.launch({headless: true, channel: process.env.ANAM_BROWSER_CHANNEL || 'chrome'});
    try {
        const page = await browser.newPage();
        const errors = []; page.on('pageerror', e => errors.push(e.message));
        await page.route('http://anam.test/**', route => route.fulfill({contentType:'text/html', body:html}));
        const boot = async () => {
            await page.goto('http://anam.test/');
            await page.addScriptTag({content: source});
            await page.evaluate(setup);
        };
        const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        await boot();
        await page.evaluate(() => Chat.loadHistory(historyPayload())); await settle();
        assert.ok((await page.evaluate(() => readPosition())).gap <= 2, 'fresh chat follows bottom');
        // A reconnect snapshot taken before the backend finishes must retain
        // recovery state and poll again. Once the completed assistant message
        // appears after the newest user turn, it replaces the partial and stops.
        const recovery = await page.evaluate(() => {
            const pending = {conversation_id:'a',messages:[
                {id:'u1',role:'user',content:'Please keep going'},
            ],has_more:false};
            Chat._lostStreamPending = true;
            Chat._lostStreamConversationId = 'a';
            Chat._lostStreamContent = 'A partial answer';
            Chat._lostStreamIdentity = 'Claude';
            Chat.loadHistory(pending);
            const afterEarly = {
                pending: Chat._lostStreamPending,
                polls: window.recoveryPolls || 0,
                visible: Chat.container.textContent,
            };
            Chat.loadHistory({...pending,messages:[...pending.messages,
                {id:'a1',role:'assistant',content:'A partial answer, now complete.'}]});
            return {afterEarly, pending:Chat._lostStreamPending,
                clears:window.recoveryClears || 0,visible:Chat.container.textContent};
        });
        assert.equal(recovery.afterEarly.pending, true, 'early history keeps recovery armed');
        assert.equal(recovery.afterEarly.polls, 1, 'early history schedules another reconciliation');
        assert.ok(recovery.afterEarly.visible.includes('A partial answer'));
        assert.equal(recovery.pending, false, 'completed history clears recovery');
        assert.ok(recovery.clears >= 1);
        assert.ok(recovery.visible.includes('now complete'));
        await page.evaluate(() => {
            Chat.container.innerHTML = '';
            localStorage.removeItem('anam-reading-a');
            Chat.loadHistory(historyPayload());
        });
        await settle();
        // Queue a follow, then scroll up before the frame runs (the original race).
        const held = await page.evaluate(() => {
            Chat.scrollToBottom();
            Chat.container.dispatchEvent(new WheelEvent('wheel', {deltaY:-20}));
            Chat.container.scrollTop -= 20;
            Chat.container.dispatchEvent(new Event('scroll'));
            return Chat.container.scrollTop;
        });
        await settle();
        assert.equal((await page.evaluate(() => readPosition())).top, held);
        assert.equal((await page.evaluate(() => readPosition())).following, false);
        await page.evaluate(() => {
            for(let i=0;i<25;i++) { Chat.container.lastChild.querySelector('.message-content').append('\nMore streaming words'); Chat.scrollToBottom(); }
        }); await settle();
        assert.equal((await page.evaluate(() => readPosition())).top, held, 'stream growth respects upward intent');
        // Touch intent must cancel a queued callback too.
        await page.evaluate(() => { Chat.scrollToBottom(true); }); await settle();
        await page.evaluate(() => {
            Chat.scrollToBottom();
            for (const [name,y] of [['touchstart',100],['touchmove',150]]) {
                const e=new Event(name); Object.defineProperty(e,'touches',{value:[{clientY:y}]}); Chat.container.dispatchEvent(e);
            }
        }); await settle();
        assert.equal((await page.evaluate(() => readPosition())).following, false);
        // Save a mid-history bookmark, reload, and verify the message/offset.
        await page.evaluate(() => { Chat.container.scrollTop=700; Chat.container.dispatchEvent(new Event('scroll')); Chat.saveReadingPosition(); });
        const saved = await page.evaluate(() => Chat._readSavedPosition('a'));
        await page.evaluate(() => { Chat.showLoadingState(); Chat.loadHistory(historyPayload()); }); await settle();
        assert.equal((await page.evaluate(() => readPosition())).top, saved.top, 'reconnect holds position');
        await boot(); await page.evaluate(() => Chat.loadHistory(historyPayload())); await settle();
        assert.equal((await page.evaluate(() => readPosition())).top, saved.top, 'full page reload holds position');
        // Late media above the anchor must keep that same message at its offset.
        await page.evaluate(() => Chat.container.firstChild.style.height='600px'); await settle();
        const offset = await page.evaluate(id => {
            const el=[...Chat.container.querySelectorAll('[data-msg-id]')].find(el=>el.dataset.msgId===id);
            return el.getBoundingClientRect().top-Chat.container.getBoundingClientRect().top;
        },saved.id);
        assert.ok(Math.abs(offset-saved.offset)<=1, 'late media preserves anchor');
        // Per-conversation storage is tied to the old DOM even if App switched first.
        await page.evaluate(() => { App.conversationId='b'; Chat.clearMessages(); Chat.loadHistory(historyPayload('b')); }); await settle();
        assert.ok((await page.evaluate(() => readPosition())).gap<=2, 'new conversation starts at bottom');
        await page.evaluate(() => Chat.loadHistory(historyPayload('a'))); await settle();
        assert.ok((await page.evaluate(() => readPosition())).top > 0, 'returning conversation restores its bookmark');
        // Reopening at a former bottom must not skip messages received while away.
        await page.evaluate(() => { Chat.scrollToBottom(true); }); await settle();
        await page.evaluate(() => { Chat.saveReadingPosition(); Chat.loadHistory(historyPayload('a',0,45)); }); await settle();
        assert.ok((await page.evaluate(() => readPosition())).gap>100, 'away messages do not pull bookmark down');
        // Bookmark outside the initial page loads older history via the real URL.
        await page.route('http://anam.test/api/messages/conversations/a?**', async route => {
            const params=new URL(route.request().url()).searchParams;
            assert.equal(params.get('offset'),'10');
            const data=await page.evaluate(() => ({messages:historyPayload('a',0,30).messages}));
            await route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
        });
        await page.evaluate(() => {
            Chat.clearMessages();
            localStorage.setItem('anam-reading-a',JSON.stringify({top:500,id:'5',offset:-10,bottom:false}));
            Chat.loadHistory(historyPayload('a',30,40));
        });
        await page.waitForFunction(() => !Chat._restoringPosition); await settle();
        assert.equal(await page.evaluate(() => Chat._historyOffset),40);
        assert.ok(await page.evaluate(() => !!Chat.container.querySelector('[data-msg-id="5"]')));
        // Native forward/backward selections cannot escape their starting bubble.
        for (const backwards of [false,true]) {
            const selected=await page.evaluate(backwards => {
                const texts=Chat.container.querySelectorAll('.message-content');
                const origin=texts[5].firstChild;
                const other=texts[backwards?0:20].firstChild;
                getSelection().setBaseAndExtent(origin,10,other,20);
                Chat._containMessageSelection();
                return {text:getSelection().toString(),focusInside:texts[5].contains(getSelection().focusNode)};
            },backwards);
            assert.equal(selected.focusInside,true);
            assert.ok(!selected.text.includes('Message 20'));
        }
        // Selection starting after scheduling must also block the queued scroll.
        const selectedTop = await page.evaluate(() => {
            getSelection().removeAllRanges(); Chat._isNearBottom=true; Chat.scrollToBottom();
            const text=Chat.container.querySelector('.message-content').firstChild;
            getSelection().setBaseAndExtent(text,1,text,8);
            return Chat.container.scrollTop;
        }); await settle();
        assert.equal((await page.evaluate(() => readPosition())).top,selectedTop);
        await page.evaluate(() => { getSelection().removeAllRanges(); Chat.scrollToBottom(true); }); await settle();
        assert.ok((await page.evaluate(() => readPosition())).gap<=2, 'explicit jump still works');
        // A still-connected stream must hold the last reading position on return.
        const beforeHidden=await page.evaluate(() => {
            Object.defineProperty(document,'hidden',{configurable:true,value:true});
            document.dispatchEvent(new Event('visibilitychange'));
            const top=Chat.container.scrollTop;
            Chat.container.lastChild.querySelector('.message-content').append('\n'.repeat(30)+'away text');
            Chat.scrollToBottom();
            Object.defineProperty(document,'hidden',{configurable:true,value:false});
            document.dispatchEvent(new Event('visibilitychange'));
            Chat.scrollToBottom();
            return top;
        }); await settle();
        assert.equal((await page.evaluate(() => readPosition())).top,beforeHidden);
        // Real streaming renderer keeps selected text nodes stable, including
        // the final event; releasing the selection paints the completed answer.
        await page.evaluate(() => {
            Chat.currentStreamEl = Chat.container.lastChild.querySelector('.message-content');
            Chat.currentStreamContent = Chat.currentStreamEl.textContent + ' final words';
            Chat.isStreaming = true;
            const node=Chat.currentStreamEl.firstChild;
            getSelection().setBaseAndExtent(node,1,node,8);
            window.selectedBefore=getSelection().toString();
            Chat._paintStreamFrame(performance.now());
        });
        assert.equal(await page.evaluate(() => getSelection().toString()), await page.evaluate(() => selectedBefore));
        await page.evaluate(() => Chat.onStreamEnd({full_content:Chat.currentStreamContent}));
        assert.equal(await page.evaluate(() => getSelection().toString()), await page.evaluate(() => selectedBefore));
        await page.evaluate(() => getSelection().removeAllRanges());
        await settle();
        assert.ok(await page.evaluate(() => Chat.container.lastChild.querySelector('.message-content').textContent.endsWith(' final words')));
        // Decisions use the original conversation even after the active chat changes.
        const approvalResult = await page.evaluate(async () => {
            window.approvalPosts = [];
            window.apiFetch = async (url, options) => {
                approvalPosts.push({url, body:JSON.parse(options.body)});
                return {ok:true,json:async()=>({ok:true})};
            };
            App.currentIdentity = 'Claude'; App.conversationId = 'original-chat';
            Chat._showCodexApproval({provider:'codex',approval_id:'test-approval',identity:'Claude',
                conversation_id:'original-chat',message:'A <script> literal request',
                details:{requestedSchema:{type:'object'}},decisions:['accept','decline','cancel']});
            const row = Chat.container.lastChild;
            row.querySelector('textarea').value = 'invalid JSON';
            App.currentIdentity = 'River'; App.conversationId = 'other-chat';
            row.querySelectorAll('button')[1].click();
            await new Promise(resolve=>setTimeout(resolve,0));
            return {posts:approvalPosts,scripts:row.querySelectorAll('script').length};
        });
        assert.equal(approvalResult.scripts,0);
        assert.equal(approvalResult.posts.length,1);
        assert.deepEqual(approvalResult.posts[0].body,{decision:'decline',content:null,identity:'Claude',conversation_id:'original-chat'});
        assert.deepEqual(errors,[]);
        console.log('PASS: streaming race, touch intent, reconnect, reload, late media, conversation switching, away messages, older bookmark pagination, bidirectional selection, queued selection, explicit jump');
    } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});



