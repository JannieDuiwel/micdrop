# Launch MicDrop.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
& "$here\.venv\Scripts\pythonw.exe" -m micdrop
