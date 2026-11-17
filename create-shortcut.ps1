param(
    [string]$Pythonw = 'E:\Downloadproject\Anaconda3\envs\ppt-master\pythonw.exe'
)

$ErrorActionPreference = 'Stop'
$app = Join-Path $PSScriptRoot 'app.pyw'
$icon = Join-Path $PSScriptRoot 'tolist.ico'
foreach ($file in @($Pythonw, $app, $icon)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
        throw "File not found: $file"
    }
}

$desktop = [Environment]::GetFolderPath('DesktopDirectory')
# Construct the Chinese characters so Windows PowerShell can read this file as ASCII.
$name = [string][char]0x4FBF + [char]0x7B7E + ' ToList.lnk'
$path = Join-Path $desktop $name
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($path)
$shortcut.TargetPath = $Pythonw
$shortcut.Arguments = '-B "' + $app + '"'
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.IconLocation = "$icon,0"
$shortcut.Description = 'TodoListMemo'
$shortcut.Save()
Write-Output $path
