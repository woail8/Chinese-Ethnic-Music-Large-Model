import glob
import mimetypes
import os
import uuid
import urllib.request
from pathlib import Path


def main():
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

    ok_text = "识别输出序列" in html
    ok_img = "/processed/" in html
    print("ok_text", ok_text, "ok_img", ok_img, "html_len", len(html))
    if not ok_img:
        for key in ["未选择文件", "不支持的文件类型", "图片读取失败"]:
            if key in html:
                print("page_error", key)
                break


if __name__ == "__main__":
    main()
