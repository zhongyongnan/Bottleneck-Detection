####Bottleneck detection model based on improved Faster-RCNN
Implemented Faster R-CNN model in TensorFlow to detect bottlenecks in over 1,000 images and used Resnet50 backbone as feature extraction network.
Employed ROI align technique to solve the problem of region mismatch due to quantization errors in ROI Pooling, aligning the ROI features with the original image coordinates and achieving the accuracy of 99.56%.
Created a functional user interface of the detection system using PyQt5 framework for users to upload images and display coordinates of the detected bottleneck.
