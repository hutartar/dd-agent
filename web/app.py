"""
DD-Agent Web MVP
上传项目zip包 → 自动分析 → 在线阅读报告 → 下载
"""
import json
import os
import sys
import zipfile
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from flask import (Flask, request, redirect, url_for, send_file, render_template,
                   flash, jsonify, session, Response)

# 把项目根目录加入path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dd_agent.runtime_config import (  # noqa: E402  （必须在 sys.path 之后）
    auth_version,
    clear_guest_password,
    ensure_initial_password,
    get_llm_config,
    has_guest_password,
    load_secret_key,
    load_settings,
    save_settings,
    set_password,
    verify_password,
)
from dd_agent.industry import list_industries  # noqa: E402
from dd_agent.visitor_config import (  # noqa: E402
    clear_visitor_config,
    load_visitor_config,
    new_visitor_id,
    save_visitor_config,
)


def _load_dotenv():
    """本地直接跑时也读项目根的 .env；Docker 里由 compose 注入，不冲突。"""
    path = PROJECT_ROOT / ".env"
    if not path.exists():
        return
    try:
        # utf-8-sig：兼容记事本保存的带 BOM 的 UTF-8
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

app = Flask(__name__)
app.secret_key = load_secret_key()
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("DD_MAX_UPLOAD_MB", "200")) * 1024 * 1024
app.permanent_session_lifetime = timedelta(days=14)  # 登录保持两周

# 第一次启动时初始化登录密码：优先 DD_INITIAL_PASSWORD，否则随机生成并打印到日志
_initial_password = ensure_initial_password()
if _initial_password:
    print("=" * 56)
    print("DD-Agent 首次启动，已生成登录密码：")
    print(f"    密码：{_initial_password}")
    print("登录后请到「设置」页改掉它。")
    print("=" * 56)

# 配置
UPLOAD_FOLDER = PROJECT_ROOT / 'web' / 'uploads'
OUTPUT_FOLDER = PROJECT_ROOT / 'web' / 'results'
ALLOWED_EXTENSIONS = {'zip'}

UPLOAD_FOLDER.mkdir(exist_ok=True)
OUTPUT_FOLDER.mkdir(exist_ok=True)


# ---------------------------------------------------------------- 登录

_PUBLIC_ENDPOINTS = {"login", "static"}

# 只有管理员能碰的接口
_ADMIN_ENDPOINTS = {
    "settings_page",
    "save_llm_settings",
    "test_llm",
    "change_admin_password",
    "set_guest_password",
}


@app.before_request
def _guard():
    endpoint = request.endpoint or ""
    if endpoint in _PUBLIC_ENDPOINTS or endpoint.startswith("static"):
        return None
    if not session.get("auth"):
        return redirect(url_for("login", next=request.path))
    # 密码改过之后，旧登录全部失效
    if session.get("v") != auth_version():
        session.clear()
        return redirect(url_for("login"))
    if endpoint in _ADMIN_ENDPOINTS and session.get("role") != "admin":
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "message": "只有管理员能改设置"}), 403
        flash("只有管理员能进设置页")
        return redirect(url_for("index"))
    return None


