from pathlib import Path
from types import SimpleNamespace
import time

import cv2
import numpy as np
from ultralytics import YOLO


class Vision:
    def __init__(self, config, model=None):
        self.config = config
        self.model = model if model is not None else YOLO(config.get("model", "yolo11n-seg.pt"))
        self.references = []
        for file in config.get("empty_references", []):
            image = cv2.imread(str(Path(file)))
            if image is None:
                raise ValueError("Empty-space reference cannot be read")
            self.references.append(image)

    def predict(self, frame, imgsz=None):
        return self.model.predict(frame, imgsz=self.config.get("imgsz", 960) if imgsz is None else imgsz,
                                  conf=self.config.get("confidence", 0.35),
                                  device=self.config.get("device", "cpu"),
                                  retina_masks=True, verbose=False)[0]

    def analyse(self, frame, result=None):
        start = time.monotonic()
        height, width = frame.shape[:2]
        polygon = np.asarray(self.config["polygon"], dtype=float)
        points = np.rint(polygon * [width - 1, height - 1]).astype(np.int32)
        roi = np.zeros((height, width), dtype=np.uint8)
        cv2.fillPoly(roi, [points], 1)
        area = int(roi.sum())
        if area < 100:
            raise ValueError("Selected parking area is too small")
        if result is None:
            result = self.predict(frame)
        vehicles = np.zeros_like(roi, dtype=bool)
        blockers = np.zeros_like(vehicles)
        detections = []
        if result.masks is not None:
            for box, mask in zip(result.boxes, result.masks.data):
                label = result.names[int(box.cls.item())]
                mask = (mask.cpu().numpy() if hasattr(mask, "cpu") else np.asarray(mask)) > 0.5
                if mask.shape != roi.shape:
                    mask = cv2.resize(mask.astype(np.uint8), (width, height),
                                      interpolation=cv2.INTER_NEAREST).astype(bool)
                if label in {"car", "truck", "bus", "motorcycle"}:
                    vehicles |= mask
                    detections.append({"label": label, "confidence": round(float(box.conf.item()), 3),
                                       "space_coverage": round(float((mask & roi.astype(bool)).sum() / area), 4)})
                else:
                    blockers |= mask
        coverage = float((vehicles & roi.astype(bool)).sum() / area)
        blocking = float((blockers & roi.astype(bool)).sum() / area)
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        pixels = grey[roi.astype(bool)]
        quality_ok = (float(pixels.mean()) >= self.config.get("min_brightness", 12)
                      and float((pixels > 250).mean()) < 0.8
                      and float(pixels.std()) >= self.config.get("min_contrast", 8))
        empty_difference = None
        empty_structure = None
        reference_matches = []
        # A vehicle detector's miss is insufficient evidence that a space is free.
        # Require a manually confirmed empty reference of this same fixed view.
        for reference in self.references:
            if reference.shape != frame.shape:
                raise ValueError("Reference dimensions differ from the current frame")
            ref_grey = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
            difference = self.reference_difference(grey, ref_grey, roi)
            structure = self.reference_structure_similarity(grey, ref_grey, roi)
            reference_matches.append((difference, structure))
        difference_limit = self.config.get("empty_difference_max", 0.08)
        structure_limit = self.config.get("empty_structure_min", 0.4)
        if reference_matches:
            # Both tests must pass against the same confirmed empty frame.
            empty_difference, empty_structure = min(reference_matches, key=lambda match:
                max(match[0] / difference_limit, (1 - match[1]) / (1 - structure_limit)))
        occupied_threshold = self.config.get("occupied_coverage", 0.20)
        free_threshold = self.config.get("free_coverage", 0.03)
        if not quality_ok:
            observation, reason = "unknown", "poor_image"
        elif coverage >= occupied_threshold:
            observation, reason = "occupied", "vehicle_in_space"
        elif blocking > 0.03 or coverage > free_threshold:
            observation, reason = "unknown", "partial_obstruction"
        elif empty_difference is None:
            observation, reason = "unknown", "need_empty_reference"
        elif empty_difference <= difference_limit and empty_structure >= structure_limit:
            observation, reason = "free", "matches_empty_reference"
        elif empty_difference <= difference_limit:
            observation, reason = "unknown", "ground_texture_mismatch"
        else:
            observation, reason = "unknown", "does_not_match_empty_reference"
        report = {"observation": observation, "reason": reason,
                  "vehicle_coverage": round(coverage, 4), "obstruction_coverage": round(blocking, 4),
                  "empty_difference": None if empty_difference is None else round(empty_difference, 4),
                  "empty_structure_similarity": None if empty_structure is None else round(empty_structure, 4),
                  "detections": detections, "inference_seconds": round(time.monotonic() - start, 3)}
        return report, roi, vehicles

    @staticmethod
    def reference_difference(grey, reference, roi):
        # Compensate for a global exposure shift, not for arbitrary scene changes.
        mask = roi.astype(bool)
        current = cv2.GaussianBlur(grey, (9, 9), 0).astype(np.float32)
        empty = cv2.GaussianBlur(reference, (9, 9), 0).astype(np.float32)
        shift = np.median(current[mask] - empty[mask])
        return float(np.abs(current[mask] - empty[mask] - shift).mean() / 255)

    @staticmethod
    def reference_structure_similarity(grey, reference, roi):
        # SSIM's contrast/structure term ignores an additive exposure shift.
        # A similarly grey roof must still show the reference's ground texture.
        current, empty = grey.astype(np.float32), reference.astype(np.float32)
        blur = lambda pixels: cv2.GaussianBlur(pixels, (21, 21), 0)
        mean_current, mean_empty = blur(current), blur(empty)
        variance_current = np.maximum(0, blur(current * current) - mean_current * mean_current)
        variance_empty = np.maximum(0, blur(empty * empty) - mean_empty * mean_empty)
        covariance = blur(current * empty) - mean_current * mean_empty
        stabiliser = (0.03 * 255) ** 2
        similarity = (2 * covariance + stabiliser) / (variance_current + variance_empty + stabiliser)
        return float(np.clip(similarity[roi.astype(bool)], -1, 1).mean())

    def annotated(self, frame, roi, vehicles):
        out = frame.copy()
        overlay = frame.copy()
        overlay[vehicles] = [70, 210, 100]
        out = cv2.addWeighted(out, 0.65, overlay, 0.35, 0)
        contours, _ = cv2.findContours(roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(out, contours, -1, (30, 180, 255), 4)
        return out


class MultiVision:
    """Shared detection, with parking crops for additional obstruction evidence."""

    def __init__(self, spaces):
        self.spaces = spaces
        self.visions = []
        for space in spaces:
            shared = self.visions[0].model if self.visions else None
            self.visions.append(Vision(space, model=shared))
        self.identity, self.identity_error = None, None
        self.occupancy_fallback = None
        settings = spaces[0].get("vehicle_identity", {})
        if settings.get("enabled"):
            try:
                from .vehicle_identity import VehicleIdentity
                self.identity = VehicleIdentity(settings, self.visions[0])
            except Exception:
                # An optional resemblance feature must never stop parking monitoring.
                self.identity_error = {"name": settings.get("name", "Эталонный автомобиль"),
                                       "confidence": "unknown", "reason": "reference_unavailable"}

    def analyse(self, frame):
        start = time.monotonic()
        result = self.visions[0].predict(frame)
        reports = {}
        out = frame.copy()
        rois = {}
        vehicle_masks = {}
        for index, (space, vision) in enumerate(zip(self.spaces, self.visions)):
            report, roi, vehicles = vision.analyse(frame, result=result)
            reports[space["id"]] = report
            rois[space["id"]] = roi
            vehicle_masks[space["id"]] = vehicles
            contours, _ = cv2.findContours(roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            colour = [(30, 180, 255), (255, 190, 40)][index % 2]
            cv2.drawContours(out, contours, -1, colour, 4)
            point = np.rint(np.asarray(space["polygon"][0]) * [frame.shape[1]-1, frame.shape[0]-1]).astype(int)
            cv2.putText(out, space["id"], tuple(point + [4, -8]), cv2.FONT_HERSHEY_SIMPLEX,
                        .7, colour, 2, cv2.LINE_AA)
        eligible = [(space, vision) for space, vision in zip(self.spaces, self.visions)
                    if space.get("contrast_occupancy_fallback", False)
                    and reports[space["id"]]["observation"] != "occupied"
                    and reports[space["id"]]["reason"] != "poor_image"]
        # Daylight colour and shadows can be less recognisable after CLAHE.
        # Try the original parking pixels before enhancing contrast for night.
        for view, cropper in [("parking_crop", self.parking_crop),
                              ("contrast_crop", self.contrast_crop)]:
            if not eligible:
                break
            crop, x, y = cropper(frame, rois.values())
            detected = self.visions[0].predict(crop)
            projected = self.project_result(detected, crop.shape[:2], frame.shape[:2], x, y)
            for space, vision in eligible:
                # References and image quality always use the untouched camera frame.
                # A miss on a crop can never provide vacancy evidence.
                extra, _, vehicles = vision.analyse(frame, result=projected)
                base = reports[space["id"]]
                if extra["observation"] == "occupied" or (
                        base["observation"] == "free" and extra["reason"] == "partial_obstruction"):
                    extra["detector_view"] = view
                    reports[space["id"]] = extra
                    vehicle_masks[space["id"]] = vehicles
            eligible = [(space, vision) for space, vision in eligible
                        if reports[space["id"]]["observation"] != "occupied"]
        for space, vision in eligible:
            # A passing road vehicle can change detection of both parking spaces.
            # Isolate the remaining space; 640 keeps this extra pass inexpensive.
            crop, x, y = self.contrast_crop(frame, [rois[space["id"]]])
            detected = vision.predict(crop, imgsz=min(space.get("imgsz", 960), 640))
            projected = self.project_result(detected, crop.shape[:2], frame.shape[:2], x, y)
            extra, _, vehicles = vision.analyse(frame, result=projected)
            base = reports[space["id"]]
            if extra["observation"] == "occupied" or (
                    base["observation"] == "free" and extra["reason"] == "partial_obstruction"):
                extra["detector_view"] = "space_contrast_crop"
                reports[space["id"]] = extra
                vehicle_masks[space["id"]] = vehicles
            fallback_name = space.get("occupancy_fallback_model")
            if fallback_name and reports[space["id"]]["observation"] != "occupied":
                try:
                    # Load the larger detector only when the fast detector misses.
                    # It sees an isolated space at 640, with the same confidence.
                    if self.occupancy_fallback is None:
                        self.occupancy_fallback = YOLO(fallback_name)
                    detected = self.occupancy_fallback.predict(
                        crop, imgsz=min(space.get("imgsz", 960), 640),
                        conf=space.get("confidence", 0.35), device=space.get("device", "cpu"),
                        retina_masks=True, verbose=False)[0]
                    projected = self.project_result(detected, crop.shape[:2], frame.shape[:2], x, y)
                    extra, _, vehicles = vision.analyse(frame, result=projected)
                    base = reports[space["id"]]
                    if extra["observation"] == "occupied" or (
                            base["observation"] == "free" and extra["reason"] == "partial_obstruction"):
                        extra["detector_view"] = "space_fallback_model"
                        reports[space["id"]] = extra
                        vehicle_masks[space["id"]] = vehicles
                except Exception:
                    # Failure of optional detection must not manufacture vacancy.
                    reports[space["id"]]["occupancy_fallback_error"] = "detector_unavailable"
                    if reports[space["id"]]["observation"] == "free":
                        reports[space["id"]].update(observation="unknown", reason="occupancy_fallback_unavailable")
        for space in self.spaces:
            key = space["id"]
            if reports[key]["observation"] != "occupied":
                continue
            identity = getattr(self, "identity", None)
            if identity is not None:
                try:
                    reports[key]["vehicle_identity"] = identity.match(frame, rois[key], vehicle_masks[key])
                except Exception:
                    reports[key]["vehicle_identity"] = {"name": identity.name, "confidence": "unknown",
                                                       "reason": "comparison_unavailable"}
            elif getattr(self, "identity_error", None):
                reports[key]["vehicle_identity"] = self.identity_error.copy()
        elapsed = round(time.monotonic() - start, 3)
        for report in reports.values():
            report["inference_seconds"] = elapsed
        return reports, out

    @staticmethod
    def parking_crop(frame, rois):
        height, width = frame.shape[:2]
        union = np.logical_or.reduce([roi.astype(bool) for roi in rois])
        ys, xs = np.nonzero(union)
        # Keep nearby context, while excluding lamps below the parking areas.
        x = max(0, int(xs.min() - width * .03))
        y = max(0, int(ys.min() - height * .03))
        right = min(width, int(xs.max() + width * .03) + 1)
        bottom = min(height, int(ys.max() + height * .03) + 1)
        return frame[y:bottom, x:right].copy(), x, y

    @staticmethod
    def contrast_crop(frame, rois):
        crop, x, y = MultiVision.parking_crop(frame, rois)
        lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
        lab[:, :, 0] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lab[:, :, 0])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR), x, y

    @staticmethod
    def project_result(result, crop_shape, frame_shape, x, y):
        masks = None
        if result.masks is not None:
            projected = []
            for tensor in result.masks.data:
                mask = tensor.cpu().numpy() if hasattr(tensor, "cpu") else np.asarray(tensor)
                if mask.shape != crop_shape:
                    mask = cv2.resize(mask, (crop_shape[1], crop_shape[0]),
                                      interpolation=cv2.INTER_NEAREST)
                full = np.zeros(frame_shape, dtype=np.float32)
                full[y:y + crop_shape[0], x:x + crop_shape[1]] = mask
                projected.append(full)
            masks = SimpleNamespace(data=projected)
        return SimpleNamespace(boxes=result.boxes, names=result.names, masks=masks)
