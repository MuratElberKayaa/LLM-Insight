"""Read-only screenshots of the running local dashboard using headless Edge CDP."""
import base64
import argparse
import json
import subprocess
import time
import urllib.request
from pathlib import Path
import websocket

ROOT=Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument('--prefix',default='')
parser.add_argument('--urls',type=Path)
args=parser.parse_args()
PORT=19357
edge=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
proc=subprocess.Popen([edge,'--headless=new','--disable-gpu','--no-first-run',
    '--no-default-browser-check',f'--remote-debugging-port={PORT}',
    '--remote-allow-origins=http://localhost:'+str(PORT),
    '--user-data-dir='+str(ROOT/'.edge-profile'),'about:blank'],
    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
    creationflags=subprocess.CREATE_NO_WINDOW)
try:
    for _ in range(80):
        try:
            tabs=json.load(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json',timeout=1))
            break
        except Exception: time.sleep(.25)
    else: raise RuntimeError('Headless Edge did not start')
    ws=websocket.create_connection(next(t['webSocketDebuggerUrl'] for t in tabs if t['type']=='page'),origin=f'http://localhost:{PORT}')
    seq=0
    def call(method,params=None):
        global seq
        seq+=1
        ws.send(json.dumps({'id':seq,'method':method,'params':params or {}}))
        while True:
            r=json.loads(ws.recv())
            if r.get('id')==seq:
                if 'error' in r: raise RuntimeError(r['error'])
                return r.get('result',{})
    def js(expr): return call('Runtime.evaluate',{'expression':expr,'returnByValue':True}).get('result',{}).get('value')
    call('Page.enable')
    call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1000,'deviceScaleFactor':1.5,'mobile':False})
    urls=json.loads((args.urls or ROOT/'capture-urls.json').read_text())
    if args.urls is None:
        urls['experiment']='http://127.0.0.1:3001/experiments/58?project=1'
    manifest={}
    for name,url in urls.items():
        call('Page.navigate',{'url':url})
        for _ in range(100):
            time.sleep(.2)
            ready=js("document.readyState==='complete' && document.body.innerText.includes('Backend ready') && !document.body.innerText.includes('Loading overview')")
            if ready: break
        time.sleep(1)
        txt=js('document.body.innerText')
        (ROOT/(args.prefix+name+'-browser.txt')).write_text(txt,encoding='utf-8')
        layout=call('Page.getLayoutMetrics')['cssContentSize']
        shot=call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':True,
            'clip':{'x':0,'y':0,'width':1440,'height':min(layout['height'],10000),'scale':1}})
        (ROOT/'assets'/(args.prefix+name+'-full.png')).write_bytes(base64.b64decode(shot['data']))
        # Record headings/sections so that crops can preserve actual content and labels.
        boxes=js("JSON.stringify([...document.querySelectorAll('h1,h2,h3,section,table')].map(e=>{let r=e.getBoundingClientRect();return {tag:e.tagName,text:e.innerText.slice(0,130),x:r.x,y:r.y,w:r.width,h:r.height}}))")
        manifest[name]={'url':url,'height':layout['height'],'boxes':json.loads(boxes)}
        print(name,layout['height'])
    (ROOT/(args.prefix+'capture-manifest.json')).write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    call('Browser.close')
finally:
    try: proc.wait(timeout=10)
    except subprocess.TimeoutExpired: proc.terminate()
