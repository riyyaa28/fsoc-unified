from ultralytics import YOLO

model = YOLO("yolo12n.pt")

model.train(
    data="dataset/data.yaml",
    epochs=10,
    imgsz=640,
    batch=8
)