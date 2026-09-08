"""Start the prebuilt frontend. Standard library only; no model or npm required."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial
from pathlib import Path
import argparse,threading,webbrowser,sys

def main():
    parser=argparse.ArgumentParser(description='SoulX frontend-only preview: no speech synthesis')
    parser.add_argument('--port',type=int,default=18782)
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    folder=Path(__file__).resolve().parent/'frontend'/'dist'
    if not (folder/'index.html').exists():raise SystemExit('缺少预编译前端，请使用完整交付包。')
    handler=partial(SimpleHTTPRequestHandler,directory=str(folder))
    try:server=ThreadingHTTPServer(('127.0.0.1',args.port),handler)
    except OSError as e:raise SystemExit(f'无法监听 127.0.0.1:{args.port}。可能已有预览运行；关闭旧进程或指定 --port。\n{e}')
    url=f'http://127.0.0.1:{args.port}/'
    print(f'SoulX 前端预览：{url}\n只在本机开放；不会生成语音。关闭此窗口或 Ctrl+C 停止。',flush=True)
    if not args.no_browser:threading.Timer(.4,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
if __name__=='__main__':main()
