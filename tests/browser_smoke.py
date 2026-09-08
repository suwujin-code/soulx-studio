"""Real Chromium DOM + live HTTP API tests using allowed set_content.
No browser policy is changed. Normal page navigation is administratively blocked.
No UI/state/storage/audio backend is mocked. SQLite service storage is exercised;
native IndexedDB is not tested in this opaque document context.
"""
import json,time,os
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'test-results';OUT.mkdir(exist_ok=True)
HTML=(ROOT/'frontend/dist/SoulX_前端可交互预览.html').read_text()
RESULTS=[]

def check(name,fn):
    fn();RESULTS.append({'name':name,'passed':True});print('PASS',name,flush=True)

def nav(page,name):page.locator(f'.sidebar [data-action="nav"][data-page="{name}"]').first.click()
def state(page):return page.evaluate('SoulXApp.getState()')
def ui(page):return page.evaluate('SoulXApp.getUI()')
def waitsave(page):page.wait_for_function("SoulXApp.getUI().saveStatus==='saved'",timeout=10000)
def current(page):s=state(page);return next(p for p in s['projects'] if p['id']==s['activeProjectId'])
def connect(page):
    nav(page,'settings');page.locator('#base-url').fill('http://127.0.0.1:18781');page.locator('#api-token').fill(os.environ.get('SOULX_BROWSER_TEST_TOKEN','frontend-integration-test-only'))
    page.locator('[data-action="connect"]').click();page.wait_for_function('SoulXApp.getUI().connected',timeout=15000)
    page.locator('[data-action="enable-service-storage"]').first.click()
    page.wait_for_timeout(200)
    if page.locator('[data-action="confirm-service-storage"]').count():page.locator('[data-action="confirm-service-storage"]').click()
    page.wait_for_function("SoulXApp.getUI().storageMode==='service'&&SoulXApp.getUI().saveStatus==='saved'",timeout=10000)

