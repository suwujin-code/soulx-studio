"""Managed local server. Native launcher owns the lifecycle, not a terminal."""
from __future__ import annotations
import argparse
import hmac
import os
import threading
from pathlib import Path


def build_managed_app(data_dir, instance: str, control_token: str, port: int):
    from fastapi import Header, HTTPException
    from run_studio import build_app
    app=build_app(data_dir=data_dir, dry_run=False)
    server_box={}
    def auth(value):
        if not control_token or not hmac.compare_digest(value or '',control_token):
            raise HTTPException(401,'Invalid launcher control token')
    @app.get('/api/desktop/instance')
    def identity(x_soulx_control: str|None=Header(None)):
        auth(x_soulx_control)
        return {'product':'SoulXStudio','instance':instance,'port':port,'dry_run':False}
    @app.post('/api/desktop/shutdown')
    def shutdown(x_soulx_control: str|None=Header(None)):
        auth(x_soulx_control)
        if not app.state.store.pause_when_idle():
            raise HTTPException(409,'当前仍在生成，请等任务停止后再退出服务。')
        # Queued jobs stay persisted; do not claim another after this response.
        if 'server' in server_box:
            server_box['server'].should_exit=True
        return {'stopping':True,'data_preserved':True}
    return app,server_box


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--data-dir',required=True)
    p.add_argument('--port',type=int,default=18781)
    a=p.parse_args()
    instance=os.environ['SOULX_DESKTOP_INSTANCE']
    token=os.environ['SOULX_CONTROL_TOKEN']
    # Never manufacture a speech result when dependencies are missing.
    app,box=build_managed_app(a.data_dir,instance,token,a.port)
    import uvicorn
    box['server']=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=a.port,log_level='info'))
    box['server'].run()
