import subprocess
import sys
import threading
import uuid
import time
from datetime import datetime

from django import forms
from django.shortcuts import render
from django.conf import settings
from openai import OpenAI
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import urllib.request
import urllib.error
import json
import os
from django.http import HttpResponse
from django.urls import reverse
from django.template.loader import render_to_string
import csv
import shutil
import urllib.parse

# Simple in-memory task queue (FIFO) and worker
TASK_QUEUE = []  # list of dicts: {run_id, task, model, enqueued_at}
TASK_QUEUE_LOCK = threading.Lock()
TASKS_LOCK = threading.Lock()
_QUEUE_WORKER_STARTED = False


def _tasks_path():
    return os.path.join(settings.BASE_DIR, "tasks.json")


def _load_tasks():
    try:
        with open(_tasks_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save_tasks(tasks):
    temp_path = _tasks_path() + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, _tasks_path())


def _update_task(run_id, **changes):
    with TASKS_LOCK:
        tasks = _load_tasks()
        for task in tasks:
            if task.get("run_id") == run_id:
                task.update(changes)
                _save_tasks(tasks)
                return


def enqueue_task(task_text, model=None, priority="medium"):
    run_id = uuid.uuid4().hex
    if priority not in {"low", "medium", "high", "urgent"}:
        priority = "medium"
    now = datetime.utcnow().isoformat() + "Z"
    item = {
        "run_id": run_id,
        "task": task_text,
        "model": model,
        "priority": priority,
        "enqueued_at": now,
    }
    # write an initial empty log to ensure SSE readers can open file
    dirp = os.path.join(settings.BASE_DIR, "task_runs")
    try:
        os.makedirs(dirp, exist_ok=True)
        open(os.path.join(dirp, f"{run_id}.log"), "a", encoding="utf-8").close()
    except Exception:
        pass
    with TASK_QUEUE_LOCK:
        TASK_QUEUE.append(item)
        position = len(TASK_QUEUE)
    with TASKS_LOCK:
        tasks = _load_tasks()
        tasks.append(
            {
                "run_id": run_id,
                "title": task_text[:120],
                "task": task_text,
                "priority": priority,
                "status": "queued",
                "enqueued_at": now,
                "completed_at": None,
            }
        )
        _save_tasks(tasks)
    return run_id, position


def _start_queue_worker_once():
    global _QUEUE_WORKER_STARTED
    if _QUEUE_WORKER_STARTED:
        return
    _QUEUE_WORKER_STARTED = True

    def worker():
        while True:
            item = None
            with TASK_QUEUE_LOCK:
                if TASK_QUEUE:
                    item = TASK_QUEUE.pop(0)
            if item is None:
                time.sleep(0.5)
                continue
            _update_task(item["run_id"], status="running", started_at=datetime.utcnow().isoformat() + "Z")
            # run the task (wrap to pass run_id)
            try:
                wrapper = {"__meta__": {"run_id": item["run_id"], "console": False}, "prompt": item["task"]}
                # log that it started
                dirp = os.path.join(settings.BASE_DIR, "task_runs")
                path = os.path.join(dirp, f"{item['run_id']}.log")
                try:
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(json.dumps({"ts": datetime.utcnow().isoformat() + "Z", "type": "queued", "text": "Started processing"}, ensure_ascii=False) + "\n")
                except Exception:
                    pass
                result = run_self_healing_loop(wrapper, model_name=item.get("model"))
                succeeded = isinstance(result, dict) and result.get("success") is True
                _update_task(
                    item["run_id"],
                    status="completed" if succeeded else "failed",
                    completed_at=datetime.utcnow().isoformat() + "Z",
                )
            except Exception as exc:
                _update_task(
                    item["run_id"],
                    status="failed",
                    completed_at=datetime.utcnow().isoformat() + "Z",
                    error=str(exc),
                )
                # ensure errors are logged to the run file
                try:
                    dirp = os.path.join(settings.BASE_DIR, "task_runs")
                    path = os.path.join(dirp, f"{item['run_id']}.log")
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(json.dumps({"ts": datetime.utcnow().isoformat() + "Z", "type": "exception", "text": "Worker exception"}, ensure_ascii=False) + "\n")
                except Exception:
                    pass

    t = threading.Thread(target=worker, daemon=True)
    t.start()


# Configure OpenAI client using OmniRoute settings from Django settings
omni_cfg = getattr(settings, "OMNIROUTE", {})
client = OpenAI(
    base_url=omni_cfg.get("BASE_URL", "http://localhost:3001/v1"),
    api_key=omni_cfg.get("API_KEY", "omniroute-local"),
    timeout=omni_cfg.get("TIMEOUT", 30),
)


