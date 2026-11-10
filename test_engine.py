import cv2, numpy as np
from engine import CrowdEngine

zones = [{"name": "Zone A", "capacity": 5,
          "points": [[0.1, 0.2], [0.9, 0.2], [0.9, 0.95], [0.1, 0.95]]}]
engine = CrowdEngine("samples/crowd1.mp4", zones)
for jpg, stats in engine.run():
    frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
    cv2.imshow("debug", frame)
    print(stats["zones"][0]["count"], stats["proc_fps"], stats["events"])
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break