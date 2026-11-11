// arXivReader pour Mac : des fenêtres à onglets (WebKit, le moteur de Safari) sur le serveur local de l'application.
//
// Au lancement, l'application démarre le serveur Python qu'elle embarque (Contents/Resources/python et
// Contents/Resources/arxivreader), sur 127.0.0.1 seulement, avec la bibliothèque dans
// ~/Library/Application Support/arXivReader ; à la fermeture, elle l'arrête. Les liens vers d'autres
// sites s'ouvrent dans le navigateur par défaut.

import AppKit
import UniformTypeIdentifiers
import WebKit

let french = Locale.preferredLanguages.first?.hasPrefix("fr") ?? true
func tr(_ fr: String, _ en: String) -> String { french ? fr : en }
// Version d'essai (macos/build-beta.sh) : bibliothèque, port et réglages à part de la version stable
let isBeta = Bundle.main.bundleIdentifier == "local.arxivreader.beta"

final class AppDelegate: NSObject, NSApplicationDelegate {
    var tabs: [Tab] = []
    var serverReady = false
    var server: Process?
    var port = 8720
    var quitting = false
    var confirmedLeaving = false   // « Quitter quand même » déjà répondu : pas de seconde question
    var asking = false
    var restoring = false          // onglets de la session précédente en cours de remise en place : aucun ne se charge d'office
    var closingGroup = false       // ⌘⇧W : la session enregistrée garde le groupe fermé tant que d'autres restent
    var recentlyClosed: [(route: String, title: String)] = []  // derniers onglets fermés (⌘⇧T), le plus récent en dernier
    var recentMenu: NSMenu?
    var saveTimer: Timer?
    var origin: String { "http://127.0.0.1:\(port)" }
    // jeton de la session, tiré au hasard : le serveur ne répond qu'aux pages qui l'ont (cookie déposé par « /?t= »)
    let token = (0..<32).map { _ in String(format: "%02x", UInt8.random(in: 0...255)) }.joined()
    // bibliothèque : ~/Library/Application Support/arXivReader (ARXIVREADER_LIBRARY pour essayer sur une copie)
    let support = ProcessInfo.processInfo.environment["ARXIVREADER_LIBRARY"].map { URL(fileURLWithPath: $0, isDirectory: true) }
        ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent(isBeta ? "arXivReader Beta" : "arXivReader", isDirectory: true)