class TaskForm(forms.Form):
    task = forms.CharField(
        label="Görev",
        widget=forms.Textarea(
            attrs={
                "rows": 8,
                "placeholder": "Örnek: 1'den 100'e kadar asal sayıları listeleyen Python fonksiyonunu yaz.",
            }
        ),
    )
    model = forms.ChoiceField(
        label="Model",
        required=False,
        help_text="OmniRoute modeli; 'Auto' seçili ise ayarlardaki varsayılan kullanılır.",
    )

    def __init__(self, *args, model_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        if model_choices is None:
            model_choices = []
        # ensure 'auto' is the first/top option
        auto_label = f"Auto (use default: {getattr(settings, 'OMNIROUTE_DEFAULT_MODEL', 'auto')})"
        choices = [("auto", auto_label)] + [(m, m) for m in model_choices]
        self.fields["model"].choices = choices
        # default to 'auto'
        self.fields["model"].initial = "auto"


def extract_code_block(text):
    if "```python" in text:
        parts = text.split("```python")
        if len(parts) > 1:
            return parts[1].split("```")[0].strip()
    elif "```" in text:
        parts = text.split("```")
        if len(parts) > 1:
            return parts[1].strip()
    return None


def run_self_healing_loop(initial_prompt, model_name=None, max_iterations=5):
    # model name may be provided in the prompt wrapper (we set default later)
    # allow callers to pass a wrapper dict: {"__meta__": {...}, "prompt": "..."}
    prompt_text = None
    if isinstance(initial_prompt, dict) and isinstance(initial_prompt.get("__meta__"), dict):
        prompt_text = initial_prompt.get("prompt") or initial_prompt.get("task") or ""
    else:
        prompt_text = initial_prompt

    conversation_history = [
        {
            "role": "system",
            "content": "You are an autonomous senior developer agent. Write ONLY valid, executable Python code inside a markdown block ```python ... ``` for the given task. Do not include extra conversational filler.",
        },
        {"role": "user", "content": prompt_text},
    ]

    # priority: explicit model_name parameter -> settings default
    if not model_name:
        model_name = getattr(settings, 'OMNIROUTE_DEFAULT_MODEL', 'auto/best-mode')

    # logging: optional run_id may be set in the outer scope by callers.
    run_id = None
    console = False
    # callers may pass in a thread-local override via attributes on the prompt tuple
    # (backwards compatible: if initial_prompt is a dict with meta fields, unpack)
    if isinstance(initial_prompt, dict) and initial_prompt.get("__meta__"):
        meta = initial_prompt.pop("__meta__")
        run_id = meta.get("run_id")
        console = meta.get("console", False)

    def _get_run_dir():
        dirp = os.path.join(settings.BASE_DIR, "task_runs")
        try:
            os.makedirs(dirp, exist_ok=True)
        except Exception:
            pass
        return dirp

    def _ensure_run_id():
        nonlocal run_id
        if not run_id:
            run_id = uuid.uuid4().hex
        # ensure file exists
        open(os.path.join(_get_run_dir(), f"{run_id}.log"), "a", encoding="utf-8").close()
        return run_id

    def _append_log(entry_type, text):
        # entry: JSON-line with ts, type, text
        try:
            rid = _ensure_run_id()
            path = os.path.join(_get_run_dir(), f"{rid}.log")
            obj = {"ts": datetime.utcnow().isoformat() + "Z", "type": entry_type, "text": str(text)}
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            if console:
                print(f"[{entry_type}] {text}", flush=True)
        except Exception:
            # best-effort logging; do not raise
            pass

    for i in range(max_iterations):
        try:
            _append_log("info", f"Iteration {i+1}/{max_iterations} - sending request to model {model_name}")
            response = client.chat.completions.create(
                model=model_name,
                messages=conversation_history,
                temperature=0.2,
            )
            content = response.choices[0].message.content
            _append_log("chat_response", content)
            code = extract_code_block(content)
            if not code:
                _append_log("warning", "Assistant did not return a python code block; asking for correction")
                conversation_history.append({"role": "assistant", "content": content})
                conversation_history.append(
                    {"role": "user", "content": "Geçerli bir python kod bloğu (```python ... ```) vermedin. Lütfen sadece çalıştırılabilir kodu ver."}
                )
                continue

            script_filename = "generated_task.py"
            with open(script_filename, "w", encoding="utf-8") as file:
                file.write(code)
            _append_log("code", code)

            # run the generated script and stream stdout/stderr to the run log
            _append_log("info", f"Executing {script_filename}")
            # Ensure child Python runs in UTF-8 mode on Windows to avoid CP1252 encode errors
            env = os.environ.copy()
            env.setdefault("PYTHONUTF8", "1")
            # Use -X utf8 to force UTF-8 mode as well
            proc = subprocess.Popen([sys.executable, "-X", "utf8", script_filename], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', env=env)
            # read stdout/stderr as they arrive
            stdout_lines = []
            stderr_lines = []
            while True:
                out = proc.stdout.readline() if proc.stdout is not None else ''
                err = proc.stderr.readline() if proc.stderr is not None else ''
                if out:
                    out = out.rstrip("\n")
                    stdout_lines.append(out)
                    _append_log("stdout", out)
                if err:
                    err = err.rstrip("\n")
                    stderr_lines.append(err)
                    _append_log("stderr", err)
                if out == '' and err == '' and proc.poll() is not None:
                    break

            result_stdout = "\n".join(stdout_lines)
            result_stderr = "\n".join(stderr_lines)
            returncode = proc.returncode
            if returncode == 0:
                _append_log("info", "Process completed successfully")
                return {"success": True, "output": result_stdout.strip() or "Kod başarıyla çalıştı; çıktı yok.", "run_id": run_id}

            error_output = result_stderr or result_stdout
            conversation_history.append({"role": "assistant", "content": content})
            feedback_prompt = (
                "Yazdığın kod şu hatayı verdi:\n"
                f"{error_output}\n"
                "Lütfen hatayı analiz et, eksikleri gider ve düzeltilmiş Python kodunun tamamını tekrar ver."
            )
            _append_log("error", f"Execution failed (code={proc.returncode}): {error_output}")
            conversation_history.append({"role": "user", "content": feedback_prompt})

        except Exception as exc:
            # If the error indicates an invalid model, try to fetch available models and suggest alternatives
            err_text = str(exc)
            _append_log("exception", err_text)
            if "not a valid" in err_text or "Unknown built-in" in err_text or "invalid_request_error" in err_text:
                try:
                    # probe OmniRoute for models
                    omni_cfg = getattr(settings, "OMNIROUTE", {})
                    base = omni_cfg.get("BASE_URL", "http://localhost:3001/v1").rstrip("/")
                    timeout = omni_cfg.get("TIMEOUT", 5)
                    candidates = [base + "/models", base, base + "/v1/models"]
                    model_ids = []
                    for url in candidates:
                        try:
                            with urllib.request.urlopen(url, timeout=timeout) as resp:
                                body = resp.read(65536).decode(errors="replace")
                                parsed = None
                                try:
                                    parsed = json.loads(body)
                                except Exception:
                                    # sometimes the response is a JSON string
                                    try:
                                        parsed = json.loads(json.loads(body))
                                    except Exception:
                                        parsed = None
                                if isinstance(parsed, dict):
                                    data = parsed.get("data") or parsed.get("models") or parsed
                                    if isinstance(data, list):
                                        for it in data:
                                            if isinstance(it, dict) and it.get("id"):
                                                model_ids.append(it.get("id"))
                                    elif isinstance(parsed.get("data"), str):
                                        # sometimes data is a JSON string
                                        try:
                                            dd = json.loads(parsed.get("data"))
                                            if isinstance(dd, list):
                                                for it in dd:
                                                    if isinstance(it, dict) and it.get("id"):
                                                        model_ids.append(it.get("id"))
                                        except Exception:
                                            pass
                                elif isinstance(parsed, list):
                                    for it in parsed:
                                        if isinstance(it, dict) and it.get("id"):
                                            model_ids.append(it.get("id"))
                                # if we found any, stop
                                if model_ids:
                                    break
                        except Exception:
                            continue

                    suggestion_text = ""
                    if model_ids:
                        top = model_ids[:8]
                        suggestion_text = "\nÖnerilen modeller: " + ", ".join(top)
                        _append_log("info", "Model suggestions: " + ",".join(top))
                    else:
                        suggestion_text = "\nModel listesi alınamadı; lütfen `/omni/models/` endpoint'ini kontrol edin."

                    return {"success": False, "output": f"Hata oluştu: {err_text}\n{suggestion_text}"}
                except Exception:
                    _append_log("exception", "Failed while probing models: " + str(exc))
                    return {"success": False, "output": f"Hata oluştu: {err_text}"}

            return {"success": False, "output": f"Hata oluştu: {err_text}"}

    return {
        "success": False,
        "output": "Maksimum deneme sayısına ulaşıldı ancak görev tam olarak çözülemedi.",
    }


def index(request):
    # Do NOT fetch model list during page render to avoid blocking the UI.
    # The frontend will populate model choices via AJAX using `/omni/models/`.
    # Keep a local placeholder; only fetch cached model ids when handling POST to
    # avoid blocking the initial GET render.
    form = TaskForm(model_choices=[])
    model_choices = []
    result = None

    if request.method == "POST":
        # try to fetch a short-lived cached model list for the POST handling
        try:
            model_choices = get_model_ids_cached() or []
        except Exception:
            model_choices = []
        form = TaskForm(request.POST, model_choices=model_choices)
        if form.is_valid():
            task = form.cleaned_data["task"]
            model = form.cleaned_data.get("model")
            # interpret 'auto' as None to use settings default
            if model == "auto" or not model:
                model = None
            result = run_self_healing_loop(task, model_name=model)
    return render(
        request,
        "agent_app/index.html",
        {"form": form, "result": result, "default_model": getattr(settings, "OMNIROUTE_DEFAULT_MODEL", "auto/best-chat")},
    )


def runs_page(request):
    """Render the full recent-runs history screen."""
    return render(request, "agent_app/runs.html")


def start_task(request):
    """Start a background task run and return a run_id for polling logs.

    Accepts POST form fields: 'task' and optional 'model'. Returns JSON {ok: True, run_id: '...'}.
    """
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)

    task = (request.POST.get("task") or request.POST.get("task") or "").strip()
    if not task:
        return JsonResponse({"ok": False, "error": "Task is required"}, status=400)
    model = request.POST.get("model") or None
    if model == "auto" or not model:
        model = None

    # enqueue the task to be processed sequentially by the worker
    _start_queue_worker_once()
    run_id, position = enqueue_task(task, model=model)
    return JsonResponse({"ok": True, "run_id": run_id, "position": position})


def _parse_assistant_decision(content):
    """Parse the model's intent decision without requiring provider JSON mode."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        decision = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            decision = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(decision, dict):
        return None
    intent = str(decision.get("intent", "")).strip().lower()
    if intent not in {"chat", "execute", "health", "models"}:
        return None
    return {
        "intent": intent,
        "message": str(decision.get("message") or "").strip(),
        "task": str(decision.get("task") or "").strip(),
        "model": str(decision.get("model") or "").strip(),
        "priority": str(decision.get("priority") or "medium").strip().lower(),
    }


def assistant_message(request):
    """Understand a natural-language message and chat or start the right action."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)

    message = (request.POST.get("message") or request.POST.get("task") or "").strip()
    if not message:
        return JsonResponse({"ok": False, "error": "Message is required"}, status=400)

    system_prompt = """You are EMOG's intent router. Read the user's Turkish or English message and return ONLY valid JSON.
Choose exactly one intent:
- chat: the user wants an explanation, answer, brainstorming, or conversation. Put a concise helpful answer in message.
- execute: the user asks EMOG to write/run/test/automate code or perform a concrete computer task. Put the complete task instruction in task.
- health: the user asks to check whether the model service/OmniRoute is reachable.
- models: the user asks which models are available.
Never execute code for a question that only asks for an explanation. Never invent a result.
Priority must be one of low, medium, high, urgent. Use urgent only when the user explicitly says it is urgent or blocking.
JSON shape: {"intent":"chat|execute|health|models","message":"...","task":"...","model":"","priority":"low|medium|high|urgent"}"""
    try:
        response = client.chat.completions.create(
            model=getattr(settings, "OMNIROUTE_DEFAULT_MODEL", "auto/best-chat"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
            temperature=0,
        )
        content = response.choices[0].message.content or ""
        decision = _parse_assistant_decision(content)
    except Exception as exc:
        return JsonResponse(
            {
                "ok": False,
                "error": "Niyet algılama servisine ulaşılamadı.",
                "detail": str(exc),
            },
            status=502,
        )

    if decision is None:
        return JsonResponse(
            {"ok": False, "error": "Model, isteğin niyetini geçerli biçimde belirleyemedi."},
            status=502,
        )

    if decision["intent"] == "chat":
        return JsonResponse(
            {
                "ok": True,
                "intent": "chat",
                "reply": decision["message"] or "İsteğinizi anladım. Biraz daha ayrıntı verebilir misiniz?",
            }
        )

    if decision["intent"] == "health":
        return omni_health(request)

    if decision["intent"] == "models":
        return omni_models(request)

    task = decision["task"] or message
    model = decision["model"] or None
    if model == "auto":
        model = None
    _start_queue_worker_once()
    run_id, position = enqueue_task(task, model=model, priority=decision["priority"])
    queue_message = (
        "Göreviniz çalıştırılıyor."
        if position == 1
        else f"Göreviniz otomatik olarak görev listesine eklendi. Sıradaki konumu: {position}."
    )
    return JsonResponse(
        {
            "ok": True,
            "intent": "execute",
            "reply": queue_message,
            "run_id": run_id,
            "position": position,
            "priority": decision["priority"],
        }
    )


def task_logs(request, run_id):
    """Return JSON-lines for a run log. Query param `since` (int) returns entries after that 0-based index."""
    since = 0
    try:
        since = int(request.GET.get("since", 0))
    except Exception:
        since = 0

    dirp = os.path.join(settings.BASE_DIR, "task_runs")
    path = os.path.join(dirp, f"{run_id}.log")
    lines = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        lines.append(json.loads(line))
                    except Exception:
                        lines.append({"ts": None, "type": "raw", "text": line})
        except Exception as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=500)
    else:
        return JsonResponse({"ok": True, "lines": [], "count": 0})

    total = len(lines)
    new = lines[since:]
    return JsonResponse({"ok": True, "lines": new, "count": total})


def queue_list(request):
    """Return the single source of truth for currently waiting tasks."""
    with TASK_QUEUE_LOCK:
        items = [
            {
                "run_id": i["run_id"],
                "title": i.get("task"),
                "task": i.get("task"),
                "priority": i.get("priority", "medium"),
                "status": "queued",
                "enqueued_at": i.get("enqueued_at"),
            }
            for i in TASK_QUEUE
        ]
    return JsonResponse({"ok": True, "queue": items, "count": len(items)})


def tasks_list(request):
    """Return the same currently waiting tasks shown by the chat queue."""
    with TASK_QUEUE_LOCK:
        tasks = [
            {
                "run_id": i["run_id"],
                "title": i.get("task"),
                "task": i.get("task"),
                "model": i.get("model"),
                "priority": i.get("priority", "medium"),
                "status": "queued",
                "enqueued_at": i.get("enqueued_at"),
            }
            for i in TASK_QUEUE
        ]
    return JsonResponse({"ok": True, "tasks": tasks})


def tasks_page(request):
    return render(request, "agent_app/tasks.html")


def sse_task_stream(request, run_id):
    """Server-Sent Events stream for a run's log file. Streams new JSON-lines as `data: <json>` events."""
    from django.http import StreamingHttpResponse

    dirp = os.path.join(settings.BASE_DIR, "task_runs")
    path = os.path.join(dirp, f"{run_id}.log")

    def event_stream():
        last_pos = 0
        last_send = time.time()
        heartbeat_interval = 15.0  # seconds; send heartbeat if no new lines
        # if file doesn't exist yet, wait for it
        waited = 0
        while not os.path.exists(path) and waited < 5:
            time.sleep(0.2)
            waited += 0.2
        while True:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    f.seek(last_pos)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        # send as data: <json>\n\n
                        # ensure newline-safe JSON payload
                        yield f"data: {line}\n\n"
                        last_send = time.time()
                    last_pos = f.tell()
            except Exception:
                # if file missing or read error, just wait and retry
                pass
            # if no new data for a while, send a lightweight heartbeat to keep the connection alive
            now = time.time()
            if now - last_send >= heartbeat_interval:
                try:
                    # send a heartbeat event as JSON so clients can ignore it reliably
                    yield 'data: {"type":"heartbeat","text":""}\n\n'
                except Exception:
                    pass
                last_send = now
            time.sleep(0.5)

    return StreamingHttpResponse(event_stream(), content_type="text/event-stream")


@csrf_exempt
def run_task_no_csrf(request):
    """Development-only endpoint to start a background task without CSRF (for automation/testing)."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)

    task = (request.POST.get("task") or request.POST.get("task") or "").strip()
    if not task:
        return JsonResponse({"ok": False, "error": "Task is required"}, status=400)
    model = request.POST.get("model") or None
    if model == "auto" or not model:
        model = None

    run_id = uuid.uuid4().hex

    wrapper = {"__meta__": {"run_id": run_id, "console": False}, "prompt": task}

    def _runner():
        try:
            run_self_healing_loop(wrapper, model_name=model)
        except Exception as e:
            try:
                dirp = os.path.join(settings.BASE_DIR, "task_runs")
                os.makedirs(dirp, exist_ok=True)
                path = os.path.join(dirp, f"{run_id}.log")
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"ts": datetime.utcnow().isoformat() + "Z", "type": "exception", "text": str(e)}) + "\n")
            except Exception:
                pass

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    return JsonResponse({"ok": True, "run_id": run_id})


@csrf_exempt
def exec_code_no_csrf(request):
    """Development-only: execute provided Python code text and log output to a run file."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)

    code = (request.POST.get("code") or request.POST.get("script") or "").strip()
    if not code:
        return JsonResponse({"ok": False, "error": "Code is required"}, status=400)

    run_id = uuid.uuid4().hex
    dirp = os.path.join(settings.BASE_DIR, "task_runs")
    os.makedirs(dirp, exist_ok=True)
    path = os.path.join(dirp, f"{run_id}.log")

    def _append(entry_type, text):
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": datetime.utcnow().isoformat() + "Z", "type": entry_type, "text": str(text)}, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # write code file
    script_filename = os.path.join(settings.BASE_DIR, "generated_task.py")
    try:
        with open(script_filename, "w", encoding="utf-8") as f:
            f.write(code)
        _append("code", code)
    except Exception as e:
        _append("exception", str(e))
        return JsonResponse({"ok": False, "error": str(e)})

    def _runner():
        try:
            _append("info", f"Executing {script_filename}")
            env = os.environ.copy()
            env.setdefault("PYTHONUTF8", "1")
            proc = subprocess.Popen([sys.executable, "-X", "utf8", script_filename], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', env=env)
            while True:
                out = proc.stdout.readline() if proc.stdout is not None else ''
                err = proc.stderr.readline() if proc.stderr is not None else ''
                if out:
                    _append("stdout", out.rstrip("\n"))
                if err:
                    _append("stderr", err.rstrip("\n"))
                if out == '' and err == '' and proc.poll() is not None:
                    break
            if proc.returncode == 0:
                _append("info", "Process completed successfully")
            else:
                _append("error", f"Process exited {proc.returncode}")
        except Exception as e:
            _append("exception", str(e))

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    return JsonResponse({"ok": True, "run_id": run_id})


# Simple in-memory cache for model ids
_MODEL_CACHE = {"expires": 0, "ids": []}
_MODEL_CACHE_TTL = 120  # seconds (shorter TTL to keep list fresh without blocking)


def get_model_ids_cached():
    import time

    now = time.time()
    if _MODEL_CACHE["expires"] > now and _MODEL_CACHE["ids"]:
        return _MODEL_CACHE["ids"]

    # otherwise fetch fresh
    ids = []
    omni_cfg = getattr(settings, "OMNIROUTE", {})
    base = omni_cfg.get("BASE_URL", "http://localhost:3001/v1")
    timeout = omni_cfg.get("TIMEOUT", 3)
    candidates = [base.rstrip("/") + "/models", base, base.rstrip("/") + "/v1/models"]
    for url in candidates:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                body = resp.read(65536).decode(errors="replace")
                parsed = None
                try:
                    parsed = json.loads(body)
                    if isinstance(parsed, str):
                        try:
                            parsed = json.loads(parsed)
                        except Exception:
                            pass
                except Exception:
                    parsed = None

                if isinstance(parsed, dict):
                    data = parsed.get("data") or parsed.get("models") or parsed
                    if isinstance(data, list):
                        for it in data:
                            if isinstance(it, dict) and it.get("id"):
                                ids.append(it.get("id"))
                    elif isinstance(parsed.get("data"), str):
                        try:
                            dd = json.loads(parsed.get("data"))
                            if isinstance(dd, list):
                                for it in dd:
                                    if isinstance(it, dict) and it.get("id"):
                                        ids.append(it.get("id"))
                        except Exception:
                            pass
                elif isinstance(parsed, list):
                    for it in parsed:
                        if isinstance(it, dict) and it.get("id"):
                            ids.append(it.get("id"))
                # if we found any, stop
                if ids:
                    break
        except Exception:
            continue

    # dedupe and limit
    uniq = []
    for x in ids:
        if x not in uniq:
            uniq.append(x)
    _MODEL_CACHE["ids"] = uniq[:500]
    _MODEL_CACHE["expires"] = now + _MODEL_CACHE_TTL
    return _MODEL_CACHE["ids"]


def omni_health(request):
    """Simple health check for the configured OmniRoute provider.

    Tries a few sensible endpoints derived from the configured BASE_URL and
    returns a JSON summary indicating if the provider is reachable.
    """
    omni_cfg = getattr(settings, "OMNIROUTE", {})
    base = omni_cfg.get("BASE_URL", "http://localhost:3001/v1")
    timeout = omni_cfg.get("TIMEOUT", 3)

    candidates = [
        base,
        base.rstrip("/") + "/health",
        base.rstrip("/") + "/v1/health",
        base.rstrip("/") + "/models",
        base.rstrip("/") + "/v1/models",
    ]

    attempts = []
    # try each candidate with a small retry/backoff
    for url in candidates:
        last_err = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(url, timeout=timeout) as resp:
                    status = resp.getcode()
                    body = resp.read(4096).decode(errors="replace")
                    # try parse JSON
                    parsed = None
                    try:
                        parsed = json.loads(body)
                    except Exception:
                        parsed = body

                    attempts.append({"url": url, "ok": True, "status": status, "data": parsed})
                    return JsonResponse(
                        {
                            "ok": True,
                            "message": "OmniRoute reachable",
                            "url": url,
                            "status": status,
                            "data": parsed,
                            "attempts": attempts,
                        }
                    )
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}: {getattr(e, 'reason', str(e))}"
                attempts.append({"url": url, "ok": False, "status": e.code, "error": last_err})
            except Exception as e:
                last_err = str(e)
                attempts.append({"url": url, "ok": False, "error": last_err})
            # small backoff between attempts
            time.sleep(0.3)

    guidance = (
        "OmniRoute erişilemedi. Lütfen OmniRoute'ın çalıştığından, `OMNIROUTE_BASE_URL` ayarının doğru olduğundan"
        " ve port'un (ör. 3001) açık olduğundan emin olun. 'Model Listesi' butonunu da deneyebilirsiniz."
    )
    return JsonResponse({"ok": False, "message": guidance, "attempts": attempts}, status=502)


def omni_models(request):
    """Demo endpoint: fetch model list or basic info from OmniRoute.

    Tries a few candidate URLs and returns the parsed JSON when available.
    """
    omni_cfg = getattr(settings, "OMNIROUTE", {})
    base = omni_cfg.get("BASE_URL", "http://localhost:3001/v1")
    timeout = omni_cfg.get("TIMEOUT", 3)

    candidates = [
        base.rstrip("/") + "/models",
        base,
        base.rstrip("/") + "/v1/models",
    ]

    last_err = None
    # try each candidate with a couple of quick retries to handle transient issues
    for url in candidates:
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=timeout) as resp:
                    status = resp.getcode()
                    body = resp.read(65536).decode(errors="replace")
                    # try to parse JSON (handle nested JSON strings)
                    parsed = None
                    try:
                        parsed = json.loads(body)
                        # if parsed is a string that contains JSON, try again
                        if isinstance(parsed, str):
                            try:
                                parsed2 = json.loads(parsed)
                                parsed = parsed2
                            except Exception:
                                pass
                    except Exception:
                        parsed = body
                    return JsonResponse({"ok": True, "url": url, "status": status, "data": parsed})
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}: {getattr(e, 'reason', str(e))}"
            except Exception as e:
                last_err = str(e)
            time.sleep(0.25)

    return JsonResponse({"ok": False, "error": f"All probes failed. Last error: {last_err}"}, status=502)


