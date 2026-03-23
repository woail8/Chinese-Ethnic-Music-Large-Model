import glob
import mimetypes
import os
import re
import uuid
import urllib.request
import json
from pathlib import Path


def post_image_get_sid():
    paths = sorted(glob.glob(os.path.join("pngImgs", "*.png")))
    if not paths:
        raise SystemExit("no pngImgs/*.png found")
    p = paths[0]

    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex
    fn = os.path.basename(p)
    ctype = mimetypes.guess_type(fn)[0] or "application/octet-stream"

    body = []
    body.append(f"--{boundary}\r\n".encode())
    body.append(f'Content-Disposition: form-data; name="file"; filename="{fn}"\r\n'.encode())
    body.append(f"Content-Type: {ctype}\r\n\r\n".encode())
    body.append(Path(p).read_bytes())
    body.append(b"\r\n")
    body.append(f"--{boundary}--\r\n".encode())
    data = b"".join(body)

    req = urllib.request.Request("http://127.0.0.1:8000/recognize", data=data, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Content-Length", str(len(data)))
    html = urllib.request.urlopen(req, timeout=120).read().decode("utf-8", "ignore")
    m = re.search(r"/processed/([0-9a-f]{32})", html)
    if not m:
        raise SystemExit("sid not found in response html")
    return m.group(1)


def main():
    sid = post_image_get_sid()
    anno = json.loads(urllib.request.urlopen(f"http://127.0.0.1:8000/api/annotation/{sid}", timeout=120).read().decode())
    print("sid", sid)
    print("keys", sorted(anno.keys()))
    print("lines", len(anno.get("lines") or []), "items", len(anno.get("items") or []))
    seq = anno.get("sequence") or ""
    print("seq_len", len(seq))


if __name__ == "__main__":
    main()

