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
        
        # ✅ CRITICO: Dimensioni INPUT della NN (NON del frame!)
        # Se la NN ha resize/crop, DEVI specificare le dimensioni esatte
        self.nn_input_height = config.get('nn_input_height', 360)  # Default 360
        self.nn_input_width = config.get('nn_input_width', 480)   # Default 480
        
        # Parametri Ottimizzati - FIXED per SuperPoint
        self.nms_dist = 4          # Distanza minima tra punti (pixel) - CRITICO per stride 8
        self.border_margin = 5    # Pixel da ignorare ai bordi
        self.conf_thresh = 0.015   # Soglia FISSA - NO auto-scaling
        
        # NMS implementation: 'scipy' (default) or 'numpy' (embedded-friendly)
        self.nms_method = config.get('nms_method', 'numpy')  # Default NumPy per embedded
        
        # Statistiche
        self.stats = {
            'total_extracted': 0,
            'frame_count': 0
        }

        
    
    
    def grid_filter_keypoints_enhanced(self, keypoints, descriptors, scores, 
                                    grid_size=24, max_per_cell=1):
        """Grid filter per distribuzione uniforme - ✅ ORDINATO per score."""
        keep_indices = []
        
        if len(keypoints) == 0:
            return keypoints, descriptors, scores
        
        # ✅ FIX CRITICO: ordina per score dentro ogni cella
        grid = {}
        for i, kp in enumerate(keypoints):
            x, y = int(kp[0]), int(kp[1])
            gx, gy = int(x / grid_size), int(y / grid_size)
            
            if (gx, gy) not in grid:
                grid[(gx, gy)] = []
            
            grid[(gx, gy)].append((i, scores[i]))
        
        # Tieni i migliori per score in ogni cella
        for cell_indices in grid.values():
            # Ordina per score decrescente
            cell_indices.sort(key=lambda x: x[1], reverse=True)
            # Tieni al massimo max_per_cell
            for idx, score in cell_indices[:max_per_cell]:
                keep_indices.append(idx)

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


    # ⚠️ DEBUG ONLY - NON USARE IN PRODUZIONE
    def extract_debug_only(self, nndata, mono_frame):
        """
        ⚠️ WARNING: SOLO PER DEBUG - NON USARE IN PRODUZIONE!
        
        Questa funzione usa:
        - Nearest sampling (non bilinear)
        - NO re-normalizzazione descrittori
        - NO half-pixel alignment
        
        Usala SOLO per verificare:
        - "Quanti keypoints escono?"
        - "La heatmap è sensata?"
        
        Per produzione: usa extract_enhanced_features()
        """
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
            if scores.shape[0] % 65 == 0:
                grid_pixels = scores.shape[0] // 65
                # ✅ FIX CRITICO: usa dimensioni INPUT NN, NON frame
                H_grid = self.nn_input_height // 8
                W_grid = self.nn_input_width // 8
                
                scores_3d = scores.reshape(65, H_grid, W_grid)
                # Softmax semplice
                exp_scores = np.exp(scores_3d - np.max(scores_3d, axis=0))
                softmax = exp_scores / np.sum(exp_scores, axis=0)
                heatmap_small = softmax[:-1, :, :]  # Remove dustbin
                
                # Pixel shuffle
                heatmap = heatmap_small.transpose(1, 2, 0).reshape(H_grid, W_grid, 8, 8)
                heatmap = heatmap.transpose(0, 2, 1, 3)
                heatmap = heatmap.reshape(H_grid * 8, W_grid * 8)
            else:
                return None, None, None
            
            # 4. Threshold fisso (NO adattivo)
            threshold = 0.015
            coords = np.argwhere(heatmap > threshold)
            
            if len(coords) == 0:
                return np.array([]), np.array([]), np.array([])
            
            keypoints = np.column_stack([coords[:, 1], coords[:, 0]]).astype(np.float32)
            
            # 5. Descriptors (semplice)
            if desc.shape[0] % 256 == 0:
                W_grid = desc.shape[0] // (256 * H_grid)
                desc_map = desc.reshape(256, H_grid, W_grid)
                # Normalizzazione L2
                norms = np.linalg.norm(desc_map, axis=0, keepdims=True)
                desc_map = desc_map / (norms + 1e-8)
                
                # Campionamento nearest
                sampled_desc = []
                # Scala i keypoint
                scale_x = W_grid / heatmap.shape[1]
                scale_y = H_grid / heatmap.shape[0]
                
                for kp in keypoints:
                    x = int(kp[0] * scale_x)
                    y = int(kp[1] * scale_y)
                    x = np.clip(x, 0, W_grid - 1)
                    y = np.clip(y, 0, H_grid - 1)
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

            # ✅ FROZEN AFTER DEBUG: nomi layer noti (semi, desc)
            # Questa lista di fallback è utile solo durante sviluppo.
            # In produzione, i nomi sono sempre 'semi' e 'desc'.
            if nndata.hasLayer('semi'):
                scores_fp16 = nndata.getLayerFp16('semi')
                if self.stats['frame_count'] % 100 == 0:
                    self.logger.info(f"✅ Layer scores: 'semi' con {len(scores_fp16)} elementi")
            
            if nndata.hasLayer('desc'):
                desc_fp16 = nndata.getLayerFp16('desc')
                if self.stats['frame_count'] % 100 == 0:
                    self.logger.info(f"✅ Layer descrittori: 'desc' con {len(desc_fp16)} elementi")

            # 3. Validazione dati estratti
            if not scores_fp16 or len(scores_fp16) == 0:
                self.logger.error("❌ Scores vuoti o None")
                return None, None, None
            
            if not desc_fp16 or len(desc_fp16) == 0:
                self.logger.error("❌ Descrittori vuoti o None")
                return None, None, None

            # Debug ridotto (ogni 30 frame)
            if self.stats.get('frame_count', 0) % 30 == 0:
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
            # Formati attesi:
            # 1) 65 * H_grid * W_grid (formato standard SuperPoint)
            # 2) H_img * W_img (heatmap già reshaped)
            
            if total_scores % 65 == 0:  # Formato standard
                grid_pixels = total_scores // 65
                # Determiniamo H_grid e W_grid in base all'aspect ratio dell'immagine di input
                # Se non abbiamo l'immagine, assumiamo l'aspect ratio 4:3 o 16:10 standard
                # In alternativa, usiamo le dimensioni della heatmap se possibile.
                
                # ✅ FIX CRITICO: usa dimensioni INPUT NN, NON frame!
                # Se NN ha resize/crop, mono_frame.shape è SBAGLIATO
                H_grid = self.nn_input_height // 8
                W_grid = self.nn_input_width // 8
                
                # Verifica che corrisponda
                if H_grid * W_grid != grid_pixels:
                    self.logger.error(f"❌ Grid mismatch: H_grid={H_grid}, W_grid={W_grid}, grid_pixels={grid_pixels}")
                    self.logger.error(f"   NN input configurato: {self.nn_input_width}×{self.nn_input_height}")
                    # Fallback: prova a calcolare
                    H_grid = int(round(math.sqrt(grid_pixels * 0.75)))  # assume 4:3
                    W_grid = grid_pixels // H_grid
                
                self.logger.info(f"📊 Rilevata griglia NN: {W_grid}x{H_grid} ({total_scores} elementi)")
                
                scores_reshaped = scores_float.reshape(65, H_grid, W_grid)
                heatmap = self._process_heatmap_superpoint(scores_reshaped)
                
            elif mono_frame is not None and total_scores == mono_frame.shape[0] * mono_frame.shape[1]:
                self.logger.debug(f"📊 Formato scores: {mono_frame.shape[1]}x{mono_frame.shape[0]} (già reshaped)")
                heatmap = scores_float.reshape(mono_frame.shape[0], mono_frame.shape[1])
            else:
                # Fallback estremo: prova a indovinare se è quadrato o altro
                side = int(math.sqrt(total_scores))
                if side * side == total_scores:
                    heatmap = scores_float.reshape(side, side)
                else:
                    self.logger.error(f"❌ Dimensioni scores inattese: {total_scores}")
                    return None, None, None

            # 6. Validazione heatmap
            h, w = heatmap.shape
            
            # Se abbiamo un frame di riferimento, verifichiamo la corrispondenza
            if mono_frame is not None:
                fh, fw = mono_frame.shape[:2]
                if h != fh or w != fw:
                    self.logger.warn(f"⚠️ Heatmap ({w}x{h}) non coincide con frame ({fw}x{fh})")
                # Potrebbe essere invertito
                if h == 320 and w == 200:
                    self.logger.warn("⚠️  Dimensioni invertite, ruoto...")
                    heatmap = np.rot90(heatmap)
                    h, w = heatmap.shape

            # 7. Statistiche heatmap (ogni 30 frame per non rallentare)
            if self.stats['frame_count'] % 30 == 0:
                heatmap_min = np.min(heatmap)
                heatmap_max = np.max(heatmap)
                heatmap_mean = np.mean(heatmap)
                self.logger.info(f"📊 Heatmap: {w}x{h}, min={heatmap_min:.4f}, max={heatmap_max:.4f}, mean={heatmap_mean:.4f}")
            
            # ✅ FIX CRITICO: Threshold FISSO - NO auto-scaling
            # SuperPoint heatmap è sempre normalizzata via softmax
            # La dinamica dipende dalla rete, non dall'immagine
            current_thresh = self.conf_thresh

            # 8. Filtraggio bordi (evita keypoints su bordi)
            border = self.border_margin
            heatmap[0:border, :] = 0
            heatmap[h-border:h, :] = 0
            heatmap[:, 0:border] = 0
            heatmap[:, w-border:w] = 0

            # ✅ FIX CRITICO: applica threshold PRIMA della NMS
            # SuperPoint: threshold → NMS → top-K
            heatmap_thr = heatmap.copy()
            heatmap_thr[heatmap_thr < current_thresh] = 0

            # 9. Estrazione keypoints con NMS
            if self.stats['frame_count'] % 30 == 0:
                self.logger.debug(f"🔍 Estrazione keypoints con threshold={current_thresh}")
            kpts = self._nms_fast_robust(heatmap_thr, h, w, threshold=current_thresh)
            
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
            # Descrittori attesi: 256 * H_grid * W_grid
            H_grid, W_grid = heatmap.shape[0] // 8, heatmap.shape[1] // 8
            expected_desc = 256 * H_grid * W_grid

            if total_desc == expected_desc:
                self.logger.debug(f"📊 Formato descrittori: 256x{H_grid}x{W_grid}")
                
                # RESHAPE
                desc_map = desc_float.reshape(256, H_grid, W_grid)
                
                # NORMALIZZAZIONE L2 OBBLIGATORIA (il blob NON è normalizzato)
                eps = 1e-6
                desc_norm_per_pixel = np.linalg.norm(desc_map, axis=0, keepdims=True)  # Shape: (1, H_grid, W_grid)
                
                # APPLICA NORMALIZZAZIONE L2
                desc_map = desc_map / (desc_norm_per_pixel + eps)

                # Debug ogni 30 frame
                if self.stats['frame_count'] % 30 == 0:
                    desc_norm_after = np.linalg.norm(desc_map, axis=0)
                    self.logger.info(
                        f"✅ Descrittori: 256x{H_grid}x{W_grid}, norme post-norm: "
                        f"min={desc_norm_after.min():.3f}, mean={desc_norm_after.mean():.3f}, max={desc_norm_after.max():.3f}"
                    )

                # 12. Campionamento descrittori sui keypoints (USA LA MAPPA NORMALIZZATA!)
                desc = self._sample_descriptors_bilinear(kpts, desc_map, (h, w))
                
                # ✅ FIX CRITICO #5: RE-NORMALIZZA dopo interpolazione bilineare
                # L'interpolazione rompe la norma unitaria → matching L2 diventa incoerente
                if desc is not None and len(desc) > 0:
                    desc_norms = np.linalg.norm(desc, axis=1, keepdims=True)
                    desc = desc / (desc_norms + 1e-8)
                    
                    # Debug ogni 30 frame
                    if self.stats['frame_count'] % 30 == 0:
                        final_norms = np.linalg.norm(desc, axis=1)
                        self.logger.info(
                            f"✅ Descrittori POST-sampling+renorm: "
                            f"norme min={final_norms.min():.3f}, mean={final_norms.mean():.3f}, max={final_norms.max():.3f}"
                        )
                
            else:
                self.logger.error(f"❌ Dimensioni descrittori inattese: {total_desc}, attesi {expected_desc}")
                return None, None, None

            # 12.5 FILTRI DI QUALITÀ DISABILITATI (erano troppo aggressivi)
            # I filtri rimuovevano punti validi sugli spigoli degli oggetti
            # TODO: Rivedere logica filter_edge_features con threshold più permissivi
            # kpts, desc, scores_temp = self.filter_edge_features(kpts, desc, scores_float[:len(kpts)], mono_frame)
            # kpts_filtered = self.filter_uniform_regions(mono_frame, kpts)

            # 13. Estrai scores associati ai keypoints
            # ⚠️ NOTA: questi sono valori della heatmap POST-NMS, NON il confidence originale
            # Usali solo per: ordinamento, top-K
            # NON usarli per: decisioni metriche, fusion depth/semantic
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
        ✅ NMS SEMPLIFICATA - SuperPoint style
        Supporta sia SciPy che NumPy-only per embedded
        """
        if np.max(heatmap) < threshold:
            return np.array([], dtype=np.float32)
        
        # Scelta implementazione NMS
        if self.nms_method == 'scipy':
            return self._nms_scipy(heatmap, h, w, threshold)
        else:
            return self._nms_numpy(heatmap, h, w, threshold)
    
    def _nms_scipy(self, heatmap, h, w, threshold):
        """NMS con SciPy maximum_filter (più veloce ma richiede SciPy)"""
        try:
            from scipy.ndimage import maximum_filter
            
            # ✅ NMS UNICA con maximum_filter - finestra DISPARI centrata
            neighborhood_size = 2 * self.nms_dist + 1  # 2*4+1 = 9
            max_filtered = maximum_filter(heatmap, size=neighborhood_size)
            
            # ✅ Identifica SOLO massimi locali sopra soglia - STOP
            is_peak = (heatmap == max_filtered) & (heatmap > threshold)
            
            # Ottieni coordinate
            y_coords, x_coords = np.where(is_peak)
            
            if len(x_coords) == 0:
                return np.array([], dtype=np.float32)
            
            # Crea array keypoints e scores
            keypoints = np.column_stack((x_coords, y_coords)).astype(np.float32)
            scores = heatmap[y_coords, x_coords]
            
            # Ordina per score decrescente
            sorted_indices = np.argsort(-scores)
            keypoints = keypoints[sorted_indices]
            
            # Limita numero massimo
            max_kpts = self.config.get('max_features', 500)
            if len(keypoints) > max_kpts:
                keypoints = keypoints[:max_kpts]
            
            return keypoints
                
        except Exception as e:
            self.logger.error(f"Errore in NMS SciPy: {e}, fallback a NumPy")
            return self._nms_numpy(heatmap, h, w, threshold)
    
    def _nms_numpy(self, heatmap, h, w, threshold):
        """
        ✅ NMS NumPy-only - EMBEDDED FRIENDLY
        No SciPy, solo NumPy - più lento ma funziona su ARM/embedded
        """
        try:
            # Manual maximum filter con NumPy
            radius = self.nms_dist
            
            # Trova tutti i punti sopra threshold
            candidates = np.argwhere(heatmap > threshold)
            if len(candidates) == 0:
                return np.array([], dtype=np.float32)
            
            scores = heatmap[candidates[:, 0], candidates[:, 1]]
            
            # Ordina per score decrescente
            sorted_idx = np.argsort(-scores)
            candidates = candidates[sorted_idx]
            scores = scores[sorted_idx]
            
            # NMS greedy
            kept = []
            suppressed = np.zeros(len(candidates), dtype=bool)
            
            for i in range(len(candidates)):
                if suppressed[i]:
                    continue
                
                y, x = candidates[i]
                kept.append([float(x), float(y)])
                
                # Sopprimi vicini
                for j in range(i + 1, len(candidates)):
                    if suppressed[j]:
                        continue
                    
                    yj, xj = candidates[j]
                    dist_sq = (x - xj)**2 + (y - yj)**2
                    
                    if dist_sq <= (radius * radius):
                        suppressed[j] = True
                
                # Limita numero
                if len(kept) >= self.config.get('max_features', 500):
                    break
            
            if kept:
                return np.array(kept, dtype=np.float32)
            else:
                return np.array([], dtype=np.float32)
                
        except Exception as e:
            self.logger.error(f"Errore in NMS NumPy: {e}")
            # Fallback estremo
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
            C, H_desc, W_desc = descriptors.shape
            H_img, W_img = img_shape
            
            # Scala i keypoint da coordinate immagine a coordinate descrittori
            scale_x = W_desc / W_img
            scale_y = H_desc / H_img
            
            # Fix half-pixel alignment: SuperPoint descriptors are sampled at the center of 8x8 patches
            # We add 0.5 to the pixel coordinates before scaling to align with the grid centers.
            kpts_scaled_x = (keypoints[:, 0] + 0.5) * scale_x - 0.5
            kpts_scaled_y = (keypoints[:, 1] + 0.5) * scale_y - 0.5
            
            # Ensure coordinates are within bounds for interpolation
            kpts_scaled_x = np.clip(kpts_scaled_x, 0, W_desc - 1.001)
            kpts_scaled_y = np.clip(kpts_scaled_y, 0, H_desc - 1.001)
            
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
            desc_00 = descriptors[:, y0, x0].T  # Shape (N, 256)
            desc_01 = descriptors[:, y0, x1].T
            desc_10 = descriptors[:, y1, x0].T
            desc_11 = descriptors[:, y1, x1].T
            
            # Interpolazione bilineare completa
            sampled_desc = (
                (1 - wx) * (1 - wy) * desc_00 +
                wx * (1 - wy) * desc_01 +
                (1 - wx) * wy * desc_10 +
                wx * wy * desc_11
            )
            
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
        
        # Log periodico ogni 30 frame (ridotto da 10)
        if self.stats['frame_count'] % 30 == 0:
            self.logger.info(f"📊 STATISTICHE: {self.stats['frame_count']} frame, {self.stats['avg_per_frame']:.1f} keypoints/frame")

    # ========================================================================
    # LEGACY CODE - DA RIMUOVERE O SPOSTARE IN legacy.py
    # Queste funzioni non sono usate nel path principale
    # ========================================================================
    
    def _call_superpoint_with_params(self, nndata, depth_frame, threshold=0.015):
        """⚠️ LEGACY - non usata nel path principale"""
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
        """⚠️ LEGACY - non usata nel path principale"""
        try:
            if not nndata:
                return None, None, None
            
            # 1. Recupero Layer
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
