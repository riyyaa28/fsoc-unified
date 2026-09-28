import csv
import time


class PerformanceLogger:
    def __init__(self, path="performance_log.csv"):
        self.f = open(path, "w", newline="", encoding="utf-8")
        self.writer = csv.writer(self.f)
        self.closed = False

        self.writer.writerow(
            ["timestamp", "fps", "source", "confidence", "error_px", "distance_m"]
        )
        self.f.flush()

    def log(self, stats):
        if self.closed or self.f.closed:
            return

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

    def close(self):
        if not self.closed:
            self.closed = True
            if not self.f.closed:
                self.f.close()
