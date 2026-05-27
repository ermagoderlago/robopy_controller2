# simple_matcher_node.py
import rclpy
from rclpy.node import Node
import numpy as np
import cv2

class SimpleMatcherNode(Node):
    def __init__(self):
        super().__init__('simple_matcher_node')
        
        # Parametri configurabili
        self.declare_parameter('match_ratio', 0.7)
        self.declare_parameter('min_matches', 10)
        
        # FLANN matcher
        FLANN_INDEX_KDTREE = 1
        index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
        search_params = dict(checks=50)
        self.flann = cv2.FlannBasedMatcher(index_params, search_params)
        
        # BFMatcher (alternativa)
        self.bf = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
        
        self.get_logger().info("Matcher FLANN inizializzato")
        
    def match_with_flann(self, desc1, desc2, k=2):
        """Matching con FLANN + Lowe's ratio test"""
        if len(desc1) < 2 or len(desc2) < 2:
            return []
        
        matches = self.flann.knnMatch(desc1, desc2, k=k)
        
        # Applica Lowe's ratio test
        good_matches = []
        ratio = self.get_parameter('match_ratio').value
        
        for m, n in matches:
            if m.distance < ratio * n.distance:
                good_matches.append(m)
        
        return good_matches
    
    def match_with_bf(self, desc1, desc2):
        """Matching con Brute Force"""
        matches = self.bf.match(desc1, desc2)
        
        # Ordina per distanza
        matches = sorted(matches, key=lambda x: x.distance)
        
        # Filtra per distanza
        max_distance = self.get_parameter('match_ratio').value * 100
        good_matches = [m for m in matches if m.distance < max_distance]
        
        return good_matches