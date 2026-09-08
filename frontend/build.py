"""Assemble precompiled assets and a single-file offline UI. Does not need npm."""
from pathlib import Path
import shutil
root=Path(__file__).resolve().parent
dist=root/'dist';dist.mkdir(exist_ok=True)
js=(dist/'studio.js').read_text()
css=(root/'src'/'studio.css').read_text()
shutil.copy2(root/'src'/'studio.css',dist/'studio.css')
head='''<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#f6f8fc"><meta name="description" content="SoulX 本地配音工作台：逐句编辑、批量生产、声音资产。"><title>SoulX Studio · 粤语 AI 配音工作台</title>'''
body='<div id="app"><p style="padding:40px;font-family:sans-serif">正在打开 SoulX 工作台…</p></div><noscript>请启用 JavaScript。工作台无需第三方 CDN。</noscript>'
(dist/'index.html').write_text(head+'<link rel="stylesheet" href="./studio.css"></head><body>'+body+'<script defer src="./studio.js"></script></body></html>')
(dist/'SoulX_前端可交互预览.html').write_text(head+'<style>'+css+'</style></head><body>'+body+'<script>'+js.replace('</script','<\\/script')+'</script></body></html>')
print('Built: index.html, studio.js, studio.css, SoulX_前端可交互预览.html')
