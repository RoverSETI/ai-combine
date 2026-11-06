# -*- coding: utf-8 -*-
"""Инкрементальная синхронизация заметок Obsidian vault с базой Знаний Open WebUI.

Использует штатные эндпоинты 0.12.5 (не загружает дубли):
  POST /api/v1/knowledge/{id}/sync/diff      — манифест {filename, path, checksum, size}
  POST /api/v1/knowledge/{id}/files/batch/add — пакетная привязка новых файлов
  POST /api/v1/knowledge/{id}/sync/cleanup   — удаление лишних/устаревших записей
  POST /api/v1/knowledge/{id}/dirs/create    — директории (структурированный режим)

Режимы пути (выбирается автоматически по diff, какой манифест согласован с базой):
  flat        — все файлы в корне базы (path=""), так база загружена изначально;
  structured  — зеркалит папки vault («Arch_Linux», «ИИ», «Obsidian/Plugins», ...).

Безопасность:
- ЕДИНСТВЕННЫЙ разрешённый хост: 127.0.0.1:8080 (константа, не из аргументов/среды).
- Перед каждым запросом проверяется точное совпадение URL с константой-префиксом.
- Редиректы отключены: ответ с редиректом трактуется как ошибка.
"""
import argparse
import glob
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# --- Жёстко зафиксированные параметры (не берутся из env/argv) ---
BASE = "http://127.0.0.1:8080"
ALLOWED_PATHS = (
    "/api/v1/knowledge/",
    "/api/v1/files/",
)
KB_ID = "dfe4a423-90f1-4db7-909c-206d599b8f58"
VAULT = "/run/media/rover/BACK/GoogleDrive/Obsidian"
TOKEN_PATH = "/tmp/webui_jwt.txt"  # только чтение


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # любой редирект = ошибка, а не переход


_opener = urllib.request.build_opener(NoRedirect)


def _check(url: str) -> None:
    """Проверить, что URL точно разрешённый loopback-адрес и путь в белом списке."""
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme, parsed.hostname, parsed.port) != ("http", "127.0.0.1", 8080):
        raise ValueError(f"Запрещённый адрес: {url}")
    if not any(parsed.path.startswith(p) for p in ALLOWED_PATHS):
        raise ValueError(f"Путь вне белого списка: {parsed.path}")


def api(method: str, path: str, data=None, files=None):
    url = BASE + path
    _check(url)
    headers = {"Authorization": "Bearer " + open(TOKEN_PATH).read().strip()}
    req = urllib.request.Request(url, data=b"", method=method)

    if files:
        boundary = "----zkb-knowledge"
        body = b""
        for name, filename, content, ctype in files:
            body += (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n"
            ).encode() + content + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req.data = body
        headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
    elif data is not None:
        req.data = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"

    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with _opener.open(req, timeout=300) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        raise RuntimeError(f"HTTP {e.code} {path}: {detail}") from e


