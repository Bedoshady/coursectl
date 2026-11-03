# One-shot rename: product identity only.
#
# Deliberately NOT replaced:
#   - "eClass" (capital C)  -> York's product name, used descriptively in prose
#   - eclass.yorku.ca       -> the real upstream host, protected by a placeholder
#   - EClassClient/EClassError -> internal identifiers naming the upstream system

$placeholder = '__UPSTREAM_SITE__'
$utf8 = New-Object System.Text.UTF8Encoding($false)

$files = git ls-files | Where-Object { $_ -match '\.(py|md|toml|mjs|example|txt)$' -or $_ -eq '.gitignore' }

$changed = 0
foreach ($f in $files) {
    if (-not (Test-Path $f)) { continue }
    $text = [System.IO.File]::ReadAllText((Resolve-Path $f))
    $original = $text

    $text = $text -replace 'https://eclass\.yorku\.ca', $placeholder
    $text = $text -replace 'eclass_api', 'coursectl'
    $text = $text -replace 'eclass-api', 'coursectl'
    $text = $text -replace 'eclass_error', 'coursectl_error'
    $text = $text -replace 'eclass\.exe', 'coursectl.exe'
    $text = $text -replace 'eclass\.db', 'coursectl.db'
    $text = $text -replace 'eclass-calendar\.ics', 'coursectl-calendar.ics'
    $text = $text -replace 'eclass\.ics', 'coursectl.ics'
    $text = $text -replace 'ECLASS_', 'COURSECTL_'
    $text = $text -replace '\beclass (login|serve|mcp|accounts|revoke|check)\b', 'coursectl $1'
    $text = $text -replace 'ek_', 'ck_'
    $text = $text -replace '(?m)^eclass = ', 'coursectl = '
    $text = $text -replace 'prog="eclass"', 'prog="coursectl"'
    $text = $text -replace $placeholder, 'https://eclass.yorku.ca'

    if ($text -ne $original) {
        [System.IO.File]::WriteAllText((Resolve-Path $f), $text, $utf8)
        $changed++
        Write-Host "  updated $f"
    }
}
Write-Host "`n$changed file(s) changed"
