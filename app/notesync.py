"""Notes d'un article, de l'export Markdown à l'import : le fichier exporté se termine par un bloc de données invisible
à la lecture (commentaire HTML) qui contient toutes ses annotations telles quelles (cible, couleur, état, dates,
identifiant stable). Ce bloc est signé (Ed25519) par la clé de l'installation qui l'a exporté : à l'import, un fichier
non signé, modifié ou d'une autre origine est refusé ; celui d'un autre ordinateur ne passe qu'une fois cet appareil
accepté (« faire confiance »). Une annotation déjà présente (même identifiant) n'est remplacée que par une version plus
récente ; énoncé, preuve et fiche de lecture (un seul par article) sont rapprochés par leur cible.
"""

import base64
import hashlib
import io
import json
import platform
import re
import zipfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import annotations, db, library

FORMAT = 1
MARK = "arxivreader-data"
DATA = re.compile(r"<!-- " + MARK + r"\s*(\{.*?\})\s*-->", re.S)
MAX_FILES = 5000
MAX_TEXT = 20_000_000
FIELDS = ("uid", "kind", "target", "color", "quote", "note", "state", "author", "created_at", "updated_at")


# ---------- Clé de l'installation, appareils acceptés ----------

def _key_path():
    return db.LIBRARY / "notes-signing.key"


def _trusted_path():
    return db.LIBRARY / "notes-trusted.json"


def _private_key():
    path = _key_path()
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    path.chmod(0o600)
    return key


def _raw(public_key):
    return public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def fingerprint(raw):
    """Empreinte lisible d'une clé publique (« 3F2A-9C1B-… »), pour la reconnaître d'un appareil à l'autre."""
    digest = hashlib.sha256(raw).hexdigest()[:16].upper()
    return "-".join(digest[i:i + 4] for i in range(0, 16, 4))


def own_fingerprint():
    return fingerprint(_raw(_private_key().public_key()))