@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get("auth"):
        return redirect(url_for("index"))
    error = ""
    if request.method == "POST":
        role = verify_password(request.form.get("password", ""))
        if role:
            # 同一浏览器重新登录时保留 visitor_id，个人模型配置不丢
            existing_visitor = session.get("visitor_id")
            session.clear()
            session["auth"] = True
            session["role"] = role
            session["v"] = auth_version()
            session["visitor_id"] = existing_visitor or new_visitor_id()
            session.permanent = True
            target = request.args.get("next") or "/"
            if not target.startswith("/") or target.startswith("//"):
                target = "/"
            return redirect(target)
        error = "密码不对，再试一次"
    return render_template("login.html", error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- 设置

def _mask_key(value: str) -> str:
    return ("••••" + value[-4:]) if value else ""


def _public_llm(llm: dict) -> dict:
    """给前端：两套引擎都返回，api_key 只回掩码。"""
    out = {
        "enabled": bool(llm.get("enabled", True)),
        "provider": llm.get("provider", "ollama"),
    }
    for section in ("ollama", "api"):
        data = dict(llm.get(section) or {})
        data["api_key_masked"] = _mask_key(data.pop("api_key", ""))
        out[section] = data
    return out


@app.route('/settings')
def settings_page():
    return render_template(
        "settings.html",
        llm=_public_llm(load_settings()["llm"]),
        guest_enabled=has_guest_password(),
    )


@app.route('/api/settings/llm', methods=['POST'])
def save_llm_settings():
    data = request.get_json(silent=True) or {}
    provider = (data.get("provider") or "ollama").strip().lower()
    if provider not in ("ollama", "api"):
        return jsonify({"ok": False, "message": "未知的引擎类型"}), 400

    current = load_settings()["llm"]
    patch = {"enabled": bool(data.get("enabled", True)), "provider": provider}
    for section in ("ollama", "api"):
        incoming = data.get(section) or {}
        merged = {
            "base_url": (incoming.get("base_url") or "").strip(),
            "model": (incoming.get("model") or "").strip(),
        }
        key = (incoming.get("api_key") or "").strip()
        if key and not key.startswith("••••"):
            merged["api_key"] = key
        elif current.get(section, {}).get("api_key"):
            merged["api_key"] = current[section]["api_key"]
        patch[section] = merged

    saved = save_settings({"llm": patch})["llm"]
    return jsonify({
        "ok": True,
        "message": "已保存，下一次分析立即生效",
        "llm": _public_llm(saved),
    })


@app.route('/api/settings/test', methods=['POST'])
def test_llm():
    data = request.get_json(silent=True) or {}
    provider = (data.get("provider") or "ollama").strip().lower()
    section = data.get("api" if provider == "api" else "ollama") or {}
    base_url = (section.get("base_url") or "").strip()
    model = (section.get("model") or "").strip()
    api_key = (section.get("api_key") or "").strip()
    if not api_key or api_key.startswith("••••"):
        saved = load_settings()["llm"].get("api" if provider == "api" else "ollama", {})
        api_key = saved.get("api_key", "")

    try:
        from dd_agent.llm.client import LLMClient
        llm = LLMClient(provider=provider, base_url=base_url, model=model, api_key=api_key)
        if provider == "ollama":
            ok = llm.available
            message = f"Ollama 可达：{llm.ollama_host}" if ok else f"连不上：{llm.ollama_host}"
        else:
            if not llm.api_key:
                return jsonify({"ok": False, "message": "还没填 API Key"})
            import requests
            response = requests.get(
                llm.base_url.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {llm.api_key}"},
                timeout=10,
            )
            ok = response.status_code < 500
            message = f"{llm.base_url} → HTTP {response.status_code}"
    except Exception as exc:
        ok, message = False, f"连接失败：{exc}"
    return jsonify({"ok": ok, "message": message})


@app.route('/api/settings/password', methods=['POST'])
def change_admin_password():
    data = request.get_json(silent=True) or request.form
    old = data.get("old") or ""
    new = data.get("new") or ""
    if verify_password(old) != "admin":
        return jsonify({"ok": False, "message": "旧密码不对"}), 400
    if len(new) < 6:
        return jsonify({"ok": False, "message": "新密码至少 6 位"}), 400
    set_password("admin", new)
    # 自己这一条 session 保住，其他人（访客）全部失效
    session["v"] = auth_version()
    return jsonify({"ok": True, "message": "管理员密码已更新，其他登录已全部失效"})


@app.route('/api/settings/guest-password', methods=['POST'])
def set_guest_password():
    data = request.get_json(silent=True) or request.form
    password = (data.get("password") or "").strip()
    if password:
        if len(password) < 6:
            return jsonify({"ok": False, "message": "访客密码至少 6 位"}), 400
        set_password("guest", password)
        message = "访客密码已设置；之前用旧访客密码登录的人需要重新登录"
    else:
        clear_guest_password()
        message = "已关闭访客登录"
    session["v"] = auth_version()
    return jsonify({
        "ok": True,
        "message": message,
        "guest_enabled": has_guest_password(),
    })


# ---------------------------------------------------------------- 我的模型（访客个人配置）

def _public_visitor(config: dict) -> dict:
    out = dict(config or {})
    api = dict(out.get("api") or {})
    key = api.pop("api_key", "")
    api["api_key_masked"] = ("••••" + key[-4:]) if key else ""
    out["api"] = api
    return out


@app.route('/my')
def my_model_page():
    config = load_visitor_config(session.get("visitor_id") or "")
    return render_template("my.html", config=_public_visitor(config))


@app.route('/api/my/llm', methods=['POST'])
def save_my_llm():
    visitor_id = session.get("visitor_id")
    if not visitor_id:
        visitor_id = new_visitor_id()
        session["visitor_id"] = visitor_id
    data = request.get_json(silent=True) or {}
    saved = save_visitor_config(visitor_id, data)
    return jsonify({
        "ok": True,
        "message": "已保存，下次上传自动使用",
        "config": _public_visitor(saved),
    })


@app.route('/api/my/test', methods=['POST'])
def test_my_llm():
    data = request.get_json(silent=True) or {}
    provider = (data.get("provider") or "ollama").strip().lower()
    section = data.get("api" if provider == "api" else "ollama") or {}
    base_url = (section.get("base_url") or "").strip()
    model = (section.get("model") or "").strip()
    api_key = (section.get("api_key") or "").strip()
    if not api_key or api_key.startswith("••••"):
        saved = load_visitor_config(session.get("visitor_id") or "")
        api_key = (saved.get("api") or {}).get("api_key") or ""

    try:
        from dd_agent.llm.client import LLMClient
        llm = LLMClient(provider=provider, base_url=base_url, model=model, api_key=api_key)
        if provider == "ollama":
            ok = llm.available
            message = f"Ollama 可达：{llm.ollama_host}" if ok else f"连不上：{llm.ollama_host}"
        else:
            if not llm.api_key:
                return jsonify({"ok": False, "message": "还没填 API Key"})
            import requests
            response = requests.get(
                llm.base_url.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {llm.api_key}"},
                timeout=10,
            )
            ok = response.status_code < 500
            message = f"{llm.base_url} → HTTP {response.status_code}"
    except Exception as exc:
        ok, message = False, f"连接失败：{exc}"
    return jsonify({"ok": ok, "message": message})


@app.route('/api/my/clear', methods=['POST'])
def clear_my_llm():
    clear_visitor_config(session.get("visitor_id") or "")
    return jsonify({"ok": True, "message": "已清除，之后使用服务器默认配置"})


@app.errorhandler(413)
def request_too_large(error):
    """上传超过 MAX_CONTENT_LENGTH 时给中文提示，而不是默认英文页。"""
    limit_mb = int(os.getenv("DD_MAX_UPLOAD_MB", "200"))
    message = (
        f"上传文件超过 {limit_mb} MB 限制。"
        f"可以把资料包拆分压缩，或者把环境变量 DD_MAX_UPLOAD_MB 调大后重启服务。"
    )
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "message": message}), 413
    return (
        "<html><body style='font-family: system-ui, sans-serif; padding: 40px; color: #1d2129;'>"
        f"<h2>文件太大</h2><p>{message}</p>"
        "<p><a href='/'>返回首页</a></p></body></html>",
        413,
    )


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/')
def index():
    """首页：上传页面"""
    # 列出历史分析结果
    results = []
    if OUTPUT_FOLDER.exists():
        for d in sorted(OUTPUT_FOLDER.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            # 过滤掉隐藏目录（比如.ocr_cache）
            if d.is_dir() and not d.name.startswith('.'):
                output_dir = d / 'output'
                report_files = list(output_dir.glob('*.html')) if output_dir.exists() else []
                md_files = list(output_dir.glob('*.md')) if output_dir.exists() else []
                xlsx_files = list(output_dir.glob('*.xlsx')) if output_dir.exists() else []
                
                # 转换时间戳
                import datetime
                time_str = datetime.datetime.fromtimestamp(d.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
                
                # 读取项目名和总耗时：两个meta文件都要读，合并信息
                project_name = d.name  # 默认用task_id
                total_seconds = 0
                
                # 1. 先读根目录的老meta：存项目名
                root_meta_path = d / 'meta.json'
                if root_meta_path.exists():
                    import json
                    with open(str(root_meta_path), 'r', encoding='utf-8') as f:
                        root_meta = json.load(f)
                        project_name = root_meta.get('project_name', d.name)
                
                # 2. 再读output目录的新meta：存总耗时、步骤状态
                output_meta_path = output_dir / 'meta.json'
                if output_meta_path.exists():
                    import json
                    with open(str(output_meta_path), 'r', encoding='utf-8') as f:
                        output_meta = json.load(f)
                        total_seconds = output_meta.get('total_seconds', 0)
                        # 如果根目录没读到项目名，就用output里的company_name
                        if project_name == d.name:
                            project_name = output_meta.get('company_name', d.name)
                
                # 转成易读的耗时格式
                if total_seconds >= 60:
                    minutes = total_seconds // 60
                    seconds = total_seconds % 60
                    duration_str = f"总耗时：{minutes}分{seconds}秒"
                elif total_seconds > 0:
                    duration_str = f"总耗时：{total_seconds}秒"
                else:
                    duration_str = "（老任务未统计耗时）"
                
                results.append({
                    'name': d.name,
                    'project_name': project_name,
                    'time': time_str,
                    'duration': duration_str,
                    'has_report': len(report_files) > 0,
                    'report_name': report_files[0].name if report_files else None,
                    'has_md': len(md_files) > 0,
                    'has_xlsx': len(xlsx_files) > 0
                })
    return render_template(
        'index.html',
        results=results,
        industries=list_industries(),
        max_upload_mb=int(os.getenv("DD_MAX_UPLOAD_MB", "200")),
    )


def _launch_analysis(task_dir: Path, data_dir: Path, output_dir: Path, industry: str, env_overrides: dict = None):
    """启动后台分析子进程，写 pid 文件。上传和重试共用。"""
    cmd = [
        sys.executable, '-m', 'dd_agent', 'scan',
        str(data_dir),
        '-o', str(output_dir),
        '--industry', industry,
    ]
    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'
    if env_overrides:
        env.update(env_overrides)

    log_path = task_dir / 'analysis.log'
    log_file = open(str(log_path), 'w', encoding='utf-8')
    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        start_new_session=True,
    )
    log_file.close()
    try:
        (task_dir / 'pid.txt').write_text(str(proc.pid), encoding='utf-8')
    except OSError:
        pass
    return proc


@app.route('/upload', methods=['POST'])
def upload():
    """上传zip包并开始分析（异步）"""
    if 'file' not in request.files:
        flash('请选择文件')
        return redirect(url_for('index'))

    file = request.files['file']
    if file.filename == '':
        flash('请选择文件')
        return redirect(url_for('index'))

    if file and allowed_file(file.filename):
        # 创建任务目录：找当前最大的task编号，+1，避免重复覆盖旧任务
        import re
        existing_tasks = [d.name for d in OUTPUT_FOLDER.iterdir() if d.is_dir() and d.name.startswith('task_')]
        max_num = 0
        for t in existing_tasks:
            match = re.match(r'task_(\d+)', t)
            if match:
                max_num = max(max_num, int(match.group(1)))
        task_id = f"task_{max_num + 1:03d}"
        task_dir = OUTPUT_FOLDER / task_id
        task_dir.mkdir(exist_ok=True)

        # 获取项目名（去掉.zip后缀）
        project_name = file.filename.rsplit('.', 1)[0]
        industry = (request.form.get("industry") or "generic").strip()

        # 使用者自带 API（可选）：只用于本次分析，key 不落盘
        own_api = request.form.get("own_api") == "on"
        own_base = (request.form.get("own_base") or "").strip()
        own_model = (request.form.get("own_model") or "").strip()
        own_key = (request.form.get("own_key") or "").strip()
        use_own = own_api and bool(own_key)
        visitor_config = {} if use_own else load_visitor_config(session.get("visitor_id") or "")
        if use_own:
            llm_mode = "own"
        elif visitor_config:
            llm_mode = "visitor"
        else:
            llm_mode = "server"

        # 保存meta信息
        import json
        meta = {
            'project_name': project_name,
            'original_zip': file.filename,
            'industry': industry,
            'llm_mode': llm_mode,
        }
        with open(str(task_dir / 'meta.json'), 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        # 保存zip
        zip_path = task_dir / 'input.zip'
        file.save(str(zip_path))

        # 解压
        extract_dir = task_dir / 'data'
        extract_dir.mkdir(exist_ok=True)
        try:
            with zipfile.ZipFile(str(zip_path), 'r') as zip_ref:
                base = extract_dir.resolve()
                for member in zip_ref.namelist():
                    target = (extract_dir / member).resolve()
                    if target != base and base not in target.parents:
                        raise ValueError(f"压缩包里有非法路径: {member}")
                zip_ref.extractall(str(extract_dir))
        except Exception as e:
            flash(f'解压失败: {e}')
            return redirect(url_for('index'))

        # 自动找到真正的数据目录
        data_dirs = [d for d in extract_dir.iterdir() if d.is_dir() and not d.name.startswith('.')]
        if len(data_dirs) == 1:
            data_dir = data_dirs[0]
        else:
            data_dir = extract_dir

        # 创建输出目录和日志文件
        output_dir = task_dir / 'output'
        output_dir.mkdir(exist_ok=True)
        log_path = task_dir / 'analysis.log'

        env_overrides = {}
        if use_own:
            env_overrides.update({
                'DD_IGNORE_SETTINGS': '1',
                'DD_LLM_ENABLED': '1',
                'DD_LLM_PROVIDER': 'api',
                'DD_LLM_BASE_URL': own_base or 'https://api.deepseek.com/v1',
                'DD_LLM_MODEL': own_model or 'deepseek-chat',
                'DD_LLM_API_KEY': own_key,
            })
        elif visitor_config:
            provider = (visitor_config.get("provider") or "ollama").strip().lower()
            section = visitor_config.get(provider) or {}
            env_overrides.update({
                'DD_IGNORE_SETTINGS': '1',
                'DD_LLM_ENABLED': '1',
                'DD_LLM_PROVIDER': provider,
            })
            if provider == 'api':
                env_overrides['DD_LLM_BASE_URL'] = section.get('base_url') or 'https://api.deepseek.com/v1'
                env_overrides['DD_LLM_MODEL'] = section.get('model') or 'deepseek-chat'
                env_overrides['DD_LLM_API_KEY'] = section.get('api_key') or ''
            else:
                env_overrides['OLLAMA_BASE_URL'] = section.get('base_url') or 'http://localhost:11434'
                env_overrides['DD_LLM_MODEL'] = section.get('model') or 'qwen2.5:7b'

        _launch_analysis(task_dir, data_dir, output_dir, industry, env_overrides)

        # 跳转到进度页
        return redirect(url_for('progress', task_id=task_id))

    flash('只支持zip文件')
    return redirect(url_for('index'))


@app.route('/progress/<task_id>')
def progress(task_id):
    """进度页面：实时显示分析日志"""
    task_dir = OUTPUT_FOLDER / task_id
    if not task_dir.exists():
        flash('任务不存在')
        return redirect(url_for('index'))
    return render_template('progress.html', task_id=task_id)


@app.route('/api/logs/<task_id>')
def get_logs(task_id):
    """获取实时日志"""
    task_dir = OUTPUT_FOLDER / task_id
    log_path = task_dir / 'analysis.log'

    if not log_path.exists():
        return jsonify({'logs': [], 'done': False, 'progress': 0, 'current_step': '等待开始...', 'estimated_remaining': 300})

    # 读取日志文件
    with open(str(log_path), 'r', encoding='utf-8') as f:
        logs = f.readlines()

    # 检查是否完成：优先读 meta.json 的状态（completed / failed / stopped）
    output_dir = task_dir / 'output'
    html_files = list(output_dir.glob('*.html')) if output_dir.exists() else []

    status = ''
    error = ''
    error_level = ''
    degraded_modules = []
    meta_path = output_dir / 'meta.json'
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8-sig'))
            status = meta.get('status') or ''
            error = meta.get('error') or ''
            error_level = meta.get('error_level') or ''
            degraded_modules = meta.get('degraded_modules') or []
        except (OSError, ValueError):
            pass

    all_logs = ''.join(logs)
    finished = status in ('completed', 'failed', 'stopped')
    done = finished or '尽调完成' in all_logs or len(html_files) > 0

    # 计算进度：根据日志里的Step判断当前步骤
    # 每个步骤有基准权重（%）和基准耗时（秒）
    steps = [
        ('Step 1: 目录盘点', 5, 60, '正在扫描和分类文件...'),
        ('Step 2: 财务报表分析', 25, 60, '正在分析财务报表和指标...'),
        ('Step 3: 红旗规则扫描', 45, 30, '正在扫描风险信号...'),
        ('Step 4: 交叉验证', 55, 20, '正在交叉验证数据真实性...'),
        ('Step 5: 法律合规分析', 70, 120, '正在分析法律和合同风险...'),
        ('Step 5.5: 资质核验与管理层背景', 80, 60, '正在核验资质和股东背景...'),
        ('Step 5.6: 运营数据分析', 85, 10, '正在分析运营数据...'),
        ('Step 5.7: OTA渠道协议分析', 90, 10, '正在分析OTA渠道协议...'),
        ('Step 6: 生成报告', 95, 30, '正在生成最终报告...'),
    ]
    
    progress = 0
    current_step = '正在初始化...'
    current_step_index = -1
    for i, (step_name, pct, base_time, desc) in enumerate(steps):
        if step_name in all_logs:
            progress = pct
            current_step = desc
            current_step_index = i
    
    if done:
        progress = 100
        if status == 'failed':
            current_step = '分析失败'
        elif status == 'stopped':
            current_step = '已中止'
        else:
            current_step = '分析完成！'

    # 动态预测剩余时间：根据实际前面步骤的速度调整
    import time
    # 用日志文件的创建时间算已运行时间，不是目录时间（避免旧任务显示几百分钟）
    log_create_time = log_path.stat().st_ctime
    elapsed = time.time() - log_create_time
    elapsed_text = f"{int(elapsed // 60)}分{int(elapsed % 60)}秒"
    
    idle_seconds = time.time() - log_path.stat().st_mtime
    stale = (not done) and (idle_seconds > 600 or elapsed > 3600)

    # 超过 30 分钟还没结果：提示可能卡住，但不替用户做决定
    if elapsed > 1800 and not done:
        predicted_text = '可能已卡住，可点「中止分析」'
    else:
        # 基准总时间：把所有步骤的基准耗时加起来
        total_base_time = sum(step[2] for step in steps)
        
        # 计算已经完成的步骤的基准总时间
        if current_step_index >= 0:
            done_base_time = sum(steps[i][2] for i in range(current_step_index + 1))
        else:
            done_base_time = 0
        
        # 速度系数：实际用了多久 / 基准应该用多久
        if done_base_time > 0:
            speed_factor = elapsed / done_base_time
        else:
            speed_factor = 1.0
        
        # 剩余基准时间
        remaining_base_time = total_base_time - done_base_time
        # 预测剩余时间 = 剩余基准时间 * 速度系数
        predicted_remaining = remaining_base_time * speed_factor
        
        # 格式化预测时间
        pred_min = int(predicted_remaining // 60)
        pred_sec = int(predicted_remaining % 60)
        predicted_text = f"约{pred_min}分{pred_sec}秒" if not done else "已完成"

    # 过滤日志：去掉PaddleOCR的DEBUG刷屏，只保留有用信息
    filtered_logs = []
    for line in logs:
        line_str = line.strip()
        # 跳过PaddleOCR的DEBUG输出
        if any(skip in line_str for skip in ['ppocr DEBUG', 'Namespace(', 'dt_boxes num', 'cls num', 'rec_res num']):
            continue
        filtered_logs.append(line_str)

    # 只有正常完成才清理原始资料；失败/中止要保留，方便一键重试
    if done and status not in ('failed', 'stopped'):
        zip_path = task_dir / 'input.zip'
        data_dir = task_dir / 'data'
        try:
            if zip_path.exists():
                zip_path.unlink()
            if data_dir.exists():
                import shutil
                shutil.rmtree(str(data_dir))
        except Exception as e:
            print(f"清理文件失败: {e}")

    return jsonify({
        'logs': filtered_logs[-50:],  # 只返回最后50行有用的日志（已过滤OCR刷屏）
        'done': done,
        'status': status,
        'error': error,
        'error_level': error_level,
        'degraded_modules': degraded_modules,
        'stale': stale,
        'progress': progress,
        'current_step': current_step,
        'elapsed_time': elapsed_text,
        'predicted_time': predicted_text
    })


@app.route('/report/<task_id>')
def report(task_id):
    """报告阅读页面"""
    task_dir = OUTPUT_FOLDER / task_id
    if not task_dir.exists():
        flash('任务不存在')
        return redirect(url_for('index'))

    output_dir = task_dir / 'output'
    
    # 找到报告文件
    html_files = list(output_dir.glob('*.html')) if output_dir.exists() else []
    md_files = list(output_dir.glob('*.md')) if output_dir.exists() else []
    xlsx_files = list(output_dir.glob('*.xlsx')) if output_dir.exists() else []
    docx_files = list(output_dir.glob('*.docx')) if output_dir.exists() else []

    # 读取HTML报告内容
    report_html = ''
    if html_files:
        with open(html_files[0], 'r', encoding='utf-8') as f:
            report_html = f.read()
        
        # 替换图片路径：把 charts/xxx.png 改成 /report_file/task_id/charts/xxx.png
        report_html = report_html.replace('src="charts/', f'src="/report_file/{task_id}/charts/')
        report_html = report_html.replace("src='charts/", f"src='/report_file/{task_id}/charts/'")
        # 证据链接：evidence/xxx 改成可访问的静态路径
        report_html = report_html.replace('href="evidence/', f'href="/report_file/{task_id}/evidence/')
        report_html = report_html.replace("href='evidence/", f"href='/report_file/{task_id}/evidence/'")

    # 读取分析日志
    log_path = task_dir / 'analysis.log'
    log_lines = []
    file_count = 0
    if log_path.exists():
        with open(str(log_path), 'r', encoding='utf-8') as f:
            log_lines = f.readlines()
            for line in log_lines:
                if '扫描到' in line and '个文件' in line:
                    # 提取数字
                    import re
                    match = re.search(r'扫描到 (\d+) 个文件', line)
                    if match:
                        file_count = int(match.group(1))
                    break

    # 读取文件清单JSON
    inventory_data = None
    inventory_path = output_dir / 'file_inventory.json' if output_dir.exists() else None
    if inventory_path and inventory_path.exists():
        import json
        with open(str(inventory_path), 'r', encoding='utf-8') as f:
            inventory_data = json.load(f)

    # 如果没有报告，用LLM总结失败原因
    failure_reason = ''
    if not html_files and log_lines:
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from dd_agent.llm.client import LLMClient
            # 用运行时配置（设置页/我的模型），不写死 ollama
            llm = LLMClient()
            if not llm.available:
                raise RuntimeError("LLM 不可用")

            log_text = ''.join(log_lines[-50:])  # 取最后50行日志总结
            prompt = f"""你是财务尽调专家。下面是一个分析任务的日志，这个任务没有生成最终报告。
请用1-2句话总结：这个任务为什么没生成报告？核心失败原因是什么？
要求：简洁、直接、不超过50字。

日志：
{log_text}

总结："""
            reason = llm.generate(prompt, max_tokens=100)
            if not reason or reason.startswith("（LLM调用失败"):
                raise RuntimeError(reason or "LLM 返回空")
            failure_reason = reason
        except Exception as e:
            # LLM调用失败，用规则兜底
            all_logs = ''.join(log_lines)
            if '未找到财务报表文件' in all_logs:
                failure_reason = '未找到利润表/资产负债表等标准财务报表，无法生成财务分析报告。请确认资料包是否包含财务报表Excel文件。'
            else:
                failure_reason = '分析过程中出现错误，请查看下方完整日志排查具体问题。'

    verdict = None
    if output_dir.exists():
        verdict_path = output_dir / 'verdict.json'
        if verdict_path.exists():
            try:
                with open(str(verdict_path), 'r', encoding='utf-8') as f:
                    verdict = json.load(f)
            except (OSError, ValueError):
                verdict = None

    return render_template(
        'report.html',
        task_id=task_id,
        report_html=report_html,
        html_files=[f.name for f in html_files],
        md_files=[f.name for f in md_files],
        xlsx_files=[f.name for f in xlsx_files],
        docx_files=[f.name for f in docx_files],
        file_count=file_count,
        inventory_data=inventory_data,
        log_lines=[l.strip() for l in log_lines] if log_lines else [],
        failure_reason=failure_reason,
        verdict=verdict,
    )


@app.route('/download/<task_id>/<filename>')
def download(task_id, filename):
    """下载报告文件"""
    task_dir = OUTPUT_FOLDER / task_id / 'output'
    return send_file(str(task_dir / filename), as_attachment=True)


@app.route('/report_file/<task_id>/<path:filepath>')
def report_file(task_id, filepath):
    """访问报告里的静态文件（图表图片等）"""
    task_dir = OUTPUT_FOLDER / task_id / 'output'
    return send_file(str(task_dir / filepath))


def _kill_task_process(task_dir: Path) -> bool:
    """按 pid.txt 终止分析子进程（Windows 杀进程树，Linux 杀进程组）。"""
    pid_file = task_dir / 'pid.txt'
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        return False

    try:
        if os.name == 'nt':
            subprocess.run(
                ['taskkill', '/PID', str(pid), '/T', '/F'],
                capture_output=True, timeout=10,
            )
        else:
            import signal
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        return True
    except Exception:
        return False


@app.route('/api/stop/<task_id>', methods=['POST'])
def stop_task(task_id):
    """中止正在跑的任务，并把状态标成 stopped。"""
    task_dir = OUTPUT_FOLDER / task_id
    if not task_dir.exists():
        return jsonify({"ok": False, "message": "任务不存在"}), 404

    killed = _kill_task_process(task_dir)
    output_dir = task_dir / 'output'
    output_dir.mkdir(exist_ok=True)
    meta_path = output_dir / 'meta.json'

    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            meta = {}
    meta['status'] = 'stopped'
    meta.setdefault('company_name', task_dir.name)
    meta.setdefault('steps_status', [])
    meta['error'] = '用户手动中止'
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')

    return jsonify({
        "ok": True,
        "message": "已中止" if killed else "进程已结束或找不到，已标记为已中止",
    })


@app.route('/retry/<task_id>', methods=['POST'])
def retry_task(task_id):
    """用原 zip / 已解压数据重跑；OCR 有 MD5 缓存，通常很快。"""
    task_dir = OUTPUT_FOLDER / task_id
    if not task_dir.exists():
        return jsonify({"ok": False, "message": "任务不存在"}), 404

    meta = {}
    meta_path = task_dir / 'meta.json'
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            meta = {}
    industry = meta.get('industry') or 'generic'

    data_dir = task_dir / 'data'
    if not data_dir.exists() or not any(data_dir.iterdir()):
        zip_path = task_dir / 'input.zip'
        if not zip_path.exists():
            return jsonify({"ok": False, "message": "原始资料已清理，请重新上传"}), 400
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(str(zip_path), 'r') as zip_ref:
                zip_ref.extractall(str(data_dir))
        except Exception as exc:
            return jsonify({"ok": False, "message": f"解压失败：{exc}"}), 400

    sub_dirs = [d for d in data_dir.iterdir() if d.is_dir() and not d.name.startswith('.')]
    actual_data = sub_dirs[0] if len(sub_dirs) == 1 else data_dir

    _kill_task_process(task_dir)

    output_dir = task_dir / 'output'
    if output_dir.exists():
        shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    _launch_analysis(task_dir, actual_data, output_dir, industry)
    return jsonify({"ok": True, "message": "已重新开始", "task_id": task_id})


@app.route('/deepread/<task_id>', methods=['POST'])
def deep_read(task_id):
    """对单个文件做深度提取：PDF 前 10 页 / 图片 OCR，返回文本和分类建议。"""
    task_dir = OUTPUT_FOLDER / task_id
    if not task_dir.exists():
        return jsonify({"ok": False, "message": "任务不存在"}), 404

    data = request.get_json(silent=True) or {}
    rel_path = (data.get("path") or "").strip()
    if not rel_path:
        return jsonify({"ok": False, "message": "缺少文件路径"}), 400

    root = (task_dir / 'data').resolve()
    target = (root / rel_path).resolve()
    if target != root and root not in target.parents:
        return jsonify({"ok": False, "message": "路径非法"}), 400
    if not target.exists():
        return jsonify({"ok": False, "message": "文件不存在（可能已被清理）"}), 404

    text = ""
    try:
        ext = target.suffix.lower()
        if ext == ".pdf":
            # 先读文本层（文字版秒回），没有文字再 OCR（扫描版才慢）
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(str(target))
            try:
                pages = min(len(pdf), 10)
                text = "\n".join(pdf[i].get_textpage().get_text_range() or "" for i in range(pages))
            finally:
                pdf.close()
            if not text.strip():
                from dd_agent.ocr.extractor import OCRExtractor
                ocr = OCRExtractor()
                if ocr.available:
                    text = ocr.extract_pdf(str(target), max_pages=10) or ""
        elif ext in (".jpg", ".jpeg", ".png"):
            from dd_agent.ocr.extractor import OCRExtractor
            ocr = OCRExtractor()
            if ocr.available:
                import numpy as np
                from PIL import Image
                result = ocr._get_paddleocr().ocr(np.array(Image.open(target)), cls=True)
                if result and result[0]:
                    text = " ".join(line[1][0] for line in result[0])
    except Exception as exc:
        return jsonify({"ok": False, "message": f"提取失败：{exc}"}), 500

    text = (text or "").strip()
    hint = ""
    try:
        from dd_agent.inventory.scanner import FileScanner
        lower = text.lower()
        for category, keywords in FileScanner.CATEGORY_RULES:
            if any(k.lower() in lower for k in keywords):
                hint = category
                break
    except Exception:
        pass

    # 追加到报告正文，并重新渲染 HTML / Word
    updated = False
    if text:
        try:
            from dd_agent.reporter.generator import ReportGenerator
            from dd_agent.reporter.docx_export import markdown_to_docx

            output_dir = task_dir / 'output'
            md_files = [p for p in output_dir.glob('*.md') if not p.name.endswith('.polished.md')]
            if md_files:
                md_path = md_files[0]
                content = md_path.read_text(encoding='utf-8')
                if '## 附录：人工深读补充' not in content:
                    content += "\n\n---\n\n## 附录：人工深读补充（点击「深读」后追加）\n"
                content += f"\n### {target.name}\n\n{text[:5000]}\n"
                md_path.write_text(content, encoding='utf-8')

                generator = ReportGenerator.__new__(ReportGenerator)
                md_path.with_suffix('.html').write_text(
                    generator._md_to_html(content), encoding='utf-8'
                )
                try:
                    markdown_to_docx(content, str(md_path.with_suffix('.docx')), base_dir=str(output_dir))
                except Exception:
                    pass
                updated = True
        except Exception as exc:
            print(f"深读追加报告失败: {exc}")

    return jsonify({
        "ok": True,
        "chars": len(text),
        "text": text[:3000],
        "category_hint": hint,
        "updated": updated,
    })


@app.route('/delete/<task_id>', methods=['POST'])
def delete_task(task_id):
    """删除任务：先终止子进程，再删目录，避免进程写已删除的目录。"""
    task_dir = OUTPUT_FOLDER / task_id
    if task_dir.exists():
        _kill_task_process(task_dir)
        import shutil
        shutil.rmtree(str(task_dir), ignore_errors=True)
    return redirect(url_for('index'))


@app.route('/api/classify/<task_id>', methods=['POST'])
def fix_classification(task_id):
    """把人工修正写进 classify_overrides.json，下次分析自动生效。"""
    data = request.get_json(silent=True) or {}
    rel_path = (data.get("path") or "").strip()
    category = (data.get("category") or "").strip()
    module = (data.get("module") or "").strip()
    allowed_modules = {"financial", "business", "legal", "qualification", "management", "other"}
    if not rel_path or not category or module not in allowed_modules:
        return jsonify({"ok": False, "message": "参数不完整"}), 400

    from dd_agent.inventory.scanner import save_override
    save_override(rel_path, category, module)
    return jsonify({"ok": True, "message": "已记录，下次分析生效"})


if __name__ == '__main__':
    host = os.getenv("DD_HOST", "127.0.0.1")
    port = int(os.getenv("DD_PORT", "5000"))
    debug = os.getenv("DD_DEBUG", "0") == "1"
    print("=" * 50)
    print("DD-Agent Web")
    print(f"访问: http://{host}:{port}")
    print("=" * 50)
    app.run(debug=debug, port=port, host=host)
