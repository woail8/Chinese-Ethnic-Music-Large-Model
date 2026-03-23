import glob
import os

import cv2

from pre_processing import preProcessing


def main():
    input_dir = "pngImgs"
    output_dir = os.path.join("output", "verify_pngImgs")
    os.makedirs(output_dir, exist_ok=True)

    patterns = ["*.png", "*.PNG"]
    paths = []
    for p in patterns:
        paths.extend(glob.glob(os.path.join(input_dir, p)))
    paths = sorted(set(paths))

    if not paths:
        print("No images found in pngImgs")
        return

    for i, path in enumerate(paths, start=1):
        img = cv2.imread(path)
        if img is None:
            print(f"[{i}/{len(paths)}] Skipped unreadable: {path}")
            continue
        out = preProcessing(img, path)
        base = os.path.splitext(os.path.basename(path))[0]
        out_path = os.path.join(output_dir, f"{base}_verify.png")
        cv2.imwrite(out_path, out)
        print(f"[{i}/{len(paths)}] Wrote: {out_path}")


if __name__ == "__main__":
    main()
