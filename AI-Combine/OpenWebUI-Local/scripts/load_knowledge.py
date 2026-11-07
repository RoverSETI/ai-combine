# -*- coding: utf-8 -*-
"""Загрузка заметок Obsidian vault в Знание Open WebUI (loopback-only).

Безопасность:
- ЕДИНСТВЕННЫЙ разрешённый хост: 127.0.0.1:8080 (константа, не из аргументов/среды).
- Перед каждым запросом проверяется точное совпадение URL с константой-префиксом.
- Редиректы отключены (urllib по умолчанию следует за 30x — блокируем кастомным
  обработчиком, чтобы не было исхода за пределы loopback).
- Ответ с редиректом трактуется как ошибка.
"""
import glob
import json
import os
import urllib.error
import urllib.parse
import urllib.request

# --- Жёстко зафиксированные параметры (не берутся из env/argv) ---
BASE = "http://127.0.0.1:8080"
ALLOWED_PREFIX = "http://127.0.0.1:8080/api/v1/"
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
    if not parsed.path.startswith(ALLOWED_PREFIX[len(BASE):]) or not parsed.path.startswith("/api/v1/"):
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
    with _opener.open(req, timeout=120) as resp:
        return json.loads(resp.read())


def main():
    files = sorted(glob.glob(VAULT + "/**/*.md", recursive=True))
    files = [f for f in files if "/.obsidian/" not in f]
    print(f"всего файлов: {len(files)}")

    ok = skip = 0
    fail = []
    for i, path in enumerate(files, 1):
        rel = os.path.relpath(path, VAULT)
        try:
            with open(path, "rb") as fh:
                content = fh.read()
            if not content.strip():
                skip += 1
                print(f"[{i}] ПУСТО, пропуск: {rel}")
                continue
            up = api(
                "POST",
                "/api/v1/files/?process=true&process_in_background=false",
                files=[("file", os.path.basename(path), content, "text/markdown")],
            )
            fid = up.get("id")
            if not fid:
                fail.append((rel, "нет id: " + str(up)[:120]))
                continue
            api("POST", f"/api/v1/knowledge/{KB_ID}/file/add", data={"file_id": fid})
            ok += 1
            print(f"[{i}/{len(files)}] OK: {rel}")
        except Exception as e:
            fail.append((rel, str(e)[:150]))
            print(f"[{i}/{len(files)}] FAIL: {rel}: {e}")

    print(f"\nИТОГ: ok={ok}, skip={skip}, fail={len(fail)}")
    for f in fail:
        print("  -", f[0], "|", f[1])


if __name__ == "__main__":
    main()
