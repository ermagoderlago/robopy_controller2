import numpy as np

class DeltaEncoder:
    def __init__(self):
        self.prev_desc = None
        self.frame_count = 0
    
    def encode(self, descriptors):
        """
        Encode descriptors con delta encoding
        
        Returns:
            mode (str): 'full' or 'delta'
            data (np.array): encoded data
        """
        self.frame_count += 1
        
        # Primo frame o shape cambiata → full
        if self.prev_desc is None or descriptors.shape != self.prev_desc.shape:
            self.prev_desc = descriptors.copy()
            # float32 → float16 già dimezza
            return 'full', descriptors.astype(np.float16)
        
        # Calcola delta
        delta = descriptors - self.prev_desc
        
        # Quantizza delta in int8 (range [-1, 1] tipico per descrittori normalizzati)
        # Assuming descriptors are normalized or small values.
        # Scale factor 127 to map [-1, 1] to [-127, 127]
        delta_quant = np.clip(delta * 127, -127, 127).astype(np.int8)
        
        # Update prev_desc with the reconstructed value to avoid drift?
        # Ideally yes, let's simulate decoding
        delta_decoded = delta_quant.astype(np.float32) / 127.0
        self.prev_desc = self.prev_desc + delta_decoded
        
        return 'delta', delta_quant
    
    def decode(self, mode, data):
        """Lato ROS: ricostruisci descrittori originali"""
        if mode == 'full':
            if data.dtype == np.float16:
                self.prev_desc = data.astype(np.float32)
            else:
                self.prev_desc = data # assume float32 if not specified
            return self.prev_desc
        else:  # delta
            delta = data.astype(np.float32) / 127.0
            if self.prev_desc is None:
                 # Should not happen if protocol followed
                 return np.zeros((len(data)//256, 256), dtype=np.float32)
            
            self.prev_desc = self.prev_desc + delta
            return self.prev_desc
