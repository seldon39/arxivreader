"""Clés d'API de l'utilisateur (Gemini, Mistral, Semantic Scholar, Zotero) dans le trousseau du système : trousseau de session
de macOS (outil `security`), gestionnaire d'identifiants de Windows. Ailleurs, ou sans ARXIVREADER_KEYCHAIN=1 (essais,
tests), elles restent dans la base comme les autres réglages.

Une entrée par clé et par bibliothèque (son chemin, résumé) : une copie de la bibliothèque pour un essai ne lit ni ne
remplace jamais les clés de la vraie. Le secret ne passe jamais en argument d'un programme (visible des autres
utilisateurs dans la liste des processus) : sur macOS, la commande d'écriture est lue sur l'entrée standard.
"""

import ctypes
import hashlib
import os
import subprocess
import sys

from .config import LIBRARY

SECRET_KEYS = ("geminiApiKey", "mistralApiKey", "s2ApiKey", "zoteroApiKey")
SERVICE = "arXivReader"
ENABLED = os.environ.get("ARXIVREADER_KEYCHAIN") == "1" and sys.platform in ("darwin", "win32")


class KeystoreError(Exception):
    pass


def _account(key):
    library = hashlib.sha256(str(LIBRARY.resolve()).encode()).hexdigest()[:12]
    return f"{key}:{library}"


# ---------- macOS : trousseau de session, par l'outil security ----------

def _mac_get(key):
    result = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", _account(key), "-w"],
                            capture_output=True, text=True, timeout=20)
    if result.returncode == 44:  # pas d'entrée
        return None
    if result.returncode != 0:
        raise KeystoreError(result.stderr.strip())
    return result.stdout.rstrip("\n")


def _mac_set(key, value):
    command = f"add-generic-password -U -s {SERVICE} -a {_account(key)} -X {value.encode().hex()}\n"
    result = subprocess.run(["security", "-i"], input=command, capture_output=True, text=True, timeout=20)
    if result.returncode != 0 or "error" in result.stderr.lower():
        raise KeystoreError(result.stderr.strip())


def _mac_delete(key):
    result = subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", _account(key)],
                            capture_output=True, text=True, timeout=20)
    if result.returncode not in (0, 44):
        raise KeystoreError(result.stderr.strip())


# ---------- Windows : gestionnaire d'identifiants ----------

class _Credential(ctypes.Structure):
    _fields_ = [("Flags", ctypes.c_uint32), ("Type", ctypes.c_uint32), ("TargetName", ctypes.c_wchar_p),
                ("Comment", ctypes.c_wchar_p), ("LastWritten", ctypes.c_uint64), ("CredentialBlobSize", ctypes.c_uint32),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_char)), ("Persist", ctypes.c_uint32),
                ("AttributeCount", ctypes.c_uint32), ("Attributes", ctypes.c_void_p), ("TargetAlias", ctypes.c_wchar_p),
                ("UserName", ctypes.c_wchar_p)]


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2  # cet ordinateur, cet utilisateur (pas d'itinérance)
ERROR_NOT_FOUND = 1168


def _advapi():
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.CredReadW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.POINTER(_Credential))]
    advapi.CredWriteW.argtypes = [ctypes.POINTER(_Credential), ctypes.c_uint32]
    advapi.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    return advapi


def _target(key):
    return f"{SERVICE}:{_account(key)}"


def _win_get(key):
    advapi = _advapi()
    found = ctypes.POINTER(_Credential)()
    if not advapi.CredReadW(_target(key), CRED_TYPE_GENERIC, 0, ctypes.byref(found)):
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            return None
        raise KeystoreError(f"CredReadW: {error}")
    try:
        return ctypes.string_at(found.contents.CredentialBlob, found.contents.CredentialBlobSize).decode()
    finally:
        advapi.CredFree(found)


def _win_set(key, value):
    blob = value.encode()
    buffer = ctypes.create_string_buffer(blob, len(blob))
    credential = _Credential(Type=CRED_TYPE_GENERIC, TargetName=_target(key), CredentialBlobSize=len(blob),
                             CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)),
                             Persist=CRED_PERSIST_LOCAL_MACHINE, UserName=SERVICE)
    if not _advapi().CredWriteW(ctypes.byref(credential), 0):
        raise KeystoreError(f"CredWriteW: {ctypes.get_last_error()}")


def _win_delete(key):
    advapi = _advapi()
    if not advapi.CredDeleteW(_target(key), CRED_TYPE_GENERIC, 0) and ctypes.get_last_error() != ERROR_NOT_FOUND:
        raise KeystoreError(f"CredDeleteW: {ctypes.get_last_error()}")


# ---------- Accès ----------

def get(key):
    """Clé enregistrée, sinon None."""
    try:
        return _win_get(key) if sys.platform == "win32" else _mac_get(key)
    except (OSError, subprocess.SubprocessError) as error:
        raise KeystoreError(str(error))


def put(key, value):
    """Enregistre la clé ; vide : la retire."""
    try:
        if not value:
            (_win_delete if sys.platform == "win32" else _mac_delete)(key)
        elif sys.platform == "win32":
            _win_set(key, value)
        else:
            _mac_set(key, value)
            if _mac_get(key) != value:  # security -i ne signale pas toujours l'échec d'une commande
                raise KeystoreError("not saved in the keychain")
    except (OSError, subprocess.SubprocessError) as error:
        raise KeystoreError(str(error))
