std_msgs/Header header
string label                    # es. "sedia", "persona", "tavolo"
float32 confidence              # 0.0 - 1.0
geometry_msgs/Point centroid_3d # (X_c, Y_c, Z_c) in camera_frame
geometry_msgs/Point centroid_2d # (X, Y) proiettato in map_frame (Z=0)
float32[4] bbox_2d              # [x_min, y_min, x_max, y_max] normalizzato
float32 estimated_width_m       # Ingombro stimato (m)
float32 estimated_depth_m       # Profondità stimata (m)
string semantic_class           # "obstacle", "target", "person", "furniture"
