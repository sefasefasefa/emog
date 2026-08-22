# PowerShell script to create a Scheduled Task that runs the Django omni_poll management command.
# Usage: run as Administrator. Edit paths below to match your environment.

$python = "$PSScriptRoot\\.venv\\Scripts\\python.exe"
$manage = "$PSScriptRoot\\manage.py"
$action = New-ScheduledTaskAction -Execute $python -Argument "$manage omni_poll --interval 60 --logfile \"$PSScriptRoot\\omni_health.log\""
$trigger = New-ScheduledTaskTrigger -AtStartup -Once -RepetitionInterval (New-TimeSpan -Seconds 60) -RepetitionDuration ([TimeSpan]::MaxValue)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
Register-ScheduledTask -TaskName "EMOG_OmniPoll" -Action $action -Trigger $trigger -Principal $principal -Force
Write-Output "Scheduled task 'EMOG_OmniPoll' registered."
