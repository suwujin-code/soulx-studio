"""Interactive Gradio studio backed by the persistent product services."""

from __future__ import annotations

import json

from .queue import JobWorker
from .store import ValidationError, WorkbenchStore


APP_CSS = """
.soulx-shell {max-width: 1440px !important; margin: 0 auto;}
.soulx-hero {border: 1px solid #e7e7e7; border-radius: 22px; padding: 22px 26px;
background: linear-gradient(135deg,#fff 0%,#f5f6f8 58%,#edf5f2 100%);}
.soulx-hero h1 {font-size: 29px; margin: 0 0 6px;}
.soulx-muted {color:#666;}
.soulx-primary button {border-radius:14px !important;}
"""


def _tags(value: str) -> list[str]:
    return [item.strip() for item in (value or "").replace("，", ",").split(",") if item.strip()]


def _voice_choices(store: WorkbenchStore, include_deleted: bool = False):
    return [
        (f"{'★ ' if voice['favorite'] else ''}{voice['name']} · {voice['region']}", voice["id"])
        for voice in store.list_voices(include_deleted=include_deleted)
    ]


def _voice_rows(store: WorkbenchStore, search: str = "", favorites_only: bool = False):
    return [
        [
            "★" if voice["favorite"] else "",
            voice["name"],
            voice["region"],
            voice["gender"],
            voice["style"],
            "、".join(voice["tags"]),
            "已删除" if voice["deleted"] else "可用",
            voice["updated_at"],
            voice["id"],
        ]
        for voice in store.list_voices(
            search=search, favorites_only=favorites_only, include_deleted=True
        )
    ]


def _job_choices(store: WorkbenchStore):
    return [
        (f"{job['status']} · {job['text'][:28]} · {job['id'][-8:]}", job["id"])
        for job in store.list_jobs(limit=300)
    ]


def _job_rows(store: WorkbenchStore):
    return [
        [
            job["status"],
            job["priority"],
            f"{job['progress']}%",
            job["stage"],
            job["attempts"],
            job["created_at"],
            job["text"][:80],
            job["id"],
        ]
        for job in store.list_jobs(limit=300)
    ]


