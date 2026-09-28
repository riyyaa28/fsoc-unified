import cv2
import numpy as np

_open_kernel = np.ones((3, 3), np.uint8)


def _radial_profile(gray, cx, cy, max_r=26):
    h, w = gray.shape
    if cx - max_r < 0 or cx + max_r >= w or cy - max_r < 0 or cy + max_r >= h:
        return None
    polar = cv2.warpPolar(
        gray, (max_r, 180), (float(cx), float(cy)), max_r, cv2.WARP_POLAR_LINEAR
    )
    return polar.mean(axis=0)


def _count_secondary_bumps(profile, min_prominence=15):
    n = len(profile)
    i = 1
    while i < n - 1 and profile[i] <= profile[i - 1]:
        i += 1
    bumps = 0
    prev_bump_val = 255.0
    while i < n - 1:
        if profile[i] > profile[i - 1] and profile[i] >= profile[i + 1]:
            local_min_before = min(profile[max(0, i - 4) : i]) if i > 0 else profile[i]
            prominence = profile[i] - local_min_before
            if prominence > min_prominence and profile[i] < prev_bump_val:
                bumps += 1
                prev_bump_val = profile[i]
            while i < n - 1 and profile[i] >= profile[i + 1]:
                i += 1
        i += 1
    return bumps


def score_candidate(gray, cx, cy):
    profile = _radial_profile(gray, cx, cy)
    if profile is None or profile[0] < 150:
        return 0.0
    bumps = _count_secondary_bumps(profile)
    return min(bumps / 2.0, 1.0)


def detect_beacon_classical(
    frame, brightness_threshold=150, match_threshold=0.5, gray=None, max_candidates=6
):
    if gray is None:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    _, mask = cv2.threshold(gray, brightness_threshold, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN, _open_kernel
    )  # kills 1-2px noise speckles cheaply
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, 0.0

    # under heavy noise this can otherwise explode into dozens of candidates —
    # only the biggest few are worth the (relatively expensive) radial check
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:max_candidates]

    best_pos, best_score = None, 0.0
    for c in contours:
        M = cv2.moments(c)
        if M["m00"] == 0:
            continue
        cx, cy = int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"])
        score = score_candidate(gray, cx, cy)
        if score > best_score:
            best_score, best_pos = score, (cx, cy)

    if best_pos is None or best_score < match_threshold:
        return None, 0.0
    return best_pos, float(best_score)
