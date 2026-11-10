import cv2
from ultralytics import YOLO

print("OpenCV:", cv2.__version__)
model = YOLO("yolov8n.pt")
print("Model loaded. Class 0 =", model.names[0])