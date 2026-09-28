import csv
import time


class PerformanceLogger:
    def __init__(self, path="performance_log.csv"):
        self.f = open(path, "w", newline="")
        self.writer = csv.writer(self.f)
        self.writer.writerow(
            ["timestamp", "fps", "source", "confidence", "error_px", "distance_m"]
        )

    def log(self, stats):
        self.writer.writerow(
            [
                time.time(),
                stats["fps"],
                stats["source"],
                stats["confidence"],
                stats["error"],
                stats.get("distance_m"),
            ]
        )
        self.f.flush()