with sync_playwright() as engine:
    browser=engine.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
    browser.on('disconnected',lambda:None)
    page=browser.new_page(viewport={'width':1512,'height':1080},device_scale_factor=1)
    page.set_default_timeout(10000)
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content(HTML,wait_until='load');page.wait_for_function('!!window.SoulXApp && !!SoulXApp.getState()')
    check('initial editor has five editable sentences',lambda:expect(page.locator('[data-testid="sentence"]')).to_have_count(5))
    check('opaque browser storage is honestly marked temporary',lambda:expect(page.locator('[data-save-indicator]')).to_contain_text('临时预览'))
    check('generation disabled without durable storage/service',lambda:expect(page.locator('[data-action="generate-all"]')).to_be_disabled())
    check('connect real HTTP test backend and enable SQLite storage',lambda:connect(page))
    nav(page,'editor')
    # New isolated project: do not depend on previous test run state.
    page.locator('[data-action="new-project"]').first.click();page.locator('#new-project-name').fill('广州街角 · 探店旁白（联调测试）');page.locator('[data-action="confirm-new-project"]').click();waitsave(page)
    check('create project by real modal',lambda:expect(page.locator('.project-title h1')).to_have_text('广州街角 · 探店旁白（联调测试）'))
    first=page.locator('textarea[data-text-id]').first
    original='各位朋友，大家好！今日一齊用粵語介紹廣州。'
    first.fill(original);waitsave(page)
    assert current(page)['segments'][0]['text']==original
    check('editor persists real text to SQLite',lambda:expect(first).to_have_value(original))
    page.locator('[data-action="open-import"]').first.click()
    page.locator('#import-text').fill('一杯熱奶茶，一籠新鮮點心。\n慢慢食，慢慢享受生活。\n下次一齊去探索更多小店。')
    page.locator('[data-action="confirm-import"]').click();waitsave(page)
    check('import appends, never replaces existing sentences',lambda:expect(page.locator('[data-testid="sentence"]')).to_have_count(4))
    sid=current(page)['segments'][0]['id']
    page.locator(f'[data-action="toggle-lock"][data-id="{sid}"]').click();waitsave(page)
    check('locked sentence is read-only',lambda:expect(page.locator('#text-'+sid)).to_have_attribute('readonly',''))
    page.locator('[data-action="open-batch"]').click();page.locator('#batch-pause').fill('0.6');page.locator('[data-action="apply-batch-pause"]').click();waitsave(page)
    assert current(page)['segments'][0]['pause']==.35 and current(page)['segments'][1]['pause']==.6
    check('batch property edits skip locked sentences',lambda:None)
    page.locator(f'[data-action="toggle-lock"][data-id="{sid}"]').click();waitsave(page)
    page.locator('[data-action="generate-all"]').click()
    page.wait_for_function("SoulXApp.getState().projects.find(p=>p.id===SoulXApp.getState().activeProjectId).segments.every(s=>s.takes.length&&s.takes.at(-1).status==='completed')",timeout=20000)
    check('four real API jobs complete as TEST tones only',lambda:None)
    snap=current(page);assert all(s['takes'][-1]['test'] for s in snap['segments'])
    oldid=snap['segments'][0]['takes'][0]['jobId']
    page.locator('#text-'+sid).fill('各位朋友，大家好！呢一句係修改後嘅新版本。');waitsave(page)
    # Move focus without editing selection so derived stale-state badge refreshes.
    page.locator('[data-action="select-sentence"][data-id="'+sid+'"]').click()
    check('changed sentence flags stale audio instead of delivering it',lambda:expect(page.locator('[data-segment="'+sid+'"] .badge')).to_contain_text('待重做'))
    page.locator('[data-action="generate-selected"]').click()
    page.wait_for_function("s=>{const p=SoulXApp.getState().projects.find(p=>p.id===SoulXApp.getState().activeProjectId);return p.segments.find(x=>x.id===s).takes.length===2&&p.segments.find(x=>x.id===s).takes[1].status==='completed'}",arg=sid,timeout=20000)
    snap=current(page);assert snap['segments'][0]['takes'][0]['jobId']==oldid and snap['segments'][0]['takes'][1]['jobId']!=oldid
    check('regeneration preserves old job and adds V2',lambda:expect(page.locator('.take-choice')).to_have_count(2))
    check('test audio cannot be marked accepted',lambda:expect(page.locator('[data-action="accept-take"]')).to_be_disabled())
    page.locator('[data-action="play-take"][data-segment-id="'+sid+'"]').click()
    page.wait_for_function('SoulXApp.getUI().playback.duration>0')
    check('player decodes real generated test audio and waveform',lambda:expect(page.locator('#audio-waveform')).to_be_visible())
    with page.expect_download() as dl:
        page.locator('[data-action="open-export"]').click();page.locator('[data-action="export-audio"][data-format="zip"]').click()
    download=dl.value;download.save_as(OUT/'BROWSER_TEST_ONLY_export.zip')
    assert '仅测试音' in download.suggested_filename
    check('real ZIP export is explicitly labeled test audio',lambda:None)
    # Layout all pages; no placeholder data silently seeded as generated jobs.
    for dest in ['home','projects','voices','tasks','works','templates','settings','backup','editor']:
        nav(page,dest);page.wait_for_timeout(60)
        assert page.locator('main').inner_text().strip()
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        check('desktop layout '+dest,lambda:None)
    nav(page,'voices')
    page.locator('[data-action="preview-voice"][data-id="reference-hk"]').first.click()
    page.wait_for_function("SoulXApp.getUI().playback.kind.includes('参考音频')")
    check('reference playback not mislabeled as new synthesis',lambda:None)
    page.wait_for_timeout(7000)
    nav(page,'home');page.wait_for_timeout(400);page.screenshot(animations='disabled',path=str(OUT/'01_创作首页_实际前端.png'),full_page=True)
    nav(page,'editor');page.wait_for_timeout(700);page.screenshot(animations='disabled',path=str(OUT/'02_逐句编辑_实际前端.png'),full_page=True)
    nav(page,'voices');page.screenshot(animations='disabled',path=str(OUT/'03_音色资产_实际前端.png'),full_page=True)
    nav(page,'tasks');page.screenshot(animations='disabled',path=str(OUT/'04_批量任务_测试模式.png'),full_page=True)
    with page.expect_download() as dl:page.locator('[data-action="nav"][data-page="backup"]').first.click();page.locator('[data-action="export-workspace"]').first.click()
    dl.value.save_as(OUT/'browser-workspace-test.json')
    before=current(page)['name']
    # Open a new document/window: fetch the persisted workspace from real SQLite.
    fresh=browser.new_page(viewport={'width':1440,'height':1000});fresh.on('pageerror',lambda e:errors.append(str(e)))
    fresh.set_content(HTML,wait_until='load');fresh.wait_for_function('!!window.SoulXApp && !!SoulXApp.getState()');connect(fresh)
    assert current(fresh)['name']==before and current(fresh)['segments'][0]['takes'][0]['jobId']==oldid
    check('new browser window restores real SQLite workspace and versions',lambda:None)
    # Conflicting windows may not silently overwrite each other.
    nav(fresh,'editor');fresh.locator('textarea[data-text-id]').last.fill('第二个窗口嘅修改。');waitsave(fresh)
    nav(page,'editor');page.locator('textarea[data-text-id]').last.fill('第一个窗口嘅过期修改。')
    page.wait_for_function("SoulXApp.getUI().saveStatus==='error'",timeout=10000)
    check('stale window detects revision conflict, refuses overwrite',lambda:expect(page.locator('[data-save-indicator]')).to_contain_text('保存失败'))
    fresh.wait_for_timeout(7000)
    fresh.set_viewport_size({'width':390,'height':844});fresh.wait_for_timeout(500)
    fresh.wait_for_function("document.querySelector('.sidebar').getBoundingClientRect().right<=0")
    check('mobile viewport has no page horizontal overflow',lambda:None)
    assert fresh.evaluate('document.documentElement.scrollWidth<=window.innerWidth'),fresh.evaluate('document.documentElement.scrollWidth')
    fresh.screenshot(animations='disabled',path=str(OUT/'05_手机编辑_实际前端.png'),full_page=True)
    fresh.locator('[data-action="mobile-nav"]').first.click();expect(fresh.locator('.sidebar')).to_have_class('sidebar open')
    fresh.locator('.sidebar [data-page="voices"]').click()
    assert ui(fresh)['page']=='voices' and not ui(fresh)['mobileNav']
    assert fresh.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
    check('mobile drawer navigation and voice grid work',lambda:None)
    # Validate escaping/backup input and parser with actual compiled functions, no application mutation.
    result=fresh.evaluate("""()=>{const d=SoulXApp.getState();d.voices[0].color='\" onclick=\"alert(1)';try{SoulXApp.validateWorkspace(d);return false}catch{return true}}""")
    assert result;check('backup rejects unsafe attribute injection',lambda:None)
    assert fresh.evaluate("SoulXApp.parseCSV('text,title\\n\"你好，朋友\",a\\n\"第一行\\n第二行\",b')")==['你好，朋友','第一行\n第二行']
    check('CSV handles quoted commas and embedded newlines',lambda:None)
    assert not errors,errors
    check('no uncaught browser JavaScript errors',lambda:None)
    browser.close()
(OUT/'browser-results.json').write_text(json.dumps({'mode':'Chromium set_content + real HTTP API + SQLite persistence; normal navigation blocked by administrator; no mocks; test tones only','checks':RESULTS,'count':len(RESULTS)},ensure_ascii=False,indent=2))
print('TOTAL',len(RESULTS))