def build_ui(store: WorkbenchStore, worker: JobWorker):
    try:
        import gradio as gr
    except ImportError as exc:
        raise RuntimeError(
            "Gradio 未安装，请先运行 pip install -r requirements.workbench.txt"
        ) from exc

    def notify_error(exc: Exception):
        gr.Warning(str(exc))
        return f"❌ {str(exc)}"

    def refresh_voice_surface(search="", favorite=False, selected=None):
        choices = _voice_choices(store, include_deleted=True)
        values = {value for _, value in choices}
        selected = selected if selected in values else (choices[0][1] if choices else None)
        return (
            _voice_rows(store, search, bool(favorite)),
            gr.update(choices=choices, value=selected),
            gr.update(choices=_voice_choices(store), value=None),
        )

    def add_voice(
        audio_path,
        name,
        transcript,
        dialect,
        region,
        gender,
        style,
        dialect_prompt,
        tags,
        notes,
        authorization,
        favorite,
        consent,
    ):
        try:
            if not consent:
                raise ValidationError("请确认你拥有该声音的使用授权")
            voice = store.create_voice(
                name=name,
                audio_file=audio_path,
                transcript=transcript,
                dialect=dialect,
                region=region,
                gender=gender,
                style=style,
                dialect_prompt=dialect_prompt,
                tags=_tags(tags),
                notes=notes,
                source_authorization=authorization,
                favorite=bool(favorite),
            )
            return (
                f"✅ 已加入音色：{voice['name']}",
                _voice_rows(store),
                gr.update(choices=_voice_choices(store, include_deleted=True), value=voice["id"]),
                gr.update(choices=_voice_choices(store), value=voice["id"]),
            )
        except Exception as exc:
            return notify_error(exc), gr.update(), gr.update(), gr.update()

    def load_voice(voice_id):
        try:
            voice = store.get_voice(voice_id, include_deleted=True)
            audio = store.resolve_asset(voice["audio_path"])
            return (
                voice["name"],
                voice["transcript"],
                voice["dialect"],
                voice["region"],
                voice["gender"],
                voice["style"],
                voice.get("dialect_prompt", ""),
                ", ".join(voice["tags"]),
                voice["notes"],
                voice["source_authorization"],
                voice["favorite"],
                str(audio) if audio and audio.is_file() else None,
                f"状态：{'已删除，可恢复' if voice['deleted'] else '可用'}",
            )
        except Exception as exc:
            return (*([gr.update()] * 12), notify_error(exc))

    def update_voice(
        voice_id,
        name,
        transcript,
        dialect,
        region,
        gender,
        style,
        dialect_prompt,
        tags,
        notes,
        authorization,
        favorite,
    ):
        try:
            voice = store.update_voice(
                voice_id,
                name=name,
                transcript=transcript,
                dialect=dialect,
                region=region,
                gender=gender,
                style=style,
                dialect_prompt=dialect_prompt,
                tags=_tags(tags),
                notes=notes,
                source_authorization=authorization,
                favorite=favorite,
            )
            return (
                f"✅ 已更新音色：{voice['name']}",
                _voice_rows(store),
                gr.update(choices=_voice_choices(store, include_deleted=True), value=voice_id),
                gr.update(choices=_voice_choices(store), value=voice_id),
            )
        except Exception as exc:
            return notify_error(exc), gr.update(), gr.update(), gr.update()

    def favorite_voice(voice_id):
        try:
            voice = store.get_voice(voice_id, include_deleted=True)
            voice = store.update_voice(voice_id, favorite=not voice["favorite"])
            return (
                f"{'⭐ 已收藏' if voice['favorite'] else '已取消收藏'}：{voice['name']}",
                _voice_rows(store),
                voice["favorite"],
                gr.update(choices=_voice_choices(store, include_deleted=True), value=voice_id),
                gr.update(choices=_voice_choices(store), value=voice_id if not voice["deleted"] else None),
            )
        except Exception as exc:
            return notify_error(exc), gr.update(), gr.update(), gr.update(), gr.update()

    def delete_voice(voice_id):
        try:
            voice = store.delete_voice(voice_id)
            return (
                f"🗑️ 已移入回收状态：{voice['name']}",
                _voice_rows(store),
                gr.update(choices=_voice_choices(store, include_deleted=True), value=voice_id),
                gr.update(choices=_voice_choices(store), value=None),
            )
        except Exception as exc:
            return notify_error(exc), gr.update(), gr.update(), gr.update()

    def restore_voice(voice_id):
        try:
            voice = store.restore_voice(voice_id)
            return (
                f"✅ 已恢复：{voice['name']}",
                _voice_rows(store),
                gr.update(choices=_voice_choices(store, include_deleted=True), value=voice_id),
                gr.update(choices=_voice_choices(store), value=voice_id),
            )
        except Exception as exc:
            return notify_error(exc), gr.update(), gr.update(), gr.update()

    def submit_single(text, voice_ids, dialect, region, priority, seed, max_tokens):
        try:
            job = store.create_job(
                text=text,
                voice_ids=voice_ids or [],
                dialect=dialect,
                region=region,
                priority=int(priority),
                settings={
                    "seed": int(seed),
                    "max_tokens": int(max_tokens),
                    "temperature": 0.6,
                    "top_k": 100,
                    "top_p": 0.9,
                    "repetition_penalty": 1.25,
                    "output_format": "wav",
                },
            )
            worker.start()
            worker.wake()
            return (
                f"✅ 已进入队列：{job['id']}",
                gr.update(choices=_job_choices(store), value=job["id"]),
                _job_rows(store),
            )
        except Exception as exc:
            return notify_error(exc), gr.update(), gr.update()

    def submit_batch(rows):
        try:
            items = []
            for row in rows or []:
                if not row or not str(row[0] or "").strip():
                    continue
                voice_ids = [
                    value.strip()
                    for value in str(row[1] or "").replace(",", "|").split("|")
                    if value.strip()
                ]
                items.append(
                    {
                        "text": str(row[0]).strip(),
                        "voice_ids": voice_ids,
                        "priority": int(row[2] or 0),
                        "dialect": str(row[3] or "yue"),
                        "region": str(row[4] or "香港粤语"),
                        "settings": {"seed": int(row[5] or 1988), "max_tokens": int(row[6] or 512)},
                    }
                )
            result = store.create_batch(items)
            worker.start()
            worker.wake()
            return (
                f"✅ 批次 {result['batch_id']} 已加入，共 {result['count']} 条",
                _job_rows(store),
                gr.update(choices=_job_choices(store), value=result["jobs"][0]["id"]),
            )
        except Exception as exc:
            return notify_error(exc), gr.update(), gr.update()

    def refresh_jobs(selected=None):
        choices = _job_choices(store)
        values = {value for _, value in choices}
        selected = selected if selected in values else (choices[0][1] if choices else None)
        stats = store.queue_stats()
        status = f"队列：{'已暂停' if stats['paused'] else '运行中'} · " + " · ".join(
            f"{key} {value}" for key, value in stats["counts"].items() if value
        )
        return _job_rows(store), gr.update(choices=choices, value=selected), status

    def job_action(job_id, action):
        try:
            if action == "cancel":
                job = store.cancel_job(job_id)
            elif action == "retry":
                job = store.retry_job(job_id)
                worker.start()
                worker.wake()
            elif action == "pause":
                job = store.pause_job(job_id)
            else:
                job = store.resume_job(job_id)
                worker.start()
                worker.wake()
            return f"✅ {job['id']} → {job['status']}", _job_rows(store)
        except Exception as exc:
            return notify_error(exc), gr.update()

    def open_job(job_id):
        try:
            job = store.get_job(job_id)
            path = store.resolve_asset(job.get("result_path"))
            return (
                json.dumps(job, ensure_ascii=False, indent=2),
                str(path) if path and path.is_file() else None,
            )
        except Exception as exc:
            return notify_error(exc), None

    def set_queue(paused):
        stats = worker.pause() if paused else worker.resume()
        return f"队列已{'暂停' if stats['paused'] else '恢复'}"

    def create_project(name):
        try:
            project = store.create_project(name)
            return (
                f"✅ 已新建项目：{project['name']}",
                gr.update(
                    choices=[(item["name"], item["id"]) for item in store.list_projects()],
                    value=project["id"],
                ),
            )
        except Exception as exc:
            return notify_error(exc), gr.update()

    def save_project(project_id, name, data_text, archived):
        try:
            data = json.loads(data_text or "{}")
            project = store.update_project(project_id, name=name, data=data, archived=archived)
            return f"✅ 已保存：{project['name']}", gr.update(
                choices=[(item["name"], item["id"]) for item in store.list_projects()],
                value=project_id,
            )
        except Exception as exc:
            return notify_error(exc), gr.update()

    def load_project(project_id):
        try:
            project = store.get_project(project_id)
            return project["name"], json.dumps(project["data"], ensure_ascii=False, indent=2), project["archived"], "已载入"
        except Exception as exc:
            return gr.update(), gr.update(), gr.update(), notify_error(exc)

    def export_bundle():
        try:
            return str(store.export_portable_bundle()), "✅ 已生成跨 Mac 迁移包"
        except Exception as exc:
            return None, notify_error(exc)

    def inspect_bundle(path):
        try:
            return json.dumps(store.inspect_portable_bundle(path), ensure_ascii=False, indent=2)
        except Exception as exc:
            return notify_error(exc)

    initial_voice_choices = _voice_choices(store)
    initial_all_voice_choices = _voice_choices(store, include_deleted=True)
    initial_jobs = _job_choices(store)

    with gr.Blocks(css=APP_CSS, elem_classes="soulx-shell", title="SoulX 粤语 AI 配音工作台") as demo:
        gr.HTML(
            """
            <section class="soulx-hero">
              <h1>SoulX 粤语 AI 配音工作台</h1>
              <div class="soulx-muted">本地音色 · 零样本克隆 · 批量队列 · REST API · MCP · 多 Mac 协作</div>
            </section>
            """
        )
        with gr.Tabs():
            with gr.Tab("立即配音"):
                with gr.Row():
                    with gr.Column(scale=3):
                        quick_text = gr.Textbox(
                            label="配音文本",
                            lines=10,
                            placeholder="单人直接输入文字；双人使用 [S1]你好 [S2]你好。",
                        )
                        quick_voices = gr.Dropdown(
                            choices=initial_voice_choices,
                            multiselect=True,
                            max_choices=4,
                            label="选择音色（最多 4 个）",
                            filterable=True,
                        )
                        with gr.Row():
                            quick_dialect = gr.Dropdown(
                                choices=[("粤语", "yue"), ("普通话", "mandarin")],
                                value="yue",
                                label="语言/方言",
                            )
                            quick_region = gr.Dropdown(
                                choices=["香港粤语", "广州粤语", "泛粤语"],
                                value="香港粤语",
                                allow_custom_value=True,
                                label="地区",
                            )
                        with gr.Accordion("高级参数", open=False):
                            with gr.Row():
                                quick_priority = gr.Slider(-10, 10, value=0, step=1, label="队列优先级")
                                quick_seed = gr.Number(value=1988, precision=0, label="随机种子")
                                quick_max_tokens = gr.Slider(64, 1500, value=512, step=16, label="最大 Token")
                        quick_submit = gr.Button("加入配音队列", variant="primary", elem_classes="soulx-primary")
                        quick_status = gr.Markdown("等待提交")
                    with gr.Column(scale=2):
                        quick_job = gr.Dropdown(choices=initial_jobs, label="最近任务")
                        quick_job_detail = gr.Code(label="任务详情", language="json")
                        quick_audio = gr.Audio(label="生成结果", type="filepath")
                        quick_open = gr.Button("刷新并打开任务")

            with gr.Tab("音色中心"):
                with gr.Row():
                    voice_search = gr.Textbox(label="搜索名称、地区、标签")
                    voice_favorite_filter = gr.Checkbox(label="只看收藏")
                    voice_refresh = gr.Button("刷新")
                voice_table = gr.Dataframe(
                    value=_voice_rows(store),
                    headers=["收藏", "名称", "地区", "性别", "风格", "标签", "状态", "更新时间", "ID"],
                    datatype=["str"] * 9,
                    interactive=False,
                    wrap=True,
                    label="音色资产",
                )
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("#### 加入新音色")
                        add_audio = gr.Audio(
                            sources=["upload", "microphone"], type="filepath", label="上传或录制参考音频"
                        )
                        add_name = gr.Textbox(label="名称")
                        add_transcript = gr.Textbox(label="参考音频对应文本", lines=3)
                        with gr.Row():
                            add_dialect = gr.Dropdown(
                                choices=[("粤语", "yue"), ("普通话", "mandarin")],
                                value="yue",
                                label="方言",
                            )
                            add_region = gr.Dropdown(
                                choices=["香港粤语", "广州粤语", "泛粤语"],
                                value="香港粤语",
                                allow_custom_value=True,
                                label="地区",
                            )
                        with gr.Row():
                            add_gender = gr.Dropdown(
                                choices=["", "男声", "女声", "中性"], value="", label="性别标签"
                            )
                            add_style = gr.Textbox(label="风格", placeholder="沉稳、亲切、新闻、播客")
                        add_dialect_prompt = gr.Textbox(
                            label="高级方言提示（可留空自动生成）",
                            placeholder="<|Yue|>参考粤语句子",
                        )
                        add_tags = gr.Textbox(label="标签（逗号分隔）")
                        add_notes = gr.Textbox(label="备注")
                        add_authorization = gr.Textbox(
                            label="授权来源/用途",
                            placeholder="本人声音；或已取得某某授权，仅限某项目",
                        )
                        add_favorite = gr.Checkbox(label="加入收藏")
                        add_consent = gr.Checkbox(label="我确认拥有该声音的合法使用授权")
                        add_button = gr.Button("保存到音色库", variant="primary")
                        add_status = gr.Markdown()
                    with gr.Column():
                        gr.Markdown("#### 编辑、收藏、删除与恢复")
                        edit_voice = gr.Dropdown(
                            choices=initial_all_voice_choices,
                            label="选择音色（包含已删除项）",
                            filterable=True,
                        )
                        edit_audio = gr.Audio(label="参考音频", type="filepath", interactive=False)
                        edit_name = gr.Textbox(label="名称")
                        edit_transcript = gr.Textbox(label="参考文本", lines=3)
                        with gr.Row():
                            edit_dialect = gr.Textbox(label="方言")
                            edit_region = gr.Textbox(label="地区")
                        with gr.Row():
                            edit_gender = gr.Textbox(label="性别标签")
                            edit_style = gr.Textbox(label="风格")
                        edit_dialect_prompt = gr.Textbox(label="方言提示")
                        edit_tags = gr.Textbox(label="标签")
                        edit_notes = gr.Textbox(label="备注")
                        edit_authorization = gr.Textbox(label="授权来源/用途")
                        edit_favorite = gr.Checkbox(label="收藏")
                        with gr.Row():
                            edit_save = gr.Button("保存修改", variant="primary")
                            edit_favorite_button = gr.Button("切换收藏")
                            edit_delete = gr.Button("移入回收状态", variant="stop")
                            edit_restore = gr.Button("恢复")
                        edit_status = gr.Markdown()

            with gr.Tab("批量与队列"):
                gr.Markdown("每行一个任务；双人文本使用 [S1]、[S2]，音色 ID 用竖线分隔。")
                batch_table = gr.Dataframe(
                    value=[["", "", 0, "yue", "香港粤语", 1988, 512]],
                    headers=["text", "voice_ids", "priority", "dialect", "region", "seed", "max_tokens"],
                    datatype=["str", "str", "number", "str", "str", "number", "number"],
                    interactive=True,
                    row_count=(1, "dynamic"),
                    label="批量任务",
                )
                batch_submit = gr.Button("批量加入队列", variant="primary")
                batch_status = gr.Markdown()
                with gr.Row():
                    queue_pause = gr.Button("暂停领取新任务")
                    queue_resume = gr.Button("恢复队列")
                    jobs_refresh = gr.Button("刷新任务")
                queue_status = gr.Markdown()
                jobs_table = gr.Dataframe(
                    value=_job_rows(store),
                    headers=["状态", "优先级", "进度", "阶段", "尝试", "创建时间", "文本", "ID"],
                    datatype=["str"] * 8,
                    interactive=False,
                    wrap=True,
                    label="持久任务队列",
                )
                with gr.Row():
                    job_selector = gr.Dropdown(choices=initial_jobs, label="任务")
                    job_cancel = gr.Button("取消", variant="stop")
                    job_retry = gr.Button("重试")
                    job_pause = gr.Button("暂停此任务")
                    job_resume = gr.Button("恢复此任务")
                job_action_status = gr.Markdown()
                refresh_timer = gr.Timer(3.0)

            with gr.Tab("项目与跨 Mac"):
                with gr.Row():
                    project_name_new = gr.Textbox(label="新项目名称")
                    project_create = gr.Button("新建项目", variant="primary")
                project_selector = gr.Dropdown(
                    choices=[(item["name"], item["id"]) for item in store.list_projects()],
                    label="项目",
                )
                project_name_edit = gr.Textbox(label="项目名称")
                project_data = gr.Code(value="{}", language="json", label="项目配置/草稿")
                project_archived = gr.Checkbox(label="归档")
                project_save = gr.Button("保存项目")
                project_status = gr.Markdown()
                gr.Markdown(
                    "迁移包包含音色、项目、队列历史和生成结果。在另一台 Mac 恢复到空目录，原数据不会被覆盖。"
                    "恢复命令：`python -m workbench.portable restore 迁移包.zip --destination 新的空目录`。"
                )
                with gr.Row():
                    export_button = gr.Button("导出完整迁移包", variant="primary")
                    export_file = gr.File(label="迁移包", interactive=False)
                export_status = gr.Markdown()
                import_file = gr.File(label="迁移包检查（不会写入）", file_types=[".zip"], type="filepath")
                import_preview = gr.Code(label="迁移包检查结果", language="json")

            with gr.Tab("接口与系统"):
                health_data = {
                    "data_dir": str(store.data_dir),
                    "model_path": str(store.config.model_path),
                    "dry_run": store.config.dry_run,
                    "queue": store.queue_stats(),
                }
                gr.Code(json.dumps(health_data, ensure_ascii=False, indent=2), language="json", label="运行状态")
                gr.Markdown(
                    """
                    ### REST API
                    OpenAPI 文档：/docs
                    创建任务：POST /api/v1/tts/jobs
                    音色管理：/api/v1/voices
                    批量队列：/api/v1/tts/batches

                    ### MCP
                    启动命令：python -m workbench.mcp_server

                    局域网模式会自动启用访问密钥；其他 Mac 使用浏览器打开主机地址即可。
                    """
                )

        quick_submit.click(
            submit_single,
            [quick_text, quick_voices, quick_dialect, quick_region, quick_priority, quick_seed, quick_max_tokens],
            [quick_status, quick_job, jobs_table],
        )
        quick_open.click(open_job, quick_job, [quick_job_detail, quick_audio], queue=False)
        quick_job.change(open_job, quick_job, [quick_job_detail, quick_audio], queue=False)
        add_button.click(
            add_voice,
            [
                add_audio, add_name, add_transcript, add_dialect, add_region, add_gender,
                add_style, add_dialect_prompt, add_tags, add_notes, add_authorization,
                add_favorite, add_consent,
            ],
            [add_status, voice_table, edit_voice, quick_voices],
        )
        voice_refresh.click(
            refresh_voice_surface,
            [voice_search, voice_favorite_filter, edit_voice],
            [voice_table, edit_voice, quick_voices],
            queue=False,
        )
        voice_search.submit(
            refresh_voice_surface,
            [voice_search, voice_favorite_filter, edit_voice],
            [voice_table, edit_voice, quick_voices],
            queue=False,
        )
        edit_voice.change(
            load_voice,
            edit_voice,
            [
                edit_name, edit_transcript, edit_dialect, edit_region, edit_gender, edit_style,
                edit_dialect_prompt, edit_tags, edit_notes, edit_authorization, edit_favorite,
                edit_audio, edit_status,
            ],
            queue=False,
        )
        edit_save.click(
            update_voice,
            [
                edit_voice, edit_name, edit_transcript, edit_dialect, edit_region, edit_gender,
                edit_style, edit_dialect_prompt, edit_tags, edit_notes, edit_authorization,
                edit_favorite,
            ],
            [edit_status, voice_table, edit_voice, quick_voices],
        )
        edit_favorite_button.click(
            favorite_voice,
            edit_voice,
            [edit_status, voice_table, edit_favorite, edit_voice, quick_voices],
        )
        edit_delete.click(delete_voice, edit_voice, [edit_status, voice_table, edit_voice, quick_voices])
        edit_restore.click(restore_voice, edit_voice, [edit_status, voice_table, edit_voice, quick_voices])
        batch_submit.click(submit_batch, batch_table, [batch_status, jobs_table, job_selector])
        jobs_refresh.click(refresh_jobs, job_selector, [jobs_table, job_selector, queue_status], queue=False)
        refresh_timer.tick(refresh_jobs, job_selector, [jobs_table, job_selector, queue_status], queue=False)
        queue_pause.click(lambda: set_queue(True), None, queue_status, queue=False)
        queue_resume.click(lambda: set_queue(False), None, queue_status, queue=False)
        job_cancel.click(lambda job_id: job_action(job_id, "cancel"), job_selector, [job_action_status, jobs_table])
        job_retry.click(lambda job_id: job_action(job_id, "retry"), job_selector, [job_action_status, jobs_table])
        job_pause.click(lambda job_id: job_action(job_id, "pause"), job_selector, [job_action_status, jobs_table])
        job_resume.click(lambda job_id: job_action(job_id, "resume"), job_selector, [job_action_status, jobs_table])
        project_create.click(create_project, project_name_new, [project_status, project_selector])
        project_selector.change(
            load_project,
            project_selector,
            [project_name_edit, project_data, project_archived, project_status],
            queue=False,
        )
        project_save.click(
            save_project,
            [project_selector, project_name_edit, project_data, project_archived],
            [project_status, project_selector],
        )
        export_button.click(export_bundle, None, [export_file, export_status])
        import_file.change(inspect_bundle, import_file, import_preview, queue=False)

    return demo
