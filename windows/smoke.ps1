# Essai de la version Windows construite (GitHub Actions) : le serveur démarre, sert la page et ses modules,
# importe un article ; Edge (sans fenêtre) en fait des captures dans shots/. Une bibliothèque vide, à part.
$ErrorActionPreference = "Stop"
$library = Join-Path $PWD "smoke-library"
New-Item -ItemType Directory -Force $library, shots | Out-Null
$env:ARXIVREADER_LIBRARY = $library
$env:ARXIVREADER_NO_BROWSER = "1"
$process = Start-Process dist/arXivReader/server/arXivReader-server.exe -PassThru

try {
    for ($i = 0; $i -lt 90 -and -not (Test-Path "$library/server.port"); $i++) { Start-Sleep 1 }
    if (-not (Test-Path "$library/server.port")) { throw "server did not start" }
    $port = (Get-Content "$library/server.port").Trim()
    $base = "http://127.0.0.1:$port"
    Write-Host "server on $base"

    $page = Invoke-WebRequest "$base/" -UseBasicParsing
    $module = Invoke-WebRequest "$base/static/js/app.js" -UseBasicParsing
    Write-Host "page $($page.StatusCode), app.js $($module.Headers['Content-Type'])"
    if ($module.Headers['Content-Type'] -notlike "text/javascript*") { throw "wrong type for JavaScript modules" }

    Invoke-RestMethod "$base/api/settings" -Method Put -ContentType "application/json" -Body '{"onboarded": true, "language": "fr"}' | Out-Null
    Write-Host "AI status: $(Invoke-RestMethod "$base/api/ai/status" | ConvertTo-Json -Compress -Depth 2)"

    # un article classique, importé comme depuis la bibliothèque (réseau de GitHub)
    Invoke-RestMethod "$base/api/papers" -Method Post -ContentType "application/json" -Body '{"query": "1603.04246"}' | Out-Null
    for ($i = 0; $i -lt 120; $i++) {
        if (-not (Invoke-RestMethod "$base/api/papers/1603.04246/importing").running) { break }
        Start-Sleep 1
    }
    $paper = Invoke-RestMethod "$base/api/papers/1603.04246"
    Write-Host "imported: $($paper.paper.title) ($($paper.structure.nodes.Count) statements)"
    if (-not $paper.paper.title) { throw "import failed" }

    $edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
    if (-not (Test-Path $edge)) { $edge = "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe" }
    foreach ($shot in @(@("library", "#/library"), @("paper", "#/paper/1603.04246"), @("feed", "#/feed"))) {
        $file = Join-Path $PWD "shots/$($shot[0]).png"
        & $edge --headless=new --disable-gpu --hide-scrollbars --window-size=1280,860 --virtual-time-budget=20000 `
            --user-data-dir="$env:RUNNER_TEMP\edge-shots" --screenshot="$file" "$base/$($shot[1])" 2>&1 | Out-Null
        Write-Host "$($shot[0]): $((Get-Item $file -ErrorAction SilentlyContinue).Length) bytes"
    }
}
finally {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep 1
    Write-Host "----- server.log"
    Get-Content "$library/server.log" -ErrorAction SilentlyContinue | Select-Object -Last 40
}

# La fenêtre de l'application elle-même (WebView2), sur deux onglets : capture de l'écran entier
Remove-Item Env:ARXIVREADER_NO_BROWSER
$env:ARXIVREADER_START = "#/paper/1603.04246,#/feed"
$window = Start-Process dist/arXivReader/arXivReader.exe -PassThru
try {
    Start-Sleep 35
    $window.Refresh()
    Write-Host "window: '$($window.MainWindowTitle)', exited: $($window.HasExited)"
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    $screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bitmap = New-Object System.Drawing.Bitmap $screen.Width, $screen.Height
    [System.Drawing.Graphics]::FromImage($bitmap).CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size)
    $bitmap.Save((Join-Path $PWD "shots/window.png"))
    Write-Host "screen $($screen.Width)x$($screen.Height)"
    if ($window.HasExited) { throw "the window closed by itself" }
}
finally {
    Stop-Process -Id $window.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep 2
    $left = Get-Process arXivReader-server -ErrorAction SilentlyContinue
    Write-Host "server processes left after the window: $(@($left).Count)"  # 0 attendu : arrêté avec la fenêtre
    Get-Content "$library/server.log" -ErrorAction SilentlyContinue | Select-Object -Last 20
}
