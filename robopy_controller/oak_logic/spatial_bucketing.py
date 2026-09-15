import numpy as np

def bucket_keypoints(kps, descriptors, grid_size=6, max_per_cell=40):
    """
    Forza distribuzione uniforme su griglia
    
    Args:
        kps: array Nx3 (x, y, score)
        descriptors: array Nx256 float32
        grid_size: dimensione griglia (6 = 6x6 = 36 celle)
        max_per_cell: max keypoints per cella
    
    Returns:
        kps_filtered, descriptors_filtered
    """
    if len(kps) == 0:
        return kps, descriptors

    h, w = 720, 1280  # Risoluzione mono 720p
    cell_h, cell_w = h // grid_size, w // grid_size
    
    # Inizializza griglia
    buckets = [[[] for _ in range(grid_size)] for _ in range(grid_size)]
    
    # Distribuisci keypoints nelle celle
    for i, (x, y, score) in enumerate(kps):
        cx = min(int(x / cell_w), grid_size - 1)
        cy = min(int(y / cell_h), grid_size - 1)
        buckets[cy][cx].append((kps[i], descriptors[i], score))
    
    # Prendi top-N per cella ordinati per score
    selected_kps = []
    selected_desc = []
    
    for row in buckets:
        for cell in row:
            if len(cell) > 0:
                # Sort by score (index 2) descending
                cell.sort(key=lambda x: x[2], reverse=True)
                top_k = cell[:max_per_cell]
                for item in top_k:
                    selected_kps.append(item[0])
                    selected_desc.append(item[1])
    
    if not selected_kps:
        return np.array([]), np.array([])
    
    # Ricostruisci array
    new_kps = np.array(selected_kps, dtype=np.float32)
    new_desc = np.array(selected_desc, dtype=np.float32)
    
    return new_kps, new_desc
