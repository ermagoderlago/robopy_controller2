import torch
import torch.nn as nn
import torch.nn.functional as F

class SkipAdd(nn.Module):
    def __init__(self):
        super(SkipAdd, self).__init__()

    def forward(self, x, y):
        return x + y

class MobileNetSkipAdd(nn.Module):
    def __init__(self):
        super(MobileNetSkipAdd, self).__init__()

        self.conv1 = nn.Conv2d(3, 32, kernel_size=3, stride=2, padding=1)
        self.bn1 = nn.BatchNorm2d(32)
        self.relu1 = nn.ReLU(inplace=True)

        self.conv2 = nn.Conv2d(32, 64, kernel_size=3, stride=2, padding=1)
        self.bn2 = nn.BatchNorm2d(64)
        self.relu2 = nn.ReLU(inplace=True)

        self.skip = SkipAdd()

        self.conv3 = nn.Conv2d(64, 32, kernel_size=3, padding=1)
        self.bn3 = nn.BatchNorm2d(32)
        self.relu3 = nn.ReLU(inplace=True)

        self.deconv1 = nn.ConvTranspose2d(32, 16, kernel_size=4, stride=2, padding=1)
        self.bn4 = nn.BatchNorm2d(16)
        self.relu4 = nn.ReLU(inplace=True)

        self.deconv2 = nn.ConvTranspose2d(16, 1, kernel_size=4, stride=2, padding=1)

    def forward(self, x):
        x1 = self.relu1(self.bn1(self.conv1(x)))
        x2 = self.relu2(self.bn2(self.conv2(x1)))
        x3 = self.skip(x2, x1)
        x4 = self.relu3(self.bn3(self.conv3(x3)))
        x5 = self.relu4(self.bn4(self.deconv1(x4)))
        out = self.deconv2(x5)
        return out
