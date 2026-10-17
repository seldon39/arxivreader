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

        string Origin => $"http://127.0.0.1:{port}";

        public MainForm(string library, Server server, EventWaitHandle show)
        {
            this.library = library;
            this.server = server;
            Text = "arXivReader";
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            MinimumSize = new Size(640, 420);
            LoadBounds();
            status.Text = Program.Tr("Démarrage d'arXivReader…", "Starting arXivReader…");
            content.Controls.Add(status);
            Controls.Add(content);
            Controls.Add(strip);
            content.BringToFront();  // la barre d'onglets en haut, les pages dans le reste
            strip.Selected += index => SelectTab(tabs[index]);
            strip.Closed += index => CloseTab(tabs[index]);
            strip.Added += () => OpenTab("#/", true);
            KeyPreview = true;
            KeyDown += (s, e) => { if (Shortcut(e.KeyData)) e.Handled = true; };

            // second lancement de l'application : cette fenêtre revient au premier plan
            new Thread(() =>
            {
                while (show.WaitOne())
                    BeginInvoke(new Action(() =>
                    {
                        if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
                        Activate();
                    }));
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
        }

        async Task OpenTab(string route, bool front)
        {
            if (environment == null || port == 0) return;
            var tab = new Tab { View = new WebView2 { Dock = DockStyle.Fill } };
            int at = current == null ? tabs.Count : tabs.IndexOf(current) + 1;  // à droite de l'onglet actif, comme le Mac
            tabs.Insert(at, tab);
            content.Controls.Add(tab.View);
            if (front) SelectTab(tab); else tab.View.SendToBack();
            UpdateStrip();
            await tab.View.EnsureCoreWebView2Async(environment);
            var core = tab.View.CoreWebView2;
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
                if (IsApp(uri)) _ = OpenTab(uri.Fragment.Length > 1 ? uri.Fragment : "#/", true);
                else if (e.IsUserInitiated) OpenOutside(uri);
            };
            core.WebMessageReceived += (s, e) => Received(e.WebMessageAsJson);
            core.AcceleratorKeyPressed += (s, e) =>
            {
                if (e.KeyEventKind != CoreWebView2KeyEventKind.KeyDown) return;
                var keys = (Keys)e.VirtualKey | ModifierKeys;
                if (Shortcut(keys)) e.Handled = true;
            };
            core.ProcessFailed += (s, e) =>
            {
                // page plantée (manque de mémoire…) : rechargée, mais pas en boucle si elle replante aussitôt
                tab.Crashes = tab.Crashes.Where(d => DateTime.Now - d < TimeSpan.FromMinutes(1)).Append(DateTime.Now).ToArray();
                if (tab.Crashes.Length <= 3) BeginInvoke(new Action(() => tab.View.Reload()));
            };
            core.Navigate($"{Origin}/{route}");
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

        void Received(string json)
        {
            // {"action":"openTab","route":"#/paper/…","front":false} ; route vérifiée, comme sur le Mac
            var action = Regex.Match(json, "\"action\"\\s*:\\s*\"([a-zA-Z]+)\"").Groups[1].Value;
            var route = Regex.Match(json, "\"route\"\\s*:\\s*\"([^\"\\\\]*)\"").Groups[1].Value;
            bool front = Regex.IsMatch(json, "\"front\"\\s*:\\s*true");
            if (action == "openTab" && Route.IsMatch(route)) _ = OpenTab(route, front);
        }

        // Raccourcis des onglets (Ctrl+T, Ctrl+W, Ctrl+Tab, Ctrl+1…9) ; le reste va à la page
        bool Shortcut(Keys keys)
        {
            bool shift = (keys & Keys.Shift) != 0;
            switch (keys & ~Keys.Shift)
            {
                case Keys.Control | Keys.T:
                case Keys.Control | Keys.N:
                    _ = OpenTab("#/", true);
                    return true;
                case Keys.Control | Keys.W:
                case Keys.Control | Keys.F4:
                    if (current != null) CloseTab(current);
                    return true;
                case Keys.Control | Keys.Tab:
                    if (tabs.Count > 1) SelectTab(tabs[(tabs.IndexOf(current) + (shift ? tabs.Count - 1 : 1)) % tabs.Count]);
                    return true;
            }
            var key = keys & Keys.KeyCode;
            if ((keys & Keys.Control) != 0 && !shift && key >= Keys.D1 && key <= Keys.D9)
            {
                int index = key == Keys.D9 ? tabs.Count - 1 : key - Keys.D1;  // Ctrl+9 : le dernier, comme dans les navigateurs
                if (index >= 0 && index < tabs.Count) SelectTab(tabs[index]);
                return true;
            }
            return false;
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
            if (tabs.Count == 0)
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
            var bounds = new Rectangle(0, 0, Math.Min(1280, area.Width), Math.Min(860, area.Height));
            bounds.Offset(area.Left + (area.Width - bounds.Width) / 2, area.Top + (area.Height - bounds.Height) / 2);
            bool maximized = false;
            try
            {
                var parts = File.ReadAllText(BoundsFile).Trim().Split(',').Select(int.Parse).ToArray();
                var saved = new Rectangle(parts[0], parts[1], parts[2], parts[3]);
                if (Screen.AllScreens.Any(s => s.WorkingArea.IntersectsWith(saved))) bounds = saved;  // écran encore là
                maximized = parts.Length > 4 && parts[4] == 1;
            }
            catch (Exception) { }
            Bounds = bounds;
            if (maximized) WindowState = FormWindowState.Maximized;
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            var b = WindowState == FormWindowState.Normal ? Bounds : base.RestoreBounds;
            try { File.WriteAllText(BoundsFile, $"{b.X},{b.Y},{b.Width},{b.Height},{(WindowState == FormWindowState.Maximized ? 1 : 0)}"); }
            catch (Exception) { }
            base.OnFormClosing(e);
        }
    }
}
