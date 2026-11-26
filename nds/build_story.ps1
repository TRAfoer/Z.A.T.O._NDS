param([switch]$Repack, [switch]$NoCopy, [switch]$SkipVideo, [string]$Variant = '', [switch]$StrictBudget, [switch]$UseExistingPacks, [string]$VideoManifest = '')
$ErrorActionPreference = 'Stop'
if ($Variant -and $Variant -notmatch '^[a-z0-9_]+$') { throw 'Variant must contain only lowercase ASCII letters, digits, underscores.' }
if ($Repack -and $UseExistingPacks) { throw 'Select either -Repack or -UseExistingPacks.' }
$romName = if ($Variant) { "zato_story_chain_${Variant}_nitrofs" } else { 'zato_story_chain_nitrofs' }
# Resolve caller-relative overrides before entering the NDS build directory.
$movieManifest = if ($VideoManifest) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($VideoManifest)
} else {
    Join-Path $PSScriptRoot '../experiments/video_lab/out/movie_manifest.json'
}
Push-Location -LiteralPath $PSScriptRoot
try {
    if ($UseExistingPacks) {
        foreach ($path in @('nitrofs/story/assets.zat','nitrofs/story/gfx.bin','nitrofs/story/text.bin','nitrofs/story/script.bin','nitrofs/story/menu.bin')) {
            if (-not (Test-Path -LiteralPath $path)) { throw "Existing compiled pack is missing: $path" }
        }
    }
    elseif ($Repack -or -not (Test-Path -LiteralPath 'nitrofs/story/assets.zat')) {
        & python -X utf8 tools/pack_story_assets.py
        if ($LASTEXITCODE -ne 0) { throw 'Shared-palette story asset compilation failed.' }
    }
    foreach ($kind in @('gfx','text','script','menu')) {
        if (-not $UseExistingPacks -and ($Repack -or $kind -eq 'script' -or $kind -eq 'gfx' -or $kind -eq 'menu' -or -not (Test-Path -LiteralPath "nitrofs/story/$kind.bin"))) {
            if ($kind -eq 'gfx') {
                & python -X utf8 tools/pack_story_gfx.py --assets nitrofs/story/assets.zat --asset-manifest artifacts/story_assets_manifest.json
            }
            elseif ($kind -eq 'text' -and $Repack) {
                & python -X utf8 tools/pack_story_text.py --force
            }
            else { & python -X utf8 "tools/pack_story_$kind.py" }
            if ($LASTEXITCODE -ne 0) { throw "Full-story $kind compilation failed." }
        }
    }
    if (-not $SkipVideo -and -not (Test-Path -LiteralPath $movieManifest)) { throw 'Converted OP manifest is missing. Use -SkipVideo for a mainline-only verification build.' }
    # An explicit filesystem prevents unrelated experiments entering this ROM.
    # Copy only the unchanged approved catalog/assets and this engine's packs.
    foreach ($folder in @('storyfs','storyfs/audio','storyfs/scene','storyfs/story')) {
        if (-not (Test-Path -LiteralPath $folder)) { New-Item -ItemType Directory -Path $folder | Out-Null }
    }
    Copy-Item -LiteralPath 'nitrofs/story/assets.zat' -Destination 'storyfs/assets.zat'
    Copy-Item -LiteralPath 'nitrofs/audio/index.bin' -Destination 'storyfs/audio/index.bin'
    $audioCatalog = Get-Content -LiteralPath 'artifacts/audio_manifest.json' -Raw | ConvertFrom-Json
    foreach ($entry in $audioCatalog.assets) {
        $destination = Join-Path 'storyfs' $entry.file
        $parent = Split-Path -Parent $destination
        if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent | Out-Null }
        Copy-Item -LiteralPath (Join-Path 'nitrofs' $entry.file) -Destination $destination
    }
    Copy-Item -LiteralPath 'nitrofs/scene/gfx.bin' -Destination 'storyfs/scene/gfx.bin'
    foreach ($kind in @('gfx','text','script','menu')) {
        Copy-Item -LiteralPath "nitrofs/story/$kind.bin" -Destination "storyfs/story/$kind.bin"
    }
    if (-not $SkipVideo) {
        $movie = Get-Content -LiteralPath $movieManifest -Raw | ConvertFrom-Json
        foreach ($name in @('movie/op.zpv','movie/op.wav')) {
            $source = $movie.embedded_files.$name
            if (-not $source) { throw "Movie manifest lacks $name" }
            if (-not (Test-Path -LiteralPath 'storyfs/movie')) { New-Item -ItemType Directory -Path 'storyfs/movie' | Out-Null }
            Copy-Item -LiteralPath $source -Destination (Join-Path 'storyfs' $name)
        }
    }
    else {
        # These two files belong only to this build's staging directory.
        foreach ($path in @('storyfs/movie/op.zpv','storyfs/movie/op.wav')) {
            if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
        }
    }
    $movieFlag = if ($SkipVideo) { 0 } else { 1 }
    # Rebuild objects after macro changes and repack NitroFS after removed files.
    $command = "make -B -j4 RESOURCE_MODE=nitrofs STORY_DEMO=1 STORY_OP=$movieFlag NAME=$romName"
    & 'C:\msys64\opt\wonderful\wonderful_shell.cmd' -defterm -no-start -here -c $command
    if ($LASTEXITCODE -ne 0) { throw 'Story ROM compilation failed.' }
    $reportArgs = @('tools/story_build_report.py', "$romName.nds")
    if ($StrictBudget) { $reportArgs += '--strict-budget' }
    & python @reportArgs
    if ($LASTEXITCODE -ne 0) { throw 'Explicit strict ROM budget check failed.' }
    if (-not $NoCopy) {
        Copy-Item -LiteralPath "$romName.nds" -Destination '../ZATO_Story_Chain.nds'
        $file = Get-Item -LiteralPath '../ZATO_Story_Chain.nds'
        Write-Output "Built: $($file.FullName) ($($file.Length) bytes)"
        Write-Output ('SHA256: ' + (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash)
    }
}
finally { Pop-Location }
