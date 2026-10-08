import cv2

_clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))


def denoise_for_detection(frame):
    denoised = cv2.bilateralFilter(frame, d=7, sigmaColor=50, sigmaSpace=50)
    lab = cv2.cvtColor(denoised, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = _clahe.apply(l)
    enhanced = cv2.merge((l, a, b))
    return cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