def omni_logs(request):
    """Render latest omni log entries in a table and provide CSV download link."""
    logfile = getattr(settings, "OMNI_LOGFILE", os.path.join(settings.BASE_DIR, "omni_health.log"))
    entries = []
    if os.path.exists(logfile):
        try:
            with open(logfile, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        entries.append({"raw": line})
        except Exception as e:
            entries = [{"error": str(e)}]

    # show the most recent 200 entries by default
    recent = list(reversed(entries))[:200]

    return render(request, "agent_app/omni_logs.html", {"entries": recent, "logfile": logfile})


def omni_logs_csv(request):
    logfile = getattr(settings, "OMNI_LOGFILE", os.path.join(settings.BASE_DIR, "omni_health.log"))
    rows = []
    if os.path.exists(logfile):
        with open(logfile, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    rows.append(obj)
                except Exception:
                    rows.append({"raw": line})

    # determine CSV headers
    headers = set()
    for r in rows:
        if isinstance(r, dict):
            headers.update(r.keys())

    headers = sorted(list(headers))

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = "attachment; filename=omni_health.csv"

    writer = csv.writer(response)
    writer.writerow(headers)
    for r in rows:
        writer.writerow([r.get(h, "") if isinstance(r, dict) else r for h in headers])

    return response


def github_page(request):
    """Render a small UI for creating a GitHub repo and pushing the project."""
    return render(request, "agent_app/github.html", {})


@csrf_exempt
def create_github_repo(request):
    """Create a GitHub repo using either the provided token or the `gh` CLI if available.

    POST fields: `repo_name`, `visibility` ('public'|'private'), optional `token`.
    Returns JSON {ok: True, url: 'https://github.com/...'} on success.
    """
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)

    repo_name = (request.POST.get("repo_name") or "").strip()
    visibility = (request.POST.get("visibility") or "private").strip()
    token = (request.POST.get("token") or "").strip()
    if not repo_name:
        return JsonResponse({"ok": False, "error": "repo_name required"}, status=400)

    # admin token check
    def _check_admin_token(req):
        token = (req.POST.get('admin_token') or req.META.get('HTTP_X_ADMIN_TOKEN') or '').strip()
        expected = os.environ.get('ADMIN_API_TOKEN') or getattr(settings, 'ADMIN_API_TOKEN', None)
        return bool(expected and token and token == expected)

    if not _check_admin_token(request):
        return JsonResponse({"ok": False, "error": "Forbidden - missing admin token"}, status=403)

    # prefer gh CLI if available
    gh_path = shutil.which("gh")
    if gh_path:
        try:
            # run: gh repo create <repo_name> --public/--private --confirm
            args = [gh_path, "repo", "create", repo_name]
            if visibility == "public":
                args.append("--public")
            else:
                args.append("--private")
            args.append("--confirm")
            proc = subprocess.run(args, capture_output=True, text=True)
            if proc.returncode == 0:
                # try to construct URL
                user = None
                # `gh repo view --json url` could be used but keep simple
                url = f"https://github.com/{repo_name}"
                return JsonResponse({"ok": True, "url": url})
            else:
                return JsonResponse({"ok": False, "error": proc.stderr or proc.stdout}, status=500)
        except Exception as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=500)

    # fallback: use GitHub REST API with provided token
    if not token:
        return JsonResponse({"ok": False, "error": "No gh CLI and no token provided"}, status=400)

    payload = {"name": repo_name, "private": (visibility != "public")}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request("https://api.github.com/user/repos", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "emog-app")
    req.add_header("Authorization", f"token {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read(65536).decode(errors="replace")
            parsed = json.loads(body)
            return JsonResponse({"ok": True, "url": parsed.get("html_url")})
    except urllib.error.HTTPError as e:
        try:
            err = e.read().decode(errors="replace")
            parsed = json.loads(err)
            message = parsed.get("message") or err
        except Exception:
            message = str(e)
        return JsonResponse({"ok": False, "error": message}, status=500)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@csrf_exempt
def push_repo(request):
    """Initialize git, commit, and push to provided remote URL.

    POST fields: `remote_url` (required). Returns JSON {ok: True, output: '...'}.
    """
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    # admin token check
    token = (request.POST.get('admin_token') or request.META.get('HTTP_X_ADMIN_TOKEN') or '').strip()
    expected = os.environ.get('ADMIN_API_TOKEN') or getattr(settings, 'ADMIN_API_TOKEN', None)
    if not (expected and token and token == expected):
        return JsonResponse({"ok": False, "error": "Forbidden - missing admin token"}, status=403)

    remote = (request.POST.get("remote_url") or "").strip()
    if not remote:
        return JsonResponse({"ok": False, "error": "remote_url required"}, status=400)

    # Execute git commands in project root
    cwd = getattr(settings, "BASE_DIR", os.getcwd())
    outputs = []
    def run(cmd):
        try:
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
            outputs.append({"cmd": " ".join(cmd), "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})
            return proc.returncode == 0
        except Exception as e:
            outputs.append({"cmd": " ".join(cmd), "error": str(e)})
            return False

    # init if needed
    if not os.path.exists(os.path.join(cwd, ".git")):
        run(["git", "init"])
    run(["git", "add", "-A"])
    run(["git", "commit", "-m", "Initial commit"])  # may fail if nothing to commit
    # remove origin if exists
    run(["git", "remote", "remove", "origin"])  # ignore failures
    run(["git", "remote", "add", "origin", remote])
    run(["git", "branch", "-M", "main"])  # may fail on older git
    ok = run(["git", "push", "-u", "origin", "main", "--force"])  # user must ensure credentials

    return JsonResponse({"ok": ok, "outputs": outputs})


@csrf_exempt
def save_settings(request):
    """Save a small set of OMNIROUTE_* settings to a local .env file (development convenience).

    POST fields: OMNIROUTE_BASE_URL, OMNIROUTE_API_KEY, OMNIROUTE_DEFAULT_MODEL
    """
    if request.method != 'POST':
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    # require admin token
    token = (request.POST.get('admin_token') or request.META.get('HTTP_X_ADMIN_TOKEN') or '').strip()
    expected = os.environ.get('ADMIN_API_TOKEN') or getattr(settings, 'ADMIN_API_TOKEN', None)
    if not (expected and token and token == expected):
        return JsonResponse({"ok": False, "error": "Forbidden - missing admin token"}, status=403)
    base = (request.POST.get('OMNIROUTE_BASE_URL') or '').strip()
    key = (request.POST.get('OMNIROUTE_API_KEY') or '').strip()
    model = (request.POST.get('OMNIROUTE_DEFAULT_MODEL') or '').strip()

    env_path = os.path.join(settings.BASE_DIR, '.env')
    lines = []
    if os.path.exists(env_path):
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                lines = f.read().splitlines()
        except Exception:
            lines = []

    def set_key(k, v):
        found = False
        for i,l in enumerate(lines):
            if l.startswith(k + '='):
                lines[i] = f"{k}={v}"
                found = True
                break
        if not found:
            lines.append(f"{k}={v}")

    if base:
        set_key('OMNIROUTE_BASE_URL', base)
    if key:
        set_key('OMNIROUTE_API_KEY', key)
    if model:
        set_key('OMNIROUTE_DEFAULT_MODEL', model)

    try:
        with open(env_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        return JsonResponse({"ok": True, "path": env_path})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


def runs_list(request):
    """Return a JSON list of recent runs from task_runs/ with basic metadata."""
    dirp = os.path.join(settings.BASE_DIR, 'task_runs')
    out = []
    if not os.path.exists(dirp):
        return JsonResponse({"ok": True, "runs": []})
    files = [f for f in os.listdir(dirp) if f.endswith('.log')]
    # sort by mtime desc
    files = sorted(files, key=lambda f: os.path.getmtime(os.path.join(dirp, f)), reverse=True)[:200]
    for fname in files:
        path = os.path.join(dirp, fname)
        entry = {"file": fname, "path": path, "ts": os.path.getmtime(path), "preview": None}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                lines = [l.strip() for l in f.readlines() if l.strip()]
                if lines:
                    # parse first and last JSON if possible
                    try:
                        first = json.loads(lines[0])
                        last = json.loads(lines[-1])
                        entry['first'] = first
                        entry['last'] = last
                        entry['preview'] = (first.get('text') if isinstance(first, dict) else lines[0])
                    except Exception:
                        entry['preview'] = lines[0]
        except Exception:
            pass
        out.append(entry)
    return JsonResponse({"ok": True, "runs": out})
