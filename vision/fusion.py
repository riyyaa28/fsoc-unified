def fuse_detection(
    yolo_pos,
    yolo_verified_score,
    classical_pos,
    classical_score,
    kalman_tracker,
    min_score=0.5,
):
    """Both candidates are scored on the SAME physically-grounded scale (ring
    signature strength, 0-1) — not on YOLO's own self-reported confidence.
    Whichever candidate actually passes the ring check with the higher score
    wins, regardless of which detector found it. A decoy structurally can't
    win this comparison, since it has no ring signature to score well on."""
    candidates = []
    if yolo_pos is not None and yolo_verified_score >= min_score:
        candidates.append((yolo_pos, yolo_verified_score, "yolo"))
    if classical_pos is not None and classical_score >= min_score:
        candidates.append((classical_pos, classical_score, "classical"))

    if candidates:
        candidates.sort(key=lambda c: c[1], reverse=True)
        best_pos, best_score, best_source = candidates[0]
        tracked_pos, _ = kalman_tracker.update(best_pos)
        return tracked_pos, best_score, best_source
    else:
        tracked_pos, kf_conf = kalman_tracker.update(None)
        return tracked_pos, kf_conf, "kalman"
