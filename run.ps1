# Locate a Python 3.10+ interpreter with pypdf or PyMuPDF, then run the CLI.
$legalToolsCandidates = @(
    (Join-Path $PSScriptRoot 'venv\Scripts\python.exe'),
    (Join-Path $PSScriptRoot 'venv/bin/python'),
    (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'),
    (Join-Path $env:USERPROFILE '.workbuddy\binaries\python\versions\3.13.12\python.exe')
)
$legalToolsPython = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($legalToolsPython) { $legalToolsCandidates += $legalToolsPython }
$legalToolsPython3 = (Get-Command python3 -ErrorAction SilentlyContinue).Source
if ($legalToolsPython3) { $legalToolsCandidates += $legalToolsPython3 }

$legalToolsSelected = $null
foreach ($legalToolsCandidate in $legalToolsCandidates) {
    if (-not $legalToolsCandidate -or -not (Test-Path -LiteralPath $legalToolsCandidate -PathType Leaf)) { continue }
    & $legalToolsCandidate -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
    if ($LASTEXITCODE -ne 0) { continue }
    & $legalToolsCandidate -c 'import pypdf' 2>$null
    if ($LASTEXITCODE -eq 0) { $legalToolsSelected = $legalToolsCandidate; break }
    & $legalToolsCandidate -c 'import pymupdf' 2>$null
    if ($LASTEXITCODE -eq 0) { $legalToolsSelected = $legalToolsCandidate; break }
}
if (-not $legalToolsSelected) {
    throw 'No Python 3.10+ with pypdf or PyMuPDF was found. Run: python -m pip install -r requirements.txt (or create venv/).'
}
$legalToolsArguments = foreach ($legalToolsArgument in $args) {
    if ($legalToolsArgument -is [array]) { $legalToolsArgument -join ',' } else { $legalToolsArgument }
}
& $legalToolsSelected -X utf8 (Join-Path $PSScriptRoot 'scripts\legal_tools_search.py') $legalToolsArguments
exit $LASTEXITCODE
