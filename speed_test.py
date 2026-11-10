import time
from ultralytics import YOLO

model = YOLO("yolov8n.pt")
start = time.time()
n = 0
for r in model.track("samples/crowd1.mp4", classes=[0], stream=True, verbose=False, imgsz=640):
    n += 1
    if n == 60:
        break
print(f"{n} frames in {time.time()-start:.1f}s -> {n/(time.time()-start):.1f} FPS")