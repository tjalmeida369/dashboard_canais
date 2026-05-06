$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$app = Join-Path $root "dash_final_a9.py"

python -m streamlit run "$app" --server.port 8502 --server.headless true
