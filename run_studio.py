"""Serve the new frontend on /app/ without importing Gradio or loading a model.
Use --dry-run ONLY for integration tests; it generates test tones, not speech.
"""
from __future__ import annotations
import argparse
import os
import threading
import webbrowser
from dataclasses import replace
from pathlib import Path

def build_app(data_dir=None,dry_run=False,start_worker=True):
    from workbench.config import WorkbenchConfig
    from workbench.api import create_app
    from workbench.studio_api import attach_studio_api
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import RedirectResponse
    config=WorkbenchConfig.from_env()
    config=replace(config,dry_run=dry_run,data_dir=Path(data_dir).resolve() if data_dir else config.data_dir)
    app=create_app(config=config,start_worker=start_worker)
    # Restrict cross-origin writes to explicitly trusted local origins. A file/opaque
    # origin is allowed only with an explicitly configured API token.
    from urllib.parse import urlsplit
    from fastapi.responses import JSONResponse
    import hmac
    @app.middleware('http')
    async def check_origin(request,call_next):
        origin=request.headers.get('origin')
        if origin and request.url.path.startswith('/api/'):
            trusted=False
            try:trusted=urlsplit(origin).hostname in {'127.0.0.1','localhost','::1'} and urlsplit(origin).scheme in {'http','https'}
            except ValueError:pass
            extra=set(filter(None,os.environ.get('SOULX_ALLOWED_ORIGINS','').split(',')))
            trusted=trusted or origin in extra
            if origin=='null' and config.api_token:
                # Permit preflight only; the actual call still requires its bearer token.
                token=request.headers.get('x-api-key') or request.headers.get('authorization','').removeprefix('Bearer ')
                trusted=request.method=='OPTIONS' or hmac.compare_digest(token,config.api_token)
            if not trusted:return JSONResponse({'detail':'来源未被允许。请通过本机服务页面打开；独立文件连接需设置 API 密钥。'},status_code=403)
        return await call_next(request)

    attach_studio_api(app)
    static=Path(__file__).parent/'frontend'/'dist'
    app.mount('/app',StaticFiles(directory=static,html=True),name='studio-v3')
    @app.get('/studio3',include_in_schema=False)
    def studio():return RedirectResponse('/app/')
    return app

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=18781)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--data-dir')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    if args.host not in {'127.0.0.1','localhost','::1'} and not os.environ.get('SOULX_API_TOKEN'):
        parser.error('局域网监听必须显式设置 SOULX_API_TOKEN；请勿直接暴露到公网。')
    import uvicorn
    app=build_app(args.data_dir,args.dry_run)
    if not args.no_browser:threading.Timer(1.0,lambda:webbrowser.open(f'http://127.0.0.1:{args.port}/app/')).start()
    print('TEST TONES ONLY; NOT SPEECH' if args.dry_run else 'Real model mode. Readiness is checked before v3 submissions.')
    uvicorn.run(app,host=args.host,port=args.port,log_level='info')
