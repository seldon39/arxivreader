// arXivReader pour Mac : une fenêtre (WebKit, le moteur de Safari) sur le serveur local de l'application.
//
// Au lancement, l'application démarre le serveur Python qu'elle embarque (Contents/Resources/python et
// Contents/Resources/arxivreader), sur 127.0.0.1 seulement, avec la bibliothèque dans
// ~/Library/Application Support/arXivReader ; à la fermeture, elle l'arrête. Les liens vers d'autres
// sites s'ouvrent dans le navigateur par défaut.

import AppKit
import WebKit

let french = Locale.preferredLanguages.first?.hasPrefix("fr") ?? true
func tr(_ fr: String, _ en: String) -> String { french ? fr : en }

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var server: Process?
    var port = 8720
    var quitting = false
    var origin: String { "http://127.0.0.1:\(port)" }
    // bibliothèque : ~/Library/Application Support/arXivReader (ARXIVREADER_LIBRARY pour essayer sur une copie)
    let support = ProcessInfo.processInfo.environment["ARXIVREADER_LIBRARY"].map { URL(fileURLWithPath: $0, isDirectory: true) }
        ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("arXivReader", isDirectory: true)

    // ---------- Démarrage et arrêt ----------

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        try? FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
        makeWindow()
        offerImport()
        port = freePort(preferred: 8720)
        startServer()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        quitting = true
        guard let server, server.isRunning else { return .terminateNow }
        server.terminate() // SIGTERM : le serveur termine proprement (vérification en cours, base)
        DispatchQueue.global().async {
            let deadline = Date().addingTimeInterval(5)
            while server.isRunning && Date() < deadline { usleep(50_000) }
            if server.isRunning { kill(server.processIdentifier, SIGKILL) }
            DispatchQueue.main.async { NSApp.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }

    func startServer() {
        guard let resources = Bundle.main.resourceURL else { return }
        let process = Process()
        process.executableURL = resources.appendingPathComponent("python/bin/python3")
        process.arguments = ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port),
                             "--log-level", "warning"]
        process.currentDirectoryURL = resources.appendingPathComponent("arxivreader")
        var env = ProcessInfo.processInfo.environment
        for key in ["PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "VIRTUAL_ENV"] { env.removeValue(forKey: key) }
        env["ARXIVREADER_PORT"] = String(port)
        env["ARXIVREADER_LIBRARY"] = support.path
        env["ARXIVREADER_WATCH_PARENT"] = "1"  // s'arrête seul si l'application disparaît sans l'arrêter
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        // certificats HTTPS fournis avec Python (son chemin d'origine n'existe plus une fois l'application déplacée)
        let certificates = resources.appendingPathComponent("python/ssl/cacert.pem")
        if FileManager.default.fileExists(atPath: certificates.path) { env["SSL_CERT_FILE"] = certificates.path }
        process.environment = env
        let log = support.appendingPathComponent("server.log")
        FileManager.default.createFile(atPath: log.path, contents: nil)
        if let handle = try? FileHandle(forWritingTo: log) {
            process.standardOutput = handle
            process.standardError = handle
        }
        process.terminationHandler = { [weak self] ended in
            DispatchQueue.main.async { self?.serverEnded(ended.terminationStatus) }
        }
        do {
            try process.run()
            server = process
            waitForServer(since: Date())
        } catch {
            fail(tr("Le serveur d'arXivReader n'a pas pu démarrer.", "The arXivReader server could not start."), detail: "\(error)")
        }
    }

    // Le serveur répond : la page s'affiche (premier lancement : quelques secondes)
    func waitForServer(since start: Date) {
        guard let url = URL(string: "\(origin)/api/settings") else { return }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 2)
        request.httpMethod = "GET"
        URLSession.shared.dataTask(with: request) { [weak self] _, response, _ in
            DispatchQueue.main.async {
                guard let self, !self.quitting, self.server?.isRunning == true else { return }
                if (response as? HTTPURLResponse)?.statusCode == 200 {
                    // Essais : ARXIVREADER_START=#/paper/… ouvre directement cette page
                    let route = ProcessInfo.processInfo.environment["ARXIVREADER_START"] ?? ""
                    self.webView.load(URLRequest(url: URL(string: "\(self.origin)/\(route)")!))
                } else if Date().timeIntervalSince(start) > 90 {
                    self.fail(tr("Le serveur d'arXivReader ne répond pas.", "The arXivReader server does not answer."), detail: self.logTail())
                } else {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { self.waitForServer(since: start) }
                }
            }
        }.resume()
    }

    func serverEnded(_ status: Int32) {
        if quitting { return }
        let alert = NSAlert()
        alert.messageText = tr("Le serveur d'arXivReader s'est arrêté.", "The arXivReader server stopped.")
        alert.informativeText = logTail()
        alert.addButton(withTitle: tr("Relancer", "Restart"))
        alert.addButton(withTitle: tr("Quitter", "Quit"))
        if alert.runModal() == .alertFirstButtonReturn {
            port = freePort(preferred: port)
            startServer()
        } else {
            NSApp.terminate(nil)
        }
    }

    func fail(_ message: String, detail: String) {
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = message
        alert.informativeText = detail
        alert.addButton(withTitle: tr("Quitter", "Quit"))
        alert.runModal()
        quitting = true
        server?.terminate()
        NSApp.terminate(nil)
    }

    func logTail() -> String {
        let text = (try? String(contentsOf: support.appendingPathComponent("server.log"), encoding: .utf8)) ?? ""
        let lines = text.split(separator: "\n").suffix(12).joined(separator: "\n")
        return lines.isEmpty ? support.appendingPathComponent("server.log").path : lines
    }

    // Port 8720 si libre (même adresse d'un lancement à l'autre), sinon un port libre quelconque
    func freePort(preferred: Int) -> Int {
        func bind(_ port: Int) -> Int? {
            let fd = socket(AF_INET, SOCK_STREAM, 0)
            guard fd >= 0 else { return nil }
            defer { close(fd) }
            var address = sockaddr_in()
            address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
            address.sin_family = sa_family_t(AF_INET)
            address.sin_port = in_port_t(UInt16(port).bigEndian)
            address.sin_addr.s_addr = inet_addr("127.0.0.1")
            let bound = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
            }
            guard bound == 0 else { return nil }
            var length = socklen_t(MemoryLayout<sockaddr_in>.size)
            _ = withUnsafeMutablePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &length) }
            }
            return Int(UInt16(bigEndian: address.sin_port))
        }
        return bind(preferred) ?? bind(0) ?? preferred
    }

    // ---------- Première ouverture : reprendre une bibliothèque existante ----------

    func offerImport() {
        let database = support.appendingPathComponent("arxivreader.db")
        guard !FileManager.default.fileExists(atPath: database.path) else { return }
        let alert = NSAlert()
        alert.messageText = tr("Bienvenue dans arXivReader", "Welcome to arXivReader")
        alert.informativeText = tr(
            "Vous avez déjà une bibliothèque arXivReader (dossier « library » de la version en ligne de commande) ? Elle peut être recopiée ici. Quittez d'abord l'ancienne version si elle est lancée.",
            "Do you already have an arXivReader library (the “library” folder of the command-line version)? It can be copied here. Quit the old version first if it is running.")
        alert.addButton(withTitle: tr("Choisir le dossier…", "Choose the folder…"))
        alert.addButton(withTitle: tr("Commencer avec une bibliothèque vide", "Start with an empty library"))
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.message = tr("Dossier « library » qui contient arxivreader.db", "“library” folder containing arxivreader.db")
        panel.prompt = tr("Recopier", "Copy")
        guard panel.runModal() == .OK, let source = panel.url else { return }
        let fm = FileManager.default
        guard fm.fileExists(atPath: source.appendingPathComponent("arxivreader.db").path) else {
            let missing = NSAlert()
            missing.messageText = tr("Ce dossier ne contient pas de bibliothèque arXivReader (arxivreader.db).",
                                     "This folder holds no arXivReader library (arxivreader.db).")
            missing.runModal()
            return offerImport()
        }
        do {
            for item in try fm.contentsOfDirectory(at: source, includingPropertiesForKeys: nil)
            where !["ai", "server.log", ".DS_Store"].contains(item.lastPathComponent) {
                let target = support.appendingPathComponent(item.lastPathComponent)
                if fm.fileExists(atPath: target.path) { try fm.removeItem(at: target) }
                try fm.copyItem(at: item, to: target)
            }
        } catch {
            let failed = NSAlert()
            failed.messageText = tr("La copie a échoué.", "Copying failed.")
            failed.informativeText = "\(error)"
            failed.runModal()
        }
    }

    // ---------- Fenêtre ----------

    func makeWindow() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        if #available(macOS 13.3, *) { webView.isInspectable = true }
        webView.loadHTMLString(startPage(), baseURL: nil)

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "arXivReader"
        window.minSize = NSSize(width: 420, height: 480)
        window.tabbingMode = .disallowed
        window.contentView = webView
        window.delegate = self
        window.center()
        window.setFrameAutosaveName("arXivReader.main")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func startPage() -> String {
        """
        <!doctype html><meta charset="utf-8"><style>
        :root { color-scheme: light dark; } body { margin: 0; height: 100vh; display: grid; place-items: center;
        font: 15px -apple-system, sans-serif; color: #6b6b6b; background: Canvas; }
        b { display: block; font: 600 28px Georgia, serif; color: #b31b1b; margin-bottom: 8px; text-align: center; }
        </style><div><b>arXivReader</b>\(tr("Démarrage…", "Starting…"))</div>
        """
    }

    // Liens : ceux de l'application restent dans la fenêtre, les autres (arXiv, DOI, claude://…) s'ouvrent à côté
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { return decisionHandler(.allow) }
        if url.absoluteString.hasPrefix(origin) || url.scheme == "about" || url.scheme == "data" || url.scheme == "blob" {
            decisionHandler(action.shouldPerformDownload ? .download : .allow)
        } else {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        decisionHandler(response.canShowMIMEType ? .allow : .download)
    }

    // target="_blank", window.open : dans le navigateur
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { webView.reload() }

    // Essais : ARXIVREADER_SNAPSHOT=fichier.png, image de la fenêtre quelques secondes après le chargement
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard let path = ProcessInfo.processInfo.environment["ARXIVREADER_SNAPSHOT"], webView.url?.absoluteString.hasPrefix(origin) == true
        else { return }
        let delay = Double(ProcessInfo.processInfo.environment["ARXIVREADER_SNAPSHOT_DELAY"] ?? "") ?? 4
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
            webView.takeSnapshot(with: nil) { image, _ in
                guard let tiff = image?.tiffRepresentation, let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:])
                else { return }
                try? png.write(to: URL(fileURLWithPath: path))
            }
        }
    }

    // Téléchargements : dans le dossier Téléchargements
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        let folder = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
        var target = folder.appendingPathComponent(suggestedFilename)
        var n = 1
        while FileManager.default.fileExists(atPath: target.path) {
            let base = (suggestedFilename as NSString).deletingPathExtension, ext = (suggestedFilename as NSString).pathExtension
            target = folder.appendingPathComponent(ext.isEmpty ? "\(base) \(n)" : "\(base) \(n).\(ext)")
            n += 1
        }
        completionHandler(target)
    }

    // alert(), confirm(), prompt() et <input type="file"> : fenêtres du Mac
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: tr("Annuler", "Cancel"))
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) }
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let alert = NSAlert()
        alert.messageText = prompt
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 24))
        field.stringValue = defaultText ?? ""
        alert.accessoryView = field
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: tr("Annuler", "Cancel"))
        alert.window.initialFirstResponder = field
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn ? field.stringValue : nil) }
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.beginSheetModal(for: window) { completionHandler($0 == .OK ? panel.urls : nil) }
    }

    // ---------- Menus ----------

    @objc func openSettings() {
        webView.evaluateJavaScript("document.getElementById('settings-button')?.click()")
    }
    @objc func reloadPage() { webView.reload() }
    // Rechercher : la page s'en charge (barre de recherche du lecteur, champ de la bibliothèque)
    func find(_ command: String) {
        webView.evaluateJavaScript("document.dispatchEvent(new CustomEvent('app:find', { detail: '\(command)' }))")
    }
    @objc func findOpen() { find("open") }
    @objc func findNext() { find("next") }
    @objc func findPrevious() { find("previous") }
    @objc func goBack() { webView.goBack() }
    @objc func goForward() { webView.goForward() }
    @objc func showLibraryFolder() { NSWorkspace.shared.activateFileViewerSelecting([support]) }

    func buildMenu() {
        let main = NSMenu()
        func submenu(_ title: String, _ items: [NSMenuItem]) {
            let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            let menu = NSMenu(title: title)
            items.forEach(menu.addItem)
            item.submenu = menu
            main.addItem(item)
        }
        func entry(_ title: String, _ action: Selector?, _ key: String = "", _ modifiers: NSEvent.ModifierFlags = .command,
                   target: AnyObject? = nil) -> NSMenuItem {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
            item.keyEquivalentModifierMask = modifiers
            item.target = target
            return item
        }
        submenu("arXivReader", [
            entry(tr("À propos d'arXivReader", "About arXivReader"), #selector(NSApplication.orderFrontStandardAboutPanel(_:))),
            .separator(),
            entry(tr("Réglages…", "Settings…"), #selector(openSettings), ",", target: self),
            entry(tr("Afficher la bibliothèque dans le Finder", "Show the library in Finder"), #selector(showLibraryFolder), target: self),
            .separator(),
            entry(tr("Masquer arXivReader", "Hide arXivReader"), #selector(NSApplication.hide(_:)), "h"),
            entry(tr("Masquer les autres", "Hide Others"), #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]),
            entry(tr("Tout afficher", "Show All"), #selector(NSApplication.unhideAllApplications(_:))),
            .separator(),
            entry(tr("Quitter arXivReader", "Quit arXivReader"), #selector(NSApplication.terminate(_:)), "q"),
        ])
        submenu(tr("Édition", "Edit"), [
            entry(tr("Annuler", "Undo"), Selector(("undo:")), "z"),
            entry(tr("Rétablir", "Redo"), Selector(("redo:")), "z", [.command, .shift]),
            .separator(),
            entry(tr("Couper", "Cut"), #selector(NSText.cut(_:)), "x"),
            entry(tr("Copier", "Copy"), #selector(NSText.copy(_:)), "c"),
            entry(tr("Coller", "Paste"), #selector(NSText.paste(_:)), "v"),
            entry(tr("Tout sélectionner", "Select All"), #selector(NSText.selectAll(_:)), "a"),
            .separator(),
            entry(tr("Rechercher…", "Find…"), #selector(findOpen), "f", target: self),
            entry(tr("Rechercher le suivant", "Find Next"), #selector(findNext), "g", target: self),
            entry(tr("Rechercher le précédent", "Find Previous"), #selector(findPrevious), "g", [.command, .shift], target: self),
        ])
        submenu(tr("Présentation", "View"), [
            entry(tr("Recharger", "Reload"), #selector(reloadPage), "r", target: self),
            .separator(),
            entry(tr("Plein écran", "Enter Full Screen"), #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control]),
        ])
        submenu(tr("Aller", "Go"), [
            entry(tr("Précédent", "Back"), #selector(goBack), "[", target: self),
            entry(tr("Suivant", "Forward"), #selector(goForward), "]", target: self),
        ])
        let windowMenu: [NSMenuItem] = [
            entry(tr("Placer dans le Dock", "Minimize"), #selector(NSWindow.performMiniaturize(_:)), "m"),
            entry(tr("Réduire/agrandir", "Zoom"), #selector(NSWindow.performZoom(_:))),
            entry(tr("Fermer", "Close"), #selector(NSWindow.performClose(_:)), "w"),
        ]
        submenu(tr("Fenêtre", "Window"), windowMenu)
        NSApp.mainMenu = main
        NSApp.windowsMenu = main.items.last?.submenu
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
