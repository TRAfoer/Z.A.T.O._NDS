param([switch]$Repack, [switch]$MemoryOnly, [switch]$Adaptive, [switch]$RepackAudio)
$ErrorActionPreference = 'Stop'
if ($MemoryOnly -and $Adaptive) { throw 'Choose either -MemoryOnly or -Adaptive.' }
Push-Location -LiteralPath $PSScriptRoot
try {
    if ($Repack -or -not (Test-Path -LiteralPath 'nitrofs/assets.zat')) {
        & python tools/pack_assets.py
        if ($LASTEXITCODE -ne 0) { throw 'Asset packing failed.' }
    }
    if (-not $MemoryOnly -and -not $Adaptive -and ($RepackAudio -or -not (Test-Path -LiteralPath 'nitrofs/audio/index.bin'))) {
        & python tools/pack_audio.py
        if ($LASTEXITCODE -ne 0) { throw 'Audio conversion failed.' }
    }
    if ($MemoryOnly -or $Adaptive) {
        if (-not (Test-Path -LiteralPath 'embedded')) { New-Item -ItemType Directory -Path 'embedded' | Out-Null }
        $embeddedPack = 'embedded/assets.bin'
        if (-not (Test-Path -LiteralPath $embeddedPack) -or
            (Get-FileHash -LiteralPath $embeddedPack).Hash -ne (Get-FileHash -LiteralPath 'nitrofs/assets.zat').Hash) {
            Copy-Item -LiteralPath 'nitrofs/assets.zat' -Destination $embeddedPack
        }
    }
    $buildCommand = if ($Adaptive) { 'make -j4 RESOURCE_MODE=adaptive' } elseif ($MemoryOnly) { 'make -j4 RESOURCE_MODE=memory' } else { 'make -j4 RESOURCE_MODE=nitrofs' }
    & 'C:\msys64\opt\wonderful\wonderful_shell.cmd' -defterm -no-start -here -c $buildCommand
    if ($LASTEXITCODE -ne 0) { throw 'NDS build failed.' }
    $romFile = if ($Adaptive) { 'zato_asset_demo_adaptive.nds' } elseif ($MemoryOnly) { 'zato_asset_demo_r4.nds' } else { 'zato_asset_demo_nitrofs.nds' }
    & python tools/rom_budget.py $romFile
    if ($LASTEXITCODE -ne 0) { throw 'ROM exceeds the 64,000,000-byte delivery limit.' }
}
finally { Pop-Location }
