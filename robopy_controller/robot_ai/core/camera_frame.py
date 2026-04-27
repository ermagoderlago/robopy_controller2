import cv2
import base64
import numpy as np
from dataclasses import dataclass

@dataclass
class CameraFrame:
    """Frame atomico e immutabile con decodifica lazy."""
    raw: bytes

    @property
    def b64(self) -> str:
        cached = self.__dict__.get('_b64')
        if cached is None:
            cached = base64.b64encode(self.raw).decode('utf-8')
            object.__setattr__(self, '_b64', cached)
        return cached

    @property
    def cv_image(self):
        cached = self.__dict__.get('_cv')
        if cached is None:
            np_arr = np.frombuffer(self.raw, np.uint8)
            cached = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            object.__setattr__(self, '_cv', cached)
        return cached
