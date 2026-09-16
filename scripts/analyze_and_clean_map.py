#!/usr/bin/env python3
"""
analyze_and_clean_map.py — Analisi ed Ottimizzazione Professionale Mappa 2D Nav2/AMCL
Marcus Robot / ROS 2 Jazzy

Funzionalità:
1. Analizza la distribuzione dei pixel della mappa di occupabilità:
   - 0: Occupato (Muri/Ostacoli)
   - 254: Libero (Spazio calpestabile)
   - 205: Sconosciuto (Esterno)
2. Rileva e rimuove il rumore spuria (speckle noise da riflessioni ToF o ostacoli dinamici):
   - Connessione ad 8 vicini: componenti isolate < min_obstacle_size pixel vengono pulite.
3. Riempie micro-buchi (pinhole) nei muri continui per prevenire leak di likelihood field.
4. Genera statistiche comparative (prima/dopo) ed esporta versione ottimizzata e PNG per ispezione.
"""

import sys
import os
import shutil
import numpy as np
from PIL import Image
from scipy import ndimage


def analyze_map(image_path: str):
    img = Image.open(image_path)
    arr = np.array(img)

    total = arr.size
    occupied = np.sum(arr == 0)
    free = np.sum(arr == 254)
    unknown = np.sum((arr != 0) & (arr != 254))

    return {
        'dimensions': (img.width, img.height),
        'total_cells': total,
        'occupied_cells': int(occupied),
        'free_cells': int(free),
        'unknown_cells': int(unknown),
        'occupied_pct': float(occupied / total * 100),
        'free_pct': float(free / total * 100),
        'unknown_pct': float(unknown / total * 100),
        'array': arr
    }


def optimize_occupancy_grid(arr: np.ndarray, min_cluster_size: int = 5):
    """
    Rimuove rumore isolato (speckle noise) mantenendo intatti i muri e gli arredi.
    arr: 0=occupied, 254=free, 205=unknown
    """
    cleaned = arr.copy()

    # 1. Maschera ostacoli
    occupied_mask = (cleaned == 0)

    # 2. Etichettatura componenti connesse degli ostacoli
    structure = ndimage.generate_binary_structure(2, 2)  # 8-connessione
    labeled, num_features = ndimage.label(occupied_mask, structure=structure)
    sizes = ndimage.sum(occupied_mask, labeled, range(1, num_features + 1))

    # Identifica cluster sotto la soglia minima (rumore isolato)
    small_clusters = np.where(sizes < min_cluster_size)[0] + 1
    speckle_mask = np.isin(labeled, small_clusters)

    # Verifica che il cluster sia circondato prevalentemente da spazio libero
    # (non eliminare estremi di muri parziali vicini a unknown)
    free_mask = (cleaned == 254)
    free_dilated = ndimage.binary_dilation(free_mask, structure=structure, iterations=2)
    isolated_speckles = speckle_mask & free_dilated

    cleaned[isolated_speckles] = 254  # Converti in spazio libero
    num_speckles_removed = int(np.sum(isolated_speckles))

    # 3. Chiusura micro-fori (gap di 1 pixel nei muri continui)
    wall_mask = (cleaned == 0)
    closed_walls = ndimage.binary_closing(wall_mask, structure=structure, iterations=1)
    # Applica il riempimento solo se tocca già spazio conosciuto
    pinholes = closed_walls & ~wall_mask & (cleaned == 254)
    # Chiudi solo se ha almeno 4 vicini muro (evita espansione muri)
    wall_neighbors = ndimage.convolve(wall_mask.astype(int), np.ones((3, 3)), mode='constant')
    real_pinholes = pinholes & (wall_neighbors >= 4)
    cleaned[real_pinholes] = 0
    num_pinholes_closed = int(np.sum(real_pinholes))

    return cleaned, num_speckles_removed, num_pinholes_closed


def main():
    map_base = sys.argv[1] if len(sys.argv) > 1 else '/mnt/ssd/maps/piano_terra'
    input_pgm = f"{map_base}.pgm"
    input_yaml = f"{map_base}.yaml"

    output_pgm = f"{map_base}_opt.pgm"
    output_yaml = f"{map_base}_opt.yaml"
    output_png = f"{map_base}_opt.png"
    orig_png = f"{map_base}_orig.png"

    if not os.path.exists(input_pgm):
        print(f"❌ Errore: file {input_pgm} non trovato!")
        sys.exit(1)

    print(f"🔍 [MAP-ANALYZER] Analisi mappa originale: {input_pgm}")
    stats_orig = analyze_map(input_pgm)
    print(f"   Dimensioni: {stats_orig['dimensions'][0]}x{stats_orig['dimensions'][1]} celle (risoluzione 0.05 m/cell)")
    print(f"   Celle Totali: {stats_orig['total_cells']}")
    print(f"   Celle Libere: {stats_orig['free_cells']} ({stats_orig['free_pct']:.2f}%)")
    print(f"   Celle Muri/Ostacoli: {stats_orig['occupied_cells']} ({stats_orig['occupied_pct']:.2f}%)")
    print(f"   Celle Ignote: {stats_orig['unknown_cells']} ({stats_orig['unknown_pct']:.2f}%)")

    # Salva copia PNG dell'originale per comparazione
    Image.fromarray(stats_orig['array']).save(orig_png)

    # Test multipli di pulizia con diverse soglie di cluster
    print("\n🔬 [OPTIMIZATION] Esecuzione sweep parametri di filtraggio cluster...")
    best_cleaned = None
    best_speckles = 0
    best_pinholes = 0

    for min_size in [3, 5, 8]:
        arr_test, speckles, pinholes = optimize_occupancy_grid(stats_orig['array'], min_cluster_size=min_size)
        print(f"   - Min Cluster Size = {min_size} celle: {speckles} pixel di rumore rimossi, {pinholes} fori chiusi")
        if min_size == 5:  # Scelta bilanciata: 5 celle = 25 cm lineari / 125 cm²
            best_cleaned = arr_test
            best_speckles = speckles
            best_pinholes = pinholes

    # Salva mappa ottimizzata
    Image.fromarray(best_cleaned).save(output_pgm)
    Image.fromarray(best_cleaned).save(output_png)

    # Genera YAML per la mappa ottimizzata
    if os.path.exists(input_yaml):
        with open(input_yaml, 'r', encoding='utf-8') as f:
            yaml_content = f.read()
        yaml_content = yaml_content.replace(os.path.basename(input_pgm), os.path.basename(output_pgm))
        with open(output_yaml, 'w', encoding='utf-8') as f:
            f.write(yaml_content)

    stats_opt = analyze_map(output_pgm)
    print("\n📊 [CONFRONTO METRICHE]")
    print(f"   Pixel Rumore / Speckle Rimossi: {best_speckles}")
    print(f"   Pinhole / Fori Muro Chiusi:     {best_pinholes}")
    print(f"   Muri / Ostacoli Originali:      {stats_orig['occupied_cells']} -> Ottimizzati: {stats_opt['occupied_cells']}")
    print(f"   Spazio Libero Pulito:           {stats_orig['free_cells']} -> Ottimizzato: {stats_opt['free_cells']}")
    print(f"\n✅ Mappa ottimizzata salvata in: {output_pgm} e {output_yaml}")
    print(f"🖼️ Immagini PNG per confronto: {orig_png} vs {output_png}")


if __name__ == '__main__':
    main()
