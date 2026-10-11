// arXivReader pour Mac : des fenêtres à onglets (WebKit, le moteur de Safari) sur le serveur local de l'application.
//
// Au lancement, l'application démarre le serveur Python qu'elle embarque (Contents/Resources/python et
// Contents/Resources/arxivreader), sur 127.0.0.1 seulement, avec la bibliothèque dans
// ~/Library/Application Support/arXivReader ; à la fermeture, elle l'arrête. Les liens vers d'autres
// sites s'ouvrent dans le navigateur par défaut.

import AppKit
import WebKit

let french = Locale.preferredLanguages.first?.hasPrefix("fr") ?? true
func tr(_ fr: String, _ en: String) -> String { french ? fr : en }

final class AppDelegate: NSObject, NSApplicationDelegate {
    var tabs: [Tab] = []
    var serverReady = false
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
        // premier onglet (essais : ARXIVREADER_START=#/paper/…,#/feed ouvre ces pages, les suivantes en onglets)
        let routes = (ProcessInfo.processInfo.environment["ARXIVREADER_START"] ?? "").split(separator: ",").map(String.init)
        let first = openTab(routes.first ?? "", beside: nil)
        for route in routes.dropFirst() { openTab(route, beside: first.window, front: false) }
        NSApp.activate(ignoringOtherApps: true)
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
            let deadline = Date().addingTimeInterval(9) // le serveur se laisse 3 s, plus l'arrêt de ses tâches
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
                             "--log-level", "warning", "--timeout-graceful-shutdown", "3"]
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
        // journal précédent gardé (server.log.1) : après « Relancer », la cause de l'arrêt reste lisible
        let previous = support.appendingPathComponent("server.log.1")
        try? FileManager.default.removeItem(at: previous)
        try? FileManager.default.moveItem(at: log, to: previous)
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
                    self.serverReady = true
                    self.tabs.forEach { $0.reconnect() }
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
            serverReady = false
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
        quitting = true // avant la fenêtre : un arrêt du serveur pendant qu'elle est affichée n'en ouvre pas une autre
        alert.runModal()
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

    // ---------- Onglets et fenêtres ----------

    // onglet de la fenêtre active (destinataire des menus : réglages, rechercher, recharger…)
    var keyTab: Tab? { tabs.first { $0.window.isKeyWindow } ?? tabs.first { $0.window.isMainWindow } ?? tabs.last }

    // Nouvel onglet à côté de `anchor` (sinon une nouvelle fenêtre) ; `front` : affiché aussitôt
    @discardableResult
    func openTab(_ route: String, beside anchor: NSWindow?, front: Bool = true) -> Tab {
        let first = tabs.isEmpty
        let tab = Tab(app: self)
        tabs.append(tab)
        if let anchor {
            tab.window.setFrame(anchor.frame, display: false)
            anchor.addTabbedWindow(tab.window, ordered: .above)
            if front { tab.window.makeKeyAndOrderFront(nil) } else { anchor.makeKeyAndOrderFront(nil) }
        } else {
            if first {
                tab.window.center()
                tab.window.setFrameAutosaveName("arXivReader.main")
            } else if let frame = tabs.dropLast().last?.window.frame {
                tab.window.setFrame(frame.offsetBy(dx: 28, dy: -28), display: false) // en cascade
            }
            // nouvelle fenêtre (⌘N) : pas rangée d'office en onglet (réglage « Préférer les onglets : toujours »)
            tab.window.tabbingMode = .disallowed
            tab.window.makeKeyAndOrderFront(nil)
            tab.window.tabbingMode = .automatic
        }
        showTabBar(tab.window)
        tab.load(route)
        return tab
    }

    // Barre d'onglets toujours affichée, même avec un seul onglet (son « + » ouvre un nouvel onglet)
    func showTabBar(_ window: NSWindow) {
        DispatchQueue.main.async {
            guard window.isVisible, let group = window.tabGroup, !group.isTabBarVisible else { return }
            window.toggleTabBar(nil)
        }
    }

    func closed(_ tab: Tab) { tabs.removeAll { $0 === tab } }

    func startPage() -> String {
        """
        <!doctype html><meta charset="utf-8"><style>
        :root { color-scheme: light dark; } body { margin: 0; height: 100vh; display: grid; place-items: center;
        font: 15px -apple-system, sans-serif; color: #6b6b6b; background: Canvas; }
        b { display: block; font: 600 28px Georgia, serif; color: #b31b1b; margin-bottom: 8px; text-align: center; }
        </style><div><b>arXivReader</b>\(tr("Démarrage…", "Starting…"))</div>
        """
    }

    // ---------- Menus ----------

    @objc func openSettings() {
        keyTab?.webView.evaluateJavaScript("document.getElementById('settings-button')?.click()")
    }
    @objc func reloadPage() { keyTab?.webView.reload() }
    @objc func newTab() { openTab("#/", beside: keyTab?.window) }
    @objc func newWindow() { openTab("#/", beside: nil) }
    // tous les onglets de la fenêtre
    @objc func closeWindow() { (keyTab?.window.tabGroup?.windows ?? keyTab.map { [$0.window] } ?? []).forEach { $0.close() } }
    // Rechercher : la page s'en charge (barre de recherche du lecteur, champ de la bibliothèque)
    func find(_ command: String) {
        keyTab?.webView.evaluateJavaScript("document.dispatchEvent(new CustomEvent('app:find', { detail: '\(command)' }))")
    }
    @objc func findOpen() { find("open") }
    @objc func findNext() { find("next") }
    @objc func findPrevious() { find("previous") }
    @objc func goBack() { keyTab?.webView.goBack() }
    @objc func goForward() { keyTab?.webView.goForward() }
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
        submenu(tr("Fichier", "File"), [
            entry(tr("Nouvel onglet", "New Tab"), #selector(newTab), "t", target: self),
            entry(tr("Nouvelle fenêtre", "New Window"), #selector(newWindow), "n", target: self),
            .separator(),
            entry(tr("Fermer l'onglet", "Close Tab"), #selector(NSWindow.performClose(_:)), "w"),
            entry(tr("Fermer la fenêtre", "Close Window"), #selector(closeWindow), "w", [.command, .shift], target: self),
            .separator(),
            entry(tr("Afficher tous les onglets", "Show All Tabs"), #selector(NSWindow.toggleTabOverview(_:)), "|"),
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
            .separator(),
            entry(tr("Afficher l'onglet précédent", "Show Previous Tab"), #selector(NSWindow.selectPreviousTab(_:)), "{"),
            entry(tr("Afficher l'onglet suivant", "Show Next Tab"), #selector(NSWindow.selectNextTab(_:)), "}"),
            entry(tr("Placer l'onglet dans une nouvelle fenêtre", "Move Tab to New Window"), #selector(NSWindow.moveTabToNewWindow(_:))),
            entry(tr("Fusionner toutes les fenêtres", "Merge All Windows"), #selector(NSWindow.mergeAllWindows(_:))),
        ]
        submenu(tr("Fenêtre", "Window"), windowMenu)
        NSApp.mainMenu = main
        NSApp.windowsMenu = main.items.last?.submenu
    }
}

// ---------- Onglets : une fenêtre (WebKit) par onglet, regroupées par la barre d'onglets du Mac ----------

/// Fenêtre d'un onglet ; le « + » de la barre d'onglets ouvre la bibliothèque dans un nouvel onglet. Un article
/// glissé (de la page, d'une autre fenêtre) qui s'attarde sur un onglet de la barre ouvre cet onglet : on le dépose
/// ensuite dans sa page (sur un dossier…), comme d'une fenêtre à l'autre.
final class TabWindow: NSWindow {
    override func newWindowForTab(_ sender: Any?) {
        (NSApp.delegate as? AppDelegate)?.openTab("#/", beside: self)
    }

    /// ce que WebKit met dans le presse-papiers d'un glissement (lien, texte, types propres à la page)
    static let dragTypes: [NSPasteboard.PasteboardType] = [.URL, .string, .html, .fileURL,
        NSPasteboard.PasteboardType("com.apple.WebKit.custom-pasteboard-data"), NSPasteboard.PasteboardType("public.url")]
    private var springTarget: NSWindow?
    private var springTimer: Timer?

    /// onglets de la barre du Mac (vues NSTabButton, une par onglet), de gauche à droite, en coordonnées de la fenêtre
    private func tabButtons() -> [NSRect] {
        var found: [NSRect] = []
        func visit(_ view: NSView) {
            if String(describing: type(of: view)) == "NSTabButton" {
                let box = view.convert(view.bounds, to: nil)
                if box.width > 0 { found.append(box) }
                return
            }
            view.subviews.forEach(visit)
        }
        if let frameView = contentView?.superview { visit(frameView) }
        return found.sorted { $0.minX < $1.minX }
    }

    /// onglet sous le point (coordonnées de la fenêtre), s'il est dans la barre d'onglets
    private func tab(at point: NSPoint) -> NSWindow? {
        guard let group = tabGroup, group.isTabBarVisible, point.y > contentLayoutRect.maxY else { return nil }
        let windows = group.windows
        let buttons = tabButtons()
        if buttons.count == windows.count {
            return buttons.firstIndex { $0.contains(point) }.map { windows[$0] }
        }
        let width = (frame.width - 28) / CGFloat(max(1, windows.count)) // sinon : largeur partagée, moins le « + »
        let index = Int(point.x / width)
        return index >= 0 && index < windows.count ? windows[index] : nil
    }

    private func spring(_ sender: NSDraggingInfo) -> NSDragOperation {
        let target = tab(at: sender.draggingLocation)
        if target !== springTarget {
            springTimer?.invalidate()
            springTarget = target
            if let target, target !== tabGroup?.selectedWindow {
                // minuterie active pendant le glissement (boucle d'événements en mode « suivi »)
                let timer = Timer(timeInterval: 0.6, repeats: false) { [weak self] _ in
                    guard let self, self.springTarget === target, let group = target.tabGroup else { return }
                    group.selectedWindow = target
                    self.springTarget = nil
                }
                RunLoop.main.add(timer, forMode: .common)
                springTimer = timer
            }
        }
        return target == nil ? [] : .generic
    }

    private func stopSpring() {
        springTimer?.invalidate()
        springTimer = nil
        springTarget = nil
    }

    // messages du glissement (protocole NSDraggingDestination, appelés par AppKit : @objc)
    @objc func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation { spring(sender) }
    @objc func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation { spring(sender) }
    @objc func draggingExited(_ sender: NSDraggingInfo?) { stopSpring() }
    @objc func draggingEnded(_ sender: NSDraggingInfo) { stopSpring() }
    @objc func performDragOperation(_ sender: NSDraggingInfo) -> Bool { stopSpring(); return false } // lâché sur la barre : rien
}

/// Messages de la page (⌘-clic sur un article : nouvel onglet). Intermédiaire faible : WebKit retient son destinataire
final class MessageProxy: NSObject, WKScriptMessageHandler {
    weak var tab: Tab?
    init(_ tab: Tab) { self.tab = tab }
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) { tab?.received(message) }
}

final class Tab: NSObject, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    unowned let app: AppDelegate
    let window: TabWindow
    let webView: WKWebView
    var pending: String?            // page à ouvrir dès que le serveur répond
    var tested = false
    var titleObserver: NSKeyValueObservation?
    var downloads: [ObjectIdentifier: URL] = [:]  // téléchargement en cours → fichier où il s'écrit

    init(app: AppDelegate) {
        self.app = app
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        webView = WKWebView(frame: .zero, configuration: config)
        window = TabWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 860),
                           styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        super.init()
        config.userContentController.add(MessageProxy(self), name: "arxivreader")
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        if #available(macOS 13.3, *) { webView.isInspectable = true }
        window.isReleasedWhenClosed = false
        window.title = "arXivReader"
        window.minSize = NSSize(width: 420, height: 480)
        window.tabbingIdentifier = "arXivReader"
        window.tabbingMode = .automatic
        window.registerForDraggedTypes(TabWindow.dragTypes) // article glissé sur un onglet : l'ouvre (TabWindow)
        window.contentView = webView
        window.delegate = self
        // titre de l'onglet et de la fenêtre : celui de la page (article lu, « Bibliothèque », « Feed »)
        titleObserver = webView.observe(\.title) { [weak self] web, _ in
            let title = web.title ?? ""
            self?.window.title = title.isEmpty ? "arXivReader" : title
        }
    }

    // Page de l'application (« #/paper/… ») ; avant que le serveur réponde : page d'attente
    func load(_ route: String) {
        if app.serverReady {
            pending = nil
            guard let url = URL(string: "\(app.origin)/\(route)") ?? URL(string: "\(app.origin)/") else { return }
            webView.load(URLRequest(url: url))
        } else {
            pending = route
            webView.loadHTMLString(app.startPage(), baseURL: nil)
        }
    }

    // Serveur prêt (ou relancé, peut-être sur un autre port) : page en attente, sinon la même page
    func reconnect() {
        let route = pending ?? webView.url.flatMap { $0.fragment.map { "#\($0)" } } ?? ""
        load(route)
    }

    func received(_ message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], body["action"] as? String == "openTab",
              let route = body["route"] as? String,
              route.range(of: "^#/[A-Za-z0-9/._%~-]*$", options: .regularExpression) != nil else { return }
        // comme Safari : nouvel onglet en arrière-plan (⇧ en plus : affiché aussitôt)
        app.openTab(route, beside: window, front: body["front"] as? Bool ?? false)
    }

    func windowDidBecomeKey(_ notification: Notification) { app.showTabBar(window) }

    func windowWillClose(_ notification: Notification) {
        titleObserver = nil
        webView.configuration.userContentController.removeScriptMessageHandler(forName: "arxivreader")
        app.closed(self)
    }

    func windowShot(_ path: String) {
        let lines = [
            "tabs=\(window.tabGroup?.windows.count ?? 0)",
            "tabBarVisible=\(window.tabGroup?.isTabBarVisible ?? false)",
            "titles=\(window.tabGroup?.windows.map(\.title) ?? [])",
            "menus=\(NSApp.mainMenu?.items.map { "\($0.title): " + ($0.submenu?.items.map { $0.isSeparatorItem ? "—" : $0.title } ?? []).joined(separator: " | ") } ?? [])",
        ]
        try? lines.joined(separator: "\n").write(toFile: path + ".txt", atomically: true, encoding: .utf8)
        guard let frame = window.contentView?.superview, let rep = frame.bitmapImageRepForCachingDisplay(in: frame.bounds) else { return }
        frame.cacheDisplay(in: frame.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
    }

    // Page de l'application : http://127.0.0.1:<port du serveur>, comparé en entier (pas un simple préfixe :
    // « http://127.0.0.1:8720@autre.site » ou un port plus long ne passent pas)
    func isAppURL(_ url: URL) -> Bool {
        url.scheme == "http" && url.host == "127.0.0.1" && url.port == app.port && url.user == nil && url.password == nil
    }

    // Lien vers l'extérieur ouvert dans le navigateur (ou l'application concernée) : seulement un clic de
    // l'utilisateur vers http(s) ou mailto, et claude:// (bouton « Continuer dans Claude ») ; le reste
    // (smb://, ssh://, schémas d'autres applications, redirections, cadres) est ignoré
    func openOutside(_ url: URL, _ action: WKNavigationAction) {
        let scheme = url.scheme?.lowercased() ?? ""
        let clicked = action.navigationType == .linkActivated
        guard (["http", "https", "mailto"].contains(scheme) && clicked) || scheme == "claude" else { return }
        NSWorkspace.shared.open(url)
    }

    // Liens : ceux de l'application restent dans la fenêtre, les autres (arXiv, DOI, claude://…) s'ouvrent à côté
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { return decisionHandler(.cancel) }
        let mainFrame = action.targetFrame?.isMainFrame ?? true
        let scheme = url.scheme?.lowercased() ?? ""
        if isAppURL(url) || scheme == "about" {
            decisionHandler(action.shouldPerformDownload ? .download : .allow)
        } else if scheme == "data" || scheme == "blob" {
            // contenus construits par la page : dans un cadre (PDF, images) ou en téléchargement, jamais comme page
            decisionHandler(action.shouldPerformDownload ? .download : mainFrame ? .cancel : .allow)
        } else {
            if mainFrame { openOutside(url, action) }
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        decisionHandler(response.canShowMIMEType ? .allow : .download)
    }

    // target="_blank", window.open : page de l'application dans un nouvel onglet, le reste dans le navigateur
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard let url = action.request.url else { return nil }
        if isAppURL(url) { app.openTab(url.fragment.map { "#\($0)" } ?? "#/", beside: window) } else { openOutside(url, action) }
        return nil
    }

    // Page plantée (manque de mémoire…) : rechargée, mais pas en boucle si elle replante aussitôt
    var crashes: [Date] = []
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        crashes = crashes.filter { $0.timeIntervalSinceNow > -60 } + [Date()]
        if crashes.count <= 3 { webView.reload() }
    }

    // Essais (premier onglet) : ARXIVREADER_SNAPSHOT=fichier.png, image de la page quelques secondes après le
    // chargement ; ARXIVREADER_WINDOWSHOT=fichier.png, la fenêtre entière (barre d'onglets) et un relevé
    // (onglets, barre, menus) dans fichier.png.txt ; ARXIVREADER_EVAL : JavaScript exécuté une fois la page chargée
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        let env = ProcessInfo.processInfo.environment
        guard app.tabs.first === self, webView.url?.absoluteString.hasPrefix(app.origin) == true, !tested else { return }
        tested = true
        if let script = env["ARXIVREADER_EVAL"] {
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { webView.evaluateJavaScript(script) }
        }
        let delay = Double(env["ARXIVREADER_SNAPSHOT_DELAY"] ?? "") ?? 4
        if let path = env["ARXIVREADER_WINDOWSHOT"] {
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in self?.windowShot(path) }
        }
        guard let path = env["ARXIVREADER_SNAPSHOT"] else { return }
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
        downloads[ObjectIdentifier(download)] = target
        completionHandler(target)
    }
    // enregistré : montré dans le Finder (export des notes…) ; échec : signalé
    func downloadDidFinish(_ download: WKDownload) {
        if let target = downloads.removeValue(forKey: ObjectIdentifier(download)) {
            NSWorkspace.shared.activateFileViewerSelecting([target])
        }
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        downloads.removeValue(forKey: ObjectIdentifier(download))
        let alert = NSAlert()
        alert.messageText = tr("Téléchargement impossible", "Download failed")
        alert.informativeText = error.localizedDescription
        alert.beginSheetModal(for: window)
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

}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
