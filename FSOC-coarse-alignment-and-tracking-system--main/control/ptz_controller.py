def compute_delta(tracked_pos, frame_center, gain=0.3):
    tx, ty = tracked_pos
    cx, cy = frame_center
    dx = (tx - cx) * gain
    dy = (ty - cy) * gain
    return dx, dy
