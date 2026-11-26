# Windows 解压入口：资源管理器自带的解压会把中文文件名弄成乱码，工具随后找不到卡。
# 这个脚本按 UTF-8 解压，解完直接跑自检。
param([string]$Zip = "", [string]$Dest = "")
Add-Type -AssemblyName System.IO.Compression.FileSystem
if (-not $Zip)  { $Zip  = Get-ChildItem -Path $PSScriptRoot -Filter '*.zip' | Sort-Object LastWriteTime | Select-Object -Last 1 | ForEach-Object { $_.FullName } }
if (-not $Zip)  { Write-Host "没找到包：把本脚本和 zip 放在同一个文件夹里"; exit 1 }
if (-not $Dest) { $Dest = Join-Path $PSScriptRoot '解压后' }
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
[System.IO.Compression.ZipFile]::ExtractToDirectory($Zip, $Dest, [System.Text.Encoding]::UTF8)
Write-Host "已按 UTF-8 解压到：$Dest"
$self = Join-Path $Dest '.user_skills\experience-recall\自检.py'
if (Test-Path $self) {
  if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 $self } else { & python $self }
}
