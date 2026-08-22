from django.urls import path

from .views import index, runs_page, omni_health, omni_models
from .views import start_task, assistant_message, task_logs
from .views import sse_task_stream
from .views import github_page, github_status, github_repositories, create_github_repo, push_repo
from .views import save_settings

urlpatterns = [
    path('', index, name='home'),
    path('runs/', runs_page, name='runs_page'),
    path('omni/health/', omni_health, name='omni_health'),
    path('omni/models/', omni_models, name='omni_models'),
    path('run_task/', start_task, name='run_task'),
    path('assistant/message/', assistant_message, name='assistant_message'),
    path('run_task_no_csrf/', __import__('agent_app.views', fromlist=['run_task_no_csrf']).run_task_no_csrf, name='run_task_no_csrf'),
    path('exec_code_no_csrf/', __import__('agent_app.views', fromlist=['exec_code_no_csrf']).exec_code_no_csrf, name='exec_code_no_csrf'),
    path('task/logs/<str:run_id>/', task_logs, name='task_logs'),
    path('task/stream/<str:run_id>/', sse_task_stream, name='task_stream'),
    path('queue/list/', __import__('agent_app.views', fromlist=['queue_list']).queue_list, name='queue_list'),
    path('tasks/list/', __import__('agent_app.views', fromlist=['tasks_list']).tasks_list, name='tasks_list'),
    path('tasks/', __import__('agent_app.views', fromlist=['tasks_page']).tasks_page, name='tasks_page'),
    path('omni/logs/', __import__('agent_app.views', fromlist=['omni_logs']).omni_logs, name='omni_logs'),
    path('omni/logs/csv/', __import__('agent_app.views', fromlist=['omni_logs_csv']).omni_logs_csv, name='omni_logs_csv'),
    path('github/', github_page, name='github_page'),
    path('github/status/', github_status, name='github_status'),
    path('github/repos/', github_repositories, name='github_repositories'),
    path('github/create/', __import__('agent_app.views', fromlist=['create_github_repo']).create_github_repo, name='create_github_repo'),
    path('github/push/', __import__('agent_app.views', fromlist=['push_repo']).push_repo, name='push_repo'),
    path('settings/save/', __import__('agent_app.views', fromlist=['save_settings']).save_settings, name='save_settings'),
    path('runs/list/', __import__('agent_app.views', fromlist=['runs_list']).runs_list, name='runs_list'),
]
