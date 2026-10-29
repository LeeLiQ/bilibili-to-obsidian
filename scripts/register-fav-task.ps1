# 注册 Windows 计划任务：每 30 分钟拉一次收藏夹，没处理过的自动生成面板。
# 只给个人 Windows 设备用。工作机不登录 B 站个人账号，也不跑采集（主力机是 Mac：scripts/install-launchd.sh）。
# 用法（PowerShell，项目根目录）：  .\scripts\register-fav-task.ps1  [-Minutes 30]
param([int]$Minutes = 30)

$proj = Split-Path -Parent $PSScriptRoot
$py = (Get-Command python -ErrorAction Stop).Source
$logDir = Join-Path $env:LOCALAPPDATA "bili-triage"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "fav.log"

$cmd = "/c set PYTHONIOENCODING=utf-8 && `"$py`" -m bili_triage fav >> `"$log`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmd -WorkingDirectory $proj
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $Minutes)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName "bili-triage-fav" -Action $action -Trigger $trigger -Settings $settings `
    -Description "B 站收藏夹 → Obsidian 证据面板" -Force | Out-Null
Write-Host "已注册 bili-triage-fav，每 $Minutes 分钟一次。日志：$log"
Write-Host "API key 需用 setx 设为用户环境变量，计划任务才读得到。"
