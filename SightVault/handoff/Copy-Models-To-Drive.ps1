param(
    [Parameter(Mandatory = $true)][string]$Destination,
    [Parameter(Mandatory = $true)][string]$ProjectRoot
)
$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$destinationRoot = [System.IO.Path]::GetFullPath($Destination)
$manifest = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'MODEL_MANIFEST.json') | ConvertFrom-Json
if ($destinationRoot.TrimEnd('\') -eq $sourceRoot.TrimEnd('\')) {
    throw 'Choose a separate transfer destination, not the live project.'
}
foreach ($item in $manifest.files) {
    $relative = $item.path.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
    $sourcePath = [System.IO.Path]::GetFullPath((Join-Path $sourceRoot $relative))
    $targetPath = [System.IO.Path]::GetFullPath((Join-Path $destinationRoot $relative))
    $sourceBoundary = $sourceRoot.TrimEnd('\') + '\'
    $targetBoundary = $destinationRoot.TrimEnd('\') + '\'
    if (-not $sourcePath.StartsWith($sourceBoundary, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not $targetPath.StartsWith($targetBoundary, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Model manifest path escapes a requested directory.'
    }
    if ((Get-Item -LiteralPath $sourcePath).Length -ne $item.size -or
        (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.sha256) {
        throw "Source model checksum failed: $relative"
    }
    if (Test-Path -LiteralPath $targetPath) {
        if ((Get-Item -LiteralPath $targetPath).Length -ne $item.size -or
            (Get-FileHash -LiteralPath $targetPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.sha256) {
            throw "Different file already exists; choose a fresh destination: $targetPath"
        }
        Write-Host "Already verified: $relative"
        continue
    }
    New-Item -ItemType Directory -Force -Path ([System.IO.Path]::GetDirectoryName($targetPath)) | Out-Null
    Copy-Item -LiteralPath $sourcePath -Destination $targetPath
    if ((Get-FileHash -LiteralPath $targetPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.sha256) {
        throw "Destination model checksum failed: $relative"
    }
    Write-Host "Copied and verified: $relative"
}
Write-Host "Model transfer ready at $destinationRoot\.models"
Write-Host 'Move this .models folder into the extracted SightVault project on the Mac.'
