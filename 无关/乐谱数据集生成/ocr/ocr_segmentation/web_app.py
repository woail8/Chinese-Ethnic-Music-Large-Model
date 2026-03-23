import os
import uuid
import json

import cv2
import numpy as np
from flask import Flask, render_template, request, send_file, jsonify

from pre_processing import preProcessing, build_sequence_line


def create_app():
    app = Flask(__name__)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(base_dir, "output")
    upload_dir = os.path.join(output_dir, "uploads")
    web_dir = os.path.join(output_dir, "web")
    os.makedirs(upload_dir, exist_ok=True)
    os.makedirs(web_dir, exist_ok=True)

    def annotation_path(sid: str):
        return os.path.join(web_dir, f"{sid}.json")

    def load_annotation(sid: str):
        p = annotation_path(sid)
        if not os.path.exists(p):
            return None
        with open(p, "r", encoding="utf-8") as r:
            return json.load(r)

    def save_annotation(sid: str, data):
        p = annotation_path(sid)
        with open(p, "w", encoding="utf-8") as w:
            json.dump(data, w, ensure_ascii=False, indent=2)

    def rebuild_sequence_from_annotation(data):
        lines = data.get("lines") or []
        items = data.get("items") or []
        seq_lines = []
        for ln in lines:
            y0 = int(ln.get("y0", 0))
            y1 = int(ln.get("y1", 0))
            if y1 <= y0:
                continue
            in_line = []
            for it in items:
                x = float(it.get("x", 0))
                y = float(it.get("y", 0))
                w = float(it.get("w", 0))
                h = float(it.get("h", 0))
                cy = y + (h / 2.0)
                if cy >= y0 and cy <= y1:
                    in_line.append(it)
            digit_boxes = []
            hline_boxes = []
            vline_boxes = []
            dot_boxes = []
            accidental_boxes = []
            for it in in_line:
                t = str(it.get("type", "")).strip()
                x = int(float(it.get("x", 0)))
                y = int(float(it.get("y", 0)))
                w = int(float(it.get("w", 0)))
                h = int(float(it.get("h", 0)))
                if w <= 0 or h <= 0:
                    continue
                if t == "digit":
                    txt = str(it.get("text", "")).strip()
                    if not txt:
                        continue
                    conf = int(it.get("conf", 100))
                    digit_boxes.append((x, y - y0, w, h, txt, conf))
                elif t == "hline":
                    hline_boxes.append((x, y - y0, w, h))
                elif t == "vline":
                    vline_boxes.append((x, y - y0, w, h))
                elif t == "dot":
                    dot_boxes.append((x, y - y0, w, h))
                elif t == "accidental":
                    txt = str(it.get("text", "")).strip()
                    if txt not in ("#", "b"):
                        continue
                    conf = int(it.get("conf", 100))
                    accidental_boxes.append((x, y - y0, w, h, txt, conf))
            digit_boxes.sort(key=lambda d: d[0])
            hline_boxes.sort(key=lambda b: b[0])
            vline_boxes.sort(key=lambda b: b[0])
            dot_boxes.sort(key=lambda b: b[0])
            accidental_boxes.sort(key=lambda b: b[0])
            seq_lines.append(build_sequence_line(digit_boxes, hline_boxes, dot_boxes, vline_boxes, accidental_boxes))
        return "\n".join(seq_lines)

    def find_upload_path(sid: str):
        if not os.path.exists(upload_dir):
            return None
        for name in os.listdir(upload_dir):
            if name.startswith(sid):
                p = os.path.join(upload_dir, name)
                if os.path.isfile(p):
                    return p
        return None

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/recognize")
    def recognize():
        f = request.files.get("file")
        if f is None or not f.filename:
            return render_template("index.html", error="未选择文件")

        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
            return render_template("index.html", error="不支持的文件类型")

        sid = uuid.uuid4().hex
        in_path = os.path.join(upload_dir, f"{sid}{ext}")
        f.save(in_path)

        with open(in_path, "rb") as r:
            buf = r.read()
        img = cv2.imdecode(np.frombuffer(buf, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return render_template("index.html", error="图片读取失败")

        out_img, seq_text, det = preProcessing(img, in_path, return_detections=True)
        out_img_path = os.path.join(web_dir, f"{sid}_processed.png")
        ok, enc = cv2.imencode(".png", out_img)
        if ok:
            with open(out_img_path, "wb") as w:
                w.write(enc.tobytes())

        lines = det.get("lines", [])
        items = []
        for ln in lines:
            for it in ln.get("items", []):
                it["line_y0"] = ln.get("y0", 0)
                it["line_y1"] = ln.get("y1", 0)
                items.append(it)
        anno = {"sid": sid, "lines": [{"y0": ln.get("y0", 0), "y1": ln.get("y1", 0)} for ln in lines], "items": items}
        save_annotation(sid, anno)

        return render_template(
            "index.html",
            sequence=seq_text,
            image_url=f"/processed/{sid}",
            sid=sid,
        )

    @app.get("/editor/<sid>")
    def editor(sid: str):
        if load_annotation(sid) is None:
            return ("not found", 404)
        return render_template("editor.html", sid=sid)

    @app.get("/api/annotation/<sid>")
    def api_get_annotation(sid: str):
        data = load_annotation(sid)
        if data is None:
            return jsonify({"error": "not found"}), 404
        data = dict(data)
        data["image_url"] = f"/original/{sid}"
        txt_path = os.path.join(output_dir, "score", f"{sid}.txt")
        if os.path.exists(txt_path):
            with open(txt_path, "r", encoding="utf-8") as r:
                data["sequence"] = r.read()
        else:
            data["sequence"] = rebuild_sequence_from_annotation(data)
        return jsonify(data)

    @app.post("/api/annotation/<sid>")
    def api_save_annotation(sid: str):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "bad json"}), 400
        if str(data.get("sid", sid)) != sid:
            data["sid"] = sid
        items = data.get("items") or []
        accs = []
        for it in items:
            if str(it.get("type")) == "accidental":
                accs.append(it)
        if accs:
            filtered = []
            for it in items:
                if str(it.get("type")) == "accidental":
                    filtered.append(it)
                    continue
                x = float(it.get("x", 0))
                y = float(it.get("y", 0))
                w = float(it.get("w", 0))
                h = float(it.get("h", 0))
                cx = x + (w / 2.0)
                cy = y + (h / 2.0)
                inside = False
                for a in accs:
                    ax = float(a.get("x", 0))
                    ay = float(a.get("y", 0))
                    aw = float(a.get("w", 0))
                    ah = float(a.get("h", 0))
                    if cx >= ax and cx <= (ax + aw) and cy >= ay and cy <= (ay + ah):
                        inside = True
                        break
                if not inside:
                    filtered.append(it)
            data["items"] = filtered
        save_annotation(sid, data)
        seq_text = rebuild_sequence_from_annotation(data)
        out_dir = os.path.join(output_dir, "score")
        os.makedirs(out_dir, exist_ok=True)
        txt_path = os.path.join(out_dir, f"{sid}.txt")
        with open(txt_path, "w", encoding="utf-8") as w:
            w.write(seq_text)
        return jsonify({"ok": True, "sequence": seq_text})

    @app.get("/processed/<sid>")
    def processed(sid: str):
        out_img_path = os.path.join(web_dir, f"{sid}_processed.png")
        if not os.path.exists(out_img_path):
            return ("not found", 404)
        return send_file(out_img_path, mimetype="image/png")

    @app.get("/original/<sid>")
    def original(sid: str):
        p = find_upload_path(sid)
        if p is None:
            return ("not found", 404)
        ext = os.path.splitext(p)[1].lower()
        mt = "application/octet-stream"
        if ext == ".png":
            mt = "image/png"
        elif ext == ".jpg" or ext == ".jpeg":
            mt = "image/jpeg"
        elif ext == ".bmp":
            mt = "image/bmp"
        elif ext == ".webp":
            mt = "image/webp"
        return send_file(p, mimetype=mt)

    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