def trusted():
    """Appareils acceptés : {empreinte: nom}."""
    try:
        return json.loads(_trusted_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def trust(fingerprints_and_names):
    known = trusted()
    known.update(fingerprints_and_names)
    library.write_atomic(_trusted_path(), json.dumps(known, ensure_ascii=False, indent=1))


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ---------- Export : bloc de données signé, en fin de fichier ----------

def _signed(payload):
    """Bloc de données en commentaire HTML (invisible à la lecture), signé par la clé de cette installation."""
    payload = {"format": FORMAT, **payload,
               "signer": {"key": base64.b64encode(_raw(_private_key().public_key())).decode(),
                          "device": re.sub(r"\.(local|lan|home)$", "", platform.node()) or "arXivReader"}}
    payload["signature"] = base64.b64encode(_private_key().sign(_canonical(payload))).decode()
    # « > » échappé : le bloc ne peut pas fermer le commentaire HTML avant sa fin (texte d'une note)
    text = json.dumps(payload, ensure_ascii=False).replace(">", "\\u003e")
    return f"<!-- {MARK}\n{text}\n-->"


PAPER_FIELDS = ("id", "arxiv_id", "version", "title", "authors", "source", "doi")


def data_block(paper, notes):
    """Notes d'un article (fin de son fichier Markdown)."""
    return _signed({"paper": {k: paper.get(k) for k in PAPER_FIELDS},
                    "annotations": [{k: note.get(k) for k in FIELDS} for note in notes]})


def library_block(papers, folder_list):
    """Toute la bibliothèque, annotée ou non, et ses dossiers (fichier « arXivReader library.md » de l'archive)."""
    return _signed({"library": [{k: paper.get(k) for k in PAPER_FIELDS} for paper in papers],
                    "folders": [{"name": f["name"], "papers": f["papers"]} for f in folder_list]})


# ---------- Import ----------

class ImportError_(ValueError):
    pass


def _texts(files):
    """(nom, texte) de chaque fichier Markdown : envoyés un par un, ou dans une archive .zip (l'export complet)."""
    count = total = 0
    for item in files:
        data = item["data"]
        if data[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for info in archive.infolist():
                    if info.is_dir() or not info.filename.lower().endswith(".md"):
                        continue
                    count += 1
                    total += info.file_size
                    if count > MAX_FILES or total > MAX_TEXT:
                        raise ImportError_("too large")
                    yield info.filename, archive.read(info).decode("utf-8", errors="replace")
        else:
            count += 1
            total += len(data)
            if count > MAX_FILES or total > MAX_TEXT:
                raise ImportError_("too large")
            yield item["name"], data.decode("utf-8", errors="replace")


def _verify(text):
    """Bloc de données vérifié : (payload, empreinte du signataire, appareil) ; ImportError_ sinon."""
    found = DATA.search(text)
    if not found:
        raise ImportError_("unsigned")
    try:
        payload = json.loads(found.group(1))
        signature = base64.b64decode(payload.pop("signature"))
        raw = base64.b64decode(payload["signer"]["key"])
        Ed25519PublicKey.from_public_bytes(raw).verify(signature, _canonical(payload))
    except (ValueError, KeyError, TypeError, InvalidSignature):
        raise ImportError_("invalid")
    if payload.get("format") != FORMAT:
        raise ImportError_("format")
    return payload, fingerprint(raw), str(payload["signer"].get("device") or "")[:100]


def import_files(files, accept=()):
    """Importe des notes exportées. `accept` : empreintes d'appareils que l'utilisateur vient d'accepter. Renvoie le
    bilan ; `untrusted` y liste les appareils inconnus (rien n'est importé de leurs fichiers tant qu'ils ne sont pas
    acceptés)."""
    own = own_fingerprint()
    known = trusted()
    report = {"papers": 0, "folders": 0, "added": 0, "updated": 0, "unchanged": 0, "missing": [], "rejected": [], "untrusted": {}}
    verified = []
    for name, text in _texts(files):
        try:
            payload, signer, device = _verify(text)
        except ImportError_ as error:
            report["rejected"].append({"file": name, "reason": str(error)})
            continue
        if signer != own and signer not in known:
            if signer in accept:
                trust({signer: device})
                known[signer] = device
            else:
                report["untrusted"][signer] = device
                continue
        verified.append(payload)
    if report["untrusted"]:
        return report  # la page demande d'abord : rien n'est importé à moitié
    # bibliothèque (articles, même non annotés) d'abord, puis les notes, puis les dossiers (articles alors présents)
    verified.sort(key=lambda payload: 0 if "library" in payload else 1)
    for payload in verified:
        if "library" in payload:
            for paper in payload.get("library") or []:
                _ensure_paper(paper, report)
        else:
            _import_paper(payload, report)
    for payload in verified:
        for folder in payload.get("folders") or []:
            _import_folder(folder, report)
    return report


def _ensure_paper(paper, report):
    """Article de la bibliothèque exportée : présent ici (remis en bibliothèque au besoin), sinon importé d'arXiv ;
    PDF importé absent : signalé (il faut le fichier). Renvoie son identifiant ici, ou None."""
    paper_id = paper.get("id") if isinstance(paper, dict) else None
    if not isinstance(paper_id, str) or not paper_id:
        return None
    here = db.get_paper(paper_id)
    if here:
        if not here.get("in_library"):
            library.set_in_library(paper_id, True)
        return paper_id
    if not paper.get("arxiv_id"):  # PDF importé : il faut le fichier, qui n'est pas dans les notes
        if paper.get("title") not in report["missing"]:
            report["missing"].append(paper.get("title") or paper_id)
        return None
    known = {"title": str(paper.get("title") or "")[:1000], "authors": [str(a)[:200] for a in (paper.get("authors") or [])[:500]]}
    added = library.quick_add(str(paper["arxiv_id"]), known, True)
    report["papers"] += 1
    return added["id"]


def _import_folder(folder, report):
    """Dossier exporté : celui du même nom ici, sinon créé ; rempli des articles présents."""
    from . import folders
    try:
        name = folders.clean_name(folder.get("name"))
    except (folders.FolderError, AttributeError):
        return
    existing = next((f for f in folders.list_folders() if f["name"].casefold() == name.casefold()), None)
    if not existing:
        existing = folders.create(name)
        report["folders"] += 1
    for paper_id in folder.get("papers") or []:
        if isinstance(paper_id, str) and db.get_paper(paper_id) and paper_id not in existing["papers"]:
            folders.set_member(existing["id"], paper_id, True)


def _import_paper(payload, report):
    paper_id = _ensure_paper(payload["paper"], report)
    if not paper_id:
        return
    for item in payload.get("annotations") or []:
        outcome = annotations.import_one(paper_id, item)
        report[outcome] += 1
