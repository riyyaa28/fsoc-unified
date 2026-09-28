import cv2


def draw_crosshair(frame, center, size=12, gap=4, color=(0, 255, 0), thickness=1):
    cx, cy = int(center[0]), int(center[1])
    cv2.line(frame, (cx - size, cy), (cx - gap, cy), color, thickness)
    cv2.line(frame, (cx + gap, cy), (cx + size, cy), color, thickness)
    cv2.line(frame, (cx, cy - size), (cx, cy - gap), color, thickness)
    cv2.line(frame, (cx, cy + gap), (cx, cy + size), color, thickness)
    cv2.circle(frame, (cx, cy), 2, color, -1)


def draw_camera_aim_crosshair(full_frame, camera, size=14, color=(0, 255, 255)):
    draw_crosshair(full_frame, (camera.pan_x, camera.tilt_y), size=size, color=color)
