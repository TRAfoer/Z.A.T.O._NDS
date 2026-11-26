param([switch]$Repack)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not (Test-Path -LiteralPath 'nitrofs/audio/index.bin')) {
        throw 'The approved stereo audio package is missing. This build does not apply or convert manual music edits.'
    }
    foreach ($kind in @('gfx', 'text', 'script')) {
        if ($Repack -or $kind -eq 'script' -or -not (Test-Path -LiteralPath "nitrofs/scene/$kind.bin")) {
            & python -X utf8 "tools/pack_scene_$kind.py"
            if ($LASTEXITCODE -ne 0) { throw "Scene $kind packing failed." }
        }
    }
    & 'C:\msys64\opt\wonderful\wonderful_shell.cmd' -defterm -no-start -here -c 'make -j4 RESOURCE_MODE=nitrofs SCENE_DEMO=1'
    if ($LASTEXITCODE -ne 0) { throw 'Scene ROM build failed.' }
    & python tools/rom_budget.py zato_ep2d3_scene_nitrofs.nds
    if ($LASTEXITCODE -ne 0) { throw 'ROM exceeds the strict 64,000,000-byte limit.' }
    Copy-Item -LiteralPath 'zato_ep2d3_scene_nitrofs.nds' -Destination '../ZATO_EP2D3_Scene_Test.nds'
    $artifact = Get-Item -LiteralPath '../ZATO_EP2D3_Scene_Test.nds'
    Write-Output "Built: $($artifact.FullName) ($($artifact.Length) bytes)"
    $digest = (Get-FileHash -LiteralPath $artifact.FullName -Algorithm SHA256).Hash
    Write-Output "SHA256: $digest"
}
finally { Pop-Location }
