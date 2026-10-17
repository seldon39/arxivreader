// Application Windows : démarre le serveur local (server\arXivReader-server.exe, construit par PyInstaller depuis
// windows/launcher.py), puis l'affiche dans une fenêtre à onglets (MainForm). Bibliothèque dans
// %LOCALAPPDATA%\arXivReader (ARXIVREADER_LIBRARY pour essayer sur une copie). Un second lancement ramène la
// fenêtre déjà ouverte au premier plan, comme sur le Mac.
using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace ArxivReader
{
    static class Program
    {
        public static bool French => CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "fr";
        public static string Tr(string fr, string en) => French ? fr : en;

        [STAThread]
        static void Main()
        {
            string library = Environment.GetEnvironmentVariable("ARXIVREADER_LIBRARY");
            if (string.IsNullOrEmpty(library))
                library = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "arXivReader");
            Directory.CreateDirectory(library);

            string key = "arXivReader-" + Hash(Path.GetFullPath(library).ToLowerInvariant());
            using var mutex = new Mutex(true, @"Local\" + key + "-window", out bool first);
            var show = new EventWaitHandle(false, EventResetMode.AutoReset, @"Local\" + key + "-show");
            if (!first)
            {
                AllowSetForegroundWindow(-1);  // ASFW_ANY : la fenêtre déjà ouverte peut passer devant (pas seulement clignoter)
                show.Set();  // déjà lancée : sa fenêtre revient au premier plan
                return;
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            using var server = new Server(library);
            Application.Run(new MainForm(library, server, show));
        }

        [DllImport("user32.dll")] static extern bool AllowSetForegroundWindow(int processId);

        static string Hash(string text)
        {
            using var sha = SHA1.Create();
            return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(text)).Take(8).Select(b => b.ToString("x2")));
        }
    }

    // Serveur Python : lancé sans fenêtre, attaché à un « job » Windows qui l'arrête avec l'application, même si
    // celle-ci plante ; port lu dans server.port, écrit par le serveur une fois prêt.
    sealed class Server : IDisposable
    {
        readonly string library;
        readonly IntPtr job = Job.Create();
        Process process;
        bool stopping, ready;

        public Server(string library) { this.library = library; }

        // Serveur arrêté de lui-même après son démarrage (plantage, arrêt forcé) : la fenêtre propose de le relancer
        public event Action Ended;

        public string Log => Path.Combine(library, "server.log");

        public Task<int> StartAsync() => Task.Run(() =>
        {
            ready = false;  // arrêt pendant le démarrage : signalé par StartAsync lui-même, pas par Ended
            string portFile = Path.Combine(library, "server.port");
            try { File.Delete(portFile); } catch (IOException) { }
            string exe = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "server", "arXivReader-server.exe");
            var info = new ProcessStartInfo(exe) { UseShellExecute = false, CreateNoWindow = true };
            info.EnvironmentVariables["ARXIVREADER_LIBRARY"] = library;
            info.EnvironmentVariables["ARXIVREADER_NO_BROWSER"] = "1";
            process = Process.Start(info);
            Job.Assign(job, process);
            var launched = process;
            launched.EnableRaisingEvents = true;
            launched.Exited += (s, e) => { if (!stopping && ready && launched == process) Ended?.Invoke(); };
            var started = Stopwatch.StartNew();
            while (started.Elapsed < TimeSpan.FromSeconds(120))
            {
                if (process.HasExited)
                    throw new InvalidOperationException(process.ExitCode == 3
                        ? Program.Tr("Une autre fenêtre d'arXivReader (peut-être une version précédente) est encore ouverte : fermez-la, puis relancez arXivReader.",
                                     "Another arXivReader window (perhaps a previous version) is still open: close it, then start arXivReader again.")
                        : Program.Tr("Le serveur s'est arrêté.", "The server stopped."));
                if (File.Exists(portFile) && int.TryParse(ReadShared(portFile).Trim(), out int port) && Listening(port))
                {
                    ready = true;
                    return port;
                }
                Thread.Sleep(150);
            }
            throw new TimeoutException(Program.Tr("Le serveur n'a pas démarré.", "The server did not start."));
        });

        public string LogTail(int lines = 12)
        {
            try { return string.Join("\n", ReadShared(Log).Split('\n').Reverse().Take(lines).Reverse()); }
            catch (IOException) { return Log; }
        }

        static string ReadShared(string path)
        {
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            using var reader = new StreamReader(stream, Encoding.UTF8);
            return reader.ReadToEnd();
        }

        static bool Listening(int port)
        {
            try
            {
                using var client = new TcpClient();
                return client.ConnectAsync("127.0.0.1", port).Wait(500) && client.Connected;
            }
            catch (Exception) { return false; }
        }

        public void Dispose()
        {
            stopping = true;
            try { if (process != null && !process.HasExited) process.Kill(); } catch (Exception) { }
            Job.Close(job);
        }
    }

    static class Job
    {
        [StructLayout(LayoutKind.Sequential)]
        struct BasicLimits
        {
            public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass, SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct IoCounters { public ulong a, b, c, d, e, f; }

        [StructLayout(LayoutKind.Sequential)]
        struct ExtendedLimits
        {
            public BasicLimits Basic;
            public IoCounters Io;
            public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll")] static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref ExtendedLimits info, int length);
        [DllImport("kernel32.dll")] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);

        public static IntPtr Create()
        {
            IntPtr job = CreateJobObject(IntPtr.Zero, null);
            var limits = new ExtendedLimits();
            // KILL_ON_JOB_CLOSE : le serveur s'arrête avec la fenêtre ; BREAKAWAY_OK : sauf ce qui est lancé à part
            // exprès (invite de commandes de connexion : « ollama serve », « npm install » continuent)
            limits.Basic.LimitFlags = 0x2000 | 0x800;
            SetInformationJobObject(job, 9, ref limits, Marshal.SizeOf(typeof(ExtendedLimits)));  // JobObjectExtendedLimitInformation
            return job;
        }

        public static void Assign(IntPtr job, Process process)
        {
            if (job != IntPtr.Zero) AssignProcessToJobObject(job, process.Handle);
        }

        public static void Close(IntPtr job)
        {
            if (job != IntPtr.Zero) CloseHandle(job);
        }
    }
}