    // ---------- Démarrage et arrêt ----------

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        try? FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
        // premier onglet (essais : ARXIVREADER_START=#/paper/…,#/feed ouvre ces pages, les suivantes en onglets)
        let routes = (ProcessInfo.processInfo.environment["ARXIVREADER_START"] ?? "").split(separator: ",").map(String.init)
        if !routes.isEmpty || !sessionEnabled || !restoreSession() {
            let first = openTab(routes.first ?? "", beside: nil)
            for route in routes.dropFirst() { openTab(route, beside: first.window, front: false) }
        }
        NSApp.activate(ignoringOtherApps: true)
        port = freePort(preferred: isBeta ? 8730 : 8720)
        startServer()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    // Travaux en cours côté serveur (réponses de l'assistant, relectures du livre, liens) : quitter les perdrait.
    // Serveur muet ou réponse illisible : comme zéro, jamais de blocage.
    func askIfRunning(_ proceed: @escaping (Bool) -> Void) {
        if asking { return }  // une question est déjà en route : sa réponse décidera (répondre « non » ici annulerait l'arrêt en attente)
        guard serverReady, !confirmedLeaving, let url = URL(string: "\(origin)/api/ai/running") else { return proceed(true) }
        asking = true
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 2)
        request.setValue(token, forHTTPHeaderField: "X-ArXivReader-Token")
        URLSession.shared.dataTask(with: request) { [weak self] data, _, _ in
            let count = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }?["count"] as? Int ?? 0
            DispatchQueue.main.async {
                guard let self else { return }
                self.asking = false
                guard count > 0 else { return proceed(true) }
                let alert = NSAlert()
                alert.messageText = tr("Des travaux sont en cours.", "Work is still running.")
                alert.informativeText = tr("\(count) en cours (réponse de l'assistant, relecture, liens) : quitter maintenant les interrompt.",
                                           "\(count) in progress (assistant answer, review, related papers): quitting now interrupts them.")
                alert.addButton(withTitle: tr("Continuer", "Keep running"))
                alert.addButton(withTitle: tr("Quitter quand même", "Quit anyway"))
                let leave = alert.runModal() == .alertSecondButtonReturn
                if leave { self.confirmedLeaving = true }
                proceed(leave)
            }
        }.resume()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        saveSession()
        if serverReady && !confirmedLeaving && !quitting {
            askIfRunning { leave in
                guard leave else { return NSApp.reply(toApplicationShouldTerminate: false) }
                if self.shutDown() == .terminateNow { NSApp.reply(toApplicationShouldTerminate: true) }
            }
            return .terminateLater
        }
        return shutDown()
    }

    func shutDown() -> NSApplication.TerminateReply {
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
        env["ARXIVREADER_TOKEN"] = token
        if isBeta { env["ARXIVREADER_NO_ALERTS"] = "1" }  // ni alertes en double, ni écriture dans Zotero depuis l'essai
        env["ARXIVREADER_KEYCHAIN"] = "1"  // clés d'API dans le trousseau de session (app/keystore.py)
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        // fuseaux horaires : ceux de macOS (le Python embarqué garde en dur le chemin de l'ordinateur où il a été
        // construit : sur un autre Mac, « No time zone found with key America/New_York » au démarrage)
        env["PYTHONTZPATH"] = "/usr/share/zoneinfo"
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
        request.setValue(token, forHTTPHeaderField: "X-ArXivReader-Token")
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

    // ---------- Reprendre une bibliothèque existante (menu Fichier) ----------

    // Une autre bibliothèque arXivReader (dossier « library » de la version en ligne de commande, copie d'un autre
    // Mac) remplace celle-ci. Rien n'est effacé : la bibliothèque actuelle est mise de côté dans un dossier voisin.
    // Le serveur, qui tient la base ouverte, est arrêté pendant la copie puis relancé.
    @objc func copyLibrary() {
        let confirm = NSAlert()
        confirm.messageText = tr("Recopier une bibliothèque", "Copy a library")
        confirm.informativeText = tr(
            "Choisissez un dossier de bibliothèque arXivReader (il contient arxivreader.db) : il remplacera la bibliothèque actuelle, mise de côté dans un dossier voisin. Quittez d'abord l'autre version si elle est lancée.",
            "Choose an arXivReader library folder (it holds arxivreader.db): it will replace the current library, which is set aside in a folder next to it. Quit the other version first if it is running.")
        confirm.addButton(withTitle: tr("Choisir le dossier…", "Choose the folder…"))
        confirm.addButton(withTitle: tr("Annuler", "Cancel"))
        guard confirm.runModal() == .alertFirstButtonReturn else { return }
        let fm = FileManager.default
        var source: URL?
        while source == nil {
            let panel = NSOpenPanel()
            panel.canChooseDirectories = true
            panel.canChooseFiles = false
            panel.message = tr("Dossier de bibliothèque qui contient arxivreader.db", "Library folder containing arxivreader.db")
            panel.prompt = tr("Recopier", "Copy")
            guard panel.runModal() == .OK, let chosen = panel.url else { return }
            if chosen.standardizedFileURL == support.standardizedFileURL { return } // la bibliothèque actuelle elle-même
            if fm.fileExists(atPath: chosen.appendingPathComponent("arxivreader.db").path) { source = chosen; break }
            let missing = NSAlert()
            missing.messageText = tr("Ce dossier ne contient pas de bibliothèque arXivReader (arxivreader.db).",
                                     "This folder holds no arXivReader library (arxivreader.db).")
            missing.runModal()
        }
        guard let source else { return }
        // serveur arrêté (sans la fenêtre « le serveur s'est arrêté »), le temps de remplacer la base
        server?.terminationHandler = nil
        server?.terminate()
        server?.waitUntilExit()
        serverReady = false
        let stamp = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "-")
        let aside = support.deletingLastPathComponent()
            .appendingPathComponent("\(support.lastPathComponent) \(tr("avant la recopie", "before the copy")) \(stamp)")
        var problem: Error?
        do {
            try fm.createDirectory(at: aside, withIntermediateDirectories: true)
            for item in try fm.contentsOfDirectory(at: support, includingPropertiesForKeys: nil) {
                try fm.moveItem(at: item, to: aside.appendingPathComponent(item.lastPathComponent))
            }
            for item in try fm.contentsOfDirectory(at: source, includingPropertiesForKeys: nil)
            where !["ai", "server.log", "server.log.1", ".DS_Store"].contains(item.lastPathComponent) {
                try fm.copyItem(at: item, to: support.appendingPathComponent(item.lastPathComponent))
            }
        } catch {
            problem = error
        }
        port = freePort(preferred: port)
        startServer() // les onglets se reconnectent une fois le serveur prêt
        let done = NSAlert()
        if let problem {
            done.alertStyle = .warning
            done.messageText = tr("La copie a échoué.", "Copying failed.")
            done.informativeText = tr("L'ancienne bibliothèque est dans : ", "The previous library is in: ") + aside.path + "\n\n\(problem)"
        } else {
            done.messageText = tr("Bibliothèque recopiée.", "Library copied.")
            done.informativeText = tr("L'ancienne bibliothèque est mise de côté dans : ", "The previous library is set aside in: ") + aside.path
        }
        done.runModal()
    }

    // ---------- Onglets et fenêtres ----------

    // onglet de la fenêtre active (destinataire des menus : réglages, rechercher, recharger…)
    var keyTab: Tab? { tabs.first { $0.window.isKeyWindow } ?? tabs.first { $0.window.isMainWindow } ?? tabs.last }

    // Nouvel onglet à côté de `anchor` (sinon une nouvelle fenêtre) ; `front` : affiché aussitôt
    @discardableResult
    func openTab(_ route: String, beside anchor: NSWindow?, front: Bool = true, lazy: Bool = false, title: String? = nil) -> Tab {
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
        if lazy {
            // onglet d'une session rouverte : chargé à son premier affichage (beaucoup d'articles d'un coup pèseraient lourd)
            tab.lazyRoute = route
            if let title, !title.isEmpty { tab.window.title = title }
        } else {
            tab.load(route)
        }
        return tab
    }

    // Barre d'onglets toujours affichée, même avec un seul onglet (son « + » ouvre un nouvel onglet)
    func showTabBar(_ window: NSWindow) {
        DispatchQueue.main.async {
            guard window.isVisible, let group = window.tabGroup, !group.isTabBarVisible else { return }
            window.toggleTabBar(nil)
        }
    }

    func closed(_ tab: Tab) {
        let route = tab.currentRoute, title = tab.window.title
        tabs.removeAll { $0 === tab }
        guard !quitting else { return }
        // bibliothèque vide (« #/ ») : rien à rouvrir ; au plus 20 gardés
        if route != "#/" {
            recentlyClosed.append((route, title))
            if recentlyClosed.count > 20 { recentlyClosed.removeFirst() }
            rebuildRecentMenu()
        }
        if !closingGroup { sessionChanged() }
    }

    // ---------- Session : onglets rouverts au lancement, onglets fermés ----------

    // Essais (ARXIVREADER_START, _SNAPSHOT, _WINDOWSHOT, _EVAL) : une seule page de départ, et la session de l'utilisateur intacte
    var sessionEnabled: Bool {
        let env = ProcessInfo.processInfo.environment
        return !["ARXIVREADER_START", "ARXIVREADER_SNAPSHOT", "ARXIVREADER_WINDOWSHOT", "ARXIVREADER_EVAL"].contains { env[$0] != nil }
    }
    var sessionFile: URL { support.appendingPathComponent("session.json") }

    // Seules les routes de l'application sont rouvertes (même règle que les messages de la page)
    static func validRoute(_ route: String) -> Bool {
        route.range(of: "^#/[A-Za-z0-9/._%~-]*(\\?[A-Za-z0-9._%~=&-]*)?$", options: .regularExpression) != nil
    }

    // Groupes d'onglets (une fenêtre chacun) : [{tabs: [{route, title}], selected}]
    func saveSession() {
        saveTimer?.invalidate()
        saveTimer = nil
        guard sessionEnabled, !quitting, !tabs.isEmpty else { return }  // dernière fenêtre fermée : on garde la session d'avant
        var groups: [[String: Any]] = []
        var seen = Set<ObjectIdentifier>()
        for tab in tabs {
            let windows = tab.window.tabGroup?.windows ?? [tab.window]
            guard seen.insert(ObjectIdentifier(tab.window.tabGroup ?? tab.window)).inserted else { continue }
            let members = windows.compactMap { window in tabs.first { $0.window === window } }
            let entries: [[String: String]] = members.map { ["route": $0.currentRoute, "title": $0.window.title] }
            let selected = members.firstIndex { $0.window === tab.window.tabGroup?.selectedWindow } ?? 0
            groups.append(["tabs": entries, "selected": selected])
        }
        guard let data = try? JSONSerialization.data(withJSONObject: ["groups": groups]) else { return }
        try? data.write(to: sessionFile, options: .atomic)
    }

    // Enregistrement différé d'une seconde (le titre et l'adresse changent plusieurs fois par page)
    func sessionChanged() {
        guard sessionEnabled, !quitting else { return }
        saveTimer?.invalidate()
        saveTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: false) { [weak self] _ in self?.saveSession() }
    }

    // Rouvre la session enregistrée ; faux s'il n'y en a pas (fichier absent ou illisible : démarrage ordinaire).
    // Seul l'onglet affiché de chaque fenêtre se charge ; les autres à leur premier affichage (windowDidBecomeKey)
    func restoreSession() -> Bool {
        guard let data = try? Data(contentsOf: sessionFile),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let groups = root["groups"] as? [[String: Any]] else { return false }
        var shown: [Tab] = []
        restoring = true
        for group in groups {
            let entries = (group["tabs"] as? [[String: String]] ?? []).filter { Self.validRoute($0["route"] ?? "") }
            guard !entries.isEmpty else { continue }
            let selected = min(max(group["selected"] as? Int ?? 0, 0), entries.count - 1)
            var anchor: NSWindow?
            var current: Tab?
            for (index, entry) in entries.enumerated() {
                let tab = openTab(entry["route"] ?? "#/", beside: anchor, front: false, lazy: index != selected, title: entry["title"])
                anchor = anchor ?? tab.window
                if index == selected { current = tab }
            }
            if let current { shown.append(current) }
        }
        guard let last = shown.last else { restoring = false; return false }
        for tab in shown { tab.window.tabGroup?.selectedWindow = tab.window }
        last.window.makeKeyAndOrderFront(nil)
        // les changements de fenêtre active du début sont passés : les onglets en attente se chargent désormais à l'affichage
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            self?.restoring = false
            self?.tabs.forEach { if $0.window.tabGroup?.selectedWindow === $0.window { $0.loadIfLazy() } }
        }
        return true
    }

    @objc func reopenClosedTab() {
        guard let last = recentlyClosed.popLast() else { return }
        rebuildRecentMenu()
        openTab(last.route, beside: keyTab?.window)
    }
    @objc func reopenRecent(_ sender: NSMenuItem) {
        guard sender.tag >= 0, sender.tag < recentlyClosed.count else { return }
        let entry = recentlyClosed.remove(at: sender.tag)
        rebuildRecentMenu()
        openTab(entry.route, beside: keyTab?.window)
    }
    func rebuildRecentMenu() {
        guard let menu = recentMenu else { return }
        menu.removeAllItems()
        if recentlyClosed.isEmpty {
            let none = NSMenuItem(title: tr("Aucun", "None"), action: nil, keyEquivalent: "")
            none.isEnabled = false
            menu.addItem(none)
            return
        }
        for (index, entry) in recentlyClosed.enumerated().reversed() {
            let title = entry.title.isEmpty ? entry.route : String(entry.title.prefix(60))
            let item = NSMenuItem(title: title, action: #selector(reopenRecent(_:)), keyEquivalent: "")
            item.target = self
            item.tag = index
            menu.addItem(item)
        }
    }
    @objc func validateMenuItem(_ item: NSMenuItem) -> Bool {
        item.action == #selector(reopenClosedTab) ? !recentlyClosed.isEmpty : true
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

    // ---------- Menus ----------

    @objc func openSettings() {
        keyTab?.webView.evaluateJavaScript("document.getElementById('settings-button')?.click()")
    }
    @objc func reloadPage() { keyTab?.webView.reload() }
    @objc func newTab() { openTab("#/", beside: keyTab?.window) }
    @objc func newWindow() { openTab("#/", beside: nil) }
    // tous les onglets de la fenêtre
    // (close() ne passe pas par windowShouldClose : la question des travaux en cours est posée ici si c'est la dernière fenêtre)
    @objc func closeWindow() {
        let windows = keyTab?.window.tabGroup?.windows ?? keyTab.map { [$0.window] } ?? []
        let doClose = {
            self.saveSession()
            self.closingGroup = true
            windows.forEach { $0.close() }
            self.closingGroup = false
            self.sessionChanged()
        }
        if tabs.count > windows.count { doClose() } else { askIfRunning { if $0 { doClose() } } }
    }
    // Rechercher : la page s'en charge (barre de recherche du lecteur, champ de la bibliothèque)
    func find(_ command: String) {
        keyTab?.webView.evaluateJavaScript("document.dispatchEvent(new CustomEvent('app:find', { detail: '\(command)' }))")
    }
    @objc func findOpen() { find("open") }
    @objc func findNext() { find("next") }
    @objc func findPrevious() { find("previous") }
    // taille du texte : la page change son réglage (settings.js), comme le curseur des Réglages
    func zoom(_ command: String) {
        keyTab?.webView.evaluateJavaScript("document.dispatchEvent(new CustomEvent('app:zoom', { detail: '\(command)' }))")
    }
    @objc func zoomIn() { zoom("in") }
    @objc func zoomOut() { zoom("out") }
    @objc func zoomReset() { zoom("reset") }
    // ⌘1 à ⌘8 : l'onglet de ce numéro dans la fenêtre active ; ⌘9 : le dernier (comme Safari)
    @objc func selectTabNumber(_ sender: NSMenuItem) {
        guard let group = keyTab?.window.tabGroup else { return }
        let number = Int(sender.keyEquivalent) ?? 1
        let windows = group.windows
        let index = number == 9 ? windows.count - 1 : number - 1
        if index >= 0, index < windows.count { group.selectedWindow = windows[index] }
    }
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
        let recentItem = NSMenuItem(title: tr("Onglets récemment fermés", "Recently Closed"), action: nil, keyEquivalent: "")
        let recent = NSMenu(title: recentItem.title)
        recentItem.submenu = recent
        recentMenu = recent
        rebuildRecentMenu()
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
            entry(tr("Rouvrir l'onglet fermé", "Reopen Closed Tab"), #selector(reopenClosedTab), "t", [.command, .shift], target: self),
            recentItem,
            .separator(),
            entry(tr("Afficher tous les onglets", "Show All Tabs"), #selector(NSWindow.toggleTabOverview(_:)), "|"),
            .separator(),
            entry(tr("Recopier une bibliothèque…", "Copy a Library…"), #selector(copyLibrary), "", target: self),
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
            // taille du texte du lecteur (celle des Réglages) ; ⌘= est aussi pris en charge par TabWindow
            entry(tr("Agrandir le texte", "Increase Text Size"), #selector(zoomIn), "+", target: self),
            entry(tr("Réduire le texte", "Decrease Text Size"), #selector(zoomOut), "-", target: self),
            entry(tr("Taille du texte par défaut", "Default Text Size"), #selector(zoomReset), "0", target: self),
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
            .separator(),
        ] + (1...9).map { entry($0 == 9 ? tr("Dernier onglet", "Last Tab") : tr("Onglet \($0)", "Tab \($0)"), #selector(selectTabNumber(_:)), String($0), target: self) }
          + [.separator(), entry(tr("Tout ramener au premier plan", "Bring All to Front"), #selector(NSApplication.arrangeInFront(_:)))]
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
    // ⌘= agrandit aussi le texte (sur un clavier français ou américain, « + » demande ⇧ ou n'est pas à la même place)
    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        if event.modifierFlags.intersection(.deviceIndependentFlagsMask) == .command, event.charactersIgnoringModifiers == "=" {
            (NSApp.delegate as? AppDelegate)?.zoomIn()
            return true
        }
        return super.performKeyEquivalent(with: event)
    }

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
// Fichiers glissés depuis le Finder sur la page : leurs chemins (PDF, fichier TeX principal) sont retenus pour la page,
// qui les demande au moment du dépôt (droppedFiles), comme ceux de la fenêtre d'import ; le glisser-déposer lui-même
// reste celui de WebKit (la page choisit où un dépôt importe)
final class DropWebView: WKWebView {
    var droppedPaths: [String] = []

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        remember(sender)
        return super.draggingEntered(sender)
    }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        remember(sender)
        return super.performDragOperation(sender)
    }

    private func remember(_ sender: NSDraggingInfo) {
        let urls = sender.draggingPasteboard.readObjects(forClasses: [NSURL.self],
                                                         options: [.urlReadingFileURLsOnly: true]) as? [URL] ?? []
        droppedPaths = urls.map(\.path).filter { ["pdf", "tex", "ltx"].contains(URL(fileURLWithPath: $0).pathExtension.lowercased()) }
    }
}

final class MessageProxy: NSObject, WKScriptMessageHandler {
    weak var tab: Tab?
    init(_ tab: Tab) { self.tab = tab }
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) { tab?.received(message) }
}

final class Tab: NSObject, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    unowned let app: AppDelegate
    let window: TabWindow
    let webView: DropWebView
    var pending: String?            // page à ouvrir dès que le serveur répond
    var tested = false
    var titleObserver: NSKeyValueObservation?
    var urlObserver: NSKeyValueObservation?
    var lazyRoute: String?          // onglet d'une session rouverte, pas encore chargé
    var downloads: [ObjectIdentifier: URL] = [:]  // téléchargement en cours → fichier où il s'écrit
    var lastDownload: URL?                        // dernier fichier enregistré (bouton « Afficher dans le Finder »)
    var pendingDialogs: [UUID: () -> Void] = [:]  // fenêtres de la page ouvertes → réponse « annulé » si l'onglet se ferme avant
    var crashes: [Date] = []

    init(app: AppDelegate) {
        self.app = app
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        webView = DropWebView(frame: .zero, configuration: config)
        window = TabWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 860),
                           styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        super.init()
        config.userContentController.add(MessageProxy(self), name: "arxivreader")
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        if #available(macOS 13.3, *) { webView.isInspectable = true }
        window.isReleasedWhenClosed = false
        window.title = isBeta ? "arXivReader Beta" : "arXivReader"
        window.minSize = NSSize(width: 420, height: 480)
        window.tabbingIdentifier = "arXivReader"
        window.tabbingMode = .automatic
        window.registerForDraggedTypes(TabWindow.dragTypes) // article glissé sur un onglet : l'ouvre (TabWindow)
        window.contentView = webView
        window.delegate = self
        // titre de l'onglet et de la fenêtre : celui de la page (article lu, « Bibliothèque », « Feed »)
        titleObserver = webView.observe(\.title) { [weak self] web, _ in
            let title = web.title ?? ""
            self?.window.title = title.isEmpty ? (isBeta ? "arXivReader Beta" : "arXivReader") : isBeta ? "β \(title)" : title
            self?.app.sessionChanged()
        }
        // les routes sont des fragments (#/paper/…) : l'adresse change sans nouvelle page, la session le suit
        urlObserver = webView.observe(\.url) { [weak self] _, _ in self?.app.sessionChanged() }
    }

    // Page de l'application (« #/paper/… ») ; avant que le serveur réponde : page d'attente
    // route affichée (pour la session et ⌘⇧T) ; page pas encore chargée : celle qui l'attend
    var currentRoute: String {
        if let route = lazyRoute ?? pending { return route }
        let route = webView.url.flatMap { $0.fragment.map { "#\($0)" } } ?? "#/"
        return AppDelegate.validRoute(route) ? route : "#/"
    }
    func loadIfLazy() { if let route = lazyRoute { load(route) } }

    func load(_ route: String) {
        lazyRoute = nil
        if app.serverReady {
            pending = nil
            // « /?t=<jeton> » dépose le cookie de la session puis renvoie à « / » (la route, après #, est gardée)
            guard let url = URL(string: "\(app.origin)/?t=\(app.token)\(route)") ?? URL(string: "\(app.origin)/?t=\(app.token)") else { return }
            webView.load(URLRequest(url: url))
        } else {
            pending = route
            webView.loadHTMLString(app.startPage(), baseURL: nil)
        }
    }

    // Serveur prêt (ou relancé, peut-être sur un autre port) : page en attente, sinon la même page
    func reconnect() {
        if lazyRoute != nil { return }  // chargé à son premier affichage
        let route = pending ?? webView.url.flatMap { $0.fragment.map { "#\($0)" } } ?? ""
        load(route)
    }

    func received(_ message: WKScriptMessage) {
        if (message.body as? [String: Any])?["action"] as? String == "chooseFolder" { chooseFolder(); return }
        if (message.body as? [String: Any])?["action"] as? String == "droppedFiles" {
            // chemins du dernier glisser-déposer, rendus une seule fois (évènement arxivreader:dropped)
            let paths = webView.droppedPaths
            webView.droppedPaths = []
            guard let data = try? JSONSerialization.data(withJSONObject: [paths]),
                  let json = String(data: data, encoding: .utf8) else { return }
            webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('arxivreader:dropped', { detail: \(json)[0] }))")
            return
        }
        if (message.body as? [String: Any])?["action"] as? String == "revealDownload" {
            if let file = lastDownload { NSWorkspace.shared.activateFileViewerSelecting([file]) } // sur demande seulement
            return
        }
        if let body = message.body as? [String: Any], body["action"] as? String == "chooseFiles" {
            chooseFiles(bib: body["kind"] as? String == "bib")
            return
        }
        guard let body = message.body as? [String: Any], body["action"] as? String == "openTab",
              let route = body["route"] as? String,
              AppDelegate.validRoute(route) else { return }
        // comme Safari : nouvel onglet en arrière-plan (⇧ en plus : affiché aussitôt)
        app.openTab(route, beside: window, front: body["front"] as? Bool ?? false)
    }

    // dossier d'export des notes (Réglages > Lecture) : choisi ici, rendu à la page (évènement arxivreader:folder,
    // chemin, ou null si la fenêtre est annulée)
    func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.prompt = tr("Choisir", "Choose")
        panel.message = tr("Dossier où exporter la bibliothèque et les notes", "Folder to export the library and notes to")
        panel.beginSheetModal(for: window) { [weak self] response in
            let path: Any = response == .OK ? (panel.url?.path ?? NSNull()) : NSNull()
            guard let data = try? JSONSerialization.data(withJSONObject: [path]),
                  let json = String(data: data, encoding: .utf8) else { return }
            self?.webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('arxivreader:folder', { detail: \(json)[0] }))")
        }
    }

    // fichiers à importer (PDF, fichier TeX principal) ou .bib d'un fichier TeX : choisis ici, chemins rendus à la page
    // (évènement arxivreader:files, liste de chemins, ou null si la fenêtre est annulée) ; le chemin d'un fichier TeX
    // est gardé pour le recompiler
    func chooseFiles(bib: Bool) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = !bib
        panel.allowedContentTypes = (bib ? ["bib"] : ["pdf", "tex", "ltx"]).compactMap { UTType(filenameExtension: $0) }
        panel.prompt = tr("Choisir", "Choose")
        panel.message = bib ? tr("Fichier .bib de la bibliographie", "Bibliography .bib file")
            : tr("PDF, ou fichier principal d'un document TeX", "PDF, or main file of a TeX document")
        panel.beginSheetModal(for: window) { [weak self] response in
            let paths: Any = response == .OK ? panel.urls.map { $0.path } : NSNull()
            guard let data = try? JSONSerialization.data(withJSONObject: [paths]),
                  let json = String(data: data, encoding: .utf8) else { return }
            self?.webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('arxivreader:files', { detail: \(json)[0] }))")
        }
    }

    func windowDidBecomeKey(_ notification: Notification) {
        app.showTabBar(window)
        if !app.restoring { loadIfLazy() }
    }

    // Dernier onglet (bouton rouge, ⌘W) : refusé si des travaux tournent et qu'on veut les garder
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if app.tabs.count > 1 || app.confirmedLeaving || !app.serverReady { return true }
        app.askIfRunning { [weak self] leave in if leave { self?.window.close() } }
        return false
    }

    func windowWillClose(_ notification: Notification) {
        // fenêtres de alert()/confirm()/prompt() restées ouvertes : WebKit exige une réponse à chacune
        let cancels = Array(pendingDialogs.values)
        pendingDialogs = [:]
        cancels.forEach { $0() }
        titleObserver = nil
        urlObserver = nil
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
        if isAppURL(url) { app.openTab(url.fragment.map { "#\($0)" } ?? "#/", beside: window) }
        else { openOutside(url, action) } // même règle que partout : http(s) seulement sur un geste (un window.open sans clic est ignoré)
        return nil
    }

    // Page plantée (manque de mémoire…) : rechargée, mais pas en boucle si elle replante aussitôt ;
    // après trois plantages en une minute : un message et un bouton, pas une page blanche
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        crashes = crashes.filter { $0.timeIntervalSinceNow > -60 } + [Date()]
        if crashes.count <= 3 { webView.reload(); return }
        crashes = []
        let alert = NSAlert()
        alert.messageText = tr("Cette page a planté à plusieurs reprises.", "This page crashed several times.")
        alert.informativeText = tr("Elle est sans doute trop lourde pour la mémoire disponible. Vous pouvez la recharger ou fermer l'onglet.",
                                   "It is probably too heavy for the available memory. You can reload it or close the tab.")
        alert.addButton(withTitle: tr("Recharger", "Reload"))
        alert.addButton(withTitle: tr("Fermer l'onglet", "Close tab"))
        sheet(alert, cancel: {}) { [weak self] response in
            guard let self else { return }
            if response == .alertFirstButtonReturn { self.webView.reload() } else { self.window.close() }
        }
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
    // enregistré : la page le dit (avec « Afficher dans le Finder »), sans faire passer le Finder devant ; échec : signalé
    func downloadDidFinish(_ download: WKDownload) {
        guard let target = downloads.removeValue(forKey: ObjectIdentifier(download)) else { return }
        lastDownload = target
        guard let data = try? JSONSerialization.data(withJSONObject: [target.lastPathComponent]),
              let json = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('arxivreader:downloaded', { detail: \(json)[0] }))")
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
        sheet(alert, cancel: completionHandler) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: tr("Annuler", "Cancel"))
        sheet(alert, cancel: { completionHandler(false) }) { completionHandler($0 == .alertFirstButtonReturn) }
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
        sheet(alert, cancel: { completionHandler(nil) }) { completionHandler($0 == .alertFirstButtonReturn ? field.stringValue : nil) }
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        let id = UUID()
        pendingDialogs[id] = { completionHandler(nil) }
        panel.beginSheetModal(for: window) { [weak self] response in
            guard self?.pendingDialogs.removeValue(forKey: id) != nil else { return }
            completionHandler(response == .OK ? panel.urls : nil)
        }
    }

    // Fenêtre attachée à l'onglet ; fermé avant la réponse, windowWillClose la clôt par « annulé » (une seule réponse)
    func sheet(_ alert: NSAlert, cancel: @escaping () -> Void, answer: @escaping (NSApplication.ModalResponse) -> Void) {
        let id = UUID()
        pendingDialogs[id] = cancel
        alert.beginSheetModal(for: window) { [weak self] response in
            guard self?.pendingDialogs.removeValue(forKey: id) != nil else { return }
            answer(response)
        }
    }

}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