def vault_manifest():
    """Манифест vault: {filename, path, checksum (sha256 сырых байтов), size}."""
    out = []
    for p in sorted(glob.glob(VAULT + "/**/*.md", recursive=True)):
        if "/.obsidian/" in p:
            continue
        rel = os.path.relpath(p, VAULT).replace(os.sep, "/")
        with open(p, "rb") as fh:
            content = fh.read()
        if not content.strip():
            continue  # пустые не индексируем
        out.append(
            {
                "filename": os.path.basename(p),
                "path": os.path.dirname(rel),  # "" для корня vault
                "checksum": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )
    return out


def vault_by_basename(manifest):
    """Карта basename → реальный путь в vault (для flat-режима: файл может
    лежать в подпапке vault, а ключ манифеста — только basename)."""
    by_name = {}
    for p in glob.glob(VAULT + "/**/*.md", recursive=True):
        if "/.obsidian/" not in p:
            by_name.setdefault(os.path.basename(p), p)
    return by_name


def diff(manifest):
    return api("POST", f"/api/v1/knowledge/{KB_ID}/sync/diff", data={"manifest": manifest})


def upload_file(path):
    """Загрузить локальный файл, вернуть его file_id."""
    with open(path, "rb") as fh:
        content = fh.read()
    resp = api(
        "POST",
        "/api/v1/files/?process=true&process_in_background=false",
        files=[("file", os.path.basename(path), content, "text/markdown")],
    )
    fid = resp.get("id")
    if not fid:
        raise RuntimeError("загрузка не вернула id: " + str(resp)[:150])
    return fid


def batch_add(file_ids, directory_id=None):
    forms = [{"file_id": fid, "directory_id": directory_id} for fid in file_ids]
    return api("POST", f"/api/v1/knowledge/{KB_ID}/files/batch/add", data=forms)


def make_dirs(paths, directory_map):
    """Создать недостающие директории (по возрастанию глубины), дополнить map."""
    for p in sorted(paths, key=lambda x: x.count("/")):
        parent = os.path.dirname(p)
        parent_id = directory_map.get(parent)
        resp = api(
            "POST",
            f"/api/v1/knowledge/{KB_ID}/dirs/create",
            data={"name": os.path.basename(p), "parent_id": parent_id},
        )
        directory_map[p] = resp.get("id")
        print(f"  директория создана: {p}")


def cleanup(file_ids, dir_ids):
    return api(
        "POST",
        f"/api/v1/knowledge/{KB_ID}/sync/cleanup",
        data={"file_ids": file_ids, "dir_ids": dir_ids},
    )


def pick_mode(manifest):
    """Выбрать режим пути: где манифест лучше согласован с базой.

    Сравнивает два варианта манифеста (flat и structured) через sync/diff
    и возвращает тот, где больше unmodified при том же размере манифеста.
    """
    flat = [{**e, "path": ""} for e in manifest]
    r_flat = diff(flat)
    if r_flat["unmodified_count"] == len(manifest):
        return "flat", r_flat
    r_struct = diff(manifest)
    if r_struct["unmodified_count"] > r_flat["unmodified_count"]:
        return "structured", r_struct
    return "flat", r_flat


def main():
    ap = argparse.ArgumentParser(description="Инкрементальная синхронизация vault → Знания")
    ap.add_argument("--dry-run", action="store_true", help="только показать diff, ничего не менять")
    ap.add_argument("--mode", choices=["auto", "flat", "structured"], default="auto",
                    help="режим путей: auto = по согласованности с базой (по умолчанию)")
    ap.add_argument("--keep-remote", action="store_true",
                    help="не удалять из базы файлы, отсутствующие в vault (cleanup отключён)")
    args = ap.parse_args()

    manifest = vault_manifest()
    print(f"vault: {len(manifest)} непустых .md")

    if args.mode == "auto":
        mode, d = pick_mode(manifest)
        print(f"режим (auto): {mode}")
    else:
        mode = args.mode
        m = manifest if mode == "structured" else [{**e, "path": ""} for e in manifest]
        d = diff(m)

    print(f"diff: added={len(d['added'])} modified={len(d['modified'])} "
          f"deleted={len(d['deleted'])} unmodified={d['unmodified_count']}")
    if mode == "structured":
        print(f"директории: mkdir={d['mkdir']} rmdir={len(d['rmdir'])}")

    if args.dry_run:
        for a in d["added"]:
            print("  NEW   :", (a["path"] + "/" if a["path"] else "") + a["filename"])
        for m in d["modified"]:
            print("  CHANGED:", (m["path"] + "/" if m["path"] else "") + m["filename"])
        for x in d["deleted"]:
            print("  GONE  :", x["filename"])
        print("dry-run: изменений не вносилось")
        return

    directory_map = d.get("directory_map", {})
    if mode == "structured" and d["mkdir"]:
        make_dirs(d["mkdir"], directory_map)

    ok = failed = 0
    by_name = vault_by_basename(manifest)
    # --- added: загрузить и привязать пакетом ---
    if d["added"]:
        print(f"добавление {len(d['added'])} новых...")
        new_ids = []
        new_meta = {}
        for a in d["added"]:
            rel = (a["path"] + "/" if a["path"] else "") + a["filename"]
            # в flat-режиме path пуст, но файл может лежать в подпапке vault
            path = by_name.get(a["filename"]) if mode == "flat" else (
                os.path.join(VAULT, a["path"], a["filename"]) if a["path"] else os.path.join(VAULT, a["filename"])
            )
            path = path or os.path.join(VAULT, a["filename"])
            try:
                fid = upload_file(path)
                new_ids.append(fid)
                new_meta[fid] = (a["path"], rel)
                ok += 1
                print(f"  загружен: {rel}")
            except Exception as e:
                failed += 1
                print(f"  FAIL {rel}: {e}")
        if new_ids:
            if mode == "structured":
                for fdir, fids in group_by_dir(new_meta):
                    batch_add(fids, directory_map.get(fdir))
            else:
                batch_add(new_ids)
            print(f"  привязано пакетом: {len(new_ids)}")

    # --- modified: загрузить новую версию, привязать, старую утилизировать в cleanup ---
    stale_ids = []
    if d["modified"]:
        print(f"обновление {len(d['modified'])} изменённых...")
        for m in d["modified"]:
            rel = (m["path"] + "/" if m["path"] else "") + m["filename"]
            path = by_name.get(m["filename"]) if mode == "flat" else (
                os.path.join(VAULT, m["path"], m["filename"]) if m["path"] else os.path.join(VAULT, m["filename"])
            )
            path = path or os.path.join(VAULT, m["filename"])
            try:
                fid = upload_file(path)
                batch_add([fid], directory_map.get(m["path"]) if mode == "structured" else None)
                if m.get("stale_file_id"):
                    stale_ids.append(m["stale_file_id"])
                ok += 1
                print(f"  обновлён: {rel}")
            except Exception as e:
                failed += 1
                print(f"  FAIL {rel}: {e}")

    # --- cleanup: удалённые из vault + устаревшие версии modified ---
    doomed = list({x["file_id"] for x in d["deleted"]} | set(stale_ids))
    if doomed and not args.keep_remote:
        print(f"очистка устаревших: {len(doomed)} файлов, dir_ids={len(d['rmdir'])}...")
        try:
            cleanup(doomed, d["rmdir"] if mode == "structured" else [])
            print("  cleanup выполнен")
        except Exception as e:
            failed += len(doomed)
            print(f"  FAIL cleanup: {e}")
    elif doomed and args.keep_remote:
        print(f"  --keep-remote: cleanup пропущен ({len(doomed)} файлов оставлены в базе)")

    print(f"\nИТОГ: ok={ok}, failed={failed}, без изменений={d['unmodified_count']}")
    if failed:
        sys.exit(1)


def group_by_dir(new_meta):
    """Сгруппировать (directory_path, [file_id]) по путям."""
    by_dir = {}
    for fid, (fdir, _rel) in new_meta.items():
        by_dir.setdefault(fdir, []).append(fid)
    return sorted(by_dir.items())


if __name__ == "__main__":
    main()
