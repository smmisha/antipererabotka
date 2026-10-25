<#
.SYNOPSIS
    Выключает автозапуск антипереработки: удаляет задачу Планировщика заданий.

.DESCRIPTION
    Данные (config.json и база в %LOCALAPPDATA%\antipererabotka) не трогаются.
    Если приложение сейчас работает, его нужно закрыть отдельно (см. README.md, раздел «Если блокировка не снимается»).
#>
[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = 'Stop'
$TaskName = 'antipererabotka'

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Output "Задачи '$TaskName' нет, удалять нечего."
    return
}

if ($PSCmdlet.ShouldProcess($TaskName, 'Удалить задачу автозапуска')) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Задача '$TaskName' удалена. Данные в %LOCALAPPDATA%\antipererabotka не тронуты."
}

