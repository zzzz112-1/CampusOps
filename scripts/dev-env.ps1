# Process-local settings only. Dot-source this script in each worktree.
$CampusOpsRoot = Split-Path -Parent $PSScriptRoot
foreach ($CampusOpsRelativeDir in @('.cache/pip', '.cache/uv', '.cache/python', '.tmp', 'var/data', 'var/logs')) {
    New-Item -ItemType Directory -Path (Join-Path $CampusOpsRoot $CampusOpsRelativeDir) -Force | Out-Null
}
$env:TEMP = Join-Path $CampusOpsRoot '.tmp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $CampusOpsRoot '.cache/pip'
$env:UV_CACHE_DIR = Join-Path $CampusOpsRoot '.cache/uv'
$env:PYTHONPYCACHEPREFIX = Join-Path $CampusOpsRoot '.cache/python'
$env:DATABASE_PATH = Join-Path $CampusOpsRoot 'var/data/campusops.db'
$env:CAMPUSOPS_LLM_ENABLED = 'false'
$env:LANGSMITH_TRACING = 'false'
$env:LANGCHAIN_TRACING_V2 = 'false'
