import cv2
import numpy as np
import math
from scipy.ndimage import maximum_filter

# ============================================================================
# CLASSE 1: EnhancedSuperPointExtractor
# ============================================================================
class EnhancedSuperPointExtractor:
    """Sistema potenziato per estrarre più keypoints da SuperPoint"""
    
    def __init__(self, config, logger):
        
        self.config = config
        self.logger = logger
        
        # Parametri Ottimizzati
        self.nms_dist = 3          # Distanza minima tra punti (pixel)
        self.border_margin = 5    # Pixel da ignorare ai bordi
        self.conf_thresh = 0.020   # Soglia base
        
        # Statistiche
        self.stats = {
            'total_extracted': 0,
            'frame_count': 0
        }

        
    
    
    def grid_filter_keypoints_enhanced(self, keypoints, descriptors, scores, 
                                    grid_size=24, max_per_cell=1):
        """Grid filter AGGRESSIVO per distribuzione uniforme."""
        h, w = 200, 320

        # ✅ FIX CRITICO
        keep_indices = []
        
        if len(keypoints) == 0:
            return keypoints, descriptors, scores
        
        # ... codice filtro ...
        # Poiché il codice originale aveva "... codice filtro ...",
        # presumo che dobbiamo implementarlo o che fosse un commento nel file.
        # Guardando la logica, sembra mancare l'implementazione del filtro griglia.
        # Implementiamo una versione standard.
        
        grid = {}
        for i, kp in enumerate(keypoints):
            x, y = int(kp[0]), int(kp[1])
            gx, gy = int(x / grid_size), int(y / grid_size)
            
            if (gx, gy) not in grid:
                grid[(gx, gy)] = []
            
            if len(grid[(gx, gy)]) < max_per_cell:
                grid[(gx, gy)].append(i)
                keep_indices.append(i)

        if len(keep_indices) > 0:
            keep_indices = np.array(keep_indices)
            # ✅ CORRETTO: usa self.logger invece di self.get_logger()
            self.logger.debug(
                f"Grid filter: {len(keypoints)} → {len(keep_indices)} keypoints "
                f"({grid_size}x{grid_size} grid, max {max_per_cell}/cell)"
            )
            return keypoints[keep_indices], descriptors[keep_indices], scores[keep_indices]
        else:
            return np.array([]), np.array([]), np.array([])



    def filter_edge_features(self, keypoints, descriptors, scores, mono_frame, threshold=50):
        """Rimuove keypoints su BORDI/EDGE usando gradiente."""
        if len(keypoints) == 0 or mono_frame is None:
            return keypoints, descriptors, scores
        
        # Converti in grayscale se necessario
        if len(mono_frame.shape) == 3:
            gray = cv2.cvtColor(mono_frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = mono_frame
        
        # Calcola gradiente Sobel (rileva bordi)
        sobelx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        sobely = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        gradient_magnitude = np.sqrt(sobelx**2 + sobely**2)
        
        # Normalizza
        gradient_magnitude = (gradient_magnitude / gradient_magnitude.max() * 255).astype(np.uint8)
        
        valid_indices = []
        
        for i, kp in enumerate(keypoints):
            x, y = int(kp[0]), int(kp[1])
            
            # Controlla bounds
            if not (0 <= x < gray.shape[1] and 0 <= y < gray.shape[0]):
                continue
            
            # Valore gradiente al keypoint
            grad_val = gradient_magnitude[y, x]
            
            # SE il gradiente è BASSO = non è un bordo = BUONO
            # SE il gradiente è ALTO = è un bordo = CATTIVO
            if grad_val < threshold:
                valid_indices.append(i)
        
        if valid_indices:
            # ✅ CORRETTO
            self.logger.debug(
                f"Edge filter: {len(keypoints)} → {len(valid_indices)} keypoints "
                f"(removed edge features)"
            )
            return (keypoints[valid_indices], 
                    descriptors[valid_indices], 
                    scores[valid_indices])
        
        return keypoints, descriptors, scores


    # Aggiungi questa funzione alla classe EnhancedSuperPointExtractor
    def extract_debug_only(self, nndata, mono_frame):
        """Estrazione SEMPLICE senza filtri - solo per debug"""
        try:
            # 1. Get raw data
            scores_fp16 = []
            desc_fp16 = []
            
            if nndata.hasLayer('semi'):
                scores_fp16 = nndata.getLayerFp16('semi')
            if nndata.hasLayer('desc'):
                desc_fp16 = nndata.getLayerFp16('desc')
            
            if not scores_fp16 or not desc_fp16:
                return None, None, None
            
            # 2. Convert
            scores = np.array(scores_fp16, dtype=np.float32)
            desc = np.array(desc_fp16, dtype=np.float32)
            
            # 3. Heatmap (semplice)
            if scores.shape[0] == 65 * 25 * 40:
                scores_3d = scores.reshape(65, 25, 40)
                # Softmax semplice
                exp_scores = np.exp(scores_3d - np.max(scores_3d, axis=0))
                softmax = exp_scores / np.sum(exp_scores, axis=0)
                heatmap = softmax[:-1, :, :]  # Remove dustbin
                
                # Pixel shuffle
                heatmap = heatmap.transpose(1, 2, 0).reshape(25, 40, 64)
                heatmap = heatmap.reshape(25, 40, 8, 8)
                heatmap = heatmap.transpose(0, 2, 1, 3)
                heatmap = heatmap.reshape(200, 320)
            else:
                return None, None, None
            
            # 4. Threshold fisso (NO adattivo)
            threshold = 0.015
            coords = np.argwhere(heatmap > threshold)
            
            if len(coords) == 0:
                return np.array([]), np.array([]), np.array([])
            
            keypoints = np.column_stack([coords[:, 1], coords[:, 0]]).astype(np.float32)
            
            # 5. Descriptors (semplice)
            if desc.shape[0] == 256 * 25 * 40:
                desc_map = desc.reshape(256, 25, 40)
                # Normalizzazione L2
                norms = np.linalg.norm(desc_map, axis=0, keepdims=True)
                desc_map = desc_map / (norms + 1e-8)
                
                # Campionamento nearest
                sampled_desc = []
                for kp in keypoints:
                    x = int(kp[0] * 0.125)  # 320→40
                    y = int(kp[1] * 0.125)  # 200→25
                    x = np.clip(x, 0, 39)
                    y = np.clip(y, 0, 24)
                    sampled_desc.append(desc_map[:, y, x])
                
                descriptors = np.array(sampled_desc, dtype=np.float32)
            else:
                descriptors = np.array([])
            
            self.logger.info(f"DEBUG RAW: {len(keypoints)} keypoints, heatmap max={heatmap.max():.4f}")
            
            return keypoints, heatmap[coords[:, 0], coords[:, 1]], descriptors
            
        except Exception as e:
            self.logger.error(f"Debug extract error: {e}")
            return None, None, None

    def filter_uniform_regions(self, frame, keypoints, window_size=5):
        """Rimuove keypoint su regioni con poca texture"""
        if len(keypoints) == 0 or frame is None:
            return keypoints
        
        gray = frame
        if len(frame.shape) == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        valid_indices = []
        
        for i, (x, y) in enumerate(keypoints):
            x_int, y_int = int(x), int(y)
            
            # Estrai patch attorno al keypoint
            x1 = max(0, x_int - window_size)
            x2 = min(gray.shape[1], x_int + window_size + 1)
            y1 = max(0, y_int - window_size)
            y2 = min(gray.shape[0], y_int + window_size + 1)
            
            patch = gray[y1:y2, x1:x2]
            
            if patch.size > 0:
                # Calcola varianza della patch (misura di texture)
                variance = np.var(patch)
                
                # Mantieni solo punti su texture sufficiente
                if variance > 10:  # Soglia empirica
                    valid_indices.append(i)
        
        if valid_indices:
            return keypoints[valid_indices]
        return keypoints

    def safe_fp16_to_float(self, fp16_values):
        """Conversione sicura da FP16 a float32"""
        safe_values = []
        for v in fp16_values:
            try:
                # Assicurati che il valore sia un intero e nel range corretto
                int_val = int(v)
                # Maschera per 16-bit (0-65535)
                safe_val = int_val & 0xFFFF
                safe_values.append(safe_val)
            except:
                safe_values.append(0)  # Valore di fallback
        
        # Converti usando numpy
        uint16_array = np.array(safe_values, dtype=np.uint16)
        return uint16_array.view(np.float16).astype(np.float32)


    def extract_enhanced_features(self, nndata, depth_frame=None, mono_frame=None):
        
        """
        Estrae keypoints lavorando direttamente sui tensori output della NN.
        Evita conversioni multiple e applica filtri in un solo passaggio.
        """
        try:
            # 0. Validazione input
            if not nndata:
                self.logger.warn("❌ NNData è None")
                return None, None, None

            # 1. Debug: lista tutti i layer disponibili
            try:
                layer_names = nndata.getAllLayerNames()
                self.logger.debug(f"🔍 Layer disponibili: {layer_names}")
            except:
                self.logger.warn("⚠️  Impossibile ottenere nomi layer")

            # 2. Estrazione dati dai layer
            scores_fp16 = []
            desc_fp16 = []

            # Prova diversi nomi di layer per massima compatibilità
            possible_score_names = ['semi', 'scores', 'output_semi', 'output_scores', '0', 'output_0']
            possible_desc_names = ['desc', 'descriptors', 'output_desc', 'output_descriptors', '1', 'output_1']
            
            for name in possible_score_names:
                if nndata.hasLayer(name):
                    scores_fp16 = nndata.getLayerFp16(name)
                    self.logger.info(f"✅ Trovato layer scores: '{name}' con {len(scores_fp16)} elementi")
                    break
            
            for name in possible_desc_names:
                if nndata.hasLayer(name):
                    desc_fp16 = nndata.getLayerFp16(name)
                    self.logger.info(f"✅ Trovato layer descrittori: '{name}' con {len(desc_fp16)} elementi")
                    break

            # 3. Validazione dati estratti
            if not scores_fp16 or len(scores_fp16) == 0:
                self.logger.error("❌ Scores vuoti o None")
                return None, None, None
            
            if not desc_fp16 or len(desc_fp16) == 0:
                self.logger.error("❌ Descrittori vuoti o None")
                return None, None, None

            # DEBUG: stampa primi valori per verifica
            self.logger.debug(f"Scores FP16 primi 5: {scores_fp16[:5]}")
            self.logger.debug(f"Desc FP16 primi 5: {desc_fp16[:5]}")

            # 4. Conversione a float32
            try:
                scores_float = np.array(scores_fp16).astype(np.float32)
                desc_float = np.array(desc_fp16).astype(np.float32)
            except Exception as e:
                self.logger.error(f"❌ Errore conversione float32: {e}")
                return None, None, None

            self.logger.debug(f"✅ Conversioni OK: scores shape={scores_float.shape}, desc shape={desc_float.shape}")

            # 5. Processamento heatmap
            total_scores = scores_float.shape[0]
            
            # Formati attesi per input 320x200:
            # 1) 65x25x40 = 65000 elementi (formato standard SuperPoint)
            # 2) 200x320 = 64000 elementi (heatmap già reshaped)
            
            if total_scores == 65 * 25 * 40:  # Formato standard
                self.logger.debug("📊 Formato scores: 65x25x40")
                scores_reshaped = scores_float.reshape(65, 25, 40)
                heatmap = self._process_heatmap_superpoint(scores_reshaped)
                
            elif total_scores == 200 * 320:  # Heatmap già reshaped
                self.logger.debug("📊 Formato scores: 200x320 (già reshaped)")
                heatmap = scores_float.reshape(200, 320)
                
            else:
                self.logger.error(f"❌ Dimensioni scores inattese: {total_scores}")
                self.logger.error(f"   Attesi: {65*25*40}=65000 (65x25x40) oppure {200*320}=64000 (200x320)")
                return None, None, None

            # 6. Validazione heatmap
            h, w = heatmap.shape
            if h != 200 or w != 320:
                self.logger.warn(f"⚠️  Heatmap ha dimensioni inattese: {w}x{h}, atteso 320x200")
                # Potrebbe essere invertito
                if h == 320 and w == 200:
                    self.logger.warn("⚠️  Dimensioni invertite, ruoto...")
                    heatmap = np.rot90(heatmap)
                    h, w = heatmap.shape

            # 7. Statistiche heatmap
            heatmap_min = np.min(heatmap)
            heatmap_max = np.max(heatmap)
            heatmap_mean = np.mean(heatmap)
            
            self.logger.info(f"📊 Heatmap: {w}x{h}, min={heatmap_min:.4f}, max={heatmap_max:.4f}, mean={heatmap_mean:.4f}")
            
            # Se la heatmap è piatta (tutti valori simili), abbassiamo la soglia
            if (heatmap_max - heatmap_min) < 0.01:
                self.logger.warn("⚠️  Heatmap molto piatta, abbasso soglia threshold")
                current_thresh = self.conf_thresh * 0.1
            else:
                current_thresh = self.conf_thresh

            # 8. Filtraggio bordi (evita keypoints su bordi)
            border = self.border_margin
            heatmap[0:border, :] = 0
            heatmap[h-border:h, :] = 0
            heatmap[:, 0:border] = 0
            heatmap[:, w-border:w] = 0

            # 9. Estrazione keypoints con NMS
            self.logger.debug(f"🔍 Estrazione keypoints con threshold={current_thresh}")
            kpts = self._nms_fast_robust(heatmap, h, w, threshold=current_thresh)
            
            # Fallback se troppi pochi keypoints
            if kpts is None or len(kpts) < 5:
                num_kpts = 0 if kpts is None else len(kpts)
                self.logger.warn(
                    f"⚠️  Pochi keypoints ({num_kpts}), provo threshold ridotta"
                )
                kpts = self._nms_fast_robust(
                    heatmap, h, w, threshold=current_thresh * 0.5
                )

            if kpts is None or len(kpts) == 0:
                self.logger.warn("⚠️  Nessun keypoint trovato dopo NMS")
                return None, None, None

            self.logger.info(f"✅ Trovati {len(kpts)} keypoints")

            # 10. Processamento descrittori
            total_desc = desc_float.shape[0]
            expected_desc = 256 * 25 * 40  # 256x25x40 = 256000

            if total_desc == expected_desc:
                self.logger.debug("📊 Formato descrittori: 256x25x40")
                
                # RESHAPE
                desc_map = desc_float.reshape(256, 25, 40)
                
                # DEBUG: Statistiche PRIMA della normalizzazione
                self.logger.debug(
                    f"📊 Descrittori pre-normalizzazione: "
                    f"min={desc_map.min():.3f}, max={desc_map.max():.3f}, mean={desc_map.mean():.3f}"
                )
                
                # NORMALIZZAZIONE L2 OBBLIGATORIA (il blob NON è normalizzato)
                eps = 1e-6
                desc_norm_per_pixel = np.linalg.norm(desc_map, axis=0, keepdims=True)  # Shape: (1, 25, 40)
                
                # DEBUG: Mostra le norme PRIMA della normalizzazione
                self.logger.debug(
                    f"📏 Norme pre-normalizzazione: "
                    f"min={desc_norm_per_pixel.min():.3f}, "
                    f"mean={desc_norm_per_pixel.mean():.3f}, "
                    f"max={desc_norm_per_pixel.max():.3f}"
                )
                
                # APPLICA NORMALIZZAZIONE L2
                desc_map = desc_map / (desc_norm_per_pixel + eps)

                # DEBUG: Verifica POST-normalizzazione
                desc_norm_after = np.linalg.norm(desc_map, axis=0)
                self.logger.info(
                    f"✅ Norme POST-normalizzazione: "
                    f"min={desc_norm_after.min():.3f}, "
                    f"mean={desc_norm_after.mean():.3f}, "
                    f"max={desc_norm_after.max():.3f}"
                )

                # Statistiche finali
                self.logger.debug(
                    f"📊 Descrittori post-normalizzazione: "
                    f"min={desc_map.min():.3f}, max={desc_map.max():.3f}, mean={desc_map.mean():.3f}"
                )

                # DEBUG CRITICO: Verifica cosa stiamo per passare
                self.logger.warn(f"🔍 PRIMA campionamento: desc_map range=[{desc_map.min():.3f}, {desc_map.max():.3f}]")
                test_pixel_norm = np.linalg.norm(desc_map[:, 16, 28])
                self.logger.warn(f"🔍 Norma pixel test [16,28]: {test_pixel_norm:.3f}")

                # 12. Campionamento descrittori sui keypoints (USA LA MAPPA NORMALIZZATA!)
                desc = self._sample_descriptors_bilinear(kpts, desc_map, (h, w))
                    
            else:
                self.logger.error(f"❌ Dimensioni descrittori inattese: {total_desc}, attesi {expected_desc}")
                return None, None, None

            # 13. Estrai scores associati ai keypoints
            scores_out = np.zeros(len(kpts), dtype=np.float32)
            for i, (x, y) in enumerate(kpts):
                xi, yi = int(round(x)), int(round(y))
                if 0 <= xi < w and 0 <= yi < h:
                    scores_out[i] = heatmap[yi, xi]
                else:
                    scores_out[i] = 0.0

            # 14. Filtra keypoints con score troppo basso
            if len(scores_out) > 0:
                score_threshold = current_thresh * 0.5
                valid_mask = scores_out > score_threshold
                
                if np.any(valid_mask):
                    kpts = kpts[valid_mask]
                    desc = desc[valid_mask]
                    scores_out = scores_out[valid_mask]
                    self.logger.debug(f"🔄 Filtro score: {len(valid_mask)} -> {np.sum(valid_mask)} keypoints")
                else:
                    self.logger.warn("⚠️  Nessun keypoint supera il filtro score")
                    return None, None, None

            # 15. Limita numero massimo di keypoints
            max_keypoints = self.config.get('max_features', 500)
            if len(kpts) > max_keypoints:
                if len(scores_out) > 0:
                    # Ordina per score decrescente
                    sorted_indices = np.argsort(scores_out)[::-1][:max_keypoints]
                    kpts = kpts[sorted_indices]
                    desc = desc[sorted_indices]
                    scores_out = scores_out[sorted_indices]
                else:
                    kpts = kpts[:max_keypoints]
                    desc = desc[:max_keypoints]
                    
                self.logger.debug(f"📉 Limite keypoints: {max_keypoints}")

            # 16. Aggiorna statistiche
            self._update_stats(len(kpts))
            
            # 17. Log finale
            self.logger.info(f"🎯 ESTRAZIONE COMPLETATA: {len(kpts)} keypoints, {desc.shape[1]}D descrittori")
            
            if len(kpts) > 0:
                # Statistiche posizione keypoints
                x_coords = kpts[:, 0]
                y_coords = kpts[:, 1]
                self.logger.debug(f"📍 Keypoints distribuzione: x[{x_coords.min():.0f}-{x_coords.max():.0f}], y[{y_coords.min():.0f}-{y_coords.max():.0f}]")
                
                # Statistiche scores
                self.logger.debug(f"📈 Scores: min={scores_out.min():.4f}, mean={scores_out.mean():.4f}, max={scores_out.max():.4f}")
            
            return kpts, scores_out, desc

        except Exception as e:
            self.logger.error(f"❌ ERRORE CRITICO in extract_enhanced_features: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None, None, None


    def _process_heatmap_superpoint(self, semi):
        """
        Converte output SuperPoint (65xHxW) in heatmap 2D.
        Versione ottimizzata per stabilità numerica.
        """
        try:
            # semi shape: (65, H, W) dove H=25, W=40
            # Softmax lungo l'asse dei canali (65)
            
            # Per stabilità numerica: sottrai il massimo
            semi_max = np.max(semi, axis=0, keepdims=True)
            semi_exp = np.exp(semi - semi_max)
            
            # Calcola softmax
            softmax = semi_exp / np.sum(semi_exp, axis=0, keepdims=True)
            
            # Rimuovi il canale dustbin (l'ultimo dei 65)
            nodust = softmax[:-1, :, :]  # (64, H, W)
            
            # Dimensione input
            H, W = nodust.shape[1], nodust.shape[2]
            
            # Pixel shuffle: da (64, H, W) a (H*8, W*8)
            # 64 = 8x8, quindi riorganizziamo per ottenere risoluzione 8x maggiore
            
            # Passo 1: (64, H, W) -> (H, W, 64)
            heatmap = nodust.transpose(1, 2, 0)
            
            # Passo 2: (H, W, 64) -> (H, W, 8, 8)
            heatmap = heatmap.reshape(H, W, 8, 8)
            
            # Passo 3: (H, W, 8, 8) -> (H, 8, W, 8)
            heatmap = heatmap.transpose(0, 2, 1, 3)
            
            # Passo 4: (H, 8, W, 8) -> (H*8, W*8)
            heatmap = heatmap.reshape(H * 8, W * 8)
            
            return heatmap
            
        except Exception as e:
            self.logger.error(f"Errore in _process_heatmap_superpoint: {e}")
            # Fallback: se non funziona, prova a usare solo il primo canale
            if len(semi.shape) == 3:
                return semi[0, :, :]  # Primo canale
            return np.zeros((200, 320), dtype=np.float32)

    def _nms_fast_robust(self, heatmap, h, w, threshold):
        """
        Non-Maximum Suppression robusta con controlli aggiuntivi.
        """
        try:
            from scipy.ndimage import maximum_filter
            
            if np.max(heatmap) < threshold:
                self.logger.debug(f"⚠️  Heatmap max ({np.max(heatmap):.4f}) < threshold ({threshold:.4f})")
                return np.array([], dtype=np.float32)
            
            # Trova massimi locali in finestra 3x3
            neighborhood_size = 3
            max_filtered = maximum_filter(heatmap, size=neighborhood_size)
            
            # Identifica massimi locali sopra la soglia
            is_local_max = (heatmap == max_filtered) & (heatmap > threshold)
            
            # Ottieni coordinate
            y_coords, x_coords = np.where(is_local_max)
            
            if len(x_coords) == 0:
                return np.array([], dtype=np.float32)
            
            # Crea array keypoints e scores
            keypoints = np.column_stack((x_coords, y_coords)).astype(np.float32)
            scores = heatmap[y_coords, x_coords]
            
            # Ordina per score decrescente
            sorted_indices = np.argsort(-scores)
            keypoints = keypoints[sorted_indices]
            scores = scores[sorted_indices]
            
            # NMS spaziale
            kept_kpts = []
            grid = np.zeros((h, w), dtype=np.uint8)
            dist_thresh = self.nms_dist
            
            for i, (kp, score) in enumerate(zip(keypoints, scores)):
                x, y = int(round(kp[0])), int(round(kp[1]))
                
                if x < 0 or x >= w or y < 0 or y >= h:
                    continue
                
                # Area di soppressione
                x0, x1 = max(0, x - dist_thresh), min(w, x + dist_thresh + 1)
                y0, y1 = max(0, y - dist_thresh), min(h, y + dist_thresh + 1)
                
                # Se l'area è già occupata, scarta
                if np.any(grid[y0:y1, x0:x1]):
                    continue
                
                # Mantieni il keypoint
                kept_kpts.append([float(x), float(y)])
                
                # Marca area come occupata
                grid[y0:y1, x0:x1] = 1
                
                # Limita numero massimo
                if len(kept_kpts) >= self.config.get('max_features', 500):
                    break
            
            if kept_kpts:
                return np.array(kept_kpts, dtype=np.float32)
            else:
                return np.array([], dtype=np.float32)
                
        except Exception as e:
            self.logger.error(f"Errore in NMS: {e}")
            # Fallback: estrai semplicemente tutti i punti sopra la soglia
            y_coords, x_coords = np.where(heatmap > threshold)
            if len(x_coords) > 0:
                return np.column_stack((x_coords, y_coords)).astype(np.float32)
            return np.array([], dtype=np.float32)


    def _sample_descriptors_bilinear(self, keypoints, descriptors, img_shape):

        """
        Campiona descrittori con interpolazione bilineare - VERSIONE CORRETTA.
        Fix: Usa accesso diretto agli array numpy invece di loop Python.
        """
        if len(keypoints) == 0:
            return None
        
        try:
            # DEBUG CRITICO: Verifica cosa abbiamo ricevuto
            self.logger.warn(f"🔍 RICEVUTO in campionamento: descriptors range=[{descriptors.min():.3f}, {descriptors.max():.3f}]")
            test_pixel_norm_received = np.linalg.norm(descriptors[:, 16, 28])
            self.logger.warn(f"🔍 Norma pixel ricevuto [16,28]: {test_pixel_norm_received:.3f}")
            
            # Descriptors shape: (256, H_desc, W_desc) = (256, 25, 40)
            # Image shape: (H_img, W_img) = (200, 320)
            # Fattore di scala = 8 (200/25 = 320/40 = 8)
            
            C, H_desc, W_desc = descriptors.shape  # 256, 25, 40
            H_img, W_img = img_shape  # 200, 320
            
            # DEBUG: Verifica dimensioni
            self.logger.debug(f"Descriptor map: {C}x{H_desc}x{W_desc}, Image: {W_img}x{H_img}")
            
            # Scala i keypoint da coordinate immagine a coordinate descrittori
            scale_x = W_desc / W_img  # 40/320 = 0.125
            scale_y = H_desc / H_img  # 25/200 = 0.125
            
            kpts_scaled_x = keypoints[:, 0] * scale_x
            kpts_scaled_y = keypoints[:, 1] * scale_y
            
            # DEBUG: Mostra range coordinate scalate
            self.logger.debug(f"Scaled coords: x[{kpts_scaled_x.min():.2f}-{kpts_scaled_x.max():.2f}], y[{kpts_scaled_y.min():.2f}-{kpts_scaled_y.max():.2f}]")
            
            # Coordinate base per interpolazione (vettorizzato)
            x0 = np.floor(kpts_scaled_x).astype(int)
            y0 = np.floor(kpts_scaled_y).astype(int)
            x1 = x0 + 1
            y1 = y0 + 1
            
            # Pesi per interpolazione
            wx = (kpts_scaled_x - x0).reshape(-1, 1)  # Shape (N, 1) per broadcasting
            wy = (kpts_scaled_y - y0).reshape(-1, 1)
            
            # Clipping delle coordinate
            x0 = np.clip(x0, 0, W_desc - 1)
            x1 = np.clip(x1, 0, W_desc - 1)
            y0 = np.clip(y0, 0, H_desc - 1)
            y1 = np.clip(y1, 0, H_desc - 1)
            
            # Interpolazione bilineare vettorizzata
            # descriptors[:, y, x] restituisce un vettore di shape (256,)
            # Trasponendo otteniamo (N, 256)
            desc_00 = descriptors[:, y0, x0].T  # Shape (N, 256)
            desc_01 = descriptors[:, y0, x1].T
            desc_10 = descriptors[:, y1, x0].T
            desc_11 = descriptors[:, y1, x1].T
            
            # DEBUG: Verifica che non siano tutti zeri
            if np.all(desc_00 == 0):
                self.logger.warn("⚠️ Descrittori campionati sono tutti ZERO - possibile errore indicizzazione!")
                self.logger.warn(f"   Sample coords: x0={x0[:3]}, y0={y0[:3]}")
                self.logger.warn(f"   Descriptor map stats: min={descriptors.min():.3f}, max={descriptors.max():.3f}, mean={descriptors.mean():.3f}")
            
            # Interpolazione bilineare completa
            sampled_desc = (
                (1 - wx) * (1 - wy) * desc_00 +
                wx * (1 - wy) * desc_01 +
                (1 - wx) * wy * desc_10 +
                wx * wy * desc_11
            )
            
            # DEBUG: Verifica output
            norms = np.linalg.norm(sampled_desc, axis=1)
            self.logger.debug(f"Sampled descriptor norms: min={norms.min():.3f}, mean={norms.mean():.3f}, max={norms.max():.3f}")
            
            return sampled_desc.astype(np.float32)
                
        except Exception as e:
            self.logger.error(f"Errore in _sample_descriptors_bilinear: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            # Fallback: campionamento nearest neighbor
            return self._sample_descriptors_nearest(keypoints, descriptors, img_shape)

    def _sample_descriptors_nearest(self, keypoints, descriptors, img_shape):
        """
        Campionamento nearest neighbor (più veloce ma meno preciso).
        """
        try:
            H_desc, W_desc = descriptors.shape[1], descriptors.shape[2]
            H_img, W_img = img_shape
            
            scale_x = W_desc / W_img
            scale_y = H_desc / H_img
            
            # Coordinate intere
            x_coords = np.clip((keypoints[:, 0] * scale_x).astype(int), 0, W_desc - 1)
            y_coords = np.clip((keypoints[:, 1] * scale_y).astype(int), 0, H_desc - 1)
            
            # Campionamento
            sampled = descriptors[:, y_coords, x_coords].T
            return sampled
            
        except Exception as e:
            self.logger.error(f"Errore in _sample_descriptors_nearest: {e}")
            return None

    def _update_stats(self, num_keypoints):
        """Aggiorna statistiche di estrazione"""
        self.stats['frame_count'] += 1
        self.stats['total_extracted'] += num_keypoints
        
        if self.stats['frame_count'] > 0:
            self.stats['avg_per_frame'] = self.stats['total_extracted'] / self.stats['frame_count']
        
        # Log periodico ogni 10 frame
        if self.stats['frame_count'] % 10 == 0:
            self.logger.info(f"📊 STATISTICHE: {self.stats['frame_count']} frame, {self.stats['avg_per_frame']:.1f} keypoints/frame")

    # METODI MANCANTI MA CHIAMATI (copiato da sotto)
    def _call_superpoint_with_params(self, nndata, depth_frame, threshold=0.015):
        """Wrapper per chiamare SuperPoint con parametri diversi"""
        orig_threshold = self.config.get('feature_threshold', 0.015)
        
        try:
            self.config['feature_threshold'] = threshold
            kpts, scores, desc = self._simplified_superpoint_extraction(nndata)
            
            if kpts is not None and len(kpts) > 0:
                kpts, desc, scores = self._apply_light_filters(kpts, desc, scores, depth_frame)
            
            return kpts, scores, desc
            
        finally:
            self.config['feature_threshold'] = orig_threshold

    def _simplified_superpoint_extraction(self, nndata):
        """Versione semplificata di estrazione SuperPoint"""
        try:
            if not nndata:
                return None, None, None
            
            # 1. Recupero Layer
            scores_fp16 = []
            desc_fp16 = []

            if nndata.hasLayer('semi'):
                scores_fp16 = nndata.getLayerFp16('semi')
            elif nndata.hasLayer('scores'):
                scores_fp16 = nndata.getLayerFp16('scores')

            if nndata.hasLayer('desc'):
                desc_fp16 = nndata.getLayerFp16('desc')
            elif nndata.hasLayer('descriptors'):
                desc_fp16 = nndata.getLayerFp16('descriptors')

            if len(scores_fp16) == 0 or len(desc_fp16) == 0:
                self.logger.error(f"❌ Dati non ricevuti: semi={len(scores_fp16)}, desc={len(desc_fp16)}")
                return None, None, None

            scores = np.array(scores_fp16).astype(np.float32)
            desc = np.array(desc_fp16).astype(np.float32)

            # 3. Processamento Heatmap
            total_scores = scores.shape[0]
            if total_scores == 65 * 25 * 40:
                semi = scores.reshape(65, 25, 40)
                heatmap = self._compute_heatmap(semi)
            else:
                self.logger.warn(f"Formato scores inatteso: {total_scores}")
                return None, None, None

            # 4. Estrazione Keypoints
            keypoints, scores_out = self._extract_keypoints_from_heatmap(heatmap)
            
            if keypoints is None or len(keypoints) == 0:
                self.logger.warn(f"Nessun punto. Max score nel layer: {scores.max():.6f}")
                return None, None, None

            # 5. Processamento Descrittori
            desc_map = desc.reshape(256, 25, 40)
            
            norm = np.linalg.norm(desc_map, axis=0, keepdims=True)
            desc_map = desc_map / (norm + 1e-6)
            
            descriptors = self._sample_descriptors_vectorized(keypoints, desc_map, (200, 320))

            return keypoints, scores_out, descriptors

        except Exception as e:
            self.logger.error(f"Errore estrazione SuperPoint: {e}")
            return None, None, None

    def _compute_heatmap(self, semi):
        semi = np.exp(semi - np.max(semi, axis=0))
        softmax = semi / np.sum(semi, axis=0)
        nodust = softmax[:-1, :, :]
        heatmap = nodust.transpose(1, 2, 0)
        h, w, _ = heatmap.shape
        heatmap = heatmap.reshape(h, w, 8, 8)
        heatmap = heatmap.transpose(0, 2, 1, 3)
        heatmap = heatmap.reshape(h * 8, w * 8)
        return heatmap

    def _extract_keypoints_from_heatmap(self, heatmap):
        threshold = self.config.get('feature_threshold', 0.015)
        coords = np.argwhere(heatmap > threshold)
        if len(coords) == 0: return None, None
        
        kpts = np.column_stack([coords[:, 1], coords[:, 0]]).astype(np.float32)
        scores = heatmap[coords[:, 0], coords[:, 1]]
        
        max_keypoints = self.config.get('max_features', 500)
        if len(kpts) > max_keypoints:
            idx_sorted = np.argsort(scores)[::-1][:max_keypoints]
            kpts = kpts[idx_sorted]
            scores = scores[idx_sorted]
        
        return kpts, scores

    def _sample_descriptors_vectorized(self, kpts, desc_map, img_shape):
        if kpts is None or len(kpts) == 0: return None
        try:
            # Scale coordinates
            # img_shape not actually used if we hardcode scaling factor based on ratio
            # But kept for signature consistency or calculation
            W_desc, H_desc = desc_map.shape[2], desc_map.shape[1]
            scale_x = W_desc / 320.0 # 0.125
            scale_y = H_desc / 200.0 # 0.125
            
            kpts_scaled_x = kpts[:, 0] * scale_x
            kpts_scaled_y = kpts[:, 1] * scale_y
            
            x = np.clip(kpts_scaled_x.astype(int), 0, W_desc - 1)
            y = np.clip(kpts_scaled_y.astype(int), 0, H_desc - 1)
            
            sampled_desc = desc_map[:, y, x].T
            return sampled_desc
        except Exception as e:
            self.logger.error(f"Errore in _sample_descriptors_vectorized: {e}")
            return None

    def _apply_light_filters(self, keypoints, descriptors, scores, depth_frame):
        if keypoints is None or len(keypoints) == 0:
            return keypoints, descriptors, scores
        h, w = 200, 320
        border = 2
        mask = (
            (keypoints[:, 0] >= border) &
            (keypoints[:, 0] < w - border) &
            (keypoints[:, 1] >= border) &
            (keypoints[:, 1] < h - border)
        )
        if np.any(mask):
            return keypoints[mask], descriptors[mask], scores[mask]
        return keypoints, descriptors, scores

    def get_stats(self):
        return self.stats
