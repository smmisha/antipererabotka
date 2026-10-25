<#
.SYNOPSIS
    Включает автозапуск антипереработки через Планировщик заданий Windows.

.DESCRIPTION
    Создаёт задачу для текущего пользователя: запуск при входе в систему и затем каждые 5 минут.
    Приложение работает в интерактивном сеансе, без прав администратора. Данные в %LOCALAPPDATA% не трогаются.
    Запускать только после согласия пользователя. Параметр -WhatIf показывает, что будет сделано, и ничего не меняет.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = 'Stop'
$TaskName = 'antipererabotka'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Pythonw = Join-Path $ProjectDir '.venv\Scripts\pythonw.exe'

if (-not (Test-Path $Pythonw)) {
    throw "Не найден $Pythonw. Создай .venv и установи зависимости: см. README.md."
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $Pythonw -Argument '-m app' -WorkingDirectory $ProjectDir
$triggers = @(
    New-ScheduledTaskTrigger -AtLogOn -User $user
    New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

if ($PSCmdlet.ShouldProcess($TaskName, 'Создать задачу автозапуска')) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings -Principal $principal `
        -Description 'Антипереработка: блокировка экрана для воды и сна. Удаляется через uninstall.ps1.' -Force | Out-Null
    Write-Output "Задача '$TaskName' создана. Приложение запустится в течение 5 минут или после следующего входа."
}
else {
    Write-Output "Режим проверки: задача '$TaskName' не создана. Команда: $Pythonw -m app (в папке $ProjectDir)."
}

