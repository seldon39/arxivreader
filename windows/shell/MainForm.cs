// Fenêtre de l'application : une page WebView2 par onglet (chacune avec son propre historique), barre d'onglets
// façon macOS (TabStrip). Comme sur le Mac : les pages de l'application restent dans la fenêtre, les autres liens
// (arXiv, DOI…) s'ouvrent dans le navigateur par défaut, et la page demande un nouvel onglet par
// window.chrome.webview.postMessage({action: "openTab", route, front}).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace ArxivReader
{
    sealed class MainForm : Form
    {
        sealed class Tab
        {
            public WebView2 View;
            public string Title = "arXivReader";
            public string Route = "#/";  // page affichée (#/…) : rouverte si le serveur ou le moteur redémarre
            public DateTime[] Crashes = new DateTime[0];
        }

        static readonly Regex Route = new Regex(@"^#/[A-Za-z0-9/._%~-]*$");

        readonly string library;
        readonly Server server;
        readonly TabStrip strip = new TabStrip();
        readonly Panel content = new Panel { Dock = DockStyle.Fill, BackColor = Color.FromArgb(245, 243, 239) };
        readonly Label status = new Label { Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleCenter, ForeColor = Color.DimGray };
        readonly List<Tab> tabs = new List<Tab>();
        CoreWebView2Environment environment;
        Tab current;
        int port;
        bool restarting, rebuilding, closing;
        FormWindowState lastState = FormWindowState.Normal;  // avant réduction dans la barre des tâches

        // tailles données en pixels « à 96 ppp », agrandies sur les écrans à haute densité
        int Scaled(int pixels) => (int)Math.Round(pixels * DeviceDpi / 96.0);

        protected override void OnResize(EventArgs e)
        {
            base.OnResize(e);
            if (WindowState != FormWindowState.Minimized) lastState = WindowState;
        }

        string Origin => $"http://127.0.0.1:{port}";

        public MainForm(string library, Server server, EventWaitHandle show)
        {
            this.library = library;
            this.server = server;
            Text = "arXivReader";
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            MinimumSize = new Size(Scaled(640), Scaled(420));
            LoadBounds();
            status.Text = Program.Tr("Démarrage d'arXivReader…", "Starting arXivReader…");
            content.Controls.Add(status);
            Controls.Add(content);
            Controls.Add(strip);
            content.BringToFront();  // la barre d'onglets en haut, les pages dans le reste
            strip.Selected += index => SelectTab(tabs[index]);
            strip.Closed += index => CloseTab(tabs[index]);
            strip.Added += () => _ = OpenTab("#/", true);
            server.Ended += () => { if (IsHandleCreated && !IsDisposed && !closing) BeginInvoke(new Action(ServerEnded)); };

            this.show = show;
        }

        readonly EventWaitHandle show;

        // Second lancement de l'application : cette fenêtre revient au premier plan (attente démarrée une fois la
        // fenêtre créée ; signal reçu pendant la fermeture : ignoré)
        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            new Thread(() =>
            {
                while (show.WaitOne())
                {
                    if (!IsHandleCreated || IsDisposed || closing) continue;
                    try
                    {
                        BeginInvoke(new Action(() =>
                        {
                            if (WindowState == FormWindowState.Minimized) WindowState = lastState;
                            Activate();
                        }));
                    }
                    catch (InvalidOperationException) { }  // fenêtre fermée entre-temps
                }
            }) { IsBackground = true }.Start();
        }

        protected override async void OnShown(EventArgs e)
        {
            base.OnShown(e);
            try
            {
                environment = await CoreWebView2Environment.CreateAsync(null, Path.Combine(library, "webview2"));
            }
            catch (WebView2RuntimeNotFoundException)
            {
                MessageBox.Show(Program.Tr(
                    "Le composant Microsoft Edge WebView2 est absent de cet ordinateur. Installez-le depuis\n" +
                    "https://go.microsoft.com/fwlink/p/?LinkId=2124703\npuis relancez arXivReader.",
                    "The Microsoft Edge WebView2 component is missing on this computer. Install it from\n" +
                    "https://go.microsoft.com/fwlink/p/?LinkId=2124703\nthen start arXivReader again."), "arXivReader",
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
                Close();
                return;
            }
            catch (Exception error)
            {
                MessageBox.Show(error.Message, "arXivReader", MessageBoxButtons.OK, MessageBoxIcon.Error);
                Close();
                return;
            }
            try
            {
                port = await server.StartAsync();
            }
            catch (Exception error)
            {
                MessageBox.Show(error.Message + "\n\n" + server.LogTail(), "arXivReader", MessageBoxButtons.OK, MessageBoxIcon.Error);
                Close();
                return;
            }
            // essais : ARXIVREADER_START=#/paper/…,#/feed ouvre ces pages, les suivantes en onglets
            var routes = (Environment.GetEnvironmentVariable("ARXIVREADER_START") ?? "").Split(',')
                .Where(r => Route.IsMatch(r)).DefaultIfEmpty("#/").ToList();
            content.Controls.Remove(status);
            foreach (var route in routes) await OpenTab(route, route == routes[0]);
            // profil Edge des préversions 1.9.15–1.9.16, inutile depuis : supprimé (en arrière-plan)
            string oldProfile = Path.Combine(library, "edge-profile");
            if (Directory.Exists(oldProfile))
                _ = Task.Run(() => { try { Directory.Delete(oldProfile, true); } catch (Exception) { } });
        }

        async Task OpenTab(string route, bool front)
        {
            if (environment == null || port == 0) return;
            var tab = new Tab { View = new WebView2 { Dock = DockStyle.Fill }, Route = route };
            int at = current == null ? tabs.Count : tabs.IndexOf(current) + 1;  // à droite de l'onglet actif, comme le Mac
            tabs.Insert(at, tab);
            content.Controls.Add(tab.View);
            if (front) SelectTab(tab); else tab.View.SendToBack();
            UpdateStrip();
            try
            {
                await tab.View.EnsureCoreWebView2Async(environment);
            }
            catch (Exception)
            {
                if (tabs.Contains(tab)) CloseTab(tab);  // moteur indisponible pour cet onglet : pas d'onglet blanc
                return;
            }
            var core = tab.View.CoreWebView2;
            core.SourceChanged += (s, e) =>
            {
                if (Uri.TryCreate(core.Source, UriKind.Absolute, out var uri) && IsApp(uri))
                    tab.Route = uri.Fragment.Length > 1 ? uri.Fragment : "#/";
            };
            core.Settings.AreDevToolsEnabled = Environment.GetEnvironmentVariable("ARXIVREADER_DEVTOOLS") == "1";
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.IsZoomControlEnabled = true;
            core.DocumentTitleChanged += (s, e) =>
            {
                tab.Title = string.IsNullOrWhiteSpace(core.DocumentTitle) ? "arXivReader" : core.DocumentTitle;
                UpdateStrip();
            };
            core.NavigationStarting += (s, e) =>
            {
                if (!Uri.TryCreate(e.Uri, UriKind.Absolute, out var uri)) { e.Cancel = true; return; }
                // data:, blob: : contenus construits par la page (exports, PDF), téléchargés plutôt qu'affichés
                if (IsApp(uri) || uri.Scheme == "about" || uri.Scheme == "data" || uri.Scheme == "blob") return;
                e.Cancel = true;  // jamais une autre page dans la fenêtre
                if (e.IsUserInitiated) OpenOutside(uri);
            };
            core.NewWindowRequested += (s, e) =>
            {
                e.Handled = true;  // target="_blank", window.open : page de l'application dans un onglet, le reste dehors
                if (!Uri.TryCreate(e.Uri, UriKind.Absolute, out var uri)) return;
                if (IsApp(uri) && uri.AbsolutePath != "/") core.Navigate(uri.AbsoluteUri);  // export, PDF : téléchargé ici
                else if (IsApp(uri)) _ = OpenTab(uri.Fragment.Length > 1 ? uri.Fragment : "#/", true);
                else if (e.IsUserInitiated) OpenOutside(uri);
            };
            core.WebMessageReceived += (s, e) =>
            {
                // seulement les pages de l'application (pas un contenu data:, blob: ou un cadre d'ailleurs)
                if (!Uri.TryCreate(e.Source, UriKind.Absolute, out var from) || !IsApp(from)) return;
                // fichiers déposés sur la page (postMessageWithAdditionalObjects) : leurs chemins, PDF et TeX seulement,
                // rendus à la page ({"action":"dropped","paths":[…]}), comme ceux de la fenêtre d'import
                if (Regex.IsMatch(e.WebMessageAsJson, "\"action\"\\s*:\\s*\"droppedFiles\""))
                {
                    var paths = ((IEnumerable<object>)e.AdditionalObjects ?? Enumerable.Empty<object>()).OfType<CoreWebView2File>().Select(f => f.Path)
                        .Where(p => new[] { ".pdf", ".tex", ".ltx" }.Contains(System.IO.Path.GetExtension(p).ToLowerInvariant()));
                    var json = "[" + string.Join(",", paths.Select(p => "\"" + p.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"")) + "]";
                    try { core.PostWebMessageAsJson("{\"action\":\"dropped\",\"paths\":" + json + "}"); } catch (Exception) { }
                    return;
                }
                Received(e.WebMessageAsJson, core);
            };
            core.ProcessFailed += (s, e) =>
            {
                switch (e.ProcessFailedKind)
                {
                    case CoreWebView2ProcessFailedKind.RenderProcessExited:
                    case CoreWebView2ProcessFailedKind.RenderProcessUnresponsive:
                        // page plantée (manque de mémoire…) : rechargée, mais pas en boucle si elle replante aussitôt
                        tab.Crashes = tab.Crashes.Where(d => DateTime.Now - d < TimeSpan.FromMinutes(1)).Append(DateTime.Now).ToArray();
                        if (tab.Crashes.Length <= 3)
                            BeginInvoke(new Action(() => { try { tab.View.Reload(); } catch (Exception) { } }));
                        break;
                    case CoreWebView2ProcessFailedKind.BrowserProcessExited:
                        // le moteur entier s'est arrêté : toutes les pages sont à recréer (une seule fois pour tous les onglets)
                        BeginInvoke(new Action(() => _ = Rebuild()));
                        break;
                    // autres processus (carte graphique, utilitaires) : le moteur les relance seul
                }
            };
            core.Navigate($"{Origin}/{route}");
        }

        // Serveur arrêté de lui-même : comme sur le Mac, le relancer (les onglets rouvrent leur page) ou quitter
        async void ServerEnded()
        {
            if (restarting || closing) return;
            restarting = true;
            var answer = MessageBox.Show(Program.Tr("Le serveur d'arXivReader s'est arrêté. Le relancer ?\n\n",
                "arXivReader's server stopped. Restart it?\n\n") + server.LogTail(6), "arXivReader",
                MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
            if (answer != DialogResult.Yes) { Close(); return; }
            try
            {
                port = await server.StartAsync();
            }
            catch (Exception error)
            {
                MessageBox.Show(error.Message + "\n\n" + server.LogTail(), "arXivReader", MessageBoxButtons.OK, MessageBoxIcon.Error);
                Close();
                return;
            }
            foreach (var tab in tabs)
                try { tab.View.CoreWebView2?.Navigate($"{Origin}/{tab.Route}"); } catch (Exception) { }
            restarting = false;
        }

        // Moteur d'affichage arrêté (toutes les pages perdues) : nouveau moteur, mêmes onglets, même onglet actif
        async Task Rebuild()
        {
            if (rebuilding || closing) return;
            rebuilding = true;
            var routes = tabs.Select(t => t.Route).ToList();
            int active = Math.Max(0, tabs.IndexOf(current));
            foreach (var tab in tabs.ToList())
            {
                content.Controls.Remove(tab.View);
                try { tab.View.Dispose(); } catch (Exception) { }
            }
            tabs.Clear();
            current = null;
            try
            {
                environment = await CoreWebView2Environment.CreateAsync(null, Path.Combine(library, "webview2"));
            }
            catch (Exception error)
            {
                MessageBox.Show(error.Message, "arXivReader", MessageBoxButtons.OK, MessageBoxIcon.Error);
                Close();
                return;
            }
            for (int i = 0; i < routes.Count; i++) await OpenTab(routes[i], i == active);
            rebuilding = false;
        }

        bool IsApp(Uri uri) =>
            uri.Scheme == "http" && uri.Host == "127.0.0.1" && uri.Port == port && string.IsNullOrEmpty(uri.UserInfo);

        // Lien vers l'extérieur : navigateur par défaut (ou messagerie), seulement http(s), mailto et claude://
        static void OpenOutside(Uri uri)
        {
            if (!new[] { "http", "https", "mailto", "claude" }.Contains(uri.Scheme.ToLowerInvariant())) return;
            try { Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true }); }
            catch (Exception) { }
        }

        void Received(string json, CoreWebView2 sender)
        {
            // {"action":"openTab","route":"#/paper/…","front":false} ; route vérifiée, comme sur le Mac
            var action = Regex.Match(json, "\"action\"\\s*:\\s*\"([a-zA-Z]+)\"").Groups[1].Value;
            // fenêtre du système hors du gestionnaire de WebView2 (une boucle de messages imbriquée y est déconseillée)
            if (action == "chooseFolder") { BeginInvoke(new Action(() => ChooseFolder(sender))); return; }
            if (action == "chooseFiles")
            {
                bool bib = Regex.IsMatch(json, "\"kind\"\\s*:\\s*\"bib\"");
                BeginInvoke(new Action(() => ChooseFiles(sender, bib)));
                return;
            }
            var route = Regex.Match(json, "\"route\"\\s*:\\s*\"([^\"\\\\]*)\"").Groups[1].Value;
            bool front = Regex.IsMatch(json, "\"front\"\\s*:\\s*true");
            if (action == "openTab" && Route.IsMatch(route)) _ = OpenTab(route, front);
        }

        // Dossier d'export des notes (Réglages > Lecture) : choisi ici, rendu à la page ({"action":"folder","path":…},
        // path null si la fenêtre est annulée)
        void ChooseFolder(CoreWebView2 sender)
        {
            string path = null;
            using (var dialog = new FolderBrowserDialog { ShowNewFolderButton = true,
                Description = Program.Tr("Dossier où exporter la bibliothèque et les notes", "Folder to export the library and notes to") })
            {
                if (dialog.ShowDialog(this) == DialogResult.OK) path = dialog.SelectedPath;
            }
            var json = path == null ? "null" : "\"" + path.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
            try { sender.PostWebMessageAsJson("{\"action\":\"folder\",\"path\":" + json + "}"); } catch (Exception) { }
        }

        // Fichiers à importer (PDF, fichier TeX principal) ou .bib d'un fichier TeX : choisis ici, chemins rendus à la
        // page ({"action":"files","paths":[…]}, paths null si la fenêtre est annulée) ; le chemin d'un fichier TeX est
        // gardé pour le recompiler
        void ChooseFiles(CoreWebView2 sender, bool bib)
        {
            string[] paths = null;
            using (var dialog = new OpenFileDialog { Multiselect = !bib,
                Filter = bib ? Program.Tr("Bibliographie (*.bib)|*.bib", "Bibliography (*.bib)|*.bib")
                    : Program.Tr("PDF ou TeX (*.pdf;*.tex;*.ltx)|*.pdf;*.tex;*.ltx", "PDF or TeX (*.pdf;*.tex;*.ltx)|*.pdf;*.tex;*.ltx") })
            {
                if (dialog.ShowDialog(this) == DialogResult.OK) paths = dialog.FileNames;
            }
            var json = paths == null ? "null"
                : "[" + string.Join(",", paths.Select(p => "\"" + p.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"")) + "]";
            try { sender.PostWebMessageAsJson("{\"action\":\"files\",\"paths\":" + json + "}"); } catch (Exception) { }
        }

        // Raccourcis des onglets (Ctrl+T, Ctrl+W, Ctrl+Tab, Ctrl+1…9) ; le reste va à la page
        bool Shortcut(Keys keys, bool dryRun = false)
        {
            // AltGr arrive comme Ctrl+Alt : AltGr+3 (« # »), AltGr+8 (« \ »)… sont des caractères à taper, pas des
            // raccourcis (claviers français, allemand…)
            if ((keys & Keys.Alt) != 0) return false;
            bool shift = (keys & Keys.Shift) != 0;
            switch (keys & ~Keys.Shift)
            {
                case Keys.Control | Keys.T:
                case Keys.Control | Keys.N:
                    if (!dryRun) _ = OpenTab("#/", true);
                    return true;
                case Keys.Control | Keys.W:
                case Keys.Control | Keys.F4:
                    var closed = current;
                    if (!dryRun && closed != null) BeginInvoke(new Action(() => { if (tabs.Contains(closed)) CloseTab(closed); }));
                    return true;
                case Keys.Control | Keys.Tab:
                    if (!dryRun && tabs.Count > 1) SelectTab(tabs[(tabs.IndexOf(current) + (shift ? tabs.Count - 1 : 1)) % tabs.Count]);
                    return true;
            }
            var key = keys & Keys.KeyCode;
            if ((keys & Keys.Control) != 0 && !shift && key >= Keys.D1 && key <= Keys.D9)
            {
                int index = key == Keys.D9 ? tabs.Count - 1 : key - Keys.D1;  // Ctrl+9 : le dernier, comme dans les navigateurs
                if (!dryRun && index >= 0 && index < tabs.Count) SelectTab(tabs[index]);
                return true;
            }
            return false;
        }

        // Touches de raccourci : le contrôle WebView2 les passe à la fenêtre (comme celles de la barre d'onglets)
        protected override bool ProcessCmdKey(ref Message message, Keys keys)
        {
            bool repeat = ((long)message.LParam & 0x40000000) != 0;  // touche maintenue (répétition automatique)
            if (repeat && Shortcut(keys, dryRun: true)) return true;  // Ctrl+W maintenu : pas tous les onglets d'un coup
            return Shortcut(keys) || base.ProcessCmdKey(ref message, keys);
        }

        void SelectTab(Tab tab)
        {
            current = tab;
            foreach (var other in tabs) other.View.Visible = other == tab;
            tab.View.BringToFront();
            tab.View.Focus();
            UpdateStrip();
        }

        void CloseTab(Tab tab)
        {
            int index = tabs.IndexOf(tab);
            tabs.Remove(tab);
            content.Controls.Remove(tab.View);
            tab.View.Dispose();
            if (tabs.Count == 0 && !rebuilding)
            {
                Close();  // dernier onglet fermé : l'application aussi, comme sur le Mac
                return;
            }
            if (current == tab) SelectTab(tabs[Math.Min(index, tabs.Count - 1)]);
            else UpdateStrip();
        }

        void UpdateStrip()
        {
            strip.SetTabs(tabs.Select(t => t.Title).ToList(), tabs.IndexOf(current));
            Text = current == null || current.Title == "arXivReader" ? "arXivReader" : $"{current.Title} — arXivReader";
        }

        // Taille et position de la fenêtre, gardées d'un lancement à l'autre (window.txt de la bibliothèque)
        string BoundsFile => Path.Combine(library, "window.txt");

        void LoadBounds()
        {
            StartPosition = FormStartPosition.Manual;
            var area = Screen.PrimaryScreen.WorkingArea;
            var bounds = new Rectangle(0, 0, Math.Min(Scaled(1280), area.Width), Math.Min(Scaled(860), area.Height));
            bounds.Offset(area.Left + (area.Width - bounds.Width) / 2, area.Top + (area.Height - bounds.Height) / 2);
            bool maximized = false;
            try
            {
                var parts = File.ReadAllText(BoundsFile).Trim().Split(',').Select(int.Parse).ToArray();
                var saved = new Rectangle(parts[0], parts[1], parts[2], parts[3]);
                // écran encore là, et barre de titre atteignable (pas seulement un coin de fenêtre visible)
                var grip = new Point(saved.Left + Math.Min(120, saved.Width / 2), saved.Top + 10);
                if (saved.Width >= 200 && saved.Height >= 150 && Screen.AllScreens.Any(s => s.WorkingArea.Contains(grip))) bounds = saved;
                maximized = parts.Length > 4 && parts[4] == 1;
            }
            catch (Exception) { }
            Bounds = bounds;
            if (maximized) WindowState = FormWindowState.Maximized;
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            closing = true;
            var b = WindowState == FormWindowState.Normal ? Bounds : base.RestoreBounds;
            bool maximized = WindowState == FormWindowState.Maximized
                || (WindowState == FormWindowState.Minimized && lastState == FormWindowState.Maximized);  // fermée réduite
            try { File.WriteAllText(BoundsFile, $"{b.X},{b.Y},{b.Width},{b.Height},{(maximized ? 1 : 0)}"); }
            catch (Exception) { }
            base.OnFormClosing(e);
        }
    }
}
