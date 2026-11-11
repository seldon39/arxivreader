"""Filet de sécurité de la bibliothèque : version du schéma, contrôle d'intégrité au démarrage, copie quotidienne.

Les notes, signets et échanges avec l'assistant sont le seul travail qu'on ne peut pas retélécharger : une base
abîmée ou lue par un code trop ancien ne doit rien perdre en silence. Module autonome (sqlite3 seulement) : les chemins
viennent de l'appelant, les tests les redirigent."""

import json
import logging
import os
import shutil
import sqlite3
import time
from pathlib import Path

log = logging.getLogger("arxivreader.safety")

# Numéro du schéma (PRAGMA user_version). À augmenter quand une version change la base d'une façon que le code
# précédent ne saurait pas lire ; une base plus récente que ce numéro est refusée, jamais « rétrogradée ».
DB_VERSION = 1
KEEP = 7  # copies gardées (une par jour)
PREFIX, SUFFIX = "arxivreader-", ".db"
RECOVERY = "recovery.json"


class NewerLibrary(RuntimeError):
    """Bibliothèque écrite par une version plus récente d'arXivReader."""


def backups_dir(db_path):
    return Path(db_path).parent / "backups"


def _snapshots(db_path):
    """Copies existantes, de la plus récente à la plus ancienne (la date est dans le nom)."""
    folder = backups_dir(db_path)
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.glob(f"{PREFIX}????-??-??{SUFFIX}") if p.is_file()), reverse=True)


BUSY_TIMEOUT = 30  # secondes d'attente d'une base occupée par un autre processus
CORRUPT = ("malformed", "not a database", "corrupt")  # messages de SQLite pour une base réellement abîmée


def _check(path):
    """(version du schéma, base saine ?). Ouverture ordinaire, en écriture : un journal resté après un arrêt brutal
    est alors rejoué par SQLite (en lecture seule il resterait là, et une base saine semblerait abîmée). Une base
    occupée, verrouillée ou illisible pour une autre raison (droits, disque) lève l'erreur : seule une corruption
    avérée justifie de mettre le fichier de côté."""
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        ok = conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    except sqlite3.OperationalError as error:
        if not any(word in str(error).lower() for word in CORRUPT):
            raise
        return 0, False
    except sqlite3.DatabaseError:
        return 0, False
    finally:
        conn.close()
    return version, ok


def _refuse_newer(version):
    if version > DB_VERSION:
        raise NewerLibrary(
            f"Cette bibliothèque a été écrite par une version plus récente d'arXivReader (schéma {version}, celle-ci "
            f"comprend jusqu'au {DB_VERSION}). Mettez l'application à jour : rien n'a été modifié. / This library was "
            f"written by a newer arXivReader (schema {version}, this one understands up to {DB_VERSION}). Update the "
            "app: nothing was changed.")


def prepare(db_path):
    """Avant toute ouverture : refuse une base plus récente ; une base abîmée est mise de côté et remplacée par la
    dernière copie saine (sinon une base neuve). Renvoie le compte rendu de la récupération, ou None."""
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    version, ok = _check(db_path)
    if ok:
        _refuse_newer(version)
        return None
    folder = backups_dir(db_path)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    aside = folder / f"corrupt-{stamp}{SUFFIX}"
    shutil.move(db_path, aside)  # jamais effacée : l'utilisateur peut encore tenter de la réparer
    for extra in ("-wal", "-shm", "-journal"):  # jamais un journal de l'ancienne base à côté de la copie remise
        side = Path(str(db_path) + extra)
        if side.exists():
            shutil.move(side, folder / f"corrupt-{stamp}{SUFFIX}{extra}")
    restored = None
    for snapshot in _snapshots(db_path):
        try:
            version, ok = _check(snapshot)
        except sqlite3.Error:
            continue  # copie illisible : la suivante
        if ok and version <= DB_VERSION:
            temp = db_path.with_name(db_path.name + ".restoring")
            shutil.copyfile(snapshot, temp)
            os.replace(temp, db_path)  # atomique : jamais une base à moitié copiée
            restored = snapshot.name
            break
    report = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "aside": aside.name, "restored": restored}
    (folder / RECOVERY).write_text(json.dumps(report), encoding="utf-8")
    log.warning("base abîmée mise de côté (%s) ; copie remise en place : %s", aside.name, restored or "aucune")
    return report


def snapshot(db_path, today=None, keep=KEEP):
    """Au plus une copie par jour (API de sauvegarde en ligne de SQLite : sûre pendant que l'application écrit),
    sans le cache des vecteurs (rechargeable, 85 % du volume). Écrite à part puis renommée. Renvoie le chemin, ou None
    si la copie du jour existe déjà."""
    db_path = Path(db_path)
    folder = backups_dir(db_path)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    dest = folder / f"{PREFIX}{today or time.strftime('%Y-%m-%d')}{SUFFIX}"
    for stale in folder.glob("*.tmp"):  # copie interrompue par une fermeture de l'application
        stale.unlink(missing_ok=True)
    if dest.exists():
        return None
    temp = dest.with_name(dest.name + ".tmp")
    source = sqlite3.connect(db_path)
    try:
        target = sqlite3.connect(temp)
        try:
            source.backup(target)
            target.execute("DROP TABLE IF EXISTS embeddings")
            target.commit()
            target.execute("VACUUM")
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("copie défectueuse")
        finally:
            target.close()
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    finally:
        source.close()
    os.chmod(temp, 0o600)  # peut contenir une clé d'API si le trousseau était indisponible
    os.replace(temp, dest)
    for old in _snapshots(db_path)[keep:]:
        old.unlink(missing_ok=True)
    return dest


def status(db_path):
    """Pour les réglages : dossier, dernière copie, nombre de copies, dernière récupération."""
    snapshots = _snapshots(db_path)
    folder = backups_dir(db_path)
    try:
        recovery = json.loads((folder / RECOVERY).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        recovery = None
    last = snapshots[0].name[len(PREFIX):-len(SUFFIX)] if snapshots else None
    return {"path": str(folder), "last": last, "count": len(snapshots), "recovery": recovery}
