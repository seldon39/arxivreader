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
    # chaque capture bornée à 90 s ; le feed dépend d'arXiv (lent par moments) : sa capture ne bloque pas
    foreach ($shot in @(@("library", "#/library", $true), @("paper", "#/paper/1603.04246", $true), @("feed", "#/feed", $false))) {
        $file = Join-Path $PWD "shots/$($shot[0]).png"
        $capture = Start-Process $edge -PassThru -ArgumentList "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--window-size=1280,860", "--virtual-time-budget=20000", "--user-data-dir=$env:RUNNER_TEMP\edge-$($shot[0])",
            "--screenshot=$file", "$base/$($shot[1])"
        if (-not $capture.WaitForExit(90000)) {
            Write-Host "$($shot[0]): screenshot timed out"
            Stop-Process -Id $capture.Id -Force -ErrorAction SilentlyContinue
            Get-CimInstance Win32_Process -Filter "name='msedge.exe'" | Where-Object { $_.CommandLine -like "*edge-$($shot[0])*" } |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        }
        $bytes = (Get-Item $file -ErrorAction SilentlyContinue).Length
        Write-Host "$($shot[0]): $bytes bytes"
        if ($shot[2] -and $bytes -lt 10000) { throw "screenshot $($shot[0]) missing or blank" }
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
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
function Save-Screen($name) {
    $screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bitmap = New-Object System.Drawing.Bitmap $screen.Width, $screen.Height
    [System.Drawing.Graphics]::FromImage($bitmap).CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size)
    $bitmap.Save((Join-Path $PWD "shots/$name.png"))
}
$window = Start-Process dist/arXivReader/arXivReader.exe -PassThru
$failure = $null
try {
    # titre de la fenêtre = celui du premier onglet (l'article) une fois la page chargée
    for ($i = 0; $i -lt 90; $i++) {
        $window.Refresh()
        if ($window.HasExited -or $window.MainWindowTitle -like "The sphere packing*") { break }
        Start-Sleep 1
    }
    Start-Sleep 5  # formules, second onglet
    Write-Host "window: '$($window.MainWindowTitle)', exited: $($window.HasExited)"
    Save-Screen "window"
    if ($window.HasExited) { throw "the window closed by itself" }
    if ($window.MainWindowTitle -notlike "The sphere packing*arXivReader") { throw "the first tab did not load" }
    # serveur arrêté de lui-même : la fenêtre reste ouverte et propose de le relancer
    Get-Process arXivReader-server | Stop-Process -Force
    Start-Sleep 4
    Save-Screen "server-stopped"
    $window.Refresh()
    if ($window.HasExited) { throw "the window closed when the server stopped" }
}
catch { $failure = $_ }
finally {
    Stop-Process -Id $window.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep 2
    $left = @(Get-Process arXivReader-server -ErrorAction SilentlyContinue).Count
    Write-Host "server processes left after the window: $left"
    Get-Content "$library/server.log" -ErrorAction SilentlyContinue | Select-Object -Last 20
}
if ($failure) { throw $failure }
if ($left -ne 0) { throw "server still running after the window closed" }

# L'installateur : installation silencieuse (sans droits d'administrateur), raccourci du menu Démarrer, application
# installée qui se reconnaît comme telle, puis désinstallation (dossier retiré, bibliothèque intacte)
$installed = "$env:LOCALAPPDATA\Programs\arXivReader"
Start-Process (Join-Path $PWD "arXivReader-setup.exe") -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait
if (-not (Test-Path "$installed\arXivReader.exe")) { throw "the installer did not install the app" }
if (-not (Test-Path "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\arXivReader.lnk")) { throw "no Start menu shortcut" }
Remove-Item "$library/server.port" -ErrorAction SilentlyContinue
$env:ARXIVREADER_NO_BROWSER = "1"
$server = Start-Process "$installed\server\arXivReader-server.exe" -PassThru
try {
    for ($i = 0; $i -lt 90 -and -not (Test-Path "$library/server.port"); $i++) { Start-Sleep 1 }
    $port = (Get-Content "$library/server.port").Trim()
    $install = (Invoke-RestMethod "http://127.0.0.1:$port/api/updates").install
    Write-Host "installed app: install = $install"
    if ($install -ne "windows-installer") { throw "the installed app does not know it was installed" }
}
finally { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue; Start-Sleep 2 }
Start-Process "$installed\unins000.exe" -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait
for ($i = 0; $i -lt 20 -and (Test-Path "$installed\arXivReader.exe"); $i++) { Start-Sleep 1 }  # désinstalleur relancé à part
if (Test-Path "$installed\arXivReader.exe") { throw "the uninstaller left the app" }
if (-not (Test-Path "$library/arxivreader.db")) { throw "the uninstaller touched the library" }
Write-Host "installer: install, shortcut, uninstall OK; library kept"
