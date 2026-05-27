import torch
import torch.nn as nn

class SuperPoint(nn.Module):
    def __init__(self):
        super().__init__()
        # Dummy model: NON è il vero SuperPoint! Solo per test pipeline.
        self.dummy = nn.Conv2d(1, 1, 3, padding=1)

    def forward(self, data):
        # data['image']: [B,1,H,W]
        img = data['image']
        B, _, H, W = img.shape
        keypoints = []
        scores = []
        for b in range(B):
            # Trova 10 punti casuali per test
            kpts = torch.randint(0, min(H, W), (10, 2), dtype=torch.float32)
            sc = torch.rand(10)
            keypoints.append(kpts)
            scores.append(sc)
        return {'keypoints': keypoints, 'scores': scores}
