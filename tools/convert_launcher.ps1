# 把 tools\launcher.src.txt 转码成「启动.bat」
# 规则: GBK(cp936) + CRLF + 无 BOM
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$src  = Join-Path $PSScriptRoot 'launcher.src.txt'
$name = [string][char]0x542F + [string][char]0x52A8 + '.bat'
$dst  = Join-Path $root $name
$gbk  = [System.Text.Encoding]::GetEncoding(936)
$c = [System.IO.File]::ReadAllText($src, [System.Text.Encoding]::UTF8)
$c = ($c -replace "`r`n", "`n") -replace "`n", "`r`n"
$bytes = $gbk.GetBytes($c)
[System.IO.File]::WriteAllBytes($dst, $bytes)
$chk = [System.IO.File]::ReadAllText($dst, $gbk)
$crlf = [regex]::Matches($chk, "`r`n").Count
$lf   = [regex]::Matches($chk, "(?<!`r)`n").Count
"dst=$dst"
"crlf=$crlf lf_only=$lf bom_byte=$($bytes[0]) size=$($bytes.Length)"
if ($lf -ne 0) { throw 'LF-only line endings found' }
if ($bytes[0] -eq 0xEF) { throw 'BOM found' }
if (-not $chk.Contains([string][char]0x5730)) { throw 'chinese garbled' }
'OK'
