# IRHumanDetection

# Current Instructions
1. Clone github:
    git clone https://github.com/fxionxa/IRHumanDetection.git  

2. Download LLVIP dataset from kaggle:
    https://www.kaggle.com/datasets/monishshrivastava1/llvip-dataset?resource=download

3. Delete Visible Folder

4. Rename infrared to images

5. Rename train to val

6. Run voc_to_yolo.ipynb, make sure to initialize ROOT

7. Check sizes match for both images and labels:
    dir LLVIP\infrared\train | Measure-Object 
    dir LLVIP\labels\train | Measure-Object 
    dir LLVIP\infrared\test | Measure-Object 
    dir LLVIP\labels\test | Measure-Object

8. Update path in data.yaml to where LLVIP and data.yaml are visible

9. Install Ultralytics and check YOLO version:
    pip install ultralytics 
    check yolo --version  

10. Check if you have NVIDIA GPU
    nvidia-smi

11. Train 1 epoch to install and test yolo11n:
    yolo detect train model=yolo11n.pt data=data.yaml epochs=1 imgsz=640 batch=16 device=0

12. Train with 100 epochs:
    yolo detect train model=yolo11n.pt data=data.yaml epochs=100 imgsz=640 batch=16 device=0

13. Run predictions to test best model:
    yolo detect predict model=runs/detect/train/weights/best.pt source=LLVIP/images/val

14. Export model:
    yolo export model=runs/detect/train/weights/best.pt format=onnx

