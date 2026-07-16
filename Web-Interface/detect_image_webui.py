"""
Simplified web GUI for testing human detection on a static image —
upload a photo from your browser instead of streaming a live camera.
Use this to test the web-GUI mechanics before the camera arrives;
swap to human_detect_gui.py once you have it.

Usage:
    python3 detect_image_webui.py
    then open http://localhost:5000 in your browser
    (or http://<board-ip>:5000 if running on the UNO Q)
"""

import base64
import numpy as np
import cv2
from flask import Flask, request
from ultralytics import YOLO

# ---------------------------------------------------------------
# CONFIG — adjust these for your setup
# ---------------------------------------------------------------
MODEL_PATH = "../Model/best.pt"
PERSON_CLASS_ID = 0         # check with: print(YOLO(MODEL_PATH).names)
CONF_THRESHOLD = 0.5

app = Flask(__name__)
model = YOLO(MODEL_PATH)


def draw_detections(frame, results):
    count = 0
    for r in results:
        for box in r.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            if cls_id != PERSON_CLASS_ID or conf < CONF_THRESHOLD:
                continue
            count += 1
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            label = f"person {conf:.2f}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (x1, y1 - th - 8), (x1 + tw + 4, y1), (0, 255, 0), -1)
            cv2.putText(frame, label, (x1 + 2, y1 - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    return frame, count


PAGE_TEMPLATE = """
<html>
  <head><title>Human Detection - Image Test</title></head>
  <body style="background:#111; color:#eee; font-family:sans-serif; text-align:center; padding:30px;">
    <h2>Human Detection — Static Image Test</h2>
    <form method="POST" enctype="multipart/form-data">
      <input type="file" id="photo-input" name="photo" accept="image/*" required
             onchange="previewImage(this)">
      <button type="submit" style="padding:6px 16px;">Run Detection</button>
    </form>
    <img id="preview" style="max-width:90%; border:2px solid #444; margin-top:10px; display:none;">
    <script>
      function previewImage(input) {{
        const preview = document.getElementById('preview');
        if (!input.files || !input.files[0]) {{
          preview.style.display = 'none';
          return;
        }}
        const reader = new FileReader();
        reader.onload = e => {{
          preview.src = e.target.result;
          preview.style.display = 'inline-block';
        }};
        reader.readAsDataURL(input.files[0]);
      }}
    </script>
    {result}
  </body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return PAGE_TEMPLATE.format(result="")

    file = request.files.get("photo")
    if not file:
        return PAGE_TEMPLATE.format(result="<p>No file received.</p>")

    file_bytes = np.frombuffer(file.read(), dtype=np.uint8)
    frame = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
    if frame is None:
        return PAGE_TEMPLATE.format(result="<p>Could not read that image.</p>")

    results = model(frame, verbose=False)
    frame, count = draw_detections(frame, results)

    ok, buffer = cv2.imencode(".jpg", frame)
    b64_image = base64.b64encode(buffer).decode("utf-8")

    result_html = f"""
      <p>Detected {count} person(s) above confidence {CONF_THRESHOLD}</p>
      <img src="data:image/jpeg;base64,{b64_image}" style="max-width:90%; border:2px solid #444; margin-top:10px;">
    """
    return PAGE_TEMPLATE.format(result=result_html)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)