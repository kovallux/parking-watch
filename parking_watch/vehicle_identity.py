from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO


class VehicleIdentity:
    """Visual resemblance to a user-verified vehicle, independent of parking state."""

    def __init__(self, settings, vision):
        if settings.get("verified_by_user") is not True:
            raise ValueError("Vehicle references need user confirmation")
        self.name = settings.get("name", "Эталонный автомобиль")
        self.high = settings.get("high_similarity", .98)
        self.medium = settings.get("medium_similarity", .95)
        if not 0 <= self.medium < self.high <= 1:
            raise ValueError("Invalid vehicle resemblance thresholds")
        self.device = vision.config.get("device", "cpu")
        # Embedding predictions have their own predictor; parking detection stays separate.
        self.model = YOLO(vision.config.get("model", "yolo11n-seg.pt"))
        self.references = []
        from .vision import Vision
        for reference in settings.get("references", []):
            frame = cv2.imread(str(Path(reference["file"])))
            if frame is None:
                raise ValueError("Vehicle reference cannot be read")
            config = {**vision.config, "polygon": reference["polygon"], "empty_references": []}
            reference_vision = Vision(config, model=vision.model)
            report, roi, vehicles = reference_vision.analyse(frame)
            crop = self.vehicle_crop(frame, roi, vehicles)
            if report["observation"] != "occupied" or crop is None:
                raise ValueError("Vehicle reference does not contain a clear vehicle")
            self.references.extend(self.embeddings(crop))
        if not self.references:
            raise ValueError("No verified vehicle references")

    @staticmethod
    def vehicle_crop(frame, roi, vehicles):
        count, labels = cv2.connectedComponents(vehicles.astype(np.uint8), connectivity=8)
        candidates = [(int(((labels == label) & roi.astype(bool)).sum()), label)
                      for label in range(1, count)]
        if not candidates:
            return None
        overlap, label = max(candidates)
        if overlap < max(100, int(roi.sum() * .2)):
            return None
        mask = labels == label
        ys, xs = np.nonzero(mask)
        crop = frame[ys.min():ys.max() + 1, xs.min():xs.max() + 1].copy()
        local = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        # Ground, lamps and the position of the parking space are not identity evidence.
        crop[~local] = 114
        lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
        lab[:, :, 0] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lab[:, :, 0])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    def embeddings(self, crop):
        images = [crop, cv2.rotate(crop, cv2.ROTATE_180)]
        tensors = self.model.embed(images, imgsz=224, device=self.device, verbose=False)
        vectors = [tensor.cpu().numpy() for tensor in tensors]
        if any(not np.isfinite(vector).all() or np.linalg.norm(vector) <= 0 for vector in vectors):
            raise ValueError("Vehicle embedding unavailable")
        return [vector / np.linalg.norm(vector) for vector in vectors]

    def match(self, frame, roi, vehicles):
        crop = self.vehicle_crop(frame, roi, vehicles)
        if crop is None:
            return {"name": self.name, "confidence": "unknown", "reason": "vehicle_crop_unavailable"}
        vectors = self.embeddings(crop)
        similarity = max(float(vector @ reference) for vector in vectors for reference in self.references)
        confidence = "high" if similarity >= self.high else "medium" if similarity >= self.medium else "low"
        return {"name": self.name, "confidence": confidence,
                "visual_similarity": round(float(np.clip(similarity, 0, 1)), 4),
                "reference_count": len(self.references) // 2,
                "score_is_identity_probability": False}
