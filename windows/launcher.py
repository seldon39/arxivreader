"""Application Windows (préversion) : le serveur local dans ce processus, l'interface dans une fenêtre
Microsoft Edge avec ses onglets, et un profil propre à arXivReader (ni les extensions ni les onglets de
l'utilisateur).

Comme l'application Mac : bibliothèque dans %LOCALAPPDATA%\\arXivReader (ARXIVREADER_LIBRARY pour essayer sur
une copie), port 8720 s'il est libre, serveur arrêté quand la dernière fenêtre se ferme. Un second lancement
ouvre seulement un nouvel onglet sur le serveur déjà démarré.

Essais : ARXIVREADER_NO_BROWSER=1 démarre le serveur seul (arrêt : Ctrl+C ou fin du processus) ;
ARXIVREADER_START=#/feed ouvre cette page.
"""

import ctypes
import hashlib
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

TITLE = "arXivReader"
PREFERRED_PORT = 8720


def library_folder():
    if os.environ.get("ARXIVREADER_LIBRARY"):
        return Path(os.environ["ARXIVREADER_LIBRARY"])
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "arXivReader"


def message(text, error=False):
    """Boîte de dialogue Windows (bloquante)."""
    flags = 0x10 if error else 0x40  # MB_ICONERROR, MB_ICONINFORMATION
    try:
        ctypes.windll.user32.MessageBoxW(None, text, TITLE, flags)
    except (AttributeError, OSError):
        print(text, file=sys.stderr)


def french():
    try:
        return ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0xFF == 0x0C  # LANG_FRENCH
    except (AttributeError, OSError):
        return False


def tr(fr, en):
    return fr if french() else en


def free_port(preferred):
    """Port préféré s'il est libre (même adresse d'un lancement à l'autre), sinon un port libre quelconque."""
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return probe.getsockname()[1]
            except OSError:
                continue
    raise OSError("no free port")


def listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def single_instance(library):
    """Vrai si aucun autre arXivReader ne tourne déjà sur cette bibliothèque (verrou nommé de Windows)."""
    name = "Local\\arXivReader-" + hashlib.sha1(str(library.resolve()).lower().encode()).hexdigest()[:16]
    try:
        kernel32 = ctypes.windll.kernel32
    except AttributeError:
        return True
    single_instance.handle = kernel32.CreateMutexW(None, False, name)  # gardé jusqu'à la fin du processus
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def find_browser():
    """Microsoft Edge (présent sur tout Windows 10 et 11), sinon Chrome."""
    roots = [os.environ.get(v) for v in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    names = [Path("Microsoft/Edge/Application/msedge.exe"), Path("Google/Chrome/Application/chrome.exe")]
    for name in names:
        for root in filter(None, roots):
            if (Path(root) / name).exists():
                return str(Path(root) / name)
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for exe in ("msedge.exe", "chrome.exe"):
                try:
                    with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as key:
                        path = winreg.QueryValue(key, None)
                    if path and Path(path).exists():
                        return path
                except OSError:
                    continue
    except ImportError:
        pass
    return None


def open_window(browser, library, url, new_window=True):
    """Fenêtre Edge (avec onglets) sur le profil d'arXivReader ; déjà ouverte : un onglet de plus."""
    profile = library / "edge-profile"  # séparé du profil de l'utilisateur : ni ses extensions, ni ses onglets
    first = not profile.exists()
    arguments = [browser, f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                 "--disable-background-mode"]  # sinon le navigateur resterait lancé une fois la fenêtre fermée
    if first:
        arguments.append("--window-size=1280,860")  # ensuite, le navigateur garde la taille choisie
    return subprocess.Popen([*arguments, *(["--new-window"] if new_window else []), url])


def browser_running(library):
    """Un processus du navigateur utilise-t-il encore le profil d'arXivReader ? (cas rare : la fenêtre a été
    confiée à un navigateur déjà lancé sur ce profil, et le processus lancé s'est terminé aussitôt)"""
    profile = str(library / "edge-profile").replace("'", "''")
    script = ("@(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*" + profile + "*' }).Count")
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                                text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        return int(result.stdout.strip() or 0) > 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def log_to(library):
    """Sorties du serveur dans server.log (le précédent gardé dans server.log.1) : sans console, rien ne
    s'afficherait, et les écritures échoueraient."""
    log = library / "server.log"
    if log.exists():
        os.replace(log, library / "server.log.1")
    stream = open(log, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = stream
    return log


def tail(log, lines=12):
    try:
        return "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return str(log)


def main():
    library = library_folder()
    library.mkdir(parents=True, exist_ok=True)
    port_file = library / "server.port"
    route = os.environ.get("ARXIVREADER_START", "")
    headless = os.environ.get("ARXIVREADER_NO_BROWSER") == "1"
    browser = None if headless else find_browser()

    if not single_instance(library):  # déjà lancé : une fenêtre de plus, sur le même serveur
        try:
            port = int(port_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            port = PREFERRED_PORT
        if browser and listening(port):
            open_window(browser, library, f"http://127.0.0.1:{port}/{route}", new_window=False)
        return

    port = free_port(PREFERRED_PORT)
    os.environ["ARXIVREADER_PORT"] = str(port)  # lus par app.config à l'import
    os.environ["ARXIVREADER_LIBRARY"] = str(library)
    log = log_to(library)

    try:
        import uvicorn
        from app.main import app
    except Exception as error:  # paquet incomplet : message plutôt qu'une fermeture silencieuse
        message(tr("arXivReader n'a pas pu démarrer :\n", "arXivReader could not start:\n") + repr(error), error=True)
        raise
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False))
    url = f"http://127.0.0.1:{port}/{route}"

    def run_browser():
        start = time.monotonic()
        while not server.started:
            if time.monotonic() - start > 90 or server.should_exit:
                server.should_exit = True  # message affiché par le fil principal
                return
            time.sleep(0.1)
        port_file.write_text(str(port), encoding="utf-8")
        if headless:
            return
        if not browser:  # ni Edge ni Chrome : le navigateur par défaut, et une boîte pour quitter
            webbrowser.open(url)
            message(tr("Microsoft Edge est introuvable : arXivReader s'est ouvert dans votre navigateur.\n\n"
                       "Cliquez sur OK pour quitter arXivReader.",
                       "Microsoft Edge was not found: arXivReader opened in your browser.\n\nClick OK to quit arXivReader."))
            server.should_exit = True
            return
        opened = time.monotonic()
        open_window(browser, library, url).wait()
        if time.monotonic() - opened < 5:  # fenêtre confiée à un navigateur déjà lancé sur ce profil
            while browser_running(library):
                time.sleep(5)
        server.should_exit = True

    threading.Thread(target=run_browser, daemon=True).start()
    try:
        server.run()  # dans le fil principal : uvicorn y gère Ctrl+C
    except SystemExit:  # port pris entre-temps, erreur au démarrage : uvicorn s'arrête par sys.exit
        pass
    finally:
        try:
            port_file.unlink()
        except OSError:
            pass
    if not server.started and not headless:
        message(tr("Le serveur d'arXivReader n'a pas démarré. Dernières lignes du journal :\n\n",
                   "arXivReader's server did not start. Last lines of the log:\n\n") + tail(log), error=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
