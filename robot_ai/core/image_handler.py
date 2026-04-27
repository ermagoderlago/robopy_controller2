"""
ImageHandler: Standard interno = bytes (raw).

Conversione a base64 SOLO al confine API.
"""

import base64
import logging
from dataclasses import dataclass
from typing import Optional, Union

logger = logging.getLogger(__name__)


@dataclass
class Image:
    """Rappresentazione immagine interna (sempre bytes).

    Lo standard interno è bytes raw. La conversione a base64
    avviene SOLO quando serve per le API esterne (es. Gemini).
    """

    data: bytes  # Raw image bytes
    format: str  # "jpeg", "png", "rgb", etc.
    width: int
    height: int
    metadata: Optional[dict] = None
    
    def to_base64(self) -> str:
        """Converte a base64 SOLO per export API."""
        return base64.b64encode(self.data).decode('utf-8')
    
    @staticmethod
    def from_base64(base64_str: str, img_format: str, width: int, height: int) -> 'Image':
        """Crea da base64 (es. da input API)."""
        try:
            raw_bytes = base64.b64decode(base64_str)
            return Image(data=raw_bytes, format=img_format, width=width, height=height)
        except Exception as e:
            logger.error(f"Decodifica base64 fallita: {e}")
            raise ValueError(f"Dati base64 non validi: {str(e)}")
    
    @staticmethod
    def from_ros_image(data: bytes, width: int, height: int, encoding: str = "rgb8") -> "Image":
        """
        Crea da messaggio ROS Image (bytes raw + encoding).
        
        Args:
            data: Bytes raw dal topic ROS.
            width: Larghezza immagine.
            height: Altezza immagine.
            encoding: Encoding ROS (es. 'rgb8', 'bgr8', 'mono8').
        """
        return Image(
            data=data,
            format=encoding,
            width=width,
            height=height,
            metadata={"source": "ros_image"}
        )
    
    @staticmethod
    def from_compressed(data: bytes, format: str = "jpeg") -> "Image":
        """
        Crea da immagine compressa (JPEG/PNG bytes).
        
        Args:
            data: Bytes compressi.
            format: Formato compressione ("jpeg", "png").
        """
        # Dimensioni non note senza decodifica — usiamo placeholder
        return Image(
            data=data,
            format=format,
            width=0,
            height=0,
            metadata={"source": "compressed", "size_bytes": len(data)}
        )


class ImageValidator:
    """Validazione immagini prima di storage/processing."""
    
    MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
    ALLOWED_FORMATS = {"jpeg", "png", "jpg", "rgb8", "bgr8", "mono8", "rgb"}
    
    @staticmethod
    def validate(image: Image) -> Optional[str]:
        """Restituisce messaggio errore se invalida, None se OK."""
        if not isinstance(image.data, bytes):
            return f"image.data deve essere bytes, ricevuto {type(image.data)}"
        
        if len(image.data) > ImageValidator.MAX_SIZE_BYTES:
            size_mb = len(image.data) / (1024 * 1024)
            return f"Immagine troppo grande: {size_mb:.1f}MB (max {ImageValidator.MAX_SIZE_BYTES // (1024*1024)}MB)"
        
        if image.format.lower() not in ImageValidator.ALLOWED_FORMATS:
            return f"Formato {image.format} non consentito. Consentiti: {ImageValidator.ALLOWED_FORMATS}"
        
        if len(image.data) == 0:
            return "Immagine vuota (0 bytes)"
        
        return None
